"""
PvE Tracker Views

upload           — drag-and-drop (or picker) upload of a combat log; parses,
                    attributes to a character the uploader owns, stores it
leaderboard      — fleet standings, rolled up to each user's main character
my_uploads       — a user's own upload history, with delete
delete_upload    — POST endpoint backing the delete button on my_uploads
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

from .forms import LogUploadForm
from .models import CombatLogUpload
from .parsing import ParseStats, iter_events_from_lines, read_header_from_lines, summarize

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # a full multi-hour session log is a few MB at most

SORT_FIELDS = {
    "damage_out": "damage_out",
    "damage_in": "damage_in",
    "reps_received_total": "reps_received_total",
    "reps_given_total": "reps_given_total",
}
RANGE_DAYS = {"7d": 7, "30d": 30}


def _owned_characters(user) -> dict:
    """Map character_name -> EveCharacter for every character *user* owns."""
    return {
        co.character.character_name: co.character
        for co in user.character_ownerships.select_related("character")
    }


def _main_character(user):
    """Null-safe lookup of a user's designated main, same pattern used across
    Alliance Auth apps (e.g. aa-pipeline's views.py)."""
    return getattr(getattr(user, "profile", None), "main_character", None)


def _make_aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


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
# Leaderboard
# ---------------------------------------------------------------------------


@login_required
@permission_required("pvetracker.basic_access", raise_exception=True)
def leaderboard(request):
    range_key = request.GET.get("range", "30d")
    sort_key = request.GET.get("sort", "damage_out")
    if sort_key not in SORT_FIELDS:
        sort_key = "damage_out"

    qs = CombatLogUpload.objects.select_related(
        "character", "uploaded_by", "uploaded_by__profile", "uploaded_by__profile__main_character"
    )
    if range_key in RANGE_DAYS:
        cutoff = timezone.now() - timedelta(days=RANGE_DAYS[range_key])
        qs = qs.filter(uploaded_at__gte=cutoff)

    # Main-character roll-up can't be a single ORM .annotate() — main_character
    # is resolved per-user via the profile relation, not a joinable field on
    # the upload itself — so bucket in Python instead.
    buckets: dict = {}
    for row in qs:
        main = _main_character(row.uploaded_by)
        key = main.character_id if main else f"user:{row.uploaded_by_id}"
        label = main.character_name if main else row.uploaded_by.username
        bucket = buckets.setdefault(key, {
            "label": label,
            "main": main,
            "damage_out": 0,
            "damage_in": 0,
            "reps_received_total": 0,
            "reps_given_total": 0,
            "by_character": {},
        })
        bucket["damage_out"] += row.damage_out
        bucket["damage_in"] += row.damage_in
        bucket["reps_received_total"] += row.reps_received_total
        bucket["reps_given_total"] += row.reps_given_total

        char_bucket = bucket["by_character"].setdefault(row.character.character_name, {
            "damage_out": 0, "damage_in": 0, "reps_received_total": 0, "reps_given_total": 0,
        })
        char_bucket["damage_out"] += row.damage_out
        char_bucket["damage_in"] += row.damage_in
        char_bucket["reps_received_total"] += row.reps_received_total
        char_bucket["reps_given_total"] += row.reps_given_total

    rows = list(buckets.values())
    rows.sort(key=lambda r: r[SORT_FIELDS[sort_key]], reverse=True)
    for row in rows:
        row["by_character"] = sorted(
            row["by_character"].items(), key=lambda kv: kv[1][sort_key], reverse=True
        )

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
# My uploads
# ---------------------------------------------------------------------------


@login_required
@permission_required("pvetracker.basic_access", raise_exception=True)
def my_uploads(request):
    uploads = CombatLogUpload.objects.filter(uploaded_by=request.user).select_related("character")
    return render(request, "pvetracker/my_uploads.html", {"uploads": uploads})


@login_required
@permission_required("pvetracker.basic_access", raise_exception=True)
def delete_upload(request, pk):
    log_upload = get_object_or_404(CombatLogUpload, pk=pk, uploaded_by=request.user)
    if request.method == "POST":
        log_upload.delete()
        messages.success(request, _("Upload deleted."))
        return redirect("pvetracker:my_uploads")
    return render(request, "pvetracker/my_uploads.html", {
        "uploads": CombatLogUpload.objects.filter(uploaded_by=request.user).select_related("character"),
        "confirm_delete": log_upload,
    })


# ---------------------------------------------------------------------------
# Dashboard teaser
# ---------------------------------------------------------------------------


def pvetracker_dashboard(request) -> str:
    """
    Render the dashboard hook snippet.

    Returns an HTML string (not a full response) so Alliance Auth can embed
    it on the dashboard alongside other app widgets.
    """
    if not request.user.is_authenticated:
        return ""
    if not request.user.has_perm("pvetracker.basic_access"):
        return ""

    cutoff = timezone.now() - timedelta(days=7)
    owned_ids = [c.character_id for c in _owned_characters(request.user).values()]
    uploads = CombatLogUpload.objects.filter(character_id__in=owned_ids, uploaded_at__gte=cutoff)
    return render_to_string("pvetracker/dashboard_widget.html", {
        "damage_out": sum(u.damage_out for u in uploads),
        "reps_given": sum(u.reps_given_total for u in uploads),
        "upload_count": uploads.count(),
    }, request=request)
