$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$bridgeScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "run_bridge.ps1"))
$all = Get-CimInstance Win32_Process
$roots = @(
    $all | Where-Object {
        $_.Name -eq "powershell.exe" -and
        $_.CommandLine -like "*run_bridge.ps1*"
    } | Select-Object -ExpandProperty ProcessId
)

$ids = [System.Collections.Generic.HashSet[int]]::new()
foreach ($root in $roots) { [void]$ids.Add([int]$root) }
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($process in $all) {
        if ($ids.Contains([int]$process.ParentProcessId) -and
            -not $ids.Contains([int]$process.ProcessId)) {
            [void]$ids.Add([int]$process.ProcessId)
            $changed = $true
        }
    }
}
foreach ($process in ($all | Where-Object { $ids.Contains([int]$_.ProcessId) } |
        Sort-Object CreationDate -Descending)) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
$wrapper = Start-Process powershell.exe -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $bridgeScript
) -WindowStyle Hidden -PassThru

$health = $null
for ($i = 0; $i -lt 180; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:1234/health" -TimeoutSec 2
    }
    catch {}
    if ($health -and $health.browser.ready) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $health -or -not $health.browser.ready) {
    throw "Bridge did not become ready after restart. Wrapper PID=$($wrapper.Id)"
}

$geminiEnabled = (Get-Content -LiteralPath (Join-Path $PSScriptRoot ".env") |
    Where-Object { $_ -eq "GEMINI_FALLBACK_ENABLED=true" }).Count -gt 0
if ($geminiEnabled -and -not $health.browser.gemini_fallback_ready) {
    throw "Bridge is ready, but Gemini fallback is not. Run diagnose_gemini.ps1."
}

Write-Host "Bridge ready. ChatGPT tabs=$($health.browser.open_chat_tabs), Gemini tabs=$($health.browser.gemini_open_tabs)."
