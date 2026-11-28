"""
Activity types: the classifier's rules, how sessions get classified (wallet
pull, upload, backfill command, opt-out), and the leaderboard filter.
"""

# Standard Library
from datetime import timedelta
from decimal import Decimal
from io import StringIO

# Django
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .. import payouts
from ..activity import classify
from ..app_settings import WALLET_SCOPE
from ..leaderboard import leaderboard_rows
from ..models import CombatLogUpload, PilotSettings
from .test_sources import _entry, _pilot, _token

Activity = CombatLogUpload.Activity
RATS = [["Pithatis Death Dealer", 9000], ["Corrupted Manager", 4000]]
INCURSION = [["Romi Thalamus", 7000], ["Mara Paleo", 3000], ["Some Frigate", 1000]]
ABYSSAL = [["Starving Damavik", 5000], ["Lucid Deepwatcher", 4000]]


class ClassifyTests(TestCase):
    def test_missions_win_when_a_mission_reward_lands(self):
        self.assertEqual(classify(RATS, {"agent_mission_reward", "bounty_prizes"}), Activity.MISSION)

    def test_bounties_mean_ratting(self):
        self.assertEqual(classify(RATS, {"bounty_prizes"}), Activity.RATTING)

    def test_incursion_names(self):
        self.assertEqual(classify(INCURSION, set()), Activity.INCURSION)
        self.assertEqual(classify(INCURSION, None), Activity.INCURSION)  # no wallet needed

    def test_abyssal_names_without_bounties(self):
        self.assertEqual(classify(ABYSSAL, set()), Activity.ABYSSAL)
        self.assertEqual(classify(ABYSSAL, None), Activity.ABYSSAL)

    def test_triglavians_with_bounties_are_ratting(self):
        # Triglavian NPCs outside the abyss pay bounties; abyssal sites never do.
        self.assertEqual(classify(ABYSSAL, {"bounty_prizes"}), Activity.RATTING)

    def test_a_few_named_npcs_dont_decide_it(self):
        mostly_rats = [["Pithatis Death Dealer", 9000], ["Romi Thalamus", 1000]]
        self.assertEqual(classify(mostly_rats, {"bounty_prizes"}), Activity.RATTING)

    def test_words_match_whole_words_any_case(self):
        self.assertEqual(classify([["SCHMAEEL MEDULLA", 10]], None), Activity.INCURSION)
        self.assertEqual(classify([["Medullary Thing", 10]], None), "")

    def test_wallet_with_nothing_is_other_and_no_wallet_is_unknown(self):
        self.assertEqual(classify(RATS, set()), Activity.OTHER)
        self.assertEqual(classify(RATS, None), "")
        self.assertEqual(classify([], None), "")


class SessionClassificationTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user, self.character = _pilot("pilot", "Example Pilot", 1000000901)
        self.n = 0

    def session(self, ago, minutes=60, targets=RATS):
        self.n += 1
        start = self.now - ago
        return CombatLogUpload.objects.create(
            character=self.character, uploaded_by=self.user, source_filename=f"{self.n}.txt",
            file_hash=f"act-{self.n}", damage_out=sum(d for _n, d in targets), top_targets=targets,
            session_started=start, session_ended=start + timedelta(minutes=minutes),
        )

    def pay(self, ref_id, when, ref_type="bounty_prizes"):
        payouts.store_journal(self.character, [_entry(ref_id, ref_type, 1_000_000.0, when=when)])

    def test_wallet_decides_ratting_and_missions(self):
        _token(self.user, self.character, WALLET_SCOPE)
        ratting = self.session(timedelta(hours=8))
        missions = self.session(timedelta(hours=4))
        idle = self.session(timedelta(hours=2))
        self.pay(1, ratting.session_started + timedelta(minutes=20))
        self.pay(2, missions.session_started + timedelta(minutes=30), "agent_mission_reward")
        payouts.match_sessions(self.character)
        for session in (ratting, missions, idle):
            session.refresh_from_db()
        self.assertEqual(ratting.activity, Activity.RATTING)
        self.assertEqual(missions.activity, Activity.MISSION)
        self.assertEqual(idle.activity, Activity.OTHER)

    def test_without_wallet_names_still_classify(self):
        incursion = self.session(timedelta(hours=8), targets=INCURSION)
        rats = self.session(timedelta(hours=4))
        payouts.match_sessions(self.character)
        incursion.refresh_from_db()
        rats.refresh_from_db()
        self.assertEqual(incursion.activity, Activity.INCURSION)
        self.assertEqual(rats.activity, "")

    def test_old_sessions_get_names_only_from_the_command(self):
        old_abyssal = self.session(timedelta(days=90), targets=ABYSSAL)
        old_rats = self.session(timedelta(days=90), targets=RATS)
        out = StringIO()
        call_command("pilotstats_classify", stdout=out)
        old_abyssal.refresh_from_db()
        old_rats.refresh_from_db()
        self.assertEqual(old_abyssal.activity, Activity.ABYSSAL)
        self.assertEqual(old_rats.activity, "")
        self.assertIn("older one(s) from NPC names", out.getvalue())

    def test_opting_out_drops_wallet_based_activity_but_keeps_names(self):
        _token(self.user, self.character, WALLET_SCOPE)
        ratting = self.session(timedelta(hours=8))
        incursion = self.session(timedelta(hours=4), targets=INCURSION)
        self.pay(1, ratting.session_started + timedelta(minutes=20))
        payouts.match_sessions(self.character)
        PilotSettings.objects.create(user=self.user, wallet_opt_out=True)
        payouts.delete_wallet_data(self.user)
        ratting.refresh_from_db()
        incursion.refresh_from_db()
        self.assertEqual(ratting.activity, "")
        self.assertEqual(incursion.activity, Activity.INCURSION)


class LeaderboardFilterTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user, self.character = _pilot("pilot", "Example Pilot", 1000000911)
        self.other_user, self.other = _pilot("other", "Example Other", 1000000912)
        for n, (user, character, kind, damage) in enumerate((
            (self.user, self.character, Activity.RATTING, 100_000),
            (self.user, self.character, Activity.INCURSION, 900_000),
            (self.other_user, self.other, Activity.RATTING, 500_000),
        )):
            start = self.now - timedelta(hours=n + 1)
            CombatLogUpload.objects.create(
                character=character, uploaded_by=user, source_filename=f"{n}.txt", file_hash=f"lb-{n}",
                session_started=start, session_ended=start + timedelta(minutes=30),
                damage_out=damage, activity=kind, payout_isk=Decimal(damage),
            )
        self.client.force_login(self.user)

    def test_rows_only_count_that_activity(self):
        rows = leaderboard_rows("7d", "damage_out", activity=Activity.RATTING)
        self.assertEqual([(r["label"], r["damage_out"]) for r in rows],
                         [("Example Other", 500_000), ("Example Pilot", 100_000)])
        self.assertNotIn("payout_isk", rows[0])  # wallet totals aren't split by activity

    def test_isk_per_hour_sort_works_with_a_filter(self):
        rows = leaderboard_rows("7d", "isk_per_hour", activity=Activity.RATTING)
        self.assertEqual(rows[0]["label"], "Example Other")

    def test_page_hides_wallet_and_killmail_columns(self):
        url = reverse("pilotstats:leaderboard")
        response = self.client.get(url + "?range=7d&activity=incursion&sort=payout_isk")
        rows = response.context["rows"]
        self.assertEqual([(r["label"], r["damage_out"]) for r in rows], [("Example Pilot", 900_000)])
        self.assertNotContains(response, "Killmail Dmg")
        self.assertContains(response, "can't be split by activity")
        self.assertEqual(response.context["sort_key"], "damage_out")  # payout sort isn't offered here

    def test_unknown_activity_is_ignored(self):
        response = self.client.get(reverse("pilotstats:leaderboard") + "?range=7d&activity=nonsense")
        self.assertEqual(response.context["activity"], "")
        self.assertContains(response, "Killmail Dmg")

    def test_session_page_shows_it(self):
        session = CombatLogUpload.objects.get(activity=Activity.INCURSION)
        self.assertContains(self.client.get(reverse("pilotstats:session", args=[session.pk])), "Incursion")
