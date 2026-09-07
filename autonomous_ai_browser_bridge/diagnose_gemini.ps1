$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe -m bridge.gemini_diagnose
exit $LASTEXITCODE
