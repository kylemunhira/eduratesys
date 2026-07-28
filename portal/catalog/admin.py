from django.contrib import admin

from .models import Branch, Customer, Product


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "status")
    list_filter = ("status",)
    search_fields = ("name", "phone", "email")


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "customer",
        "manager",
        "is_online",
        "last_sync_at",
        "machine_id",
    )
    list_filter = ("customer", "is_online")
    search_fields = ("name", "api_key", "machine_id")
    readonly_fields = ("api_key", "last_sync_at")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "category",
        "unit",
        "selling_price",
        "cost_price",
        "status",
    )
    list_filter = ("status", "category")
    search_fields = ("code", "barcode", "name")
