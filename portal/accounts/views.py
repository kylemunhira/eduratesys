from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from catalog.models import Branch, Customer, Product
from inventory.models import BranchStock, Dispatch
from reports.period import parse_period_bounds
from sales.models import Sale, SaleItem, SyncLog


@login_required
def dashboard(request):
    bounds = parse_period_bounds(request)
    start_dt = bounds["start_dt"]
    end_dt = bounds["end_dt"]
    now = timezone.now()
    offline_cutoff = now - timedelta(hours=settings.BRANCH_OFFLINE_HOURS)
    threshold = settings.LOW_STOCK_THRESHOLD

    branches = Branch.objects.select_related("customer")
    offline_branches = [
        b
        for b in branches
        if not b.last_sync_at or b.last_sync_at < offline_cutoff
    ]

    low_stock = []
    for row in BranchStock.objects.select_related("branch", "product"):
        limit = row.product.effective_threshold(threshold)
        if row.quantity <= limit:
            low_stock.append((row, limit))

    fast_moving = list(
        SaleItem.objects.filter(
            sale__sold_at__gte=start_dt, sale__sold_at__lt=end_dt
        )
        .values("product__code", "product__name")
        .annotate(units_sold=Sum("quantity"))
        .order_by("-units_sold")[:8]
    )
    top_branches = list(
        Sale.objects.filter(sold_at__gte=start_dt, sold_at__lt=end_dt)
        .values("branch__name", "branch__customer__name")
        .annotate(revenue=Sum("total_amount"), sales_count=Count("id"))
        .order_by("-revenue")[:8]
    )

    period_sales = Sale.objects.filter(
        sold_at__gte=start_dt, sold_at__lt=end_dt
    )
    context = {
        "period": bounds["period"],
        "date_from": bounds["date_from_iso"],
        "date_to": bounds["date_to_iso"],
        "period_label": bounds["period_label"],
        "fast_moving_chart": {
            "labels": [
                f"{row['product__code']} · {row['product__name']}" for row in fast_moving
            ],
            "values": [row["units_sold"] for row in fast_moving],
        },
        "top_branches_chart": {
            "labels": [
                f"{row['branch__customer__name']} / {row['branch__name']}"
                for row in top_branches
            ],
            "values": [float(row["revenue"] or 0) for row in top_branches],
        },
        "total_customers": Customer.objects.count(),
        "total_branches": Branch.objects.count(),
        "total_products": Product.objects.filter(status=Product.Status.ACTIVE).count(),
        "period_sales_count": period_sales.count(),
        "period_sales_total": period_sales.aggregate(s=Sum("total_amount"))["s"] or 0,
        "inventory_units": BranchStock.objects.aggregate(s=Sum("quantity"))["s"] or 0,
        "low_stock": low_stock[:10],
        "offline_branches": offline_branches[:10],
        "recent_dispatches": Dispatch.objects.select_related("customer", "branch")
        .filter(created_at__gte=start_dt, created_at__lt=end_dt)[:8],
        "recent_syncs": SyncLog.objects.select_related("branch")
        .filter(created_at__gte=start_dt, created_at__lt=end_dt)[:8],
    }
    return render(request, "portal/dashboard.html", context)
