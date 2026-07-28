from django.contrib import admin

from .models import Sale, SaleItem, SyncLog


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ("product", "quantity", "unit_price")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "external_sale_id",
        "branch",
        "sold_at",
        "total_amount",
        "received_at",
    )
    list_filter = ("branch__customer", "branch")
    search_fields = ("external_sale_id",)
    inlines = [SaleItemInline]


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "branch", "status", "external_sale_id", "message")
    list_filter = ("status", "branch")
    search_fields = ("external_sale_id", "message")
