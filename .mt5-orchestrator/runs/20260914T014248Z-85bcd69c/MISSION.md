# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

Close three defects found by the front-end audit of the 2026-09-14 04:18-04:24 live session:

1. The EA sends candidates that the Python hard pre-gate is guaranteed to reject
   (`synthetic_fallback_crossed_obstacle_blocked`), because the two sides implement the same rule
   differently. The EA then reports the deterministic rejection as `degraded_ai_response_non_trading`.
2. The EA labels every Python `hard_pre_gate` reply as a degraded/integrity failure.
3. The gate's single-instance lease log cannot prove whether two gates held the lease at once, and
   the Windows mutex lives in the per-session `Local\` namespace.

Fail-closed behaviour must not weaken anywhere. Python's rule is the authority and stays unchanged.

## Confirmed evidence (front-end verified, do not rediscover)

- Bus: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`.
- 2026-09-14 04:21:01 the EA queued 5 requests; 3 were rejected in <1 s by Python with
  `decision_source=hard_pre_gate`, `rejection_codes=[synthetic_fallback_crossed_obstacle_blocked]`,
  `decision_quality_tier=DEGRADED_NON_TRADING`, no provider call:
  `<bus>\rejected\python_12508_1789348794__591813800_1789359636_384450234_1789359661_{#US30_12359,GBPCAD_27959,GBPJPY_26018}.json`.
- In all 13 candidates of those 3 requests: `target_arbitration_required=true`,
  `tp_model=synthetic_rr_fallback`, `obstacle_kind=crossed_opposing_imbalance` or
  `crossed_htf_opposing_imbalance`, and `target_candidates.liquidity_target.available=false`
  (e.g. GBPJPY `infeasible_reason=exceeds_max_target_distance`).
- Python rule (`python/ai_gate.py:7777-7781`, `_candidate_hard_block_reason`):
  with `reject_synthetic_fallback_after_crossed_obstacle` true, a synthetic target crossing an
  opposing obstacle passes only if `_target_arbitration_required(...)` AND
  `_has_real_liquidity_target_candidate(...)` (`ai_gate.py:5760-5773`: liquidity option `tp2 > 0`
  and `available` true). For these candidates `_target_arbitration_required` is always true, so the
  deciding term is liquidity availability.
- EA rule (`MT5_PO3_Codex Include/TradeEngine.mqh:3792-3803`, `_HardSuppressionGate`): rejects only
  when `!p.target_arbitration_required && !_AiTargetArbitrationHasAuthority(p)`. It never consults
  liquidity availability, so it forwards what Python rejects.
- The EA serializes `liquidity_target.available` from `liquidity_feasible` in
  `AIGateBridge.mqh:_TargetCandidatesJson` (`:98-228`):
  `liquidity_feasible = (liquidity_tp > 0.0 && liquidity_rr_ok && liquidity_within_max)`.
- The EA advisory line prints `threshold_reason=degraded_ai_response_non_trading` for these replies
  (`TradeEngine.mqh:19028-19032`), classifies them as stage `decision_integrity` (`:19275-19278`),
  and `AIGateBridge.mqh:1916-1926` sets `llm_quality_reject_reason = "degraded_ai_response_non_trading"`
  for every non-trading tier. `review_gate_reuse` (`:18950`) is the existing precedent for giving a
  deliberate non-trading Python answer its own label.
- `_InfrastructureAiRejection` (`TradeEngine.mqh:458-467`) lists neither token, so the AI cooldown
  applies to these rejections today and must keep applying.
- Lease: `ai_gate.py:12511-12567` uses `CreateMutexW(None, False, "Local\\PO3_AI_GATE_<id>")`;
  `_release_gate_single_instance` (`:12488`) logs nothing. `ai_gate.log` lines 189988/189989
  (pids 27848, 26708) and 211000/211001 (pids 17140, 29088) show back-to-back `acquired=true`; in
  both pairs the first pid never processed a request, so it exited before the second acquired. The
  log cannot show that.
- `request_lifecycle.process_is_available` uses `os.kill(pid, 0)`. On Windows signal 0 is
  `CTRL_C_EVENT`; do NOT use that helper for the new holder check, and do NOT change it (out of scope).

## Requirements

- R1 (EA/Python parity). In `_HardSuppressionGate`, the `InpRejectSyntheticFallbackAfterCrossedObstacle`
  branch must waive only when
  `(p.target_arbitration_required && <liquidity target feasible>) || _AiTargetArbitrationHasAuthority(p)`.
  The `InpHardRejectCrossedObstacleTarget` branch is unchanged. The reason token stays exactly
  `synthetic_fallback_crossed_obstacle_blocked`.
- R2 (single definition). "Liquidity target feasible" has ONE definition, shared by the payload and
  the gate: a free function defined in `AIGateBridge.mqh` above the class (it already includes
  `Types.mqh`, `Config.mqh`, `ExecutionAdjustmentContract.mqh`), e.g.
  `bool PlanLiquidityTargetVerdict(const TradePlan &p, double &liquidity_tp, double &liquidity_rr, bool &liquidity_rr_ok, bool &liquidity_within_max)`
  returning `liquidity_tp > 0.0 && liquidity_rr_ok && liquidity_within_max`, computed exactly as
  `_TargetCandidatesJson` does today (same `liquidity_tp`, `liquidity_rr` fallback, `risk_dist`,
  `p.fallback_max_allowed_distance`, `PlanTickSize`, `RewardWithinMaxDistance`, `InpMinLiveRR2 + 0.0001`).
  `_TargetCandidatesJson` must obtain those four values from the helper. Existing
  `python/tests/test_target_menu_feasibility.py` must pass UNMODIFIED: it requires, inside
  `_TargetCandidatesJson`, a declaration matching `bool liquidity_feasible = (... liquidity_rr_ok ... liquidity_within_max ...);`
  and `JsonKVBool("available", liquidity_feasible)` / `JsonKVBool("feasible_for_tp2", liquidity_feasible)`.
  Serialized values must be byte-identical to today for every plan.
- R3 (diagnostics). When `_ObjectiveHardPreTradeGate` rejects with
  `synthetic_fallback_crossed_obstacle_blocked`, the `_LogSetupReject` detail additionally carries
  `target_arbitration_required=`, `liquidity_target_feasible=`, `obstacle_kind=`, `tp_model=`.
- R4 (label). When a reply has `dec.decision_source == "hard_pre_gate"` and a non-trading tier:
  `reject_reason = "python_hard_pre_gate_reject"` (add a named constant beside
  `AI_REVIEW_GATE_REUSE_SOURCE`), the advisory journal line adds `python_rejection_codes=<dec.rejection_codes_json>`,
  the reject stage is `python_hard_pre_gate` (not `decision_integrity`), and
  `AIGateBridge.mqh` sets `llm_quality_reject_reason = "python_hard_pre_gate_reject"` for that source.
  Everything else stays non-trading exactly as today: no allow, risk multiplier 0, cooldown still
  applied, no change to `review_gate_reuse`, no change to any other tier's label. No Python change,
  no schema/manifest/version change.
- R5 (lease namespace). `_acquire_gate_single_instance` uses `Global\\PO3_AI_GATE_<identity>`.
  `ERROR_ACCESS_DENIED` (5) from `CreateMutexW` returns `(False, "existing_gate_for_bus_access_denied:<identity>")`.
  POSIX branch unchanged. Return signature `(bool, str)` unchanged.
- R6 (lease evidence). After a successful acquire, atomically (tmp + `os.replace`) write
  `<bus>/locks/ai_gate.instance.json`: `pid`, `ppid`, `executable`, `argv`, `hostname`, `lease`,
  `started_at` (epoch), `released_at` null. Before overwriting, read the previous record. `main()`
  logs the acquire as one line that still starts with `[single_instance] acquired=true bus=... lease=... pid=...`
  and adds `ppid=`, `executable=`, `previous_holder_pid=`, `previous_holder_started_at=`,
  `previous_holder_released=true|false|none`, `previous_holder_alive=true|false|unknown`.
  Liveness on Windows: ctypes `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION=0x1000)` +
  `GetExitCodeProcess == STILL_ACTIVE (259)`; `ERROR_INVALID_PARAMETER` (87) => false; anything else
  => unknown. POSIX: `os.kill(pid, 0)`. If the previous holder is alive AND not released, also log
  `[single_instance_anomaly] ...` (warning only; never exit, pids can be reused).
- R7 (release evidence). `_release_gate_single_instance` sets `released_at` in the record when the
  record's pid is this process, and the run command logs `[single_instance] released pid=... lease=...`
  on exit. Unit tests must never write to the live bus `ai_gate.log`.
- TST-001 MAJOR. One new focused test file `python/tests/test_hard_gate_parity_and_lease.py`:
  (a) MQL source: the crossed-obstacle waiver in `_HardSuppressionGate` requires the shared
  liquidity verdict; `_TargetCandidatesJson` obtains its liquidity values from the same helper;
  the `python_hard_pre_gate_reject` relabel and `python_hard_pre_gate` stage exist and the
  degraded label is still used for other non-trading tiers.
  (b) Python lease: holder record written with pid/lease; release sets `released_at`; re-acquire
  after release reports `previous_holder_released=true` and no anomaly; Windows mutex name uses
  `Global\\` (source or monkeypatch assertion).
  Existing `test_single_instance_guard_rejects_duplicate_for_same_bus` must pass unmodified.

## Work packages

### WP1 -- EA parity, shared liquidity verdict, relabel
Objective: R1, R2, R3, R4, MQL half of TST-001.
Runtime authority: MQL repo sources; deploy copy to the terminal only in final verification.
Allowed scope: `MT5_PO3_Codex Include/TradeEngine.mqh`, `MT5_PO3_Codex Include/AIGateBridge.mqh`,
`MT5_PO3_Codex Include/Config.mqh` (constant only, if that is where the review-gate constant lives).
Forbidden changes: Python rule, schemas, contract/manifest/version constants, inputs/defaults,
`.set` files, any other reject path, `*.bak*` files, existing tests.
Acceptance evidence: diff; serialized liquidity fields provably unchanged; waiver expression shown.
Change size: MAJOR
         Tests: focused test for main success + fail-closed path (MAJOR)
Escalation triggers: `_HardSuppressionGate` cannot call the helper (const/include order);
test_target_menu_feasibility.py cannot pass unmodified.

### WP2 -- Gate lease namespace and evidence
Objective: R5, R6, R7, Python half of TST-001.
Allowed scope: `python/ai_gate.py` (lease functions and the `run` acquire/release logging only),
the new test file.
Forbidden changes: `request_lifecycle.py`, file-bus recovery, worker count, any other command path,
existing tests.
Change size: MAJOR
         Tests: focused test for main success + fail-closed path (MAJOR)
Escalation triggers: `Global\` mutex creation fails for a normal user in this environment.

### WP3 -- Final verification and review
Objective: one full Python suite `cd python; .\.venv\Scripts\python.exe -m pytest tests -q`;
`python -m py_compile python/ai_gate.py`; copy the changed `.mqh` files to
`C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex`
and compile `...\MQL5\Experts\MT5_PO3_Codex\PO3_AIGate_ScannerEA.mq5` with
`C:\Program Files\FxPro - MetaTrader 5\metaeditor64.exe` (0 errors, 0 warnings, `.ex5` newer than every
`.mqh`, repo and deployed hashes identical; also copy the rebuilt `.ex5` into
`MT5_PO3_Codex Experts`). One logic review (`minimax-m3`) of the parity expression against
`ai_gate.py:7777-7781` and of fail-closed preservation.
Change size: SMALL

## Budgets
- target wall clock: 90 minutes
- material model invocations: <= 8
- repair attempts per approach: <= 2

## Runtime safety
- RuntimeMode: Offline
- Demo authorization: NO
- Real-money/live mutation: forbidden
- Never start, stop or signal `ai_gate.py` (a live gate, pid 12508, is running) or the MT5 terminal.
  Never touch bus queues, ledger, `python/.env`, or `python/data/*`.

## Final proof
- requirement coverage per ID;
- changed files;
- one final full Python suite run + one MQL5 compile (results recorded);
- test-integrity audit (no existing test modified);
- independent review;
- complete diff audit;
- explicit uncertainty (no live run exercises these changes until the user restarts the gate and
  re-attaches the EA).
