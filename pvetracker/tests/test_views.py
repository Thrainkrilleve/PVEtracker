"""
Django-backed tests — need a real Alliance Auth environment (Redis is
required just to boot allianceauth.authentication's app config), so these
are meant to run inside the Docker test instance:

    docker compose exec allianceauth_gunicorn python manage.py test pvetracker

Covers the two things that are actually risky to get wrong in this app:
ownership-gated upload attribution, and main/alt leaderboard roll-up.
"""

# Standard Library
import hashlib
from io import BytesIO

# Django
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from ..models import CombatLogUpload

SAMPLE_LOG = (
    "------------------------------------------------------------\n"
    "  Gamelog\n"
    "  Listener: Thrain Krill\n"
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
            character_id=2120829300, character_name="Thrain Krill", corporation_id=2345,
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

        self.main = EveCharacter.objects.create(
            character_id=1000000001, character_name="Main Character", corporation_id=2345,
        )
        self.alt = EveCharacter.objects.create(
            character_id=1000000002, character_name="Alt Character", corporation_id=2345,
        )
        _own_character(self.user, self.main)
        _own_character(self.user, self.alt)
        AuthUtils.add_main_character_2(self.user, "Main Character", 1000000001, corp_id=2345)

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
