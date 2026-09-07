$ErrorActionPreference = "Stop"

$pythonRoot = "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python"
$health = Invoke-RestMethod -Uri "http://127.0.0.1:1234/health" -TimeoutSec 10
if (-not $health.ok -or -not $health.browser.ready) {
    throw "Browser bridge is not ready."
}
if ([int]$health.browser.open_chat_tabs -ne [int]$health.browser.chat_tab_count) {
    throw "Browser tab count mismatch."
}

$sourceEnv = Join-Path $pythonRoot ".env"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempEnv = Join-Path $tempRoot ("po3_demo_live_" + [guid]::NewGuid().ToString("N") + ".env")
$lines = [System.Collections.Generic.List[string]]::new()
Get-Content -LiteralPath $sourceEnv | ForEach-Object { [void]$lines.Add($_) }

function Set-RunEnvValue([string]$Name, [string]$Value) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match ('^' + [regex]::Escape($Name) + '=')) {
            $lines[$i] = "$Name=$Value"
            return
        }
    }
    [void]$lines.Add("$Name=$Value")
}

Set-RunEnvValue "AI_DECISION_CACHE_ENABLE" "false"
Set-RunEnvValue "AI_SHADOW_REPEAT_ENABLE" "false"
Set-RunEnvValue "AI_GATE_WORKERS" "3"
Set-RunEnvValue "LOCAL_AI_PARALLELISM" "3"
Set-RunEnvValue "LOCAL_AI_TIMEOUT_SEC" "1800"
Set-RunEnvValue "LOCAL_AI_ANALYST_TIMEOUT_SEC" "900"
Set-RunEnvValue "LOCAL_AI_CRITIC_TIMEOUT_SEC" "450"
Set-RunEnvValue "LOCAL_AI_ADJUDICATOR_TIMEOUT_SEC" "300"
Set-RunEnvValue "AI_PROVIDER_ANALYST_TIMEOUT_SEC" "900"
Set-RunEnvValue "AI_PROVIDER_CRITIC_TIMEOUT_SEC" "450"
Set-RunEnvValue "AI_PROVIDER_ADJUDICATOR_TIMEOUT_SEC" "300"
Set-RunEnvValue "AI_MT5_TERMINAL_TIMEOUT_SEC" "1800"
Set-RunEnvValue "AI_RESPONSE_WRITE_MARGIN_SEC" "15"
[System.IO.File]::WriteAllLines($tempEnv, $lines, [System.Text.UTF8Encoding]::new($false))
$env:PO3_DOTENV_FILE = $tempEnv

Push-Location $pythonRoot
try {
    python ai_gate.py run --workers 3
}
finally {
    Pop-Location
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempEnv)
    if ($resolvedTemp.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTemp -Force -ErrorAction SilentlyContinue
    }
}
