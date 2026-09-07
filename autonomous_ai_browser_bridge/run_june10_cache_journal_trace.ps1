param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

$terminalPath = "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe"
$configPath = Join-Path $PSScriptRoot "logs\june10_cache_journal_trace_nonvisual.ini"
$sourceSetPath = Join-Path $PSScriptRoot "logs\FULL_AI_GOLD_JUNE10_CACHE_JOURNAL_TRACE.set"
$testerSetPath = "C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Profiles\Tester\FULL_AI_GOLD_JUNE10_CACHE_JOURNAL_TRACE.set"
$cachePath = "C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\tester_ai_cache"
$reportPath = "C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\june10_cache_journal_trace_report.htm"

foreach ($requiredPath in @($terminalPath, $configPath, $sourceSetPath, $testerSetPath, $cachePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path is missing: $requiredPath"
    }
}

$sourceSetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceSetPath).Hash
$testerSetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $testerSetPath).Hash
if ($sourceSetHash -ne $testerSetHash) {
    throw "The deployed MT5 .set file does not match the reviewed source .set file."
}

$cacheCount = (Get-ChildItem -LiteralPath $cachePath -Filter "*.json" -File).Count
if ($cacheCount -lt 1) {
    throw "Tester cache is empty: $cachePath"
}

$runningTerminal = Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $terminalPath }
if ($runningTerminal) {
    throw "FxPro MetaTrader 5 is already running. Close the red-icon terminal, then run this file again so /config is applied reliably."
}

Write-Host "Starting GOLD M15 cache/journal diagnostic..."
Write-Host "Tester period: 2026-05-27 through 2026-06-11"
Write-Host "Tester cache files: $cacheCount"
Write-Host "Browser AI backend: not required"
Write-Host "Verbose tester journal: enabled"
Write-Host "Expected report: $reportPath"

if ($PreflightOnly) {
    Write-Host "Preflight passed. MT5 was not launched."
    return
}

Start-Process -FilePath $terminalPath -ArgumentList @("/config:$configPath")
