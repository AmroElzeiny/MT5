$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$bridgeHealth = "http://127.0.0.1:1234/health"
$bridgeReachable = $false
$bridgeReady = $false
try {
    $health = Invoke-RestMethod -Uri $bridgeHealth -TimeoutSec 3
    $bridgeReachable = [bool]$health.ok
    $bridgeReady = [bool]$health.ok -and [bool]$health.browser.ready
}
catch {
    $bridgeReachable = $false
    $bridgeReady = $false
}

if (-not $bridgeReachable) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run_bridge.ps1")
    ) -WindowStyle Normal
}

if (-not $bridgeReady) {
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        Start-Sleep -Seconds 2
        try {
            $health = Invoke-RestMethod -Uri $bridgeHealth -TimeoutSec 3
            $bridgeReady = [bool]$health.ok -and [bool]$health.browser.ready
        }
        catch {
            $bridgeReady = $false
        }
    } while (-not $bridgeReady -and [DateTime]::UtcNow -lt $deadline)
}

if (-not $bridgeReady) { throw "Browser bridge did not become ready within 3 minutes." }
if ([int]$health.browser.open_chat_tabs -ne [int]$health.browser.chat_tab_count) {
    throw "Browser tabs are not ready: opened=$($health.browser.open_chat_tabs), configured=$($health.browser.chat_tab_count)."
}

$gateRunning = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'ai_gate\.py'
}
if ($gateRunning) {
    throw "Another ai_gate.py process is already running. Stop it, then use this single launcher so fresh-decision settings are guaranteed."
}
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run_full_ai_1m_ai_gate.ps1")
) -WindowStyle Normal
Start-Sleep -Seconds 3

& (Join-Path $PSScriptRoot "run_full_ai_1m_mt5.ps1")
