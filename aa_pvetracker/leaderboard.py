"""
Shared fleet leaderboard roll-up.

Web views, the dashboard widget, and a later Discord digest all group the
same way: each user's sessions fold up to their designated main character,
with a per-character breakdown underneath. Extra numeric columns (payout
ISK, kill counts) can be summed later by passing ``sum_fields`` — damage,
ISK, and kill value stay separate numbers.
"""

# Standard Library
from datetime import timedelta

SUM_FIELDS = (
    "damage_out",
    "damage_in",
    "reps_received_total",
    "reps_given_total",
)

# "all" is the absence of a cutoff, not a day count.
RANGE_DAYS = {"7d": 7, "30d": 30}


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
            "by_character": [(name, {field: int, ...}), ...],
        }
    """
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
            }
            for field in fields:
                bucket[field] = 0
            buckets[key] = bucket

        bucket["upload_count"] += 1
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
    rows.sort(key=lambda item: item[sort_key], reverse=True)
    for row in rows:
        row["by_character"] = sorted(
            row["by_character"].items(),
            key=lambda kv: kv[1][sort_key],
            reverse=True,
        )
    return rows


def rank_of(rows, user):
    """1-based rank of ``user``'s bucket, or None when they have no row."""
    main = user_main(user)
    key = main.character_id if main else f"user:{user.pk}"
    for index, row in enumerate(rows, start=1):
        if row["key"] == key:
            return index
    return None


def leaderboard_rows(range_key="30d", sort_key="damage_out", *, sum_fields=SUM_FIELDS, now=None):
    """
    Roll up stored combat-log sessions.

    ``range_key`` is ``"7d"``, ``"30d"``, or anything else (treated as all time,
    matching the leaderboard's existing query-string behavior).
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
    )
    days = RANGE_DAYS.get(range_key)
    if days is not None:
        qs = qs.filter(uploaded_at__gte=now - timedelta(days=days))
    return rollup_sessions(qs, sort_key=sort_key, sum_fields=sum_fields)
