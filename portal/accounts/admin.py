from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "entity", "entity_id", "actor")
    list_filter = ("action", "entity")
    search_fields = ("entity_id", "details")
    readonly_fields = ("created_at",)
