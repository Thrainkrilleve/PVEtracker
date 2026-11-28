"""
AA Desktop routes (desktop.py), through the real aa-desktop-bridge.

These run only where aa-desktop-bridge is installed: settings_dev turns it on
when it can import it. The bridge is not on PyPI yet, so this repository's CI
skips them, and aa-desktop's CI runs them.
"""

# Standard Library
import unittest
from datetime import timedelta
from unittest import mock

# Django
from django.apps import apps
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from .. import views
from ..models import CombatLogUpload
from .test_views import GROWN_LOG, SAMPLE_LOG

BRIDGE = apps.is_installed("aa_desktop_bridge")
BASE = "/api/desktop/v1/modules/pilotstats"
SCOPE = "desktop.pilotstats.upload"


def _file(content=SAMPLE_LOG, name="20260914_222751.txt"):
    return SimpleUploadedFile(name, content.encode("utf-8"))


@unittest.skipUnless(BRIDGE, "aa-desktop-bridge is not installed")
class DesktopTestCase(TestCase):
    def setUp(self):
        # Third Party
        from oauth2_provider.models import get_application_model

        cache_patch = mock.patch.object(views, "cache", LocMemCache(f"pilotstats-desktop-{self.id()}", {}))
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

        self.user = AuthUtils.create_user("desktop_pilot")
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], self.user)
        self.character = AuthUtils.add_main_character_2(
            self.user, "Example Pilot", 1000000099, corp_id=2345,
        )
        CharacterOwnership.objects.create(
            character=self.character, user=self.user, owner_hash="hash-desktop",
        )
        model = get_application_model()
        self.application = model.objects.create(
            name="AA Desktop",
            client_id="example-desktop-public-client",
            client_type=model.CLIENT_PUBLIC,
            authorization_grant_type=model.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://127.0.0.1/callback",
        )
        # Bearer requests carry no CSRF token; this client would reject them
        # if the route were still behind Django's CSRF check.
        self.api = Client(enforce_csrf_checks=True)

    def token(self, user=None, scope=f"desktop.core {SCOPE}", value="example-desktop-token"):
        # Third Party
        from oauth2_provider.models import get_access_token_model

        return get_access_token_model().objects.create(
            user=user or self.user,
            application=self.application,
            token=value,
            scope=scope,
            expires=timezone.now() + timedelta(seconds=60),
        ).token

    def post(self, content=SAMPLE_LOG, token=None, **kwargs):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token or self.token()}"}
        return self.api.post(f"{BASE}/upload", {"log_file": _file(content, **kwargs)}, **headers)


class DesktopUploadTests(DesktopTestCase):
    def test_created_then_updated_then_no_new_lines(self):
        token = self.token()
        first = self.post(token=token)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first["Cache-Control"], "no-store")
        data = first.json()
        self.assertEqual(data["result"], "created")
        session = CombatLogUpload.objects.get()
        self.assertEqual(session.uploaded_by, self.user)
        self.assertEqual(data["character"], "Example Pilot")
        self.assertEqual(data["session_url"], reverse("pilotstats:session", kwargs={"pk": session.pk}))

        grown = self.post(GROWN_LOG, token=token).json()
        self.assertEqual(grown["result"], "updated")
        self.assertEqual(grown["new_lines"], 1)

        again = self.post(SAMPLE_LOG + "[ 2026.09.14 23:30:00 ] (notify) Example notice\n", token=token)
        self.assertEqual(again.json()["result"], "no_new_lines")
        self.assertEqual(CombatLogUpload.objects.count(), 1)

    def test_duplicate(self):
        token = self.token()
        self.post(token=token)
        self.assertEqual(self.post(token=token).json()["result"], "duplicate")

    def test_another_users_character_is_not_owned(self):
        EveCharacter.objects.create(
            character_id=1000000500, character_name="Someone Else", corporation_id=2345,
        )
        data = self.post(SAMPLE_LOG.replace("Example Pilot", "Someone Else")).json()
        self.assertEqual(data["result"], "not_owned")
        self.assertEqual(CombatLogUpload.objects.count(), 0)

    def test_missing_file_is_a_400(self):
        response = self.api.post(
            f"{BASE}/upload", {}, HTTP_AUTHORIZATION=f"Bearer {self.token()}"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["result"], "invalid")

    def test_shares_the_scanner_throttle(self):
        """The browser scanner and AA Desktop count against one per-user limit."""
        self.client.force_login(self.user)
        with mock.patch.object(views, "SCANNER_UPLOADS_PER_MINUTE", 1):
            scanner = self.client.post(
                reverse("pilotstats:upload"), {"log_file": _file(name="a.txt")},
                HTTP_ACCEPT="application/json",
            )
            desktop = self.post(name="b.txt")
        self.assertEqual(scanner.status_code, 200)
        self.assertEqual(desktop.status_code, 429)
        self.assertEqual(desktop.json()["result"], "throttled")
        self.assertTrue(1 <= int(desktop["Retry-After"]) <= 60)


class DesktopAuthTests(DesktopTestCase):
    def test_no_token_is_401(self):
        response = self.api.post(f"{BASE}/upload", {"log_file": _file()})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.api.get(f"{BASE}/characters").status_code, 401)

    def test_a_session_cookie_is_not_a_credential(self):
        self.api.force_login(self.user)
        self.assertEqual(self.api.get(f"{BASE}/characters").status_code, 401)
        self.assertEqual(CombatLogUpload.objects.count(), 0)

    def test_a_token_without_the_upload_scope_is_403(self):
        token = self.token(scope="desktop.core")
        response = self.post(token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "insufficient_scope")
        self.assertEqual(CombatLogUpload.objects.count(), 0)

    def test_a_user_without_basic_access_is_403(self):
        viewer = AuthUtils.create_user("desktop_viewer")
        AuthUtils.add_main_character_2(viewer, "Viewer Main", 1000000096, corp_id=2345)
        token = self.token(user=viewer, value="example-viewer-token")
        response = self.post(token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "permission_denied")
        characters = self.api.get(f"{BASE}/characters", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(characters.status_code, 403)


class DesktopCharactersTests(DesktopTestCase):
    def test_lists_only_the_users_own_characters(self):
        alt = EveCharacter.objects.create(
            character_id=1000000098, character_name="Example Alt", corporation_id=2345,
        )
        CharacterOwnership.objects.create(character=alt, user=self.user, owner_hash="hash-alt")
        other = AuthUtils.create_user("desktop_other")
        other_main = AuthUtils.add_main_character_2(other, "Other Main", 1000000097, corp_id=2345)
        CharacterOwnership.objects.create(character=other_main, user=other, owner_hash="hash-other")

        response = self.api.get(f"{BASE}/characters", HTTP_AUTHORIZATION=f"Bearer {self.token()}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json(), {"characters": ["Example Alt", "Example Pilot"]})


class DesktopModuleTests(DesktopTestCase):
    def test_the_bridge_mounted_pilotstats(self):
        # Third Party
        from aa_desktop_bridge.api import get_registry

        registry = get_registry()
        self.assertEqual([module.id for module in registry.mounted], ["pilotstats"])
        self.assertEqual(registry.skipped, [])
        module = registry.mounted[0]
        self.assertEqual(set(module.scopes), {SCOPE})
        self.assertEqual(module.required_permissions, ("pilotstats.basic_access",))

    def test_modules_lists_pilotstats_for_a_user_with_access(self):
        response = self.api.get(
            "/api/desktop/v1/modules", HTTP_AUTHORIZATION=f"Bearer {self.token()}"
        )
        modules = response.json()["modules"]
        self.assertEqual([module["id"] for module in modules], ["pilotstats"])
        self.assertEqual(modules[0]["scopes"][0]["name"], SCOPE)
        self.assertTrue(modules[0]["scopes"][0]["granted"])

    def test_discovery_lists_the_upload_scope(self):
        body = self.api.get("/api/desktop/v1/discovery").json()
        self.assertIn(SCOPE, {scope["name"] for scope in body["scopes"]})
