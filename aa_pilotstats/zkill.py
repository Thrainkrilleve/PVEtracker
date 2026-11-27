"""
zKillboard R2Z2 killmails: which ones are kept.

A mail is kept when at least one victim or attacker is a character on Auth
whose corporation or alliance (as recorded on the mail) is tracked. Only
those Auth characters are stored as participants. Mails are keyed by
killmail_id, so the same mail seen again under a new sequence is ignored.
"""

# Standard Library
import logging
from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

# Django
from django.db import transaction

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter

from .app_settings import ZKILL_AUTO_STATES
from .models import Killmail, KillmailParticipant, TrackedEntity

logger = logging.getLogger(__name__)


def refresh_auto_entities() -> None:
    """Track the corporations and alliances of mains in the auto states.

    Admin-added rows are never touched. Automatic rows an admin disabled stay
    disabled (and stay, so the choice is remembered); other automatic rows
    whose corp/alliance no longer has a member main are removed.
    """
    mains = EveCharacter.objects.filter(
        userprofile__state__name__in=ZKILL_AUTO_STATES,
    ).values_list("corporation_id", "corporation_name", "alliance_id", "alliance_name")
    wanted = {}
    for corp_id, corp_name, alliance_id, alliance_name in mains:
        if corp_id:
            wanted[(TrackedEntity.Kind.CORPORATION, corp_id)] = corp_name or ""
        if alliance_id:
            wanted[(TrackedEntity.Kind.ALLIANCE, alliance_id)] = alliance_name or ""

    existing = {(e.kind, e.entity_id): e for e in TrackedEntity.objects.all()}
    for (kind, entity_id), name in wanted.items():
        row = existing.get((kind, entity_id))
        if row is None:
            TrackedEntity.objects.create(
                kind=kind, entity_id=entity_id, name=name, source=TrackedEntity.Source.AUTO,
            )
        elif row.source == TrackedEntity.Source.AUTO and name and row.name != name:
            row.name = name
            row.save(update_fields=["name"])
    stale = [
        row.pk for key, row in existing.items()
        if row.source == TrackedEntity.Source.AUTO and row.enabled and key not in wanted
    ]
    if stale:
        TrackedEntity.objects.filter(pk__in=stale).delete()


def tracked_ids() -> tuple:
    corps, alliances = set(), set()
    for kind, entity_id in TrackedEntity.objects.filter(enabled=True).values_list("kind", "entity_id"):
        (corps if kind == TrackedEntity.Kind.CORPORATION else alliances).add(entity_id)
    return corps, alliances


def _parse_time(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def process_killmail(package: dict, corps=None, alliances=None) -> bool:
    """Store one R2Z2 package if it involves a tracked Auth character. Returns whether it was kept."""
    if corps is None or alliances is None:
        corps, alliances = tracked_ids()
    if not corps and not alliances:
        return False
    esi = package.get("esi") or {}
    zkb = package.get("zkb") or {}
    killmail_id = package.get("killmail_id") or esi.get("killmail_id")
    if not killmail_id:
        return False

    victim = esi.get("victim") or {}
    entries = [(victim, True)] + [(a, False) for a in esi.get("attackers") or []]
    tracked = [
        (who, is_victim) for who, is_victim in entries
        if who.get("character_id")
        and (who.get("corporation_id") in corps or who.get("alliance_id") in alliances)
    ]
    if not tracked:
        return False
    on_auth = {
        c.character_id: c
        for c in EveCharacter.objects.filter(character_id__in={w["character_id"] for w, _v in tracked})
    }
    tracked = [(w, v) for w, v in tracked if w["character_id"] in on_auth]
    if not tracked:
        return False

    with transaction.atomic():
        killmail, _created = Killmail.objects.get_or_create(
            killmail_id=int(killmail_id),
            defaults={
                "killmail_hash": package.get("hash") or zkb.get("hash") or "",
                "killmail_time": _parse_time(esi.get("killmail_time")),
                "solar_system_id": esi.get("solar_system_id"),
                "victim_ship_type_id": victim.get("ship_type_id"),
                "total_value": Decimal(str(zkb.get("totalValue") or 0)),
                "npc": bool(zkb.get("npc")),
                "solo": bool(zkb.get("solo")),
            },
        )
        for who, is_victim in tracked:
            KillmailParticipant.objects.get_or_create(
                killmail=killmail,
                character=on_auth[who["character_id"]],
                is_victim=is_victim,
                defaults={
                    "final_blow": bool(who.get("final_blow")),
                    "damage": int(who.get("damage_taken" if is_victim else "damage_done") or 0),
                    "ship_type_id": who.get("ship_type_id"),
                },
            )
    return True

