# Mission

Front-end: Codex
Tier: Deep
RuntimeMode: Offline

## Outcome

Implement crash- and shutdown-safe continuation of pending AI decisions and shadow-tracked candidate outcomes. After restart, a valid pending response may resume the normal assessed-plan workflow exactly once, but only while the live market remains inside the existing canonical entry-drift/max-distance contract and all identity, structure, target, broker, session, portfolio, and risk gates still pass. Extend the shadow outcome observation horizon represented by the historical 8-hour setting to 3 days, including a safe migration of already-persisted unresolved trackers, without changing broker-order expiry by accident.

## Confirmed baseline and authority map

- Prior delegated attempt `20260912T214338Z-3f097327` was interrupted after about 41 minutes because its WP1 worker stopped making material progress. It left exactly one untrusted five-line declaration, `m_recovered_pending_req_ids[]`, near the top of `TradeEngine.mqh`, with no implementation, tests, or report. Audit, complete, replace, or remove that fragment; do not treat it as baseline evidence. This is the second and final meaningful attempt. Change approach: pass only targeted function bodies/symbols to workers, use small bounded patches, and do not attach or reread the entire 1 MB `TradeEngine.mqh` in a worker context.
- Product authority: `CLAUDE.md`, `python/CLAUDE.md`, and the Python/MQL/file-bus contracts named there.
- Baseline focused suite before edits: `184 passed, 1 skipped, 81 subtests passed` for `test_shadow_outcome_lifecycle.py`, `test_python_owned_identity_lifecycle.py`, `test_execution_contract_coherence.py`, and `test_authoritative_response_contract.py`.
- Current MQL state paths: `watchlist.ndjson`, `pending_ai.ndjson`, `shadow_pending.ndjson`, `shadow_tracker_index.ndjson`, and `shadow_tracker_quarantine.ndjson`, scoped by account and magic number under the bus log runtime state.
- Current Python file-bus lifecycle already recovers stale `processing` artifacts using heartbeat/lease evidence. Do not replace or weaken it.
- Current MQL `Init` restores pending AI state, watchlist state, and shadow trackers; current `Deinit` deliberately converts all pending AI groups to shutdown rejects, archives their artifacts, clears them, and saves an empty pending snapshot. That behavior conflicts with the requested clean-shutdown continuation.
- Current pending-AI persistence is periodic; enqueue/removal transitions are not durably checkpointed at the transition site, leaving crash windows.
- Current canonical adverse entry-distance authority is `InpMaxEntryDriftR` through `ExecutionAdjustmentContract`; target maximum distance has separate canonical helpers. Reuse those authorities rather than inventing a second threshold.
- The literal 8-hour settings found are `InpShadowCandidateHorizonMinutes=480` in `scalp_v2_full.set` and pending broker order values such as `InpPendingOrderExpiryMin=480`. The active compiled shadow default is currently 1440 and the swing preset is 1440. The user's uncertain phrase “logs/tracks” refers to research/shadow tracking for this mission. Do not change broker pending-order expiry unless end-to-end evidence proves that it is the intended tracking control.
- Existing user changes/runtime artifacts are present. Preserve all unrelated dirty files, especially `CLAUDE.md`, Python data stores, and Python logs.

## Requirements

- REC-001: Persist every material pending-AI group transition atomically and immediately enough that an abnormal process/terminal loss cannot orphan a just-queued group or resurrect an already-consumed group from an older snapshot.
- REC-002: A normal shutdown must preserve resumable pending-AI groups and their immutable request lineage rather than synthesize a rejection or archive their request/response solely because the EA stopped. Tester-only semantics may remain deliberately non-resumable where required, but must be explicit and tested.
- REC-003: On live-mode startup, reconcile restored `pending_ai` state with request, processing, response, and terminal archive/ledger evidence. Continue waiting for a legitimately in-flight request or consume an already-complete response. Do not call the provider twice and do not execute/order twice. Missing, partial, malformed, incompatible, or contradictory artifacts must fail closed with a precise terminal reason.
- REC-004: A recovered approval must pass the same response schema, immutable identity, candidate binding, assessed fingerprint, target arbitration, and MQL final-decision path as a non-recovered response. Recovery must not create a second authority path.
- REC-005: Before a recovered approval can enter the watchlist or place/modify an order, refresh relevant live state and enforce the canonical adverse entry drift/max-distance limit from the assessed/planned entry, plus the canonical target-distance, structural invalidation, target-already-reached, execution-cost, broker-distance, session, portfolio, and hard-safety gates. If the market is beyond the allowed entry distance, terminally reject that recovered opportunity with an explicit reason; never chase or force a trade.
- REC-006: Recovery must be idempotent across repeated restarts at the dangerous boundaries: response present before startup, crash after response parsing, crash after watchlist insertion, crash after pending removal, and broker order/position already present. At most one authoritative provider call, one live plan/watchlist identity, and one broker order effect may result per request/candidate identity.
- SHD-001: Persist, restore, and continue evaluating every compatible unresolved shadow tracker after both crash and clean shutdown. Preserve entry activation, progress mask, MFE/MAE, ambiguity, data-retry, and decision-attribution state. Produce exactly one terminal resolution per variant and keep all shadow data non-trading authority.
- SHD-002: Reconcile shadow state with decision recovery so a response arriving during downtime updates the matching shadow variants once without resetting or replacing their counterfactual price path.
- RET-001: Make the shadow candidate outcome horizon 3 days (`4320` minutes) in the authoritative compiled default and every repository-owned applicable preset/config override, including the historical 8-hour override. Keep broker pending-order expiry, AI request timeout, watchlist age, and identity-dedup retention as distinct concepts.
- RET-002: Safely extend already-persisted unresolved trackers created under the shorter horizon to at least `observed_at + 4320 minutes` on restore, never shorten a later explicit horizon, never reopen an already-resolved variant, and durably persist/log the migration so the system updates itself without manual file editing. Ensure the observe-once/terminal-once identity remains retained beyond the effective outcome horizon.
- OBS-001: Add bounded, structured recovery diagnostics and counters that make startup reconciliation, recovered response consumption, entry-distance acceptance/rejection, shadow restoration/migration, deduplication, and terminal disposition auditable without log spam or secrets.
- TST-001: Add regression tests that fail against the pre-change source for crash windows, clean shutdown, response-before-restart, in-flight request continuation, stale/malformed/incompatible response failure, max entry drift boundaries, target-already-reached, duplicate restart, shadow continuation, horizon migration, and exactly-once invariants.
- TST-002: Run focused tests, all tests touching changed modules/contracts, the complete Python test suite, available static/contract checks, affected MQL compilation, and an offline realistic file-bus/harness recovery scenario. Record exact commands, exit codes, counts, compile warnings/errors, and explicit limits of any behavior not executable Offline.
- SAF-001: Do not change AI/risk thresholds to obtain trades; do not weaken fail-closed behavior; do not touch secrets, the live Common Files bus, a running gate/terminal, deployed terminal sources/presets/binaries, or broker state. Do not claim deployment or live verification.

## Work packages

### WP1 — Recovery contract and durable state transitions

Objective: Trace the complete Python + MQL + state-store + file-bus lifecycle, identify the earliest incorrect shutdown/crash states, and implement REC-001 through REC-006 and OBS-001 without a parallel decision path.
Runtime authority: repository source and isolated temporary test buses only.
Allowed scope: `MT5_PO3_Codex Include/*.mqh`, `MT5_PO3_Codex Experts/*.mq5`, Python lifecycle/contract helpers only if genuinely required, and focused tests/fixtures.
Forbidden changes: live/deployed terminal tree, Common Files production bus, `.env`, provider selection, thresholds unrelated to recovery, schema re-interpretation, forced approvals/trades.
Acceptance evidence: explicit lifecycle/state-transition map; pre-fix failing tests; immediate atomic checkpoints; startup reconciliation matrix; exactly-once proof; canonical entry-drift and target-distance gates exercised at pass/fail boundaries; bounded recovery logs.
Tests: focused unit/source-contract tests plus an isolated realistic file-bus restart scenario covering response-present and request-in-flight cases.
Escalation triggers: a required recovery decision cannot be made without changing external broker state; identity cannot be preserved from durable artifacts; proposed behavior requires weakening an existing hard gate.

### WP2 — Shadow continuation and three-day migration

Objective: Implement SHD-001, SHD-002, RET-001, and RET-002 while keeping shadow research strictly non-authoritative and broker-order timing separate.
Runtime authority: repository source and isolated temporary fixtures only.
Allowed scope: shadow lifecycle fields/helpers, state serialization, authoritative repository presets, migration/reconciliation tests, and version constants when a real contract change requires them.
Forbidden changes: reopening resolved outcomes, deleting/quarantining compatible samples, using shadow results as trade authority, changing broker pending-order expiry merely because it also contains 480 minutes, editing deployed presets.
Acceptance evidence: old 480/1440-minute unresolved fixtures restore with an effective 4320-minute horizon and retain prior progress; later horizons are unchanged; resolved variants stay resolved; restart continues M1/tick feeding; index retention outlives the migrated horizon; all changes persist atomically and are logged once.
Tests: shadow lifecycle suite, state round-trip/migration tests, repeated-restart terminal-once tests, and preset/default synchronization assertions.
Escalation triggers: migration would require rewriting immutable provider/request identity or historical terminal outcomes.

### WP3 — Full verification and independent review

Objective: Prove all requirements, inspect the complete diff, and independently review state/idempotency/execution safety and test integrity.
Runtime authority: Offline only.
Allowed scope: tests, temporary harness artifacts outside production bus, report evidence, and source corrections found by review within the mission scope.
Forbidden changes: demo/live actions, deployment claims, unbounded repair loops, unrelated cleanup.
Acceptance evidence: full Python suite; changed MQL/EA compile with 0 errors and 0 warnings; isolated end-to-end recovery evidence; actual model IDs; independent logic and adversarial reviews from different configured model families; requirement-by-requirement status; final diff and test-integrity audits; explicit uncertainty.
Tests: all required by TST-002. If an MQL behavior cannot be dynamically executed Offline, provide falsified source/contract tests plus compile evidence and mark the runtime observation unverified.
Escalation triggers: more than two meaningful failures with the same approach, projected Deep budget overrun, or inability to compile/test affected MQL without mutating deployed runtime.

## Budgets

- target wall clock: 180 minutes
- material model invocations: <= 12
- repair attempts per approach: <= 2
- use the normal low-cost model map; no vision work is needed

## Runtime safety

- RuntimeMode: Offline
- Demo authorization: NO
- Real-money/live mutation: forbidden
- Production Common Files bus access: read-only only if indispensable for evidence; prefer the checked-in snapshots and isolated temporary buses.
- Deployment to the FxPro terminal: forbidden in this mission.

## Final proof

- requirement coverage with evidence for every stable ID;
- confirmed root causes and earliest incorrect authoritative states;
- changed files/functions and all schema/version/migration effects;
- focused and full test commands with exact results;
- MQL compilation results and binary/source locations used;
- isolated end-to-end success and failure evidence;
- test-integrity audit including pre-fix falsification;
- complete final diff audit including unrelated-file preservation;
- independent logic and adversarial review findings and their closure;
- actual model IDs and budget summary;
- explicit unresolved uncertainty and user deployment steps;
- no claim of live/demo verification.
