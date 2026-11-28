"""Fill in the activity (ratting, incursion, abyssal, missions) for stored sessions."""

# Django
from django.core.management.base import BaseCommand

from ...models import CombatLogUpload
from ...payouts import classify_unmatched, match_sessions


class Command(BaseCommand):
    help = (
        "Classify stored Gamelog sessions as ratting, incursion, abyssal or missions. "
        "Recent sessions use the wallet and the NPCs shot; older ones the NPCs only. "
        "Safe to run more than once."
    )

    def handle(self, *args, **options):
        characters = (
            CombatLogUpload.objects.order_by().values_list("character", flat=True).distinct()
        )
        matched = 0
        for character_pk in characters:
            session = CombatLogUpload.objects.filter(character_id=character_pk).select_related("character").first()
            matched += match_sessions(session.character)
        older = CombatLogUpload.objects.filter(activity="").only("pk", "activity", "top_targets")
        named = classify_unmatched(older.iterator())
        self.stdout.write(self.style.SUCCESS(
            f"Updated {matched} recent session(s) from the wallet and {named} older one(s) from NPC names."
        ))
