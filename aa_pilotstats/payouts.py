"""
Wallet-journal payouts: what is kept, and how.

Only payout lines are stored: bounty, ESS and mission rewards (the
``Payout.EntryType`` values). Every ``*_tax`` line, every other journal
type, and every field besides id / date / type / amount is dropped. For
``bounty_prizes`` lines the NPC kill counts are derived from the line's
``reason`` (a ``typeID: count`` list); the raw text is not stored.
"""

# Standard Library
import logging
from collections import defaultdict
from datetime import timezone as dt_timezone
from decimal import Decimal

# Django
from django.db import transaction
from django.db.models import F

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from .models import NpcKillDaily, Payout, PayoutDaily, PilotSettings

logger = logging.getLogger(__name__)

PAYOUT_TYPES = frozenset(choice.value for choice in Payout.EntryType)
NPC_KILL_TYPES = frozenset({Payout.EntryType.BOUNTY_PRIZES.value})


def is_payout(ref_type: str) -> bool:
    return bool(ref_type) and ref_type in PAYOUT_TYPES and not ref_type.endswith("_tax")


def parse_npc_kills(reason) -> dict:
    """``"23707: 3,23708: 1"`` → ``{23707: 3, 23708: 1}``. Malformed parts are skipped."""
    kills: dict = defaultdict(int)
    for part in str(reason or "").split(","):
        type_id, sep, count = part.partition(":")
        if not sep:
            continue
        try:
            type_id, count = int(type_id.strip()), int(count.strip())
        except ValueError:
            continue
        if type_id > 0 and count > 0:
            kills[type_id] += count
    return dict(kills)


def _field(entry, name):
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def store_journal(character, entries) -> int:
    """Store the payout lines among *entries* for *character*. Returns how many were new.

    Daily totals and NPC counts are added only for journal ids not stored
    before, in the same transaction, so a re-pull never double-counts.
    """
    wanted = {}
    for entry in entries:
        ref_type = _field(entry, "ref_type")
        if not is_payout(ref_type):
            continue
        ref_id = _field(entry, "id")
        occurred_at = _field(entry, "date")
        amount = _field(entry, "amount")
        if ref_id is None or occurred_at is None or amount is None:
            continue
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=dt_timezone.utc)
        wanted[int(ref_id)] = Payout(
            character=character,
            occurred_at=occurred_at,
            entry_type=ref_type,
            amount=Decimal(str(amount)),
            ref_id=int(ref_id),
            npc_kills=(
                {str(k): v for k, v in parse_npc_kills(_field(entry, "reason")).items()}
                if ref_type in NPC_KILL_TYPES else {}
            ),
        )
    if not wanted:
        return 0

    with transaction.atomic():
        known = set(
            Payout.objects.filter(ref_id__in=wanted).values_list("ref_id", flat=True)
        )
        new_rows = [row for ref_id, row in wanted.items() if ref_id not in known]
        if not new_rows:
            return 0
        Payout.objects.bulk_create(new_rows)

        day_totals: dict = defaultdict(Decimal)
        npc_totals: dict = defaultdict(int)
        for row in new_rows:
            day = row.occurred_at.astimezone(dt_timezone.utc).date()
            day_totals[(day, row.entry_type)] += row.amount
            for type_id, count in row.npc_kills.items():
                npc_totals[(day, int(type_id))] += count

        for (day, entry_type), amount in day_totals.items():
            obj, created = PayoutDaily.objects.get_or_create(
                character=character, day=day, entry_type=entry_type,
                defaults={"amount": amount},
            )
            if not created:
                PayoutDaily.objects.filter(pk=obj.pk).update(amount=F("amount") + amount)
        for (day, type_id), count in npc_totals.items():
            obj, created = NpcKillDaily.objects.get_or_create(
                character=character, day=day, npc_type_id=type_id,
                defaults={"count": count},
            )
            if not created:
                NpcKillDaily.objects.filter(pk=obj.pk).update(count=F("count") + count)
    return len(new_rows)


def wallet_opted_out_user_ids() -> set:
    return set(
        PilotSettings.objects.filter(wallet_opt_out=True).values_list("user_id", flat=True)
    )


def delete_wallet_data(user) -> None:
    """Remove every stored payout, daily total, and NPC count for *user*'s characters."""
    character_ids = CharacterOwnership.objects.filter(user=user).values_list("character_id", flat=True)
    Payout.objects.filter(character_id__in=character_ids).delete()
    PayoutDaily.objects.filter(character_id__in=character_ids).delete()
    NpcKillDaily.objects.filter(character_id__in=character_ids).delete()
