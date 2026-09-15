# Generated migration for aa-pvetracker 0.0.6

# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pvetracker", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="combatlogupload",
            name="feed",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Last events of the session (most recent first) — same shape as the "
                    "standalone prototype's live feed, capped so this stays small."
                ),
                verbose_name="recent activity",
            ),
        ),
    ]
