"""Parse dispatch product lines from an uploaded Excel workbook."""

from __future__ import annotations

import re
from io import BytesIO

import openpyxl

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from catalog.models import Product

TEMPLATE_HEADERS = ("Product Code", "Product Name", "Quantity")


def build_dispatch_template() -> bytes:
    """Return an .xlsx template with headers and one example row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dispatch"
    ws.append(list(TEMPLATE_HEADERS))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    # Example row — replace with real product codes before upload.
    ws.append(["F-EXAMPLE25KG", "Example product (replace me)", 10])
    ws.append(["", "", ""])
    widths = (18, 40, 12)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


_CODE_HEADERS = {
    "code",
    "product code",
    "product_code",
    "sku",
    "item code",
    "itemcode",
    "barcode",
    "product barcode",
}
_QTY_HEADERS = {
    "qty",
    "quantity",
    "units",
    "qty dispatched",
    "dispatch qty",
    "amount",
}
_NAME_HEADERS = {
    "name",
    "product",
    "product name",
    "description",
    "item",
    "item description",
}


def _norm_header(value) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _as_int_qty(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if value <= 0:
            return None
        return int(value) if value == int(value) else None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    if num <= 0 or num != int(num):
        return None
    return int(num)


def _detect_header_row(rows: list[tuple]) -> tuple[int, dict[str, int]] | None:
    """Return (row_index, {code|qty|name: col_index}) for the first header-like row."""
    for idx, row in enumerate(rows[:30]):
        mapping: dict[str, int] = {}
        for col, cell in enumerate(row):
            header = _norm_header(cell)
            if not header:
                continue
            if header in _CODE_HEADERS and "code" not in mapping:
                mapping["code"] = col
            elif header in _QTY_HEADERS and "qty" not in mapping:
                mapping["qty"] = col
            elif header in _NAME_HEADERS and "name" not in mapping:
                mapping["name"] = col
        if "code" in mapping and "qty" in mapping:
            return idx, mapping
    return None


def parse_dispatch_excel(file_obj) -> dict:
    """
    Parse an Excel file into matched / unmatched product lines.

    Expected columns (flexible headers): Product Code | Quantity
    Optional: Product Name / Description

    Returns:
      {
        "lines": [{code, name, quantity, product_id, product_label, matched, error}],
        "matched_count": int,
        "error_count": int,
      }
    """
    data = file_obj.read() if hasattr(file_obj, "read") else file_obj
    wb = openpyxl.load_workbook(BytesIO(data), data_only=True, read_only=True)
    ws = wb.active
    rows = [tuple(cell for cell in row) for row in ws.iter_rows(values_only=True)]
    wb.close()
    if not rows:
        raise ValueError("The Excel file is empty.")

    detected = _detect_header_row(rows)
    if not detected:
        raise ValueError(
            "Could not find header columns. Include 'Product Code' (or Code/SKU/Barcode) "
            "and 'Quantity' (or Qty)."
        )
    header_idx, cols = detected
    data_rows = rows[header_idx + 1 :]

    # Preload active products for matching.
    products = list(Product.objects.filter(status=Product.Status.ACTIVE))
    by_code = {p.code.strip().upper(): p for p in products if p.code}
    by_barcode = {
        (p.barcode or "").strip().upper(): p
        for p in products
        if (p.barcode or "").strip()
    }

    lines = []
    for row in data_rows:
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        raw_code = row[cols["code"]] if cols["code"] < len(row) else None
        raw_qty = row[cols["qty"]] if cols["qty"] < len(row) else None
        raw_name = (
            row[cols["name"]]
            if "name" in cols and cols["name"] < len(row)
            else None
        )
        code = str(raw_code or "").strip()
        if not code or code.lower() in ("none", "nan"):
            continue
        # Skip category / section header rows (no qty).
        qty = _as_int_qty(raw_qty)
        name_hint = str(raw_name or "").strip()

        line = {
            "code": code,
            "name": name_hint,
            "quantity": qty or 0,
            "product_id": None,
            "product_label": "",
            "matched": False,
            "error": "",
        }
        if qty is None:
            line["error"] = "Invalid or missing quantity"
            lines.append(line)
            continue

        key = code.upper()
        product = by_code.get(key) or by_barcode.get(key)
        if not product:
            line["error"] = "Product not found"
            lines.append(line)
            continue

        line["matched"] = True
        line["product_id"] = product.pk
        line["product_label"] = f"{product.code} — {product.name}"
        line["name"] = product.name
        line["code"] = product.code
        lines.append(line)

    # Merge duplicate product lines by summing quantities.
    merged: dict[int, dict] = {}
    unmatched = []
    for line in lines:
        if not line["matched"]:
            unmatched.append(line)
            continue
        pid = line["product_id"]
        if pid in merged:
            merged[pid]["quantity"] += line["quantity"]
        else:
            merged[pid] = line

    result_lines = list(merged.values()) + unmatched
    matched_count = sum(1 for line in result_lines if line["matched"])
    error_count = sum(1 for line in result_lines if not line["matched"])
    return {
        "lines": result_lines,
        "matched_count": matched_count,
        "error_count": error_count,
    }
