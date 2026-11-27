"""
Initial Pilot Stats schema, and the upgrade from PvE Tracker.

Fresh install: creates the tables.

Upgrade from PvE Tracker (``aa_pvetracker`` <= 0.0.11, label ``pvetracker``):
renames its tables to ``pilotstats_*`` with their rows, records the schema
without recreating it, moves the old content types (so ``basic_access``
grants carry over), and drops PvE Tracker's migration rows. See
``aa_pilotstats.migration_ops``.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from aa_pilotstats.migration_ops import (
    CreateModelUnlessAdopted,
    adopt_legacy_records,
    adopt_legacy_tables,
)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('eveonline', '0021_alter_eveallianceinfo_options_and_more'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Table renames are DDL. MySQL/MariaDB can't roll DDL back, and Django
        # refuses DDL inside a transaction there, so this step isn't atomic.
        migrations.RunPython(adopt_legacy_tables, migrations.RunPython.noop, atomic=False),
        CreateModelUnlessAdopted(
            name='General',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'permissions': (('basic_access', 'Can upload Pilot Stats combat logs'),),
                'managed': False,
                'default_permissions': (),
            },
        ),
        CreateModelUnlessAdopted(
            name='PilotSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hide_from_live_feed', models.BooleanField(default=False, help_text="Hide this user's sessions from the events feed. Leaderboard totals stay.", verbose_name='hide from the live feed')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='pilotstats_settings', to=settings.AUTH_USER_MODEL, verbose_name='user')),
            ],
            options={
                'verbose_name': 'pilot settings',
                'verbose_name_plural': 'pilot settings',
            },
        ),
        CreateModelUnlessAdopted(
            name='CombatLogUpload',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, verbose_name='uploaded at')),
                ('session_started', models.DateTimeField(blank=True, null=True, verbose_name='session started')),
                ('session_ended', models.DateTimeField(blank=True, null=True, verbose_name='session ended')),
                ('log_started', models.DateTimeField(blank=True, help_text="The Gamelog header's 'Session Started:' time (UTC). With the character, this identifies one client log, so re-uploading a log that has grown updates the same session instead of adding a second one.", null=True, verbose_name='log started')),
                ('source_filename', models.CharField(max_length=255, verbose_name='source filename')),
                ('file_hash', models.CharField(help_text='SHA-256 of the raw upload — rejects accidental duplicate re-uploads.', max_length=64, unique=True, verbose_name='file hash')),
                ('damage_out', models.PositiveIntegerField(default=0, verbose_name='damage dealt')),
                ('damage_in', models.PositiveIntegerField(default=0, verbose_name='damage received')),
                ('shots_out', models.PositiveIntegerField(default=0, verbose_name='shots fired')),
                ('shots_in', models.PositiveIntegerField(default=0, verbose_name='shots received')),
                ('reps_received_total', models.PositiveIntegerField(default=0, verbose_name='reps received')),
                ('reps_given_total', models.PositiveIntegerField(default=0, help_text="Estimated: the outgoing-rep line pattern hasn't been checked against a real logi pilot's log yet (see Known limitations in the README).", verbose_name='reps given')),
                ('quality_out', models.JSONField(blank=True, default=list, verbose_name='hit quality — outgoing')),
                ('quality_in', models.JSONField(blank=True, default=list, verbose_name='hit quality — incoming')),
                ('top_targets', models.JSONField(blank=True, default=list, verbose_name='top targets')),
                ('top_attackers', models.JSONField(blank=True, default=list, verbose_name='top attackers')),
                ('reps_by_healer', models.JSONField(blank=True, default=list, verbose_name='reps by healer')),
                ('reps_by_recipient', models.JSONField(blank=True, default=list, verbose_name='reps by recipient')),
                ('feed', models.JSONField(blank=True, default=list, help_text="Last events of the session (most recent first) — same shape as the standalone prototype's live feed, capped so this stays small.", verbose_name='recent activity')),
                ('combat_lines', models.PositiveIntegerField(default=0, help_text='Total (combat) channel lines found in the uploaded file.', verbose_name='combat lines seen')),
                ('matched_lines', models.PositiveIntegerField(default=0, help_text='Of the combat lines seen, how many matched a known event pattern.', verbose_name='combat lines scored')),
                ('ship_type_id', models.PositiveIntegerField(blank=True, help_text='Hull type id for this session, for a later SDE name lookup. Empty until a source reports it.', null=True, verbose_name='ship type id')),
                ('character', models.ForeignKey(help_text="Character the combat log's 'Listener:' header identified.", on_delete=django.db.models.deletion.CASCADE, related_name='pilotstats_uploads', to='eveonline.evecharacter', verbose_name='character')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pilotstats_uploads', to=settings.AUTH_USER_MODEL, verbose_name='uploaded by')),
            ],
            options={
                'verbose_name': 'combat log upload',
                'verbose_name_plural': 'combat log uploads',
                'ordering': ['-uploaded_at'],
                'indexes': [models.Index(fields=['session_started'], name='pvetracker_session_started')],
                'constraints': [models.UniqueConstraint(fields=('character', 'log_started'), name='pvetracker_unique_character_log')],
            },
        ),
        CreateModelUnlessAdopted(
            name='Payout',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('occurred_at', models.DateTimeField(verbose_name='time')),
                ('entry_type', models.CharField(choices=[('bounty', 'Bounty'), ('bounty_prize', 'Bounty prize'), ('bounty_prizes', 'Bounty prizes'), ('ess_escrow_transfer', 'ESS escrow'), ('agent_mission_reward', 'Mission reward'), ('agent_mission_time_bonus_reward', 'Mission time bonus'), ('mission_reward', 'Mission reward')], max_length=64, verbose_name='type')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=20, verbose_name='amount')),
                ('ref_id', models.BigIntegerField(help_text='ESI wallet-journal id. Unique so the same line is not stored twice.', unique=True, verbose_name='journal id')),
                ('character', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pilotstats_payouts', to='eveonline.evecharacter', verbose_name='character')),
            ],
            options={
                'verbose_name': 'payout',
                'verbose_name_plural': 'payouts',
                'ordering': ['-occurred_at'],
                'indexes': [models.Index(fields=['character', 'occurred_at'], name='pvetracker__charact_7a7d23_idx')],
            },
        ),
        migrations.RunPython(adopt_legacy_records, migrations.RunPython.noop),
    ]
