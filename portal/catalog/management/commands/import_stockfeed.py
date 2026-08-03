"""Import products from data/Product Codes Stockfeed.xlsx."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Product

EXCEL_CANDIDATES = (
    # Shipped with portal package (production)
    Path(__file__).resolve().parents[3] / "data" / "Product Codes Stockfeed.xlsx",
    # Repo root data/ (development)
    Path(__file__).resolve().parents[4] / "data" / "Product Codes Stockfeed.xlsx",
)


def resolve_excel_default() -> Path:
    for path in EXCEL_CANDIDATES:
        if path.exists():
            return path
    return EXCEL_CANDIDATES[0]


EXCEL_DEFAULT = resolve_excel_default()

CATEGORY_MAP = {
    "POULTRYBROILERS": "Poultry Broilers",
    "POULTRYLAYERS": "Poultry Layers",
    "POULTRYROADRUNNERS": "Poultry Road Runners",
    "PIG": "Pig",
    "CATTLEDAIRY": "Cattle Dairy",
    "CATTLEBEEF": "Cattle Beef",
    "ZIMGOLDPRODUCTS": "Zimgold Products",
    "OFFALS": "Offals",
}

CODE_FIXES = {
    ("F-BGP5KG3PH", "25KG"): "F-BGP25KG3PH",
    ("F-RRBM50KG", "Breeder"): "F-RRBRM50KG",
    ("F-RRBM50KG", "Con"): "F-RRCON50KG",
    ("F-PC5KG", "Calf starter"): "F-CS50KG",
    ("F-PC10KG", "Calf Grower"): "F-CG50KG",
}

BRAND_CODE_PREFIX = {
    "sunrise": "SR",
    "pureoil": "ZG",
}


def normalize_category(raw: str) -> str | None:
    compact = re.sub(r"\s+", "", (raw or "").upper())
    return CATEGORY_MAP.get(compact)


def is_category_header(code: str, desc: str) -> bool:
    if desc and str(desc).strip():
        return False
    text = str(code or "")
    letters = re.findall(r"[A-Za-z]", text)
    spaces = text.count(" ")
    return len(letters) >= 3 and spaces >= 3


def slug_code(prefix: str, name: str, used: set[str]) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name.upper())
    base = prefix + "-" + "".join(w[:4] for w in words[:4])
    base = base[:18]
    candidate = base
    n = 2
    while candidate in used:
        suffix = f"-{n}"
        candidate = (base[: 20 - len(suffix)] + suffix)[:20]
        n += 1
    return candidate


def fix_code(raw_code: str, name: str, used: set[str]) -> str:
    code = (raw_code or "").strip()
    name_s = name or ""

    for (bad, needle), fixed in CODE_FIXES.items():
        if code.upper() == bad.upper() and needle.lower() in name_s.lower():
            code = fixed
            break

    brand_key = code.lower().rstrip()
    if brand_key in BRAND_CODE_PREFIX:
        code = slug_code(BRAND_CODE_PREFIX[brand_key], name_s, used)

    if not code or code.lower() in ("none", "nan"):
        code = slug_code("GEN", name_s, used)

    code = code[:20]
    if code in used:
        base = code[:17]
        n = 2
        while f"{base}-{n}"[:20] in used:
            n += 1
        code = f"{base}-{n}"[:20]
    return code


def parse_products(excel_path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb["Product Codes"]
    category = "Uncategorized"
    products: list[dict] = []
    used_codes: set[str] = set()

    for row in ws.iter_rows(min_row=7, values_only=True):
        raw_code = row[0]
        name = row[1]
        price = row[2]

        if raw_code is None and name is None:
            continue

        code_s = str(raw_code).strip() if raw_code is not None else ""
        name_s = str(name).strip() if name is not None else ""

        upper = code_s.upper()
        if (
            upper.startswith("TOTAL")
            or upper.startswith("PREPARED")
            or upper.startswith("VERIFIED")
            or upper.startswith("NB:")
        ):
            break
        if not code_s and not name_s:
            continue

        if is_category_header(code_s, name_s):
            cat = normalize_category(code_s)
            if cat:
                category = cat
            continue

        if code_s.lower().startswith("sunrise") and category in (
            "Cattle Beef",
            "Uncategorized",
        ):
            if any(
                word in name_s.lower()
                for word in ("flour", "meal", "roller")
            ):
                category = "Sunrise Flour"

        if not name_s:
            continue

        code = fix_code(code_s, name_s, used_codes)
        used_codes.add(code)

        try:
            price_d = (
                Decimal(str(price)) if price not in (None, "") else Decimal("0")
            )
        except (InvalidOperation, ValueError):
            price_d = Decimal("0")

        products.append(
            {
                "code": code,
                "name": name_s,
                "category": category,
                "selling_price": price_d,
            }
        )

    return products


class Command(BaseCommand):
    help = "Import category/code/name/price from Product Codes Stockfeed.xlsx"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(EXCEL_DEFAULT),
            help="Path to Product Codes Stockfeed.xlsx",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing products (and protected dependents) first",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        excel_path = Path(options["file"])
        if not excel_path.exists():
            raise CommandError(f"Excel file not found: {excel_path}")

        if options["clear"]:
            from inventory.models import BranchStock, DispatchItem, StockLoss
            from sales.models import SaleItem

            SaleItem.objects.all().delete()
            DispatchItem.objects.all().delete()
            StockLoss.objects.all().delete()
            BranchStock.objects.all().delete()
            deleted, _ = Product.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} products."))

        rows = parse_products(excel_path)
        if not rows:
            raise CommandError("No products parsed from spreadsheet.")

        created = updated = 0
        for row in rows:
            _, was_created = Product.objects.update_or_create(
                code=row["code"],
                defaults={
                    "name": row["name"],
                    "category": row["category"],
                    "barcode": row["code"],
                    "unit": "bag",
                    "selling_price": row["selling_price"],
                    "cost_price": row["selling_price"],
                    "status": Product.Status.ACTIVE,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(rows)} products ({created} created, {updated} updated)."
            )
        )
        cats = (
            Product.objects.order_by("category")
            .values_list("category", flat=True)
            .distinct()
        )
        for cat in cats:
            n = Product.objects.filter(category=cat).count()
            self.stdout.write(f"  {cat}: {n}")
