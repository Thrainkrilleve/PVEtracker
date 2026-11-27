"""
Migration helpers for taking over a PvE Tracker install.

Pilot Stats was called PvE Tracker (package ``aa_pvetracker``, Django label
``pvetracker``) up to v0.0.11. Its tables, permission, and migration history
are adopted by ``0001_initial`` so an upgrade keeps every stored session and
every group's upload permission.

Migrations import this module by path, so keep it (and these names) even
after nobody runs the upgrade any more.
"""

# Django
from django.db import migrations
from django.db.migrations.recorder import MigrationRecorder

LEGACY_LABEL = "pvetracker"
NEW_LABEL = "pilotstats"

# Every managed PvE Tracker model as of v0.0.11 (General is unmanaged).
LEGACY_TABLES = {
    "pvetracker_combatlogupload": "pilotstats_combatlogupload",
    "pvetracker_pilotsettings": "pilotstats_pilotsettings",
    "pvetracker_payout": "pilotstats_payout",
}

NEW_PERMISSION_NAMES = {
    "basic_access": "Can upload Pilot Stats combat logs",
}


def adopt_legacy_tables(apps, schema_editor):
    """Rename PvE Tracker's tables to Pilot Stats names, keeping their rows.

    Index and constraint names are not touched: the models keep the old
    ``pvetracker_*`` names so the renamed tables match the migration state.
    """
    existing = set(schema_editor.connection.introspection.table_names())
    for old, new in LEGACY_TABLES.items():
        if old in existing and new not in existing:
            schema_editor.alter_db_table(None, old, new)


def adopt_legacy_records(apps, schema_editor):
    """Move content types (and with them permission grants) and drop old migration rows."""
    db = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    for legacy in ContentType.objects.using(db).filter(app_label=LEGACY_LABEL):
        current = (
            ContentType.objects.using(db)
            .filter(app_label=NEW_LABEL, model=legacy.model)
            .first()
        )
        if current is None:
            legacy.app_label = NEW_LABEL
            legacy.save(update_fields=["app_label"])
            continue
        # Both exist (e.g. Pilot Stats ran post_migrate earlier). Keep the new
        # content type and carry over any permission it doesn't already have,
        # so group and user grants survive.
        for perm in Permission.objects.using(db).filter(content_type=legacy):
            if Permission.objects.using(db).filter(
                content_type=current, codename=perm.codename
            ).exists():
                continue
            perm.content_type = current
            perm.save(update_fields=["content_type"])
        legacy.delete()

    for codename, name in NEW_PERMISSION_NAMES.items():
        Permission.objects.using(db).filter(
            content_type__app_label=NEW_LABEL, codename=codename
        ).update(name=name)

    MigrationRecorder(schema_editor.connection).migration_qs.filter(app=LEGACY_LABEL).delete()


class CreateModelUnlessAdopted(migrations.CreateModel):
    """``CreateModel`` that leaves the table alone when it already exists.

    After ``adopt_legacy_tables`` an upgraded install already has the table
    (with its data, indexes, and constraints), so only the migration state is
    recorded. A fresh install has no table and gets a normal create.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.name)
        if model._meta.managed and model._meta.db_table in set(
            schema_editor.connection.introspection.table_names()
        ):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)
