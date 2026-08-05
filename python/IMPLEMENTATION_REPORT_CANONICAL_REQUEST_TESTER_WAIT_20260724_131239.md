# Implementation Report: Canonical Request and Tester Wait

Generated: 2026-07-24 13:12:39 UTC

## Executive Summary

The confirmed zero-authority chain was caused by a local lifecycle defect, not
by the strategy filters. Python froze a candidate group and later
`_strict_taxonomy_failures()` wrote taxonomy fields into that frozen payload.
`FrozenAIRequest.assert_unchanged()` correctly detected the mutation, but a
generic exception path mislabeled the local integrity failure as provider
transport failure. Separately, MT5's pending-response path treated disappearance
of the request file after Python claimed it as a 15-second terminal condition.

The repaired lifecycle is:

```text
read request
-> validate transport and contract manifest
-> enrich and validate taxonomy
-> normalize, deduplicate, cap, and order candidates
-> add decision-relevant fingerprint and prior inputs
-> seal the canonical request and identity
-> assert immutability at every provider/consensus/repeatability/cache boundary
-> write a bound response
-> apply strict MQL candidate/fingerprint/execution gates
```

No strategy thresholds, family defaults, RR floors, target safety gates, AI
strictness settings, or rule-only fallback settings were changed.

## Root Causes

1. `_strict_taxonomy_failures()` both validated and assigned taxonomy fields
   after `freeze_ai_request()`.
2. `_score_setup_impl()` classified every non-`ProviderCallError` exception as
   `PROVIDER_TRANSPORT_ERROR`.
3. A provider-orchestration catch could wrap a typed local integrity exception
   in a generic `RuntimeError`.
4. `TradeEngine::ProcessPendingAI()` contained an implicit 15-second cutoff when
   the request file moved from `requests` to Python's `processing` directory.
5. Python and MQL had distributed version constants without one manifest hash.
6. Error responses lacked a complete transport identity contract.
7. PO3 normalization could erase displacement/BOS state instead of restoring
   immutable source timestamps or rejecting precisely.
8. `net_reward_after_cost_r` was calculated after the first accepted-plan log.

## Files Changed

### Python

- `ai_gate.py`
- `decision_integrity.py`
- `architecture_contracts.py`
- `compatibility_manifest.py` (new)
- `pipeline_integrity.py` (new)
- `tester_wait_contract.py` (new)
- `tests/test_canonical_request_wait_contract.py` (new)
- `tests/test_provider_neutral_ai.py`
- `tests/test_python_owned_identity_lifecycle.py`
- `tests/test_zero_trade_reliability.py`
- `tools/verify_v_next_controls.py`
- `docs/canonical_request_and_tester_wait_20260724.md` (new)

### Active MQL

- `MQL5\Include\MT5_PO3_Codex\Config.mqh`
- `MQL5\Include\MT5_PO3_Codex\Types.mqh`
- `MQL5\Include\MT5_PO3_Codex\AIGateBridge.mqh`
- `MQL5\Include\MT5_PO3_Codex\TradeEngine.mqh`
- `MQL5\Experts\MT5_PO3_Codex\PO3_AIGate_ScannerEA.mq5`

## Functions and Structures

### Python

- `ai_gate._freeze_request_for_provider`
- `ai_gate._enrich_candidate_taxonomy_before_freeze`
- `ai_gate._strict_taxonomy_failures`
- `ai_gate._score_setup_ai`
- `ai_gate._score_setup_impl`
- `ai_gate._write_error_response`
- `ai_gate.process_one`
- `ai_gate._archive_incompatible_bus_cohorts`
- `decision_integrity._build_ai_request_identity_from_frozen_candidates`
- `decision_integrity.FrozenAIRequest.assert_unchanged`
- `decision_integrity.freeze_ai_request`
- `architecture_contracts.response_binding_material`
- `compatibility_manifest.compatibility_manifest`
- `compatibility_manifest.validate_mql_contract`
- `pipeline_integrity.PipelineIntegrityError` and typed subclasses
- `tester_wait_contract.TesterAIWaitDeadline`

### MQL

- `PO3ContractManifestMaterial` / `PO3ContractManifestHash`
- `AIGateBridge::_NowId`
- `AIGateBridge::_ContractManifestJson`
- `AIGateBridge::BuildRequestJson`
- `AIGateBridge::TryReadDecision`
- `TradeEngine::_PendingAiTimedOut`
- `TradeEngine::_NormalizePlanPO3State`
- `TradeEngine::_CanonicalNetRewardAfterCostR`
- `TradeEngine::_FinalizePlanEconomics`
- `TradeEngine::PendingAIOldestWallStartMs`
- `_WaitForPendingAIInTester`
- `AiDecision.contract_manifest_hash`

## Issue-by-Issue Patch Summary

### 1. Frozen Candidate Mutation

Taxonomy enrichment now happens before the final canonical seal. Post-freeze
taxonomy checking is read-only. The frozen object compares both candidate JSON
and complete payload JSON. Assertions run before the provider call, after the
Analyst response, after Critic/Adjudicator consensus, after repeatability, after
cache handling, and before response serialization.

### 2. Error Classification

Local failures use typed categories:

- `LOCAL_REQUEST_INTEGRITY_ERROR`
- `FROZEN_REQUEST_MUTATION`
- `REQUEST_IDENTITY_ERROR`
- `CANDIDATE_IDENTITY_ERROR`
- `LOCAL_PIPELINE_ERROR`

Provider configuration, transport, HTTP, timeout, schema, and response failures
remain separate. `[pipeline_failure]` records stage, original exception type and
message, provider-call status, and HTTP-send status.

### 3. Contract Compatibility

Python and MQL share manifest hash `1024838206`. Python rejects an incompatible
request before selecting the provider. MQL rejects a response with a different
manifest before applying it. Requests and responses persist the full manifest
and hash.

Version changes:

- Engine: `5.5-version-z-canonical-request-20260724-v8`
- Input schema: `po3-fvg-ai-provider-version-z-20260724-v7`
- Decision schema: `20260724_canonical_frozen_request_v10`
- Prompt contract: `20260724_canonical_frozen_request_v12`
- Request identity: `20260724_ai_request_identity_v3`
- File-bus lifecycle: `20260724_file_bus_lifecycle_v3`
- Request lifecycle: `20260724_exactly_once_request_v1`
- Manifest: `20260724_contract_compatibility_v1`

### 4. Full 120-Second Tester Wait

The hidden 15-second request-file cutoff was removed. One immutable
`ai_requested_wall_ms` drives one deadline. MT5 uses `GetTickCount64()` and
converts `InpAiWaitTimeoutRealMin * 60 * 1000`, yielding 120,000 ms at the
unchanged default of 2 minutes.

Fake-clock tests prove:

- 14, 15, 30, 60, and 119 seconds remain active.
- A valid response before 120 seconds is accepted.
- 120 seconds is timeout.
- Simulated time cannot alter the wall deadline.

### 5. Freshness Domains

During deliberate blocking `LIVE_WAIT_DEBUG`, simulated age is logged as
diagnostic and does not shorten the wall wait. Explicit debug-trading
acknowledgement remains mandatory. Live-forward freshness, identity, nonce,
session, candidate, schema, provider cohort, and fingerprint checks remain
strict.

### 6. Explicit Error Envelopes

Python error envelopes echo the immutable request/session/nonce/candidate
identity and manifest, contain no fabricated assessments, set
`DEGRADED_NON_TRADING`, `python_final_allow=false`, and risk multiplier `0.0`,
and receive a response binding hash. MQL accepts a correctly bound error
envelope as a valid non-trading terminal response; malformed error envelopes
are quarantined.

### 7. Stale and Cohort Protection

Transport IDs now include the session scope. Contract hash is part of request
identity and response binding. Incompatible transport cohorts can be archived
with `python ai_gate.py archive-incompatible-cohorts`; ledgers and outcome files
are not touched.

### 8. PO3 Displacement and BOS

Plan normalization restores `t_disp` and `t_bos` only from immutable source
timestamps. A true displacement/BOS flag with no positive timestamp rejects
with a precise lineage reason. Full PO3 families enforce
`t_sweep < t_disp < t_bos`; branch families are not incorrectly erased by a
full-sequence-only rule.

### 9. Net Reward After Cost

The canonical value is finalized before accepted-plan logging, fingerprinting,
and deterministic execution checks:

```text
net_reward_after_cost_r =
    max(0, execution_rr2)
    - max(0, spread_r)
    - max(0, slippage_r)
    - max(0, commission_r)
```

In the active structure, `execution_cost_r` is the spread-in-R field and is
copied to `spread_r`; costs are deducted exactly once.

### 10. Authority Path

A trading response must remain `FULL_STRUCTURED`, schema-complete, manifest
compatible, request-bound, candidate-bound, target-valid, repeatability-valid
when required, and risk-valid. MQL retains final authority through candidate,
execution-fingerprint, current-market, broker, risk, and session checks.

## Tests

Commands:

```powershell
python -m compileall .
python -m pytest -q
python tools/verify_v_next_controls.py --skip-mql-compile
```

Results:

- Python compileall: passed.
- Pytest: `197 passed, 99 subtests passed`.
- Repository verifier: all v-next controls passed.
- Verifier's mirrored active-source MetaEditor compile:
  `0 errors, 0 warnings`, 198,639 ms.

Representative identity test evidence:

```text
[identity_validation] valid=true
model_assessment_order=[1,0]
normalized_order=[0,1]
[ai_schema_validation] valid=true
decision_schema_version=20260724_canonical_frozen_request_v10
candidate_count=2
```

The contract-incompatibility regression proves provider selection is never
reached for an incompatible MQL request.

## MetaEditor Result

The actual active EA source was compiled with:

```text
C:\Program Files\MetaTrader 5\MetaEditor64.exe
/compile:<active PO3_AIGate_ScannerEA.mq5>
```

Result:

```text
0 errors, 0 warnings
213727 msec elapsed
```

The verifier also compiled an exact mirror of the active EA/include tree:

```text
0 errors, 0 warnings
198639 msec elapsed
```

## Before and After

Before:

```text
freeze
-> taxonomy mutation
-> frozen_request_candidate_mutation
-> mislabeled provider transport failure
-> degraded response
-> 15-second tester request-file cutoff
```

After:

```text
taxonomy enrichment
-> canonical seal and identity
-> immutable provider/consensus/repeatability/cache path
-> correctly classified local/provider outcome
-> bound response
-> full 120-second monotonic tester deadline
-> strict MQL authority gates
```

## Expected Logs

```text
[contract_compatibility] compatible=true manifest_hash=1024838206
[tester_ai_wait_started] request_id=... timeout_ms=120000 deadline_ms=...
[tester_ai_wait_progress] request_id=... elapsed_wall_ms=15000 remaining_wall_ms=105000
[provider_call_completed] quality_tier=FULL_STRUCTURED
[identity_validation] valid=true
[ai_schema_validation] valid=true decision_schema_version=20260724_canonical_frozen_request_v10
[tester_ai_wait_completed] request_id=... result=response_applied configured_timeout_ms=120000
```

On a local integrity defect:

```text
[pipeline_failure] stage=... category=FROZEN_REQUEST_MUTATION
provider_call_attempted=... http_request_sent=...
```

## Required User Steps

1. Recompile `PO3_AIGate_ScannerEA.mq5` in the active terminal data folder.
2. Confirm MetaEditor reports `0 errors, 0 warnings`.
3. Restart or reattach the EA so the terminal loads the new EX5.
4. Run `python ai_gate.py cache-audit`.
5. Archive incompatible transport/cache cohorts if reported:
   `python ai_gate.py archive-incompatible-cohorts`.
6. Regenerate tester cache with the same build and inputs using RECORD_ONLY,
   then replay with CACHE_ONLY.
7. Use LIVE_WAIT_DEBUG only for short lifecycle checks.

## Remaining Limitations

- No live provider call or broker order was forced during this engineering
  verification. The mocked full-structured path reached genuine Python
  identity/schema/consensus authority; MetaEditor compilation verified the MQL
  source.
- Strategy Tester can still advance simulated market time during a wall-clock
  provider wait. The repair prevents that movement from truncating the
  configured wait; CACHE_ONLY remains the reliable performance workflow.
- Old caches are intentionally non-authoritative under the new manifest.
- A real short tester run is still required to capture Python and MT5 logs from
  the user's provider and terminal environment.

## Final Verification Checklist

- [x] Taxonomy enrichment occurs before canonical freeze.
- [x] Frozen payload mutation fails with a local integrity category.
- [x] Local failures are not labeled provider transport errors.
- [x] Python/MQL manifest constants match.
- [x] Incompatible request blocks before provider selection.
- [x] Error envelope is identity-bound and non-trading.
- [x] Hidden 15-second cutoff removed.
- [x] Wait deadline is 120,000 ms at unchanged input value 2.
- [x] Simulated age cannot shorten the blocking wall wait.
- [x] PO3 source timestamps propagate or fail precisely.
- [x] Net reward after cost is finalized before accepted-plan authority.
- [x] Full Python suite passes.
- [x] Repository verifier passes.
- [x] Active and mirrored MQL compiles pass with zero errors and warnings.
- [ ] Real provider/terminal log captured after EX5 reload.
- [ ] Real broker execution evidence, if an AI-approved setup passes all gates.
