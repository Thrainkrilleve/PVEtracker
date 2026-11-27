"""Pilot Stats Django Admin"""

# Django
from django.contrib import admin

from .models import (
    CombatLogUpload,
    FleetMember,
    Killmail,
    KillmailParticipant,
    NpcKillDaily,
    Payout,
    PayoutDaily,
    PilotSettings,
    R2Z2State,
    TrackedEntity,
    TrackedFleet,
    TypeName,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Rows written by tasks or uploads; admin is for looking, not editing."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CombatLogUpload)
class CombatLogUploadAdmin(admin.ModelAdmin):
    list_display = (
        "character", "uploaded_by", "uploaded_at",
        "damage_out", "damage_in", "reps_received_total", "reps_given_total",
        "matched_lines", "combat_lines",
    )
    list_filter = ("uploaded_at",)
    search_fields = ("character__character_name", "uploaded_by__username", "source_filename")
    readonly_fields = [f.name for f in CombatLogUpload._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(PilotSettings)
class PilotSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "hide_from_live_feed", "wallet_opt_out")
    list_filter = ("hide_from_live_feed", "wallet_opt_out")
    search_fields = ("user__username",)


@admin.register(Payout)
class PayoutAdmin(ReadOnlyAdmin):
    list_display = ("character", "occurred_at", "entry_type", "amount", "ref_id")
    list_filter = ("entry_type",)
    search_fields = ("character__character_name",)
    date_hierarchy = "occurred_at"


@admin.register(PayoutDaily)
class PayoutDailyAdmin(ReadOnlyAdmin):
    list_display = ("character", "day", "entry_type", "amount")
    list_filter = ("entry_type",)
    search_fields = ("character__character_name",)
    date_hierarchy = "day"


@admin.register(NpcKillDaily)
class NpcKillDailyAdmin(ReadOnlyAdmin):
    list_display = ("character", "day", "npc_type_id", "count")
    search_fields = ("character__character_name",)
    date_hierarchy = "day"


class KillmailParticipantInline(admin.TabularInline):
    model = KillmailParticipant
    extra = 0
    can_delete = False
    readonly_fields = ("character", "is_victim", "final_blow", "damage", "ship_type_id")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Killmail)
class KillmailAdmin(ReadOnlyAdmin):
    list_display = ("killmail_id", "killmail_time", "victim_ship_type_id", "total_value", "npc", "solo")
    list_filter = ("npc", "solo")
    search_fields = ("killmail_id", "participants__character__character_name")
    date_hierarchy = "killmail_time"
    inlines = [KillmailParticipantInline]


@admin.register(TrackedEntity)
class TrackedEntityAdmin(admin.ModelAdmin):
    """Which corporations/alliances' killmails are kept from zKill.

    Automatic rows follow the mains of Member-state users (setting
    PILOTSTATS_ZKILL_AUTO_STATES). Untick "enabled" to stop one; add rows
    by hand for anyone else.
    """

    list_display = ("name", "kind", "entity_id", "source", "enabled")
    list_filter = ("kind", "source", "enabled")
    list_editable = ("enabled",)
    search_fields = ("name", "entity_id")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.source == TrackedEntity.Source.AUTO:
            return ("kind", "entity_id", "name", "source")
        return ("source",)


@admin.register(R2Z2State)
class R2Z2StateAdmin(admin.ModelAdmin):
    """Edit last_sequence only to replay or skip part of the zKill feed."""

    list_display = ("last_sequence", "updated_at")

    def has_add_permission(self, request):
        return not R2Z2State.objects.exists()


class FleetMemberInline(admin.TabularInline):
    model = FleetMember
    extra = 0
    can_delete = False
    readonly_fields = ("eve_character_id", "character", "ship_type_id", "first_seen", "last_seen")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TrackedFleet)
class TrackedFleetAdmin(ReadOnlyAdmin):
    list_display = ("fleet_id", "boss", "started_by", "started_at", "ended_at", "end_reason")
    list_filter = ("ended_at",)
    inlines = [FleetMemberInline]


@admin.register(TypeName)
class TypeNameAdmin(ReadOnlyAdmin):
    list_display = ("type_id", "name", "group_name", "fetched_at")
    search_fields = ("name", "type_id")
