"""
Shared fleet leaderboard roll-up.

Web views, the dashboard widget, and a later Discord digest all group the
same way: everything a user's characters did folds up to their designated
main character. Gamelog sessions give damage and reps; wallet payouts, rat
counts, and killmails are merged into the same buckets as separate
columns. Damage, ISK, and kill value are never added together.
"""

# Standard Library
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

SUM_FIELDS = (
    "damage_out",
    "damage_in",
    "reps_received_total",
    "reps_given_total",
)

# Columns from the other sources (see merge_sources). isk_per_hour is
# filled by rollup_sessions from each session's matched payouts.
SOURCE_FIELDS = (
    "payout_isk",
    "isk_per_hour",
    "rats_killed",
    "kills",
    "losses",
    "isk_destroyed",
    "isk_lost",
    "km_damage",
)

SORT_FIELDS = SUM_FIELDS + SOURCE_FIELDS

# "all" is the absence of a cutoff, not a day count.
RANGE_DAYS = {"7d": 7, "30d": 30}

# Per-session JSON breakdowns. Roll-ups never read them, so list queries
# defer them rather than pull every session's feed and charts into memory.
DETAIL_FIELDS = (
    "quality_out",
    "quality_in",
    "top_targets",
    "top_attackers",
    "reps_by_healer",
    "reps_by_recipient",
    "feed",
)


def within_days(qs, days, now=None):
    """
    Keep sessions that were *flown* in the last ``days`` days.

    The fight time is the log's first combat line (``session_started``).
    Rows without one fall back to the upload time, so an old log uploaded
    today doesn't count as this week's work.
    """
    # Django
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    if now is None:
        now = timezone.now()
    return qs.annotate(
        flown_at=Coalesce("session_started", "uploaded_at"),
    ).filter(flown_at__gte=now - timedelta(days=days))


def user_main(user):
    """Designated main character, or None when the user has no profile or no main."""
    profile = getattr(user, "profile", None)
    return getattr(profile, "main_character", None)


def normalize_sort_key(sort_key, sum_fields=SUM_FIELDS):
    """Fall back to the first sum field when the requested sort is unknown."""
    if sort_key in sum_fields:
        return sort_key
    return sum_fields[0]


def rollup_sessions(sessions, *, sort_key="damage_out", sum_fields=SUM_FIELDS):
    """
    Bucket session-like rows by the uploader's main character.

    Each item needs ``uploaded_by`` (user), ``character`` (with
    ``character_name``), and the numeric attributes named in ``sum_fields``.
    ``uploaded_by_id`` is used when present so a user with no main stays in
    their own bucket.

    Returns a list of dicts, highest ``sort_key`` first::

        {
            "key": <character_id or "user:<id>">,
            "label": str,
            "main": character or None,
            "user_id": int,
            "upload_count": int,
            "<sum field>": int,
            "isk_per_hour": Decimal or None,
            "by_character": [(name, {field: int, ...}), ...],
        }

    ``isk_per_hour`` is weighted by time: payout ISK earned in the bucket's
    sessions divided by their total hours. Only sessions with wallet data
    and at least ``ISK_RATE_MIN_SECONDS`` long count, so pilots without a
    connected wallet get None rather than 0.
    """
    from .models import ISK_RATE_MIN_SECONDS

    fields = tuple(sum_fields)
    sort_key = normalize_sort_key(sort_key, fields)
    buckets: dict = {}

    for row in sessions:
        user = row.uploaded_by
        main = user_main(user)
        user_id = getattr(row, "uploaded_by_id", None)
        if user_id is None:
            user_id = user.pk
        key = main.character_id if main else f"user:{user_id}"
        label = main.character_name if main else user.username

        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "key": key,
                "label": label,
                "main": main,
                "user_id": user_id,
                "upload_count": 0,
                "by_character": {},
                "_earned": Decimal(0),
                "_earned_seconds": 0.0,
            }
            for field in fields:
                bucket[field] = 0
            buckets[key] = bucket

        bucket["upload_count"] += 1
        earned = getattr(row, "payout_isk", None)
        seconds = _session_seconds(row)
        if earned is not None and seconds >= ISK_RATE_MIN_SECONDS:
            bucket["_earned"] += earned
            bucket["_earned_seconds"] += seconds
        for field in fields:
            bucket[field] += getattr(row, field, 0) or 0

        char_name = row.character.character_name
        char_bucket = bucket["by_character"].get(char_name)
        if char_bucket is None:
            char_bucket = {field: 0 for field in fields}
            bucket["by_character"][char_name] = char_bucket
        for field in fields:
            char_bucket[field] += getattr(row, field, 0) or 0

    rows = list(buckets.values())
    for row in rows:
        earned, seconds = row.pop("_earned"), row.pop("_earned_seconds")
        row["isk_per_hour"] = earned * 3600 / Decimal(str(seconds)) if seconds else None
    rows.sort(key=lambda item: item[sort_key], reverse=True)
    for row in rows:
        row["by_character"] = sorted(
            row["by_character"].items(),
            key=lambda kv: kv[1][sort_key],
            reverse=True,
        )
    return rows


def _session_seconds(row) -> float:
    started = getattr(row, "session_started", None)
    ended = getattr(row, "session_ended", None)
    if not started or not ended:
        return 0.0
    return max((ended - started).total_seconds(), 0.0)


def rank_of(rows, user):
    """1-based rank of ``user``'s bucket, or None when they have no row."""
    main = user_main(user)
    key = main.character_id if main else f"user:{user.pk}"
    for index, row in enumerate(rows, start=1):
        if row["key"] == key:
            return index
    return None


def leaderboard_rows(
    range_key="30d", sort_key="damage_out", *, sum_fields=SUM_FIELDS, now=None, sources=True,
):
    """
    Roll up stored combat-log sessions, then (``sources=True``) merge in
    payouts, rat counts, and killmails per main.

    ``range_key`` is ``"7d"``, ``"30d"``, or anything else (treated as all time,
    matching the leaderboard's existing query-string behavior). The window is
    on when things happened, not when they were uploaded or fetched (see
    ``within_days``).
    """
    # Django
    from django.utils import timezone

    from .models import CombatLogUpload

    if now is None:
        now = timezone.now()
    qs = CombatLogUpload.objects.select_related(
        "character",
        "uploaded_by",
        "uploaded_by__profile",
        "uploaded_by__profile__main_character",
    ).defer(*DETAIL_FIELDS)
    days = RANGE_DAYS.get(range_key)
    if days is not None:
        qs = within_days(qs, days, now)
    session_sort = sort_key if sort_key in sum_fields else sum_fields[0]
    rows = rollup_sessions(qs, sort_key=session_sort, sum_fields=sum_fields)
    if not sources:
        return rows
    rows = merge_sources(rows, days=days, now=now)
    sort_key = normalize_sort_key(sort_key, tuple(sum_fields) + SOURCE_FIELDS)
    rows.sort(key=lambda row: row.get(sort_key) or 0, reverse=True)
    return rows


def character_buckets(character_pks) -> dict:
    """``{EveCharacter pk: (key, label, main, user_id)}`` using the same keys as rollup_sessions.

    A character nobody owns on Auth gets its own ``char:<pk>`` bucket.
    """
    # Alliance Auth
    from allianceauth.authentication.models import CharacterOwnership
    from allianceauth.eveonline.models import EveCharacter

    result = {}
    owned = CharacterOwnership.objects.filter(character_id__in=character_pks).select_related(
        "user", "user__profile", "user__profile__main_character",
    )
    for ownership in owned:
        user = ownership.user
        main = user_main(user)
        key = main.character_id if main else f"user:{user.pk}"
        label = main.character_name if main else user.username
        result[ownership.character_id] = (key, label, main, user.pk)
    for character in EveCharacter.objects.filter(pk__in=set(character_pks) - set(result)):
        result[character.pk] = (f"char:{character.pk}", character.character_name, None, None)
    return result


def merge_sources(rows, *, days=None, now=None):
    """Add payout ISK, rat counts, and killmail columns to *rows* (and new rows as needed).

    Each killmail counts once per bucket, so two alts of one main on the same
    kill are one kill and one kill's value. Killmail damage is summed per
    character (it's that character's own damage on the mail).
    """
    # Django
    from django.db.models import Sum
    from django.utils import timezone

    from esi.models import Token

    from .app_settings import WALLET_SCOPE
    from .models import KillmailParticipant, NpcKillDaily, PayoutDaily, PilotSettings

    if now is None:
        now = timezone.now()
    since = now - timedelta(days=days) if days else None

    payout_qs = PayoutDaily.objects.all()
    npc_qs = NpcKillDaily.objects.all()
    km_qs = KillmailParticipant.objects.all()
    if since is not None:
        payout_qs = payout_qs.filter(day__gte=since.date())
        npc_qs = npc_qs.filter(day__gte=since.date())
        km_qs = km_qs.filter(killmail__killmail_time__gte=since)

    payouts = list(payout_qs.values("character_id", "entry_type").annotate(total=Sum("amount")))
    rats = list(npc_qs.values("character_id").annotate(total=Sum("count")))
    entries = list(km_qs.values(
        "character_id", "is_victim", "killmail_id", "killmail__total_value", "damage",
    ))

    character_pks = (
        {p["character_id"] for p in payouts}
        | {r["character_id"] for r in rats}
        | {e["character_id"] for e in entries}
    )
    buckets_for = character_buckets(character_pks)
    by_key = {row["key"]: row for row in rows}

    def bucket(character_pk):
        key, label, main, user_id = buckets_for[character_pk]
        row = by_key.get(key)
        if row is None:
            row = {
                "key": key, "label": label, "main": main, "user_id": user_id,
                "upload_count": 0, "by_character": [],
                **{field: 0 for field in SUM_FIELDS},
            }
            by_key[key] = row
            rows.append(row)
        return row

    for row in rows:
        row.update({
            "payout_isk": Decimal(0), "payout_by_type": defaultdict(Decimal),
            "rats_killed": 0, "km_damage": 0,
            "_kills": {}, "_losses": {},
        })

    for item in payouts:
        row = bucket(item["character_id"])
        _ensure_source_fields(row)
        row["payout_isk"] += item["total"] or 0
        row["payout_by_type"][item["entry_type"]] += item["total"] or 0
    for item in rats:
        row = bucket(item["character_id"])
        _ensure_source_fields(row)
        row["rats_killed"] += item["total"] or 0
    for item in entries:
        row = bucket(item["character_id"])
        _ensure_source_fields(row)
        value = item["killmail__total_value"] or Decimal(0)
        if item["is_victim"]:
            row["_losses"][item["killmail_id"]] = value
        else:
            row["_kills"][item["killmail_id"]] = value
            row["km_damage"] += item["damage"] or 0

    opted_out = set(PilotSettings.objects.filter(wallet_opt_out=True).values_list("user_id", flat=True))
    wallet_users = set(
        Token.objects.filter(scopes__name=WALLET_SCOPE).values_list("user_id", flat=True)
    ) - opted_out
    for row in rows:
        _ensure_source_fields(row)
        kills, losses = row.pop("_kills"), row.pop("_losses")
        row["kills"], row["isk_destroyed"] = len(kills), sum(kills.values(), Decimal(0))
        row["losses"], row["isk_lost"] = len(losses), sum(losses.values(), Decimal(0))
        row["payout_by_type"] = dict(row["payout_by_type"])
        row["wallet_connected"] = row.get("user_id") in wallet_users
    return rows


def _ensure_source_fields(row):
    if "_kills" not in row and "kills" not in row:
        row.update({
            "payout_isk": Decimal(0), "payout_by_type": defaultdict(Decimal),
            "rats_killed": 0, "km_damage": 0, "_kills": {}, "_losses": {},
        })
