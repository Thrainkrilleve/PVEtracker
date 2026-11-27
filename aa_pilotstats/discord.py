"""
Discord webhook feed: what gets posted, and in what shape.

Two kinds of post, built from the same roll-up as the site:

- the **daily top 10** (damage and payout ISK over the last 24 hours, plus
  kill/loss highlights), once a day at the configured time;
- **major events**: a single payout line or a member's kill/loss at or over
  its threshold, and new session-damage / session-DPS / 24h-payout records.

Every post has a unique key (``daily:2026-11-27``, ``killmail:123``, …) so it
happens at most once, and event posts are capped per hour. Posts carry
names and numbers only: the channel may be visible to non-members.
Sending happens in a Celery task, never inside a web request.
"""

# Standard Library
import logging
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

# Django
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import typenames
from .leaderboard import DETAIL_FIELDS, character_buckets, rollup_sessions
from .models import (
    CombatLogUpload,
    DiscordPost,
    DiscordSettings,
    Killmail,
    Payout,
    PayoutDaily,
)
from .templatetags.pilotstats import isk

logger = logging.getLogger(__name__)

# Discord limits: https://discord.com/developers/docs/resources/message#embed-object-embed-limits
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_LIMIT = 25
FIELD_VALUE_LIMIT = 1024
MESSAGE_EMBED_TOTAL = 6000
MESSAGE_EMBED_COUNT = 10

TOP_N = 10
# Records only count sessions at least this long, so a 30-second burst
# can't set a "DPS record"; personal records need some history first.
RECORD_MIN_SECONDS = 10 * 60
PERSONAL_RECORD_MIN_SESSIONS = 5

COLOR_DAILY = 0x3B82F6
COLOR_PAYOUT = 0xF59E0B
COLOR_KILL = 0x10B981
COLOR_LOSS = 0xEF4444
COLOR_RECORD = 0x8B5CF6


# ---- Embeds -----------------------------------------------------------------


def _cut(text, limit) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def embed(title, description="", *, color=COLOR_DAILY, fields=(), footer=None, url=None) -> dict:
    """One embed, every part clipped to Discord's limits."""
    data = {
        "title": _cut(title, TITLE_LIMIT),
        "description": _cut(description, DESCRIPTION_LIMIT),
        "color": color,
    }
    if fields:
        data["fields"] = [
            {"name": _cut(name, TITLE_LIMIT), "value": _cut(value, FIELD_VALUE_LIMIT) or "—", "inline": inline}
            for name, value, inline in list(fields)[:FIELD_LIMIT]
        ]
    if footer:
        data["footer"] = {"text": _cut(footer, 2048)}
    if url:
        data["url"] = url
    return data


def embed_size(data: dict) -> int:
    size = len(data.get("title", "")) + len(data.get("description", ""))
    size += sum(len(f["name"]) + len(f["value"]) for f in data.get("fields", []))
    size += len((data.get("footer") or {}).get("text", ""))
    return size


def pack_messages(embeds) -> list:
    """Group embeds into messages under Discord's per-message limits (6000 chars, 10 embeds)."""
    messages, current, total = [], [], 0
    for data in embeds:
        size = embed_size(data)
        if size > MESSAGE_EMBED_TOTAL:
            # A single embed can't exceed this after clipping, but be safe.
            data = dict(data, description=_cut(data.get("description", ""), MESSAGE_EMBED_TOTAL // 2))
            size = embed_size(data)
        if current and (total + size > MESSAGE_EMBED_TOTAL or len(current) >= MESSAGE_EMBED_COUNT):
            messages.append(current)
            current, total = [], 0
        current.append(data)
        total += size
    if current:
        messages.append(current)
    return messages


def payload(embeds) -> dict:
    return {
        "username": "Pilot Stats",
        "embeds": embeds,
        "allowed_mentions": {"parse": []},  # never ping anyone
    }


def _site_link(path="") -> str:
    base = getattr(settings, "SITE_URL", "") or ""
    return f"{base.rstrip('/')}/pilotstats/{path}" if base.startswith("http") else ""


# ---- Queueing (dedupe + hourly cap) -------------------------------------------


EVENT_KINDS = (DiscordPost.Kind.PAYOUT, DiscordPost.Kind.KILLMAIL, DiscordPost.Kind.RECORD)


def queue(key, kind, embeds, *, detail="", config=None, now=None) -> DiscordPost | None:
    """Record and dispatch a post once per key. Event posts past the hourly cap are skipped."""
    config = config or DiscordSettings.get()
    if not config.is_active:
        return None
    now = now or timezone.now()
    try:
        with transaction.atomic():
            post = DiscordPost.objects.create(key=key[:128], kind=kind, detail=_cut(detail, 255))
    except IntegrityError:
        return None  # already posted (or skipped) once
    if kind in EVENT_KINDS:
        recent = DiscordPost.objects.filter(
            kind__in=EVENT_KINDS,
            status__in=[DiscordPost.Status.QUEUED, DiscordPost.Status.SENT],
            created_at__gte=now - timedelta(hours=1),
        ).exclude(pk=post.pk).count()
        if recent >= config.max_posts_per_hour:
            post.status = DiscordPost.Status.SKIPPED
            post.save(update_fields=["status"])
            return post

    from .tasks import send_discord_post  # avoid an import cycle

    for message in pack_messages(embeds):
        send_discord_post.delay(post.pk, payload(message))
    return post


# ---- Daily top 10 -------------------------------------------------------------


def daily_due(config, now=None):
    """The local date the daily post is due for, or None if not yet / already done."""
    now = now or timezone.now()
    local = now.astimezone(ZoneInfo(config.timezone))
    if local.time() < config.daily_post_time:
        return None
    if DiscordPost.objects.filter(key=f"daily:{local.date().isoformat()}").exists():
        return None
    return local.date()


def daily_embeds(now=None) -> list:
    """Top 10 for damage and payout ISK over the last 24 hours, plus kill/loss highlights."""
    now = now or timezone.now()
    since = now - timedelta(hours=24)

    # Damage: sessions flown in the window, rolled up per main like the site.
    sessions = within(
        CombatLogUpload.objects.select_related(
            "character", "uploaded_by", "uploaded_by__profile", "uploaded_by__profile__main_character",
        ).defer(*DETAIL_FIELDS),
        since, now,
    )
    damage = [
        (row["label"], row["damage_out"])
        for row in rollup_sessions(sessions, sort_key="damage_out") if row["damage_out"]
    ][:TOP_N]

    # Payouts: itemized lines (exact 24h), not the per-day totals.
    per_character = (
        Payout.objects.filter(occurred_at__gte=since, occurred_at__lte=now)
        .values("character_id").annotate(total=Sum("amount"))
    )
    buckets = character_buckets({row["character_id"] for row in per_character})
    per_main: dict = {}
    for row in per_character:
        _key, label, _main, _uid = buckets[row["character_id"]]
        per_main[_key] = (label, per_main.get(_key, (label, Decimal(0)))[1] + (row["total"] or 0))
    payout = sorted(per_main.values(), key=lambda lt: lt[1], reverse=True)[:TOP_N]

    def ranked(items, shown):
        return "\n".join(
            f"**{i}.** {_cut(label, 64)} — {shown(value)}" for i, (label, value) in enumerate(items, start=1)
        )

    embeds = [embed(
        "Top 10 — damage dealt (24h)",
        ranked(damage, lambda v: f"{int(v):,}") or "No combat logs flown in the last 24 hours.",
        color=COLOR_DAILY,
        url=_site_link() or None,
        footer="From uploaded combat logs. Payouts and kills are separate numbers.",
    )]
    if payout:
        embeds.append(embed(
            "Top 10 — payout ISK (24h)",
            ranked(payout, isk),
            color=COLOR_PAYOUT,
            footer="Bounty, ESS and mission payouts from connected wallets.",
        ))

    kills = (
        Killmail.objects.filter(killmail_time__gte=since, participants__is_victim=False)
        .distinct().order_by("-total_value")[:5]
    )
    losses = (
        Killmail.objects.filter(killmail_time__gte=since, participants__is_victim=True)
        .distinct().order_by("-total_value")[:5]
    )
    if kills or losses:
        names = typenames.names(
            [k.victim_ship_type_id for k in kills] + [k.victim_ship_type_id for k in losses]
        )
        fields = []
        if kills:
            fields.append(("Biggest kills", "\n".join(
                f"[{typenames.label(k.victim_ship_type_id, names)}]({k.zkill_url}) — {isk(k.total_value)}"
                for k in kills
            ), False))
        if losses:
            fields.append(("Biggest losses", "\n".join(
                f"[{typenames.label(k.victim_ship_type_id, names)}]({k.zkill_url}) — {isk(k.total_value)}"
                for k in losses
            ), False))
        embeds.append(embed("Kills and losses (24h)", color=COLOR_KILL, fields=fields))
    return embeds


def post_daily(config=None, now=None) -> DiscordPost | None:
    config = config or DiscordSettings.get()
    if not config.is_active:
        return None
    due = daily_due(config, now)
    if due is None:
        return None
    post = queue(f"daily:{due.isoformat()}", DiscordPost.Kind.DAILY, daily_embeds(now), config=config, now=now)
    if config.post_records:
        check_payout_records(config, now=now)
    return post


# ---- Major events --------------------------------------------------------------


def _owner_label(character_pk) -> str:
    return character_buckets([character_pk]).get(character_pk, (None, "Unknown pilot"))[1]


def check_payouts(character, config=None, now=None) -> int:
    """Post this character's recent payout lines at or over the threshold (each once)."""
    config = config or DiscordSettings.get()
    if not config.is_active:
        return 0
    now = now or timezone.now()
    big = Payout.objects.filter(
        character=character,
        amount__gte=config.payout_threshold_isk,
        occurred_at__gte=now - timedelta(hours=26),
    )
    labels = dict(Payout.EntryType.choices)
    posted = 0
    for payout in big:
        who = _owner_label(character.pk)
        posted += bool(queue(
            f"payout:{payout.ref_id}", DiscordPost.Kind.PAYOUT,
            [embed(
                f"{isk(payout.amount)} payout",
                f"**{_cut(who, 64)}** received a {labels.get(payout.entry_type, payout.entry_type).lower()}.",
                color=COLOR_PAYOUT,
                footer="Wallet payouts arrive up to an hour late (ESI cache).",
            )],
            detail=f"{payout.entry_type} {payout.amount}", config=config, now=now,
        ))
    return posted


def check_killmail(killmail: Killmail, config=None, now=None) -> bool:
    """Post a member's kill or loss at or over the threshold (once per mail)."""
    config = config or DiscordSettings.get()
    if not config.is_active or killmail.total_value < config.kill_threshold_isk:
        return False
    participants = list(killmail.participants.select_related("character"))
    if not participants:
        return False
    victims = [p for p in participants if p.is_victim]
    hull = typenames.label(killmail.victim_ship_type_id, typenames.names([killmail.victim_ship_type_id]))
    if victims:
        who = _owner_label(victims[0].character_id)
        title, color = f"Loss: {hull} — {isk(killmail.total_value)}", COLOR_LOSS
        text = f"**{_cut(who, 64)}** lost a {hull}."
    else:
        mains = sorted({_owner_label(p.character_id) for p in participants})
        title, color = f"Kill: {hull} — {isk(killmail.total_value)}", COLOR_KILL
        text = "On the kill: " + ", ".join(f"**{_cut(m, 48)}**" for m in mains[:10])
    return bool(queue(
        f"killmail:{killmail.killmail_id}", DiscordPost.Kind.KILLMAIL,
        [embed(title, text, color=color, url=killmail.zkill_url)],
        detail=title, config=config, now=now,
    ))


# ---- Records -------------------------------------------------------------------


def _dps(upload) -> float:
    return upload.dps_out if upload.duration_seconds >= RECORD_MIN_SECONDS else 0.0


def check_session_records(session: CombatLogUpload, config=None, now=None) -> list:
    """Post new fleet / personal records for session damage and session DPS. Each once per session."""
    config = config or DiscordSettings.get()
    if not config.is_active or not config.post_records:
        return []
    if session.duration_seconds < RECORD_MIN_SECONDS or not session.damage_out:
        return []
    others = CombatLogUpload.objects.exclude(pk=session.pk).filter(
        session_started__isnull=False, session_ended__isnull=False,
    ).only("damage_out", "session_started", "session_ended", "uploaded_by_id")
    who = _owner_label(session.character_id)
    posted = []

    def best(qs, metric):
        values = [metric(u) for u in qs]
        return max(values) if values else None

    mine = others.filter(uploaded_by_id=session.uploaded_by_id)
    checks = (
        ("damage", "session damage", lambda u: u.damage_out, f"{session.damage_out:,} damage"),
        ("dps", "session DPS", _dps, f"{_dps(session):,.0f} DPS"),
    )
    for kind, label, metric, shown in checks:
        value = metric(session)
        if not value:
            continue
        fleet_best = best(others, metric)
        if fleet_best is not None and value > fleet_best:
            scope, previous = "Fleet record", fleet_best
        elif mine.count() >= PERSONAL_RECORD_MIN_SESSIONS and value > (best(mine, metric) or 0):
            scope, previous = "Personal record", best(mine, metric)
        else:
            continue
        previous_text = f"{previous:,.0f}" if isinstance(previous, float) else f"{previous:,}"
        if queue(
            f"record:session:{session.pk}:{kind}", DiscordPost.Kind.RECORD,
            [embed(
                f"{scope}: {label}",
                f"**{_cut(who, 64)}** — {shown} (previous best {previous_text}).",
                color=COLOR_RECORD,
                url=_site_link(f"sessions/{session.pk}/") or None,
            )],
            detail=f"{scope} {label} {value}", config=config, now=now,
        ):
            posted.append(kind)
    return posted


def check_payout_records(config=None, now=None) -> list:
    """After the daily post: did any main set a new best 24h payout total yesterday?"""
    config = config or DiscordSettings.get()
    if not config.is_active or not config.post_records:
        return []
    now = now or timezone.now()
    day = (now - timedelta(days=1)).date()
    per_character = PayoutDaily.objects.values("character_id", "day").annotate(total=Sum("amount"))
    buckets = character_buckets({row["character_id"] for row in per_character})
    by_main_day: dict = {}
    for row in per_character:
        key, label, _main, _uid = buckets[row["character_id"]]
        entry = by_main_day.setdefault((key, row["day"]), [label, Decimal(0)])
        entry[1] += row["total"] or 0
    if not any(d == day for _k, d in by_main_day):
        return []
    earlier_best = max((t for (_k, d), (_l, t) in by_main_day.items() if d < day), default=None)
    day_rows = sorted(
        ((label, total) for (key, d), (label, total) in by_main_day.items() if d == day),
        key=lambda lt: lt[1], reverse=True,
    )
    label, total = day_rows[0]
    if earlier_best is None or total <= earlier_best:
        return []
    queue(
        f"record:payout-day:{day.isoformat()}", DiscordPost.Kind.RECORD,
        [embed(
            "Fleet record: payouts in one day",
            f"**{_cut(label, 64)}** — {isk(total)} on {day.isoformat()} (previous best {isk(earlier_best)}).",
            color=COLOR_RECORD,
        )],
        detail=f"payout day {day} {total}", config=config, now=now,
    )
    return [label]


def test_message(config=None) -> DiscordPost | None:
    now = timezone.now()
    return queue(
        f"test:{now.isoformat()}", DiscordPost.Kind.TEST,
        [embed("Pilot Stats test message", "The webhook works. Daily top 10 and big events will post here.")],
        config=config, now=now,
    )


def within(qs, since, until):
    """Sessions flown between *since* and *until* (fight time, falling back to upload time)."""
    return qs.annotate(flown_at=Coalesce("session_started", "uploaded_at")).filter(
        flown_at__gte=since, flown_at__lte=until,
    )
