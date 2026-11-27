"""
The JSON side of the upload endpoint, used by the Gamelog folder scanner and
multi-file drops, plus the characters list the scanner filters with.

The HTML form path is covered by test_views.py and is unchanged.
"""

# Standard Library
from unittest import mock

# Django
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from .. import views
from ..models import CombatLogUpload
from .test_views import GROWN_LOG, SAMPLE_LOG

JSON = {"HTTP_ACCEPT": "application/json"}


def _file(content=SAMPLE_LOG, name="20260914_222751.txt"):
    return SimpleUploadedFile(name, content.encode("utf-8"))


class ScannerUploadTests(TestCase):
    def setUp(self):
        # A private cache per test: the throttle counter must not leak between
        # tests, and must never touch a real Auth's Redis when run in-container.
        cache_patch = mock.patch.object(views, "cache", LocMemCache(f"pilotstats-tests-{self.id()}", {}))
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

        self.user = AuthUtils.create_user("scanner")
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], self.user)
        self.character = AuthUtils.add_main_character_2(
            self.user, "Example Pilot", 1000000099, corp_id=2345,
        )
        CharacterOwnership.objects.create(
            character=self.character, user=self.user, owner_hash="hash-scanner",
        )
        self.client.force_login(self.user)
        self.url = reverse("pilotstats:upload")

    def _post(self, content=SAMPLE_LOG, **kwargs):
        return self.client.post(self.url, {"log_file": _file(content, **kwargs)}, **JSON)

    def test_created_then_updated_then_no_new_lines(self):
        first = self._post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first["Content-Type"], "application/json")
        data = first.json()
        self.assertEqual(data["result"], "created")
        session = CombatLogUpload.objects.get()
        self.assertEqual(data["session_id"], session.pk)
        self.assertEqual(data["session_url"], reverse("pilotstats:session", kwargs={"pk": session.pk}))
        self.assertEqual(data["character"], "Example Pilot")

        grown = self._post(GROWN_LOG).json()
        self.assertEqual(grown["result"], "updated")
        self.assertEqual(grown["new_lines"], 1)
        self.assertEqual(grown["session_id"], session.pk)

        # A different copy (not byte-identical) with no new combat lines.
        shorter = self._post(SAMPLE_LOG + "[ 2026.09.14 23:30:00 ] (notify) Example notice\n").json()
        self.assertEqual(shorter["result"], "no_new_lines")
        self.assertEqual(CombatLogUpload.objects.count(), 1)

    def test_duplicate(self):
        self._post()
        self.assertEqual(self._post().json()["result"], "duplicate")

    def test_not_gamelog(self):
        data = self._post("just some text\nwith no header\n").json()
        self.assertEqual(data["result"], "not_gamelog")

    def test_not_owned(self):
        EveCharacter.objects.create(
            character_id=1000000500, character_name="Someone Else", corporation_id=2345,
        )
        data = self._post(SAMPLE_LOG.replace("Example Pilot", "Someone Else")).json()
        self.assertEqual(data["result"], "not_owned")
        self.assertEqual(CombatLogUpload.objects.count(), 0)

    def test_no_combat(self):
        header_only = "\n".join(SAMPLE_LOG.splitlines()[:5]) + "\n"
        self.assertEqual(self._post(header_only).json()["result"], "no_combat")

    def test_too_large(self):
        with mock.patch.object(views, "MAX_UPLOAD_BYTES", 10):
            self.assertEqual(self._post().json()["result"], "too_large")

    def test_missing_file_is_a_400(self):
        response = self.client.post(self.url, {}, **JSON)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["result"], "invalid")

    def test_throttle_returns_429_with_retry_after(self):
        with mock.patch.object(views, "SCANNER_UPLOADS_PER_MINUTE", 2):
            codes = [self._post(name=f"log{i}.txt").status_code for i in range(3)]
            response = self._post(name="log4.txt")
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["result"], "throttled")
        self.assertTrue(1 <= int(response["Retry-After"]) <= 60)

    def test_html_form_posts_are_not_throttled(self):
        with mock.patch.object(views, "SCANNER_UPLOADS_PER_MINUTE", 1):
            for i in range(3):
                response = self.client.post(self.url, {"log_file": _file(name=f"form{i}.txt")})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"].split(";")[0], "text/html")

    def test_html_form_still_shows_the_dashboard(self):
        response = self.client.post(self.url, {"log_file": _file()})
        self.assertContains(response, "Uploaded and parsed")
        self.assertContains(response, "Bewildering Lathe")  # session dashboard rendered
        self.assertContains(response, 'id="pilotstats-scanner"')


class ScannerCharactersTests(TestCase):
    def setUp(self):
        self.user = AuthUtils.create_user("scanner")
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], self.user)
        main = AuthUtils.add_main_character_2(self.user, "Example Pilot", 1000000099, corp_id=2345)
        alt = EveCharacter.objects.create(
            character_id=1000000098, character_name="Example Alt", corporation_id=2345,
        )
        for character in (main, alt):
            CharacterOwnership.objects.create(
                character=character, user=self.user, owner_hash=f"hash-{character.character_id}",
            )
        other = AuthUtils.create_user("other")
        other_main = AuthUtils.add_main_character_2(other, "Other Main", 1000000097, corp_id=2345)
        CharacterOwnership.objects.create(character=other_main, user=other, owner_hash="hash-other")
        self.url = reverse("pilotstats:upload_characters")

    def test_lists_only_the_users_own_characters(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.json(), {"characters": ["Example Alt", "Example Pilot"]})

    def test_needs_the_upload_permission(self):
        viewer = AuthUtils.create_user("viewer")
        AuthUtils.add_main_character_2(viewer, "Viewer Main", 1000000096, corp_id=2345)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("pilotstats:upload"), {}, **JSON).status_code, 403
        )
