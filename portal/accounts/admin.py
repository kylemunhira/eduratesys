from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User

from .models import AuditLog, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    filter_horizontal = ("branches",)
    can_delete = False


class UserAdmin(DjangoUserAdmin):
    inlines = (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "entity", "entity_id", "actor")
    list_filter = ("action", "entity")
    search_fields = ("entity_id", "details")
    readonly_fields = ("created_at",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "updated_at")
    list_filter = ("role",)
    filter_horizontal = ("branches",)
    search_fields = ("user__username", "user__email")
