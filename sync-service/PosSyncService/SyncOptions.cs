namespace PosSyncService;

public sealed class SyncOptions
{
    public string ApiBaseUrl { get; set; } = "http://127.0.0.1:8000";
    public string ApiKey { get; set; } = "";
    public string SqlConnectionString { get; set; } = "";
    public int PollIntervalSeconds { get; set; } = 30;
    public bool UseDemoMode { get; set; } = true;

    /// <summary>
    /// Must return columns: ExternalSaleId, SoldAt, ProductCode, Barcode, Quantity, UnitPrice.
    /// Parameter @Watermark is the last successful ExternalSaleId (string).
    /// </summary>
    public string SalesQuery { get; set; } = """
        SELECT TOP 50
            CAST(h.SaleId AS nvarchar(100)) AS ExternalSaleId,
            h.SaleDate AS SoldAt,
            ISNULL(p.ProductCode, '') AS ProductCode,
            ISNULL(p.Barcode, '') AS Barcode,
            CAST(l.Qty AS int) AS Quantity,
            CAST(l.UnitPrice AS decimal(12,2)) AS UnitPrice
        FROM SalesHeader h
        INNER JOIN SalesLine l ON l.SaleId = h.SaleId
        LEFT JOIN Products p ON p.ProductId = l.ProductId
        WHERE CAST(h.SaleId AS nvarchar(100)) > @Watermark
        ORDER BY h.SaleId, l.LineId
        """;
}
