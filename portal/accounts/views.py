from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from catalog.models import Branch, Customer, Product
from inventory.models import BranchStock, Dispatch
from sales.models import Sale, SyncLog


@login_required
def dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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

    context = {
        "total_customers": Customer.objects.count(),
        "total_branches": Branch.objects.count(),
        "total_products": Product.objects.filter(status=Product.Status.ACTIVE).count(),
        "today_sales_count": Sale.objects.filter(sold_at__gte=today_start).count(),
        "today_sales_total": Sale.objects.filter(sold_at__gte=today_start).aggregate(
            s=Sum("total_amount")
        )["s"]
        or 0,
        "inventory_units": BranchStock.objects.aggregate(s=Sum("quantity"))["s"] or 0,
        "low_stock": low_stock[:10],
        "offline_branches": offline_branches[:10],
        "recent_dispatches": Dispatch.objects.select_related("customer", "branch")[:8],
        "recent_syncs": SyncLog.objects.select_related("branch")[:8],
    }
    return render(request, "portal/dashboard.html", context)
