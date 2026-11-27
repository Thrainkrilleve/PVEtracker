"""
PvE Tracker Views

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
from datetime import timedelta

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .forms import LiveFeedOptOutForm, LogUploadForm
from .leaderboard import leaderboard_rows, normalize_sort_key, rank_of
from .models import CombatLogUpload, PilotSettings
from .parsing import ParseStats, iter_events_from_lines, read_header_from_lines, summarize

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


def _make_aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _can_delete(user, log_upload) -> bool:
    return user.has_perm("pvetracker.basic_access") and log_upload.uploaded_by_id == user.id


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@login_required
def leaderboard(request):
    range_key = request.GET.get("range", "30d")
    sort_key = normalize_sort_key(request.GET.get("sort", "damage_out"))
    rows = leaderboard_rows(range_key, sort_key)

    return render(request, "pvetracker/leaderboard.html", {
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
@permission_required("pvetracker.basic_access", raise_exception=True)
def upload(request):
    result = None

    if request.method == "POST":
        form = LogUploadForm(request.POST, request.FILES)
        if form.is_valid():
            result = _handle_upload(request, form.cleaned_data["log_file"])
    else:
        form = LogUploadForm()

    return render(request, "pvetracker/upload.html", {"form": form, "result": result})


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

    log_upload = CombatLogUpload.objects.create(
        character=character,
        uploaded_by=request.user,
        session_started=_make_aware(summary["session_started"]),
        session_ended=_make_aware(summary["session_ended"]),
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
    messages.success(request, _("Uploaded and parsed — %(n)d combat lines scored.") % {"n": stats.matched})
    return log_upload


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
    return render(request, "pvetracker/session_detail.html", {
        "session": session,
        "can_delete": _can_delete(request.user, session),
    })


# ---------------------------------------------------------------------------
# My uploads
# ---------------------------------------------------------------------------


def _my_uploads_context(user, confirm_delete=None):
    uploads = CombatLogUpload.objects.filter(uploaded_by=user).select_related("character")
    rows = leaderboard_rows("30d", "damage_out")
    return {
        "uploads": uploads,
        "last_session": uploads.first(),
        "rank": rank_of(rows, user),
        "confirm_delete": confirm_delete,
    }


@login_required
@permission_required("pvetracker.basic_access", raise_exception=True)
def my_uploads(request):
    return render(request, "pvetracker/my_uploads.html", _my_uploads_context(request.user))


@login_required
@permission_required("pvetracker.basic_access", raise_exception=True)
def delete_upload(request, pk):
    log_upload = get_object_or_404(CombatLogUpload, pk=pk, uploaded_by=request.user)
    if request.method == "POST":
        log_upload.delete()
        messages.success(request, _("Upload deleted."))
        return redirect("pvetracker:my_uploads")
    return render(
        request,
        "pvetracker/my_uploads.html",
        _my_uploads_context(request.user, confirm_delete=log_upload),
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _feed_opt_out(user) -> bool:
    settings_obj = PilotSettings.objects.filter(user=user).only("hide_from_live_feed").first()
    return bool(settings_obj and settings_obj.hide_from_live_feed)


def _recent_feed_rows():
    hidden_ids = PilotSettings.objects.filter(hide_from_live_feed=True).values("user_id")
    uploads = (
        CombatLogUpload.objects.exclude(uploaded_by_id__in=hidden_ids)
        .select_related("character")
        .order_by("-uploaded_at")[:EVENTS_SESSION_LIMIT]
    )
    rows = []
    for upload in uploads:
        for item in (upload.feed or [])[:EVENTS_PER_SESSION]:
            rows.append({
                "ts": item.get("ts", ""),
                "kind": item.get("kind", ""),
                "amount": item.get("amount", ""),
                "party": item.get("party", ""),
                "quality": item.get("quality", ""),
                "character_name": upload.character.character_name,
                "session_id": upload.pk,
            })
            if len(rows) >= EVENTS_ROW_LIMIT:
                return rows
    return rows


@login_required
def events(request):
    if request.method == "POST":
        form = LiveFeedOptOutForm(request.POST)
        if form.is_valid():
            settings_obj, _created = PilotSettings.objects.get_or_create(user=request.user)
            settings_obj.hide_from_live_feed = form.cleaned_data["hide_from_live_feed"]
            settings_obj.save(update_fields=["hide_from_live_feed"])
            messages.success(request, _("Live-feed visibility saved."))
            return redirect("pvetracker:events")
    else:
        form = LiveFeedOptOutForm(
            initial={"hide_from_live_feed": _feed_opt_out(request.user)},
        )

    return render(request, "pvetracker/events.html", {
        "form": form,
        "feed_rows": _recent_feed_rows(),
    })


# ---------------------------------------------------------------------------
# Dashboard teaser
# ---------------------------------------------------------------------------


def pvetracker_dashboard(request) -> str:
    """
    Render the dashboard hook snippet.

    Returns an HTML string (not a full response) so Alliance Auth can embed
    it on the dashboard alongside other app widgets. Any logged-in member
    gets the widget; uploading stays behind basic_access.
    """
    if not request.user.is_authenticated:
        return ""

    user = request.user
    cutoff = timezone.now() - timedelta(days=7)
    # Filter on the character row, not EveCharacter.character_id. On Auth 5
    # the EVE id is no longer the primary key, so comparing it to the FK
    # column misses every upload.
    owned = list(_owned_characters(user).values())
    recent = CombatLogUpload.objects.filter(character__in=owned, uploaded_at__gte=cutoff)
    last = (
        CombatLogUpload.objects.filter(character__in=owned)
        .select_related("character")
        .first()
    )
    rows = leaderboard_rows("7d", "damage_out")
    return render_to_string("pvetracker/dashboard_widget.html", {
        "damage_out": sum(u.damage_out for u in recent),
        "reps_given": sum(u.reps_given_total for u in recent),
        "upload_count": recent.count(),
        "last_session": last,
        "rank": rank_of(rows, user),
        "can_upload": user.has_perm("pvetracker.basic_access"),
    }, request=request)
