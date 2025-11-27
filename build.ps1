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

Write-Host "Testing executable..." -ForegroundColor Yellow
Write-Host "  Testing --help..." -ForegroundColor Cyan
$testOutput = & .\dist\folio\folio.exe --help 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Basic help test failed!" -ForegroundColor Red
    Write-Host $testOutput
    exit 1
}

Write-Host "  Testing version command..." -ForegroundColor Cyan
$versionOutput = & .\dist\folio\folio.exe version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Version test failed!" -ForegroundColor Red
    Write-Host $versionOutput
    exit 1
}

Write-Host "  Testing demo (subcommand) help command..." -ForegroundColor Cyan
$testOutput = & .\dist\folio\folio.exe demo --help 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Demo help test failed!" -ForegroundColor Red
    Write-Host $testOutput
    exit 1
}

Write-Host "  Testing demo creation..." -ForegroundColor Cyan
$demoOutput = & .\dist\folio\folio.exe demo 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Demo creation failed!" -ForegroundColor Red
    Write-Host $demoOutput
    exit 1
}

# Verify demo files were created in the executable's directory
if (!(Test-Path "dist\folio\data\folio.db")) {
    Write-Host "Demo did not create database file!" -ForegroundColor Red
    exit 1
}

if (!(Test-Path "dist\folio\config.yaml")) {
    Write-Host "Demo did not create config file!" -ForegroundColor Red
    exit 1
}

Write-Host "  Testing settle-info command..." -ForegroundColor Cyan
$settleInfoOutput = & .\dist\folio\folio.exe settle-info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Settle-info test failed!" -ForegroundColor Red
    Write-Host $settleInfoOutput
    exit 1
}

Write-Host "  ✅ All tests passed!" -ForegroundColor Green

# Clean up test artifacts before packaging
Write-Host "Cleaning up test artifacts..." -ForegroundColor Yellow
$testArtifacts = @(
    "dist\folio\data",
    "dist\folio\backups", 
    "dist\folio\logs",
    "dist\folio\config.yaml"
)

foreach ($artifact in $testArtifacts) {
    if (Test-Path $artifact) {
        Write-Host "  Removing $artifact" -ForegroundColor DarkYellow
        Remove-Item -Path $artifact -Recurse -Force -ErrorAction SilentlyContinue
    }
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