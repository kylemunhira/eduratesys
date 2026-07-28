using Microsoft.Extensions.Options;
using PosSyncService;

var builder = Host.CreateApplicationBuilder(args);
builder.Services.Configure<SyncOptions>(builder.Configuration.GetSection("Sync"));
builder.Services.AddHttpClient("ssms");
builder.Services.AddHostedService<Worker>();

var host = builder.Build();
host.Run();
