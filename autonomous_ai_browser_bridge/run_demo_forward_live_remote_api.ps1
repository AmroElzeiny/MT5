$ErrorActionPreference = "Stop"

$pythonRoot = "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python"
$envFile = Join-Path $pythonRoot ".env"

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Python runtime .env is missing: $envFile"
}

$remoteEnabled = Select-String -LiteralPath $envFile -Pattern '^AI_USE_REMOTE_API=true$' -Quiet
if (-not $remoteEnabled) {
    throw "AI_USE_REMOTE_API must be true in the Python .env before starting this launcher."
}

$env:PO3_DOTENV_FILE = $envFile
Push-Location $pythonRoot
try {
    python ai_gate.py run --workers 3
}
finally {
    Pop-Location
}
