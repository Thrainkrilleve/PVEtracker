"""
Session timeline (the damage-over-time chart): bucketing in the parser,
storage on upload, the re-upload backfill, and the session page.
"""

# Standard Library
import json
import re
from datetime import datetime, timedelta
from unittest import mock

# Django
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.tests.auth_utils import AuthUtils

from .. import tasks
from ..models import CombatLogUpload
from ..parsing import TIMELINE_MAX_POINTS, CombatEvent, build_timeline, summarize
from .test_views import SAMPLE_LOG

T0 = datetime(2026, 11, 27, 20, 15, 42)


def _event(seconds, kind, amount):
    return CombatEvent(T0 + timedelta(seconds=seconds), kind, amount)


class BuildTimelineTests(TestCase):
    def test_minute_buckets_from_the_first_events_minute(self):
        events = [
            _event(0, "damage_out", 100),
            _event(10, "damage_out", 50),       # same minute (20:15)
            _event(30, "damage_in", 40),        # 20:16:12, next minute
            _event(150, "remote_rep_in", 300),  # 20:18:12
            _event(150, "remote_rep_out", 70),
            _event(150, "miss_out", 0),         # not charted
        ]
        timeline = summarize(events)["timeline"]
        self.assertEqual(timeline["start"], "2026-11-27T20:15:00Z")
        self.assertEqual(timeline["bucket"], 60)
        self.assertEqual(timeline["damage_out"], [150, 0, 0, 0])
        self.assertEqual(timeline["damage_in"], [0, 40, 0, 0])
        self.assertEqual(timeline["remote_rep_in"], [0, 0, 0, 300])
        self.assertEqual(timeline["remote_rep_out"], [0, 0, 0, 70])

    def test_totals_match_the_session_totals(self):
        events = [_event(s * 7, "damage_out", 10 + s) for s in range(500)]
        events += [_event(s * 11, "damage_in", 3) for s in range(200)]
        summary = summarize(events)
        self.assertEqual(sum(summary["timeline"]["damage_out"]), summary["damage_out"])
        self.assertEqual(sum(summary["timeline"]["damage_in"]), summary["damage_in"])

    def test_long_sessions_use_bigger_buckets(self):
        # Ten hours of fighting: 601 minutes is too many points, so 2-minute steps.
        events = [_event(0, "damage_out", 1), _event(10 * 3600, "damage_out", 1)]
        timeline = build_timeline(
            [(e.timestamp, e.kind, e.amount) for e in events], events[0].timestamp, events[-1].timestamp,
        )
        self.assertEqual(timeline["bucket"], 120)
        self.assertLessEqual(len(timeline["damage_out"]), TIMELINE_MAX_POINTS)
        self.assertEqual(sum(timeline["damage_out"]), 2)

    def test_absurdly_long_logs_stay_capped(self):
        samples = [(T0, "damage_out", 1), (T0 + timedelta(days=30), "damage_out", 1)]
        timeline = build_timeline(samples, T0, T0 + timedelta(days=30))
        self.assertEqual(len(timeline["damage_out"]), TIMELINE_MAX_POINTS)
        self.assertEqual(sum(timeline["damage_out"]), 2)

    def test_nothing_to_chart_is_empty(self):
        self.assertEqual(summarize([_event(0, "miss_out", 0)])["timeline"], {})
        self.assertEqual(summarize([])["timeline"], {})


class UploadTimelineTests(TestCase):
    def setUp(self):
        self.user = AuthUtils.create_user("pilot")
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], self.user)
        self.character = AuthUtils.add_main_character_2(self.user, "Example Pilot", 1000000099, corp_id=2345)
        CharacterOwnership.objects.create(character=self.character, user=self.user, owner_hash="hash-99")
        self.client.force_login(self.user)
        patcher = mock.patch.object(tasks.check_session_records, "delay")
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, content=SAMPLE_LOG, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.post(
            reverse("pilotstats:upload"),
            {"log_file": SimpleUploadedFile("log.txt", content.encode("utf-8"))},
            HTTP_ACCEPT="application/json",
        )

    def test_upload_stores_the_timeline(self):
        self.upload()
        timeline = CombatLogUpload.objects.get().timeline
        self.assertEqual(timeline["bucket"], 60)
        self.assertEqual(sum(timeline["damage_out"]), 1022)
        self.assertEqual(sum(timeline["damage_in"]), 762)

    def test_reupload_adds_a_chart_to_an_old_session(self):
        self.upload()
        CombatLogUpload.objects.update(timeline={})  # as if stored before charts existed
        response = self.upload()
        self.assertEqual(response.json()["result"], "updated")
        self.assertEqual(sum(CombatLogUpload.objects.get().timeline["damage_out"]), 1022)
        self.assertEqual(CombatLogUpload.objects.count(), 1)

    def test_reupload_with_a_chart_is_still_a_duplicate(self):
        self.upload()
        self.assertEqual(self.upload().json()["result"], "duplicate")

    def test_someone_elses_session_is_not_touched(self):
        self.upload()
        CombatLogUpload.objects.update(timeline={})
        other = AuthUtils.create_user("other")
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], other)
        AuthUtils.add_main_character_2(other, "Example Other", 1000000098, corp_id=2345)
        self.assertEqual(self.upload(user=other).json()["result"], "duplicate")
        self.assertEqual(CombatLogUpload.objects.get().timeline, {})


class SessionPageChartTests(TestCase):
    def setUp(self):
        self.user = AuthUtils.create_user("viewer")
        self.character = AuthUtils.add_main_character_2(self.user, "Example Viewer", 1000000097, corp_id=2345)
        self.client.force_login(self.user)

    def session(self, timeline):
        return CombatLogUpload.objects.create(
            character=self.character, uploaded_by=self.user, source_filename="s.txt",
            file_hash=f"h{CombatLogUpload.objects.count()}", damage_out=1000,
            timeline=timeline,
        )

    def test_chart_data_and_script_are_on_the_page(self):
        timeline = {"start": "2026-11-27T20:15:00Z", "bucket": 120, "damage_out": [1200, 600],
                    "damage_in": [0, 0], "remote_rep_in": [0, 0], "remote_rep_out": [0, 0]}
        response = self.client.get(reverse("pilotstats:session", args=[self.session(timeline).pk]))
        self.assertContains(response, 'id="pstats-timeline-chart"')
        self.assertContains(response, "pilotstats/js/session_chart.js")
        self.assertContains(response, "chart.umd.min.js")
        self.assertContains(response, "averaged over 2-minute steps")
        match = re.search(r'<script id="pstats-timeline-data"[^>]*>(.*?)</script>', response.content.decode(), re.S)
        self.assertEqual(json.loads(match.group(1)), timeline)

    def test_old_session_explains_how_to_get_a_chart(self):
        response = self.client.get(reverse("pilotstats:session", args=[self.session({}).pk]))
        self.assertNotContains(response, 'id="pstats-timeline-chart"')
        self.assertNotContains(response, "chart.umd.min.js")
        self.assertContains(response, "Upload the same log file again")
