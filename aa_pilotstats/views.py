"""
Pilot Stats Views

leaderboard      — fleet standings, rolled up to each user's main character
upload           — drag-and-drop (or picker) upload of a combat log
session_detail   — the upload-result dashboard for one stored session
my_uploads       — a user's own upload history, with delete
delete_upload    — POST endpoint backing the delete button on my_uploads
events           — recent activity from stored sessions, plus the feed opt-out
"""

# Standard Library
import hashlib
import logging
from datetime import timezone as dt_timezone

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .forms import LiveFeedOptOutForm, LogUploadForm
from .leaderboard import DETAIL_FIELDS, leaderboard_rows, normalize_sort_key, rank_of, within_days
from .models import CombatLogUpload, PilotSettings
from .parsing import (
    ParseStats,
    iter_events_from_lines,
    parse_header_timestamp,
    read_header_from_lines,
    summarize,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # a full multi-hour session log is a few MB at most

# Events page caps. The per-session feed is already capped at 60; this keeps
# one long log from filling the shared list.
EVENTS_SESSION_LIMIT = 25
EVENTS_PER_SESSION = 8
EVENTS_ROW_LIMIT = 60


def _owned_characters(user) -> dict:
    """Map character_name -> EveCharacter for every character *user* owns."""
    return {
        co.character.character_name: co.character
        for co in user.character_ownerships.select_related("character")
    }


def _eve_time(dt):
    """Gamelog timestamps are EVE time, which is UTC — not the server's TIME_ZONE."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _can_delete(user, log_upload) -> bool:
    return user.has_perm("pilotstats.basic_access") and log_upload.uploaded_by_id == user.id


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@login_required
def leaderboard(request):
    range_key = request.GET.get("range", "30d")
    sort_key = normalize_sort_key(request.GET.get("sort", "damage_out"))
    rows = leaderboard_rows(range_key, sort_key)

    return render(request, "pilotstats/leaderboard.html", {
        "rows": rows,
        "range_key": range_key,
        "sort_key": sort_key,
        "ranges": [("7d", _("Last 7 days")), ("30d", _("Last 30 days")), ("all", _("All time"))],
        "sorts": [
            ("damage_out", _("Damage Dealt")),
            ("damage_in", _("Damage Received")),
            ("reps_received_total", _("Reps Received")),
            ("reps_given_total", _("Reps Given")),
        ],
    })


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def upload(request):
    result = None

    if request.method == "POST":
        form = LogUploadForm(request.POST, request.FILES)
        if form.is_valid():
            result = _handle_upload(request, form.cleaned_data["log_file"])
    else:
        form = LogUploadForm()

    return render(request, "pilotstats/upload.html", {"form": form, "result": result})


def _handle_upload(request, uploaded_file):
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        messages.error(request, _("That file is larger than a real combat log should be — rejected."))
        return None

    raw = uploaded_file.read()
    file_hash = hashlib.sha256(raw).hexdigest()

    if CombatLogUpload.objects.filter(file_hash=file_hash).exists():
        messages.warning(request, _("This exact file has already been uploaded."))
        return None

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    header = read_header_from_lines(lines)
    character_name = header.get("character")
    if not character_name:
        messages.error(request, _("Couldn't find a 'Listener:' line — is this really a Gamelog file?"))
        return None

    owned = _owned_characters(request.user)
    character = owned.get(character_name)
    if character is None:
        messages.error(
            request,
            _(
                "This log belongs to '%(name)s', which isn't a character on your Alliance Auth "
                "account. Make sure that character has been added (and its ownership verified) "
                "before uploading its logs."
            ) % {"name": character_name},
        )
        return None

    stats = ParseStats()
    events = list(iter_events_from_lines(lines, stats))
    if stats.combat_lines == 0:
        messages.warning(request, _("No (combat) channel lines found in this file — nothing to record."))
        return None

    summary = summarize(events)
    log_started = _eve_time(parse_header_timestamp(header.get("session_started")))

    values = dict(
        uploaded_by=request.user,
        session_started=_eve_time(summary["session_started"]),
        session_ended=_eve_time(summary["session_ended"]),
        source_filename=uploaded_file.name,
        file_hash=file_hash,
        damage_out=summary["damage_out"],
        damage_in=summary["damage_in"],
        shots_out=summary["shots_out"],
        shots_in=summary["shots_in"],
        reps_received_total=summary["reps_received_total"],
        reps_given_total=summary["reps_given_total"],
        quality_out=summary["quality_out"],
        quality_in=summary["quality_in"],
        top_targets=summary["top_targets"],
        top_attackers=summary["top_attackers"],
        reps_by_healer=summary["reps_by_healer"],
        reps_by_recipient=summary["reps_by_recipient"],
        feed=summary["feed"],
        combat_lines=stats.combat_lines,
        matched_lines=stats.matched,
    )

    try:
        with transaction.atomic():
            existing = None
            if log_started is not None:
                existing = (
                    CombatLogUpload.objects.select_for_update()
                    .filter(character=character, log_started=log_started)
                    .first()
                )
            if existing is None:
                log_upload = CombatLogUpload.objects.create(
                    character=character, log_started=log_started, **values
                )
                messages.success(
                    request,
                    _("Uploaded and parsed — %(n)d combat lines scored.") % {"n": stats.matched},
                )
                return log_upload
            return _update_existing_session(request, existing, values)
    except IntegrityError:
        # Two uploads of the same file (or the same log) raced each other; the
        # other one won. Same outcome as the duplicate checks above.
        messages.warning(request, _("This log has already been uploaded."))
        return None


def _update_existing_session(request, existing, values):
    """
    A log the server already has a session for (same character and header
    start). The EVE client appends to one file for the whole client session,
    so a longer copy replaces the stored totals instead of being counted twice.
    """
    if existing.uploaded_by_id != request.user.id:
        messages.error(
            request,
            _("Another Alliance Auth account already uploaded this character's log for that session."),
        )
        return None
    if values["combat_lines"] <= existing.combat_lines:
        messages.warning(
            request,
            _("This session is already stored, and this copy of the log has no new combat lines."),
        )
        return None

    added = values["combat_lines"] - existing.combat_lines
    for field, value in values.items():
        setattr(existing, field, value)
    existing.save()
    messages.success(
        request,
        _("Updated your existing session for this log — %(n)d new combat lines.") % {"n": added},
    )
    return existing


# ---------------------------------------------------------------------------
# Session detail
# ---------------------------------------------------------------------------


@login_required
def session_detail(request, pk):
    """Full session dashboard. Any logged-in member can open it."""
    session = get_object_or_404(
        CombatLogUpload.objects.select_related("character", "uploaded_by"),
        pk=pk,
    )
    return render(request, "pilotstats/session_detail.html", {
        "session": session,
        "can_delete": _can_delete(request.user, session),
    })


# ---------------------------------------------------------------------------
# My uploads
# ---------------------------------------------------------------------------


def _my_uploads_context(user, confirm_delete=None):
    uploads = (
        CombatLogUpload.objects.filter(uploaded_by=user)
        .select_related("character")
        .defer(*DETAIL_FIELDS)
    )
    rows = leaderboard_rows("30d", "damage_out")
    return {
        "uploads": uploads,
        "last_session": uploads.first(),
        "rank": rank_of(rows, user),
        "confirm_delete": confirm_delete,
    }


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def my_uploads(request):
    return render(request, "pilotstats/my_uploads.html", _my_uploads_context(request.user))


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def delete_upload(request, pk):
    log_upload = get_object_or_404(CombatLogUpload, pk=pk, uploaded_by=request.user)
    if request.method == "POST":
        log_upload.delete()
        messages.success(request, _("Upload deleted."))
        return redirect("pilotstats:my_uploads")
    return render(
        request,
        "pilotstats/my_uploads.html",
        _my_uploads_context(request.user, confirm_delete=log_upload),
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _feed_opt_out(user) -> bool:
    settings_obj = PilotSettings.objects.filter(user=user).only("hide_from_live_feed").first()
    return bool(settings_obj and settings_obj.hide_from_live_feed)


def _session_date(upload) -> str:
    """UTC date for feed rows stored before they carried one."""
    when = upload.session_started or upload.uploaded_at
    return when.astimezone(dt_timezone.utc).strftime("%Y-%m-%d") if when else ""


def _recent_feed_rows():
    """
    Most recent lines across the latest sessions, newest first by fight time.

    Sessions are picked by when they were flown (an old log uploaded today
    doesn't jump the queue), and lines are ordered by their own EVE date and
    time, so lines from different sessions and days interleave correctly.
    """
    hidden_ids = PilotSettings.objects.filter(hide_from_live_feed=True).values("user_id")
    uploads = (
        CombatLogUpload.objects.exclude(uploaded_by_id__in=hidden_ids)
        .select_related("character")
        .only(
            "feed", "session_started", "session_ended", "uploaded_at",
            "character__character_name",
        )
        .annotate(flown_until=Coalesce("session_ended", "uploaded_at"))
        .order_by("-flown_until", "-pk")[:EVENTS_SESSION_LIMIT]
    )
    rows = []
    for upload in uploads:
        fallback_date = _session_date(upload)
        for item in (upload.feed or [])[:EVENTS_PER_SESSION]:
            rows.append({
                "date": item.get("date") or fallback_date,
                "ts": item.get("ts", ""),
                "kind": item.get("kind", ""),
                "amount": item.get("amount", ""),
                "party": item.get("party", ""),
                "quality": item.get("quality", ""),
                "character_name": upload.character.character_name,
                "session_id": upload.pk,
            })
    rows.sort(key=lambda row: (row["date"], row["ts"]), reverse=True)
    return rows[:EVENTS_ROW_LIMIT]


@login_required
def events(request):
    if request.method == "POST":
        form = LiveFeedOptOutForm(request.POST)
        if form.is_valid():
            settings_obj, _created = PilotSettings.objects.get_or_create(user=request.user)
            settings_obj.hide_from_live_feed = form.cleaned_data["hide_from_live_feed"]
            settings_obj.save(update_fields=["hide_from_live_feed"])
            messages.success(request, _("Live-feed visibility saved."))
            return redirect("pilotstats:events")
    else:
        form = LiveFeedOptOutForm(
            initial={"hide_from_live_feed": _feed_opt_out(request.user)},
        )

    return render(request, "pilotstats/events.html", {
        "form": form,
        "feed_rows": _recent_feed_rows(),
    })


# ---------------------------------------------------------------------------
# Dashboard teaser
# ---------------------------------------------------------------------------


def pilotstats_dashboard(request) -> str:
    """
    Render the dashboard hook snippet.

    Returns an HTML string (not a full response) so Alliance Auth can embed
    it on the dashboard alongside other app widgets. Any logged-in member
    gets the widget; uploading stays behind basic_access.
    """
    if not request.user.is_authenticated:
        return ""

    user = request.user
    # Filter on the character row, not EveCharacter.character_id. On Auth 5
    # the EVE id is no longer the primary key, so comparing it to the FK
    # column misses every upload.
    owned = list(_owned_characters(user).values())
    totals = within_days(CombatLogUpload.objects.filter(character__in=owned), 7).aggregate(
        damage_out=Sum("damage_out"),
        reps_given=Sum("reps_given_total"),
        upload_count=Count("id"),
    )
    last = (
        CombatLogUpload.objects.filter(character__in=owned)
        .select_related("character")
        .defer(*DETAIL_FIELDS)
        .first()
    )
    rows = leaderboard_rows("7d", "damage_out")
    return render_to_string("pilotstats/dashboard_widget.html", {
        "damage_out": totals["damage_out"] or 0,
        "reps_given": totals["reps_given"] or 0,
        "upload_count": totals["upload_count"],
        "last_session": last,
        "rank": rank_of(rows, user),
        "can_upload": user.has_perm("pilotstats.basic_access"),
    }, request=request)
