from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Trim
from django.shortcuts import render
from django.utils import timezone

from accounts.roles import accessible_branches, filter_by_accessible_branches
from catalog.models import Branch, Customer, Product
from inventory.models import BranchStock, Dispatch
from reports.period import parse_period_bounds
from reports.views import _tons, pack_size_kg
from sales.models import Sale, SaleItem, SyncLog


def _selected_category(request):
    return (request.GET.get("category") or "").strip() or None


def _filter_by_category(qs, category, category_field="product__category"):
    """Restrict a queryset to products in the given category (or blank = Uncategorized)."""
    if not category:
        return qs
    if category == "Uncategorized":
        return qs.annotate(_dash_cat=Trim(category_field)).filter(
            Q(_dash_cat="") | Q(_dash_cat__isnull=True)
        )
    return qs.filter(**{category_field: category})


@login_required
def dashboard(request):
    bounds = parse_period_bounds(request)
    start_dt = bounds["start_dt"]
    end_dt = bounds["end_dt"]
    selected_category = _selected_category(request)
    now = timezone.now()
    offline_cutoff = now - timedelta(hours=settings.BRANCH_OFFLINE_HOURS)
    threshold = settings.LOW_STOCK_THRESHOLD
    allowed_branches = accessible_branches(request.user)

    products_qs = _filter_by_category(
        Product.objects.filter(status=Product.Status.ACTIVE),
        selected_category,
        category_field="category",
    )
    stock_qs = _filter_by_category(
        filter_by_accessible_branches(
            BranchStock.objects.select_related("branch", "product"),
            request.user,
        ),
        selected_category,
    )
    sale_items = _filter_by_category(
        filter_by_accessible_branches(
            SaleItem.objects.filter(
                sale__sold_at__gte=start_dt, sale__sold_at__lt=end_dt
            ),
            request.user,
            field="sale__branch_id",
        ),
        selected_category,
    )

    if selected_category:
        branch_ids = set(stock_qs.values_list("branch_id", flat=True))
        branch_ids.update(sale_items.values_list("sale__branch_id", flat=True))
        branches = allowed_branches.filter(pk__in=branch_ids)
    else:
        branches = allowed_branches

    offline_branches = [
        b
        for b in branches
        if not b.last_sync_at or b.last_sync_at < offline_cutoff
    ]

    low_stock = []
    inventory_units = 0
    inventory_tons = Decimal("0")
    for row in stock_qs:
        inventory_units += row.quantity
        tons = _tons(row.quantity, pack_size_kg(row.product.name))
        if tons is not None:
            inventory_tons += tons
        limit = row.product.effective_threshold(threshold)
        if row.quantity <= limit:
            low_stock.append((row, limit))
    inventory_tons = inventory_tons.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    fast_moving = list(
        sale_items.values("product__code", "product__name")
        .annotate(units_sold=Sum("quantity"))
        .order_by("-units_sold")[:8]
    )

    # Category cards always cover the full period so users can switch filters.
    # Volume is tons (qty × pack kg / 1000), matching sales reports.
    category_totals = {}
    category_sale_items = filter_by_accessible_branches(
        SaleItem.objects.filter(
            sale__sold_at__gte=start_dt, sale__sold_at__lt=end_dt
        ),
        request.user,
        field="sale__branch_id",
    )
    for row in category_sale_items.values(
        "product__category", "product__name"
    ).annotate(qty=Sum("quantity")):
        name = (row["product__category"] or "").strip() or "Uncategorized"
        tons = _tons(row["qty"], pack_size_kg(row["product__name"]))
        volume = tons if tons is not None else Decimal("0")
        category_totals[name] = category_totals.get(name, Decimal("0")) + volume
    category_sold = [
        {"category": name, "tons_sold": qty}
        for name, qty in sorted(
            category_totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    category_sold_total = sum(
        (row["tons_sold"] for row in category_sold), Decimal("0")
    )

    if selected_category:
        top_branches = list(
            sale_items.values("sale__branch__name", "sale__branch__customer__name")
            .annotate(
                revenue=Sum(F("quantity") * F("unit_price")),
                sales_count=Count("sale_id", distinct=True),
            )
            .order_by("-revenue")[:8]
        )
        top_branch_labels = [
            f"{row['sale__branch__customer__name']} / {row['sale__branch__name']}"
            for row in top_branches
        ]
        period_sales_count = sale_items.values("sale_id").distinct().count()
        period_sales_total = (
            sale_items.aggregate(s=Sum(F("quantity") * F("unit_price")))["s"]
            or Decimal("0")
        )
    else:
        top_branches = list(
            filter_by_accessible_branches(
                Sale.objects.filter(sold_at__gte=start_dt, sold_at__lt=end_dt),
                request.user,
            )
            .values("branch__name", "branch__customer__name")
            .annotate(revenue=Sum("total_amount"), sales_count=Count("id"))
            .order_by("-revenue")[:8]
        )
        top_branch_labels = [
            f"{row['branch__customer__name']} / {row['branch__name']}"
            for row in top_branches
        ]
        period_sales = filter_by_accessible_branches(
            Sale.objects.filter(sold_at__gte=start_dt, sold_at__lt=end_dt),
            request.user,
        )
        period_sales_count = period_sales.count()
        period_sales_total = period_sales.aggregate(s=Sum("total_amount"))["s"] or 0

    dispatches = filter_by_accessible_branches(
        Dispatch.objects.select_related("customer", "branch").filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        ),
        request.user,
    )
    if selected_category:
        dispatches = _filter_by_category(
            dispatches, selected_category, category_field="items__product__category"
        ).distinct()

    syncs = filter_by_accessible_branches(
        SyncLog.objects.select_related("branch").filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        ),
        request.user,
    )
    if selected_category:
        syncs = syncs.filter(branch_id__in=branches.values_list("pk", flat=True))

    context = {
        "period": bounds["period"],
        "date_from": bounds["date_from_iso"],
        "date_to": bounds["date_to_iso"],
        "period_label": bounds["period_label"],
        "selected_category": selected_category,
        "fast_moving_chart": {
            "labels": [
                f"{row['product__code']} · {row['product__name']}" for row in fast_moving
            ],
            "values": [row["units_sold"] for row in fast_moving],
        },
        "top_branches_chart": {
            "labels": top_branch_labels,
            "values": [float(row["revenue"] or 0) for row in top_branches],
        },
        "total_customers": (
            Customer.objects.filter(
                pk__in=allowed_branches.values_list("customer_id", flat=True)
            ).distinct().count()
        ),
        "total_branches": branches.count(),
        "total_products": products_qs.count(),
        "period_sales_count": period_sales_count,
        "period_sales_total": period_sales_total,
        "inventory_units": inventory_units,
        "inventory_tons": f"{inventory_tons:.2f}",
        "category_sold": category_sold,
        "category_sold_total": category_sold_total,
        "low_stock": low_stock[:10],
        "offline_branches": offline_branches[:10],
        "recent_dispatches": dispatches[:8],
        "recent_syncs": syncs[:8],
    }
    return render(request, "portal/dashboard.html", context)
