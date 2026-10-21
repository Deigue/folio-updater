# Smoke checks for a packaged folio: build sanity, not a test suite.
#
# Runs the built exe on a fresh demo folio to prove the bundle starts, imports
# what it needs and can replay and render. Called by build.ps1 and by the release
# workflow, so both run the same checks. Whatever the checks write next to the
# exe (data, backups, logs, config) is removed afterwards, pass or fail, so it
# never ends up in the release archive.
#
# Usage: .\scripts\smoke_test.ps1 [-Dist dist\folio]

param(
    [string]$Dist = "dist\folio"
)

$exe = Join-Path $Dist "folio.exe"
if (!(Test-Path $exe)) {
    Write-Host "No executable at $exe; build first." -ForegroundColor Red
    exit 1
}

function Invoke-Folio {
    $line = "folio $($args -join ' ')"
    Write-Host "  $line" -ForegroundColor Cyan
    $output = & $exe @args 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ($output | Out-String)
        throw "$line failed with exit code $LASTEXITCODE"
    }
}

function Assert-Exists($path, $what) {
    if (!(Test-Path (Join-Path $Dist $path))) {
        throw "Demo did not create $what ($path)"
    }
}

Write-Host "Smoke testing $exe..." -ForegroundColor Yellow
$failed = $false
try {
    Invoke-Folio --help
    Invoke-Folio version
    Invoke-Folio demo --help

    Invoke-Folio demo
    Assert-Exists "data\folio.db" "the database"
    Assert-Exists "config.yaml" "the config file"

    # Read-only commands over the demo folio: settlement calendars, the
    # cost-base replay, the dashboard (cached quotes only, so no network) and
    # the transaction query parser.
    Invoke-Folio settle-info
    Invoke-Folio dash --offline
    Invoke-Folio query MSFT BUY
    Invoke-Folio acb MSFT

    Write-Host "  All smoke checks passed." -ForegroundColor Green
}
catch {
    Write-Host "  Smoke check failed: $_" -ForegroundColor Red
    $failed = $true
}
finally {
    Write-Host "Removing smoke test artifacts..." -ForegroundColor Yellow
    foreach ($artifact in "data", "backups", "logs", "config.yaml") {
        $path = Join-Path $Dist $artifact
        if (Test-Path $path) {
            Write-Host "  Removing $path" -ForegroundColor DarkYellow
            Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($failed) { exit 1 }
