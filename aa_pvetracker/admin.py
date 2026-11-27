"""PvE Tracker Django Admin"""

# Django
from django.contrib import admin

from .models import CombatLogUpload, Payout, PilotSettings


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
    list_display = ("user", "hide_from_live_feed")
    list_filter = ("hide_from_live_feed",)
    search_fields = ("user__username",)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """Read-only. Phase 2 is what inserts rows; admin is for inspection."""

    list_display = ("character", "occurred_at", "entry_type", "amount", "ref_id")
    list_filter = ("entry_type",)
    search_fields = ("character__character_name",)
    readonly_fields = [f.name for f in Payout._meta.fields]

    def has_add_permission(self, request):
        return False
