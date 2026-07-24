# Version Z Provider-Neutral AI Implementation Report

Generated: 2026-07-18 03:30:20 UTC

## Executive summary

Version Z now selects exactly one AI transport at startup through the required
`AI_USE_REMOTE_API=true|false` contract. Remote mode constructs only the remote
provider and permits only configured remote-model fallback. Local mode constructs
only the configured OpenAI-compatible provider and permits only local-model
fallback. Provider failure, ambiguous configuration, invalid evidence, malformed
role output, unresolved role disagreement, failed health/schema capability, and
contract mismatch all fail closed; none can become a rule-only live approval.

Both transports use the same deterministic evidence envelope, family profile,
historical retrieval, Analyst/Critic/conditional-Adjudicator pipeline, consensus
resolver, strict decision-integrity checks, repeatability authority, risk gates,
cache partitioning, file-bus response, and MQL execution authority.

The active deployment was hash-compared against the tested staging manifest:
20 Python files and 5 MQL include files matched, with zero mismatches. The private
`.env` was not read, printed, copied, or modified. It must explicitly receive
`AI_USE_REMOTE_API=true` for current remote operation or `false` for local
operation; omission intentionally removes live AI authority.

## Active files changed

### Python

- `.env.example`: sanitized provider-neutral configuration contract.
- `ai_gate.py`: `AIGateRuntimeConfig`, startup provider selection,
  `_score_setup_ai`, compatibility `_score_setup_openai`, strict role orchestration,
  memory retrieval/persistence, provider-aware cache/repeatability identity,
  shadow comparison, observability, and fail-closed response export.
- `ai_provider.py`: `AIProvider`, `RemoteAPIProvider`,
  `LocalOpenAICompatibleProvider`, `UnavailableProvider`, strict JSON handling,
  health/schema probe, same-provider fallback, bounded retry, circuit breaker,
  context budget, sequential local execution, and provider metrics.
- `decision_evidence.py`: canonical `DecisionEvidenceEnvelope`, authoritative
  numeric lineage, deterministic validation, and compact provider-neutral evidence.
- `family_context.py`: versioned deterministic family registry and one-profile-only
  prompt context for all supported Version Z setup taxonomies.
- `decision_pipeline.py`: strict Critic and Adjudicator schemas, evidence-backed
  objection validation, conditional adjudication, and deterministic consensus.
- `trade_memory.py`: versioned SQLite memory, locking, deduplication, clean/exact
  outcome ingestion, quarantine, leakage-safe deterministic analogue retrieval.
- `architecture_contracts.py`: provider-aware architecture/cohort contracts.
- `decision_integrity.py`: provider-neutral decision, prompt, and role versions;
  provider/model/fingerprint/generation/retrieval binding.
- `expectancy_report.py`: provider and role-output cohort metadata.
- `governance_contracts.py`: provider-neutral governance compatibility.
- `openai_usage_logger.py`: provider-neutral usage records while preserving remote
  token-cost reporting and zero remote cost for local calls.
- `po3_env.py`: selective dotenv loading so local mode does not load the remote
  secret into process configuration.
- `runtime_governance.py`: provider identity in behavior contracts.
- `tools/verify_v_next_controls.py`: provider-neutral fixtures and source checks.
- `tests/test_provider_neutral_ai.py`: 23 focused configuration, routing,
  orchestration, retrieval, cache, and end-to-end tests.
- `tests/test_decision_integrity.py`: provider-aware strict binding regressions.
- `tests/test_version_z_reliability.py`: provider-neutral scoring hook update.
- `docs/ai_provider_configuration.md`: remote/local setup, LM Studio/Bionic-compatible
  endpoint example, migration, health, privacy, and run instructions.
- `docs/ai_cost_controls.md`: provider-neutral cost/cache semantics.

### Active MQL include tree

- `Config.mqh`: synchronized engine, provider, evidence, family, memory, retrieval,
  role, consensus, prompt, decision, ledger, and repeatability contract versions.
- `Types.mqh`: provider/model/generation/health and Analyst/Critic/Adjudicator
  fingerprints in AI decisions, trade plans, and persisted attribution.
- `AIGateBridge.mqh`: strict provider-neutral response parsing, duplicate/contract
  validation, request binding, and replay response rebinding after original proof.
- `StateStore.mqh`: provider and role identity persistence/recovery validation.
- `TradeEngine.mqh`: provider-aware cache validation, cohorts, ledgers, shadow rows,
  completed outcomes, and final MQL contract enforcement.

No Expert `.mq5` file required a change; the existing EA entry points continue to
call the changed include implementation. No `.set` file or strategy threshold was
changed.

## Provider selection and fallback

The switch is parsed once by `AIGateRuntimeConfig.from_env()` and one provider is
constructed during `ai_gate.py` startup.

| Selected mode | Constructed transport | Allowed fallback | Forbidden fallback |
| --- | --- | --- | --- |
| `AI_USE_REMOTE_API=true` | `RemoteAPIProvider` | remote primary to configured remote fallback models | remote to local |
| `AI_USE_REMOTE_API=false` | `LocalOpenAICompatibleProvider` | local role model to configured local fallback model | local to remote |
| missing/invalid | `UnavailableProvider` | none | all; result is no-trade |

Provider selection is absent from business scoring branches. The compatibility
name `_score_setup_openai()` delegates to `_score_setup_ai()` and does not select a
transport. Fallback model and provider identity are included in fingerprints.

## Environment contract

Required selector:

```env
AI_USE_REMOTE_API=true
```

Remote configuration uses `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`,
`AI_GATE_MODEL`, `AI_GATE_FALLBACK_MODELS`, `AI_GATE_REASONING_EFFORT`, and
`AI_OPENAI_TIMEOUT_SEC`. Existing model environment aliases remain migration inputs
where required, without changing provider authority.

Local configuration uses `LOCAL_AI_BASE_URL`, `LOCAL_AI_API_KEY`,
`LOCAL_AI_MODEL`, role-specific model IDs, optional audit-only model path,
health path, timeout, retries, output budget, temperature, top-p, seed, thinking,
JSON-schema requirement, parallelism, context budget, and retrieval top-k.

Additional controls include explicit non-loopback acknowledgement, local circuit
breaker settings, trade-memory path, and `AI_SHADOW_COMPARE_PROVIDERS=false`.
Local mode defaults to loopback-only and never requires or transmits the remote API
key. Safe startup summaries omit keys, authorization values, prompts, account data,
and the local model path.

## Authoritative decision path

1. MQL constructs the candidate and authoritative broker/market numbers.
2. Python validates identity, required runtime inputs, hard blockers, finite values,
   freshness, units, and lineage before provider invocation where possible.
3. Deterministic taxonomy selects exactly one versioned family profile. Unknown,
   conflicting, or incomplete live taxonomy abstains.
4. `TradeMemoryStore` retrieves only clean, exact, completed, provider-neutral,
   noncurrent, nonlineage-duplicate analogues.
5. Analyst receives compact canonical evidence and emits strict JSON.
6. Critic independently receives the same evidence, not the Analyst verdict.
7. Adjudicator runs only for disagreement, abstention, low confidence, disputed
   material objection, boundary proximity, or repeatability instability.
8. `run_qualitative_consensus()` resolves only supported qualitative issues and
   cannot override deterministic authority.
9. Existing schema, candidate hash, execution fingerprint, target, repeatability,
   risk, and MQL broker gates remain authoritative.
10. MQL remains final execution authority.

The model cannot calculate or alter entry, stop, targets, position size, costs,
RR, exposure, broker feasibility, or calibrated statistical fields. Unsupported or
truncated role output is no-trade after one bounded same-provider schema retry.

## Evidence, family context, and memory

Authoritative numerics carry value, unit, source, observation time, freshness,
validity, and lineage hash. The compact envelope contains identity, runtime,
instrument, setup taxonomy, sequence, regime, HTF/LTF context, liquidity,
entry/invalidation, targets/obstacles, execution costs, risk, correlations,
validation, historical analogues, and authority manifest.

The family registry supports the repository taxonomies requested by the contract,
including micro FVG midpoint/edge reversal, breaker retest, OTE reversal,
continuation, nested continuation, range/session reentry, failed-breakout reclaim,
and full PO3 reversal/continuation. It supplies only the resolved family profile.

Trade memory is SQLite-backed with explicit connection closure, locking, schema
versioning, immutable pre-entry snapshots, role/provider outputs, deduplication,
and quarantine. Open trades and incomplete, suspicious, unreconciled, or
non-exact outcomes cannot become retrieval samples. Retrieval reports
`INSUFFICIENT_SAMPLE` instead of inventing a prior.

## Local health and runtime safety

Local startup validates URL class, loopback policy, the configured models endpoint,
model availability, and a minimal strict structured-output probe. Live authority is
withheld if any check fails. Runtime transport and schema retries are bounded, local
parallelism is one, the context budget is enforced before transport, and repeated
failure opens a circuit breaker. While open, the file bus receives no-trade without
cross-provider fallback or a retry storm.

The implementation integrates with OpenAI-compatible HTTP servers and does not
automate LM Studio/Bionic GUIs or load duplicate GGUF weights in Python.

## Cache, repeatability, and shadow isolation

Cache and repeatability identities include provider mode/ID, endpoint class,
configured and actual model, model fingerprint, generation settings affecting
behavior, prompt/decision/role/provider/family/retrieval contracts, taxonomy, and
input fingerprint. Remote and local approvals therefore cannot share authority.

Tester workflow-only flags remain excluded from the economic decision signature.
Strict MQL replay validation checks provider contracts before transient request,
session, nonce, workload, and behavior bindings are safely rebound.

Provider shadow comparison is disabled by default, skipped in `LIVE_FORWARD`, and
has no trading, cache, memory, or outcome-attribution authority. To preserve the
local privacy contract, selected local authority never constructs a remote shadow.
Selected remote research may compare a configured local provider. Promotion still
requires repository-specific chronological out-of-sample evidence.

## Observability

Decision and ledger records now carry provider mode/ID, endpoint class, configured
and actual model, fallback model, model fingerprint, prompt/decision/role/family/
memory/retrieval versions, generation identity, input and role fingerprints,
retrieved analogue IDs, resolver reason, latency, retry, and health state. Missing
token counts or throughput remain unavailable rather than fabricated. Local calls
record zero remote cost; remote usage keeps existing cost reporting.

## Contract versions

- Engine: `5.5-version-z-provider-neutral-20260718-v6`
- Engine input: `po3-fvg-ai-provider-version-z-20260718-v5`
- Architecture: `20260718_provider_neutral_architecture_v4`
- Provider: `20260718_provider_neutral_transport_v1`
- Evidence: `20260718_decision_evidence_v1`
- Family: `20260718_family_context_v1`
- Trade memory: `20260718_trade_memory_v1`
- Retrieval: `20260718_hybrid_analogue_retrieval_v1`
- Role: `20260718_analyst_critic_adjudicator_v1`
- Consensus: `20260718_deterministic_consensus_v1`
- Prompt: `20260718_provider_neutral_family_memory_v9`
- Decision: `20260718_provider_neutral_consensus_v7`
- Target: `20260717_target_fingerprint_authority_v6`
- Repeatability: `20260718_provider_neutral_repeatability_v3`
- Ledger: `20260718_trade_ledger_provider_identity_v8`

Old cached responses missing these strict provider/role/evidence contracts cannot
trade. No pass-friendly migration was added.

## Validation performed

Active commands and results:

```text
python -m compileall -q .
PASS

python -m unittest discover -s tests -p 'test_*.py'
Ran 159 tests in 8.919s - OK

python tools/verify_v_next_controls.py --skip-mql-compile
PASS - all v-next controls

python tests/run_golden_validators.py
case_count=10, mismatch_count=0

python tools/verify_v_next_controls.py
PASS - all controls and active MetaEditor compile
```

Active MetaEditor compiler log result:

```text
Result: 0 errors, 0 warnings, 252615 msec elapsed, cpu='X64 Regular'
```

The focused provider suite contains 23 tests covering explicit selector parsing,
invalid/missing switch, secret redaction, loopback enforcement, selected-provider
construction, local model/schema health probe, same-provider-only fallback,
context budget, bounded malformed-output retry, Analyst/Critic agreement,
adjudication, unresolved abstention, evidence-backed vetoes, deterministic
leakage-safe retrieval, provider/model/generation/retrieval cache partitioning,
tester signature stability, shadow nonauthority/privacy, pre-model evidence failure,
and a mocked local response through the real schema/consensus path.

`pytest` is not installed in the active virtual environment; no dependency was
downloaded. The complete repository `unittest` suite and standalone verifier were
used and passed.

## Run configuration

Remote mode:

```powershell
$env:AI_USE_REMOTE_API='true'
.\.venv\Scripts\python.exe ai_gate.py
```

Local mode after starting the configured OpenAI-compatible server:

```powershell
$env:AI_USE_REMOTE_API='false'
$env:LOCAL_AI_BASE_URL='http://127.0.0.1:1234/v1'
$env:LOCAL_AI_MODEL='qwen3.5-9b'
.\.venv\Scripts\python.exe ai_gate.py
```

Persistent configuration should be placed in the private `.env` using the sanitized
`.env.example` as a reference. The current private `.env` was intentionally not
modified. Until the explicit selector is added, startup fails closed by design.

## Remaining limitations and operational evidence

- No paid remote API request was made during this implementation.
- No real LM Studio, Bionic-compatible runtime, llama.cpp server, or Qwen GGUF was
  running for a live health/schema probe. Transport behavior was tested with mocked
  HTTP while exercising the real orchestration and schemas.
- No live/demo broker trade was opened, so this work does not add broker-fill or
  profitability evidence.
- No claim is made that local Qwen matches or exceeds the remote model. That requires
  clean, homogeneous, provider-separated, chronological out-of-sample outcomes.
- Historical memory becomes useful only after clean exact-attribution completed
  outcomes exist; insufficient evidence remains explicit and nonfabricated.

## Requirement status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Explicit remote/local switch | Implemented and tested | `AIGateRuntimeConfig`; selector tests |
| Provider abstraction | Implemented and tested | `ai_provider.py`; routing tests |
| No cross-provider fallback | Implemented and tested | same-provider fallback tests and fingerprints |
| Strict deterministic evidence | Implemented and tested | `decision_evidence.py`; pre-provider failure test |
| Deterministic family intelligence | Implemented and tested | `family_context.py`; taxonomy/retrieval tests |
| Durable historical memory | Implemented and tested locally | `trade_memory.py`; dedupe/quarantine/retrieval tests |
| Analyst/Critic/Adjudicator | Implemented and tested | `decision_pipeline.py`; consensus tests |
| Deterministic final resolver | Implemented and tested | `run_qualitative_consensus`; no-trade cases |
| Local token discipline | Implemented and tested | context budget and sequential provider path |
| Local health/circuit safety | Implemented and mocked-transport tested | provider health/schema/circuit code |
| Provider-neutral observability | Implemented and source verified | response, cache, ledger, MQL fields |
| Non-authoritative shadow comparison | Implemented and tested | live skip/local privacy/non-authority tests |
| Cache/repeatability isolation | Implemented and tested | provider/model/fingerprint contract tests |
| MT5 compatibility | Implemented and compiled | active MetaEditor: 0 errors, 0 warnings |
| Real local Qwen operational proof | Not performed | no local server was running |
| Real remote operational proof | Not performed | no paid call was made |

## Settings-preservation confirmation

No PO3/FVG definitions, setup thresholds, family defaults, RR floors, target
arbitration settings, model defaults, Flex behavior, symbol universe, portfolio
risk defaults, `.set` files, or broker execution rules were loosened or changed.
The implementation changes transport selection, evidence/role orchestration,
provider identity, cache/repeatability isolation, memory, observability, and strict
failure behavior only. It makes no profitability claim.
