import csv
import io
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font

from catalog.models import Branch, Customer, Product
from django.db.models import Sum
from inventory.models import BranchStock, Dispatch, DispatchItem, StockLoss
from reports.period import parse_period_bounds
from sales.models import SaleItem, SyncLog


def _make_response(filename, content_type):
    resp = HttpResponse(content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _write_csv(rows, headers):
    resp = _make_response(
        f"report_{datetime.now():%Y%m%d_%H%M%S}.csv", "text/csv"
    )
    writer = csv.writer(resp)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return resp


def _write_xlsx(rows, headers, sheet_title="Report"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    resp = _make_response(
        f"report_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    wb.save(resp)
    return resp


def _export(request, headers, rows, sheet_title="Report"):
    fmt = request.GET.get("format", "csv")
    if fmt == "xlsx":
        return _write_xlsx(rows, headers, sheet_title)
    return _write_csv(rows, headers)


# ── Stock balance ──────────────────────────────────────────────

@login_required
def export_stock_balance(request):
    stocks = BranchStock.objects.select_related(
        "branch", "branch__customer", "product"
    )
    headers = ["Customer", "Branch", "Code", "Product", "Qty"]
    rows = [
        [s.branch.customer.name, s.branch.name, s.product.code, s.product.name, s.quantity]
        for s in stocks
    ]
    return _export(request, headers, rows, "Stock Balance")


# ── Dispatch report ────────────────────────────────────────────

@login_required
def export_dispatches(request):
    bounds = parse_period_bounds(request)
    dispatches = (
        Dispatch.objects.select_related("customer", "branch")
        .prefetch_related("items__product")
        .filter(created_at__gte=bounds["start_dt"], created_at__lt=bounds["end_dt"])
    )
    status = request.GET.get("status")
    if status:
        dispatches = dispatches.filter(status=status)
    headers = ["Reference", "Customer", "Branch", "Status", "Items"]
    rows = [
        [
            d.reference,
            d.customer.name,
            d.branch.name,
            d.get_status_display(),
            ", ".join(f"{i.product.code}×{i.quantity}" for i in d.items.all()),
        ]
        for d in dispatches
    ]
    return _export(request, headers, rows, "Dispatches")


# ── Sales report (VastAfrica template) ─────────────────────────

def _sales_raw_row(r):
    d = r["date"]
    date_str = f"{d.day}/{d.month}/{d.year}" if d else ""
    return [
        date_str,
        r["item_description"],
        r["group"],
        r["sale_reporting_group"],
        r["account"],
        r["customer_name"],
        float(r["tons"]) if r["tons"] is not None else "",
        r["quantity"],
        float(r["amount"]),
        float(r["unit_price"]),
        r["branch"],
        float(r["psize"]) if r["psize"] is not None else "",
    ]


def _write_sales_xlsx(bounds, raw_rows, by_category, by_sku, by_branch, totals):
    from reports.views import COMPANY_NAME

    wb = Workbook()

    ws_cat = wb.active
    ws_cat.title = "Sales-By-Category"
    ws_cat.append(["Group", "(All)"])
    ws_cat.append([])
    ws_cat.append(["Product Category", "Volume"])
    for cell in ws_cat[3]:
        cell.font = Font(bold=True)
    for r in by_category:
        ws_cat.append([r["label"], float(r["volume"])])
    ws_cat.append(["Grand Total", float(totals["tons"])])
    ws_cat["A" + str(ws_cat.max_row)].font = Font(bold=True)

    ws_sku = wb.create_sheet("Sales-by-SKU")
    ws_sku.append(["PSize", "(All)"])
    ws_sku.append([])
    ws_sku.append(["Product Name", "Volume"])
    for cell in ws_sku[3]:
        cell.font = Font(bold=True)
    for r in by_sku:
        ws_sku.append([r["label"], float(r["volume"])])
    ws_sku.append(["Grand Total", float(totals["tons"])])
    ws_sku["A" + str(ws_sku.max_row)].font = Font(bold=True)

    ws_br = wb.create_sheet("Sales-by-Branch")
    ws_br.append(["Date", "(All)"])
    ws_br.append([])
    ws_br.append(["Branch Sales", "Volume"])
    for cell in ws_br[3]:
        cell.font = Font(bold=True)
    for r in by_branch:
        ws_br.append([r["label"], float(r["volume"])])
    ws_br.append(["Grand Total", float(totals["tons"])])
    ws_br["A" + str(ws_br.max_row)].font = Font(bold=True)

    ws_raw = wb.create_sheet("Raw-Data")
    ws_raw["C1"] = "Inventory Transactions"
    ws_raw["C1"].font = Font(bold=True)
    ws_raw["C2"] = COMPANY_NAME
    ws_raw["A3"] = "Date From :"
    ws_raw["B4"] = f"{bounds['date_from'].day}/{bounds['date_from'].month}/{bounds['date_from'].year}"
    ws_raw["A5"] = "Date To :"
    ws_raw["B5"] = f"{bounds['date_to'].day}/{bounds['date_to'].month}/{bounds['date_to'].year}"
    ws_raw["A6"] = "Inventory Transactions"
    headers = [
        "Date",
        "Item Description",
        "Group",
        "Sale Reporting Group",
        "Account",
        "Customer/ Supplier Name",
        "Tons",
        "Quantity",
        "Amount",
        "Unit Price",
        "Branch",
        "PSize",
    ]
    for col, header in enumerate(headers, 1):
        cell = ws_raw.cell(row=7, column=col, value=header)
        cell.font = Font(bold=True)
    for row_idx, r in enumerate(raw_rows, start=8):
        for col_idx, value in enumerate(_sales_raw_row(r), start=1):
            ws_raw.cell(row=row_idx, column=col_idx, value=value)

    resp = _make_response(
        f"sales_report_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    wb.save(resp)
    return resp


@login_required
def export_sales(request):
    from reports.views import sales_report_data

    bounds, raw_rows, by_category, by_sku, by_branch, totals = sales_report_data(
        request
    )
    fmt = request.GET.get("format", "csv")
    if fmt == "xlsx":
        return _write_sales_xlsx(
            bounds, raw_rows, by_category, by_sku, by_branch, totals
        )

    headers = [
        "Date",
        "Item Description",
        "Group",
        "Sale Reporting Group",
        "Account",
        "Customer/ Supplier Name",
        "Tons",
        "Quantity",
        "Amount",
        "Unit Price",
        "Branch",
        "PSize",
    ]
    rows = [_sales_raw_row(r) for r in raw_rows]
    return _write_csv(rows, headers)


# ── Customer stock summary ─────────────────────────────────────

@login_required
def export_customer_stock(request):
    from reports.views import customer_stock_summary_data

    data = customer_stock_summary_data()
    headers = ["Customer", "Total Units", "Total Tons Sold"]
    rows = [
        [r["customer_name"], r["total_qty"], float(r["total_tons_sold"])]
        for r in data
    ]
    return _export(request, headers, rows, "Customer Stock")


# ── Sync report ────────────────────────────────────────────────

@login_required
def export_sync(request):
    bounds = parse_period_bounds(request)
    logs = SyncLog.objects.select_related("branch").filter(
        created_at__gte=bounds["start_dt"], created_at__lt=bounds["end_dt"]
    )[:100]
    headers = ["When", "Branch", "Status", "Sale ID", "Message"]
    rows = [
        [
            str(l.created_at),
            l.branch.name,
            l.get_status_display(),
            l.external_sale_id,
            l.message,
        ]
        for l in logs
    ]
    return _export(request, headers, rows, "Sync Report")


# ── Stock movement ─────────────────────────────────────────────

@login_required
def export_stock_movement(request):
    from reports.views import stock_movement as _sm_view
    from collections import defaultdict

    bounds = parse_period_bounds(request)
    start_dt = bounds["start_dt"]
    end_dt = bounds["end_dt"]
    group_by = request.GET.get("group_by") or "branch"
    if group_by not in ("branch", "customer"):
        group_by = "branch"

    customer_id = request.GET.get("customer") or ""
    branch_id = request.GET.get("branch") or ""

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

    stock_qs = BranchStock.objects.all()
    if customer_id.isdigit():
        stock_qs = stock_qs.filter(branch__customer_id=int(customer_id))
    if branch_id.isdigit():
        stock_qs = stock_qs.filter(branch_id=int(branch_id))

    if group_by == "customer":
        stock_balance = _bucket(stock_qs, ("branch__customer_id", "product_id"))
    else:
        stock_balance = _bucket(stock_qs, ("branch__customer_id", "branch_id", "product_id"))

    dispatched_in = _bucket(
        dispatch_base.filter(dispatch__approved_at__gte=start_dt, dispatch__approved_at__lt=end_dt),
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

    all_keys = set()
    all_keys.update(stock_balance)
    all_keys.update(dispatched_in)
    all_keys.update(sold_in)
    all_keys.update(loss_in)

    product_ids = {k[-1] for k in all_keys}
    customer_ids = {k[0] for k in all_keys}
    products = {p.pk: p for p in Product.objects.filter(pk__in=product_ids)}
    customer_map = {c.pk: c for c in Customer.objects.filter(pk__in=customer_ids)}
    branch_map = {}
    if group_by == "branch":
        branch_ids = {k[1] for k in all_keys}
        branch_map = {b.pk: b for b in Branch.objects.filter(pk__in=branch_ids)}

    if group_by == "branch":
        headers = ["Customer", "Branch", "Code", "Product", "Opening", "Dispatched", "Sold", "Shrinkage", "Closing"]
    else:
        headers = ["Customer", "Code", "Product", "Opening", "Dispatched", "Sold", "Shrinkage", "Closing"]

    export_rows = []
    for key in sorted(all_keys):
        product = products.get(key[-1])
        customer = customer_map.get(key[0])
        if not product or not customer:
            continue
        opening = stock_balance.get(key, 0)
        dispatched = dispatched_in.get(key, 0)
        sold = sold_in.get(key, 0)
        shrinkage = loss_in.get(key, 0)
        closing = opening + dispatched - sold - shrinkage
        if opening == dispatched == sold == shrinkage == closing == 0:
            continue
        row = [customer.name]
        if group_by == "branch":
            branch = branch_map.get(key[1])
            if not branch:
                continue
            row.append(branch.name)
        row.extend([product.code, product.name, opening, dispatched, sold, shrinkage, closing])
        export_rows.append(row)

    return _export(request, headers, export_rows, "Stock Movement")


# ── Dispatch warehouse ─────────────────────────────────────────

@login_required
def export_dispatch_warehouse(request):
    from reports.views import dispatch_warehouse_data

    _bounds, groups, _grand_total = dispatch_warehouse_data(request)
    headers = ["Category", "Code", "Product", "Customer", "Branch", "Qty dispatched"]
    export_rows = []
    for group in groups:
        for r in group["rows"]:
            export_rows.append(
                [
                    group["category"],
                    r["code"],
                    r["name"],
                    r["customer"],
                    r["branch"],
                    r["quantity"],
                ]
            )
        export_rows.append(
            [group["category"], "", "Category total", "", "", group["total_qty"]]
        )
    return _export(request, headers, export_rows, "Dispatch Warehouse")


# ── Customer tonnage (POS GRV) ─────────────────────────────────

@login_required
def export_customer_tonnage(request):
    from reports.views import customer_tonnage_data

    _bounds, data, totals, error = customer_tonnage_data(request)

    headers = [
        "Customer",
        "Purchased Units",
        "Purchased Tons",
        "Sold Tons",
        "Variance Tons",
        "Amount",
        "GRV Count",
        "Line Count",
    ]
    rows = [
        [
            r["customer_name"],
            r["quantity"],
            float(r["purchased_tons"]),
            float(r["sold_tons"]),
            float(r["variance_tons"]),
            float(r["amount"]),
            r["grv_count"],
            r["line_count"],
        ]
        for r in data
    ]
    if not rows and error:
        return _export(request, ["Error"], [[error]], "Customer Tonnage")

    rows.append(
        [
            "Grand Total",
            totals["quantity"],
            float(totals["purchased_tons"]),
            float(totals["sold_tons"]),
            float(totals["variance_tons"]),
            float(totals["amount"]),
            "",
            "",
        ]
    )
    return _export(request, headers, rows, "Customer Tonnage")
