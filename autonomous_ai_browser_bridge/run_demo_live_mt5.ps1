$ErrorActionPreference = "Stop"

$terminal = "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe"
$config = "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\gold_demo_live_smoke.ini"
if (-not (Test-Path -LiteralPath $terminal)) { throw "FxPro terminal not found." }
if (-not (Test-Path -LiteralPath $config)) { throw "Demo-live config not found." }

& $terminal "/config:$config"
