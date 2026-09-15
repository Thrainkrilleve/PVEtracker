"""PvE Tracker Django Admin"""

# Django
from django.contrib import admin

from .models import CombatLogUpload


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
