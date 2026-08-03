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
    /// Must return columns: ExternalSaleId, SoldAt, ProductCode, Barcode, Quantity, UnitPrice.
    /// Only rows with a Barcode are synced; portal matches barcode to catalog products.
    /// Parameter @Watermark is the last successful ExternalSaleId (string).
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
        WHERE (LEN(@Watermark) = 0 OR s.SaleID > TRY_CAST(@Watermark AS int))
        ORDER BY s.SaleID, si_item.SaleItemID
        """;

    /// <summary>
    /// Must return columns: Barcode, Quantity (POS remaining / on-hand qty).
    /// Portal sets BranchStock to these absolute quantities by barcode.
    /// </summary>
    public string StockQuery { get; set; } = """
        SELECT
            ISNULL(c.Catalogue_Barcode, '') AS Barcode,
            CAST(si.StockItem_Quantity AS int) AS Quantity
        FROM StockItem si
        INNER JOIN Catalogue c ON c.Catalogue_StockItemID = si.StockItemID
        WHERE ISNULL(c.Catalogue_Barcode, '') <> ''
          AND si.StockItem_Disabled = 0
        ORDER BY si.StockItemID
        """;
}
