"""
Fix newly imported stock items so they appear on POS like existing products.
Adds ChannelData, systemStockItemPricing, StockitemHistory, StockTake
and aligns StockItem defaults (supplier/shrink/deposit/vat).
"""
import pyodbc

CONN = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DEVELOPER03\\SQLEXPRESS2012;"
    "Database=pricing;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)


def main():
    conn = pyodbc.connect(CONN)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT TOP 1 WarehouseID FROM Warehouse ORDER BY WarehouseID")
    warehouse_id = cur.fetchone()[0]

    # Working template defaults from Default table / existing items
    cur.execute(
        """
        SELECT TOP 1 Default_SupplierID, Default_ShrinkID, Default_DepositID,
               Default_PackSizeID, Default_PricingGroupID
        FROM [Default]
        """
    )
    defaults = cur.fetchone()
    supplier_id = (defaults[0] if defaults else None) or 2
    shrink_id = (defaults[1] if defaults else None) or 1
    deposit_id = (defaults[2] if defaults else None) or 1
    print(f"Defaults: supplier={supplier_id}, shrink={shrink_id}, deposit={deposit_id}")

    # New items = those missing from ChannelData (or ID > max ChannelData)
    cur.execute(
        """
        SELECT si.StockItemID, si.StockItem_Name, si.StockItem_ListCost,
               c.Catalogue_Barcode, si.StockItem_VatID, v.Vat_Amount
        FROM StockItem si
        INNER JOIN Catalogue c ON c.Catalogue_StockItemID = si.StockItemID
            AND c.Catalogue_Quantity = 1
        LEFT JOIN Vat v ON v.VatID = si.StockItem_VatID
        WHERE NOT EXISTS (
            SELECT 1 FROM ChannelData cd
            WHERE cd.POSCatalogueChannelLnk_StockItemID = si.StockItemID
        )
        AND ISNULL(si.StockItem_Disabled, 0) = 0
        ORDER BY si.StockItemID
        """
    )
    missing = cur.fetchall()
    print(f"Products missing from ChannelData (POS): {len(missing)}")

    fixed = 0
    try:
        for sid, name, list_cost, barcode, vat_id, vat_amount in missing:
            price = float(list_cost or 0)
            # Match existing POS items: use Standard 15% VAT in ChannelData
            vat = float(vat_amount if vat_amount is not None else 15)
            # Align stock item fields to working products
            cur.execute(
                """
                UPDATE StockItem SET
                    StockItem_SupplierID = ?,
                    StockItem_ShrinkID = ?,
                    StockItem_DepositID = ?,
                    StockItem_VatID = 2,
                    StockItem_PrintGroupID = 1,
                    StockItem_OrderRounding = 1,
                    StockItem_OrderQuantity = CASE
                        WHEN ISNULL(StockItem_OrderQuantity, 0) = 0 THEN 100
                        ELSE StockItem_OrderQuantity END,
                    StockItem_Quantity = CASE
                        WHEN ISNULL(StockItem_Quantity, 0) = 0 THEN 0
                        ELSE StockItem_Quantity END,
                    Notes = ISNULL(Notes, '')
                WHERE StockItemID = ?
                """,
                supplier_id,
                shrink_id,
                deposit_id,
                sid,
            )

            # ChannelData — POS barcode/price cache (critical)
            cur.execute(
                """
                INSERT INTO ChannelData (
                    POSCatalogueChannelLnk_StockItemID,
                    POSCatalogueChannelLnk_Quantity,
                    Barcode,
                    POSCatalogueChannelLnk_Price,
                    Vat_Amount,
                    theType
                ) VALUES (?, 1, ?, ?, 15, 0)
                """,
                sid,
                barcode,
                price,
            )

            # systemStockItemPricing
            cur.execute(
                "SELECT 1 FROM systemStockItemPricing WHERE systemStockItemPricing = ?",
                sid,
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO systemStockItemPricing (systemStockItemPricing) VALUES (?)",
                    sid,
                )

            # StockitemHistory zero row
            cur.execute(
                "SELECT 1 FROM StockitemHistory WHERE StockitemHistory_StockItemID = ?",
                sid,
            )
            if not cur.fetchone():
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
                    sid,
                )

            # StockTake warehouse row
            cur.execute(
                """
                SELECT 1 FROM StockTake
                WHERE StockTake_StockItemID = ? AND StockTake_WarehouseID = ?
                """,
                sid,
                warehouse_id,
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO StockTake (
                        StockTake_StockItemID, StockTake_WarehouseID,
                        StockTake_Quantity, StockTake_Adjustment, StockTake_QuantityOrig
                    ) VALUES (?, ?, 0, 0, 0)
                    """,
                    sid,
                    warehouse_id,
                )

            # Ensure warehouse link qty exists
            cur.execute(
                """
                SELECT 1 FROM WarehouseStockItemLnk
                WHERE WarehouseStockItemLnk_WarehouseID = ?
                  AND WarehouseStockItemLnk_StockItemID = ?
                """,
                warehouse_id,
                sid,
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO WarehouseStockItemLnk (
                        WarehouseStockItemLnk_WarehouseID,
                        WarehouseStockItemLnk_StockItemID,
                        WarehouseStockItemLnk_Quantity
                    ) VALUES (?, ?, 0)
                    """,
                    warehouse_id,
                    sid,
                )

            # Flag for POS refresh if table is used that way
            cur.execute(
                "SELECT 1 FROM gGlobalUpdate WHERE gStockItemID = ?", sid
            )
            if not cur.fetchone():
                try:
                    cur.execute(
                        "INSERT INTO gGlobalUpdate (gStockItemID) VALUES (?)", sid
                    )
                except Exception:
                    pass

            print(f"Fixed #{sid}: {barcode} | {name} @ {price}")
            fixed += 1

        # Also refresh ChannelData VAT for items we set to Standard
        cur.execute(
            """
            UPDATE ChannelData SET Vat_Amount = 15
            WHERE POSCatalogueChannelLnk_StockItemID >= 9
            """
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Verify
    conn = pyodbc.connect(CONN)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ChannelData")
    print(f"\nChannelData rows now: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM systemStockItemPricing")
    print(f"systemStockItemPricing rows: {cur.fetchone()[0]}")
    cur.execute(
        """
        SELECT COUNT(*) FROM StockItem si
        WHERE NOT EXISTS (
            SELECT 1 FROM ChannelData cd
            WHERE cd.POSCatalogueChannelLnk_StockItemID = si.StockItemID
        ) AND ISNULL(si.StockItem_Disabled,0)=0
        """
    )
    print(f"Still missing from POS ChannelData: {cur.fetchone()[0]}")
    cur.execute(
        "SELECT TOP 3 * FROM ChannelData ORDER BY POSCatalogueChannelLnk_StockItemID DESC"
    )
    print("Latest ChannelData:")
    for r in cur.fetchall():
        print(r)
    conn.close()
    print(f"\nDone. Fixed {fixed} products.")


if __name__ == "__main__":
    main()
