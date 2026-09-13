param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$outDir = Join-Path $repo ".mt5-orchestrator\models"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    throw "OpenCode CLI not found."
}

$prev = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & opencode models --refresh 2>&1 | Out-Null
    $verbose = @(& opencode models opencode-go --verbose 2>&1)
    $ids = @(& opencode models opencode-go 2>&1)
} finally {
    $ErrorActionPreference = $prev
}

$verbose | ForEach-Object { "$_" } | Set-Content -Encoding UTF8 (Join-Path $outDir "LIVE_MODELS_VERBOSE.txt")
$ids | ForEach-Object { "$_" } | Set-Content -Encoding UTF8 (Join-Path $outDir "LIVE_MODEL_IDS.txt")

$liveIds = @(
    $ids |
    Where-Object { "$_" -match '^opencode-go/' } |
    ForEach-Object { "$_".Trim() } |
    Sort-Object -Unique
)

$md = @()
$md += "# Live OpenCode Go model snapshot"
$md += ""
$md += "Generated UTC: $([DateTime]::UtcNow.ToString('o'))"
$md += ""
$md += "The local OpenCode CLI is authority."
$md += ""
foreach ($m in $liveIds) { $md += "- ``$m``" }
$md | Set-Content -Encoding UTF8 (Join-Path $outDir "LIVE_MODELS.md")

if (-not $Quiet) {
    Write-Host "Refreshed $($liveIds.Count) OpenCode Go models."
}
