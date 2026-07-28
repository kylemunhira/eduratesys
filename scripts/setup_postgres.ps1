param(
    [Parameter(Mandatory = $true)]
    [string]$PostgresPassword,
    [string]$PgHost = "localhost",
    [int]$Port = 5432,
    [string]$DbUser = "ssms",
    [string]$DbPassword = "ssms",
    [string]$DbName = "ssms"
)

$ErrorActionPreference = "Stop"
$psql = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
if (-not (Test-Path $psql)) {
    $psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
}
if (-not $psql) {
    throw "psql not found. Install PostgreSQL or add it to PATH."
}

$env:PGPASSWORD = $PostgresPassword
& $psql -U postgres -h $PgHost -p $Port -d postgres -v ON_ERROR_STOP=1 -c @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DbUser') THEN
    CREATE USER $DbUser WITH PASSWORD '$DbPassword';
  ELSE
    ALTER USER $DbUser WITH PASSWORD '$DbPassword';
  END IF;
END
`$`$;
"@
$dbExists = (& $psql -U postgres -h $PgHost -p $Port -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DbName'").Trim()
if ($dbExists -ne "1") {
    & $psql -U postgres -h $PgHost -p $Port -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DbName OWNER $DbUser;"
}
& $psql -U postgres -h $PgHost -p $Port -d postgres -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE $DbName TO $DbUser;"
& $psql -U postgres -h $PgHost -p $Port -d $DbName -v ON_ERROR_STOP=1 -c "GRANT ALL ON SCHEMA public TO $DbUser;"

Write-Host "PostgreSQL ready: postgres://${DbUser}:****@${PgHost}:${Port}/${DbName}" -ForegroundColor Green
