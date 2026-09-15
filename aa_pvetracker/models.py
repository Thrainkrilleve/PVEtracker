"""
PvE Tracker — fleet damage & logi tracking for Alliance Auth.

Models
------
General             Unmanaged meta-model that holds app-level permissions.
CombatLogUpload      One parsed EVE Gamelog upload, aggregated per pilot per session.
"""

# Django
from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter


# ---------------------------------------------------------------------------
# Permissions meta-model
# ---------------------------------------------------------------------------


class General(models.Model):
    """Unmanaged model used solely to hold app-level permissions."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access PvE Tracker"),
        )


# ---------------------------------------------------------------------------
# CombatLogUpload
# ---------------------------------------------------------------------------


class CombatLogUpload(models.Model):
    """
    One EVE Gamelog combat-log file, parsed and aggregated.

    Only aggregated per-upload totals are stored — not individual combat
    events. Event-level detail only matters while a fight is live; for
    history and leaderboards, the per-session summary is all that's needed.
    """

    character = models.ForeignKey(
        EveCharacter,
        on_delete=models.CASCADE,
        related_name="pvetracker_uploads",
        verbose_name=_("character"),
        help_text=_("Character the combat log's 'Listener:' header identified."),
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="pvetracker_uploads",
        verbose_name=_("uploaded by"),
    )
    uploaded_at = models.DateTimeField(_("uploaded at"), auto_now_add=True)

    session_started = models.DateTimeField(_("session started"), null=True, blank=True)
    session_ended = models.DateTimeField(_("session ended"), null=True, blank=True)

    source_filename = models.CharField(_("source filename"), max_length=255)
    file_hash = models.CharField(
        _("file hash"),
        max_length=64,
        unique=True,
        help_text=_("SHA-256 of the raw upload — rejects accidental duplicate re-uploads."),
    )

    # ---- Aggregate totals -----------------------------------------------------

    damage_out = models.PositiveIntegerField(_("damage dealt"), default=0)
    damage_in = models.PositiveIntegerField(_("damage received"), default=0)
    shots_out = models.PositiveIntegerField(_("shots fired"), default=0)
    shots_in = models.PositiveIntegerField(_("shots received"), default=0)
    reps_received_total = models.PositiveIntegerField(_("reps received"), default=0)
    reps_given_total = models.PositiveIntegerField(
        _("reps given"),
        default=0,
        help_text=_("Estimated — see PVETRACKER_REMOTE_REP_OUT_UNVERIFIED in the docs."),
    )

    # ---- Breakdowns (same shape the local prototype's live dashboard used) ---

    quality_out = models.JSONField(_("hit quality — outgoing"), default=list, blank=True)
    quality_in = models.JSONField(_("hit quality — incoming"), default=list, blank=True)
    top_targets = models.JSONField(_("top targets"), default=list, blank=True)
    top_attackers = models.JSONField(_("top attackers"), default=list, blank=True)
    reps_by_healer = models.JSONField(_("reps by healer"), default=list, blank=True)
    reps_by_recipient = models.JSONField(_("reps by recipient"), default=list, blank=True)
    feed = models.JSONField(
        _("recent activity"), default=list, blank=True,
        help_text=_("Last events of the session (most recent first) — same shape as the "
                    "standalone prototype's live feed, capped so this stays small."),
    )

    # ---- Parse-coverage sanity numbers -----------------------------------------

    combat_lines = models.PositiveIntegerField(
        _("combat lines seen"), default=0,
        help_text=_("Total (combat) channel lines found in the uploaded file."),
    )
    matched_lines = models.PositiveIntegerField(
        _("combat lines scored"), default=0,
        help_text=_("Of the combat lines seen, how many matched a known event pattern."),
    )

    class Meta:
        verbose_name = _("combat log upload")
        verbose_name_plural = _("combat log uploads")
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.character.character_name} — {self.source_filename} ({self.uploaded_at:%Y-%m-%d %H:%M})"

    @property
    def duration_seconds(self) -> float:
        if not self.session_started or not self.session_ended:
            return 0.0
        return max((self.session_ended - self.session_started).total_seconds(), 1.0)

    @property
    def dps_out(self) -> float:
        return self.damage_out / self.duration_seconds if self.duration_seconds else 0.0

    @property
    def dps_in(self) -> float:
        return self.damage_in / self.duration_seconds if self.duration_seconds else 0.0

    @property
    def duration_display(self) -> str:
        total = int(self.duration_seconds)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
