from django.contrib import admin

from .models import BranchStock, Dispatch, DispatchItem


class DispatchItemInline(admin.TabularInline):
    model = DispatchItem
    extra = 1


@admin.register(Dispatch)
class DispatchAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "customer",
        "branch",
        "status",
        "dispatched_at",
        "approved_at",
        "created_at",
    )
    list_filter = ("status", "customer")
    search_fields = ("reference",)
    inlines = [DispatchItemInline]
    readonly_fields = (
        "dispatched_at",
        "dispatched_by",
        "approved_at",
        "approved_by",
    )


@admin.register(BranchStock)
class BranchStockAdmin(admin.ModelAdmin):
    list_display = ("branch", "product", "quantity", "updated_at")
    list_filter = ("branch__customer", "branch")
    search_fields = ("product__code", "product__name", "branch__name")
