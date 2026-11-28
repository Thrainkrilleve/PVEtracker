"""
ISK per hour: which wallet payouts belong to which Gamelog session, how the
rate is computed, and where it shows up. ESI is mocked; nothing leaves the test.
"""

# Standard Library
from datetime import timedelta
from decimal import Decimal
from unittest import mock

# Django
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

from .. import payouts, tasks
from ..app_settings import WALLET_SCOPE
from ..leaderboard import leaderboard_rows
from ..models import CombatLogUpload, PilotSettings
from .test_sources import _entry, _pilot, _token

M = Decimal(1_000_000)


class IskPerHourTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user, self.character = _pilot("ratter", "Example Ratter", 1000000701)
        self.n = 0

    def session(self, start_ago, minutes, character=None, user=None):
        """A session that started *start_ago* before now and lasted *minutes*."""
        self.n += 1
        start = self.now - start_ago
        return CombatLogUpload.objects.create(
            character=character or self.character, uploaded_by=user or self.user,
            source_filename=f"{self.n}.txt", file_hash=f"isk-{self.n}",
            session_started=start, session_ended=start + timedelta(minutes=minutes),
        )

    def pay(self, ref_id, when, amount, ref_type="bounty_prizes", character=None):
        payouts.store_journal(character or self.character, [_entry(ref_id, ref_type, float(amount), when=when)])

    def connect_wallet(self, user=None, character=None):
        return _token(user or self.user, character or self.character, WALLET_SCOPE)


class MatchingTests(IskPerHourTestCase):
    def test_counts_bounties_and_missions_in_the_window_but_not_ess(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        start, end = session.session_started, session.session_ended
        self.pay(1, start + timedelta(minutes=20), 10 * M)                      # during
        self.pay(2, end + timedelta(minutes=19), 5 * M)                          # last tick, within 20 min
        self.pay(3, start + timedelta(minutes=30), 2 * M, "agent_mission_reward")
        self.pay(4, start + timedelta(minutes=40), 50 * M, "ess_escrow_transfer")  # never counted
        self.pay(5, end + timedelta(minutes=30), 7 * M)                          # too late
        self.pay(6, start - timedelta(minutes=5), 7 * M)                          # before it started
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 17 * M)
        self.assertEqual(session.isk_per_hour, 17 * M)

    def test_overlapping_windows_give_the_payout_to_the_later_session(self):
        self.connect_wallet()
        first = self.session(timedelta(hours=5), 60)
        second = self.session(timedelta(hours=5) - timedelta(minutes=65), 60)  # starts 5 min after the first ends
        self.pay(1, first.session_ended + timedelta(minutes=10), 4 * M)       # inside both windows
        payouts.match_sessions(self.character)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.payout_isk, 0)
        self.assertEqual(second.payout_isk, 4 * M)
        self.assertEqual(first.payout_isk + second.payout_isk, 4 * M)

    def test_no_wallet_means_unknown_not_zero(self):
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 10 * M)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertIsNone(session.payout_isk)
        self.assertIsNone(session.isk_per_hour)

    def test_connected_wallet_with_no_payouts_is_zero(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 0)
        self.assertEqual(session.isk_per_hour, 0)

    def test_short_sessions_get_no_rate(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 5)
        self.pay(1, session.session_started + timedelta(minutes=3), 10 * M)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 10 * M)
        self.assertIsNone(session.isk_per_hour)

    def test_sessions_past_retention_keep_their_value(self):
        self.connect_wallet()
        old = self.session(timedelta(days=60), 60)
        old.payout_isk = Decimal("123.00")
        old.save(update_fields=["payout_isk"])
        payouts.match_sessions(self.character)
        old.refresh_from_db()
        self.assertEqual(old.payout_isk, Decimal("123.00"))

    def test_other_characters_payouts_are_ignored(self):
        self.connect_wallet()
        other_user, other = _pilot("other", "Example Other", 1000000702)
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 9 * M, character=other)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 0)

    def test_opting_out_clears_session_isk(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 10 * M)
        payouts.match_sessions(self.character)
        PilotSettings.objects.create(user=self.user, wallet_opt_out=True)
        payouts.delete_wallet_data(self.user)
        session.refresh_from_db()
        self.assertIsNone(session.payout_isk)
        # And it stays unknown while opted out.
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertIsNone(session.payout_isk)


class TriggerTests(IskPerHourTestCase):
    def test_wallet_pull_fills_sessions(self):
        token = self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        journal = [_entry(10, "bounty_prizes", 3_000_000.0, when=session.session_started + timedelta(minutes=20))]
        with mock.patch.object(tasks.Token, "get_token", return_value=token), \
                mock.patch.object(tasks.providers, "wallet_journal", return_value=journal), \
                mock.patch.object(tasks.resolve_type_names, "delay"):
            tasks.pull_wallet_journal(self.character.character_id)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 3 * M)

    def test_pull_with_nothing_new_still_marks_sessions_known(self):
        token = self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        with mock.patch.object(tasks.Token, "get_token", return_value=token), \
                mock.patch.object(tasks.providers, "wallet_journal", return_value=[]):
            tasks.pull_wallet_journal(self.character.character_id)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 0)

    def test_upload_picks_up_payouts_already_stored(self):
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], self.user)
        self.client.force_login(self.user)
        self.connect_wallet()
        start = self.now - timedelta(hours=3)
        end = start + timedelta(minutes=30)
        self.pay(1, start + timedelta(minutes=20), 6 * M)
        stamp = "%Y.%m.%d %H:%M:%S"
        log = (
            "------------------------------------------------------------\n"
            "  Gamelog\n"
            "  Listener: Example Ratter\n"
            f"  Session Started: {start:{stamp}}\n"
            "------------------------------------------------------------\n"
            f"[ {start:{stamp}} ] (combat) <color=0xff00ffff><b>1022</b> "
            "<color=0x77ffffff><font size=10>to</font> <b><color=0xffffffff>Example Rat</b>"
            "<font size=10><color=0x77ffffff> - Example Laser - Hits\n"
            f"[ {end:{stamp}} ] (combat) <color=0xff00ffff><b>900</b> "
            "<color=0x77ffffff><font size=10>to</font> <b><color=0xffffffff>Example Rat</b>"
            "<font size=10><color=0x77ffffff> - Example Laser - Hits\n"
        )
        with mock.patch.object(tasks.check_session_records, "delay"):
            self.client.post(reverse("pilotstats:upload"), {
                "log_file": SimpleUploadedFile("ratting.txt", log.encode("utf-8")),
            })
        session = CombatLogUpload.objects.get()
        self.assertEqual(session.payout_isk, 6 * M)
        self.assertEqual(session.isk_per_hour, 12 * M)


class LeaderboardTests(IskPerHourTestCase):
    def test_rate_is_weighted_by_hours_and_skips_unknown_sessions(self):
        self.connect_wallet()
        one = self.session(timedelta(hours=10), 60)     # 1 h, 10M
        two = self.session(timedelta(hours=5), 180)     # 3 h, 50M
        self.pay(1, one.session_started + timedelta(minutes=20), 10 * M)
        self.pay(2, two.session_started + timedelta(minutes=20), 50 * M)
        payouts.match_sessions(self.character)
        rows = leaderboard_rows("7d", "isk_per_hour")
        row = next(r for r in rows if r["label"] == "Example Ratter")
        self.assertEqual(row["isk_per_hour"], 15 * M)   # 60M over 4 h, not the mean of 10M and 16.7M

    def test_pilot_without_wallet_has_no_rate(self):
        self.session(timedelta(hours=5), 60)
        payouts.match_sessions(self.character)
        rows = leaderboard_rows("7d", "isk_per_hour")
        self.assertIsNone(rows[0]["isk_per_hour"])

    def test_sorting_by_rate(self):
        self.connect_wallet()
        slow = self.session(timedelta(hours=5), 60)
        self.pay(1, slow.session_started + timedelta(minutes=20), 10 * M)
        fast_user, fast = _pilot("fast", "Example Fast", 1000000703)
        _token(fast_user, fast, WALLET_SCOPE)
        quick = self.session(timedelta(hours=4), 60, character=fast, user=fast_user)
        self.pay(2, quick.session_started + timedelta(minutes=20), 40 * M, character=fast)
        payouts.match_sessions(self.character)
        payouts.match_sessions(fast)
        rows = leaderboard_rows("7d", "isk_per_hour")
        self.assertEqual([r["label"] for r in rows[:2]], ["Example Fast", "Example Ratter"])

    def test_page_shows_the_column(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 20 * M)
        payouts.match_sessions(self.character)
        self.client.force_login(self.user)
        response = self.client.get(reverse("pilotstats:leaderboard") + "?range=7d&sort=isk_per_hour")
        self.assertContains(response, "ISK/h")
        self.assertContains(response, "20.00M ISK")


class SessionPageTests(IskPerHourTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_shows_rate(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 120)
        self.pay(1, session.session_started + timedelta(minutes=20), 30 * M)
        payouts.match_sessions(self.character)
        response = self.client.get(reverse("pilotstats:session", args=[session.pk]))
        self.assertContains(response, "ISK per hour")
        self.assertContains(response, "15.00M ISK/h")

    def test_explains_missing_wallet(self):
        session = self.session(timedelta(hours=5), 60)
        response = self.client.get(reverse("pilotstats:session", args=[session.pk]))
        self.assertContains(response, "wallet connected")
