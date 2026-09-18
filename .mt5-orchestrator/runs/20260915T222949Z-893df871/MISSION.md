# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

Make the deterministic setup-score floor an exact, per-family gate that is enforced before any
candidate reaches the AI (or the tester bootstrap path), with no soft/hard derivation and no
policy/tier/staleness modifiers. Align the Config.mqh floor defaults with the user's
`deterministic.set`, and generate two new presets (`scalp_v3.set`, `swing_v3.set`).

User rule, verbatim intent: "if we say 170 is the floor, then we shouldn't have -5 and -11".
The configured family number IS the floor: `setup_score < floor` rejects, `setup_score >= floor`
passes. Nothing may raise or lower it.

Fail-closed behaviour must not weaken anywhere. No trade forcing.

## Confirmed evidence (front-end verified, do not rediscover)

Paths:
- Repo includes: `MT5_PO3_Codex Include\` ; repo EA: `MT5_PO3_Codex Experts\PO3_AIGate_ScannerEA.mq5/.ex5`.
- Deployed: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\`
  and `...\MQL5\Experts\MT5_PO3_Codex\`. Repo and deployed are currently byte-identical (all .mqh,
  .mq5, .ex5 hashes match; .ex5 2026-09-15 19:55:38 newer than newest .mqh 19:51:34).
- Presets: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Presets\`
  contains `deterministic.set` and `Swing_v2_full.set`. Both are UTF-16 LE with BOM (FF FE), 390 input
  lines each, format `Name=value||start||step||stop||N`, first lines are `;` comments.
- Compiler: `C:\Program Files\FxPro - MetaTrader 5\metaeditor64.exe` (never the other MT5 install).
- The working tree has many unrelated uncommitted modifications (git status). Do not revert,
  reformat or commit anything.

Current floor code (`MT5_PO3_Codex Include\TradeEngine.mqh`):
- `_FamilySetupFloor` (~5431-5446): line 5432 returns `InpSetupFloorSession` for ANY family whose
  `entry_branch == session_reentry`, before the family lookup. `_ResolveSetupTaxonomy` (~5328) can
  resolve a `session_reentry` branch to `FULL_PO3_CONTINUATION` (full_scope && continuation), so a
  full-PO3 continuation currently gets floor 28 instead of `InpSetupFloorFullPO3`. Unknown family falls
  through to `InpSetupFloorRange` (lenient).
- Family map (`_FamilyForTaxonomy` ~5283): FULL_PO3_REVERSAL->full_po3_reversal;
  FULL_PO3_CONTINUATION->full_po3_continuation; FAILED_BREAKOUT_RECLAIM->micro_failed_breakout_reclaim;
  MICRO_RANGE_REENTRY, MICRO_SESSION_REENTRY->micro_range_reentry; MICRO_CONTINUATION_FVG,
  MICRO_NESTED_CONTINUATION->micro_continuation_fvg; MICRO_FVG_EDGE_REVERSAL, MICRO_BREAKER_RETEST->
  micro_bisi_sibi_edge; MICRO_FVG_MID_REVERSAL, MICRO_OTE_REVERSAL->micro_po3_reversal.
  `_FamilyGroup` (~5384) maps legacy names reversal/continuation/failed_breakout/range/session/edge/breaker.
- `_DeterministicSetupScoreFloor` (~13055-13066): replaces the floor for `stale_fvg` with
  `MathMax(InpSetupFloorRange, InpSetupFloorReversal) + 4.0` (cross-family: a stale micro_po3_reversal
  needs 175 instead of 149), and adds `InpSetupFloorTierBExtra` / `InpSetupFloorTierCExtra` when
  `!InpRequireConfirmedPO3ForExecution`.
- `_ApplyPreAiSetupFloor` (~6255-6295): soft = policy or `MathMax(24, floor-5)`, hard = policy or
  `MathMax(18, soft-6)`; below hard -> `pre_ai_floor_hard`; below soft -> `setup_score -= penalty` and
  `pre_ai_floor_strict` when `InpAiStrict`, else soft pass. Called from `_TryBuildCandidateFromBranch`
  (~13478), live market placement (~16831) and pending placement (~17050).
- `_DeterministicExecutionGate` (~13265-13272): floor check with a soft-penalty relaxation branch
  `if(!execution_stage && p.setup_floor_action == "soft_penalty" && !InpAiStrict) floor = MathMax(18, setup_floor_score-6)`.
- Active policy loader (~804-812) reads `soft_setup_floor`, `hard_setup_floor`,
  `setup_floor_penalty_mult` into `m_active_policy`; struct fields in `Types.mqh` ~454-456.
- `_QueueCandidateGroup` (~13822) is the single dispatcher (only caller ~18883) for tester bootstrap
  (`_QueueTesterBootstrapCandidate` ~13864), tester cache, record-only and live AI
  (`m_ai.SendRequestCandidates` ~13963/13988). Order inside it: PO3 normalization -> index loop ->
  `_FilterOutcomePolicyBeforeAI` (~13854) -> `count = ArraySize(cands)` -> index loop with
  `_PrepareDecisionIdentity` (~13859-13863) -> bootstrap/AI dispatch.
- `setup_score` mutations before the floor: subtype (~6177), context (~6199), session-weekday (~6250).
- Request/state JSON still serialize `setup_floor_penalty` and `setup_floor_action`
  (`AIGateBridge.mqh` 1420-1421, 1679-1680; `StateStore.mqh` 256-257, 1195-1196; TradeEngine ~16525).
  Keep these fields (schema stability): penalty is always 0.0, action is `pass` or `reject`.
- Only non-MQL consumers: `python/expectancy_report.py` lists `soft_setup_floor`, `hard_setup_floor`,
  `setup_floor_penalty_mult` at ~95-97 (DECISION_MAKER_EDIT_SCOPE), ~2323-2324 (`_change_rate_check`),
  ~2346 (`_active_policy_step_floor`), ~2370-2372 (`_apply_gradual_active_policy`), ~2500 (`current`).
  No `.py` file references `pre_ai_floor_*` or `setup_floor_action`. `python/tests/test_governance_contracts.py:1488`
  asserts the `[setup_floor_gate]` tag exists (keep the tag).
- No active_policy file in `<bus>\config` contains soft/hard floor keys today.

Config.mqh (`MT5_PO3_Codex Include\Config.mqh`) floor inputs today: 647 Reversal 30.0, 648 Breaker 29.0,
649 Continuation 28.0, 650 Session 28.0, 651 Range 28.0, 652 TierBExtra 0.5, 653 TierCExtra 99.0,
663 FullPO3 30.0, 664 MicroPO3 30.0, 665 MicroBisiSibi 29.0, 666 ContinuationFamily 28.0,
667 RangeFamily 28.0, 668 FailedBreakout 29.0.

## Requirements

- R1 (exact pre-AI floor). Rewrite `_ApplyPreAiSetupFloor`: `floor = _DeterministicSetupScoreFloor(p, floor_input)`;
  `p.setup_floor_score = floor; p.setup_floor_penalty = 0.0;` if `p.setup_score < floor` ->
  `p.setup_floor_action = "reject"`, `reason = "setup_score_below_floor"` (or `setup_floor_family_unmapped`
  when the family is unmapped), journal `[setup_floor_gate]` with symbol, branch, family, taxonomy,
  setup_score, floor, floor_input, action=reject, return false. Otherwise action `pass`, return true.
  No soft/hard floors, no -5/-6/-11, no MathMax(24/18), no `InpAiStrict` dependence, no mutation of
  `setup_score`, no active-policy override.
- R2 (no floor modifiers). `_DeterministicSetupScoreFloor` returns the exact family floor. Remove the
  `stale_fvg` override and both tier extras. Keep `_IsFvgEntryNonTradable -> DBL_MAX`. Delete the now
  unused inputs `InpSetupFloorTierBExtra` and `InpSetupFloorTierCExtra` from Config.mqh after proving (grep)
  they have no other reference in MQL or Python.
- R3 (execution gate). In `_DeterministicExecutionGate` use the same exact floor; delete the
  soft-penalty relaxation branch. Rejection reason text may stay `setup_score=X<Y`.
- R4 (a floor for every family, no bypass). `_FamilySetupFloor(const TradePlan &p, string &floor_input) const`:
  full_po3_reversal and full_po3_continuation -> InpSetupFloorFullPO3; micro_po3_reversal -> InpSetupFloorMicroPO3;
  micro_continuation_fvg -> InpSetupFloorContinuationFamily; micro_failed_breakout_reclaim -> InpSetupFloorFailedBreakout;
  micro_bisi_sibi_edge -> InpSetupFloorMicroBisiSibi; micro_range_reentry -> InpSetupFloorSession when
  `p.setup_taxonomy == MICRO_SESSION_REENTRY`, else InpSetupFloorRangeFamily. The branch-based session
  override is removed so no branch can replace a family floor. Legacy group fallback (only for legacy
  family strings): reversal -> InpSetupFloorReversal, continuation -> InpSetupFloorContinuation,
  edge -> InpSetupFloorBreaker, range -> InpSetupFloorRange, failed_breakout -> InpSetupFloorFailedBreakout.
  Empty/unknown family -> fail closed: return DBL_MAX, `floor_input = "unmapped_family"`.
  `floor_input` is the input name string (e.g. "InpSetupFloorMicroBisiSibi"). Update every caller.
- R5 (single pre-AI chokepoint). In `_QueueCandidateGroup`, after `_FilterOutcomePolicyBeforeAI` and
  `count = ArraySize(cands)` and BEFORE the index loop that calls `_PrepareDecisionIdentity`, drop every
  candidate with `setup_score < exact floor` (do not recompute setup_score). For each drop: journal
  `[setup_floor_gate] stage=pre_ai_queue ...` and `_LogSetupReject(symbol, "setup_floor", reason, detail)`.
  Compact the array; if none remain, journal and return false. This must precede the bootstrap dispatch,
  the tester cache lookup and every `SendRequestCandidates`.
- R6 (policy cannot move the floor). MQL: stop reading `soft_setup_floor`, `hard_setup_floor`,
  `setup_floor_penalty_mult`; remove the three struct fields from `Types.mqh` if unused elsewhere. If any of
  those keys are present in the loaded active policy, journal once
  `[policy_schema] section=active ignored_fields=<list> reason=setup_floor_input_authority` without
  invalidating the schema. Python `expectancy_report.py`: remove the three fields from every list/dict
  named in the evidence; change nothing else in the policy.
- R7 (Config defaults = deterministic.set floors ONLY; user decision). Config.mqh: InpSetupFloorReversal = 171.0,
  InpSetupFloorBreaker = 170.0, InpSetupFloorContinuation = 169.0, InpSetupFloorFullPO3 = 171.0,
  InpSetupFloorMicroPO3 = 149.0, InpSetupFloorMicroBisiSibi = 170.0, InpSetupFloorContinuationFamily = 169.0.
  Session 28.0, Range 28.0, RangeFamily 28.0, FailedBreakout 29.0 unchanged. No other input default changes.
- R8 (`scalp_v3.set`). New file in the Presets folder, built from `deterministic.set` (do not modify the
  source). Same encoding (UTF-16 LE BOM), line endings and comment header. Exactly these value changes,
  keeping each line's `||start||step||stop||N` suffix: `InpUseAI=true`, `InpTesterAiMode=0`,
  `InpTesterAiCache=true`, `InpShadowCandidateLedgerEnable=true`, `InpShadowTrackPreAiRejects=true`,
  `InpShadowMaxEvaluationsPerTick=25`, `InpShadowMaxDataRetries=30`. Remove the two TierExtra lines.
  Every other line identical to the source.
- R9 (`swing_v3.set`). New file in the Presets folder, built from `Swing_v2_full.set` (do not modify the
  source). Replace the 11 lines InpSetupFloorReversal, Breaker, Continuation, Session, Range, FullPO3,
  MicroPO3, MicroBisiSibi, ContinuationFamily, RangeFamily, FailedBreakout with the exact lines from
  `deterministic.set`. Remove the two TierExtra lines. Every other line identical to the source.
- R10 (tests, MAJOR). One focused file `python/tests/test_setup_floor_exact_contract.py`, asserting on
  source text and presets: R1-R7 present (no soft/hard derivation, no stale/tier modifiers, full family
  map, unmapped fails closed, chokepoint precedes `_PrepareDecisionIdentity`/bootstrap/SendRequestCandidates,
  Config defaults, Python no longer emits the three fields), and that both v3 presets carry exactly the
  Config input key set and the required values (skip the preset tests when the Presets folder is absent).
  Update an existing test only if it asserts a removed floor field or reason; list each such edit.
- R11 (final verification, once). Copy changed .mqh/.mq5 to deployed; compile the deployed EA with the FxPro
  MetaEditor; copy the resulting .ex5 back to the repo Experts folder; prove 0 errors 0 warnings, .ex5 newer
  than every .mqh, repo == deployed hashes. Run the full suite once:
  `cd python; .\.venv\Scripts\python.exe -m pytest tests -q`. Report exact counts.

## Work packages

1. WP1 MAJOR - MQL exact floor (R1, R2, R3, R4, R5, R6-MQL, R7).
2. WP2 SMALL - Python policy fields (R6-Python).
3. WP3 SMALL - presets (R8, R9).
4. WP4 MAJOR - focused test + final sync/compile/suite (R10, R11).

## Constraints

- Offline. Do not start/stop/restart MT5 terminal (pid 15620 is running) or the AI gate processes.
- Do not edit CLAUDE.md, python/CLAUDE.md, .claude, .mt5-orchestrator/policy, tools.
- Do not commit. Do not touch unrelated uncommitted changes.
- Never delete, skip, xfail or loosen tests to make code pass.
- Report every file changed and the final diff summary.
