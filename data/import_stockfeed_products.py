"""
Import Product Codes Stockfeed.xlsx into SQL Server pricing DB
(StockItem + Catalogue + prices + warehouse links).
"""
from __future__ import annotations

import re
from decimal import Decimal

import openpyxl
import pyodbc

EXCEL = r"d:\Roysen\GitHub\eduratesys\data\Product Codes Stockfeed.xlsx"
CONN = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DEVELOPER03\\SQLEXPRESS2012;"
    "Database=pricing;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

# Category header rows are spaced-out titles in column A
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

# Known bad/duplicate codes from the spreadsheet → corrected unique codes
CODE_FIXES = {
    # (raw_code, description_contains) -> fixed_code
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
    # Spaced letter headers like "P  O  U  L  T  R  Y ..."
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
        # Duplicate remaining codes get numeric suffix
        base = code[:17]
        n = 2
        while f"{base}-{n}"[:20] in used:
            n += 1
        code = f"{base}-{n}"[:20]
    return code


def parse_products():
    wb = openpyxl.load_workbook(EXCEL, data_only=True)
    ws = wb["Product Codes"]
    category = "Uncategorized"
    products = []
    used_codes: set[str] = set()

    for row in ws.iter_rows(min_row=7, values_only=True):
        raw_code = row[0]
        name = row[1]
        price = row[2]
        sku = row[3]

        if raw_code is None and name is None:
            continue

        code_s = str(raw_code).strip() if raw_code is not None else ""
        name_s = str(name).strip() if name is not None else ""

        # Skip totals / signature rows
        upper = code_s.upper()
        if upper.startswith("TOTAL") or upper.startswith("PREPARED") or upper.startswith("VERIFIED") or upper.startswith("NB:"):
            break
        if not code_s and not name_s:
            continue

        if is_category_header(code_s, name_s):
            cat = normalize_category(code_s)
            if cat:
                category = cat
            continue

        # Sunrise flour block has no category header — detect by brand
        if code_s.lower().startswith("sunrise") and category in (
            "Cattle Beef",
            "Uncategorized",
        ):
            # flour products sit after cattle beef without a header
            if "flour" in name_s.lower() or "meal" in name_s.lower() or "roller" in name_s.lower():
                category = "Sunrise Flour"

        if not name_s:
            continue

        code = fix_code(code_s, name_s, used_codes)
        used_codes.add(code)

        try:
            price_d = Decimal(str(price)) if price not in (None, "") else Decimal("0")
        except Exception:
            price_d = Decimal("0")

        try:
            sku_d = Decimal(str(sku)) if sku not in (None, "") else Decimal("0")
        except Exception:
            sku_d = Decimal("0")

        vat_inc = "vat inc" in name_s.lower()
        products.append(
            {
                "code": code,
                "name": name_s,
                "category": category,
                "price": price_d,
                "sku": sku_d,
                "vat_standard": vat_inc,
            }
        )

    return products


def get_or_create_stock_group(cur, name: str, cache: dict[str, int]) -> int:
    if name in cache:
        return cache[name]
    cur.execute(
        "SELECT StockGroupID FROM StockGroup WHERE StockGroup_Name = ?", name
    )
    row = cur.fetchone()
    if row:
        cache[name] = row[0]
        return row[0]
    cur.execute(
        "INSERT INTO StockGroup (StockGroup_Name, StockGroup_Disabled) "
        "OUTPUT INSERTED.StockGroupID VALUES (?, 0)",
        name,
    )
    gid = cur.fetchone()[0]
    cache[name] = gid
    return gid


def already_exists(cur, code: str, name: str) -> bool:
    cur.execute(
        "SELECT TOP 1 StockItemID FROM StockItem "
        "WHERE StockItem_QuickKey = ? OR StockItem_Name = ?",
        code,
        name,
    )
    return cur.fetchone() is not None


def insert_product(cur, p: dict, stock_group_id: int, warehouse_id: int):
    name = p["name"][:128]
    receipt = p["name"][:40]
    code = p["code"]
    price = p["price"]
    content = p["sku"] if p["sku"] else (price / Decimal("100") if price else Decimal("0"))
    # Match working POS items: Standard VAT + Default supplier/shrink/deposit
    vat_id = 2
    pricing_group_id = 2  # Unallocated
    barcode = code[:25]

    cur.execute(
        """
        INSERT INTO StockItem (
            StockItem_BrandItemID, StockItem_SupplierID, StockItem_ShrinkID,
            StockItem_PackSizeID, StockItem_PricingGroupID, StockItem_StockGroupID,
            StockItem_VatID, StockItem_DepositID,
            StockItem_Name, StockItem_ReceiptName, StockItem_Quantity,
            StockItem_ListCost, StockItem_ActualCost,
            StockItem_MinimumStock, StockItem_MaximumStock,
            StockItem_OrderQuantity, StockItem_OrderRounding, StockItem_OrderDynamic,
            StockItem_Disabled, StockItem_Discontinued,
            StockItem_QuickKey, StockItem_SupplierCode,
            StockItem_ActualCostChange, StockItem_PriceSetID, StockItem_LastCost,
            StockItem_Parameters, StockItem_Fractions, StockItem_NegSale,
            StockItem_VariablePrice, StockItem_NonWeighted,
            StockItem_PrintLocationID, StockItem_RecipeType, StockItem_PrintGroupID,
            StockItem_SerialTracker, StockItem_SBarcode, StockItem_SShelf,
            StockItem_ReportID, StockItemOrderType,
            StockItem_ATItem, StockItem_ATStockTypeID, StockItem_ExpiryDays,
            StockItem_MakeFinishItem, Notes
        )
        OUTPUT INSERTED.StockItemID
        VALUES (
            0, 2, 1,
            1, ?, ?,
            ?, 1,
            ?, ?, 0,
            ?, ?,
            0, 0,
            100, 1, 0,
            0, 0,
            ?, ?,
            0, 0, 0,
            0, 0, 0,
            0, 0,
            0, 0, 1,
            0, 0, 0,
            0, 1,
            0, 0, 0,
            0, ''
        )
        """,
        pricing_group_id,
        stock_group_id,
        vat_id,
        name,
        receipt,
        price,
        price,
        code,
        barcode,
    )
    stock_item_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO Catalogue (
            Catalogue_StockItemID, Catalogue_Quantity, Catalogue_Barcode,
            Catalogue_Deposit, Catalogue_Content, Catalogue_Disabled,
            Catalogue_PLU, Catalogue_Barcode2, Catalogue_Data
        ) VALUES (?, 1, ?, 0, ?, 0, NULL, NULL, NULL)
        """,
        stock_item_id,
        barcode,
        content,
    )

    # POS mirror tables
    cur.execute(
        """
        INSERT INTO POSCatalogue (
            POSCatalogue_StockItemID, POSCatalogue_Quantity, POSCatalogue_Barcode,
            POSCatalogue_Deposit, POSCatalogue_Content
        ) VALUES (?, 1, ?, 0, ?)
        """,
        stock_item_id,
        barcode,
        price,
    )

    # Selling price on channel 1 (+ mirror)
    cur.execute(
        """
        INSERT INTO PriceChannelLnk (
            PriceChannelLnk_StockItemID, PriceChannelLnk_Quantity,
            PriceChannelLnk_ChannelID, PriceChannelLnk_Price
        ) VALUES (?, 1, 1, ?)
        """,
        stock_item_id,
        price,
    )

    markups = {
        1: Decimal("20.0000"),
        2: Decimal("17.5000"),
        3: Decimal("15.0000"),
        4: Decimal("12.5000"),
        5: Decimal("10.0000"),
        6: Decimal("7.5000"),
        7: Decimal("5.0000"),
        8: Decimal("0.0000"),
        9: Decimal("0.0000"),
    }
    for channel_id, markup in markups.items():
        location = 4 if channel_id == 1 else (0 if channel_id == 9 else 1)
        cur.execute(
            """
            INSERT INTO CatalogueChannelLnk (
                CatalogueChannelLnk_StockItemID, CatalogueChannelLnk_Quantity,
                CatalogueChannelLnk_ChannelID, CatalogueChannelLnk_Markup,
                CatalogueChannelLnk_Price, CatalogueChannelLnk_PriceOriginal,
                CatalogueChannelLnk_PriceSystem, CatalogueChannelLnk_Location
            ) VALUES (?, 1, ?, ?, ?, 0, 0, ?)
            """,
            stock_item_id,
            channel_id,
            markup,
            price,
            location,
        )
        # POSCatalogueChannelLnk may already exist partially — insert for channels 1-5+
        try:
            cur.execute(
                """
                INSERT INTO POSCatalogueChannelLnk (
                    POSCatalogueChannelLnk_StockItemID,
                    POSCatalogueChannelLnk_Quantity,
                    POSCatalogueChannelLnk_ChannelID,
                    POSCatalogueChannelLnk_Price
                ) VALUES (?, 1, ?, ?)
                """,
                stock_item_id,
                channel_id,
                price,
            )
        except pyodbc.IntegrityError:
            pass

    cur.execute(
        """
        INSERT INTO WarehouseStockItemLnk (
            WarehouseStockItemLnk_WarehouseID,
            WarehouseStockItemLnk_StockItemID,
            WarehouseStockItemLnk_Quantity
        ) VALUES (?, ?, 0)
        """,
        warehouse_id,
        stock_item_id,
    )

    cur.execute(
        """
        INSERT INTO StockItemExt (
            StockItemId, ServiceItem, LoyaltyItem, ColorId, SizeId,
            RequireAttention, AvailableOnECommerce, EDescription
        ) VALUES (?, 0, 0, NULL, NULL, 0, 0, '')
        """,
        stock_item_id,
    )

    # POS visibility tables (required — matching existing sellable items)
    cur.execute(
        """
        INSERT INTO ChannelData (
            POSCatalogueChannelLnk_StockItemID, POSCatalogueChannelLnk_Quantity,
            Barcode, POSCatalogueChannelLnk_Price, Vat_Amount, theType
        ) VALUES (?, 1, ?, ?, 15, 0)
        """,
        stock_item_id,
        barcode,
        float(price),
    )
    cur.execute(
        "INSERT INTO systemStockItemPricing (systemStockItemPricing) VALUES (?)",
        stock_item_id,
    )
    cur.execute(
        """
        INSERT INTO StockitemHistory (
            StockitemHistory_StockItemID, StockitemHistory_Value,
            StockitemHistory_Day1, StockitemHistory_Day2, StockitemHistory_Day3,
            StockitemHistory_Day4, StockitemHistory_Day5, StockitemHistory_Day6,
            StockitemHistory_Day7, StockitemHistory_Day8, StockitemHistory_Day9,
            StockitemHistory_Day10, StockitemHistory_Day11, StockitemHistory_Day12,
            StockitemHistory_Week1, StockitemHistory_Week2, StockitemHistory_Week3,
            StockitemHistory_Week4, StockitemHistory_Week5, StockitemHistory_Week6,
            StockitemHistory_Week7, StockitemHistory_Week8, StockitemHistory_Week9,
            StockitemHistory_Week10, StockitemHistory_Week11, StockitemHistory_Week12,
            StockitemHistory_Month1, StockitemHistory_Month2, StockitemHistory_Month3,
            StockitemHistory_Month4, StockitemHistory_Month5, StockitemHistory_Month6,
            StockitemHistory_Month7, StockitemHistory_Month8, StockitemHistory_Month9,
            StockitemHistory_Month10, StockitemHistory_Month11, StockitemHistory_Month12
        ) VALUES (
            ?, 0,
            0,0,0,0,0,0,0,0,0,0,0,0,
            0,0,0,0,0,0,0,0,0,0,0,0,
            0,0,0,0,0,0,0,0,0,0,0,0
        )
        """,
        stock_item_id,
    )
    cur.execute(
        """
        INSERT INTO StockTake (
            StockTake_StockItemID, StockTake_WarehouseID,
            StockTake_Quantity, StockTake_Adjustment, StockTake_QuantityOrig
        ) VALUES (?, ?, 0, 0, 0)
        """,
        stock_item_id,
        warehouse_id,
    )
    try:
        cur.execute("INSERT INTO gGlobalUpdate (gStockItemID) VALUES (?)", stock_item_id)
    except Exception:
        pass

    return stock_item_id


def main():
    products = parse_products()
    print(f"Parsed {len(products)} products from Excel\n")

    by_cat: dict[str, int] = {}
    for p in products:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    for cat, n in by_cat.items():
        print(f"  {cat}: {n}")

    conn = pyodbc.connect(CONN)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT TOP 1 WarehouseID FROM Warehouse ORDER BY WarehouseID")
    warehouse_id = cur.fetchone()[0]

    group_cache: dict[str, int] = {}
    inserted = 0
    skipped = 0

    try:
        for p in products:
            if already_exists(cur, p["code"], p["name"]):
                print(f"SKIP existing: {p['code']} | {p['name']}")
                skipped += 1
                continue
            gid = get_or_create_stock_group(cur, p["category"], group_cache)
            sid = insert_product(cur, p, gid, warehouse_id)
            print(f"OK  #{sid}: {p['code']:20} | {p['category']:22} | {p['name']}")
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\nDone. Inserted={inserted}, Skipped={skipped}")


if __name__ == "__main__":
    main()
