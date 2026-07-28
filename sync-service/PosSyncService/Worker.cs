using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Options;

namespace PosSyncService;

public sealed class Worker : BackgroundService
{
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
            "SSMS POS sync started. DemoMode={Demo} Interval={Interval}s Api={Api}",
            _options.UseDemoMode,
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
            var payload = new
            {
                external_sale_id = group.Key,
                sold_at = group.First().SoldAt,
                items = group.Select(g => new
                {
                    product_code = g.ProductCode,
                    barcode = g.Barcode,
                    quantity = g.Quantity,
                    unit_price = g.UnitPrice
                }).ToList()
            };

            var ok = await PostSaleAsync(payload, ct);
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
            new SaleRow(id, DateTime.UtcNow, "MILK-1L", "6001001", 1, 2.50m),
            new SaleRow(id, DateTime.UtcNow, "BREAD-LOAF", "6001002", 1, 1.20m)
        ];
    }

    private async Task<List<SaleRow>> QueryPosAsync(string watermark, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(_options.SqlConnectionString))
        {
            throw new InvalidOperationException(
                "SqlConnectionString is empty. Set Sync:UseDemoMode=true or provide a SQL Server connection string.");
        }

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

    private async Task<bool> PostSaleAsync(object payload, CancellationToken ct)
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

        using var response = await client.PostAsJsonAsync("api/sales/", payload, ct);
        var body = await response.Content.ReadAsStringAsync(ct);
        if (response.IsSuccessStatusCode)
        {
            _logger.LogDebug("API response: {Body}", body);
            return true;
        }

        _logger.LogError("API {Status}: {Body}", (int)response.StatusCode, body);
        return false;
    }

    private async Task<SyncState> LoadStateAsync(CancellationToken ct)
    {
        if (!File.Exists(_statePath))
        {
            return new SyncState();
        }

        await using var stream = File.OpenRead(_statePath);
        var state = await JsonSerializer.DeserializeAsync<SyncState>(stream, cancellationToken: ct);
        return state ?? new SyncState();
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

    private sealed class SyncState
    {
        public string Watermark { get; set; } = "";
        public DateTime? LastSuccessUtc { get; set; }
    }
}
