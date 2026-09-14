# AI call efficiency — implementation report

Date: 2026-09-14 · Production model unchanged: `muse-spark-1.3-contributor` (OpenCode Go), effort HIGH.
Evidence run: `.mt5-orchestrator/runs/20260914T0000Z-ai-call-efficiency/` (replay outputs per policy).

Every number is **measured** (source named) unless marked **estimated** (method named).
Muse USD always uses Muse rows only, at the published Go rates ($0.10 / $0.20 / $0.002 per 1M
input / output / cached-read tokens). Luna rows are never repriced as Muse.

---

## 0. Findings that change the premise (read first)

1. **The ~11,000 requests are not one Monday–Friday week.** The 11,098 files in `<bus>\requests`
   are one Strategy Tester RECORD_ONLY cohort (run `305658125`, base `1785715200`) spanning simulated
   **2026-08-04 → 2026-09-04: 24 weekdays, 21 symbols, ≈462 requests per weekday.**
2. **Measured live workload on Muse** (usage ledger, full UTC day 2026-09-10, 27–35 symbols in
   Market Watch): **482 provider-reaching requests/day**, mean Muse cost **$0.009232/request**
   (median $0.008955, n=825), **≈2.11 provider calls/request**. Mon–Fri projection:
   **2,410 requests, ≈5,090 calls, ≈$22.25/week = 74% of the $30 weekly allowance** — already
   under 100% before this work.
3. The weekly limit was exhausted by other consumers of the same allowance: OpenCode CLI
   orchestration ($51.32 over 7 days, `OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md` §2) and processing the
   tester cohort on 2026-09-13 ($3.47 in 5 h). Tester workloads are always fully adjudicated
   (adjudicator on 80% of requests vs 3.5% live), so a cohort request costs ≈$0.0148, ≈60% more
   than a live one.
4. **The AI is not deterministic near its approval boundary.** On consecutive requests for the same
   setup identity with unchanged state, Muse flipped decision state on 14.9% of pairs and Luna more.
   Non-approvals whose quality sat **≥2.0 below the family threshold never flipped to an approval
   (0/158 Muse, 0/92 Luna)**; borderline ones did (Muse 1.7–3.8%, Luna 9–18%). This bounds what can
   be skipped without losing approvals: see §9 and §18.
5. **Repository session labels are not the real-world sessions.** `PO3.mqh` treats the preset hours
   as market-local time with DST. In summer the labels are: ASIA 00:00–10:00 UTC, LONDON 10:00–16:00,
   OFF_HOURS 16:00–20:00, NEW_YORK 20:00–24:00 (ASIA wins the 00:00–04:00 overlap). The real New York
   session is labelled OFF_HOURS/LONDON. The gate uses the repository authority as instructed and does
   not change it; correcting session semantics is a strategy decision (affects scores, killzone evidence,
   session penalties, bucket priors).

Consequence: under the strict acceptance rule (zero actionable approvals lost), the safe saving on
live Muse traffic is **≈9–15% of calls**, not 35–65%. Against the measured live baseline the result
is **≈67% of the allowance (default) / ≈63% (option `AI_REVIEW_DECISIVE_TTL_CADENCES=2`)**. Against
the stated premise (11,000/week, $85) the irreducible minimum is **≈$70/week (233%)** — see §16–§17.

---

## 1. Original request flow

```text
MT5 timer (1 s) ── every InpScanIntervalMinutes (10) ─► scan all Market Watch symbols
  └─ per symbol: PO3/FVG → plans → branch filters → pre-AI deterministic gate
       └─ FinalizeScan: portfolio scheduler → _QueueCandidateGroup
            ├─ tester cache (tester only)
            ├─ MQL cooldown: exact signature, InpAiRetryBackoffMin (10 min) after a non-approval
            └─ SendRequestCandidates → <bus>\requests\<id>.json          (1 scan ≈ 1 request/symbol)
Python process_one: contract → taxonomy → live candidate budget (3) → freeze → ledger
  └─ _score_setup_impl: taxonomy gate → mandatory priors → hard pre-gate → snapshot integrity
       → provider health → exact decision cache (0 hits live) → _score_setup_ai
            analyst → critic (always) → adjudicator (if required and not "hopeless")
            → [shadow repeat 2% × 2 extra full panels, inline]
       → validation / veto / threshold / authority → response → MQL
MQL ProcessPendingAI → watchlist (_AddToWatchlist: sweep cap, target validation) → entry
```

## 2. New request flow

```text
MT5 scan every 10 min (UNCHANGED market observation)
  └─ _QueueCandidateGroup
       ├─ NEW predetermined-outcome gate: all candidates on a consumed sweep (InpMaxTradesPerSweep=1)
       │     → no request ([ai_invocation_gate] … reason=predetermined_sweep_already_consumed)
       └─ SendRequestCandidates(…, ai_review_context{session_code, killzone_code, server_time})
Python _score_setup_impl
  taxonomy / priors / hard pre-gate / snapshot integrity   (precedence unchanged)
  └─ NEW AI review gate (live only, ai_review_gate.py)
       REUSE only if: prior authoritative FULL_STRUCTURED decision for account+symbol,
                      no candidate approved, every candidate ≥2.0 below its family threshold,
                      fingerprint unchanged, same provider identity, age < cadence(session)
            → RULE_ONLY_NON_TRADING envelope, decision_source=ai_review_gate_reuse, 0 provider calls
       otherwise CALL → analyst → critic (always) → adjudicator
            (NEW: skipped also when the live analyst REJECTs — outcome invariant)
       → authoritative decision recorded as the new prior (actual answering provider identity)
MQL: reuse labelled ai_review_reused_prior_non_approval, stage ai_review_gate (still non-trading)
```

## 3. Call classification (exactly one category per logical request)

Source: replay over **all 2,168 archived LIVE_FORWARD requests** (2026-08-11 → 2026-09-11; Muse 825,
Luna 873, other 392, unknown 78), `replay/default/replay_summary.json`. Provider calls from the usage
ledger (4,492 calls in total).

| Category | Requests | % | Provider calls (analyst/critic/adjudicator/repair/shadow) | Muse USD | Safely removable | Rationale |
|---|---:|---:|---|---:|---|---|
| REQUIRED_NEW_DECISION | 706 | 32.6 | 668 / 633 / 187 / 37 / 63 | 3.064 | no | no prior (121), prior approved (105), or a meaningful event before the cadence elapsed (480) |
| SCHEDULED_REVIEW_WITH_MEANINGFUL_CHANGE | 593 | 27.4 | 535 / 495 / 184 / 37 / 62 | 2.172 | no | state changed and cadence elapsed |
| UNCHANGED_STATE | **99** | 4.6 | 99 / 99 / 8 / 2 / 12 (**220 calls**) | 0.546 | **yes** | decisive non-approval, identical fingerprint, within TTL |
| PREDETERMINED_OUTCOME | 204 | 9.4 | 43 / 43 / 19 / 0 / 15 (**120 calls**) | 0.222 | **49 new** | 155 already skipped by the Python hard pre-gate before this work; 49 = all candidates on a consumed sweep (new MQL skip) |
| POINTLESS_ADJUDICATION | 0 (call-level) | — | **77 adjudicator calls** inside kept requests | 0 Muse | **yes** | analyst REJECT: outcome REJECT on every adjudicator verdict (all 77 were Luna fallback) |
| REPAIR | 0 (call-level) | — | 81 repair calls in kept requests | ≈0.08% of Muse cost | no | first-response validity already high; see §10 |
| QA_OR_NON_PRODUCTION | 0 (call-level) | — | 171 shadow-repeat calls in kept requests | 3.67% of Muse cost | operator choice | QA, now labelled `SHADOW`; not deleted |
| OTHER_REQUIRED | 493 | 22.7 | 494 / 492 / 200 / 6 / 46 | 1.771 | no | unchanged but borderline prior (397) or decisive TTL expired (116) |
| UNKNOWN | 73 | 3.4 | 6 / 5 / 1 / 1 / 0 | 0.024 | no | no archived decision; never optimised |

Muse-only subset (825): REQUIRED 320, SCHEDULED 227, UNCHANGED **57 (6.9%)**, PREDETERMINED 21,
OTHER 200, UNKNOWN 0.

## 4. Session schedule

- Authority: MQL `m_po3.SessionCodeAt(t)` / `KillzoneCodeAt(t)` for the request time, sent as
  `ai_review_context` (Config `AI_REVIEW_CONTEXT_VERSION = 20260914_ai_review_context_v1`, pinned to
  Python by a governance test). Python never re-derives boundaries in production; a missing,
  mismatched or malformed context ⇒ CALL.
- Precedence on overlap: the repository's `_SessionName` order (ASIA > LONDON > NEW_YORK > OFF).
- Config (Python `.env`, documented in `python/docs/runtime_env_template_opencode.txt`):
  `AI_REVIEW_CADENCE_ASIA_MIN=30`, `AI_REVIEW_CADENCE_LONDON_MIN=15`, `AI_REVIEW_CADENCE_NEW_YORK_MIN=7`,
  `AI_REVIEW_CADENCE_OFF_HOURS_MIN=30` (not specified in the task; the slowest session value, safe
  because only decisive states are reused — replay with 10 gave 61 reuses vs 99, no approval difference).
- Nominal scheduled re-review opportunities for an unchanged state, summer, repository sessions:
  ASIA 600 min/30 = 20, LONDON 360/15 = 24, OFF 240/30 = 8, NY 240 min at 7-min cadence is bounded by the
  10-minute scan and the 20:54–22:05 UTC rollover freeze ⇒ ≈17. **≈69/day vs 138 before.** This differs
  from the expected 14/28/48 because the repository sessions differ from real-world sessions (§0.5).
- NY 7 min < 10-min scan: every NY scan remains a review opportunity, so NY entry timing is exactly as
  before (0 added delay, verified in replay: 287 NY requests, 0 NY reuses). Faster NY scanning was not
  added (it would add calls in a window the repository labels post-close).

## 5. Event override rules

Any of these since the prior decision ⇒ immediate CALL, regardless of cadence
(`ai_review_gate.compare_states`):

| Event | Components |
|---|---|
| new candidate | a current cohort candidate not assessed in the prior (setup lineage `setup_id#branch#taxonomy`) |
| candidate state | po3_state, structure_type, FVG mitigation/execution class/touched/mid-mitigated/filled/invalidated/entry-invalid/structure-invalidated, entry_trigger_phase, context tier, ote_state, obstacle kind/tf, target_arbitration_required, liquidity target blocked/valid, tp_model, target_source, target_model |
| regime | MQL `volatility_profile`, `regime_profile`, policy-bucket regime label |
| structure / sweep / BOS / CHOCH / displacement | request po3: has_sweep, has_displacement, has_bos, has_follow_through, developing_bos, htf/ltf bos, mss, choch, context_tier, po3_state, structure_type, sweep_side, t_sweep, t_disp, t_bos, liquidity_kind; direction |
| price | entry, SL, TP1, TP2 drift > `AI_REVIEW_PRICE_TOLERANCE_R` (0.10 R of the prior stop distance) |
| session / killzone | MQL session code or killzone code differs |
| news | news_risk band none/low/high |
| identity | runtime_input_hash, decision_input_hash, contract manifest hash, prompt/decision/target schema, family profile, evidence envelope, provider decision context, wire projection, gate version, provider id, model id, static generation settings |

Measured frequency (replay, all events): new_candidate 1,010; provider identity 392 (Muse↔Luna
fallback in history); session transition 370; request structure fields 150–354 each; price drift 313;
runtime/decision inputs 202/169; killzone 178.

## 6. Decision-state fingerprint

`build_fingerprint(payload, context, python_identity)` = scope (account from request id, symbol,
workload) + identity (above) + request state + per-candidate state (discrete + regime + prices + risk).
Only information the model sees in the evidence envelope, or the deterministic source of a target or
trigger projection it reads, is included. **Excluded** (never reach the model or are noise): request
ids, nonces, timestamps, hashes of the request itself, bid/ask, continuous scores (retest, freshness,
VWAP distance, portfolio score), portfolio scheduler concentration counts (they moved on 880 of 1,655
change events), runner/bucket/subtype policy flags, per-call timeout/budget in `generation_identity`.
Continuous evidence drift is covered by the decisiveness margin, not by the fingerprint.

## 7. Invalidation rules

A prior becomes non-reusable on: any event of §5; TTL expiry (`cadence(session) × AI_REVIEW_DECISIVE_TTL_CADENCES`,
capped by `AI_REVIEW_MAX_REUSE_MINUTES` 60); any candidate APPROVE or python_final_allow; any borderline
candidate (gap < `AI_REVIEW_DECISIVE_MARGIN` 2.0); a decision answered by a different provider/model than
the configured primary (the record stores the answering leg); non-FULL_STRUCTURED, non-authoritative
source, incomplete mandatory fields or integrity rejection codes (never become priors); clock regression;
missing/invalid context; corrupt or foreign state file (ignored); schema/fingerprint version change.
Deadline expiry and stale data cannot produce a reuse (reuse needs a valid prior and identical state; a
late provider result is degraded and never recorded). Ages use MQL server time, so a restart never
extends validity. Records are per account+symbol+workload; an older late decision never overwrites a newer.

## 8. Deterministic-outcome rules

| Case | Authority reused | Proof AI_APPROVE ≡ AI_REJECT ≡ AI_ABSTAIN | Status |
|---|---|---|---|
| all candidates on a consumed sweep, `InpMaxTradesPerSweep == 1` | `_AddToWatchlist → _SweepTradeCapReached` (rejects any approval; `m_consumed_sweep_keys` never pruned in-process) | approval dropped at watchlist ⇒ no order; reject/abstain ⇒ no order | **implemented (MQL)** — 47/1,822 queued requests and 10 wasted approvals in the 09-07..11 journals |
| every candidate hard-blocked | Python `hard_pre_gate` | already skipped before this work (155 requests) | unchanged |
| missing plan/PO3 fields in `process_one` | same function after the call | 0 live occurrences in 2,090 decisions | audited, not changed |
| sweep cap from a pending order or pre-restart position | watchlist cap | not permanent (order can cancel, restart forgets keys) | not skipped |
| AutoTrading disabled / max positions / freeze window | placement gates | can clear while an approval waits on the watchlist | not skipped |
| `AI_REQUIRE_REPEATABILITY_LIVE=true` without a REPEATABLE group | `_apply_repeatability_authority` | would be provable per leg, but the flag is `false` in production | audited, not active |

No new veto rule was introduced.

## 9. Adjudicator-skip proof

`run_qualitative_consensus`: without adjudication an analyst REJECT returns `(REJECT, allow=False)`;
with adjudication the resolver returns REJECT when `analyst_state == REJECT or verdict == UPHOLD_BLOCK`,
so REJECT again, for UPHOLD_APPROVE, UPHOLD_BLOCK and ABSTAIN alike. Decision state and final allow are
identical; the skip also removes one failure mode (invalid adjudicator output). New rule
`_analyst_reject_makes_adjudication_pointless` uses the same live-only and enable switches as the
existing floor rule (record-only/replay keep a complete panel, per `test_panel_shortcircuit.py`).
Analyst ABSTAIN is **not** skipped beyond the existing floor: adjudication can turn ABSTAIN into REJECT
(state changes, executable outcome does not). APPROVE with objections always adjudicates.
Historical avoidable calls under the new rule: **77 (all Luna fallback); 0 on live Muse** (Muse REJECTs
already fell below the floor).

## 10. Repair-call analysis

Usage ledger: Muse repair calls 0.0012 per request (0.08% of Muse cost); gate log over all eras: 121
repair passes (critic 36, evidence 43, mapping 78 attempts incl. older models); Muse: critic 3, evidence 1,
mapping 0. Cross-candidate citations are already normalised in Python (`[evidence_reference_normalized]`
836). A per-request `enum` of allowed ids in the structured schema would make the schema request-specific
(new schema fingerprint per call, lost cached prefix, provider-exchange contract change) for a 0.1% saving,
so it was not done. Repairs remain bounded to one pass and invalid output never passes validation.
Observability gap noted: a critic repair's first (invalid) call is in the OpenCode attempt ledger but not in
`openai_usage.ndjson`.

## 11. Files changed

| File | Change |
|---|---|
| `python/ai_review_gate.py` | **new**: config, MQL context validation, fingerprint, change detection, decisive reuse, TTL, persisted state store, verdicts, shadow outcome |
| `python/ai_gate.py` | gate instance + config log; `_evaluate_ai_review_gate`, `_ai_review_reuse_decision`, `_record_ai_review_decision`; gate after all hard gates, before provider health; record after every authoritative decision; counters |
| `python/decision_pipeline.py` | `_analyst_reject_makes_adjudication_pointless`; skip reason logged (`rule=`) |
| `python/openai_usage_logger.py` | `traffic_class` on every usage row (TRADING_DECISION / CRITIC / ADJUDICATOR / REPAIR / SHADOW / QA / REPLAY / BENCHMARK / CAPABILITY_PROBE / OTHER); `AI_TRAFFIC_CLASS` override limited to non-trading classes |
| `python/tools/ai_call_efficiency_replay.py` | **new**: baseline classification + shadow replay driving the production gate |
| `python/tools/ai_review_gate_live_check.py` | **new**: isolated real-provider end-to-end check |
| `python/tests/test_ai_review_gate.py` | **new** focused tests |
| `python/docs/runtime_env_template_opencode.txt` | `AI_REVIEW_*` documented |
| `MT5_PO3_Codex Include/Config.mqh` | `AI_REVIEW_CONTEXT_VERSION`, `AI_REVIEW_GATE_REUSE_SOURCE` (not manifest fields) |
| `MT5_PO3_Codex Include/AIGateBridge.mqh` | `ai_review_context` block in the request |
| `MT5_PO3_Codex Include/TradeEngine.mqh` | `_AiReviewContextJson`; consumed-sweep predetermined skip; reuse label/stage; `ai_review_gate_reuse_total`, `ai_predetermined_outcome_skips_total` in `[final_summary]` |

Not changed: Muse model/effort/reserve, OpenCode routing and fallback, schemas, prompts, contract versions
and manifest hash, evidence, candidate generation, ranking, scores, risk, execution authority, `.env`.

## 12. Tests added (`python/tests/test_ai_review_gate.py`)

A cadence per session and TTL; scheduled-review counts derived from session durations; DST; session
transition; invalid/missing context. B structure/new-candidate event. C noise. D material evidence
(mitigation, obstacle, regime, price, killzone). E one decision then reuse. F TTL expiry. G hard gate
precedence with zero provider calls. H borderline/approving priors never reused; call verdict reaches the
provider path; shadow never suppresses. I live analyst REJECT skips adjudicator with identical outcome.
J borderline ABSTAIN / disputed APPROVE still adjudicate. K critic on every analyst state. L non-authoritative
decisions never become priors; reuse envelope is non-trading. M restart keeps valid state, corrupt state
ignored, TTL not extended; older decision never overwrites newer. N scope isolation + 20 concurrent threads.
O gate config cannot touch provider settings; source order of gates. Q MQL: context version synchronised and
outside the manifest; context on every send; reuse label keeps the approval predicate; consumed-sweep gate
precedes sending and uses the watchlist rule. Traffic classes. Fallback answers never count as primary.
P is the live check (§13).

## 13. Full test results and live provider check

- Focused: `tests/test_ai_review_gate.py` + `test_panel_shortcircuit.py` + `test_provider_neutral_ai.py`: 72 passed.
- Full suite (`python -m pytest tests -q`): **1258 passed, 12 skipped, 674 subtests passed, 0 failed**
  (baseline before this work: 1220 passed, 12 skipped, 674 subtests).
- MQL: FxPro MetaEditor compile of `PO3_AIGate_ScannerEA.mq5` — **0 errors, 0 warnings**; `.ex5`
  2026-09-14 03:49:45 newer than every `.mqh`; repo and deployed includes identical (previous deployed
  copies backed up in the session scratchpad).
- Live end-to-end (`tools/ai_review_gate_live_check.py`, isolated scratch bus, traffic class QA,
  production provider selection, archived request `903871_…_1788988853_AUDUSD_30210`):
  - A, first scan: gate `CALL no_prior_authoritative_decision` → real Muse analyst (144.6 s) + critic
    (20.5 s), adjudicator skipped, `identity_validation valid=true`, `ai_schema_validation valid=true`,
    `final_response_validation valid=true`, `response_written quality_tier=FULL_STRUCTURED`,
    decision ABSTAIN, every candidate decisive → `record stored=true`.
  - B, same state 10 minutes later: gate `REUSE unchanged_decisive_non_approval_within_ttl`
    (prior age 10.0 min, TTL 30, session OFF) → **0 provider calls**, response written in 0.1 s,
    `RULE_ONLY_NON_TRADING`, `decision_source=ai_review_gate_reuse`, `python_final_allow=false`,
    bound to its own `request_identity_hash`.
  - No write reached the live bus, `python/data` or `python/logs` (isolation guard passed; it refused the
    first attempt when the credential loader re-applied production paths, which the tool now rebuilds).
- Same check with the injected GPT-5.6 Luna low/flex test leg (no production setting changed; Luna tokens
  not used for any Muse figure): A → real analyst + critic, `FULL_STRUCTURED`, identity/schema/final
  validation true, decision **APPROVE** (`python_final_allow=true`), recorded. B, unchanged state 10 minutes
  later → gate `CALL prior_decision_approved_never_reused` → real analyst + critic again, APPROVE. This is the
  complementary proof: an approval is never reused, only decisive non-approvals are.

## 14. Historical replay results

`replay/policies_overview.json` (2,168 live requests, 88 Python approvals, 44 actionable = added to the
watchlist per MQL journals):

| Policy | Unchanged-state skips | Actionable approvals lost | Any approval lost |
|---|---:|---:|---:|
| **default** (TTL 1 cadence, tol 0.10 R, no borderline) | **99** | **0** | 10 (all non-actionable: sweep already consumed) |
| `AI_REVIEW_DECISIVE_TTL_CADENCES=2` | 179 | 0 | 10 |
| TTL 3 cadences, cap 90 | 214 | 1 (Luna, USDCHF, 71 min) | 11 |
| price tolerance 0.25 R | 129 | 1 (Luna, #Germany40) | 11 |
| borderline deferral on | 291 | **10** (8 Luna, 2 Muse) | 27 |
| OFF_HOURS cadence 10 | 61 | 0 | 10 |

Session reconstruction used only by the replay matched the session token MQL wrote in every candidate's
broker comment: **7,123 / 7,123**.

## 15. Before/after call waterfall (replay population, all models)

```text
2,168 logical requests (4,492 provider calls)
 −   0 session-cadence duplicates        (borderline deferral disabled: would remove 192 more, loses 10 actionable approvals)
 −  99 unchanged-state reviews           (220 calls)
 −  49 predetermined-outcome requests     (120 calls; the other 155 predetermined were already free)
 −  77 pointless adjudications           (calls)
 −   0 preventable repair calls          (81 kept)
 −   0 non-production calls              (171 shadow calls kept and now labelled SHADOW)
 +   0 event-driven override calls        (480 in-cadence event calls are retained, none added)
 = 2,020 provider-reaching requests, 4,075 provider calls (−9.3%)
```

## 16. Estimated Muse weekly cost (live Mon–Fri, measured baseline)

Method (estimated): measured baseline × measured Muse-subset fractions (UNCHANGED 6.9% default / 12.4% TTL2,
consumed-sweep predetermined 2.26%, shadow 3.67% of cost, adjudicator-REJECT 0% on Muse).

| Metric | Before | After (default) | After (TTL 2 cadences) |
|---|---:|---:|---:|
| Market observations/day | 138 scans | 138 scans | 138 scans |
| Scheduled AI review opportunities/day | 138 | ≈69 | ≈69 |
| Actual AI decisions/day | 482 | 438 | 413 |
| Analyst calls/week | 2,407 | 2,185 | 2,061 |
| Critic calls/week | 2,405 | 2,183 | 2,059 |
| Adjudicator calls/week | 85 | 77 | 73 |
| Repair calls/week | 3 | 3 | 3 |
| QA/non-production calls/week (shadow) | 190 | 172 | 162 |
| Total provider calls/week | 5,090 | 4,620 | 4,360 |
| Unchanged-state skips/week | 0 | 166 | 299 |
| Predetermined-outcome skips/week | 0 | 54 | 54 |
| Pointless-adjudication skips/week | 0 | 0 (Muse) | 0 (Muse) |
| Event-driven override calls/week | n/a | 534 retained, 0 added | 534 retained, 0 added |
| Missed opportunities | — | 0 | 0 |
| Materially delayed opportunities | — | 0 | 0 |
| Estimated Muse weekly cost | $22.25 | $20.20 | $19.00 |
| Muse weekly quota consumption | 74.2% | 67.4% | 63.3% |

With `AI_SHADOW_REPEAT_ENABLE=false` (operator choice; QA stays available offline): default $19.46 (64.9%).

## 17. Premise scenario (11,000 requests/week, $85 ≈ 283%)

Same measured fractions: default $77.2 (257%); TTL 2 $72.6 (242%); TTL 2 + shadow off **$69.9 (233%)**.
Neither ≤150% nor ≤100% is reachable on that volume without losing approvals (§14 borderline row) or
changing a contract/model choice (§19).

## 18. Missed-opportunity analysis

88 Python approvals in the replay; MQL outcome from the journals: 44 added to the watchlist (actionable),
16 dropped by a live sweep cap, 10 dropped because the sweep was consumed, 6 no outcome line, 12 no journal
coverage. Under the default policy every one of the 44 actionable approvals is a CALL at its original time
(0 missed, 0 delayed). The 10 approvals skipped are exactly the consumed-sweep ones MQL would have dropped.
NY: 287 requests, 0 reuses, 0 delay. Borderline deferral would have cost 10 actionable approvals, which is
why it stays off.

## 19. Remaining irreducible calls

- Changed states (≈60% of requests): new candidates, structure, price, session/killzone transitions.
- Borderline non-approvals (≈18% of requests): the model flips near the threshold, so they are re-reviewed.
- The critic (≈26% of Muse cost): required by `AIGateBridge.mqh` (`critic_response_fingerprint`) and
  `_mql_tester_cache_skip_reason`; it cannot change a non-approving analyst outcome, but skipping it needs a
  contract change and was not done (task Part 7).
- Remaining levers that are user decisions: critic only for analyst APPROVE (≈−24% cost, contract change);
  `AI_SHADOW_REPEAT_ENABLE=false` (−3.7%); TTL 2 cadences (replay-safe, −5.5% more); correcting session
  semantics; lower reasoning effort / `compact_v1` (unproven, not touched).

## 20. Known limitations

- The gate is active only for requests from the new EA build (with `ai_review_context`); the EA must be
  re-attached in MT5 to load the compiled `.ex5`. Older builds are always called.
- Replay decisions for skipped requests are counterfactual only for non-approvals; the acceptance relies on
  "a reused decisive non-approval would still be a non-approval", supported by 0/250 historical flips.
- Replay history mixes models (Muse 825, Luna 873); the default policy lost no approval for either.
- Historical session context in the replay is reconstructed (validated 7,123/7,123); production uses MQL's.
- MQL still labels other deterministic Python non-trading decisions (hard pre-gate, taxonomy) as
  `degraded_ai_response_non_trading` (pre-existing, not changed).
- `m_consumed_sweep_keys` is in-memory; after an EA restart consumed sweeps are forgotten (pre-existing).
