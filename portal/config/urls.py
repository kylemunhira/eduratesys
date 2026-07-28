from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.views import dashboard
from catalog import views as catalog_views
from inventory import views as inventory_views
from reports import views as report_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
    path("api/", include("sales.urls_api")),
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
        "reports/stock-movement/",
        report_views.stock_movement,
        name="report_stock_movement",
    ),
    path("reports/sync/", report_views.sync_report, name="report_sync"),
]
