from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.auth_views import PortalLoginView
from accounts.views import dashboard
from accounts import user_views
from catalog import views as catalog_views
from inventory import views as inventory_views
from reports import views as report_views
from reports import exports as report_exports

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", PortalLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
    path("api/", include("sales.urls_api")),
    # Users (IT)
    path("users/", user_views.user_list, name="user_list"),
    path("users/new/", user_views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", user_views.user_edit, name="user_edit"),
    # Catalog
    path("customers/", catalog_views.customer_list, name="customer_list"),
    path("customers/new/", catalog_views.customer_create, name="customer_create"),
    path("customers/<int:pk>/", catalog_views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", catalog_views.customer_edit, name="customer_edit"),
    path(
        "customers/<int:pk>/delete/",
        catalog_views.customer_delete,
        name="customer_delete",
    ),
    path("branches/", catalog_views.branch_list, name="branch_list"),
    path("branches/new/", catalog_views.branch_create, name="branch_create"),
    path("branches/<int:pk>/", catalog_views.branch_detail, name="branch_detail"),
    path("branches/<int:pk>/edit/", catalog_views.branch_edit, name="branch_edit"),
    path(
        "branches/<int:pk>/regenerate-key/",
        catalog_views.branch_regenerate_key,
        name="branch_regenerate_key",
    ),
    path(
        "branches/<int:pk>/delete/",
        catalog_views.branch_delete,
        name="branch_delete",
    ),
    path("products/", catalog_views.product_list, name="product_list"),
    path("products/new/", catalog_views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", catalog_views.product_edit, name="product_edit"),
    # Inventory
    path("stock/", inventory_views.stock_list, name="stock_list"),
    path("dispatches/", inventory_views.dispatch_list, name="dispatch_list"),
    path("dispatches/new/", inventory_views.dispatch_create, name="dispatch_create"),
    path("dispatches/<int:pk>/", inventory_views.dispatch_detail, name="dispatch_detail"),
    path(
        "dispatches/<int:pk>/approve/",
        inventory_views.dispatch_approve,
        name="dispatch_approve",
    ),
    # Reports
    path("reports/stock/", report_views.stock_balance, name="report_stock"),
    path("reports/dispatches/", report_views.dispatch_report, name="report_dispatches"),
    path("reports/sales/", report_views.sales_report, name="report_sales"),
    path(
        "reports/customer-stock/",
        report_views.customer_stock_summary,
        name="report_customer_stock",
    ),
    path(
        "reports/customer-tonnage/",
        report_views.customer_tonnage_report,
        name="report_customer_tonnage",
    ),
    path(
        "reports/stock-movement/",
        report_views.stock_movement,
        name="report_stock_movement",
    ),
    path(
        "reports/dispatch-warehouse/",
        report_views.dispatch_warehouse_report,
        name="report_dispatch_warehouse",
    ),
    path("reports/sync/", report_views.sync_report, name="report_sync"),
    # Report exports
    path("reports/stock/export/", report_exports.export_stock_balance, name="export_stock_balance"),
    path("reports/dispatches/export/", report_exports.export_dispatches, name="export_dispatches"),
    path("reports/sales/export/", report_exports.export_sales, name="export_sales"),
    path("reports/customer-stock/export/", report_exports.export_customer_stock, name="export_customer_stock"),
    path(
        "reports/customer-tonnage/export/",
        report_exports.export_customer_tonnage,
        name="export_customer_tonnage",
    ),
    path("reports/stock-movement/export/", report_exports.export_stock_movement, name="export_stock_movement"),
    path(
        "reports/dispatch-warehouse/export/",
        report_exports.export_dispatch_warehouse,
        name="export_dispatch_warehouse",
    ),
    path("reports/sync/export/", report_exports.export_sync, name="export_sync"),
]
