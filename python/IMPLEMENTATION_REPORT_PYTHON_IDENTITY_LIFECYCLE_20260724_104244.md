# Python AI Identity and File-Bus Lifecycle Repair

UTC report ID: `20260724_104244`

## Executive Summary

The confirmed identity failures were Python-owned. The provider-facing
`AIGateEnvelope` required the model to reproduce request hashes, ordered
candidate identities, provider/model IDs, candidate hashes, and execution
fingerprints. Python rejected otherwise valid `FULL_STRUCTURED` output when
that untrusted echo differed or when assessments were returned in another
order.

The provider now returns analytical content keyed only by
`candidate_index`. Python freezes the final candidate collection before the
call, owns every transport and execution identity field, normalizes returned
assessments into request order, and constructs the final strict
`AIGateEnvelope` itself. Invalid, duplicate, missing, or unknown candidate
indexes still fail closed.

The repeated same-request calls were also Python-owned. Shadow repeatability
calls ran after an identity-invalid primary and reused the authoritative
request ID. Shadow repeats now require a valid full primary, use derivative
non-trading request IDs, and cannot enter the authoritative cache.

A durable request ledger, active heartbeat, collision detection, stable
producer-file check, and heartbeat-aware startup recovery now enforce
exactly-once authority across the file bus.

No strategy threshold, PO3/FVG rule, target rule, risk rule, tester mode, or
trading default was changed.

## Root Causes

1. `AIGateEnvelope` was simultaneously a provider schema and the authoritative
   Python/MQL transport envelope.
2. The model was instructed to echo `ordered_candidate_identities`; Python
   compared that untrusted echo before assessment normalization.
3. `request_identity_hash` could be rebuilt around mutable candidate objects.
4. Candidate capping occurred in prompt compaction after identity construction.
5. Shadow repeatability launched after invalid primary responses and reused the
   same request ID.
6. Existing response files were trusted by filename before reconstructing and
   validating the request identity.
7. Startup recovery used generic processing-file age and had no active-provider
   heartbeat.
8. Producer readiness checked file size only, not modification stability plus
   successful strict JSON parsing.

## Active Files Changed

### Python runtime

- `ai_gate.py`
  - `_freeze_request_for_provider`
  - `_attach_frozen_identity`
  - `_bind_python_owned_analyst_envelope`
  - `_score_setup_ai`
  - `_run_shadow_repeat_evaluation`
  - `_stable_input_status`
  - `_quarantine_unstable_input_if_terminal`
  - `_handle_claim_deferred`
  - `process_one`
  - `_process_claimed_request`
  - `fill_tester_cache_once`
  - `main`
- `decision_integrity.py`
  - canonicalization/schema version constants
  - `canonical_decimal`
  - `normalize_and_freeze_candidates`
  - `FrozenAIRequest`
  - `freeze_ai_request`
  - `build_ai_request_identity`
- `structured_models.py`
  - `ModelCandidateAssessment`
  - `ModelAIGateOutput`
  - `ModelCriticDecision`
  - `ModelAdjudicatorDecision`
  - internal authoritative role/envelope contracts
- `decision_pipeline.py`
  - `run_qualitative_consensus`
  - Python-owned Critic and Adjudicator binding
- `request_lifecycle.py`
  - new durable `RequestIdempotencyLedger`
  - new `RequestHeartbeat`
  - process-liveness and recovery helpers
- `architecture_contracts.py`
  - `FileBusLifecycle.recover_processing`
  - lifecycle version bump

### MQL compatibility

- `MQL5/Include/MT5_PO3_Codex/Config.mqh`
  - strict contract constants only
  - no MQL execution or strategy logic changed

### Tests/verifier

- `tests/test_provider_neutral_ai.py`
- `tests/test_python_owned_identity_lifecycle.py`
- `tools/verify_v_next_controls.py`

## Contract Versions

| Contract | New version |
|---|---|
| Decision schema | `20260724_python_owned_identity_v9` |
| Prompt contract | `20260724_python_owned_identity_v11` |
| Role contract | `20260724_python_bound_roles_v3` |
| Request identity | `20260724_ai_request_identity_v2` |
| Canonicalization | `20260724_canonical_json_ticks_v1` |
| File-bus lifecycle | `20260724_file_bus_lifecycle_v3` |
| Exactly-once lifecycle | `20260724_exactly_once_request_v1` |

Old cache responses using prior decision/prompt/identity contracts remain
incompatible and cannot trade. No permissive migration was added.

## Corrected Request Lifecycle

```text
parse strict JSON
-> verify size and mtime stability
-> normalize candidates
-> reject invalid candidates
-> deduplicate exact identities
-> apply cap
-> stable sort by canonical deterministic fields
-> freeze candidate JSON
-> construct ordered candidate identities
-> hash canonical request identity once
-> durable idempotency/collision check
-> heartbeat-backed provider execution
-> parse analytical output
-> map unique candidate indexes into frozen request order
-> Python attaches authoritative identities and plan fields
-> validate final strict envelope
-> qualitative consensus
-> repeatability authority
-> deterministic risk/target gates
-> persist validated response in idempotency ledger
-> atomic response write
-> tester cache export when authoritative
-> completion/archive
```

Wall-clock creation time remains diagnostic but is excluded from the canonical
identity hash. Temporary filenames, worker IDs, process IDs, response times,
and model-produced identity fields are also excluded.

## Python-Owned Identity

The model no longer returns:

- request ID or request identity hash;
- ordered candidate identities;
- candidate ID/hash or execution fingerprint;
- provider/model identity;
- runtime-input hash;
- schema/prompt/taxonomy versions;
- authoritative entry, SL, TP, or assessed fingerprint.

The model returns one analytical assessment per `candidate_index` and a
`selected_candidate_index`. Python rejects:

- duplicate indexes;
- missing indexes;
- unknown indexes;
- selected indexes outside the frozen request;
- assessment-count mismatch;
- mutation of candidate JSON before or after provider latency.

Assessment array order is explicitly non-authoritative.

## Exactly-Once and Recovery

The durable key includes:

```text
request_id
request_identity_hash
provider_id
model_id
prompt_contract_version
schema_fingerprint
```

Lifecycle states are:

```text
CREATED -> CLAIMED -> PROVIDER_RUNNING -> RESPONSE_VALIDATED
-> RESPONSE_WRITTEN -> COMPLETED -> ARCHIVED
```

A repeated ID with the same identity reuses the persisted validated response.
A repeated ID with another identity is blocked as
`request_id_identity_collision`. Active heartbeat metadata includes worker ID,
PID, hostname, claim time, provider start time, heartbeat time, and expected
maximum provider duration.

Startup recovery requires expired heartbeat, unavailable worker where
checkable, no response/completion marker, and an additional stale grace period.
Fresh three-to-four-minute provider calls are not reclaimed.

## Tester Cache

`LIVE_WAIT_DEBUG` remains non-authoritative for replay. The authoritative path
remains:

```text
RECORD_ONLY request
-> offline Python full structured processing
-> frozen identity validation
-> authoritative tester cache export
-> CACHE_ONLY replay
```

Cache export still requires full structured quality, complete mandatory fields,
valid candidate/fingerprint binding, compatible schemas, and non-debug
workflow origin.

## Proof

The representative two-candidate provider fixture returned assessments in
reverse order:

```text
[identity_validation] valid=true request_id=identity-e2e
expected_count=2 actual_count=2 expected_order=[0, 1]
model_assessment_order=[1, 0] normalized_order=[0, 1]
identity_schema_version=20260724_ai_request_identity_v2
canonicalization_version=20260724_canonical_json_ticks_v1

[ai_schema_validation] valid=true
decision_schema_version=20260724_python_owned_identity_v9
candidate_count=2 selected_candidate_hash=HASHB12345678

[identity_e2e_proof] quality_tier=FULL_STRUCTURED allow=true
source=ai_consensus_full_structured chosen=1 llm_quality_score=8.75
provider_calls=['analyst', 'critic'] assessment_order=[0, 1]
```

This proves:

- a valid full response is no longer rejected because model order differs;
- actual model metrics survive identity validation;
- the selected candidate is the independently assessed candidate;
- only one Analyst call and one required Critic call occurred;
- transport identity was attached by Python.

Invalid duplicate assessment indexes still produce:

```text
[identity_validation] valid=false
reason=model_candidate_mapping_invalid
duplicate_candidate_indexes=[0]
missing_candidate_indexes=[1]
```

## Tests Added

`tests/test_python_owned_identity_lifecycle.py` covers:

- deterministic pre-hash sorting;
- exact deduplication before identity;
- mutation detection after freezing;
- dictionary-order invariance;
- wall-clock exclusion from hash;
- deterministic finite decimal formatting;
- identity change when candidate/index binding changes;
- reverse model assessment order;
- preservation of actual LLM metrics;
- duplicate/unknown/missing candidate index rejection;
- validated response reuse;
- request-ID identity collision;
- active heartbeat recovery protection;
- stale dead-worker recovery eligibility;
- processing-file recovery safety;
- identity-invalid primary cannot launch shadow repeats;
- derivative non-authoritative shadow request IDs.

Existing provider tests were migrated to analytical-only role schemas.

## Commands and Results

```text
python -m compileall .
PASS

python -m unittest discover -s tests -p "test_*.py"
Ran 184 tests in 6.730s
OK

python tools/verify_v_next_controls.py
[verify] all v-next controls passed

MetaEditor64.exe active EA compile through tools/verify_v_next_controls.py
Result: 0 errors, 0 warnings, 140583 msec elapsed
```

The verifier also exercised RECORD_ONLY tester cache writing and reported:

```text
[tester_cache_export] written=true
[verify] PASS MQL tester replay cache export
[verify] PASS fill tester cache once
```

## MQL Impact

No producer serialization defect was required to explain the observed false
identity mismatch. The MQL change is limited to synchronized strict contract
versions in `Config.mqh`. Python owns the repaired identity and lifecycle
behavior.

## Remaining Operational Limits

- No paid live OpenAI call was made during validation. The real provider
  transport and schemas were exercised with deterministic mocked
  `ProviderResult` objects through the production orchestration path.
- A new live/debug run is still required to confirm the external provider now
  emits `FULL_STRUCTURED` followed by `identity_validation valid=true` under
  actual latency.
- A crash after the provider returns but before Python validates and durably
  records the response can require a new provider call after stale recovery.
  Once `RESPONSE_VALIDATED` is persisted, restart reuse is exactly-once.
- The first run after this schema bump will intentionally miss old decision
  cache cohorts.

## Safety Confirmation

- Identity mismatch remains fail-closed.
- Unknown, duplicate, or missing assessment indexes remain fail-closed.
- Provider failures cannot become rule-only approval.
- Degraded responses remain non-trading and non-cacheable.
- Live-wait debug remains replay-non-authoritative.
- Tester workflow flags remain excluded from economic decision signatures.
- No PO3/FVG definitions, target feasibility rules, AI thresholds, RR rules,
  watchlist checks, risk controls, family defaults, or trading defaults changed.
