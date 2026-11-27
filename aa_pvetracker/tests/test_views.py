"""
Django-backed tests. Redis has to be reachable (allianceauth.authentication
counts task events in Redis at startup). With that, the dev harness is enough:

    python manage.py test pvetracker

Covers ownership-gated upload attribution, main/alt leaderboard roll-up,
who can view versus who can upload, and the session / events pages.
"""

# Standard Library
import hashlib
from datetime import timedelta
from decimal import Decimal

# Django
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from ..models import CombatLogUpload, Payout, PilotSettings
from ..views import pvetracker_dashboard

SAMPLE_LOG = (
    "------------------------------------------------------------\n"
    "  Gamelog\n"
    "  Listener: Example Pilot\n"
    "  Session Started: 2026.09.14 22:27:51\n"
    "------------------------------------------------------------\n"
    "[ 2026.09.14 23:14:14 ] (combat) <color=0xff00ffff><b>1022</b> "
    "<color=0x77ffffff><font size=10>to</font> <b><color=0xffffffff>Bewildering Lathe</b>"
    "<font size=10><color=0x77ffffff> - Mega Pulse Laser II - Hits\n"
    "[ 2026.09.14 23:16:53 ] (combat) <color=0xffcc0000><b>762</b> "
    "<color=0x77ffffff><font size=10>from</font> <b><color=0xffffffff>Alienator Crusher</b>"
    "<font size=10><color=0x77ffffff> - Glances Off\n"
)


def _own_character(user, character):
    CharacterOwnership.objects.create(
        character=character, user=user, owner_hash=f"hash-{character.character_id}"
    )


def _upload_file(name="thrain.txt", content=SAMPLE_LOG):
    return SimpleUploadedFile(name, content.encode("utf-8"))


class UploadOwnershipTests(TestCase):
    def setUp(self):
        self.user = AuthUtils.create_user("pilot")
        AuthUtils.add_permissions_to_user_by_name(["pvetracker.basic_access"], self.user)
        self.client.force_login(self.user)

        self.character = EveCharacter.objects.create(
            character_id=1000000099, character_name="Example Pilot", corporation_id=2345,
        )

    def test_rejects_log_for_unowned_character(self):
        # No CharacterOwnership created — the log names a character not on this account.
        response = self.client.post(reverse("pvetracker:upload"), {"log_file": _upload_file()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CombatLogUpload.objects.count(), 0)

    def test_accepts_log_for_owned_character(self):
        _own_character(self.user, self.character)
        response = self.client.post(reverse("pvetracker:upload"), {"log_file": _upload_file()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CombatLogUpload.objects.count(), 1)

        upload = CombatLogUpload.objects.get()
        self.assertEqual(upload.character, self.character)
        self.assertEqual(upload.uploaded_by, self.user)
        self.assertEqual(upload.damage_out, 1022)
        self.assertEqual(upload.damage_in, 762)
        self.assertEqual(upload.file_hash, hashlib.sha256(SAMPLE_LOG.encode("utf-8")).hexdigest())
        self.assertEqual(len(upload.feed), 2)
        self.assertEqual(upload.feed[0]["kind"], "damage_in")  # most recent first

    def test_rejects_duplicate_upload(self):
        _own_character(self.user, self.character)
        self.client.post(reverse("pvetracker:upload"), {"log_file": _upload_file()})
        self.client.post(reverse("pvetracker:upload"), {"log_file": _upload_file(name="thrain2.txt")})
        self.assertEqual(CombatLogUpload.objects.count(), 1)


class LeaderboardRollupTests(TestCase):
    """Two characters owned by the same user, one designated main, should
    combine into a single leaderboard row — same mechanism FAT uses."""

    def setUp(self):
        self.user = AuthUtils.create_user("pilot")
        AuthUtils.add_permissions_to_user_by_name(["pvetracker.basic_access"], self.user)
        self.client.force_login(self.user)

        self.main = AuthUtils.add_main_character_2(
            self.user, "Main Character", 1000000001, corp_id=2345,
        )
        self.alt = EveCharacter.objects.create(
            character_id=1000000002, character_name="Alt Character", corporation_id=2345,
        )
        _own_character(self.user, self.main)
        _own_character(self.user, self.alt)

        for character, damage in [(self.main, 1000), (self.alt, 500)]:
            CombatLogUpload.objects.create(
                character=character,
                uploaded_by=self.user,
                source_filename=f"{character.character_name}.txt",
                file_hash=f"hash-{character.character_id}",
                damage_out=damage,
            )

    def test_alt_damage_rolls_up_to_main(self):
        response = self.client.get(reverse("pvetracker:leaderboard"), {"range": "all"})
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Main Character")
        self.assertEqual(rows[0]["damage_out"], 1500)
        self.assertEqual(len(rows[0]["by_character"]), 2)

    def test_seven_day_range_skips_older_uploads(self):
        old = CombatLogUpload.objects.create(
            character=self.main,
            uploaded_by=self.user,
            source_filename="old.txt",
            file_hash="hash-old",
            damage_out=9000,
        )
        CombatLogUpload.objects.filter(pk=old.pk).update(
            uploaded_at=timezone.now() - timedelta(days=10)
        )

        week = self.client.get(reverse("pvetracker:leaderboard"), {"range": "7d"})
        self.assertEqual(week.context["rows"][0]["damage_out"], 1500)
        everything = self.client.get(reverse("pvetracker:leaderboard"), {"range": "all"})
        self.assertEqual(everything.context["rows"][0]["damage_out"], 10500)

    def test_unknown_sort_falls_back_to_damage_dealt(self):
        response = self.client.get(
            reverse("pvetracker:leaderboard"),
            {"range": "all", "sort": "not-a-column"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort_key"], "damage_out")


class AccessAndSessionTests(TestCase):
    def setUp(self):
        self.owner = AuthUtils.create_user("owner")
        AuthUtils.add_permissions_to_user_by_name(["pvetracker.basic_access"], self.owner)
        self.viewer = AuthUtils.create_user("viewer")
        self.character = AuthUtils.add_main_character_2(
            self.owner, "Main Character", 1000000100, corp_id=2345,
        )
        _own_character(self.owner, self.character)
        started = timezone.now() - timedelta(minutes=12)
        self.upload = CombatLogUpload.objects.create(
            character=self.character,
            uploaded_by=self.owner,
            source_filename="session.txt",
            file_hash="hash-access",
            damage_out=2500,
            session_started=started,
            session_ended=started + timedelta(minutes=12),
            feed=[{
                "ts": "00:01:00",
                "kind": "damage_out",
                "amount": 2500,
                "party": "Example Target",
                "quality": "Hits",
            }],
        )

    def test_anonymous_leaderboard_redirects_to_login(self):
        response = self.client.get(reverse("pvetracker:leaderboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_viewer_can_open_leaderboard_session_and_events(self):
        self.client.force_login(self.viewer)
        pages = (
            ("pvetracker:leaderboard", {}),
            ("pvetracker:session", {"pk": self.upload.pk}),
            ("pvetracker:events", {}),
        )
        for name, kwargs in pages:
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 200, name)

    def test_viewer_cannot_upload_or_open_my_uploads(self):
        self.client.force_login(self.viewer)
        for name in ("pvetracker:upload", "pvetracker:my_uploads"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403, name)

    def test_session_page_reuses_the_upload_dashboard(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("pvetracker:session", kwargs={"pk": self.upload.pk}))
        self.assertContains(response, "Main Character")
        self.assertContains(response, "2500")
        self.assertContains(response, "Example Target")
        self.assertContains(response, "00:12:00")
        self.assertNotContains(response, reverse("pvetracker:delete_upload", kwargs={"pk": self.upload.pk}))

    def test_my_uploads_links_to_the_session(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("pvetracker:my_uploads"))
        session_url = reverse("pvetracker:session", kwargs={"pk": self.upload.pk})
        self.assertContains(response, session_url)
        self.assertContains(response, "00:12:00")
        self.assertContains(response, "30-day damage rank #1")

    def test_nav_follows_the_upload_permission(self):
        self.client.force_login(self.viewer)
        hidden = self.client.get(reverse("pvetracker:leaderboard"))
        self.assertContains(hidden, reverse("pvetracker:events"))
        self.assertNotContains(hidden, reverse("pvetracker:upload"))

        self.client.force_login(self.owner)
        shown = self.client.get(reverse("pvetracker:leaderboard"))
        self.assertContains(shown, reverse("pvetracker:upload"))
        self.assertContains(shown, "My Uploads")

    def test_owner_can_delete_and_viewer_cannot(self):
        self.client.force_login(self.viewer)
        denied = self.client.post(
            reverse("pvetracker:delete_upload", kwargs={"pk": self.upload.pk})
        )
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(CombatLogUpload.objects.filter(pk=self.upload.pk).exists())

        self.client.force_login(self.owner)
        deleted = self.client.post(
            reverse("pvetracker:delete_upload", kwargs={"pk": self.upload.pk})
        )
        self.assertRedirects(deleted, reverse("pvetracker:my_uploads"))
        self.assertFalse(CombatLogUpload.objects.filter(pk=self.upload.pk).exists())

    def test_hide_from_live_feed_drops_events_but_not_the_leaderboard(self):
        self.client.force_login(self.viewer)
        visible = self.client.get(reverse("pvetracker:events"))
        self.assertContains(visible, "Example Target")

        self.client.force_login(self.owner)
        self.client.post(reverse("pvetracker:events"), {"hide_from_live_feed": "on"})
        self.assertTrue(PilotSettings.objects.get(user=self.owner).hide_from_live_feed)

        self.client.force_login(self.viewer)
        hidden = self.client.get(reverse("pvetracker:events"))
        self.assertNotContains(hidden, "Example Target")
        board = self.client.get(reverse("pvetracker:leaderboard"), {"range": "all"})
        self.assertEqual(board.context["rows"][0]["damage_out"], 2500)

        self.client.force_login(self.owner)
        self.client.post(reverse("pvetracker:events"), {})
        self.assertFalse(PilotSettings.objects.get(user=self.owner).hide_from_live_feed)
        self.client.force_login(self.viewer)
        restored = self.client.get(reverse("pvetracker:events"))
        self.assertContains(restored, "Example Target")

    def test_dashboard_widget_shows_last_session_and_rank(self):
        request = RequestFactory().get("/dashboard/")
        request.user = self.owner
        html = pvetracker_dashboard(request)
        self.assertIn("Main Character", html)
        self.assertIn("00:12:00", html)
        self.assertIn("7-day damage rank #1", html)
        self.assertIn(reverse("pvetracker:upload"), html)

        request.user = self.viewer
        empty = pvetracker_dashboard(request)
        self.assertIn("No combat logs in the last 7 days.", empty)
        self.assertNotIn(reverse("pvetracker:upload"), empty)

        request.user = AnonymousUser()
        self.assertEqual(pvetracker_dashboard(request), "")


class PayoutStubTests(TestCase):
    def test_payout_stores_only_the_allowed_fields(self):
        names = {field.name for field in Payout._meta.fields}
        self.assertEqual(
            names,
            {"id", "character", "occurred_at", "entry_type", "amount", "ref_id"},
        )

    def test_ship_type_id_starts_empty(self):
        self.assertTrue(CombatLogUpload._meta.get_field("ship_type_id").null)

    def test_ref_id_is_unique(self):
        character = EveCharacter.objects.create(
            character_id=1000000200, character_name="Payout Pilot", corporation_id=2345,
        )
        kwargs = {
            "character": character,
            "occurred_at": timezone.now(),
            "entry_type": Payout.EntryType.BOUNTY,
            "amount": Decimal("100.50"),
            "ref_id": 424242,
        }
        Payout.objects.create(**kwargs)
        with self.assertRaises(IntegrityError):
            Payout.objects.create(**kwargs)

