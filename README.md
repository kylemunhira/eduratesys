# Supplier Stock Monitoring System (SSMS) — Phase 1

Web portal for suppliers to track stock dispatched to customer branches, with sales pulled from each branch POS via a Windows sync agent.

**Stock formula:** `current = dispatched − synced sales`

## Layout

| Path | Purpose |
|------|---------|
| `portal/` | Django + DRF supplier portal |
| `sync-service/PosSyncService/` | .NET 9 Windows Worker Service |
| `sys.txt` | Original design specification |

## Portal setup (Windows)

Requires **PostgreSQL**. Create a role and database once (use your `postgres` superuser password):

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE USER ssms WITH PASSWORD 'ssms';"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE ssms OWNER ssms;"
```

Then:

```powershell
cd c:\Users\HP\Documents\GitHub\eduratesys
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r portal\requirements.txt
copy portal\.env.example portal\.env
# Edit portal\.env if your Postgres user/password/port differ
cd portal
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

`portal/.env` must set:

```
DATABASE_URL=postgres://ssms:ssms@localhost:5432/ssms
```

(Without `DATABASE_URL`, Django falls back to SQLite for local experiments only.)

Open http://127.0.0.1:8000/

- **Login:** `admin` / `admin123` (change after first login)
- **Admin:** http://127.0.0.1:8000/admin/
- Branch API keys are on each branch detail page (seed prints Borrowdale’s key)

### Sales sync API

`POST /api/sales/`

Header: `X-API-Key: <branch api key>`

```json
{
  "external_sale_id": "POS-1001",
  "sold_at": "2026-07-24T10:00:00Z",
  "items": [
    { "product_code": "MILK-1L", "quantity": 5, "unit_price": "2.50" },
    { "barcode": "6001002", "quantity": 2 }
  ]
}
```

Duplicates on `(branch, external_sale_id)` return HTTP 200 with status `duplicate` and do not change stock again.

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
| `UseDemoMode` | `true` posts one demo sale (no SQL Server needed) |
| `SqlConnectionString` | POS SQL Server (when `UseDemoMode` is false) |
| `SalesQuery` | Must return `ExternalSaleId`, `SoldAt`, `ProductCode`, `Barcode`, `Quantity`, `UnitPrice`; filter with `@Watermark` |
| `PollIntervalSeconds` | Poll interval |

Watermark is stored in `sync-state.json` next to the binary. Failed cycles append to `retry-queue.log`.

Point `SalesQuery` at your real POS tables/columns before production use.

## Phase 1 features

- Auth (Django) + Admin/Supplier groups
- Customers, branches (API keys), products
- Dispatch draft → approve → branch stock increase
- Sales ingest API with dedupe + sync logs + audit log
- Dashboard (counts, low stock, offline branches)
- Reports: stock balance, dispatches, sales, customer stock, sync status
- Configurable .NET sync worker

## Design reference

See [sys.txt](sys.txt) for the full business scenario (Zimhope / Ngoni / Kiran / Kyle).
