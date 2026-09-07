$ErrorActionPreference = "Stop"

$pythonRoot = "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python"
Push-Location $pythonRoot
try {
    python ai_gate.py `
        --fill-tester-cache-once `
        --tester-cache-max-requests 60 `
        --tester-cache-max-per-family 20
}
finally {
    Pop-Location
}
