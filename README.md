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
python manage.py runserver
```

`portal/.env` must set:

```
DATABASE_URL=postgres://ssms:ssms@localhost:5432/ssms
```

`reset_portal` wipes all data and loads **VAST AFRICA** (customer, 5 branches) plus products/categories from `Product Codes Stockfeed.xlsx`.

**Docker alternative** (Linux containers): `docker compose up -d` uses port **5433** — set `DATABASE_URL=postgres://ssms:ssms@localhost:5433/ssms` in `portal/.env`.

(Without `DATABASE_URL`, Django falls back to SQLite for local experiments only.)

Open http://127.0.0.1:8000/

- **Login:** `admin` / `admin123` (change after first login)
- **Admin:** http://127.0.0.1:8000/admin/
- Branch API keys are on each branch detail page (local dev: Chivhu uses `ssms-local-chivhu-dev-key` after `seed_demo`)

### Production (Waitress Windows service)

On the portal server:

1. Copy `build/portal` (or the `portal/` tree) onto the server.
2. Create a venv and install deps: `pip install -r requirements.txt`
3. Copy `.env.production.example` → `.env.production` and fill in real values.
4. Download [NSSM](https://nssm.cc/download) and put `nssm.exe` on PATH (or pass `-NssmPath`).
5. Install and start the service (run PowerShell **as Administrator**):

```powershell
.\scripts\install_portal_service.ps1 `
  -PortalDir "C:\apps\ssms\portal" `
  -PythonExe "C:\apps\ssms\.venv\Scripts\python.exe"
```

Manual smoke test (without a service):

```powershell
cd C:\apps\ssms\portal
$env:APP_ENV = "production"
..\..\.venv\Scripts\python.exe run_waitress.py
```

Uninstall:

```powershell
.\scripts\uninstall_portal_service.ps1
```

Service name: `SSMSPortal`. Logs go to `portal\logs\`. Default listen address: `0.0.0.0:2023` (override with `WAITRESS_*` in `.env.production` or the install script).

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

```powershell
cd sync-service\PosSyncService
# Edit appsettings.json: set Sync:ApiKey from the branch page
dotnet run
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
