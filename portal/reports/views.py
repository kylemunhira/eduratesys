import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from catalog.models import Branch, Customer, Product
from inventory.models import BranchStock, Dispatch, DispatchItem, StockLoss
from reports.period import parse_period_bounds
from reports.pos_db import PosDbError, fetch_grv_purchase_lines
from sales.models import SaleItem, SyncLog
from accounts.roles import (
    accessible_branches,
    filter_by_accessible_branches,
    user_can_access_branch,
)

_PACK_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*kg\b", re.IGNORECASE)
COMPANY_NAME = "EDURATE INVESTMENTS (PVT) LTD"
TONS_QUANT = Decimal("0.001")


def _period_context(bounds):
    return {
        "period": bounds["period"],
        "date_from": bounds["date_from_iso"],
        "date_to": bounds["date_to_iso"],
        "period_label": bounds["period_label"],
    }


def pack_size_kg(product_name: str) -> Decimal | None:
    """Extract pack size in kg from a product name (e.g. '… 25kg')."""
    match = _PACK_SIZE_RE.search(product_name or "")
    if not match:
        return None
    return Decimal(match.group(1))


def _quantize_tons(value: Decimal) -> Decimal:
    return value.quantize(TONS_QUANT, rounding=ROUND_HALF_UP)


def _tons(quantity, psize: Decimal | None) -> Decimal | None:
    if psize is None:
        return None
    return _quantize_tons(Decimal(quantity) * psize / Decimal(1000))


def _normalize_category(value):
    return (value or "").strip() or "Uncategorized"


def _local_date(dt):
    if timezone.is_aware(dt):
        return timezone.localtime(dt).date()
    return dt.date() if hasattr(dt, "date") else dt


def sales_report_data(request):
    """
    Build VastAfrica-style sales report rows and volume summaries.

    Raw rows are aggregated by product × customer × branch × unit price
    (not one line per receipt), so qty/tons/amount are totals sold.

    Returns (bounds, raw_rows, by_category, by_sku, by_branch, totals).
    Volume is tons from aggregated qty (qty × pack kg / 1000), rounded once
    per product — not per receipt line — so totals match true weight.
    """
    bounds = parse_period_bounds(request)
    items = filter_by_accessible_branches(
        SaleItem.objects.select_related(
            "product",
            "sale",
            "sale__branch",
            "sale__branch__customer",
        ).filter(
            sale__sold_at__gte=bounds["start_dt"],
            sale__sold_at__lt=bounds["end_dt"],
        ),
        request.user,
        field="sale__branch_id",
    ).order_by("-sale__sold_at", "-sale__external_sale_id", "-pk")
    branch_id = request.GET.get("branch") or ""
    if branch_id.isdigit():
        bid = int(branch_id)
        if not user_can_access_branch(request.user, bid):
            items = items.none()
        else:
            items = items.filter(sale__branch_id=bid)

    # Key: (product_id, customer_id, branch_id, unit_price) → combined qty sold
    aggregated = {}
    # qty by dimension → product name (pack size is derived from name)
    qty_by_category_product = defaultdict(lambda: defaultdict(int))
    qty_by_sku = defaultdict(int)
    qty_by_branch_product = defaultdict(lambda: defaultdict(int))
    total_qty = 0
    total_amount = Decimal("0")

    for item in items:
        product = item.product
        sale = item.sale
        branch = sale.branch
        customer = branch.customer
        psize = pack_size_kg(product.name)
        unit_price = Decimal(item.unit_price)
        amount = (Decimal(item.quantity) * unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        reporting_group = _normalize_category(product.category)
        sold_date = _local_date(sale.sold_at)
        key = (product.pk, customer.pk, branch.pk, unit_price)

        bucket = aggregated.get(key)
        if bucket is None:
            aggregated[key] = {
                "date": sold_date,
                "item_description": product.name,
                "group": "Stockfeeds Finished Product",
                "sale_reporting_group": reporting_group,
                "account": "",
                "customer_name": customer.name,
                "quantity": item.quantity,
                "amount": amount,
                "unit_price": unit_price,
                "branch": branch.name,
                "psize": psize,
            }
        else:
            bucket["quantity"] += item.quantity
            bucket["amount"] += amount
            if sold_date > bucket["date"]:
                bucket["date"] = sold_date

        qty_by_category_product[reporting_group][product.name] += item.quantity
        qty_by_sku[product.name] += item.quantity
        qty_by_branch_product[branch.name][product.name] += item.quantity
        total_qty += item.quantity
        total_amount += amount

    def _volume_from_product_qty(product_qty):
        """Sum tons from per-product totals (round once per product)."""
        total = Decimal("0")
        for name, qty in product_qty.items():
            tons = _tons(qty, pack_size_kg(name))
            if tons is not None:
                total += tons
        return total

    by_category = {
        cat: _volume_from_product_qty(products)
        for cat, products in qty_by_category_product.items()
    }
    by_sku = {}
    for name, qty in qty_by_sku.items():
        tons = _tons(qty, pack_size_kg(name))
        by_sku[name] = tons if tons is not None else Decimal("0")
    by_branch = {
        branch: _volume_from_product_qty(products)
        for branch, products in qty_by_branch_product.items()
    }
    total_tons = _quantize_tons(sum(by_category.values(), Decimal("0")))

    raw_rows = []
    for bucket in aggregated.values():
        tons = _tons(bucket["quantity"], bucket["psize"])
        raw_rows.append(
            {
                "date": bucket["date"],
                "item_description": bucket["item_description"],
                "group": bucket["group"],
                "sale_reporting_group": bucket["sale_reporting_group"],
                "account": bucket["account"],
                "customer_name": bucket["customer_name"],
                "tons": tons,
                "quantity": bucket["quantity"],
                "amount": bucket["amount"].quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "unit_price": bucket["unit_price"],
                "branch": bucket["branch"],
                "psize": bucket["psize"],
            }
        )

    # Newest sale date first; names stay A–Z within the same date (stable sort).
    raw_rows.sort(
        key=lambda r: (
            r["customer_name"].lower(),
            r["branch"].lower(),
            r["item_description"].lower(),
        )
    )
    raw_rows.sort(key=lambda r: r["date"] or timezone.localdate(), reverse=True)

    def _summary(mapping):
        return [
            {"label": label, "volume": vol}
            for label, vol in sorted(mapping.items(), key=lambda x: x[0].lower())
        ]

    totals = {
        "tons": total_tons,
        "quantity": total_qty,
        "amount": total_amount,
    }
    return (
        bounds,
        raw_rows,
        _summary(by_category),
        _summary(by_sku),
        _summary(by_branch),
        totals,
    )


def _qty_bucket(qs, branch_field, product_field="product_id"):
    return {
        (row[branch_field], row[product_field]): row["total"] or 0
        for row in qs.values(branch_field, product_field).annotate(total=Sum("quantity"))
    }


def closing_stock_quantities(user, end_dt, *, branch_id=None):
    """
    Closing on-hand quantity keyed by (branch_id, product_id) as of end_dt.

    Closing = current BranchStock − dispatches after + sales after + losses after.
    """
    stock_qs = filter_by_accessible_branches(BranchStock.objects.all(), user)
    dispatch_qs = filter_by_accessible_branches(
        DispatchItem.objects.filter(
            dispatch__status=Dispatch.Status.APPROVED,
            dispatch__approved_at__isnull=False,
            dispatch__approved_at__gte=end_dt,
        ),
        user,
        field="dispatch__branch_id",
    )
    sale_qs = filter_by_accessible_branches(
        SaleItem.objects.filter(sale__sold_at__gte=end_dt),
        user,
        field="sale__branch_id",
    )
    loss_qs = filter_by_accessible_branches(
        StockLoss.objects.filter(recorded_at__gte=end_dt),
        user,
    )

    if branch_id is not None:
        bid = int(branch_id)
        if not user_can_access_branch(user, bid):
            return {}
        stock_qs = stock_qs.filter(branch_id=bid)
        dispatch_qs = dispatch_qs.filter(dispatch__branch_id=bid)
        sale_qs = sale_qs.filter(sale__branch_id=bid)
        loss_qs = loss_qs.filter(branch_id=bid)

    stock_now = _qty_bucket(stock_qs, "branch_id")
    dispatched_after = _qty_bucket(dispatch_qs, "dispatch__branch_id")
    sold_after = _qty_bucket(sale_qs, "sale__branch_id")
    loss_after = _qty_bucket(loss_qs, "branch_id")

    closing = {}
    for key in set(stock_now) | set(dispatched_after) | set(sold_after) | set(loss_after):
        qty = (
            stock_now.get(key, 0)
            - dispatched_after.get(key, 0)
            + sold_after.get(key, 0)
            + loss_after.get(key, 0)
        )
        if qty != 0:
            closing[key] = qty
    return closing


def stock_balance_data(request):
    """
    Closing stock with metric tons and stock value (selling_price × qty) as of date_to.

    Optional ?branch= filters to one accessible branch (also applied on export).
    Returns per branch×SKU lines, by-SKU totals, by-branch totals, and grand totals.
    """
    bounds = parse_period_bounds(request)
    branch_id = request.GET.get("branch") or ""
    bid = int(branch_id) if branch_id.isdigit() else None
    closing = closing_stock_quantities(request.user, bounds["end_dt"], branch_id=bid)

    product_ids = {pid for _, pid in closing}
    branch_ids = {bid_ for bid_, _ in closing}
    products = {p.pk: p for p in Product.objects.filter(pk__in=product_ids)}
    branches = {
        b.pk: b
        for b in Branch.objects.select_related("customer").filter(pk__in=branch_ids)
    }

    money = Decimal("0.01")
    lines = []
    for (bid_key, pid), qty in closing.items():
        product = products.get(pid)
        branch = branches.get(bid_key)
        if not product or not branch:
            continue
        unit_price = Decimal(product.selling_price or 0)
        stock_value = (Decimal(qty) * unit_price).quantize(
            money, rounding=ROUND_HALF_UP
        )
        tons = _tons(qty, pack_size_kg(product.name))
        lines.append(
            {
                "customer_name": branch.customer.name,
                "branch_id": branch.pk,
                "branch_name": branch.name,
                "branch_label": f"{branch.customer.name} / {branch.name}",
                "product_code": product.code,
                "product_name": product.name,
                "quantity": qty,
                "tons": tons,
                "unit_price": unit_price,
                "stock_value": stock_value,
            }
        )

    lines.sort(
        key=lambda r: (
            r["customer_name"].lower(),
            r["branch_name"].lower(),
            r["product_code"] or "",
            r["product_name"].lower(),
        )
    )

    by_sku_map = defaultdict(
        lambda: {"quantity": 0, "tons": Decimal("0"), "stock_value": Decimal("0")}
    )
    by_branch_map = defaultdict(
        lambda: {"quantity": 0, "tons": Decimal("0"), "stock_value": Decimal("0")}
    )
    for row in lines:
        sku = by_sku_map[row["product_name"]]
        sku["quantity"] += row["quantity"]
        if row["tons"] is not None:
            sku["tons"] += row["tons"]
        sku["stock_value"] += row["stock_value"]
        br = by_branch_map[row["branch_label"]]
        br["quantity"] += row["quantity"]
        if row["tons"] is not None:
            br["tons"] += row["tons"]
        br["stock_value"] += row["stock_value"]

    by_sku = [
        {
            "label": name,
            "quantity": vals["quantity"],
            "tons": _quantize_tons(vals["tons"]),
            "stock_value": vals["stock_value"].quantize(money, rounding=ROUND_HALF_UP),
        }
        for name, vals in sorted(by_sku_map.items(), key=lambda x: x[0].lower())
    ]
    by_branch = [
        {
            "label": name,
            "quantity": vals["quantity"],
            "tons": _quantize_tons(vals["tons"]),
            "stock_value": vals["stock_value"].quantize(money, rounding=ROUND_HALF_UP),
        }
        for name, vals in sorted(by_branch_map.items(), key=lambda x: x[0].lower())
    ]
    total_tons = _quantize_tons(
        sum((r["tons"] or Decimal("0") for r in lines), Decimal("0"))
    )
    totals = {
        "quantity": sum(r["quantity"] for r in lines),
        "tons": total_tons,
        "stock_value": sum((r["stock_value"] for r in lines), Decimal("0")).quantize(
            money, rounding=ROUND_HALF_UP
        ),
    }
    return bounds, lines, by_sku, by_branch, totals


@login_required
def stock_balance(request):
    bounds, lines, by_sku, by_branch, totals = stock_balance_data(request)
    view = (request.GET.get("view") or "detail").lower()
    if view not in ("detail", "sku", "branch"):
        view = "detail"
    branch_id = request.GET.get("branch") or ""
    return render(
        request,
        "reports/stock_balance.html",
        {
            "view": view,
            "lines": lines,
            "by_sku": by_sku,
            "by_branch": by_branch,
            "totals": totals,
            "branches": accessible_branches(request.user),
            "selected_branch": branch_id,
            "date_to_display": bounds["date_to"],
            **_period_context(bounds),
        },
    )


@login_required
def dispatch_report(request):
    bounds = parse_period_bounds(request)
    dispatches = filter_by_accessible_branches(
        Dispatch.objects.select_related("customer", "branch")
        .prefetch_related("items__product")
        .filter(created_at__gte=bounds["start_dt"], created_at__lt=bounds["end_dt"]),
        request.user,
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
    bounds, raw_rows, by_category, by_sku, by_branch, totals = sales_report_data(
        request
    )
    view = (request.GET.get("view") or "raw").lower()
    if view not in ("raw", "category", "sku", "branch"):
        view = "raw"
    branch_id = request.GET.get("branch") or ""
    return render(
        request,
        "reports/sales_report.html",
        {
            "company_name": COMPANY_NAME,
            "view": view,
            "raw_rows": raw_rows,
            "by_category": by_category,
            "by_sku": by_sku,
            "by_branch": by_branch,
            "totals": totals,
            "branches": accessible_branches(request.user),
            "selected_branch": branch_id,
            "date_from_display": bounds["date_from"],
            "date_to_display": bounds["date_to"],
            **_period_context(bounds),
        },
    )


def _customer_stock_lines(user, customer_id=None):
    """
    Current BranchStock lines with metric tons and stock value.

    Optional customer_id filters to one customer. Respects branch access.
    """
    money = Decimal("0.01")
    stock_qs = BranchStock.objects.select_related(
        "branch", "branch__customer", "product"
    )
    if user is not None:
        stock_qs = filter_by_accessible_branches(stock_qs, user)
    if customer_id is not None:
        stock_qs = stock_qs.filter(branch__customer_id=customer_id)

    lines = []
    for row in stock_qs:
        qty = row.quantity
        unit_price = Decimal(row.product.selling_price or 0)
        stock_value = (Decimal(qty) * unit_price).quantize(
            money, rounding=ROUND_HALF_UP
        )
        tons = _tons(qty, pack_size_kg(row.product.name))
        lines.append(
            {
                "customer_id": row.branch.customer_id,
                "customer_name": row.branch.customer.name,
                "branch_id": row.branch_id,
                "branch_name": row.branch.name,
                "product_code": row.product.code,
                "product_name": row.product.name,
                "quantity": qty,
                "tons": tons,
                "unit_price": unit_price,
                "stock_value": stock_value,
                "updated_at": row.updated_at,
            }
        )
    lines.sort(
        key=lambda r: (
            r["customer_name"].lower(),
            r["branch_name"].lower(),
            r["product_code"] or "",
            r["product_name"].lower(),
        )
    )
    return lines


def customer_stock_summary_data(user=None):
    """Current stock units, metric tons, stock value, and all-time sold tonnage per customer."""
    money = Decimal("0.01")
    lines = _customer_stock_lines(user)

    by_customer = defaultdict(
        lambda: {
            "customer_name": "",
            "customer_id": None,
            "total_qty": 0,
            "tons": Decimal("0"),
            "stock_value": Decimal("0"),
            "branch_count": set(),
        }
    )
    for row in lines:
        cid = row["customer_id"]
        bucket = by_customer[cid]
        bucket["customer_id"] = cid
        bucket["customer_name"] = row["customer_name"]
        bucket["total_qty"] += row["quantity"]
        bucket["stock_value"] += row["stock_value"]
        if row["tons"] is not None:
            bucket["tons"] += row["tons"]
        bucket["branch_count"].add(row["branch_id"])

    sale_qs = SaleItem.objects.all()
    if user is not None:
        sale_qs = filter_by_accessible_branches(
            sale_qs, user, field="sale__branch_id"
        )
    sold_by_customer = defaultdict(lambda: Decimal("0"))
    sale_aggs = sale_qs.values(
        "sale__branch__customer_id",
        "product__name",
    ).annotate(qty=Sum("quantity"))
    for row in sale_aggs:
        tons = _tons(row["qty"], pack_size_kg(row["product__name"]))
        if tons is not None:
            sold_by_customer[row["sale__branch__customer_id"]] += tons

    rows = []
    for cid, vals in by_customer.items():
        rows.append(
            {
                "customer_name": vals["customer_name"],
                "customer_id": vals["customer_id"],
                "total_qty": vals["total_qty"],
                "tons": _quantize_tons(vals["tons"]),
                "stock_value": vals["stock_value"].quantize(
                    money, rounding=ROUND_HALF_UP
                ),
                "branch_count": len(vals["branch_count"]),
                "total_tons_sold": _quantize_tons(
                    sold_by_customer.get(cid, Decimal("0"))
                ),
            }
        )
    rows.sort(key=lambda r: r["customer_name"].lower())
    return rows


def customer_stock_detail_data(user, customer):
    """
    Branch and product stock breakdown for one customer.

    Returns None if the user has no accessible stock for this customer.
    """
    money = Decimal("0.01")
    lines = _customer_stock_lines(user, customer_id=customer.pk)
    if not lines:
        # Still allow detail if user can see at least one of the customer's branches.
        branches = accessible_branches(user).filter(customer=customer)
        if not branches.exists():
            return None
        return {
            "customer": customer,
            "lines": [],
            "by_branch": [],
            "totals": {
                "quantity": 0,
                "tons": Decimal("0"),
                "stock_value": Decimal("0"),
                "branch_count": branches.count(),
            },
        }

    by_branch_map = defaultdict(
        lambda: {
            "branch_name": "",
            "branch_id": None,
            "quantity": 0,
            "tons": Decimal("0"),
            "stock_value": Decimal("0"),
        }
    )
    for row in lines:
        br = by_branch_map[row["branch_id"]]
        br["branch_id"] = row["branch_id"]
        br["branch_name"] = row["branch_name"]
        br["quantity"] += row["quantity"]
        br["stock_value"] += row["stock_value"]
        if row["tons"] is not None:
            br["tons"] += row["tons"]

    by_branch = [
        {
            "branch_id": vals["branch_id"],
            "branch_name": vals["branch_name"],
            "quantity": vals["quantity"],
            "tons": _quantize_tons(vals["tons"]),
            "stock_value": vals["stock_value"].quantize(
                money, rounding=ROUND_HALF_UP
            ),
        }
        for vals in sorted(
            by_branch_map.values(), key=lambda v: v["branch_name"].lower()
        )
    ]
    totals = {
        "quantity": sum(r["quantity"] for r in lines),
        "tons": _quantize_tons(
            sum((r["tons"] or Decimal("0") for r in lines), Decimal("0"))
        ),
        "stock_value": sum((r["stock_value"] for r in lines), Decimal("0")).quantize(
            money, rounding=ROUND_HALF_UP
        ),
        "branch_count": len(by_branch),
    }
    return {
        "customer": customer,
        "lines": lines,
        "by_branch": by_branch,
        "totals": totals,
    }


@login_required
def customer_stock_summary(request):
    rows = customer_stock_summary_data(request.user)
    money = Decimal("0.01")
    totals = {
        "quantity": sum(r["total_qty"] for r in rows),
        "tons": _quantize_tons(
            sum((r["tons"] for r in rows), Decimal("0"))
        ),
        "stock_value": sum((r["stock_value"] for r in rows), Decimal("0")).quantize(
            money, rounding=ROUND_HALF_UP
        ),
        "customers": len(rows),
    }
    return render(
        request,
        "reports/customer_stock.html",
        {"rows": rows, "totals": totals},
    )


@login_required
def customer_stock_detail(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    data = customer_stock_detail_data(request.user, customer)
    if data is None:
        raise Http404("Customer stock not found.")
    return render(
        request,
        "reports/customer_stock_detail.html",
        data,
    )


@login_required
def sync_report(request):
    bounds = parse_period_bounds(request)
    branches = accessible_branches(request.user)
    logs = filter_by_accessible_branches(
        SyncLog.objects.select_related("branch").filter(
            created_at__gte=bounds["start_dt"], created_at__lt=bounds["end_dt"]
        ),
        request.user,
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


def stock_movement_data(request):
    """
    Period stock movement per customer or branch × product.

    Closing = current BranchStock rolled back to period end
              (on-hand − In after period + Out after period)
    Opening = Closing − In + Out
              (In = dispatched, Out = sold + shrinkage/damaged)
    """
    bounds = parse_period_bounds(request)
    start_dt = bounds["start_dt"]
    end_dt = bounds["end_dt"]
    group_by = request.GET.get("group_by") or "branch"
    if group_by not in ("branch", "customer"):
        group_by = "branch"

    customer_id = request.GET.get("customer") or ""
    branch_id = request.GET.get("branch") or ""

    allowed = accessible_branches(request.user)
    customers = Customer.objects.filter(
        pk__in=allowed.values_list("customer_id", flat=True)
    ).distinct()
    branches = allowed
    if customer_id.isdigit():
        branches = branches.filter(customer_id=int(customer_id))

    dispatch_base = filter_by_accessible_branches(
        DispatchItem.objects.filter(
            dispatch__status=Dispatch.Status.APPROVED,
            dispatch__approved_at__isnull=False,
        ),
        request.user,
        field="dispatch__branch_id",
    )
    sale_base = filter_by_accessible_branches(
        SaleItem.objects.all(),
        request.user,
        field="sale__branch_id",
    )
    loss_base = filter_by_accessible_branches(StockLoss.objects.all(), request.user)

    if customer_id.isdigit():
        cid = int(customer_id)
        dispatch_base = dispatch_base.filter(dispatch__customer_id=cid)
        sale_base = sale_base.filter(sale__branch__customer_id=cid)
        loss_base = loss_base.filter(branch__customer_id=cid)
    if branch_id.isdigit():
        bid = int(branch_id)
        if not user_can_access_branch(request.user, bid):
            dispatch_base = dispatch_base.none()
            sale_base = sale_base.none()
            loss_base = loss_base.none()
        else:
            dispatch_base = dispatch_base.filter(dispatch__branch_id=bid)
            sale_base = sale_base.filter(sale__branch_id=bid)
            loss_base = loss_base.filter(branch_id=bid)

    if group_by == "customer":
        d_key = ("dispatch__customer_id", "product_id")
        s_key = ("sale__branch__customer_id", "product_id")
        l_key = ("branch__customer_id", "product_id")
        stock_key = ("branch__customer_id", "product_id")
    else:
        d_key = ("dispatch__customer_id", "dispatch__branch_id", "product_id")
        s_key = ("sale__branch__customer_id", "sale__branch_id", "product_id")
        l_key = ("branch__customer_id", "branch_id", "product_id")
        stock_key = ("branch__customer_id", "branch_id", "product_id")

    def _bucket(qs, values_fields, qty_field="quantity"):
        return {
            tuple(row[f] for f in values_fields): row["total"] or 0
            for row in qs.values(*values_fields).annotate(total=Sum(qty_field))
        }

    stock_qs = filter_by_accessible_branches(BranchStock.objects.all(), request.user)
    if customer_id.isdigit():
        stock_qs = stock_qs.filter(branch__customer_id=int(customer_id))
    if branch_id.isdigit():
        bid = int(branch_id)
        if user_can_access_branch(request.user, bid):
            stock_qs = stock_qs.filter(branch_id=bid)
        else:
            stock_qs = stock_qs.none()

    stock_now = _bucket(stock_qs, stock_key)
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
    dispatched_after = _bucket(
        dispatch_base.filter(dispatch__approved_at__gte=end_dt),
        d_key,
    )
    sold_after = _bucket(
        sale_base.filter(sale__sold_at__gte=end_dt),
        s_key,
    )
    loss_after = _bucket(
        loss_base.filter(recorded_at__gte=end_dt),
        l_key,
    )

    all_keys = set()
    all_keys.update(stock_now)
    all_keys.update(dispatched_in)
    all_keys.update(sold_in)
    all_keys.update(loss_in)
    all_keys.update(dispatched_after)
    all_keys.update(sold_after)
    all_keys.update(loss_after)

    totals = {}
    for key in all_keys:
        dispatched = dispatched_in.get(key, 0)
        sold = sold_in.get(key, 0)
        shrinkage = loss_in.get(key, 0)
        closing = (
            stock_now.get(key, 0)
            - dispatched_after.get(key, 0)
            + sold_after.get(key, 0)
            + loss_after.get(key, 0)
        )
        opening = closing - dispatched + sold + shrinkage
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

    return {
        "bounds": bounds,
        "rows": rows,
        "group_by": group_by,
        "customers": customers,
        "branches": branches,
        "selected_customer": customer_id,
        "selected_branch": branch_id,
    }


@login_required
def stock_movement(request):
    data = stock_movement_data(request)
    bounds = data.pop("bounds")
    return render(
        request,
        "reports/stock_movement.html",
        {
            **data,
            **_period_context(bounds),
        },
    )


def dispatch_warehouse_data(request):
    """
    Received (stockist-confirmed) dispatch quantities, grouped by product category.
    Returns (bounds, groups, grand_total).
    """
    bounds = parse_period_bounds(request)
    start_dt = bounds["start_dt"]
    end_dt = bounds["end_dt"]

    items = (
        filter_by_accessible_branches(
            DispatchItem.objects.filter(
                dispatch__status=Dispatch.Status.APPROVED,
                dispatch__approved_at__isnull=False,
                dispatch__approved_at__gte=start_dt,
                dispatch__approved_at__lt=end_dt,
            ),
            request.user,
            field="dispatch__branch_id",
        )
        .values(
            "product__category",
            "product__code",
            "product__name",
            "dispatch__customer__name",
            "dispatch__branch__name",
        )
        .annotate(quantity=Sum("quantity"))
        .order_by(
            "product__category",
            "product__code",
            "dispatch__customer__name",
            "dispatch__branch__name",
        )
    )

    grouped = defaultdict(lambda: {"rows": [], "total_qty": 0})
    for row in items:
        category = _normalize_category(row["product__category"])
        qty = row["quantity"] or 0
        grouped[category]["rows"].append(
            {
                "code": row["product__code"],
                "name": row["product__name"],
                "customer": row["dispatch__customer__name"],
                "branch": row["dispatch__branch__name"],
                "quantity": qty,
            }
        )
        grouped[category]["total_qty"] += qty

    groups = [
        {
            "category": category,
            "rows": data["rows"],
            "total_qty": data["total_qty"],
        }
        for category, data in sorted(
            grouped.items(), key=lambda item: item[0].lower()
        )
    ]
    grand_total = sum(g["total_qty"] for g in groups)
    return bounds, groups, grand_total


@login_required
def dispatch_warehouse_report(request):
    bounds, groups, grand_total = dispatch_warehouse_data(request)
    return render(
        request,
        "reports/dispatch_warehouse.html",
        {
            "groups": groups,
            "grand_total": grand_total,
            **_period_context(bounds),
        },
    )


def _sold_tons_by_customer(start_dt, end_dt, user=None):
    """Portal sold tonnage per customer name for [start_dt, end_dt)."""
    sold = defaultdict(lambda: Decimal("0"))
    display_names = {}
    sale_qs = SaleItem.objects.filter(
        sale__sold_at__gte=start_dt,
        sale__sold_at__lt=end_dt,
    )
    if user is not None:
        sale_qs = filter_by_accessible_branches(
            sale_qs, user, field="sale__branch_id"
        )
    sale_aggs = sale_qs.values(
        "sale__branch__customer__name", "product__name"
    ).annotate(qty=Sum("quantity"))
    for row in sale_aggs:
        name = row["sale__branch__customer__name"] or "Unknown"
        key = name.casefold()
        display_names[key] = name
        tons = _tons(row["qty"], pack_size_kg(row["product__name"]))
        if tons is not None:
            sold[key] += tons
    return sold, display_names


def customer_tonnage_data(request):
    """
    Purchased tonnage (POS GRV) vs sold tonnage (portal sales) per customer.

    Purchased tons = QuantityTotalKG/1000 when POS recorded kg, otherwise
    qty × pack kg (from item name) / 1000.
    Sold tons use the same pack-size formula against portal SaleItem rows.
    Rows are merged by case-insensitive customer / GRV party name.
    """
    bounds = parse_period_bounds(request)
    try:
        lines = fetch_grv_purchase_lines(bounds["start_dt"], bounds["end_dt"])
        error = None
    except PosDbError as exc:
        lines = []
        error = str(exc)

    by_customer = defaultdict(
        lambda: {
            "customer_name": "",
            "quantity": Decimal("0"),
            "purchased_tons": Decimal("0"),
            "sold_tons": Decimal("0"),
            "amount": Decimal("0"),
            "grv_ids": set(),
            "lines": 0,
        }
    )

    for line in lines:
        name = line["customer_name"] or "Unknown"
        key = name.casefold()
        qty = Decimal(str(line["quantity"] or 0))
        qty_kg = Decimal(str(line["quantity_kg"] or 0))
        unit_price = Decimal(str(line["unit_price"] or 0))
        amount = (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if qty_kg > 0:
            tons = _quantize_tons(qty_kg / Decimal(1000))
        else:
            tons = _tons(qty, pack_size_kg(line["item_name"])) or Decimal("0")

        bucket = by_customer[key]
        bucket["customer_name"] = name
        bucket["quantity"] += qty
        bucket["purchased_tons"] += tons
        bucket["amount"] += amount
        bucket["grv_ids"].add(line["grv_id"])
        bucket["lines"] += 1

    sold_by_key, sold_names = _sold_tons_by_customer(
        bounds["start_dt"], bounds["end_dt"], user=request.user
    )
    for key, tons in sold_by_key.items():
        bucket = by_customer[key]
        if not bucket["customer_name"]:
            bucket["customer_name"] = sold_names.get(key, key)
        bucket["sold_tons"] = tons

    rows = []
    total_purchased = Decimal("0")
    total_sold = Decimal("0")
    total_qty = Decimal("0")
    total_amount = Decimal("0")

    for data in by_customer.values():
        qty = data["quantity"]
        purchased = _quantize_tons(data["purchased_tons"])
        sold = _quantize_tons(data["sold_tons"])
        amount = data["amount"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rows.append(
            {
                "customer_name": data["customer_name"],
                "quantity": qty.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if qty % 1
                else int(qty),
                "purchased_tons": purchased,
                "sold_tons": sold,
                "variance_tons": _quantize_tons(purchased - sold),
                "amount": amount,
                "grv_count": len(data["grv_ids"]),
                "line_count": data["lines"],
            }
        )
        total_purchased += purchased
        total_sold += sold
        total_qty += qty
        total_amount += amount

    rows.sort(
        key=lambda r: (
            -r["purchased_tons"],
            -r["sold_tons"],
            r["customer_name"].lower(),
        )
    )

    totals = {
        "purchased_tons": _quantize_tons(total_purchased),
        "sold_tons": _quantize_tons(total_sold),
        "variance_tons": _quantize_tons(total_purchased - total_sold),
        "quantity": total_qty.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if total_qty % 1
        else int(total_qty),
        "amount": total_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "customers": len(rows),
    }
    return bounds, rows, totals, error


@login_required
def customer_tonnage_report(request):
    bounds, rows, totals, error = customer_tonnage_data(request)
    return render(
        request,
        "reports/customer_tonnage.html",
        {
            "rows": rows,
            "totals": totals,
            "error": error,
            "company_name": COMPANY_NAME,
            "date_from_display": bounds["date_from"],
            "date_to_display": bounds["date_to"],
            **_period_context(bounds),
        },
    )
