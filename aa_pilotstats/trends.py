"""
Personal trends for the My Stats page: a pilot's weekly numbers over the last
few weeks, and their personal records.

Weeks are EVE weeks: they start Monday 00:00 UTC (EVE time) and sessions count
in the week they were flown (``session_started``), not uploaded. Rates are
weighted by time (total damage or ISK over total hours), and sessions shorter
than ``ISK_RATE_MIN_SECONDS`` don't count toward a rate, the same rule as ISK
per hour on the session page and the leaderboard.
"""

# Standard Library
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

# Django
from django.db.models import Sum
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from .models import ISK_RATE_MIN_SECONDS, CombatLogUpload, NpcKillDaily, PayoutDaily

WEEKS = 12

SESSION_FIELDS = (
    "pk", "character", "session_started", "session_ended",
    "damage_out", "reps_given_total", "payout_isk",
)


def week_start(moment) -> datetime:
    """Monday 00:00 UTC of the EVE week holding *moment*."""
    day = moment.astimezone(dt_timezone.utc).date()
    monday = day - timedelta(days=day.weekday())
    return datetime.combine(monday, time.min, tzinfo=dt_timezone.utc)


def _sessions(user):
    return (
        CombatLogUpload.objects.filter(
            uploaded_by=user, session_started__isnull=False, session_ended__isnull=False,
        )
        .select_related("character")
        .only(*SESSION_FIELDS, "character__character_name")
    )


def _character_ids(user):
    return list(CharacterOwnership.objects.filter(user=user).values_list("character_id", flat=True))


def weekly(user, weeks=WEEKS, now=None) -> list:
    """One dict per week, oldest first, for the last *weeks* weeks (this one included).

    ``dps`` and ``isk_per_hour`` are None when no session of 10 minutes or
    more (with wallet data, for ISK) was flown that week. ``payouts`` and
    ``rats`` come from the wallet journal and cover all payout types,
    ESS included, whether or not a combat log was uploaded.
    """
    now = now or timezone.now()
    first = week_start(now) - timedelta(weeks=weeks - 1)
    rows = {}
    for index in range(weeks):
        start = first + timedelta(weeks=index)
        rows[start.date()] = {
            "week": start.date(),
            "sessions": 0,
            "seconds": 0.0,
            "damage_out": 0,
            "reps_given": 0,
            "_dps_damage": 0,
            "_dps_seconds": 0.0,
            "_isk": Decimal(0),
            "_isk_seconds": 0.0,
            "payouts": Decimal(0),
            "rats": 0,
        }

    for session in _sessions(user).filter(session_started__gte=first):
        row = rows.get(week_start(session.session_started).date())
        if row is None:
            continue
        seconds = max((session.session_ended - session.session_started).total_seconds(), 0.0)
        row["sessions"] += 1
        row["seconds"] += seconds
        row["damage_out"] += session.damage_out
        row["reps_given"] += session.reps_given_total
        if seconds >= ISK_RATE_MIN_SECONDS:
            row["_dps_damage"] += session.damage_out
            row["_dps_seconds"] += seconds
            if session.payout_isk is not None:
                row["_isk"] += session.payout_isk
                row["_isk_seconds"] += seconds

    character_ids = _character_ids(user)
    for model, field, key in ((PayoutDaily, "amount", "payouts"), (NpcKillDaily, "count", "rats")):
        totals = (
            model.objects.filter(character_id__in=character_ids, day__gte=first.date())
            .values("day").annotate(total=Sum(field))
        )
        for item in totals:
            monday = item["day"] - timedelta(days=item["day"].weekday())
            if monday in rows:
                rows[monday][key] += item["total"] or 0

    result = []
    for row in rows.values():
        dps_seconds, isk_seconds = row.pop("_dps_seconds"), row.pop("_isk_seconds")
        dps_damage, isk = row.pop("_dps_damage"), row.pop("_isk")
        row["hours"] = row["seconds"] / 3600
        row["dps"] = dps_damage / dps_seconds if dps_seconds else None
        row["isk_per_hour"] = isk * 3600 / Decimal(str(isk_seconds)) if isk_seconds else None
        result.append(row)
    return result


def records(user) -> dict:
    """The pilot's all-time bests. Each value is None when there's nothing to show.

    Sessions under 10 minutes can hold the damage record but not a rate record.
    """
    best_damage = best_dps = best_isk = None
    for session in _sessions(user):
        if session.damage_out and (best_damage is None or session.damage_out > best_damage.damage_out):
            best_damage = session
        if session.duration_seconds >= ISK_RATE_MIN_SECONDS:
            if session.damage_out and (best_dps is None or session.dps_out > best_dps.dps_out):
                best_dps = session
            rate = session.isk_per_hour
            if rate and (best_isk is None or rate > best_isk.isk_per_hour):
                best_isk = session

    character_ids = _character_ids(user)

    def best_day(model, field):
        item = (
            model.objects.filter(character_id__in=character_ids)
            .values("day").annotate(total=Sum(field)).order_by("-total", "-day").first()
        )
        return item if item and item["total"] else None

    return {
        "damage": best_damage,
        "dps": best_dps,
        "isk_per_hour": best_isk,
        "payout_day": best_day(PayoutDaily, "amount"),
        "rats_day": best_day(NpcKillDaily, "count"),
    }
