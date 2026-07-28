from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from catalog.models import Branch, Customer, Product
from inventory.models import BranchStock, Dispatch, DispatchItem, StockLoss
from reports.period import parse_period_bounds
from sales.models import Sale, SaleItem, SyncLog


def _period_context(bounds):
    return {
        "period": bounds["period"],
        "date_from": bounds["date_from_iso"],
        "date_to": bounds["date_to_iso"],
        "period_label": bounds["period_label"],
    }


@login_required
def stock_balance(request):
    stocks = BranchStock.objects.select_related(
        "branch", "branch__customer", "product"
    )
    return render(request, "reports/stock_balance.html", {"stocks": stocks})


@login_required
def dispatch_report(request):
    bounds = parse_period_bounds(request)
    dispatches = (
        Dispatch.objects.select_related("customer", "branch")
        .prefetch_related("items__product")
        .filter(created_at__gte=bounds["start_dt"], created_at__lt=bounds["end_dt"])
    )
    status = request.GET.get("status")
    if status:
        dispatches = dispatches.filter(status=status)
    return render(
        request,
        "reports/dispatch_report.html",
        {
            "dispatches": dispatches,
            "status": status,
            **_period_context(bounds),
        },
    )


@login_required
def sales_report(request):
    bounds = parse_period_bounds(request)
    sales = (
        Sale.objects.select_related("branch", "branch__customer")
        .prefetch_related("items__product")
        .filter(sold_at__gte=bounds["start_dt"], sold_at__lt=bounds["end_dt"])
    )
    return render(
        request,
        "reports/sales_report.html",
        {
            "sales": sales,
            **_period_context(bounds),
        },
    )


@login_required
def customer_stock_summary(request):
    rows = (
        BranchStock.objects.values(
            "branch__customer__name", "branch__customer_id"
        )
        .annotate(total_qty=Sum("quantity"))
        .order_by("branch__customer__name")
    )
    return render(request, "reports/customer_stock.html", {"rows": rows})


@login_required
def sync_report(request):
    bounds = parse_period_bounds(request)
    branches = Branch.objects.select_related("customer")
    logs = SyncLog.objects.select_related("branch").filter(
        created_at__gte=bounds["start_dt"], created_at__lt=bounds["end_dt"]
    )[:100]
    return render(
        request,
        "reports/sync_report.html",
        {
            "branches": branches,
            "logs": logs,
            **_period_context(bounds),
        },
    )


@login_required
def stock_movement(request):
    """
    Period stock movement per customer or branch × product.

    Opening = approved dispatches − sales − losses (before period)
    Closing = opening + dispatched − sold − shrinkage/damaged (in period)
    """
    bounds = parse_period_bounds(request)
    start_dt = bounds["start_dt"]
    end_dt = bounds["end_dt"]
    group_by = request.GET.get("group_by") or "branch"
    if group_by not in ("branch", "customer"):
        group_by = "branch"

    customer_id = request.GET.get("customer") or ""
    branch_id = request.GET.get("branch") or ""

    customers = Customer.objects.all()
    branches = Branch.objects.select_related("customer")
    if customer_id.isdigit():
        branches = branches.filter(customer_id=int(customer_id))

    dispatch_base = DispatchItem.objects.filter(
        dispatch__status=Dispatch.Status.APPROVED,
        dispatch__approved_at__isnull=False,
    )
    sale_base = SaleItem.objects.all()
    loss_base = StockLoss.objects.all()

    if customer_id.isdigit():
        cid = int(customer_id)
        dispatch_base = dispatch_base.filter(dispatch__customer_id=cid)
        sale_base = sale_base.filter(sale__branch__customer_id=cid)
        loss_base = loss_base.filter(branch__customer_id=cid)
    if branch_id.isdigit():
        bid = int(branch_id)
        dispatch_base = dispatch_base.filter(dispatch__branch_id=bid)
        sale_base = sale_base.filter(sale__branch_id=bid)
        loss_base = loss_base.filter(branch_id=bid)

    if group_by == "customer":
        d_key = ("dispatch__customer_id", "product_id")
        s_key = ("sale__branch__customer_id", "product_id")
        l_key = ("branch__customer_id", "product_id")
    else:
        d_key = ("dispatch__customer_id", "dispatch__branch_id", "product_id")
        s_key = ("sale__branch__customer_id", "sale__branch_id", "product_id")
        l_key = ("branch__customer_id", "branch_id", "product_id")

    def _bucket(qs, values_fields, qty_field="quantity"):
        return {
            tuple(row[f] for f in values_fields): row["total"] or 0
            for row in qs.values(*values_fields).annotate(total=Sum(qty_field))
        }

    dispatched_before = _bucket(
        dispatch_base.filter(dispatch__approved_at__lt=start_dt), d_key
    )
    sold_before = _bucket(sale_base.filter(sale__sold_at__lt=start_dt), s_key)
    loss_before = _bucket(loss_base.filter(recorded_at__lt=start_dt), l_key)

    dispatched_in = _bucket(
        dispatch_base.filter(
            dispatch__approved_at__gte=start_dt, dispatch__approved_at__lt=end_dt
        ),
        d_key,
    )
    sold_in = _bucket(
        sale_base.filter(sale__sold_at__gte=start_dt, sale__sold_at__lt=end_dt),
        s_key,
    )
    loss_in = _bucket(
        loss_base.filter(recorded_at__gte=start_dt, recorded_at__lt=end_dt),
        l_key,
    )

    totals = defaultdict(
        lambda: {
            "opening": 0,
            "dispatched": 0,
            "sold": 0,
            "shrinkage": 0,
            "closing": 0,
        }
    )

    all_keys = set()
    for mapping in (
        dispatched_before,
        sold_before,
        loss_before,
        dispatched_in,
        sold_in,
        loss_in,
    ):
        all_keys.update(mapping)

    for key in all_keys:
        opening = (
            dispatched_before.get(key, 0)
            - sold_before.get(key, 0)
            - loss_before.get(key, 0)
        )
        dispatched = dispatched_in.get(key, 0)
        sold = sold_in.get(key, 0)
        shrinkage = loss_in.get(key, 0)
        closing = opening + dispatched - sold - shrinkage
        if opening == dispatched == sold == shrinkage == closing == 0:
            continue
        totals[key] = {
            "opening": opening,
            "dispatched": dispatched,
            "sold": sold,
            "shrinkage": shrinkage,
            "closing": closing,
        }

    product_ids = {k[-1] for k in totals}
    customer_ids = {k[0] for k in totals}
    products = {
        p.pk: p for p in Product.objects.filter(pk__in=product_ids)
    }
    customer_map = {
        c.pk: c for c in Customer.objects.filter(pk__in=customer_ids)
    }
    branch_map = {}
    if group_by == "branch":
        branch_ids = {k[1] for k in totals}
        branch_map = {
            b.pk: b for b in Branch.objects.filter(pk__in=branch_ids)
        }

    rows = []
    for key, vals in totals.items():
        product = products.get(key[-1])
        customer = customer_map.get(key[0])
        if not product or not customer:
            continue
        row = {
            "customer": customer,
            "product": product,
            **vals,
        }
        if group_by == "branch":
            row["branch"] = branch_map.get(key[1])
            if not row["branch"]:
                continue
        rows.append(row)

    if group_by == "branch":
        rows.sort(
            key=lambda r: (
                r["customer"].name,
                r["branch"].name,
                r["product"].code,
            )
        )
    else:
        rows.sort(key=lambda r: (r["customer"].name, r["product"].code))

    return render(
        request,
        "reports/stock_movement.html",
        {
            "rows": rows,
            "group_by": group_by,
            "customers": customers,
            "branches": branches,
            "selected_customer": customer_id,
            "selected_branch": branch_id,
            **_period_context(bounds),
        },
    )
