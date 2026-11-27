"""
Upgrade from PvE Tracker (aa_pvetracker <= 0.0.11, label "pvetracker").

The test database starts as a fresh Pilot Stats install. Each test turns it
back into what a PvE Tracker 0.0.11 site looks like (old table names, old
content types and permission name, old migration rows), then runs the real
``migrate`` and checks nothing was lost.
"""

# Standard Library
from unittest import mock

# Django
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.checks import run_checks
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

from ..migration_ops import LEGACY_LABEL, LEGACY_TABLES, NEW_LABEL
from ..models import CombatLogUpload

LEGACY_MIGRATIONS = (
    "0001_initial",
    "0002_combatlogupload_feed",
    "0003_phase1_nav_and_phase2_stub",
    "0004_combatlogupload_log_started",
)


def _tables():
    return set(connection.introspection.table_names())


def _migrate():
    """Apply pilotstats 0001 exactly as `migrate` would, minus post_migrate
    signals (Auth's handlers want a Celery broker)."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(NEW_LABEL, "0001_initial")])


class UpgradeFromPvETrackerTests(TransactionTestCase):
    def setUp(self):
        # Adding a user to a group makes Auth queue a services-sync Celery
        # task; there's no broker in tests and the sync isn't under test.
        patcher = mock.patch("celery.app.task.Task.apply_async")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_legacy_install(self):
        recorder = MigrationRecorder(connection)
        with connection.schema_editor() as editor:
            for old, new in LEGACY_TABLES.items():
                editor.alter_db_table(None, new, old)
        ContentType.objects.filter(app_label=NEW_LABEL).update(app_label=LEGACY_LABEL)
        Permission.objects.filter(
            content_type__app_label=LEGACY_LABEL, codename="basic_access"
        ).update(name="Can upload PvE Tracker combat logs")
        recorder.migration_qs.filter(app=NEW_LABEL).delete()
        for name in LEGACY_MIGRATIONS:
            recorder.record_applied(LEGACY_LABEL, name)
        ContentType.objects.clear_cache()

    def test_migrate_keeps_data_permission_grants_and_drops_old_history(self):
        # Data and a group grant, as a PvE Tracker site would have them.
        uploader = AuthUtils.create_user("uploader")
        character = AuthUtils.add_main_character_2(
            uploader, "Example Pilot", 1000000300, corp_id=2345,
        )
        upload = CombatLogUpload.objects.create(
            character=character,
            uploaded_by=uploader,
            source_filename="session.txt",
            file_hash="hash-upgrade",
            damage_out=4321,
        )
        group = Group.objects.create(name="Uploaders")
        group.permissions.add(
            Permission.objects.get(content_type__app_label=NEW_LABEL, codename="basic_access")
        )
        member = User.objects.create_user("member")
        member.groups.add(group)

        self._make_legacy_install()
        self.assertTrue(set(LEGACY_TABLES) <= _tables())
        self.assertFalse(set(LEGACY_TABLES.values()) & _tables())

        _migrate()
        ContentType.objects.clear_cache()

        # Tables renamed, rows kept.
        self.assertTrue(set(LEGACY_TABLES.values()) <= _tables())
        self.assertFalse(set(LEGACY_TABLES) & _tables())
        self.assertEqual(CombatLogUpload.objects.get(pk=upload.pk).damage_out, 4321)

        # The group's grant follows the content type to the new label.
        member = User.objects.get(pk=member.pk)  # fresh permission cache
        self.assertTrue(member.has_perm(f"{NEW_LABEL}.basic_access"))
        self.assertFalse(ContentType.objects.filter(app_label=LEGACY_LABEL).exists())
        self.assertEqual(
            Permission.objects.get(content_type__app_label=NEW_LABEL, codename="basic_access").name,
            "Can upload Pilot Stats combat logs",
        )

        # Old migration history gone, new history recorded.
        recorder = MigrationRecorder(connection)
        self.assertFalse(recorder.migration_qs.filter(app=LEGACY_LABEL).exists())
        self.assertTrue(recorder.migration_qs.filter(app=NEW_LABEL, name="0001_initial").exists())

        # The adopted tables still work for new writes.
        CombatLogUpload.objects.create(
            character=character,
            uploaded_by=uploader,
            source_filename="after.txt",
            file_hash="hash-after-upgrade",
        )
        self.assertEqual(CombatLogUpload.objects.count(), 2)

    def test_migrate_on_an_install_with_both_content_types(self):
        # post_migrate may already have created Pilot Stats content types.
        self._make_legacy_install()
        for legacy in ContentType.objects.filter(app_label=LEGACY_LABEL):
            ContentType.objects.create(app_label=NEW_LABEL, model=legacy.model)
        legacy_perm = Permission.objects.get(
            content_type__app_label=LEGACY_LABEL, codename="basic_access"
        )
        group = Group.objects.create(name="Uploaders")
        group.permissions.add(legacy_perm)
        member = User.objects.create_user("member")
        member.groups.add(group)

        _migrate()
        ContentType.objects.clear_cache()

        member = User.objects.get(pk=member.pk)
        self.assertTrue(member.has_perm(f"{NEW_LABEL}.basic_access"))
        self.assertFalse(ContentType.objects.filter(app_label=LEGACY_LABEL).exists())


class MigrationShapeTests(TestCase):
    def test_table_renames_run_outside_a_transaction(self):
        # SQLite (the test database) allows DDL in a transaction, so only this
        # catches it: on MySQL/MariaDB an atomic rename step makes migrate fail
        # with "Executing DDL statements while in a transaction ... prohibited".
        import importlib

        from django.db.migrations import RunPython

        from ..migration_ops import adopt_legacy_tables

        module = importlib.import_module("aa_pilotstats.migrations.0001_initial")
        steps = [
            op for op in module.Migration.operations
            if isinstance(op, RunPython) and op.code is adopt_legacy_tables
        ]
        self.assertEqual(len(steps), 1)
        self.assertFalse(steps[0].atomic)


class LegacyUrlTests(TestCase):
    def test_old_links_redirect_to_the_new_path(self):
        user = AuthUtils.create_user("viewer")
        AuthUtils.add_main_character_2(user, "Viewer Main", 1000000301, corp_id=2345)
        self.client.force_login(user)

        response = self.client.get("/pvetracker/sessions/12/?range=7d")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"],
            reverse("pilotstats:session", kwargs={"pk": 12}) + "?range=7d",
        )
        self.assertEqual(self.client.get("/pvetracker/")["Location"], reverse("pilotstats:leaderboard"))


class LegacyAppCheckTests(TestCase):
    def test_both_apps_installed_is_an_error(self):
        # The legacy package isn't importable here, so don't let Django try to
        # load it: patch only what the check reads.
        from django.conf import settings

        from .. import checks

        fake = mock.Mock(INSTALLED_APPS=[*settings.INSTALLED_APPS, "aa_pvetracker"])
        with mock.patch.object(checks, "settings", fake):
            errors = checks.legacy_app_not_installed(None)
        self.assertEqual([e.id for e in errors], ["pilotstats.E001"])

    def test_no_error_without_the_legacy_app(self):
        ids = [e.id for e in run_checks()]
        self.assertNotIn("pilotstats.E001", ids)
