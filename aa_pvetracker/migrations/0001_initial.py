# Generated migration for aa-pvetracker 0.0.1

# Django
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("eveonline", "0021_alter_eveallianceinfo_options_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- General (permissions only, unmanaged) --------------------------
        migrations.CreateModel(
            name="General",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "permissions": (("basic_access", "Can access PvE Tracker"),),
                "managed": False,
                "default_permissions": (),
            },
        ),
        # --- CombatLogUpload --------------------------------------------------
        migrations.CreateModel(
            name="CombatLogUpload",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("uploaded_at", models.DateTimeField(auto_now_add=True, verbose_name="uploaded at")),
                ("session_started", models.DateTimeField(blank=True, null=True, verbose_name="session started")),
                ("session_ended", models.DateTimeField(blank=True, null=True, verbose_name="session ended")),
                ("source_filename", models.CharField(max_length=255, verbose_name="source filename")),
                (
                    "file_hash",
                    models.CharField(
                        help_text="SHA-256 of the raw upload — rejects accidental duplicate re-uploads.",
                        max_length=64,
                        unique=True,
                        verbose_name="file hash",
                    ),
                ),
                ("damage_out", models.PositiveIntegerField(default=0, verbose_name="damage dealt")),
                ("damage_in", models.PositiveIntegerField(default=0, verbose_name="damage received")),
                ("shots_out", models.PositiveIntegerField(default=0, verbose_name="shots fired")),
                ("shots_in", models.PositiveIntegerField(default=0, verbose_name="shots received")),
                ("reps_received_total", models.PositiveIntegerField(default=0, verbose_name="reps received")),
                (
                    "reps_given_total",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Estimated — see PVETRACKER_REMOTE_REP_OUT_UNVERIFIED in the docs.",
                        verbose_name="reps given",
                    ),
                ),
                ("quality_out", models.JSONField(blank=True, default=list, verbose_name="hit quality — outgoing")),
                ("quality_in", models.JSONField(blank=True, default=list, verbose_name="hit quality — incoming")),
                ("top_targets", models.JSONField(blank=True, default=list, verbose_name="top targets")),
                ("top_attackers", models.JSONField(blank=True, default=list, verbose_name="top attackers")),
                ("reps_by_healer", models.JSONField(blank=True, default=list, verbose_name="reps by healer")),
                ("reps_by_recipient", models.JSONField(blank=True, default=list, verbose_name="reps by recipient")),
                (
                    "combat_lines",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Total (combat) channel lines found in the uploaded file.",
                        verbose_name="combat lines seen",
                    ),
                ),
                (
                    "matched_lines",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Of the combat lines seen, how many matched a known event pattern.",
                        verbose_name="combat lines scored",
                    ),
                ),
                (
                    "character",
                    models.ForeignKey(
                        help_text="Character the combat log's 'Listener:' header identified.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pvetracker_uploads",
                        to="eveonline.evecharacter",
                        verbose_name="character",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pvetracker_uploads",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="uploaded by",
                    ),
                ),
            ],
            options={
                "verbose_name": "combat log upload",
                "verbose_name_plural": "combat log uploads",
                "ordering": ["-uploaded_at"],
            },
        ),
    ]
