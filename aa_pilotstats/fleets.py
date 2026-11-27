"""
Fleet tracking with the fleet boss's ESI token.

A tracked fleet's roster is polled about once a minute. Each pilot gets one
FleetMember row per hull flown (first and last seen). Tracking ends by
itself when the boss leaves the fleet, hands boss over, or the token stops
working; it never retries in a loop.
"""

# Standard Library
import logging
from dataclasses import dataclass, field
from datetime import timedelta

# Django
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter

from esi.exceptions import HTTPClientError
from esi.models import Token

from . import providers
from .app_settings import FLEET_SCOPE, SESSION_LINK_MINUTES
from .models import CombatLogUpload, FleetMember, TrackedFleet

logger = logging.getLogger(__name__)


class FleetError(Exception):
    """Why a fleet can't be (or stops being) tracked, in words for the user."""


def boss_token(character_id: int):
    return Token.get_token(character_id, [FLEET_SCOPE])


def fleet_of(character_id: int, token):
    """(fleet_id, fleet_boss_id) for the character, or FleetError when not in a fleet."""
    try:
        info = providers.character_fleet(character_id, token)
    except HTTPClientError as exc:
        if exc.status_code == 404:
            raise FleetError("That character isn't in a fleet.") from exc
        raise
    return int(_get(info, "fleet_id")), int(_get(info, "fleet_boss_id") or 0)


def start_tracking(user, token) -> TrackedFleet:
    """Begin tracking the fleet *token*'s character bosses. Reuses an open row for the same fleet."""
    character = EveCharacter.objects.filter(character_id=token.character_id).first()
    if character is None:
        raise FleetError("That character isn't on Auth yet.")
    fleet_id, boss_id = fleet_of(token.character_id, token)
    if boss_id and boss_id != token.character_id:
        raise FleetError("Only the fleet boss can start tracking a fleet.")
    tracked = TrackedFleet.objects.filter(fleet_id=fleet_id, ended_at__isnull=True).first()
    if tracked is None:
        tracked = TrackedFleet.objects.create(fleet_id=fleet_id, boss=character, started_by=user)
    return tracked


def end_tracking(fleet: TrackedFleet, reason: str) -> None:
    fleet.ended_at = timezone.now()
    fleet.end_reason = reason[:255]
    fleet.save(update_fields=["ended_at", "end_reason"])


def poll_fleet(fleet: TrackedFleet, now=None) -> None:
    """Refresh one open fleet's roster, or close it with a reason."""
    now = now or timezone.now()
    boss_id = fleet.boss.character_id
    token = boss_token(boss_id)
    if token is None:
        end_tracking(fleet, "The boss's fleet token is missing or expired.")
        return
    try:
        current_fleet, current_boss = fleet_of(boss_id, token)
    except FleetError:
        end_tracking(fleet, "The boss left the fleet.")
        return
    if current_fleet != fleet.fleet_id:
        end_tracking(fleet, "The boss is now in a different fleet.")
        return
    if current_boss and current_boss != boss_id:
        end_tracking(fleet, "The boss handed fleet boss to someone else.")
        return
    try:
        members = providers.fleet_members(fleet.fleet_id, token)
    except HTTPClientError as exc:
        if exc.status_code in (403, 404):
            end_tracking(fleet, "ESI no longer shows this fleet's members to the boss.")
            return
        raise
    record_members(fleet, members, now)


def record_members(fleet: TrackedFleet, members, now) -> None:
    rows = [
        (int(_get(m, "character_id")), int(_get(m, "ship_type_id")))
        for m in members
        if _get(m, "character_id") and _get(m, "ship_type_id")
    ]
    on_auth = {
        c.character_id: c
        for c in EveCharacter.objects.filter(character_id__in={cid for cid, _ in rows})
    }
    with transaction.atomic():
        for character_id, ship_type_id in rows:
            member, created = FleetMember.objects.get_or_create(
                fleet=fleet,
                eve_character_id=character_id,
                ship_type_id=ship_type_id,
                defaults={
                    "character": on_auth.get(character_id),
                    "first_seen": now,
                    "last_seen": now,
                },
            )
            if not created:
                member.last_seen = now
                if member.character_id is None and character_id in on_auth:
                    member.character = on_auth[character_id]
                member.save(update_fields=["last_seen", "character"])
        fleet.last_polled_at = now
        fleet.save(update_fields=["last_polled_at"])


def hull_for_session(session: CombatLogUpload):
    """Ship type id a tracked fleet saw this session's character flying, or None.

    Caches the answer on ``session.ship_type_id``.
    """
    if session.ship_type_id:
        return session.ship_type_id
    if not session.session_started or not session.session_ended:
        return None
    slack = timedelta(minutes=SESSION_LINK_MINUTES)
    member = (
        FleetMember.objects.filter(
            character_id=session.character_id,
            first_seen__lte=session.session_ended + slack,
            last_seen__gte=session.session_started - slack,
        )
        .order_by("-last_seen")
        .first()
    )
    if member is None:
        return None
    CombatLogUpload.objects.filter(pk=session.pk).update(ship_type_id=member.ship_type_id)
    session.ship_type_id = member.ship_type_id
    return member.ship_type_id


@dataclass
class Coverage:
    on_auth: int = 0
    with_log: int = 0
    not_on_auth: int = 0
    rows: list = field(default_factory=list)


def coverage(fleet: TrackedFleet) -> Coverage:
    """Who was in the fleet, and which Auth pilots uploaded a Gamelog for that time."""
    end = fleet.ended_at or fleet.last_polled_at or timezone.now()
    slack = timedelta(minutes=SESSION_LINK_MINUTES)
    members = list(fleet.members.select_related("character").order_by("first_seen"))
    character_pks = {m.character_id for m in members if m.character_id}
    sessions = {}
    for upload in CombatLogUpload.objects.filter(
        Q(character_id__in=character_pks),
        session_started__lte=end + slack,
        session_ended__gte=fleet.started_at - slack,
    ).only("pk", "character_id", "damage_out"):
        sessions.setdefault(upload.character_id, upload)

    result = Coverage()
    seen_pilots = {}
    for member in members:
        pilot = seen_pilots.setdefault(member.eve_character_id, {
            "eve_character_id": member.eve_character_id,
            "character": member.character,
            "hulls": [],
            "first_seen": member.first_seen,
            "last_seen": member.last_seen,
            "session": sessions.get(member.character_id) if member.character_id else None,
        })
        pilot["hulls"].append(member.ship_type_id)
        pilot["first_seen"] = min(pilot["first_seen"], member.first_seen)
        pilot["last_seen"] = max(pilot["last_seen"], member.last_seen)
    for pilot in seen_pilots.values():
        if pilot["character"] is None:
            result.not_on_auth += 1
        else:
            result.on_auth += 1
            if pilot["session"] is not None:
                result.with_log += 1
        result.rows.append(pilot)
    return result


def _get(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
