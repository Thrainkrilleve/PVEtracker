"""
Discord webhook feed: formatting within Discord's limits, dedupe keys, the
hourly cap, thresholds, records, the daily post's timing, and sending.
No request ever leaves the test: dispatch and requests.post are mocked.
"""

# Standard Library
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

# Django
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.tests.auth_utils import AuthUtils

from .. import discord, payouts, tasks
from ..models import (
    CombatLogUpload,
    DiscordPost,
    DiscordSettings,
    Killmail,
    KillmailParticipant,
)

# Fictional, well-formed webhook (never contacted: dispatch is mocked).
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/example-token_ABC"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=dt_timezone.utc)


def _pilot(username, name, character_id):
    user = AuthUtils.create_user(username)
    main = AuthUtils.add_main_character_2(user, name, character_id, corp_id=2345)
    CharacterOwnership.objects.create(character=main, user=user, owner_hash=f"hash-{character_id}")
    return user, main


class DiscordTestCase(TestCase):
    def setUp(self):
        self.config = DiscordSettings.get()
        self.config.enabled = True
        self.config.webhook_url = WEBHOOK
        self.config.save()
        patcher = mock.patch.object(tasks.send_discord_post, "delay")
        self.sent = patcher.start()
        self.addCleanup(patcher.stop)

    def payloads(self):
        return [c.args[1] for c in self.sent.call_args_list]


class EmbedLimitTests(TestCase):
    def test_parts_are_clipped(self):
        data = discord.embed(
            "t" * 400, "d" * 5000,
            fields=[(f"n{i}", "v" * 2000, False) for i in range(40)],
        )
        self.assertEqual(len(data["title"]), discord.TITLE_LIMIT)
        self.assertEqual(len(data["description"]), discord.DESCRIPTION_LIMIT)
        self.assertEqual(len(data["fields"]), discord.FIELD_LIMIT)
        self.assertTrue(all(len(f["value"]) == discord.FIELD_VALUE_LIMIT for f in data["fields"]))

    def test_messages_stay_under_6000_chars_and_10_embeds(self):
        embeds = [discord.embed(f"Embed {i}", "x" * 2500) for i in range(12)]
        messages = discord.pack_messages(embeds)
        self.assertGreater(len(messages), 1)
        for message in messages:
            self.assertLessEqual(len(message), discord.MESSAGE_EMBED_COUNT)
            self.assertLessEqual(sum(discord.embed_size(e) for e in message), discord.MESSAGE_EMBED_TOTAL)
        self.assertEqual(sum(len(m) for m in messages), 12)

    def test_payload_never_pings(self):
        self.assertEqual(discord.payload([])["allowed_mentions"], {"parse": []})


class SettingsValidationTests(TestCase):
    def test_webhook_url_must_be_discord(self):
        config = DiscordSettings(webhook_url="https://example.com/hook")
        with self.assertRaises(ValidationError):
            config.full_clean()
        DiscordSettings(webhook_url=WEBHOOK, timezone="UTC").full_clean()

    def test_timezone_must_exist(self):
        with self.assertRaises(ValidationError):
            DiscordSettings(webhook_url=WEBHOOK, timezone="Mars/Olympus").full_clean()

    def test_inactive_feed_posts_nothing(self):
        with mock.patch.object(tasks.send_discord_post, "delay") as sent:
            self.assertIsNone(discord.test_message())
        sent.assert_not_called()


class QueueTests(DiscordTestCase):
    def test_each_key_posts_once(self):
        first = discord.queue("killmail:1", DiscordPost.Kind.KILLMAIL, [discord.embed("a")])
        second = discord.queue("killmail:1", DiscordPost.Kind.KILLMAIL, [discord.embed("a")])
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(self.sent.call_count, 1)

    def test_hourly_cap_skips_events_but_not_the_daily_post(self):
        self.config.max_posts_per_hour = 2
        self.config.save()
        for i in range(4):
            discord.queue(f"killmail:{i}", DiscordPost.Kind.KILLMAIL, [discord.embed("a")], config=self.config)
        discord.queue("daily:2026-11-27", DiscordPost.Kind.DAILY, [discord.embed("d")], config=self.config)
        statuses = dict(DiscordPost.objects.values_list("key", "status"))
        self.assertEqual(
            sorted(k for k, s in statuses.items() if s == DiscordPost.Status.SKIPPED),
            ["killmail:2", "killmail:3"],
        )
        self.assertEqual(statuses["daily:2026-11-27"], DiscordPost.Status.QUEUED)
        self.assertEqual(self.sent.call_count, 3)


class EventTests(DiscordTestCase):
    def setUp(self):
        super().setUp()
        self.user, self.main = _pilot("pilot", "Example Main", 1000000901)

    def test_payout_threshold(self):
        now = NOW
        payouts.store_journal(self.main, [
            SimpleNamespace(id=1, ref_type="ess_escrow_transfer", amount=350_000_000.0, date=now, reason=None),
            SimpleNamespace(id=2, ref_type="bounty_prizes", amount=40_000_000.0, date=now, reason="1: 1"),
        ])
        self.assertEqual(discord.check_payouts(self.main, config=self.config, now=now), 1)
        self.assertEqual(discord.check_payouts(self.main, config=self.config, now=now), 0)  # once
        text = str(self.payloads())
        self.assertIn("350.00M ISK", text)
        self.assertIn("Example Main", text)

    def test_kill_and_loss_threshold(self):
        small = Killmail.objects.create(killmail_id=1, killmail_hash="h", killmail_time=NOW,
                                        total_value=Decimal("20000000"))
        KillmailParticipant.objects.create(killmail=small, character=self.main, is_victim=True)
        big = Killmail.objects.create(killmail_id=2, killmail_hash="h", killmail_time=NOW,
                                      total_value=Decimal("1200000000"), victim_ship_type_id=17738)
        KillmailParticipant.objects.create(killmail=big, character=self.main, is_victim=True)
        self.assertFalse(discord.check_killmail(small, config=self.config))
        self.assertTrue(discord.check_killmail(big, config=self.config))
        embed = self.payloads()[0]["embeds"][0]
        self.assertTrue(embed["title"].startswith("Loss:"))
        self.assertEqual(embed["url"], "https://zkillboard.com/kill/2/")


class RecordTests(DiscordTestCase):
    def setUp(self):
        super().setUp()
        self.user, self.main = _pilot("pilot", "Example Main", 1000000911)
        self.other_user, self.other = _pilot("other", "Example Other", 1000000912)
        self.n = 0

    def _session(self, who, damage, minutes=30, user=None):
        self.n += 1
        start = NOW - timedelta(hours=self.n)
        return CombatLogUpload.objects.create(
            character=who, uploaded_by=user or self.user, source_filename=f"{self.n}.txt",
            file_hash=f"h{self.n}", damage_out=damage,
            session_started=start, session_ended=start + timedelta(minutes=minutes),
        )

    def test_fleet_record_for_damage_and_dps(self):
        self._session(self.other, 100_000, user=self.other_user)
        best = self._session(self.main, 250_000)
        self.assertEqual(sorted(discord.check_session_records(best, config=self.config)), ["damage", "dps"])
        self.assertEqual(discord.check_session_records(best, config=self.config), [])  # once per session
        self.assertIn("Fleet record", str(self.payloads()))

    def test_short_sessions_never_set_records(self):
        self._session(self.other, 1_000, user=self.other_user)
        burst = self._session(self.main, 900_000, minutes=2)
        self.assertEqual(discord.check_session_records(burst, config=self.config), [])

    def test_personal_record_needs_history(self):
        self._session(self.other, 10_000_000, user=self.other_user)  # fleet best stays out of reach
        first = self._session(self.main, 50_000)
        self.assertEqual(discord.check_session_records(first, config=self.config), [])
        for damage in (10_000, 20_000, 30_000, 40_000, 45_000):
            self._session(self.main, damage)
        best = self._session(self.main, 60_000)
        self.assertIn("damage", discord.check_session_records(best, config=self.config))
        self.assertIn("Personal record", str(self.payloads()))

    def test_records_can_be_turned_off(self):
        self.config.post_records = False
        self.config.save()
        self._session(self.other, 1, user=self.other_user)
        self.assertEqual(discord.check_session_records(self._session(self.main, 999_999), config=self.config), [])


class DailyPostTests(DiscordTestCase):
    def setUp(self):
        super().setUp()
        self.user, self.main = _pilot("pilot", "Example Main", 1000000921)
        CombatLogUpload.objects.create(
            character=self.main, uploaded_by=self.user, source_filename="d.txt", file_hash="hd",
            damage_out=123_456, session_started=NOW - timedelta(hours=3),
            session_ended=NOW - timedelta(hours=2),
        )
        payouts.store_journal(self.main, [SimpleNamespace(
            id=5, ref_type="bounty_prizes", amount=75_000_000.0, date=NOW - timedelta(hours=1), reason=None,
        )])

    def test_waits_for_the_configured_time_then_posts_once(self):
        self.config.daily_post_time = time(13, 0)
        self.config.save()
        self.assertIsNone(discord.post_daily(self.config, now=NOW))  # 12:00 < 13:00
        later = NOW + timedelta(hours=2)
        self.assertIsNotNone(discord.post_daily(self.config, now=later))
        self.assertIsNone(discord.post_daily(self.config, now=later + timedelta(minutes=5)))
        text = str(self.payloads())
        self.assertIn("Top 10 — damage dealt (24h)", text)
        self.assertIn("Example Main — 123,456", text)
        self.assertIn("75.00M ISK", text)

    def test_time_zone_decides_the_day(self):
        self.config.timezone = "America/Denver"
        self.config.daily_post_time = time(8, 0)
        self.config.save()
        # 12:00 UTC is 05:00 in Denver: not yet.
        self.assertIsNone(discord.daily_due(self.config, now=NOW))
        self.assertEqual(
            discord.daily_due(self.config, now=NOW + timedelta(hours=4)).isoformat(), "2026-11-27",
        )


class SendTaskTests(DiscordTestCase):
    def setUp(self):
        super().setUp()
        self.post = DiscordPost.objects.create(key="test:1", kind=DiscordPost.Kind.TEST)

    def _send(self, response):
        with mock.patch.object(tasks.requests, "post", return_value=response) as post:
            tasks.send_discord_post(self.post.pk, {"embeds": []})
        return post

    def test_success_marks_sent(self):
        post = self._send(SimpleNamespace(status_code=204, headers={}))
        self.assertEqual(post.call_args.args[0], WEBHOOK)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, DiscordPost.Status.SENT)

    def test_429_retries_after_discords_wait(self):
        response = SimpleNamespace(status_code=429, headers={}, json=lambda: {"retry_after": 2.5})
        with mock.patch.object(tasks.send_discord_post, "retry", side_effect=RuntimeError("retry")) as retry:
            with self.assertRaisesRegex(RuntimeError, "retry"):
                self._send(response)
        self.assertEqual(retry.call_args.kwargs["countdown"], 3)

    def test_bad_webhook_fails_without_retry(self):
        self._send(SimpleNamespace(status_code=404, headers={}))
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, DiscordPost.Status.FAILED)


class SettingsAdminTests(TestCase):
    """The settings page opens directly and has a "Save and send a test message" button."""

    def setUp(self):
        self.admin = AuthUtils.create_user("admin")
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        self.client.force_login(self.admin)
        patcher = mock.patch.object(tasks.send_discord_post, "delay")
        self.sent = patcher.start()
        self.addCleanup(patcher.stop)

    def _form_data(self, url, **changes):
        form = self.client.get(url).context["adminform"].form
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name, field.initial)
            if isinstance(value, bool):
                if value:
                    data[name] = "on"
            elif value is not None:
                data[name] = value
        data.update(changes)
        return {k: v for k, v in data.items() if v is not False}

    def test_list_page_opens_the_add_form_then_the_settings(self):
        response = self.client.get(reverse("admin:pilotstats_discordsettings_changelist"))
        self.assertRedirects(response, reverse("admin:pilotstats_discordsettings_add"))
        config = DiscordSettings.get()
        response = self.client.get(reverse("admin:pilotstats_discordsettings_changelist"))
        self.assertRedirects(response, reverse("admin:pilotstats_discordsettings_change", args=[config.pk]))

    def test_button_is_on_the_form(self):
        config = DiscordSettings.get()
        response = self.client.get(reverse("admin:pilotstats_discordsettings_change", args=[config.pk]))
        html = response.content.decode()
        self.assertIn('name="_send_test"', html)
        # Save stays the form's first submit button, so Enter in a field is a plain save.
        self.assertLess(html.index('name="_save"'), html.index('name="_send_test"'))

    def test_button_saves_then_sends_a_test(self):
        config = DiscordSettings.get()
        url = reverse("admin:pilotstats_discordsettings_change", args=[config.pk])
        data = self._form_data(url, enabled="on", webhook_url=WEBHOOK, _send_test="1")
        response = self.client.post(url, data, follow=True)
        self.assertRedirects(response, url)
        config.refresh_from_db()
        self.assertTrue(config.is_active)
        self.assertEqual(self.sent.call_count, 1)
        self.assertEqual(DiscordPost.objects.get().kind, DiscordPost.Kind.TEST)
        self.assertContains(response, "Test message queued")

    def test_button_on_first_save_creates_and_sends(self):
        url = reverse("admin:pilotstats_discordsettings_add")
        data = self._form_data(url, enabled="on", webhook_url=WEBHOOK, _send_test="1")
        response = self.client.post(url, data)
        config = DiscordSettings.objects.get()
        self.assertRedirects(response, reverse("admin:pilotstats_discordsettings_change", args=[config.pk]))
        self.assertEqual(self.sent.call_count, 1)

    def test_inactive_feed_saves_but_sends_nothing(self):
        config = DiscordSettings.get()
        url = reverse("admin:pilotstats_discordsettings_change", args=[config.pk])
        data = self._form_data(url, webhook_url=WEBHOOK, _send_test="1")
        data.pop("enabled", None)
        response = self.client.post(url, data, follow=True)
        config.refresh_from_db()
        self.assertEqual(config.webhook_url, WEBHOOK)
        self.sent.assert_not_called()
        self.assertContains(response, "no test was sent")

    def test_plain_save_sends_nothing(self):
        config = DiscordSettings.get()
        url = reverse("admin:pilotstats_discordsettings_change", args=[config.pk])
        data = self._form_data(url, enabled="on", webhook_url=WEBHOOK, _save="Save")
        self.client.post(url, data)
        self.sent.assert_not_called()
