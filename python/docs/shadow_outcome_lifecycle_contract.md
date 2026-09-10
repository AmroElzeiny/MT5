# Shadow (counterfactual) outcome lifecycle — schema and contract

Schema version: `20260908_shadow_lifecycle_v4`
Analysis version: `20260908_shadow_analysis_v1`

The shadow tracker follows what **would** have happened to every plan the system
assessed — after APPROVE, REJECT, ABSTAIN, a policy rejection or an execution
rejection — and makes the result available to later AI decisions without
look-ahead. It has **no trading authority** and never will without the explicit
promotion gate in §8.

Producers and consumers:

| Side | File | Role |
|---|---|---|
| MQL5 | `TradeEngine.mqh` | observes, tracks, resolves, appends events |
| MQL5 | `StateStore.mqh` | persists pending trackers, the identity index and the quarantine |
| MQL5 | `Config.mqh` | `SHADOW_CANDIDATE_SCHEMA_VERSION` + the `InpShadow*` inputs |
| Python | `shadow_outcome_ledger.py` | reads, consolidates, aggregates, audits, reconciles |
| Python | `ai_gate.py` / `decision_evidence.py` / `evidence_catalog.py` | exposes leakage-free evidence to the model |

---

## 1. Files

| Path | Written by | Contents |
|---|---|---|
| `<bus>\logs\shadow_candidates.jsonl` | EA | the append-only event stream (UTF-16LE, MQL5 `FILE_TXT`) |
| `<bus>\logs\runtime_state\<scope>\shadow_candidate_pending.ndjson` | EA | serialized `TradePlan` per open tracker |
| `<bus>\logs\runtime_state\<scope>\shadow_tracker_index.ndjson` | EA | `"<id>\|<expiry_epoch>"` observe-once and terminal-once identities |
| `<bus>\logs\runtime_state\<scope>\shadow_tracker_quarantine.ndjson` | EA + `reconcile` | records this build cannot address, retained with their reason |

`<scope>` is the runtime-state scope (`live_account_<account>_magic_<magic>` or a
tester session). **Scope orphaning is a real failure mode**: a pending file written
under one magic number is never loaded by an EA running under another. Use
`shadow_outcome_ledger reconcile` (§9) to find and retain those records.

The event stream is **append-only**. No line is ever rewritten; state changes are
emitted as compact delta events joined on `candidate_variant_id`.

---

## 2. Identity

```
sweep_opportunity_id = H( "shadow_opportunity|v4|"
                          + symbol + direction + sweep_side
                          + t_sweep + t_displacement + t_bos
                          + setup lineage )

candidate_variant_id = H( "shadow_variant|v4|"
                          + sweep_opportunity_id + entry_branch + fvg.t_form
                          + entry + sl + tp1 + tp2   (fixed 8 decimals)
                          + target_model + tp_model
                          + request_execution_fingerprint )

parent_record_hash   = H( SHADOW_CANDIDATE_SCHEMA_VERSION + "|" + candidate_variant_id )
```

`H` is `_IntegrityHash` (double FNV-1a rendered `%08X%08X`). Prices are rendered at
a fixed 8 decimals rather than at symbol digits so the identity does not move when
a symbol's digits do.

Two rules follow from the construction and are asserted in the tests:

* **No time ingredient.** Neither id mixes in `shadow_observed_at` or the wall
  clock, so re-scanning the same sweep every 15 minutes for 24 hours yields the
  *same* ids and one sample — not 96. (The pre-v4 record hash mixed in
  `observed_at`, which is why deduplication could never match.)
* **Different plans stay different.** Any change to entry, SL, TP1, TP2, branch,
  target model or execution fingerprint produces a new variant. Variants of one
  sweep are children of one opportunity (`variant_parent_id`, `variant_revision`),
  never independent opportunities.

**Idempotency.** Observation, decision update, path progress and terminal
resolution are each guarded: the observe-once index suppresses a repeat
observation, and `_CommitShadowTerminalResolution` consults the resolved-variant
index *before* appending, so exactly one terminal event exists per variant no
matter how many scans, AI retries, restarts or duplicate callbacks occur.

### Identity safety

`SHADOW_CANDIDATE_SCHEMA_VERSION` participates in `RuntimeInputHash` (provenance)
and deliberately **not** in `_ComputeDecisionInputHash`. Shadow research is not an
economic decision input, so bumping the shadow schema must not move the decision
identity and strand a recorded replay cohort (the failure recorded in CLAUDE.md
§4y/§4z). Both halves are pinned by tests.

---

## 3. Event types

Every event carries the same envelope:

```
schema_version, event_type, event_id, event_at,
sweep_opportunity_id, candidate_variant_id,
parent_record_hash, candidate_hash, execution_fingerprint,
symbol, family, setup_taxonomy,
decision_state, decision_source, tracking_status,
trading_authority=false, can_trade=false
```

| `event_type` | When | Adds |
|---|---|---|
| `shadow_opportunity_observed` | first time a sweep is seen | direction, sweep_side, lineage, session, killzone, context_tier, po3_state, source sweep/displacement/BOS timestamps, `statistical_weight` |
| `shadow_candidate_observed` | first time a variant is seen | `observed_at`, `horizon_at`, entry branch, setup class, target model, the **assessed** entry/sl/tp1/tp2, variant revision/parent |
| `shadow_decision_recorded` | an AI or policy decision lands | decision state/source/stage, rejection reason, model + prompt + decision schema versions, python/mql reasons, the full candidate assessment |
| `shadow_entry_activated` | the hypothetical entry is first touched | `entry_activated_at`, `time_to_entry_sec`, `entry_touch_price`, `entry_order_ambiguous` |
| `shadow_path_progress` | +0.25R, +0.50R | milestone name, time from activation, R level |
| `shadow_tp1_reached` | TP1 first touched | `tp1_hit_at`, `time_to_tp1_sec` |
| `shadow_terminal_resolution` | exactly once per variant | the full outcome block (§5) |
| `shadow_data_quality_failure` | a bounded retry failed | failure code and detail |

A delta whose `parent_record_hash`, `candidate_hash` or `execution_fingerprint`
disagrees with its parent observation is **rejected**, never guessed onto a
candidate. Orphaned deltas and orphaned outcomes are reported by the audit.

### `decision_state` is the CANDIDATE's, not the request's

An error envelope is schema-required to carry one of `APPROVE`/`REJECT`/`ABSTAIN`
(`AIGateBridge.mqh`), so a local pipeline failure reaches MT5 as
`decision_state="REJECT"` with `decision_quality_tier=DEGRADED_NON_TRADING` and
zero candidate assessments. Copying that string onto every candidate wrote
infrastructure failures into this ledger as AI rejections — **89 of 89 decision
events in the 2026-09-08 ledger** were exactly that, all of them produced by the
`contract_manifest_incompatible` envelopes.

`shadow_decision_recorded` and `shadow_terminal_resolution` therefore carry both
sides:

| Field | Meaning |
|---|---|
| `decision_state` | the **candidate's** attribution: its own assessment, or `NOT_ASSESSED` |
| `decision_state_authority` | `candidate_assessment`, `request_level_selected_candidate`, `error_envelope_no_candidate_assessment`, `candidate_not_assessed_by_python`, or `candidate_assessment_unparseable` |
| `request_decision_state` | the request-level verdict, verbatim |
| `decision_quality_tier` / `trading_tier` | whether that request was of trading grade at all |
| `candidate_selected_by_python` | whether the request-level verdict describes this candidate |

`NOT_ASSESSED` is deliberately **not** in `DECISION_STATES`, so it never enters
an APPROVE/REJECT/ABSTAIN comparison. `historical_evidence` additionally refuses
any row with `trading_tier=False` when a named decision state is queried, and
reports the count as `excluded_non_trading_tier_decision`. A row written before
the tier existed (`trading_tier=None`) stays eligible, so old ledgers are not
silently dropped.

### Every observation reaches exactly one terminal

An untrackable candidate has a known, permanent outcome — there is no price
contract to follow — so it is resolved at observation time as `UNTRACKABLE` /
`EXCLUDED_INVALID_CONTRACT` instead of leaving an observation with no resolution
(243 such rows in the 2026-09-08 ledger). "Exactly one terminal per observed
variant" is now an invariant the audit can check.

---

## 4. Entry activation

Nothing is measured before the hypothetical entry is reached.
`_ShadowStepPrice` gates on `shadow_entry_activated` as its very first statement
and returns without measuring when the bar did not touch the entry, so a level
touched *before* entry can never be counted as a win or a loss.

Fields: `entry_activated`, `entry_activated_at`, `time_to_entry_sec`,
`entry_touch_price`, `entry_order_ambiguous`, `entry_never_reached`.

If the entry and an opposing level fall in the same M1 bar and tick data cannot
order them, `entry_order_ambiguous` is set and the ambiguity reason recorded.
A candidate whose entry never activated resolves as `ENTRY_NEVER_REACHED` and is
classified separately — **never** as a win or a loss.

### Intrabar ordering

`_ShadowBarIsContested` decides whether a bar needs tick resolution. Entry plus
stop alone is *strictly ordered* by geometry (for a buy the stop is below the
entry, so reaching the stop means the entry was passed); only a favourable level
**and** the stop in one bar is genuinely ambiguous. Contested bars are replayed
with `CopyTicksRange(COPY_TICKS_ALL)` when `InpShadowUseTickOrdering` is on;
`ordering_source` records `tick_sequence` or `m1_bar`. Ordering is never invented.

---

## 5. Terminal events

| `terminal_event` | Sample class | Carries R? |
|---|---|---|
| `TP2_BEFORE_SL` | CLEAN | yes |
| `TP1_THEN_TP2` | CLEAN | yes |
| `TP1_THEN_SL` | CLEAN | yes |
| `SL_BEFORE_TP1` | CLEAN | yes |
| `HORIZON_CENSORED` | RIGHT_CENSORED | yes (open R at horizon) |
| `SESSION_CLOSE_CENSORED` | RIGHT_CENSORED | yes (open R at close) |
| `ENTRY_NEVER_REACHED` | ENTRY_NEVER_REACHED | **no — JSON `null`** |
| `AMBIGUOUS_TP1_AND_SL_SAME_BAR` | EXCLUDED_AMBIGUOUS | **no — JSON `null`** |
| `AMBIGUOUS_TP2_AND_SL_SAME_BAR` | EXCLUDED_AMBIGUOUS | **no — JSON `null`** |
| `DATA_LOSS` | EXCLUDED_DATA_LOSS | **no — JSON `null`** |
| `UNTRACKABLE` | EXCLUDED_INVALID_CONTRACT | **no — JSON `null`** |

`_ShadowTerminalCarriesResult` is a whitelist. A terminal outside it is written as
`null`, not as `0.0` — a zero would read as a break-even trade in every average.

Outcome fields: `tp1_hit`, `tp1_hit_at`, `time_to_tp1_sec`, `tp1_before_sl`,
`sl_before_tp1`, `tp2_hit`, `tp2_hit_at`, `time_to_tp2_sec`, `tp2_before_sl`,
`sl_before_tp2`, `tp1_then_sl`, `tp1_then_tp2`, `neither_target_nor_stop`,
`mfe_r`, `mae_r`, `maximum_favorable_price`, `maximum_adverse_price`,
`result_r_unmanaged`, `result_r_with_configured_tp1_partial`,
`terminal_event`, `terminal_event_at`, `censoring_status`, `ambiguity_status`,
`ambiguity_reason`, `data_quality_status`, `ordering_source`.

**TP2 implies TP1** when TP1 lies between entry and TP2: reaching the far level
requires passing the near one. That is geometry, not a guess, and recording it
keeps the TP1 event complete on gap-through bars.

### The two R figures

`InpTP1PartialPct` (`frac`) is the configured TP1 partial close.

```
TP2_BEFORE_SL / TP1_THEN_TP2   unmanaged = rr2 - cost
                               partial   = frac*rr1 + (1-frac)*rr2 - cost   (when TP1 was hit)
TP1_THEN_SL                    unmanaged = -1 - cost
                               partial   = frac*rr1 + (1-frac)*(-1) - cost
SL_BEFORE_TP1                  unmanaged = -1 - cost
                               partial   = -1 - cost
censored                       unmanaged = open_R - cost
                               partial   = frac*rr1 + (1-frac)*open_R - cost  (when TP1 was hit)
```

Keeping both apart is what stops **entry quality** and **management quality** from
being averaged into one indistinguishable number.

---

## 6. Data availability

`_ShadowEnsureM1History` **requests** the M1 series before its absence is called
a data loss. The engine scans on H4/M15, so nothing else in the EA touches M1 for
the other symbols and their series stays unsynchronized; the ranged
`CopyRates(symbol, tf, from, to, ...)` overload returns 0 on an unbuilt series
without reliably starting the build, so the tracker used to burn its whole retry
budget waiting for history that had never been ordered. Measured in the
2026-09-08 ledger: **103 of 106 non-BITCOIN terminal resolutions were
`DATA_LOSS`**, while BITCOIN — the one symbol whose M1 the terminal already held
— produced none at all.

Three states are now distinguished and reported by name:

| Condition | Result |
|---|---|
| series synchronized, no bars in range | market closed — censor at the horizon, never a data loss |
| series not synchronized | `m1_history_not_synchronized_awaiting_download`, the series is requested, the tracker stays pending |
| still not synchronized when the horizon and the budget are both spent | exactly one `DATA_LOSS` terminal plus a `shadow_data_quality_failure` with `failure=m1_series_not_synchronized_after_retries` |

`InpShadowMaxDataRetries` (default 30) bounds the wait; `_ShadowTrackerDue`
spaces evaluations by `InpShadowEvaluationIntervalSeconds`, so the budget is a
wall-clock bound, not a tick count. A candidate is never left pending forever,
and a data-loss record never enters the statistics.

Two counters separate the recoverable case from the terminal one:
`history_requests_total` and `history_pending_total`, beside `data_loss_total`.

---

## 7. Restart and retention

* Pending trackers are written through `CFileBus::WriteText` (tmp file + `FileMove`), so a crash mid-write cannot leave a half-parsed queue.
* `Init()` calls `_RestoreShadowPendingTrackers()` then `_ResolveOverdueShadowTrackersOnInit()`; an overdue tracker resolves immediately from available history rather than waiting for the next tick.
* The observe-once and terminal-once identities live in a **separate** index file, because a resolved variant leaves the pending queue — without the index a restart could not tell "never tracked" from "already resolved" and would emit a second terminal.
* A restored record this build cannot address (incompatible schema, lost identity, unusable price contract) is written to `shadow_tracker_quarantine.ndjson` **with its reason and its plan verbatim** and counted. **An unresolved candidate is never deleted.**

Runtime counters, reported by `[shadow_tracker]` and `[final_summary]`:
opportunities, observed, deduplicated, variant revisions, pending, restored,
restored-overdue-resolved, resolved, censored, ambiguous, data-loss,
entry-activated, entry-never-reached, untrackable, terminal-duplicate-suppressed,
quarantined, capacity-rejected, tick-ordered bars, progress events, expired
pending. `[shadow_tracker_warning]` fires on expired pending records, on a
capacity rejection, and on a failed quarantine write.

---

## 8. Evidence for future decisions

`historical_evidence()` answers one decision at a time.

* **Leakage.** Only variants with `terminal_event_at` **strictly less than** the decision timestamp are eligible. Unresolved variants and variants that resolved at or after the decision are excluded, not imputed — "still open" at decision time is not evidence of anything. `leakage_policy` records the rule in the payload.
* **Sweep weighting.** `_sweep_weighted_variants` picks one representative per sweep (the decided, resolved, earliest-observed variant) so ten branches of one setup cannot outvote ten separate setups.
* **Exclusions.** Ambiguous, entry-never-activated, data-loss and right-censored samples are counted in their own buckets and are never folded into win/loss rates.
* **Uncertainty.** Every rate carries a Wilson interval; a small sample reports `INSUFFICIENT_SAMPLE` and an empty narrative rather than a confident number.

### Configuration switch

| Variable | Default | Effect |
|---|---|---|
| `AI_SHADOW_EVIDENCE_ENABLE` | `true` | off ⇒ state `DISABLED`, nothing reaches the model |
| `AI_SHADOW_EVIDENCE_MIN_SAMPLES` | 20 | clean samples needed to emit a narrative |
| `AI_SHADOW_EVIDENCE_MIN_SWEEPS` | 12 | unique sweeps needed |
| `AI_SHADOW_EVIDENCE_TRADING_AUTHORITY` | `false` | operator switch in the promotion gate |
| `AI_SHADOW_EVIDENCE_OOS_VALIDATED` | `false` | out-of-sample attestation |

### Promotion gate

`authority` is `DIAGNOSTIC_SHADOW_ONLY` until **all** of these hold:

```
clean samples      >= 200
unique sweeps      >= 120
TP2-before-SL Wilson interval width <= 0.20
out-of-sample validated                (AI_SHADOW_EVIDENCE_OOS_VALIDATED)
operator switch enabled                (AI_SHADOW_EVIDENCE_TRADING_AUTHORITY)
```

Only then does it become `CALIBRATED_ADVISORY`. Even satisfied, the gate permits
the evidence to be *labelled* authoritative — it never approves a trade by itself
and never overrides a veto. The AI gate, the deterministic gates and the risk
controls all remain in the path, and `provider_decision_context.rules` states this
to the model explicitly.

The evidence is flattened by `compact_candidate_evidence()` into scalars only,
because the evidence catalog can issue an id for a scalar alone — a nested object
would be visible to the model but uncitable, which is the same failure as showing
evidence the model is forbidden to reference.

---

## 9. Command-line tools

```
python -m shadow_outcome_ledger [--ledger PATH] [--out FILE] <command>

  audit        duplicates, orphaned updates, orphaned outcomes, expired pending
               records, conflicting terminals, fingerprint mismatches, missing AI
               decision attribution, unparseable lines.  Exit code 2 when unhealthy.
  report       aggregates by decision state, family, family x decision state,
               family x session, family x entry branch, abstain reason and
               missing-confirmation category, plus the abstain analysis.
  consolidate  one clean record per candidate variant.
  evidence     leakage-free historical evidence for one decision
               (--at EPOCH, --family, --taxonomy, --branch, --session, --symbol,
                --decision-state).
  reconcile    walk every runtime_state scope's pending file and classify each
               tracker (ADDRESSABLE_PENDING / ADDRESSABLE_OVERDUE / LEGACY_SCHEMA /
               FOREIGN_SCHEMA / UNADDRESSABLE / UNTRACKABLE).
               --quarantine retains the unaddressable ones in that scope's
               quarantine file.  It never deletes a pending file and never
               fabricates a terminal outcome.
```

`PO3_SHADOW_LEDGER_PATH`, `PO3_BUS_ROOT` and `PO3_RUNTIME_STATE_ROOT` override the
default locations.

---

## 10. Migration from v3

Pre-v4 rows (`candidate_observed`, `candidate_decision_update`,
`hypothetical_outcome_resolution`) are recognised as `LEGACY_EVENT_TYPES`, counted
as `legacy_v3_rows_ignored`, and **never mixed into the statistics** — their
identity was time-dependent and their outcomes had no entry-activation gate, so
they are not comparable with v4 samples. They are kept in the file for provenance.

Pre-v4 *pending* records are handled by `reconcile --quarantine`. They cannot be
re-keyed into v4 identities: recomputing the identity in Python from a different
representation is exactly what `python/CLAUDE.md` forbids, and the same reasoning
that made the tester cache non-re-keyable (CLAUDE.md §4y) applies here. They are
retained with their reason and their plan, and excluded from the study.

---

## 11. Tests

`python/tests/test_shadow_outcome_lifecycle.py` covers the eighteen required
properties across three surfaces: the Python consolidation/statistics/evidence
layer (through a real UTF-16 ledger file), the reconciler, and the MQL5 tracker
asserted against its own source text.

The MQL assertions are falsified two ways: a mutation harness that reverts each
guarantee one at a time and requires the matching assertion to fail, and a run
against the pre-v4 `TradeEngine.mqh` (set `PO3_SHADOW_PRE_V4_TRADEENGINE`).
