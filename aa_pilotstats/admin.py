"""Pilot Stats Django Admin"""

# Django
from django import forms
from django.contrib import admin, messages

from .models import (
    CombatLogUpload,
    DiscordPost,
    DiscordSettings,
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


class DiscordSettingsForm(forms.ModelForm):
    """The webhook URL is write-only: it's never shown again after saving."""

    webhook_url = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off", "size": 80}),
        help_text="Discord channel → Integrations → Webhooks → Copy URL. Leave blank to keep the saved one.",
    )
    clear_webhook = forms.BooleanField(required=False, label="Remove the saved webhook URL")

    class Meta:
        model = DiscordSettings
        fields = "__all__"

    def clean_webhook_url(self):
        value = (self.cleaned_data.get("webhook_url") or "").strip()
        if not value and self.instance.pk:
            return self.instance.webhook_url
        return value

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("clear_webhook"):
            obj.webhook_url = ""
            obj.enabled = False
        if commit:
            obj.save()
        return obj


@admin.register(DiscordSettings)
class DiscordSettingsAdmin(admin.ModelAdmin):
    form = DiscordSettingsForm
    list_display = ("__str__", "enabled", "webhook_saved", "daily_post_time", "timezone",
                    "payout_threshold_isk", "kill_threshold_isk", "max_posts_per_hour", "post_records")
    actions = ["send_test_message"]
    fieldsets = (
        (None, {"fields": ("enabled", "webhook_url", "clear_webhook")}),
        ("Daily top 10", {"fields": ("daily_post_time", "timezone")}),
        ("Major events", {"fields": ("payout_threshold_isk", "kill_threshold_isk",
                                     "max_posts_per_hour", "post_records")}),
    )

    @admin.display(boolean=True, description="webhook saved")
    def webhook_saved(self, obj):
        return bool(obj.webhook_url)

    def has_add_permission(self, request):
        return not DiscordSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Send a test message to the webhook")
    def send_test_message(self, request, queryset):
        from . import discord

        config = DiscordSettings.get()
        if not config.is_active:
            self.message_user(request, "Enable the feed and save a webhook URL first.", messages.WARNING)
            return
        discord.test_message(config)
        self.message_user(request, "Test message queued. It appears in the channel within a few seconds.")


@admin.register(DiscordPost)
class DiscordPostAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "kind", "status", "key", "detail")
    list_filter = ("kind", "status")
    search_fields = ("key", "detail")
