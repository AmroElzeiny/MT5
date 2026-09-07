$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$locked = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*gemini_browser_profile*"
}
if ($locked) {
    throw "Gemini profile is in use. Stop run_bridge.ps1, close its Gemini Chrome window, then run this script again."
}

& .\.venv\Scripts\python.exe -m bridge.gemini_login
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& .\.venv\Scripts\python.exe -m bridge.gemini_diagnose
if ($LASTEXITCODE -ne 0) {
    throw "Gemini login or selectors are not ready. Review the diagnosis above before enabling fallback."
}

$envPath = Join-Path $PSScriptRoot ".env"
$lines = Get-Content -LiteralPath $envPath
$updated = $false
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^GEMINI_FALLBACK_ENABLED=') {
        $lines[$i] = 'GEMINI_FALLBACK_ENABLED=true'
        $updated = $true
        break
    }
}
if (-not $updated) { $lines += 'GEMINI_FALLBACK_ENABLED=true' }
[System.IO.File]::WriteAllLines($envPath, $lines, [System.Text.UTF8Encoding]::new($false))

Write-Host "Gemini fallback is enabled. Restarting the bridge now."
& (Join-Path $PSScriptRoot "restart_bridge.ps1")
