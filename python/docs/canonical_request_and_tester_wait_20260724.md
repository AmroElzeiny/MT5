# Canonical AI Requests and Tester Wait

## Contract

Python and MT5 now publish and validate one compatibility manifest. The active
manifest hash is `1024838206`. A request with an absent or different manifest is
rejected before provider selection. Responses must carry the same manifest.

Current authoritative versions:

- Engine: `5.5-version-z-canonical-request-20260724-v8`
- Input schema: `po3-fvg-ai-provider-version-z-20260724-v7`
- Decision schema: `20260724_canonical_frozen_request_v10`
- Prompt contract: `20260724_canonical_frozen_request_v12`
- Request identity: `20260724_ai_request_identity_v3`

After source updates, compile and load:

`MQL5\Experts\MT5_PO3_Codex\PO3_AIGate_ScannerEA.mq5`

The expected MetaEditor result is `0 errors, 0 warnings`.

## Tester Wait

`InpAiWaitTimeoutRealMin=2` means an immutable wall-clock deadline of 120,000
milliseconds. The deadline is based on `GetTickCount64()` and is not reset by
polling, file movement, heartbeat changes, simulated market movement, or a
partial response.

Expected events:

```text
[tester_ai_wait_started] request_id=... timeout_ms=120000 deadline_ms=...
[tester_ai_wait_progress] request_id=... elapsed_wall_ms=... remaining_wall_ms=...
[tester_ai_wait_completed] request_id=... result=response_applied|timeout configured_timeout_ms=120000
```

In `TESTER_AI_LIVE_WAIT_DEBUG`, simulated age is diagnostic while the blocking
wait is active. Trading still requires `InpTesterAllowLiveWaitDebugTrading=true`.
Live-forward freshness and all request/candidate/fingerprint checks remain
unchanged.

Reliable backtests continue to use:

1. `TESTER_AI_RECORD_ONLY`
2. Process the exported requests through Python.
3. Confirm `FULL_STRUCTURED` and identity-valid responses.
4. Export the tester replay cache.
5. Rerun the same build and inputs in `TESTER_AI_CACHE_ONLY`.

## Cohort Migration

Audit before migration:

```powershell
python ai_gate.py cache-audit
```

Archive incompatible transport cohorts without touching trade ledgers or
historical outcomes:

```powershell
python ai_gate.py archive-incompatible-cohorts
```

Old responses or tester-cache rows missing the new contract are non-trading and
must be regenerated from the matching build and inputs.

## Verification

```powershell
python -m compileall .
python -m pytest -q
python tools/verify_v_next_controls.py
```

Expected runtime evidence:

```text
[contract_compatibility] compatible=true manifest_hash=1024838206
[provider_call_completed] quality_tier=FULL_STRUCTURED
[identity_validation] valid=true
[ai_schema_validation] valid=true decision_schema_version=20260724_canonical_frozen_request_v10
```
