using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Options;

namespace PosSyncService;

public sealed class Worker : BackgroundService
{
    private static readonly JsonSerializerOptions SaleJsonOptions = new()
    {
        PropertyNamingPolicy = null,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly ILogger<Worker> _logger;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly SyncOptions _options;
    private readonly string _statePath;

    public Worker(
        ILogger<Worker> logger,
        IHttpClientFactory httpClientFactory,
        IOptions<SyncOptions> options)
    {
        _logger = logger;
        _httpClientFactory = httpClientFactory;
        _options = options.Value;
        _statePath = Path.Combine(AppContext.BaseDirectory, "sync-state.json");
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation(
            "SSMS POS sync started. DemoMode={Demo} SyncStock={Stock} Interval={Interval}s Api={Api}",
            _options.UseDemoMode,
            _options.SyncStock,
            _options.PollIntervalSeconds,
            _options.ApiBaseUrl);

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await SyncOnceAsync(stoppingToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _logger.LogError(ex, "Sync cycle failed; will retry.");
                await AppendRetryAsync(ex.Message);
            }

            await Task.Delay(TimeSpan.FromSeconds(Math.Max(5, _options.PollIntervalSeconds)), stoppingToken);
        }
    }

    private async Task SyncOnceAsync(CancellationToken ct)
    {
        await SyncSalesAsync(ct);
        if (_options.SyncStock)
        {
            await SyncStockAsync(ct);
        }
    }

    private async Task SyncSalesAsync(CancellationToken ct)
    {
        var state = await LoadStateAsync(ct);
        var rows = _options.UseDemoMode
            ? BuildDemoRows(state)
            : await QueryPosAsync(state.Watermark, ct);

        if (rows.Count == 0)
        {
            _logger.LogDebug("No new sales.");
            return;
        }

        foreach (var group in rows.GroupBy(r => r.ExternalSaleId))
        {
            var barcodeRows = group
                .Where(r => !string.IsNullOrWhiteSpace(r.Barcode))
                .ToList();
            if (barcodeRows.Count == 0)
            {
                _logger.LogInformation(
                    "Skipping sale {SaleId}: no lines with barcode",
                    group.Key);
                state.Watermark = group.Key;
                state.LastSuccessUtc = DateTime.UtcNow;
                await SaveStateAsync(state, ct);
                continue;
            }

            var payload = new
            {
                external_sale_id = group.Key,
                sold_at = barcodeRows[0].SoldAt,
                items = barcodeRows.Select(g => new
                {
                    barcode = g.Barcode,
                    quantity = g.Quantity,
                    unit_price = g.UnitPrice
                }).ToList()
            };

            var ok = await PostJsonAsync("api/sales/", payload, ct);
            if (!ok)
            {
                await AppendRetryAsync($"Failed upload for {group.Key}");
                return;
            }

            state.Watermark = group.Key;
            state.LastSuccessUtc = DateTime.UtcNow;
            await SaveStateAsync(state, ct);
            _logger.LogInformation("Uploaded sale {SaleId}", group.Key);
        }
    }

    private async Task SyncStockAsync(CancellationToken ct)
    {
        var rows = _options.UseDemoMode
            ? BuildDemoStockRows()
            : await QueryStockAsync(ct);

        var items = rows
            .Where(r => !string.IsNullOrWhiteSpace(r.Barcode))
            .GroupBy(r => r.Barcode.Trim(), StringComparer.OrdinalIgnoreCase)
            .Select(g => new { barcode = g.Key, quantity = g.Sum(x => x.Quantity) })
            .ToList();

        if (items.Count == 0)
        {
            _logger.LogDebug("No POS stock rows with barcode.");
            return;
        }

        var payload = new { items };
        var ok = await PostJsonAsync("api/stock/", payload, ct);
        if (!ok)
        {
            await AppendRetryAsync("Failed stock snapshot upload");
            return;
        }

        _logger.LogInformation("Synced stock snapshot ({Count} barcodes)", items.Count);
    }

    private List<SaleRow> BuildDemoRows(SyncState state)
    {
        // Emits one demo sale per process lifetime after watermark advances past DEMO-0.
        if (!string.IsNullOrEmpty(state.Watermark) && state.Watermark.StartsWith("DEMO-", StringComparison.Ordinal))
        {
            return [];
        }

        var id = $"DEMO-{DateTime.UtcNow:yyyyMMddHHmmss}";
        return
        [
            new SaleRow(id, DateTime.UtcNow, "8880029570102", "8880029570102", 1, 16.50m),
            new SaleRow(id, DateTime.UtcNow, "8880029560103", "8880029560103", 1, 17.00m)
        ];
    }

    private static List<StockRow> BuildDemoStockRows() =>
    [
        new StockRow("8880029570102", 48),
        new StockRow("8880029560103", 35),
        new StockRow("8880029550104", 22),
        new StockRow("8880029390106", 12),
        new StockRow("8880029380107", 40),
        new StockRow("8880029370108", 18)
    ];

    private async Task<List<SaleRow>> QueryPosAsync(string watermark, CancellationToken ct)
    {
        EnsureSqlConfigured();

        var rows = new List<SaleRow>();
        await using var conn = new SqlConnection(_options.SqlConnectionString);
        await conn.OpenAsync(ct);
        await using var cmd = new SqlCommand(_options.SalesQuery, conn);
        cmd.Parameters.AddWithValue("@Watermark", watermark ?? "");
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            rows.Add(new SaleRow(
                reader.GetString(reader.GetOrdinal("ExternalSaleId")),
                reader.GetDateTime(reader.GetOrdinal("SoldAt")),
                reader["ProductCode"]?.ToString() ?? "",
                reader["Barcode"]?.ToString() ?? "",
                Convert.ToInt32(reader["Quantity"]),
                Convert.ToDecimal(reader["UnitPrice"])
            ));
        }

        return rows;
    }

    private async Task<List<StockRow>> QueryStockAsync(CancellationToken ct)
    {
        EnsureSqlConfigured();

        var rows = new List<StockRow>();
        await using var conn = new SqlConnection(_options.SqlConnectionString);
        await conn.OpenAsync(ct);
        await using var cmd = new SqlCommand(_options.StockQuery, conn);
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            rows.Add(new StockRow(
                reader["Barcode"]?.ToString() ?? "",
                Convert.ToInt32(reader["Quantity"])
            ));
        }

        return rows;
    }

    private void EnsureSqlConfigured()
    {
        if (string.IsNullOrWhiteSpace(_options.SqlConnectionString))
        {
            throw new InvalidOperationException(
                "SqlConnectionString is empty. Set Sync:UseDemoMode=true or provide a SQL Server connection string.");
        }
    }

    private async Task<bool> PostJsonAsync(string relativeUrl, object payload, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(_options.ApiKey))
        {
            _logger.LogWarning("ApiKey is empty; cannot POST. Set Sync:ApiKey from the branch detail page.");
            return false;
        }

        var client = _httpClientFactory.CreateClient("ssms");
        client.BaseAddress = new Uri(_options.ApiBaseUrl.TrimEnd('/') + "/");
        client.DefaultRequestHeaders.Remove("X-API-Key");
        client.DefaultRequestHeaders.Add("X-API-Key", _options.ApiKey);
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));

        var json = JsonSerializer.Serialize(payload, SaleJsonOptions);
        using var content = new StringContent(json, Encoding.UTF8, "application/json");
        using var response = await client.PostAsync(relativeUrl, content, ct);
        var body = await response.Content.ReadAsStringAsync(ct);
        if (response.IsSuccessStatusCode)
        {
            _logger.LogDebug("API {Url} response: {Body}", relativeUrl, body);
            return true;
        }

        _logger.LogError("API {Url} {Status}: {Body}", relativeUrl, (int)response.StatusCode, body);
        return false;
    }

    private async Task<SyncState> LoadStateAsync(CancellationToken ct)
    {
        if (!File.Exists(_statePath))
        {
            return new SyncState();
        }

        await using var stream = File.OpenRead(_statePath);
        var state = await JsonSerializer.DeserializeAsync<SyncState>(stream, cancellationToken: ct)
            ?? new SyncState();

        // Demo watermarks (DEMO-*) sort above numeric POS SaleIDs in string comparisons,
        // which blocks all real sales until the state file is cleared.
        if (!_options.UseDemoMode
            && !string.IsNullOrEmpty(state.Watermark)
            && state.Watermark.StartsWith("DEMO-", StringComparison.Ordinal))
        {
            _logger.LogWarning(
                "Clearing demo watermark {Watermark} before live SQL sync.",
                state.Watermark);
            state.Watermark = "";
            await SaveStateAsync(state, ct);
        }

        return state;
    }

    private async Task SaveStateAsync(SyncState state, CancellationToken ct)
    {
        await using var stream = File.Create(_statePath);
        await JsonSerializer.SerializeAsync(stream, state, cancellationToken: ct);
    }

    private async Task AppendRetryAsync(string message)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "retry-queue.log");
        await File.AppendAllTextAsync(
            path,
            $"{DateTime.UtcNow:o}\t{message}{Environment.NewLine}");
    }

    private sealed record SaleRow(
        string ExternalSaleId,
        DateTime SoldAt,
        string ProductCode,
        string Barcode,
        int Quantity,
        decimal UnitPrice);

    private sealed record StockRow(string Barcode, int Quantity);

    private sealed class SyncState
    {
        public string Watermark { get; set; } = "";
        public DateTime? LastSuccessUtc { get; set; }
    }
}
