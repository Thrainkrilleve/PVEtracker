"""
Pilot Stats — fleet damage & logi tracking for Alliance Auth.

Models
------
General              Unmanaged meta-model that holds app-level permissions.
CombatLogUpload      One parsed EVE Gamelog upload, aggregated per pilot per session.
PilotSettings        Per-user live-feed visibility. Missing row means visible.
Payout               One ESI wallet-journal payout line. Nothing writes this yet.
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
            # Viewing the leaderboard, sessions, and events is any logged-in
            # member. This permission is the upload (and delete) gate.
            ("basic_access", "Can upload Pilot Stats combat logs"),
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
        related_name="pilotstats_uploads",
        verbose_name=_("character"),
        help_text=_("Character the combat log's 'Listener:' header identified."),
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="pilotstats_uploads",
        verbose_name=_("uploaded by"),
    )
    uploaded_at = models.DateTimeField(_("uploaded at"), auto_now_add=True)

    session_started = models.DateTimeField(_("session started"), null=True, blank=True)
    session_ended = models.DateTimeField(_("session ended"), null=True, blank=True)
    log_started = models.DateTimeField(
        _("log started"),
        null=True,
        blank=True,
        help_text=_(
            "The Gamelog header's 'Session Started:' time (UTC). With the character, "
            "this identifies one client log, so re-uploading a log that has grown "
            "updates the same session instead of adding a second one."
        ),
    )

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
        help_text=_(
            "Estimated: the outgoing-rep line pattern hasn't been checked against a "
            "real logi pilot's log yet (see Known limitations in the README)."
        ),
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

    ship_type_id = models.PositiveIntegerField(
        _("ship type id"),
        null=True,
        blank=True,
        help_text=_(
            "Hull type id for this session, for a later SDE name lookup. "
            "Empty until a source reports it."
        ),
    )

    class Meta:
        verbose_name = _("combat log upload")
        verbose_name_plural = _("combat log uploads")
        ordering = ["-uploaded_at"]
        # Index and constraint names keep their PvE Tracker-era "pvetracker_"
        # prefix on purpose: an upgraded install renames the tables in place
        # (migration 0001) and its existing indexes keep these names, so the
        # model state must match them. Don't rename them.
        constraints = [
            # Rows from before this field existed have no log_started; NULLs
            # don't collide in a unique constraint, so they're unaffected.
            models.UniqueConstraint(
                fields=["character", "log_started"],
                name="pvetracker_unique_character_log",
            ),
        ]
        indexes = [
            models.Index(fields=["session_started"], name="pvetracker_session_started"),
        ]

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


# ---------------------------------------------------------------------------
# Pilot settings
# ---------------------------------------------------------------------------


class PilotSettings(models.Model):
    """
    Per-user preferences.

    One checkbox: hide this user's sessions from the events feed. The
    leaderboard is unaffected, and a missing row means visible.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="pilotstats_settings",
        verbose_name=_("user"),
    )
    hide_from_live_feed = models.BooleanField(
        _("hide from the live feed"),
        default=False,
        help_text=_("Hide this user's sessions from the events feed. Leaderboard totals stay."),
    )

    class Meta:
        verbose_name = _("pilot settings")
        verbose_name_plural = _("pilot settings")

    def __str__(self) -> str:
        return f"{self.user} — hide_from_live_feed={self.hide_from_live_feed}"


# ---------------------------------------------------------------------------
# Payout (Phase 2 stub — no ingest yet)
# ---------------------------------------------------------------------------


class Payout(models.Model):
    """
    One kept ESI wallet-journal payout line.

    Stored fields are only character, time, type, amount, and the journal id
    used to dedupe. No raw journal body, balance, or tax line belongs here.
    Nothing in this version writes rows; the table is reserved for later.
    """

    class EntryType(models.TextChoices):
        BOUNTY = "bounty", _("Bounty")
        BOUNTY_PRIZE = "bounty_prize", _("Bounty prize")
        BOUNTY_PRIZES = "bounty_prizes", _("Bounty prizes")
        ESS_ESCROW_TRANSFER = "ess_escrow_transfer", _("ESS escrow")
        AGENT_MISSION_REWARD = "agent_mission_reward", _("Mission reward")
        AGENT_MISSION_TIME_BONUS_REWARD = (
            "agent_mission_time_bonus_reward",
            _("Mission time bonus"),
        )
        MISSION_REWARD = "mission_reward", _("Mission reward")

    character = models.ForeignKey(
        EveCharacter,
        on_delete=models.CASCADE,
        related_name="pilotstats_payouts",
        verbose_name=_("character"),
    )
    occurred_at = models.DateTimeField(_("time"))
    entry_type = models.CharField(_("type"), max_length=64, choices=EntryType.choices)
    amount = models.DecimalField(_("amount"), max_digits=20, decimal_places=2)
    ref_id = models.BigIntegerField(
        _("journal id"),
        unique=True,
        help_text=_("ESI wallet-journal id. Unique so the same line is not stored twice."),
    )

    class Meta:
        verbose_name = _("payout")
        verbose_name_plural = _("payouts")
        ordering = ["-occurred_at"]
        indexes = [
            # Explicit legacy name — see the note on CombatLogUpload.Meta.
            models.Index(fields=["character", "occurred_at"], name="pvetracker__charact_7a7d23_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.character_id} {self.entry_type} {self.amount} ({self.ref_id})"
