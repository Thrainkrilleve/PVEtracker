"""
Pilot Stats — fleet damage & logi tracking for Alliance Auth.

Models
------
General              Unmanaged meta-model that holds app-level permissions.
CombatLogUpload      One parsed EVE Gamelog upload, aggregated per pilot per session.
PilotSettings        Per-user live-feed visibility. Missing row means visible.
Payout               One ESI wallet-journal payout line. Nothing writes this yet.
"""

# Standard Library
import datetime
import re
from decimal import Decimal

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

# Sessions shorter than this get no ISK per hour: a few minutes around one
# bounty tick would give a wildly inflated rate.
ISK_RATE_MIN_SECONDS = 10 * 60


class CombatLogUpload(models.Model):
    """
    One EVE Gamelog combat-log file, parsed and aggregated.

    Only aggregated per-upload totals are stored — not individual combat
    events. Event-level detail only matters while a fight is live; for
    history and leaderboards, the per-session summary is all that's needed.
    """

    class Activity(models.TextChoices):
        RATTING = "ratting", _("Ratting")
        INCURSION = "incursion", _("Incursion")
        ABYSSAL = "abyssal", _("Abyssal")
        MISSION = "mission", _("Missions")
        OTHER = "other", _("Other")


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
    timeline = models.JSONField(
        _("timeline"),
        default=dict,
        blank=True,
        help_text=_(
            "Damage and reps per time bucket for the session chart (parsing.build_timeline). "
            "Empty for sessions uploaded before charts existed."
        ),
    )
    activity = models.CharField(
        _("activity"),
        max_length=16,
        choices=Activity.choices,
        blank=True,
        default="",
        help_text=_(
            "Ratting, incursion, abyssal or missions, from the wallet and the NPCs shot "
            "(see activity.classify). Empty when there's no telling."
        ),
    )
    payout_isk = models.DecimalField(
        _("payout ISK earned"),
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Bounty and mission payouts from the session's start until shortly after its end "
            "(see payouts.match_sessions). Empty when the character's wallet isn't connected."
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
    def isk_per_hour(self):
        """Payout ISK per hour of this session, or None (no wallet data, or too short to mean much)."""
        if self.payout_isk is None or self.duration_seconds < ISK_RATE_MIN_SECONDS:
            return None
        return self.payout_isk * 3600 / Decimal(str(self.duration_seconds))

    @property
    def duration_display(self) -> str:
        total = int(self.duration_seconds)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# Pilot settings
# ---------------------------------------------------------------------------


class PilotSettings(models.Model):
    """
    Per-user preferences. A missing row means the defaults (visible, wallet
    read when a token allows it).
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
    wallet_opt_out = models.BooleanField(
        _("don't read my wallet"),
        default=False,
        help_text=_(
            "Never read this user's wallet journal, even with a token that allows it. "
            "Turning it on also deletes the payouts and rat counts already stored."
        ),
    )

    class Meta:
        verbose_name = _("pilot settings")
        verbose_name_plural = _("pilot settings")

    def __str__(self) -> str:
        return f"{self.user} — hide_from_live_feed={self.hide_from_live_feed}"


# ---------------------------------------------------------------------------
# Wallet payouts (ESI wallet journal)
# ---------------------------------------------------------------------------


class Payout(models.Model):
    """
    One kept ESI wallet-journal payout line, kept for
    PILOTSTATS_PAYOUT_RETENTION_DAYS (just past the journal's 30-day window,
    so the journal id still dedupes). Long-term totals live in PayoutDaily.

    Stored: character, time, type, amount, the journal id, and for
    ``bounty_prizes`` lines the NPC kill counts derived from the line's
    reason. Never stored: the raw reason or description, balance, tax, the
    other party, or any non-payout or ``*_tax`` line.
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
    npc_kills = models.JSONField(
        _("NPC kills"),
        default=dict,
        blank=True,
        help_text=_("For bounty_prizes lines: {npc type id: count}, derived from the line."),
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


class PayoutDaily(models.Model):
    """Per character, day (UTC) and payout type: the long-lived ISK total."""

    character = models.ForeignKey(
        EveCharacter, on_delete=models.CASCADE, related_name="pilotstats_payout_days",
    )
    day = models.DateField()
    entry_type = models.CharField(max_length=64, choices=Payout.EntryType.choices)
    amount = models.DecimalField(max_digits=22, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("daily payout total")
        verbose_name_plural = _("daily payout totals")
        constraints = [
            models.UniqueConstraint(
                fields=["character", "day", "entry_type"], name="pilotstats_payoutdaily_unique",
            ),
        ]
        indexes = [models.Index(fields=["day"], name="pilotstats_payoutdaily_day")]

    def __str__(self) -> str:
        return f"{self.character_id} {self.day} {self.entry_type} {self.amount}"


class NpcKillDaily(models.Model):
    """Per character, day (UTC) and NPC type: rats killed, from bounty ticks."""

    character = models.ForeignKey(
        EveCharacter, on_delete=models.CASCADE, related_name="pilotstats_npc_kill_days",
    )
    day = models.DateField()
    npc_type_id = models.PositiveIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("daily NPC kills")
        verbose_name_plural = _("daily NPC kills")
        constraints = [
            models.UniqueConstraint(
                fields=["character", "day", "npc_type_id"], name="pilotstats_npckilldaily_unique",
            ),
        ]
        indexes = [models.Index(fields=["day"], name="pilotstats_npckilldaily_day")]

    def __str__(self) -> str:
        return f"{self.character_id} {self.day} {self.npc_type_id} x{self.count}"


# ---------------------------------------------------------------------------
# Killmails (zKillboard R2Z2)
# ---------------------------------------------------------------------------


class Killmail(models.Model):
    """A killmail involving at least one tracked character. Only the fields shown are kept."""

    killmail_id = models.BigIntegerField(unique=True)
    killmail_hash = models.CharField(max_length=64)
    killmail_time = models.DateTimeField(db_index=True)
    solar_system_id = models.PositiveIntegerField(null=True, blank=True)
    victim_ship_type_id = models.PositiveIntegerField(null=True, blank=True)
    total_value = models.DecimalField(max_digits=22, decimal_places=2, default=0)
    npc = models.BooleanField(default=False, help_text=_("zKill: killed by NPCs only."))
    solo = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("killmail")
        verbose_name_plural = _("killmails")
        ordering = ["-killmail_time"]

    def __str__(self) -> str:
        return f"Killmail {self.killmail_id}"

    @property
    def zkill_url(self) -> str:
        return f"https://zkillboard.com/kill/{self.killmail_id}/"


class KillmailParticipant(models.Model):
    """One Auth character on a killmail, as victim or attacker."""

    killmail = models.ForeignKey(Killmail, on_delete=models.CASCADE, related_name="participants")
    character = models.ForeignKey(
        EveCharacter, on_delete=models.CASCADE, related_name="pilotstats_killmail_entries",
    )
    is_victim = models.BooleanField(default=False)
    final_blow = models.BooleanField(default=False)
    damage = models.PositiveIntegerField(
        default=0, help_text=_("damage_done for an attacker, damage_taken for the victim."),
    )
    ship_type_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = _("killmail participant")
        verbose_name_plural = _("killmail participants")
        constraints = [
            models.UniqueConstraint(
                fields=["killmail", "character", "is_victim"],
                name="pilotstats_kmparticipant_unique",
            ),
        ]


class TrackedEntity(models.Model):
    """A corporation or alliance whose killmails are kept from the zKill feed."""

    class Kind(models.TextChoices):
        CORPORATION = "corporation", _("Corporation")
        ALLIANCE = "alliance", _("Alliance")

    class Source(models.TextChoices):
        AUTO = "auto", _("Automatic (Member-state mains)")
        MANUAL = "manual", _("Added by an admin")

    kind = models.CharField(max_length=16, choices=Kind.choices)
    entity_id = models.BigIntegerField()
    name = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    enabled = models.BooleanField(
        default=True,
        help_text=_("Untick to stop keeping this entity's mails. Automatic rows stay unticked."),
    )

    class Meta:
        verbose_name = _("tracked corporation or alliance")
        verbose_name_plural = _("tracked corporations and alliances")
        constraints = [
            models.UniqueConstraint(fields=["kind", "entity_id"], name="pilotstats_trackedentity_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.name or self.entity_id}"


class R2Z2State(models.Model):
    """Where the zKill R2Z2 reader is in the sequence. One row."""

    last_sequence = models.BigIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("zKill feed position")
        verbose_name_plural = _("zKill feed position")

    def __str__(self) -> str:
        return f"R2Z2 sequence {self.last_sequence}"

    @classmethod
    def get(cls) -> "R2Z2State":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


# ---------------------------------------------------------------------------
# Fleets (ESI, fleet boss token)
# ---------------------------------------------------------------------------


class TrackedFleet(models.Model):
    """An in-game fleet whose roster is polled with its boss's token."""

    fleet_id = models.BigIntegerField()
    boss = models.ForeignKey(
        EveCharacter, on_delete=models.CASCADE, related_name="pilotstats_bossed_fleets",
    )
    started_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="pilotstats_tracked_fleets",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("tracked fleet")
        verbose_name_plural = _("tracked fleets")
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Fleet {self.fleet_id} ({self.boss.character_name})"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class FleetMember(models.Model):
    """One pilot in one hull in a tracked fleet. A hull swap starts a new row."""

    fleet = models.ForeignKey(TrackedFleet, on_delete=models.CASCADE, related_name="members")
    eve_character_id = models.BigIntegerField(help_text=_("EVE character id"))
    character = models.ForeignKey(
        EveCharacter, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pilotstats_fleet_entries",
        help_text=_("Set when the pilot is on Auth."),
    )
    ship_type_id = models.PositiveIntegerField()
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()

    class Meta:
        verbose_name = _("fleet member")
        verbose_name_plural = _("fleet members")
        constraints = [
            models.UniqueConstraint(
                fields=["fleet", "eve_character_id", "ship_type_id"], name="pilotstats_fleetmember_unique",
            ),
        ]


# ---------------------------------------------------------------------------
# Type names (hulls, NPCs)
# ---------------------------------------------------------------------------


class TypeName(models.Model):
    """Cached EVE type name and group, for the hulls and NPCs this app shows."""

    type_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    group_name = models.CharField(max_length=255, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("type name")
        verbose_name_plural = _("type names")

    def __str__(self) -> str:
        return f"{self.name} ({self.type_id})"


# ---------------------------------------------------------------------------
# Discord webhook
# ---------------------------------------------------------------------------


def _validate_webhook_url(value):
    # Django
    from django.core.exceptions import ValidationError

    if value and not DISCORD_WEBHOOK_RE.match(value):
        raise ValidationError(_("That isn't a Discord webhook URL (https://discord.com/api/webhooks/…)."))


def _validate_timezone(value):
    # Standard Library
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    # Django
    from django.core.exceptions import ValidationError

    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(_("Unknown time zone. Use a name like UTC or Europe/London.")) from exc


DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+/?$"
)


class DiscordSettings(models.Model):
    """Discord webhook feed. One row, edited in Django admin by staff only.

    The webhook URL is a secret: it lives only here, never in settings files
    or the repository, and the admin never shows it after saving.
    """

    enabled = models.BooleanField(default=False)
    webhook_url = models.CharField(
        _("webhook URL"), max_length=255, blank=True, validators=[_validate_webhook_url],
    )
    daily_post_time = models.TimeField(
        _("daily post time"), default=datetime.time(11, 5),
        help_text=_("When the daily top 10 goes out. Default 11:05, just after EVE downtime (UTC)."),
    )
    timezone = models.CharField(
        _("time zone"), max_length=64, default="UTC", validators=[_validate_timezone],
        help_text=_("Time zone for the daily post time and its 24-hour window. EVE time is UTC."),
    )
    payout_threshold_isk = models.DecimalField(
        _("payout threshold (ISK)"), max_digits=22, decimal_places=2, default=300_000_000,
        help_text=_("Post a single bounty / ESS / mission payout line at or above this."),
    )
    kill_threshold_isk = models.DecimalField(
        _("kill / loss threshold (ISK)"), max_digits=22, decimal_places=2, default=300_000_000,
        help_text=_("Post a member's kill or loss worth at least this. Smaller losses are never posted."),
    )
    max_posts_per_hour = models.PositiveIntegerField(
        _("max event posts per hour"), default=6,
        help_text=_("Events past this in any hour are skipped, not queued. The daily post doesn't count."),
    )
    post_records = models.BooleanField(
        _("post records"), default=True,
        help_text=_("New personal and fleet records for session damage, session DPS, and 24h payouts."),
    )

    class Meta:
        verbose_name = _("Discord settings")
        verbose_name_plural = _("Discord settings")

    def __str__(self) -> str:
        return "Discord settings"

    @classmethod
    def get(cls) -> "DiscordSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_active(self) -> bool:
        return self.enabled and bool(self.webhook_url)


class DiscordPost(models.Model):
    """One post (or skipped post) to the webhook. ``key`` makes each post happen once."""

    class Kind(models.TextChoices):
        DAILY = "daily", _("Daily top 10")
        PAYOUT = "payout", _("Big payout")
        KILLMAIL = "killmail", _("Big kill or loss")
        RECORD = "record", _("Record")
        TEST = "test", _("Test message")

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        SKIPPED = "skipped", _("Skipped (hourly cap)")
        FAILED = "failed", _("Failed")

    key = models.CharField(max_length=128, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    detail = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _("Discord post")
        verbose_name_plural = _("Discord posts")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind} {self.key} ({self.status})"
