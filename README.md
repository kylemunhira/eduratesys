# Supplier Stock Monitoring System (SSMS) — Phase 1

Web portal for suppliers to track stock dispatched to customer branches, with sales pulled from each branch POS via a Windows sync agent.

**Stock formula:** branch on-hand is synced from POS remaining quantity (`StockItem_Quantity`); dispatches still increase stock when approved until the next POS stock snapshot overwrites it.

## Layout

| Path | Purpose |
|------|---------|
| `portal/` | Django + DRF supplier portal |
| `sync-service/PosSyncService/` | .NET 8 Windows Worker Service |
| `sys.txt` | Original design specification |

## Portal setup (Windows)

Requires **PostgreSQL 17+** (already installed on this machine). One-time database setup:

```powershell
cd D:\Roysen\GitHub\eduratesys
.\scripts\setup_postgres.ps1 -PostgresPassword '<your-postgres-superuser-password>'
```

Then:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r portal\requirements.txt
copy portal\.env.example portal\.env
cd portal
python manage.py migrate
python manage.py reset_portal
python manage.py runserver 0.0.0.0:8000
```

`0.0.0.0` binds the dev server to all network interfaces so other PCs on the same LAN can open the portal at `http://<this-pc-ip>:8000/` (find the IP with `ipconfig` on Windows). Allow inbound TCP **8000** in Windows Firewall if another machine cannot connect.

`portal/.env` must set:

```
DATABASE_URL=postgres://ssms:ssms@localhost:5432/ssms
```

`reset_portal` wipes all data and loads **VAST AFRICA** (customer, 5 branches) plus products/categories from `Product Codes Stockfeed.xlsx`.

**Docker alternative** (Linux containers): `docker compose up -d` uses port **5433** — set `DATABASE_URL=postgres://ssms:ssms@localhost:5433/ssms` in `portal/.env`.

`DATABASE_URL` defaults to `postgres://ssms:ssms@localhost:5432/ssms` if unset (see `portal/config/settings.py`).

Open http://127.0.0.1:8000/ on this PC, or `http://<this-pc-ip>:8000/` from another device on the network.

- **Login:** `admin` / `admin123` (change after first login)
- **Admin:** http://127.0.0.1:8000/admin/
- Branch API keys are on each branch detail page (local dev: Chivhu uses `ssms-local-chivhu-dev-key` after `seed_demo`)

### Production (Waitress + NSSM Windows service)

NSSM runs the portal as a Windows service so it starts on boot and restarts after failures. Service name: **`SSMSPortal`**.

#### Portal server setup

1. Copy the repo (or at least `portal/`, `scripts/`, and a venv) onto the server, e.g. `C:\apps\ssms\`.
2. Create venv and install deps:

```powershell
cd C:\apps\ssms
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r portal\requirements.txt
```

3. Configure production env:

```powershell
copy portal\.env.production.example portal\.env.production
# Edit portal\.env.production — DATABASE_URL, SECRET_KEY, ALLOWED_HOSTS, etc.
```

4. NSSM is at `C:\nssm\nssm.exe` on the server (scripts auto-detect this path). Or download [NSSM](https://nssm.cc/download) and extract there.

5. Install and start the service — run PowerShell **as Administrator**:

```powershell
cd C:\apps\ssms
.\scripts\install_portal_service.ps1 `
  -PortalDir "C:\apps\ssms\portal" `
  -PythonExe "C:\apps\ssms\.venv\Scripts\python.exe"
```

Optional overrides: `-WaitressPort 2023`, `-WaitressHost 0.0.0.0`, `-NssmPath "C:\nssm\nssm.exe"`.

The install script runs `migrate` and `collectstatic`, registers NSSM, and starts the service.

6. Allow inbound TCP on the Waitress port in Windows Firewall (default **2023**).

7. Verify:

```powershell
Get-Service SSMSPortal
# Status should be Running
curl http://localhost:2023/
```

Logs: `portal\logs\waitress-stdout.log` and `waitress-stderr.log`.

Manual smoke test (without NSSM):

```powershell
cd C:\apps\ssms\portal
$env:APP_ENV = "production"
C:\apps\ssms\.venv\Scripts\python.exe run_waitress.py
```

Uninstall portal service:

```powershell
.\scripts\uninstall_portal_service.ps1
```

Default listen address: `0.0.0.0:2023` (override with `WAITRESS_*` in `.env.production` or install-script parameters).

### Sales sync API

`POST /api/sales/`

Header: `X-API-Key: <branch api key>`

```json
{
  "external_sale_id": "POS-1001",
  "sold_at": "2026-07-24T10:00:00Z",
  "items": [
    { "barcode": "8880029570102", "quantity": 5, "unit_price": "16.50" },
    { "barcode": "8880029560103", "quantity": 2 }
  ]
}
```

Items are matched to portal products **by barcode only**. Lines with no barcode or unknown barcode are skipped; stock is deducted only for matches.

Duplicates on `(branch, external_sale_id)` return HTTP 200 with status `duplicate` and do not change stock again.

Sales are recorded for reporting; **branch stock on-hand is set by the stock snapshot API**, not deducted per sale.

### Stock sync API

`POST /api/stock/`

Header: `X-API-Key: <branch api key>`

```json
{
  "items": [
    { "barcode": "8880029570102", "quantity": 48 },
    { "barcode": "8880029560103", "quantity": 35 }
  ]
}
```

Sets absolute `BranchStock.quantity` for matching barcodes (4Pos remaining qty).

## Sync service (.NET)

### Development

```powershell
cd sync-service\PosSyncService
# Edit appsettings.json: set Sync:ApiKey from the branch page
dotnet run
```

### Production (NSSM Windows service)

On each branch PC that syncs POS data to the portal. Service name: **`SSMSPosSync`**.

1. Edit `appsettings.Production.json` (or `appsettings.json` in the publish folder): set `Sync:ApiBaseUrl`, `Sync:ApiKey`, `Sync:SqlConnectionString`, and queries.
2. Install NSSM (same as portal server).
3. Run PowerShell **as Administrator**:

```powershell
cd C:\apps\ssms
.\scripts\install_sync_service.ps1 -Publish
```

Or publish manually and point at the output folder:

```powershell
dotnet publish sync-service\PosSyncService -c Release -o C:\apps\ssms\sync
.\scripts\install_sync_service.ps1 -ServiceDir "C:\apps\ssms\sync"
```

Logs: `sync\logs\sync-stdout.log` and `sync-stderr.log`. State/retry files: `sync-state.json`, `retry-queue.log` next to the executable.

Uninstall:

```powershell
.\scripts\uninstall_sync_service.ps1
```

Config (`Sync` section):

| Key | Meaning |
|-----|---------|
| `ApiBaseUrl` | Portal root, e.g. `http://127.0.0.1:8000` |
| `ApiKey` | Branch API key |
| `UseDemoMode` | `true` posts demo sale + demo stock (no SQL Server needed) |
| `SyncStock` | `true` pushes POS remaining qty to `/api/stock/` each cycle |
| `SqlConnectionString` | POS SQL Server (when `UseDemoMode` is false) |
| `SalesQuery` | Must return `ExternalSaleId`, `SoldAt`, `ProductCode`, `Barcode`, `Quantity`, `UnitPrice`; filter with `@Watermark` |
| `StockQuery` | Must return `Barcode`, `Quantity` (POS remaining / on-hand) |
| `PollIntervalSeconds` | Poll interval |

Watermark is stored in `sync-state.json` next to the binary. Failed cycles append to `retry-queue.log`.

Point `SalesQuery` at your real POS tables/columns before production use.

## Phase 1 features

- Auth (Django) + roles: IT, Business Head, Commercial Manager, Sales Admin, Sales
- IT creates users; Sales Admin / Sales are limited to assigned branches
- Customers, branches (API keys), products
- Dispatch draft → approve → branch stock increase
- Sales ingest API with dedupe + sync logs + audit log
- Dashboard (counts, low stock, offline branches)
- Reports: stock balance, dispatches, sales, customer stock, sync status
- Configurable .NET sync worker

## Design reference

See [sys.txt](sys.txt) for the full business scenario (Zimhope / Ngoni / Kiran / Kyle).
