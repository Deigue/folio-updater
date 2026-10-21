# Creates a distributable folio application directory

Write-Host "=== Building folio distribution ===" -ForegroundColor Green
Write-Host "Cleaning previous builds..." -ForegroundColor Yellow
if (Test-Path folio-release.zip) { 
    Remove-Item -Force folio-release.zip -ErrorAction SilentlyContinue
}

# --clean flag removes dist/build dirs and clears PyInstaller cache, handling file locks
Write-Host "Building directory distribution with optimized spec..." -ForegroundColor Yellow
uv run pyinstaller --noconfirm --clean folio.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    exit 1
}

# Same checks as the release workflow; cleans up after itself
& .\scripts\smoke_test.ps1 -Dist dist\folio
if ($LASTEXITCODE -ne 0) {
    Write-Host "Smoke test failed!" -ForegroundColor Red
    exit 1
}

# Copy basic docs
Write-Host "Adding documentation..." -ForegroundColor Yellow
Copy-Item README.md dist\folio\
Copy-Item LICENSE.md dist\folio\ -ErrorAction SilentlyContinue

# Calculate sizes
$mainExeSize = [math]::Round((Get-Item "dist\folio\folio.exe").Length / 1MB, 2)
$totalSize = [math]::Round(((Get-ChildItem "dist\folio" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB), 2)

Write-Host "Build Summary:" -ForegroundColor Green
Write-Host "  Main executable: $mainExeSize MB" -ForegroundColor Cyan
Write-Host "  Total directory: $totalSize MB" -ForegroundColor Cyan
Write-Host "  Location: dist\folio\" -ForegroundColor Cyan

# Create distributable ZIP archive
Write-Host "Creating release archive..." -ForegroundColor Yellow
$zipPath = "dist\folio-release.zip"
Compress-Archive -Path "dist\folio\*" -DestinationPath $zipPath -Force

$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Host "  Release archive: $zipPath ($zipSize MB)" -ForegroundColor Cyan

Write-Host ""
Write-Host "=== Build Complete! ===" -ForegroundColor Green
Write-Host "Users extract and run: folio\folio.exe" -ForegroundColor White