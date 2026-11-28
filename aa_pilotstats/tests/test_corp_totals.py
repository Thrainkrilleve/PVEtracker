"""
Corporation and alliance totals on the leaderboard, and the CSV export.
"""

# Standard Library
import csv
import io
from datetime import timedelta
from decimal import Decimal

# Django
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.tests.auth_utils import AuthUtils

from ..leaderboard import group_rows, leaderboard_rows
from ..models import CombatLogUpload, Killmail, KillmailParticipant

M = Decimal(1_000_000)
CORP_A, CORP_B, ALLIANCE = 98000101, 98000102, 99000101


def _member(username, name, character_id, corp_id, corp_name, alliance_id=None, alliance_name=""):
    user = AuthUtils.create_user(username)
    main = AuthUtils.add_main_character_2(
        user, name, character_id, corp_id=corp_id, corp_name=corp_name,
        alliance_id=alliance_id, alliance_name=alliance_name, disconnect_signals=True,
    )
    CharacterOwnership.objects.create(character=main, user=user, owner_hash=f"hash-{character_id}")
    return user, main


class CorpTotalsTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.a1_user, self.a1 = _member("a1", "Example Alpha One", 1000001001, CORP_A, "Example Corp A", ALLIANCE, "Example Alliance")
        self.a2_user, self.a2 = _member("a2", "Example Alpha Two", 1000001002, CORP_A, "Example Corp A", ALLIANCE, "Example Alliance")
        self.b1_user, self.b1 = _member("b1", "Example Bravo One", 1000001003, CORP_B, "Example Corp B", ALLIANCE, "Example Alliance")
        self.n = 0
        # a1: 1 h earning 30M; a2: 3 h earning 30M; b1: 2 h, no wallet data.
        self.session(self.a1_user, self.a1, 60, 100_000, 30 * M)
        self.session(self.a2_user, self.a2, 180, 300_000, 30 * M)
        self.session(self.b1_user, self.b1, 120, 50_000, None)

    def session(self, user, character, minutes, damage, payout):
        self.n += 1
        start = self.now - timedelta(hours=6 + self.n)
        CombatLogUpload.objects.create(
            character=character, uploaded_by=user, source_filename=f"{self.n}.txt", file_hash=f"corp-{self.n}",
            session_started=start, session_ended=start + timedelta(minutes=minutes),
            damage_out=damage, payout_isk=payout,
        )


class GroupRowsTests(CorpTotalsTestCase):
    def test_sums_by_main_corporation(self):
        groups = group_rows(leaderboard_rows("7d", "damage_out"), by="corporation")
        self.assertEqual(
            [(g["label"], g["pilot_count"], g["damage_out"], g["upload_count"]) for g in groups],
            [("Example Corp A", 2, 400_000, 2), ("Example Corp B", 1, 50_000, 1)],
        )
        self.assertEqual(groups[0]["alliance"], "Example Alliance")

    def test_isk_per_hour_is_weighted_over_all_member_hours(self):
        corp_a = group_rows(leaderboard_rows("7d", "damage_out"), by="corporation")[0]
        self.assertEqual(corp_a["isk_per_hour"], 15 * M)  # 60M over 4 h, not the mean of 30M/h and 10M/h
        corp_b = group_rows(leaderboard_rows("7d", "damage_out"), by="corporation")[1]
        self.assertIsNone(corp_b["isk_per_hour"])

    def test_alliance_groups_merge_corporations(self):
        groups = group_rows(leaderboard_rows("7d", "damage_out"), by="alliance")
        self.assertEqual([(g["label"], g["pilot_count"], g["damage_out"]) for g in groups],
                         [("Example Alliance", 3, 450_000)])

    def test_a_shared_kill_counts_once_per_corporation(self):
        mail = Killmail.objects.create(killmail_id=77, killmail_hash="h", killmail_time=self.now - timedelta(hours=1),
                                       total_value=Decimal("500000000"))
        KillmailParticipant.objects.create(killmail=mail, character=self.a1, damage=100)
        KillmailParticipant.objects.create(killmail=mail, character=self.a2, damage=200)
        rows = leaderboard_rows("7d", "damage_out")
        self.assertEqual(sorted(r["kills"] for r in rows if r["label"].startswith("Example Alpha")), [1, 1])
        corp_a = group_rows(rows, by="corporation")[0]
        self.assertEqual((corp_a["kills"], corp_a["isk_destroyed"]), (1, Decimal("500000000")))
        self.assertEqual(corp_a["km_damage"], 300)

    def test_pilots_without_a_main_go_last_as_unknown(self):
        user = AuthUtils.create_user("nomain")
        character = AuthUtils.add_main_character_2(user, "Example Loner", 1000001009, corp_id=98000109)
        user.profile.main_character = None
        user.profile.save()
        CombatLogUpload.objects.create(
            character=character, uploaded_by=user, source_filename="x.txt", file_hash="corp-x",
            session_started=self.now - timedelta(hours=1), session_ended=self.now, damage_out=9_999_999,
        )
        groups = group_rows(leaderboard_rows("7d", "damage_out"), by="corporation")
        self.assertFalse(groups[-1]["known"])
        self.assertEqual(groups[-1]["damage_out"], 9_999_999)

    def test_sort_key_orders_groups(self):
        groups = group_rows(leaderboard_rows("7d", "isk_per_hour"), by="corporation", sort_key="isk_per_hour")
        self.assertEqual(groups[0]["label"], "Example Corp A")


class PageAndCsvTests(CorpTotalsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.a1_user)
        self.url = reverse("pilotstats:leaderboard")

    def csv_rows(self, query):
        response = self.client.get(self.url + query)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        return response, list(csv.reader(io.StringIO(response.content.decode("utf-8"))))

    def test_corporation_view(self):
        response = self.client.get(self.url + "?range=7d&group=corporation")
        self.assertContains(response, "Example Corp A")
        self.assertContains(response, "Corporations")
        self.assertEqual(response.context["groups"][0]["pilot_count"], 2)

    def test_unknown_group_falls_back_to_pilots(self):
        response = self.client.get(self.url + "?range=7d&group=nonsense")
        self.assertIsNone(response.context["groups"])

    def test_pilot_csv(self):
        response, rows = self.csv_rows("?range=7d&format=csv")
        self.assertIn('filename="pilotstats-pilots-7d.csv"', response["Content-Disposition"])
        self.assertEqual(rows[0][:5], ["Pilot", "Corporation", "Alliance", "Sessions", "Damage dealt"])
        self.assertIn("Payout ISK", rows[0])
        alpha_two = next(r for r in rows if r[0] == "Example Alpha Two")
        self.assertEqual(alpha_two[1:5], ["Example Corp A", "Example Alliance", "1", "300000"])
        self.assertEqual(alpha_two[rows[0].index("ISK per hour")], "10000000.00")

    def test_corporation_csv_with_activity_leaves_out_wallet_columns(self):
        CombatLogUpload.objects.update(activity=CombatLogUpload.Activity.RATTING)
        response, rows = self.csv_rows("?range=7d&group=corporation&activity=ratting&format=csv")
        self.assertIn('filename="pilotstats-corporation-7d-ratting.csv"', response["Content-Disposition"])
        self.assertEqual(rows[0][:3], ["Corporation", "Pilots", "Sessions"])
        self.assertNotIn("Payout ISK", rows[0])
        self.assertEqual(rows[1][:2], ["Example Corp A", "2"])

    def test_names_cant_become_spreadsheet_formulas(self):
        user, character = _member("evil", "=HYPERLINK(1)", 1000001010, 98000110, "+Example Corp")
        CombatLogUpload.objects.create(
            character=character, uploaded_by=user, source_filename="e.txt", file_hash="corp-e",
            session_started=self.now - timedelta(hours=1), session_ended=self.now, damage_out=1,
        )
        _response, rows = self.csv_rows("?range=7d&format=csv")
        evil = next(r for r in rows if "HYPERLINK" in r[0])
        self.assertEqual(evil[0], "'=HYPERLINK(1)")
        self.assertEqual(evil[1], "'+Example Corp")
