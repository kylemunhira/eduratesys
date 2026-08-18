namespace PosSyncService;

public sealed class SyncOptions
{
    public string ApiBaseUrl { get; set; } = "http://127.0.0.1:8080";
    public string ApiKey { get; set; } = "";
    public string SqlConnectionString { get; set; } = "";
    public int PollIntervalSeconds { get; set; } = 30;
    public bool UseDemoMode { get; set; } = true;

    /// <summary>
    /// When true, sync POS remaining quantities to portal branch stock each cycle.
    /// </summary>
    public bool SyncStock { get; set; } = true;

    /// <summary>
    /// On first run, only sales on or after (install date minus this many days) are synced.
    /// Older POS history is ignored so year-old sales are not backfilled.
    /// </summary>
    public int SalesLookbackDays { get; set; } = 20;

    /// <summary>
    /// Must return columns: ExternalSaleId, SoldAt, ProductCode, Barcode, Quantity, UnitPrice.
    /// Only rows with a Barcode are synced; portal matches barcode to catalog products.
    /// Parameters: @Watermark (last successful ExternalSaleId), @MinSaleDate (install lookback cutoff).
    /// </summary>
    public string SalesQuery { get; set; } = """
        SELECT TOP 50
            CAST(s.SaleID AS nvarchar(100)) AS ExternalSaleId,
            s.Sale_Date AS SoldAt,
            ISNULL(si.StockItem_QuickKey, '') AS ProductCode,
            ISNULL(c.Catalogue_Barcode, '') AS Barcode,
            CAST(ABS(si_item.SaleItem_Quantity) AS int) AS Quantity,
            CAST(si_item.SaleItem_Price AS decimal(12,2)) AS UnitPrice
        FROM Sale s
        INNER JOIN SaleItem si_item ON si_item.SaleItem_SaleID = s.SaleID
        LEFT JOIN StockItem si ON si.StockItemID = si_item.SaleItem_StockItemID
        LEFT JOIN Catalogue c ON c.Catalogue_StockItemID = si.StockItemID
        WHERE s.Sale_Date >= @MinSaleDate
          AND (LEN(@Watermark) = 0 OR s.SaleID > TRY_CAST(@Watermark AS int))
        ORDER BY s.SaleID, si_item.SaleItemID
        """;

    /// <summary>
    /// Must return columns: Barcode, Quantity (POS warehouse on-hand).
    /// Source of truth is WarehouseStockItemLnk (same qty as 4POS stock reports),
    /// not StockItem_Quantity. Portal sets BranchStock to these absolute quantities by barcode.
    /// Negative warehouse lines are treated as 0 so the snapshot API (min 0) is not rejected.
    /// </summary>
    public string StockQuery { get; set; } = """
        SELECT
            ISNULL(c.Catalogue_Barcode, '') AS Barcode,
            CAST(SUM(CASE
                WHEN wsi.WarehouseStockItemLnk_Quantity < 0 THEN 0
                ELSE wsi.WarehouseStockItemLnk_Quantity
            END) AS int) AS Quantity
        FROM WarehouseStockItemLnk wsi
        INNER JOIN StockItem si ON si.StockItemID = wsi.WarehouseStockItemLnk_StockItemID
        INNER JOIN Catalogue c ON c.Catalogue_StockItemID = si.StockItemID
        WHERE ISNULL(c.Catalogue_Barcode, '') <> ''
          AND si.StockItem_Disabled = 0
        GROUP BY c.Catalogue_Barcode
        ORDER BY c.Catalogue_Barcode
        """;
}
