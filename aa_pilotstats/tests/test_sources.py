"""
Wallet payouts, NPC kill counts, zKill R2Z2 killmails, fleets, and how they
merge into the leaderboard. All ESI and HTTP is mocked; caches are local so
nothing touches a real Auth's Redis when run in-container.
"""

# Standard Library
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

# Django
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from esi.exceptions import ESIBucketLimitException, HTTPClientError, HTTPNotModified
from esi.models import Scope, Token

from .. import fleets, payouts, tasks, typenames, zkill
from ..app_settings import FLEET_SCOPE, WALLET_SCOPE
from ..leaderboard import leaderboard_rows
from ..models import (
    CombatLogUpload,
    FleetMember,
    Killmail,
    KillmailParticipant,
    NpcKillDaily,
    Payout,
    PayoutDaily,
    PilotSettings,
    R2Z2State,
    TrackedEntity,
    TrackedFleet,
    TypeName,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=dt_timezone.utc)
CORP = 98000001
ALLIANCE = 99000001


def _own(user, character):
    CharacterOwnership.objects.create(
        character=character, user=user, owner_hash=f"hash-{character.character_id}",
    )


def _pilot(username, name, character_id, *, member=False, corp_id=CORP, alliance_id=ALLIANCE):
    user = AuthUtils.create_member(username) if member else AuthUtils.create_user(username)
    main = AuthUtils.add_main_character_2(
        user, name, character_id, corp_id=corp_id, corp_name="Example Corp",
        alliance_id=alliance_id, alliance_name="Example Alliance", disconnect_signals=True,
    )
    _own(user, main)
    return user, main


def _entry(ref_id, ref_type, amount, when=NOW, reason=None, **extra):
    return SimpleNamespace(
        id=ref_id, ref_type=ref_type, amount=amount, date=when, reason=reason,
        balance=123456789.0, description="should never be stored", first_party_id=1,
        second_party_id=2, tax=1.0, **extra,
    )


def _token(user, character, scope):
    token = Token.objects.create(
        user=user, character_id=character.character_id, character_name=character.character_name,
        access_token="example-access", refresh_token="example-refresh",
        character_owner_hash=f"hash-{character.character_id}", token_type="character",
    )
    token.scopes.add(Scope.objects.get_or_create(name=scope)[0])
    return token


def _local_cache(test):
    patcher = mock.patch.object(tasks, "cache", LocMemCache(f"pilotstats-{test.id()}", {}))
    patcher.start()
    test.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# Wallet journal
# ---------------------------------------------------------------------------


class NpcKillParsingTests(TestCase):
    def test_parses_and_sums(self):
        self.assertEqual(payouts.parse_npc_kills("23707: 3,23708: 1, 23707:2"), {23707: 5, 23708: 1})

    def test_skips_malformed_parts(self):
        self.assertEqual(payouts.parse_npc_kills("abc, 12:, :4, 99: x, 0: 3, 7: 2"), {7: 2})
        self.assertEqual(payouts.parse_npc_kills(None), {})

    def test_payout_types(self):
        self.assertTrue(payouts.is_payout("bounty_prizes"))
        self.assertTrue(payouts.is_payout("ess_escrow_transfer"))
        self.assertFalse(payouts.is_payout("bounty_prize_corporation_tax"))
        self.assertFalse(payouts.is_payout("campaign_objective_isk_reward_tax"))
        self.assertFalse(payouts.is_payout("market_transaction"))
        self.assertFalse(payouts.is_payout("player_donation"))


class StoreJournalTests(TestCase):
    def setUp(self):
        self.user, self.character = _pilot("ratter", "Example Ratter", 1000000401)

    def test_keeps_only_payout_lines_and_allowed_fields(self):
        entries = [
            _entry(1, "bounty_prizes", 1_500_000.0, reason="23707: 3,23708: 1"),
            _entry(2, "ess_escrow_transfer", 40_000_000.0),
            _entry(3, "agent_mission_reward", 900_000.0),
            _entry(4, "bounty_prize_corporation_tax", -150_000.0),
            _entry(5, "market_transaction", 99.0),
            _entry(6, "player_donation", 5.0),
        ]
        self.assertEqual(payouts.store_journal(self.character, entries), 3)
        self.assertEqual(
            sorted(Payout.objects.values_list("ref_id", flat=True)), [1, 2, 3],
        )
        bounty = Payout.objects.get(ref_id=1)
        self.assertEqual(bounty.npc_kills, {"23707": 3, "23708": 1})
        self.assertEqual(Payout.objects.get(ref_id=2).npc_kills, {})
        stored_text = " ".join(str(v) for v in Payout.objects.values_list(
            "entry_type", "amount", "npc_kills", "ref_id",
        ))
        self.assertNotIn("should never be stored", stored_text)
        self.assertNotIn("123456789", stored_text)

    def test_rerun_does_not_double_count(self):
        entries = [_entry(1, "bounty_prizes", 1_000_000.0, reason="23707: 2")]
        payouts.store_journal(self.character, entries)
        self.assertEqual(payouts.store_journal(self.character, entries), 0)
        self.assertEqual(PayoutDaily.objects.get().amount, Decimal("1000000.00"))
        self.assertEqual(NpcKillDaily.objects.get().count, 2)

    def test_daily_totals_add_new_lines_only(self):
        payouts.store_journal(self.character, [_entry(1, "bounty_prizes", 1_000_000.0, reason="23707: 2")])
        payouts.store_journal(self.character, [
            _entry(1, "bounty_prizes", 1_000_000.0, reason="23707: 2"),
            _entry(7, "bounty_prizes", 500_000.0, when=NOW + timedelta(minutes=20), reason="23707: 1"),
        ])
        self.assertEqual(PayoutDaily.objects.get().amount, Decimal("1500000.00"))
        self.assertEqual(NpcKillDaily.objects.get(npc_type_id=23707).count, 3)

    def test_purge_keeps_daily_totals(self):
        payouts.store_journal(self.character, [
            _entry(1, "bounty_prizes", 1_000_000.0, when=timezone.now() - timedelta(days=60)),
        ])
        tasks.purge_old_payouts()
        self.assertFalse(Payout.objects.exists())
        self.assertEqual(PayoutDaily.objects.count(), 1)


class PullWalletTaskTests(TestCase):
    def setUp(self):
        self.user, self.character = _pilot("ratter", "Example Ratter", 1000000401)
        self.token = _token(self.user, self.character, WALLET_SCOPE)
        get_token = mock.patch.object(tasks.Token, "get_token", return_value=self.token)
        get_token.start()
        self.addCleanup(get_token.stop)
        delay = mock.patch.object(tasks.resolve_type_names, "delay")
        self.resolve = delay.start()
        self.addCleanup(delay.stop)

    def test_lists_only_granted_and_not_opted_out(self):
        other, other_main = _pilot("other", "Other Pilot", 1000000402)
        self.assertEqual(tasks.wallet_character_ids(), [self.character.character_id])
        PilotSettings.objects.create(user=self.user, wallet_opt_out=True)
        self.assertEqual(tasks.wallet_character_ids(), [])

    def test_stores_payouts_and_queues_npc_names(self):
        journal = [_entry(10, "bounty_prizes", 2_000_000.0, reason="23707: 4")]
        with mock.patch.object(tasks.providers, "wallet_journal", return_value=journal) as call:
            tasks.pull_wallet_journal(self.character.character_id)
        call.assert_called_once_with(self.character.character_id, self.token)
        self.assertEqual(Payout.objects.count(), 1)
        self.resolve.assert_called_once_with([23707])

    def test_not_modified_is_a_no_op(self):
        with mock.patch.object(
            tasks.providers, "wallet_journal", side_effect=HTTPNotModified(304, {}),
        ):
            tasks.pull_wallet_journal(self.character.character_id)
        self.assertFalse(Payout.objects.exists())

    def test_opted_out_user_is_never_called(self):
        PilotSettings.objects.create(user=self.user, wallet_opt_out=True)
        with mock.patch.object(tasks.providers, "wallet_journal") as call:
            tasks.pull_wallet_journal(self.character.character_id)
        call.assert_not_called()

    def test_missing_scope_is_never_called(self):
        with mock.patch.object(tasks.Token, "get_token", return_value=None), \
                mock.patch.object(tasks.providers, "wallet_journal") as call:
            tasks.pull_wallet_journal(self.character.character_id)
        call.assert_not_called()

    def test_rate_limit_retries(self):
        error = ESIBucketLimitException("char-wallet", 30)
        with mock.patch.object(tasks.providers, "wallet_journal", side_effect=error), \
                mock.patch.object(tasks.pull_wallet_journal, "retry", side_effect=RuntimeError("retry")) as retry:
            with self.assertRaisesRegex(RuntimeError, "retry"):
                tasks.pull_wallet_journal(self.character.character_id)
        self.assertGreaterEqual(retry.call_args.kwargs["countdown"], 60)


class WalletOptOutViewTests(TestCase):
    def test_opting_out_deletes_stored_wallet_data(self):
        user, character = _pilot("ratter", "Example Ratter", 1000000401)
        payouts.store_journal(character, [_entry(1, "bounty_prizes", 1_000_000.0, reason="23707: 2")])
        self.client.force_login(user)
        response = self.client.post(reverse("pilotstats:events"), {"wallet_opt_out": "on"})
        self.assertRedirects(response, reverse("pilotstats:events"))
        self.assertTrue(PilotSettings.objects.get(user=user).wallet_opt_out)
        self.assertFalse(Payout.objects.exists())
        self.assertFalse(PayoutDaily.objects.exists())
        self.assertFalse(NpcKillDaily.objects.exists())

    def test_connect_wallet_needs_login(self):
        response = self.client.get(reverse("pilotstats:connect_wallet"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("pilotstats", response.url.split("?")[0])


# ---------------------------------------------------------------------------
# zKill R2Z2
# ---------------------------------------------------------------------------


def _package(killmail_id, *, victim_char=None, attacker_chars=(), corp=CORP, alliance=ALLIANCE,
             value=150_000_000.0, when="2026-11-27T11:00:00Z"):
    victim = {"ship_type_id": 587, "damage_taken": 3000, "corporation_id": 1}
    if victim_char:
        victim.update(character_id=victim_char, corporation_id=corp, alliance_id=alliance)
    attackers = [
        {"character_id": c, "corporation_id": corp, "alliance_id": alliance,
         "damage_done": 1000 + i, "final_blow": i == 0, "ship_type_id": 17738}
        for i, c in enumerate(attacker_chars)
    ] or [{"character_id": 1, "corporation_id": 1, "damage_done": 10, "ship_type_id": 600}]
    return {
        "killmail_id": killmail_id, "hash": f"hash{killmail_id}",
        "esi": {"killmail_id": killmail_id, "killmail_time": when, "solar_system_id": 30000142,
                "victim": victim, "attackers": attackers},
        "zkb": {"totalValue": value, "npc": False, "solo": len(attacker_chars) == 1},
    }


class FakeR2Z2:
    """requests.Session stand-in serving sequence.json and {seq}.json."""

    def __init__(self, latest, files, status=None):
        self.latest, self.files, self.status = latest, files, status or {}
        self.headers, self.calls = {}, []

    def get(self, url, timeout=None):
        self.calls.append(url.rsplit("/", 1)[-1])
        name = url.rsplit("/", 1)[-1]
        if name == "sequence.json":
            return SimpleNamespace(status_code=200, json=lambda: {"sequence": self.latest},
                                   raise_for_status=lambda: None)
        seq = int(name.split(".")[0])
        if seq in self.status:
            return SimpleNamespace(status_code=self.status[seq], raise_for_status=lambda: None)
        if seq in self.files:
            data = self.files[seq]
            return SimpleNamespace(status_code=200, json=lambda: data, raise_for_status=lambda: None)
        return SimpleNamespace(status_code=404, raise_for_status=lambda: None)


class R2Z2Tests(TestCase):
    def setUp(self):
        _local_cache(self)
        self.user, self.member = _pilot("member", "Example Member", 1000000501, member=True)
        delay = mock.patch.object(tasks.resolve_type_names, "delay")
        delay.start()
        self.addCleanup(delay.stop)

    def _run(self, fake, **kwargs):
        with mock.patch.object(tasks.requests, "Session", return_value=fake):
            tasks.drain_r2z2(interval=0, **kwargs)

    def test_tracks_member_mains_corp_and_alliance_automatically(self):
        zkill.refresh_auto_entities()
        kinds = set(TrackedEntity.objects.values_list("kind", "entity_id", "source"))
        self.assertIn(("corporation", CORP, "auto"), kinds)
        self.assertIn(("alliance", ALLIANCE, "auto"), kinds)

    def test_first_run_starts_at_the_current_sequence(self):
        fake = FakeR2Z2(latest=500, files={500: _package(1, attacker_chars=[1000000501])})
        self._run(fake)
        self.assertEqual(R2Z2State.get().last_sequence, 500)
        self.assertTrue(Killmail.objects.filter(killmail_id=1).exists())

    def test_resumes_stops_at_404_and_dedupes(self):
        R2Z2State.objects.create(pk=1, last_sequence=99)
        same = _package(7, attacker_chars=[1000000501])
        fake = FakeR2Z2(latest=101, files={100: same, 101: same})
        self._run(fake)
        self.assertEqual(R2Z2State.get().last_sequence, 101)
        self.assertEqual(Killmail.objects.count(), 1)
        self.assertEqual(KillmailParticipant.objects.count(), 1)
        self.assertEqual(fake.calls[:2], ["100.json", "101.json"])
        self.assertEqual(fake.calls[-1], "sequence.json")  # 102 missing and not yet published

    def test_keeps_only_tracked_auth_characters(self):
        R2Z2State.objects.create(pk=1, last_sequence=0)
        EveCharacter.objects.create(character_id=1000000999, character_name="Example Outsider",
                                    corporation_id=12345)
        files = {
            1: _package(1, attacker_chars=[1000000501, 2000000001]),  # member + unknown pilot
            2: _package(2, attacker_chars=[1000000999], corp=12345, alliance=None),  # untracked
            3: _package(3, victim_char=1000000501, value=20_000_000.0),  # member loss
        }
        self._run(FakeR2Z2(latest=3, files=files))
        self.assertEqual(sorted(Killmail.objects.values_list("killmail_id", flat=True)), [1, 3])
        self.assertEqual(KillmailParticipant.objects.count(), 2)
        loss = KillmailParticipant.objects.get(killmail__killmail_id=3)
        self.assertTrue(loss.is_victim)
        self.assertEqual(loss.damage, 3000)

    def test_rate_limit_does_not_advance(self):
        R2Z2State.objects.create(pk=1, last_sequence=9)
        self._run(FakeR2Z2(latest=12, files={}, status={10: 429}))
        self.assertEqual(R2Z2State.get().last_sequence, 9)

    def test_skips_a_missing_file_inside_the_window(self):
        R2Z2State.objects.create(pk=1, last_sequence=9)
        fake = FakeR2Z2(latest=11, files={11: _package(5, attacker_chars=[1000000501])})
        self._run(fake)
        self.assertEqual(R2Z2State.get().last_sequence, 11)
        self.assertTrue(Killmail.objects.filter(killmail_id=5).exists())

    def test_lock_prevents_overlap(self):
        tasks.cache.add(tasks.R2Z2_LOCK, 1, timeout=60)
        fake = FakeR2Z2(latest=5, files={})
        self._run(fake)
        self.assertEqual(fake.calls, [])

    def test_admin_disabled_auto_row_stays_disabled(self):
        zkill.refresh_auto_entities()
        TrackedEntity.objects.filter(kind="corporation").update(enabled=False)
        zkill.refresh_auto_entities()
        self.assertFalse(TrackedEntity.objects.get(kind="corporation").enabled)
        self.assertEqual(zkill.tracked_ids(), (set(), {ALLIANCE}))


# ---------------------------------------------------------------------------
# Fleets
# ---------------------------------------------------------------------------


class FleetTests(TestCase):
    def setUp(self):
        _local_cache(self)
        self.user, self.boss = _pilot("boss", "Example Boss", 1000000601)
        self.token = _token(self.user, self.boss, FLEET_SCOPE)
        self.wingman_user, self.wingman = _pilot("wingman", "Example Wingman", 1000000602)
        patcher = mock.patch.object(fleets, "boss_token", return_value=self.token)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fleet_info(self, fleet_id=555, boss_id=1000000601):
        return SimpleNamespace(fleet_id=fleet_id, fleet_boss_id=boss_id, role="fleet_commander")

    def test_only_the_boss_can_start(self):
        with mock.patch.object(fleets.providers, "character_fleet",
                               return_value=self._fleet_info(boss_id=1000000602)):
            with self.assertRaises(fleets.FleetError):
                fleets.start_tracking(self.user, self.token)
        with mock.patch.object(fleets.providers, "character_fleet", return_value=self._fleet_info()):
            fleet = fleets.start_tracking(self.user, self.token)
            again = fleets.start_tracking(self.user, self.token)
        self.assertEqual(fleet.pk, again.pk)

    def test_poll_records_members_hull_changes_and_coverage(self):
        fleet = TrackedFleet.objects.create(fleet_id=555, boss=self.boss, started_by=self.user)
        roster = [
            {"character_id": 1000000601, "ship_type_id": 22474},
            {"character_id": 1000000602, "ship_type_id": 17738},
            {"character_id": 2000000001, "ship_type_id": 17738},  # not on Auth
        ]
        t0 = timezone.now()
        with mock.patch.object(fleets.providers, "character_fleet", return_value=self._fleet_info()), \
                mock.patch.object(fleets.providers, "fleet_members", return_value=roster):
            fleets.poll_fleet(fleet, now=t0)
            roster[1]["ship_type_id"] = 17740  # wingman reships
            fleets.poll_fleet(fleet, now=t0 + timedelta(minutes=1))
        self.assertEqual(FleetMember.objects.filter(eve_character_id=1000000602).count(), 2)
        self.assertIsNone(FleetMember.objects.get(eve_character_id=2000000001).character)

        CombatLogUpload.objects.create(
            character=self.wingman, uploaded_by=self.wingman_user, source_filename="w.txt",
            file_hash="hash-w", damage_out=900,
            session_started=t0 - timedelta(minutes=5), session_ended=t0 + timedelta(minutes=10),
        )
        cover = fleets.coverage(fleet)
        self.assertEqual((cover.on_auth, cover.with_log, cover.not_on_auth), (2, 1, 1))
        upload = CombatLogUpload.objects.get(file_hash="hash-w")
        self.assertIn(fleets.hull_for_session(upload), (17738, 17740))
        self.assertIsNotNone(CombatLogUpload.objects.get(pk=upload.pk).ship_type_id)

    def test_poll_closes_when_boss_leaves_or_hands_over(self):
        fleet = TrackedFleet.objects.create(fleet_id=555, boss=self.boss, started_by=self.user)
        with mock.patch.object(fleets.providers, "character_fleet",
                               side_effect=HTTPClientError(404, {}, None)):
            fleets.poll_fleet(fleet)
        self.assertFalse(TrackedFleet.objects.get(pk=fleet.pk).is_open)

        other = TrackedFleet.objects.create(fleet_id=556, boss=self.boss, started_by=self.user)
        with mock.patch.object(fleets.providers, "character_fleet",
                               return_value=self._fleet_info(fleet_id=556, boss_id=1000000602)):
            fleets.poll_fleet(other)
        self.assertIn("handed", TrackedFleet.objects.get(pk=other.pk).end_reason)

    def test_only_starter_or_boss_can_stop(self):
        fleet = TrackedFleet.objects.create(fleet_id=555, boss=self.boss, started_by=self.user)
        self.client.force_login(self.wingman_user)
        self.assertEqual(self.client.post(reverse("pilotstats:stop_fleet", args=[fleet.pk])).status_code, 403)
        self.client.force_login(self.user)
        self.client.post(reverse("pilotstats:stop_fleet", args=[fleet.pk]))
        self.assertFalse(TrackedFleet.objects.get(pk=fleet.pk).is_open)

    def test_pages_render(self):
        fleet = TrackedFleet.objects.create(fleet_id=555, boss=self.boss, started_by=self.user)
        FleetMember.objects.create(fleet=fleet, eve_character_id=1000000602, character=self.wingman,
                                   ship_type_id=17738, first_seen=timezone.now(), last_seen=timezone.now())
        TypeName.objects.create(type_id=17738, name="Example Hull")
        self.client.force_login(self.wingman_user)
        self.assertContains(self.client.get(reverse("pilotstats:fleets")), "Example Boss")
        page = self.client.get(reverse("pilotstats:fleet", args=[fleet.pk]))
        self.assertContains(page, "Example Hull")
        self.assertContains(page, "no log uploaded")


# ---------------------------------------------------------------------------
# Leaderboard merge, session links, names
# ---------------------------------------------------------------------------


class LeaderboardMergeTests(TestCase):
    def setUp(self):
        self.user, self.main = _pilot("pilot", "Example Main", 1000000701)
        self.alt = EveCharacter.objects.create(
            character_id=1000000702, character_name="Example Alt", corporation_id=CORP,
        )
        _own(self.user, self.alt)

    def test_sources_merge_per_main_and_double_alt_kill_counts_once(self):
        now = timezone.now()
        payouts.store_journal(self.main, [_entry(1, "bounty_prizes", 3e9, when=now, reason="23707: 10")])
        payouts.store_journal(self.alt, [_entry(2, "ess_escrow_transfer", 1e9, when=now)])
        km = Killmail.objects.create(killmail_id=1, killmail_hash="h", killmail_time=now,
                                     total_value=Decimal("500000000"))
        KillmailParticipant.objects.create(killmail=km, character=self.main, damage=700)
        KillmailParticipant.objects.create(killmail=km, character=self.alt, damage=300)
        loss = Killmail.objects.create(killmail_id=2, killmail_hash="h", killmail_time=now,
                                       total_value=Decimal("80000000"))
        KillmailParticipant.objects.create(killmail=loss, character=self.alt, is_victim=True)

        rows = leaderboard_rows("7d", "payout_isk")
        self.assertEqual(len(rows), 1)  # no Gamelog session, still a row
        row = rows[0]
        self.assertEqual(row["label"], "Example Main")
        self.assertEqual(row["payout_isk"], Decimal("4000000000.00"))
        self.assertEqual(row["rats_killed"], 10)
        self.assertEqual((row["kills"], row["losses"]), (1, 1))
        self.assertEqual(row["isk_destroyed"], Decimal("500000000"))
        self.assertEqual(row["km_damage"], 1000)

    def test_leaderboard_page_sorts_by_a_source_column(self):
        other, other_main = _pilot("other", "Other Main", 1000000703)
        payouts.store_journal(other_main, [_entry(9, "bounty_prizes", 9e8, when=timezone.now())])
        CombatLogUpload.objects.create(character=self.main, uploaded_by=self.user,
                                       source_filename="a.txt", file_hash="h-a", damage_out=5000)
        self.client.force_login(self.user)
        page = self.client.get(reverse("pilotstats:leaderboard"), {"range": "all", "sort": "payout_isk"})
        self.assertEqual(page.context["sort_key"], "payout_isk")
        self.assertEqual([r["label"] for r in page.context["rows"]], ["Other Main", "Example Main"])
        self.assertContains(page, "900.00M ISK")


class SessionLinkTests(TestCase):
    def test_session_page_shows_payouts_rats_and_kills_separately(self):
        user, main = _pilot("pilot", "Example Main", 1000000801)
        start = timezone.now() - timedelta(hours=1)
        upload = CombatLogUpload.objects.create(
            character=main, uploaded_by=user, source_filename="s.txt", file_hash="h-s",
            damage_out=12345, session_started=start, session_ended=start + timedelta(minutes=40),
        )
        payouts.store_journal(main, [
            _entry(1, "bounty_prizes", 2.5e7, when=start + timedelta(minutes=20), reason="23707: 6"),
            _entry(2, "bounty_prizes", 9e9, when=start - timedelta(hours=3), reason="23707: 99"),
        ])
        TypeName.objects.create(type_id=23707, name="Example Rat")
        self.client.force_login(user)
        page = self.client.get(reverse("pilotstats:session", args=[upload.pk]))
        self.assertContains(page, "25.00M ISK")
        self.assertNotContains(page, "9.00B ISK")  # outside the window
        self.assertContains(page, "Example Rat")
        self.assertEqual(page.context["linked"]["rat_total"], 6)
        self.assertContains(page, "12345")  # Gamelog damage untouched


class TypeNameTests(TestCase):
    def test_names_never_hit_the_network_and_resolve_fills_the_cache(self):
        self.assertEqual(typenames.names([587]), {})

        def fake_get(url, timeout=None):
            if url.endswith("/types/587.json"):
                return SimpleNamespace(status_code=200, json=lambda: {"name": {"en": "Rifter"}, "groupID": 25})
            if url.endswith("/groups/25.json"):
                return SimpleNamespace(status_code=200, json=lambda: {"name": {"en": "Frigate"}})
            return SimpleNamespace(status_code=404, json=lambda: {})

        session = SimpleNamespace(headers={}, get=fake_get)
        with mock.patch.object(typenames.requests, "Session", return_value=session):
            self.assertEqual(typenames.resolve([587, 0, None]), 1)
        self.assertEqual(typenames.names([587]), {587: "Rifter"})
        self.assertEqual(TypeName.objects.get(type_id=587).group_name, "Frigate")
