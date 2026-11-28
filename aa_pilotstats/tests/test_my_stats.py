"""
My Stats: weekly trends (EVE weeks, time-weighted rates), personal records,
and the page itself. Only the pilot's own data is ever shown.
"""

# Standard Library
import json
import re
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

# Django
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .. import trends
from ..app_settings import WALLET_SCOPE
from ..models import CombatLogUpload, NpcKillDaily, PayoutDaily
from .test_sources import _pilot, _token

# A Wednesday; its EVE week starts Monday 2026-11-23.
NOW = datetime(2026, 11, 25, 18, 0, tzinfo=dt_timezone.utc)
M = Decimal(1_000_000)


class TrendsTestCase(TestCase):
    def setUp(self):
        self.user, self.character = _pilot("pilot", "Example Pilot", 1000000801)
        self.n = 0

    def session(self, start, minutes, damage=0, payout=None, user=None, character=None):
        self.n += 1
        return CombatLogUpload.objects.create(
            character=character or self.character, uploaded_by=user or self.user,
            source_filename=f"{self.n}.txt", file_hash=f"trend-{self.n}",
            session_started=start, session_ended=start + timedelta(minutes=minutes),
            damage_out=damage, payout_isk=payout,
        )


class WeekStartTests(TestCase):
    def test_weeks_start_monday_utc(self):
        self.assertEqual(trends.week_start(NOW), datetime(2026, 11, 23, tzinfo=dt_timezone.utc))
        monday_morning = datetime(2026, 11, 23, 0, 0, 1, tzinfo=dt_timezone.utc)
        self.assertEqual(trends.week_start(monday_morning).date(), date(2026, 11, 23))
        sunday_night = datetime(2026, 11, 22, 23, 59, tzinfo=dt_timezone.utc)
        self.assertEqual(trends.week_start(sunday_night).date(), date(2026, 11, 16))


class WeeklyTests(TrendsTestCase):
    def test_twelve_weeks_oldest_first(self):
        weeks = trends.weekly(self.user, now=NOW)
        self.assertEqual(len(weeks), 12)
        self.assertEqual(weeks[-1]["week"], date(2026, 11, 23))
        self.assertEqual(weeks[0]["week"], date(2026, 9, 7))

    def test_sessions_land_in_the_week_they_were_flown(self):
        self.session(datetime(2026, 11, 24, 12, 0, tzinfo=dt_timezone.utc), 60, damage=360_000)
        self.session(datetime(2026, 11, 17, 12, 0, tzinfo=dt_timezone.utc), 30, damage=90_000)
        self.session(datetime(2026, 1, 5, 12, 0, tzinfo=dt_timezone.utc), 30, damage=1)  # too old
        weeks = trends.weekly(self.user, now=NOW)
        this_week, last_week = weeks[-1], weeks[-2]
        self.assertEqual((this_week["sessions"], this_week["damage_out"]), (1, 360_000))
        self.assertEqual((last_week["sessions"], last_week["damage_out"]), (1, 90_000))
        self.assertEqual(sum(week["sessions"] for week in weeks), 2)
        self.assertEqual(this_week["hours"], 1.0)

    def test_dps_is_time_weighted_and_skips_short_sessions(self):
        monday = datetime(2026, 11, 23, 10, 0, tzinfo=dt_timezone.utc)
        self.session(monday, 60, damage=360_000)                       # 100 DPS for an hour
        self.session(monday + timedelta(hours=2), 30, damage=360_000)  # 200 DPS for 30 min
        self.session(monday + timedelta(hours=4), 5, damage=900_000)   # 3000 DPS, but only 5 min
        week = trends.weekly(self.user, now=NOW)[-1]
        self.assertAlmostEqual(week["dps"], 720_000 / 5400)  # 133.3, not the mean of 100 and 200
        self.assertEqual(week["sessions"], 3)                  # the short one still counts here
        self.assertAlmostEqual(week["hours"], 95 / 60)

    def test_isk_per_hour_only_counts_sessions_with_wallet_data(self):
        monday = datetime(2026, 11, 23, 10, 0, tzinfo=dt_timezone.utc)
        self.session(monday, 60, payout=30 * M)
        self.session(monday + timedelta(hours=2), 60, payout=None)  # no wallet data
        self.assertEqual(trends.weekly(self.user, now=NOW)[-1]["isk_per_hour"], 30 * M)
        self.assertIsNone(trends.weekly(self.user, now=NOW)[-2]["isk_per_hour"])

    def test_payouts_and_rats_come_from_the_wallet(self):
        PayoutDaily.objects.create(character=self.character, day=date(2026, 11, 24), entry_type="bounty_prizes", amount=12 * M)
        PayoutDaily.objects.create(character=self.character, day=date(2026, 11, 24), entry_type="ess_escrow_transfer", amount=3 * M)
        PayoutDaily.objects.create(character=self.character, day=date(2026, 11, 18), entry_type="bounty_prizes", amount=5 * M)
        NpcKillDaily.objects.create(character=self.character, day=date(2026, 11, 25), npc_type_id=23707, count=40)
        weeks = trends.weekly(self.user, now=NOW)
        self.assertEqual(weeks[-1]["payouts"], 15 * M)
        self.assertEqual(weeks[-2]["payouts"], 5 * M)
        self.assertEqual(weeks[-1]["rats"], 40)

    def test_other_pilots_are_never_included(self):
        other_user, other = _pilot("other", "Example Other", 1000000802)
        self.session(datetime(2026, 11, 24, 12, 0, tzinfo=dt_timezone.utc), 60, damage=999,
                     user=other_user, character=other)
        PayoutDaily.objects.create(character=other, day=date(2026, 11, 24), entry_type="bounty_prizes", amount=M)
        week = trends.weekly(self.user, now=NOW)[-1]
        self.assertEqual((week["sessions"], week["payouts"]), (0, 0))


class RecordTests(TrendsTestCase):
    def test_bests(self):
        start = timezone.now() - timedelta(days=3)
        big = self.session(start, 5, damage=900_000)                                    # most damage, too short for a rate
        steady = self.session(start + timedelta(hours=1), 60, damage=720_000, payout=20 * M)
        rich = self.session(start + timedelta(hours=3), 30, damage=180_000, payout=15 * M)
        PayoutDaily.objects.create(character=self.character, day=date(2026, 11, 20), entry_type="bounty_prizes", amount=40 * M)
        PayoutDaily.objects.create(character=self.character, day=date(2026, 11, 20), entry_type="ess_escrow_transfer", amount=5 * M)
        PayoutDaily.objects.create(character=self.character, day=date(2026, 11, 21), entry_type="bounty_prizes", amount=30 * M)
        NpcKillDaily.objects.create(character=self.character, day=date(2026, 11, 21), npc_type_id=1, count=70)
        records = trends.records(self.user)
        self.assertEqual(records["damage"], big)
        self.assertEqual(records["dps"], steady)        # 200 DPS; the 5-minute session can't hold it
        self.assertEqual(records["isk_per_hour"], rich)  # 30M/h beats 20M/h
        self.assertEqual(records["payout_day"]["total"], 45 * M)
        self.assertEqual(records["payout_day"]["day"], date(2026, 11, 20))
        self.assertEqual(records["rats_day"]["total"], 70)

    def test_nothing_yet(self):
        self.assertEqual(trends.records(self.user), {
            "damage": None, "dps": None, "isk_per_hour": None, "payout_day": None, "rats_day": None,
        })


class PageTests(TrendsTestCase):
    url = reverse("pilotstats:my_stats")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_shows_own_data_chart_and_tab(self):
        _token(self.user, self.character, WALLET_SCOPE)
        # Start of this EVE week, so the session is "this week" whenever the test runs.
        self.session(trends.week_start(timezone.now()) + timedelta(minutes=1), 60, damage=360_000, payout=25 * M)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, "My Stats")
        self.assertContains(response, 'id="pstats-trends-chart"')
        self.assertContains(response, "pilotstats/js/my_stats_chart.js")
        self.assertContains(response, "25.00M ISK")
        self.assertNotContains(response, "need a connected wallet")
        match = re.search(r'<script id="pstats-trends-data"[^>]*>(.*?)</script>', response.content.decode(), re.S)
        chart = json.loads(match.group(1))
        self.assertEqual(len(chart["labels"]), 12)
        self.assertEqual(chart["dps"][-1], 100)
        self.assertEqual(chart["isk_per_hour"][-1], 25_000_000)

    def test_empty_page_without_wallet(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="pstats-trends-chart"')
        self.assertNotContains(response, "chart.umd.min.js")
        self.assertContains(response, "need a connected wallet")
        self.assertContains(response, "Nothing in the last weeks yet.")
