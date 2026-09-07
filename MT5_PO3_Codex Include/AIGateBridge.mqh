//+------------------------------------------------------------------+
//| AIGateBridge.mqh - JSON request/response via Common folder         |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_AIGATEBRIDGE_MQH__
#define __PO3_AIGATE_AIGATEBRIDGE_MQH__
#include "FileBus.mqh"
#include "Types.mqh"
#include "Config.mqh"
#include "JsonLite.mqh"
// RewardWithinMaxDistance / PlanTickSize: the target menu must measure
// feasibility with the same function the sanitizer enforces it with.
#include "ExecutionAdjustmentContract.mqh"

class CAIGateBridge {
private:
   CFileBus *m_bus;
   datetime m_next_allowed; // backoff if AI failed recently
   string m_session_id;

   // Replay identity.  DecisionInputHash() is component 4 of every tester cache
   // signature, so it decides which recorded cohort a run can reach.  It used to
   // be recomputed on every lookup, so a single unstable term could re-key the
   // cohort mid-run and orphan every remaining recorded decision -- observed on
   // the 2026.09.06 CACHE_ONLY replay, where it changed once at simulated
   // 2026.08.03 09:34:59 and turned 11 recorded approvals into 1,276 cache
   // misses and 0 trades.  The identity is now computed once and frozen for the
   // life of the process; later drift is reported, never silently applied.
   bool   m_identity_frozen;
   string m_frozen_runtime_input_hash;
   string m_frozen_decision_input_hash;
   string m_frozen_policy_content_hash;
   string m_frozen_invalidation_content_hash;
   int    m_identity_drift_events;

   string _NowId(const string symbol) {
      uint r = (uint)MathRand();
      // Session scope prevents a stale response from a prior tester/terminal
      // run sharing the same timestamp/symbol/random suffix.
      return m_session_id + "_" + IntegerToString((int)TimeLocal())
             + "_" + symbol + "_" + IntegerToString((int)r);
   }

   string _ContractManifestJson() const {
      string j = "{";
      j += JsonKVStr("contract_manifest_version", CONTRACT_MANIFEST_VERSION) + ",";
      j += JsonKVStr("engine_version", ENGINE_VERSION) + ",";
      j += JsonKVStr("engine_input_schema", ENGINE_INPUT_SCHEMA) + ",";
      j += JsonKVStr("decision_schema_version", AI_DECISION_SCHEMA_VERSION) + ",";
      j += JsonKVStr("target_arbitration_schema_version", AI_TARGET_ARBITRATION_SCHEMA_VERSION) + ",";
      j += JsonKVStr("prompt_contract_version", AI_PROMPT_CONTRACT_VERSION) + ",";
      j += JsonKVStr("role_contract_version", AI_ROLE_CONTRACT_VERSION) + ",";
      j += JsonKVStr("provider_contract_version", AI_PROVIDER_CONTRACT_VERSION) + ",";
      j += JsonKVStr("file_bus_lifecycle_version", FILE_BUS_LIFECYCLE_VERSION) + ",";
      j += JsonKVStr("request_lifecycle_version", REQUEST_LIFECYCLE_VERSION) + ",";
      j += JsonKVStr("request_identity_version", AI_REQUEST_IDENTITY_VERSION) + ",";
      j += JsonKVStr("setup_taxonomy_version", SETUP_TAXONOMY_VERSION) + ",";
      j += JsonKVStr("family_profile_version", AI_FAMILY_PROFILE_VERSION) + ",";
      j += JsonKVStr("retrieval_policy_version", AI_RETRIEVAL_POLICY_VERSION) + ",";
      j += JsonKVStr("consensus_resolver_version", AI_CONSENSUS_RESOLVER_VERSION) + ",";
      j += JsonKVStr("evidence_envelope_version", AI_EVIDENCE_ENVELOPE_VERSION) + ",";
      j += JsonKVStr("trade_memory_schema_version", AI_TRADE_MEMORY_SCHEMA_VERSION) + ",";
      j += JsonKVStr("repeatability_schema_version", REPEATABILITY_SCHEMA_VERSION);
      j += "}";
      return j;
   }

   string _AccountTradeModeLabel() const {
      long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
      if(mode == ACCOUNT_TRADE_MODE_REAL) return "real";
      if(mode == ACCOUNT_TRADE_MODE_DEMO) return "demo";
      if(mode == ACCOUNT_TRADE_MODE_CONTEST) return "contest";
      return "unknown";
   }

   string _StopModelName() const {
      if(InpStopModel == STOP_FVG_EDGE) return "fvg_edge";
      if(InpStopModel == STOP_STRUCTURAL_SWING) return "structural_swing";
      return "structural_sweep";
   }

   bool _AllowedQualitativeVetoCode(const string code) const {
      return (code == "ai_veto_missing_mandatory_evidence" ||
              code == "ai_veto_structural_contradiction" ||
              code == "ai_veto_sequence_contradiction" ||
              code == "ai_veto_target_arbitration_incoherent" ||
              code == "ai_veto_execution_plan_mismatch" ||
              code == "ai_veto_prior_override_unsupported" ||
              code == "ai_veto_data_integrity_failure");
   }

   string _TargetCandidatesJson(const TradePlan &p) const {
      double risk_dist = MathAbs(p.entry_est - p.sl);
      double liquidity_tp = (p.liquidity_target_preserved > 0.0 ? p.liquidity_target_preserved : p.po3.liquidity_target);
      string liquidity_model = (StringLen(p.liquidity_target_model) > 0 ? p.liquidity_target_model :
                                (StringLen(p.target_model) > 0 ? p.target_model :
                                 (StringLen(p.po3.liquidity_kind) > 0 ? p.po3.liquidity_kind : "liquidity_target")));
      double liquidity_rr = p.liquidity_rr;
      if(liquidity_rr <= 0.0 && risk_dist > 0.0 && liquidity_tp > 0.0){
         double reward = (p.is_buy ? liquidity_tp - p.entry_est : p.entry_est - liquidity_tp);
         if(reward > 0.0) liquidity_rr = reward / risk_dist;
      }
      double capped_tp = p.capped_before_obstacle_tp;
      double capped_rr = p.capped_before_obstacle_rr;
      if(capped_rr <= 0.0 && risk_dist > 0.0 && capped_tp > 0.0){
         double reward = (p.is_buy ? capped_tp - p.entry_est : p.entry_est - capped_tp);
         if(reward > 0.0) capped_rr = reward / risk_dist;
      }
      double fallback_tp = p.fallback_tp;
      double fallback_rr = p.fallback_rr;
      if(fallback_rr <= 0.0 && risk_dist > 0.0 && fallback_tp > 0.0){
         double reward = (p.is_buy ? fallback_tp - p.entry_est : p.entry_est - fallback_tp);
         if(reward > 0.0) fallback_rr = reward / risk_dist;
      }
      double effective_fallback_rr = MathMax(InpFallbackRR2, InpMinLiveRR2 + MathMax(0.0, InpFallbackRRBufferR));
      double obstacle_to_liquidity_r = 0.0;
      if(risk_dist > 0.0 && p.obstacle_price > 0.0 && liquidity_tp > 0.0)
         obstacle_to_liquidity_r = MathAbs(liquidity_tp - p.obstacle_price) / risk_dist;
      double fvg_width = MathAbs(p.fvg.upper - p.fvg.lower);
      double fvg_width_atr = (p.fvg.gap_width_atr_score > 0.0 ? p.fvg.gap_width_atr_score : 0.0);
      // A partial leg is only "possible" if the execution layer will actually place it.
      // capped_rr > 0 was never that test: a 0.0108R leg in front of an adjacent major
      // obstacle satisfied it, so this payload advertised a partial that _BuildPlanPrices
      // then had to move past the obstacle -- and the AI vetoed the contradiction as
      // ai_veto_target_arbitration_incoherent.  p.min_tp1_reward is the same number the
      // ranking and the price builder use, published by CTradeEngine::_MinTp1Reward.
      double capped_tp1_reward = (capped_tp > 0.0
                                  ? (p.is_buy ? capped_tp - p.entry_est : p.entry_est - capped_tp)
                                  : 0.0);
      // p.min_tp1_reward is MathMax(stop_dist * 0.65, spread * mult) and is therefore
      // strictly positive for any plan that reached target selection.  Zero means the
      // floor was never computed -- a plan restored from a pre-fix state file -- and an
      // unmeasured leg must not be advertised as placeable.  Fail closed on unknown.
      bool capped_tp1_clears_floor = (capped_tp1_reward > 0.0 &&
                                      p.min_tp1_reward > 0.0 &&
                                      capped_tp1_reward >= p.min_tp1_reward);
      bool tp1_before_obstacle_possible = (InpAllowPartialBeforeObstacle && capped_tp > 0.0 &&
                                           capped_rr > 0.0 && capped_tp1_clears_floor);

      // Every route must be measured against the SAME cap the target sanitizer
      // will apply to whatever the model picks.  Offering a route that clears
      // the minimum RR floor while breaching the maximum distance is how the
      // 2026-09-04 EURCHF approval died: the payload said feasible_for_tp2=true
      // at rr2=5.7293 with max_allowed_distance sitting unused in the same
      // object, and _EvaluateTargetFeasibility then refused the approved plan
      // as ai_chosen_target_exceeds_max_distance.  A cap of 0 means "no cap",
      // matching RewardWithinMaxDistance and the sanitizer exactly, so this
      // can never be stricter than the enforcement.
      double tick_size = PlanTickSize(p.symbol);
      double max_allowed = p.fallback_max_allowed_distance;
      double liquidity_reward = (risk_dist > 0.0 ? liquidity_rr * risk_dist : 0.0);
      double capped_reward    = (risk_dist > 0.0 ? capped_rr * risk_dist : 0.0);
      bool liquidity_within_max = RewardWithinMaxDistance(liquidity_reward, max_allowed, tick_size);
      bool capped_within_max    = RewardWithinMaxDistance(capped_reward, max_allowed, tick_size);
      bool liquidity_rr_ok = (liquidity_rr + 0.0001 >= InpMinLiveRR2);
      bool capped_rr_ok    = (capped_rr + 0.0001 >= InpMinLiveRR2);
      bool liquidity_feasible = (liquidity_tp > 0.0 && liquidity_rr_ok && liquidity_within_max);
      bool capped_feasible    = (capped_tp > 0.0 && capped_rr_ok && capped_within_max);

      // The incumbent.  CTradeEngine already honours "keep_current" as an
      // arbitration answer (see the chosen_model == "keep_current" branch in
      // _ApplyAiTargetArbitration, which binds chosen_tp to p.tp2), but the
      // menu never told the model the option existed.  With the max-distance
      // test above now correctly retiring an over-cap liquidity route, a plan
      // whose OWN deterministic target is perfectly feasible -- EURCHF held
      // prev_day_high at 4.4254R inside a 5.0R cap -- would otherwise be left
      // with an all-infeasible menu and no way to say "the plan is fine".
      double current_reward = (p.tp2 > 0.0
                               ? (p.is_buy ? p.tp2 - p.entry_est : p.entry_est - p.tp2)
                               : 0.0);
      double current_rr = p.effective_rr2;
      if(current_rr <= 0.0 && risk_dist > 0.0 && current_reward > 0.0)
         current_rr = current_reward / risk_dist;
      bool current_within_max = RewardWithinMaxDistance(current_reward, max_allowed, tick_size);
      bool current_rr_ok = (current_rr + 0.0001 >= InpMinLiveRR2);
      bool current_feasible = (p.tp2 > 0.0 && current_reward > 0.0 && current_rr_ok && current_within_max);

      string j = "{";
      j += JsonKVBool("arbitration_required", p.target_arbitration_required) + ",";
      j += JsonKVNum("entry", p.entry_est, 8) + ",";
      j += JsonKVNum("sl", p.sl, 8) + ",";
      j += JsonKVNum("risk_distance", risk_dist, 8) + ",";
      j += JsonKVStr("current_target_source", p.target_source) + ",";
      j += JsonKVStr("current_tp_model", p.tp_model) + ",";
      j += JsonKVNum("current_tp2", p.tp2, 8) + ",";
      j += JsonKVNum("current_rr2", p.effective_rr2, 4) + ",";
      j += JsonKVStr("obstacle_kind", p.obstacle_kind) + ",";
      j += JsonKVNum("obstacle_price", p.obstacle_price, 8) + ",";
      j += JsonKVNum("obstacle_r", p.obstacle_r, 4) + ",";
      j += JsonKVNum("obstacle_distance_r", p.obstacle_distance_r, 4) + ",";
      j += JsonKVStr("obstacle_tf", p.obstacle_tf) + ",";
      j += JsonKVStr("obstacle_strength_features", p.obstacle_strength_features) + ",";
      j += JsonKVNum("effective_fallback_rr", effective_fallback_rr, 4) + ",";
      j += JsonKVNum("max_allowed_target_distance", p.fallback_max_allowed_distance, 8) + ",";
      j += "\"blocker_features\":{";
      j += JsonKVStr("obstacle_kind", p.obstacle_kind) + ",";
      j += JsonKVStr("obstacle_tf", p.obstacle_tf) + ",";
      j += JsonKVNum("obstacle_price", p.obstacle_price, 8) + ",";
      j += JsonKVNum("obstacle_distance_r", p.obstacle_distance_r, 4) + ",";
      j += JsonKVNum("obstacle_width_price", fvg_width, 8) + ",";
      j += JsonKVNum("obstacle_width_atr", fvg_width_atr, 4) + ",";
      j += JsonKVInt("obstacle_age_bars", p.fvg.age_bars) + ",";
      j += JsonKVStr("obstacle_freshness", (p.fvg.age_bars <= 0 ? "unknown" : (p.fvg.age_bars <= PO3EffectiveMaxContextAgeBars() ? "fresh" : "stale"))) + ",";
      j += JsonKVStr("obstacle_mitigation_state", p.fvg.mitigation_state) + ",";
      j += JsonKVNum("obstacle_mitigated_percent", MathMax(0.0, p.fvg.mitigation_depth_frac) * 100.0, 2) + ",";
      j += JsonKVBool("obstacle_retested", (p.fvg.touched || p.fvg.mid_mitigated)) + ",";
      j += JsonKVNum("distance_from_obstacle_to_liquidity_target_r", obstacle_to_liquidity_r, 4) + ",";
      j += JsonKVBool("tp1_before_obstacle_possible", tp1_before_obstacle_possible) + ",";
      j += JsonKVNum("capped_rr", capped_rr, 4) + ",";
      j += JsonKVNum("liquidity_rr", liquidity_rr, 4) + ",";
      j += JsonKVNum("fallback_rr", fallback_rr, 4) + ",";
      j += JsonKVNum("effective_fallback_rr", effective_fallback_rr, 4) + ",";
      j += JsonKVNum("displacement_quality", p.po3.displacement_score, 4) + ",";
      j += JsonKVNum("diagnostic_legacy_setup_score", p.setup_score, 4) + ",";
      j += JsonKVNum("htf_alignment_score", p.htf_alignment_score, 4) + ",";
      j += JsonKVStr("session", p.po3.session_name) + ",";
      j += JsonKVStr("killzone", p.killzone_code) + ",";
      j += JsonKVBool("is_htf_obstacle", StringFind(p.obstacle_tf, "htf") >= 0) + ",";
      j += JsonKVBool("is_ltf_obstacle", (StringFind(p.obstacle_tf, "ltf") >= 0 || p.obstacle_tf == "entry_tf"));
      j += "},";
      j += "\"liquidity_target\":{";
      j += JsonKVBool("available", liquidity_feasible) + ",";
      j += JsonKVStr("model", liquidity_model) + ",";
      j += JsonKVNum("tp2", liquidity_tp, 8) + ",";
      j += JsonKVNum("rr2", liquidity_rr, 4) + ",";
      j += JsonKVNum("reward_distance_price", liquidity_reward, 8) + ",";
      j += JsonKVNum("max_allowed_distance", max_allowed, 8) + ",";
      j += JsonKVBool("exceeds_max_target_distance", !liquidity_within_max) + ",";
      j += JsonKVBool("min_rr_pass", liquidity_rr_ok) + ",";
      j += JsonKVBool("max_rr_pass", liquidity_within_max) + ",";
      j += JsonKVBool("feasible_for_tp2", liquidity_feasible) + ",";
      j += JsonKVBool("feasible_for_tp1_only", false) + ",";
      // Reason precedence mirrors _EvaluateTargetFeasibility: max distance is
      // decided before the RR floor, so the two never disagree on why.
      j += JsonKVStr("infeasible_reason", (liquidity_tp <= 0.0 ? "missing_tp"
                                           : (!liquidity_within_max ? "exceeds_max_target_distance"
                                              : (liquidity_rr_ok ? "" : "rr_below_min")))) + ",";
      j += JsonKVBool("valid_structurally", p.liquidity_target_valid_structurally) + ",";
      j += JsonKVBool("blocked_by_obstacle", p.liquidity_target_blocked_by_obstacle);
      j += "},";
      j += "\"capped_before_obstacle\":{";
      j += JsonKVBool("available", capped_feasible) + ",";
      j += JsonKVStr("model", (StringLen(p.capped_before_obstacle_source) > 0 ? p.capped_before_obstacle_source : "capped_before_obstacle")) + ",";
      j += JsonKVNum("tp2", capped_tp, 8) + ",";
      j += JsonKVNum("rr2", capped_rr, 4) + ",";
      j += JsonKVNum("reward_distance_price", capped_reward, 8) + ",";
      j += JsonKVNum("max_allowed_distance", max_allowed, 8) + ",";
      j += JsonKVBool("exceeds_max_target_distance", !capped_within_max) + ",";
      j += JsonKVBool("min_rr_pass", capped_rr_ok) + ",";
      j += JsonKVBool("max_rr_pass", capped_within_max) + ",";
      j += JsonKVBool("feasible_for_tp2", capped_feasible) + ",";
      j += JsonKVBool("feasible_for_tp1_only", (capped_tp > 0.0 && InpAllowPartialBeforeObstacle && capped_tp1_clears_floor)) + ",";
      j += JsonKVStr("infeasible_reason", (capped_tp <= 0.0 ? "missing_tp"
                                           : (!capped_within_max ? "exceeds_max_target_distance"
                                              : (capped_rr_ok ? "" : "rr_below_min")))) + ",";
      j += JsonKVBool("partial_allowed", InpAllowPartialBeforeObstacle);
      j += "},";
      j += "\"partial_before_obstacle_then_liquidity\":{";
      // Availability and feasibility must judge BOTH legs.  They used to judge only the
      // runner, which is how a route whose first leg the engine cannot place was offered
      // to the model with an empty infeasible_reason.
      // The runner leg is the liquidity target, so it carries the liquidity
      // route's max-distance verdict too: a partial whose tp2 breaches the cap
      // is refused by the sanitizer exactly like a plain liquidity target.
      j += JsonKVBool("available", (InpAllowPartialBeforeObstacle && capped_tp > 0.0 && liquidity_tp > 0.0 && capped_tp1_clears_floor && liquidity_within_max)) + ",";
      j += JsonKVStr("model", "partial_before_obstacle_then_liquidity") + ",";
      j += JsonKVNum("tp1", capped_tp, 8) + ",";
      j += JsonKVNum("rr1", capped_rr, 4) + ",";
      j += JsonKVNum("tp1_reward_price", capped_tp1_reward, 8) + ",";
      j += JsonKVNum("tp1_min_required_reward", p.min_tp1_reward, 8) + ",";
      j += JsonKVNum("tp2", liquidity_tp, 8) + ",";
      j += JsonKVNum("rr2", liquidity_rr, 4) + ",";
      j += JsonKVNum("reward_distance_price", liquidity_reward, 8) + ",";
      j += JsonKVNum("max_allowed_distance", max_allowed, 8) + ",";
      j += JsonKVBool("exceeds_max_target_distance", !liquidity_within_max) + ",";
      j += JsonKVBool("max_rr_pass", liquidity_within_max) + ",";
      j += JsonKVBool("feasible_for_tp2", (InpAllowPartialBeforeObstacle && liquidity_tp > 0.0 && liquidity_rr_ok && liquidity_within_max && capped_tp1_clears_floor)) + ",";
      j += JsonKVBool("feasible_for_tp1_only", false) + ",";
      j += JsonKVStr("infeasible_reason", (!InpAllowPartialBeforeObstacle ? "partial_disabled" : (liquidity_tp <= 0.0 ? "missing_liquidity_tp2" : (!capped_tp1_clears_floor ? "partial_leg_below_tp1_floor" : (!liquidity_within_max ? "tp2_exceeds_max_target_distance" : (liquidity_rr_ok ? "" : "tp2_rr_below_min"))))));
      j += "},";
      j += "\"synthetic_rr_fallback\":{";
      j += JsonKVBool("available", p.fallback_feasible_for_tp2) + ",";
      j += JsonKVStr("model", (StringLen(p.fallback_source) > 0 ? p.fallback_source : "synthetic_rr_fallback")) + ",";
      j += JsonKVNum("tp", fallback_tp, 8) + ",";
      j += JsonKVNum("tp2", fallback_tp, 8) + ",";
      j += JsonKVNum("rr", fallback_rr, 4) + ",";
      j += JsonKVNum("rr2", fallback_rr, 4) + ",";
      j += JsonKVNum("configured_rr", effective_fallback_rr, 4) + ",";
      j += JsonKVNum("effective_rr2", effective_fallback_rr, 4) + ",";
      j += JsonKVBool("target_direction_valid", fallback_tp > 0.0) + ",";
      j += JsonKVBool("target_already_reached", false) + ",";
      j += JsonKVNum("reward_distance_price", p.fallback_reward_distance, 8) + ",";
      j += JsonKVNum("max_allowed_distance", p.fallback_max_allowed_distance, 8) + ",";
      j += JsonKVBool("exceeds_max_target_distance", p.fallback_infeasible_reason == "exceeds_max_target_distance") + ",";
      j += JsonKVBool("min_rr_pass", fallback_rr + 0.0001 >= InpMinLiveRR2) + ",";
      j += JsonKVBool("max_rr_pass", p.fallback_infeasible_reason != "exceeds_max_target_distance") + ",";
      j += JsonKVBool("feasible_for_tp2", p.fallback_feasible_for_tp2) + ",";
      j += JsonKVBool("feasible_for_tp1_only", p.fallback_feasible_for_tp1_only) + ",";
      j += JsonKVStr("infeasible_reason", p.fallback_infeasible_reason) + ",";
      j += JsonKVBool("crosses_obstacle", StringFind(p.obstacle_kind, "crossed") >= 0);
      j += "},";
      j += "\"synthetic_rr_capped_to_max_distance\":{";
      j += JsonKVBool("available", p.synthetic_capped_to_max_distance_feasible) + ",";
      j += JsonKVStr("model", "synthetic_rr_capped_to_max_distance") + ",";
      j += JsonKVNum("tp", p.synthetic_capped_to_max_distance_tp, 8) + ",";
      j += JsonKVNum("tp2", p.synthetic_capped_to_max_distance_tp, 8) + ",";
      j += JsonKVNum("rr", p.synthetic_capped_to_max_distance_rr, 4) + ",";
      j += JsonKVNum("rr2", p.synthetic_capped_to_max_distance_rr, 4) + ",";
      j += JsonKVNum("configured_rr", effective_fallback_rr, 4) + ",";
      j += JsonKVBool("feasible_for_tp2", p.synthetic_capped_to_max_distance_feasible) + ",";
      j += JsonKVBool("feasible_for_tp1_only", false) + ",";
      j += JsonKVStr("reason", p.synthetic_capped_to_max_distance_reason) + ",";
      j += JsonKVStr("infeasible_reason", (p.synthetic_capped_to_max_distance_feasible ? "" : "unavailable"));
      j += "},";
      // "keep_current" is already a valid answer to arbitration -- CTradeEngine
      // binds it to p.tp2 -- it was simply never on the menu.  The model must
      // be able to say "the target the engine already chose is the right one"
      // instead of being forced onto an alternative route.
      j += "\"keep_current_target\":{";
      j += JsonKVBool("available", current_feasible) + ",";
      j += JsonKVStr("model", "keep_current") + ",";
      j += JsonKVNum("tp2", p.tp2, 8) + ",";
      j += JsonKVNum("rr2", current_rr, 4) + ",";
      // The level's identity is already on the envelope as current_target_source
      // / current_tp_model; duplicating it here would emit fields the Python
      // compactor whitelist drops, so the raw request would disagree with what
      // the model actually reads.
      j += JsonKVNum("reward_distance_price", current_reward, 8) + ",";
      j += JsonKVNum("max_allowed_distance", max_allowed, 8) + ",";
      j += JsonKVBool("exceeds_max_target_distance", !current_within_max) + ",";
      j += JsonKVBool("min_rr_pass", current_rr_ok) + ",";
      j += JsonKVBool("max_rr_pass", current_within_max) + ",";
      j += JsonKVBool("feasible_for_tp2", current_feasible) + ",";
      j += JsonKVBool("feasible_for_tp1_only", false) + ",";
      j += JsonKVStr("infeasible_reason", (p.tp2 <= 0.0 || current_reward <= 0.0 ? "missing_tp"
                                           : (!current_within_max ? "exceeds_max_target_distance"
                                              : (current_rr_ok ? "" : "rr_below_min"))));
      j += "}";
      j += "}";
      return j;
   }

   uint _Fnv1a(const string value) const {
      uint h = 2166136261;
      for(int i=0; i<StringLen(value); i++){
         h = (h ^ (uint)StringGetCharacter(value, i)) * 16777619;
      }
      return h;
   }

   uint _Fnv1aBytes(const uchar &bytes[], const int count) const {
      uint h = 2166136261;
      for(int i=0; i<count; i++){
         h = (h ^ (uint)bytes[i]) * 16777619;
      }
      return h;
   }

   string _RequestNonce(const string req_id) const {
      return IntegerToString((int)(_Fnv1a(m_session_id + "|" + req_id) % 2147483647));
   }

   string _WorkloadMode() const {
      if(!(bool)MQLInfoInteger(MQL_TESTER)) return "LIVE_FORWARD";
      if(InpTesterAiMode == TESTER_AI_BOOTSTRAP_RULE_ONLY) return "TESTER_AI_BOOTSTRAP_RULE_ONLY";
      if(InpTesterAiMode == TESTER_AI_RECORD_ONLY) return "TESTER_AI_RECORD_ONLY";
      if(InpTesterAiMode == TESTER_AI_CACHE_ONLY) return "TESTER_AI_CACHE_ONLY";
      return "TESTER_AI_LIVE_WAIT_DEBUG";
   }

   string _BehaviorContractHash() const {
      string contract = LIVE_FORWARD_CONTRACT_VERSION
                        + "|fail_closed_full_structured_only|rule_only_non_trading"
                        + "|risk_caps_identical|semantic_cache|exact_or_quarantine";
      return IntegerToString((int)(_Fnv1a(contract) % 2147483647));
   }

   // Byte-exact content hash.  This used to open the file with FILE_TXT -- which
   // is UTF-16 in MQL5 unless FILE_ANSI is given -- and concatenate
   // FileReadString() results.  Both policy files are odd-length ASCII JSON, so
   // the byte stream does not divide into whole UTF-16 code units, and under the
   // file I/O load of a real scan that read did not return the same string
   // twice.  Measured on the 2026.09.06 one-day replay, against a file whose
   // mtime had not moved since 2026.07.17: 49736647 at init, then 1990245157,
   // 1989935653 and 1984261413 during the run.  That is what moved
   // DecisionInputHash(), which is component 4 of every replay cache signature.
   //
   // Hashing the raw bytes removes the decode entirely, and a short read is now
   // reported as unavailable instead of silently hashing as different content.
   string _CommonFileContentHash(const string path) const {
      int h = FileOpen(path, FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE) return "UNAVAILABLE";
      ulong size64 = FileSize(h);
      if(size64 > 268435456){ FileClose(h); return "UNAVAILABLE"; }
      int size = (int)size64;
      uchar bytes[];
      if(size > 0 && ArrayResize(bytes, size) != size){ FileClose(h); return "UNAVAILABLE"; }
      uint got = 0;
      if(size > 0) got = FileReadArray(h, bytes, 0, size);
      FileClose(h);
      if((int)got != size) return "UNAVAILABLE";
      return IntegerToString((int)(_Fnv1aBytes(bytes, size) % 2147483647));
   }

   string _PolicyIdentityContentHash(const string path) const {
      if(!InpTesterLegacyPolicyFingerprint || !(bool)MQLInfoInteger(MQL_TESTER) ||
         InpTesterAiMode != TESTER_AI_CACHE_ONLY)
         return _CommonFileContentHash(path);
      // Versioned compatibility with the historical digest, not the defective
      // FILE_TXT reader. Only complete little-endian code units participated in
      // the original stable cohort. The full raw bytes (including the odd tail)
      // are pinned separately at startup and monitored by CheckIdentityDrift.
      int h = FileOpen(path, FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE) return "UNAVAILABLE";
      ulong size64 = FileSize(h);
      if(size64 > 268435456){ FileClose(h); return "UNAVAILABLE"; }
      int size = (int)size64;
      uchar bytes[];
      if(size > 0 && ArrayResize(bytes, size) != size){ FileClose(h); return "UNAVAILABLE"; }
      uint got = (size > 0 ? FileReadArray(h, bytes, 0, size) : 0);
      FileClose(h);
      if((int)got != size) return "UNAVAILABLE";
      uint digest = 2166136261;
      for(int i=0; i+1<size; i+=2){
         uint unit = (uint)bytes[i] | ((uint)bytes[i+1] << 8);
         digest = (digest ^ unit) * 16777619;
      }
      return IntegerToString((int)(digest % 2147483647));
   }

   string _ComputeRuntimeInputHash() const {
      string s = ENGINE_INPUT_SCHEMA + "|";
      s += IntegerToString((int)InpStrategyMode) + "|" + InpStrategyPreset + "|";
      s += IntegerToString((int)InpStopModel) + "|" + IntegerToString((int)PO3EffectiveHTF()) + "|" + IntegerToString((int)PO3EffectiveEntryTF()) + "|";
      s += (InpTradeOnlyKillzones ? "1" : "0") + "|" + IntegerToString(InpLondonKillzoneStartHour) + ":" + IntegerToString(InpLondonKillzoneStartMinute) + "-" + IntegerToString(InpLondonKillzoneEndHour) + ":" + IntegerToString(InpLondonKillzoneEndMinute) + "|";
      s += IntegerToString(InpNewYorkKillzoneStartHour) + ":" + IntegerToString(InpNewYorkKillzoneStartMinute) + "-" + IntegerToString(InpNewYorkKillzoneEndHour) + ":" + IntegerToString(InpNewYorkKillzoneEndMinute) + "|";
      s += (InpEnableAsiaKillzone ? "1" : "0") + "|" + IntegerToString(InpAsiaKillzoneStartHour) + ":" + IntegerToString(InpAsiaKillzoneStartMinute) + "-" + IntegerToString(InpAsiaKillzoneEndHour) + ":" + IntegerToString(InpAsiaKillzoneEndMinute) + "|";
      s += (InpEnableMPCTrading ? "1" : "0") + "|" + (InpEnableBucketRiskPolicy ? "1" : "0") + "|" + InpBucketRiskPolicyFile + "|";
      s += (InpSuppressMicroBisiSibiEdge ? "1" : "0") + "|" + (InpSuppressStaleFvgBranches ? "1" : "0") + "|";
      s += (InpSuppressTouchedContinuationUnlessRetested ? "1" : "0") + "|" + (InpSuppressContinuationTouchedFvg ? "1" : "0") + "|" + (InpSuppressContinuationStaleFvg ? "1" : "0") + "|";
      s += DoubleToString(InpExecutionRejectCostR, 4) + "|" + DoubleToString(InpExecutionReduceRiskCostR, 4) + "|" + DoubleToString(InpMicroScalpMaxCostFracOfPlannedR, 4) + "|";
      s += (InpRejectSyntheticFallbackAfterCrossedObstacle ? "1" : "0") + "|";
      s += (InpRequireAITargetArbitrationOnObstacle ? "1" : "0") + "|" + (InpHardRejectCrossedObstacleTarget ? "1" : "0") + "|" + (InpAllowAIToUseLiquidityTargetBehindMinorBlocker ? "1" : "0") + "|" + (InpAllowPartialBeforeObstacle ? "1" : "0") + "|";
      s += DoubleToString(InpBlockerKillSeverity, 4) + "|" + DoubleToString(InpBlockerMajorSeverity, 4) + "|" + DoubleToString(InpBlockerMinorMaxSeverity, 4) + "|";
      s += DoubleToString(InpStandardTradeLiquidityRRFloor, 4) + "|" + DoubleToString(InpMaxTargetAdrFrac, 4) + "|" + DoubleToString(InpMaxTargetAtrMult, 4) + "|";
      s += (InpRequireDisplacement ? "1" : "0") + "|" + (InpAllowSyntheticRRTarget ? "1" : "0") + "|" + DoubleToString(InpMinLiveRR2, 4) + "|" + DoubleToString(InpFallbackRR2, 4) + "|" + DoubleToString(InpFallbackRRBufferR, 4) + "|" + DoubleToString(InpObstacleRejectR, 4) + "|";
      s += (InpTesterAiCache ? "1" : "0") + "|" + IntegerToString((int)InpTesterAiMode) + "|" + (InpTesterAllowLiveWaitDebugTrading ? "1" : "0") + "|";
      s += (InpUseAI ? "1" : "0") + "|" + (InpAiStrict ? "1" : "0") + "|" + DoubleToString(InpMinAiScoreTrend, 4) + "|";
      s += (InpAiVetoEnable ? "1" : "0") + "|" + DoubleToString(InpAiMinFollowThroughProb, 4) + "|" + DoubleToString(InpAiMaxInvalidationRisk, 4) + "|" + DoubleToString(InpAiMaxChopRisk, 4) + "|" + DoubleToString(InpAiMaxPostEntryFailureRisk, 4) + "|" + DoubleToString(InpAiMinFinalExpectancyScore, 4) + "|";
      s += DoubleToString(InpAiScoreFullPO3, 4) + "|" + DoubleToString(InpAiScoreMicroPO3, 4) + "|" + DoubleToString(InpAiScoreContinuation, 4) + "|" + DoubleToString(InpAiScoreRange, 4) + "|" + DoubleToString(InpAiScoreFailedBreakout, 4) + "|" + (InpGlobalAiScoreAsHardFloor ? "1" : "0") + "|";
      s += (InpUseSnapshotAI ? "1" : "0") + "|" + (InpRequireSnapshots ? "1" : "0") + "|" + (InpOnlyBreakerRetestVirginStrongOrigin ? "1" : "0") + "|";
      s += DoubleToString(InpRiskPerTradePct, 4) + "|" + DoubleToString(InpRiskPerTradeMoney, 4) + "|" + IntegerToString(InpMaxOpenPositions) + "|" + IntegerToString(InpMaxTradesPerScan) + "|" + IntegerToString(InpMaxTradesPerSweep);
      s += "|" + REPEATABILITY_SCHEMA_VERSION + "|" + HIERARCHICAL_PRIOR_SCHEMA_VERSION + "|";
      s += RISK_FACTOR_SCHEMA_VERSION + "|" + COMMISSION_MODEL_SCHEMA_VERSION + "|" + MANAGEMENT_SCHEMA_VERSION + "|";
      s += SHADOW_CANDIDATE_SCHEMA_VERSION + "|" + NORMALIZED_FVG_SCHEMA_VERSION + "|";
      s += IntegerToString((int)InpNormalizedFvgMode) + "|" + DoubleToString(InpNormalizedFvgSpreadMult, 4) + "|";
      s += DoubleToString(InpNormalizedFvgAtrFrac, 6) + "|" + DoubleToString(InpNormalizedFvgSessionNoiseFrac, 6) + "|";
      s += IntegerToString(InpNormalizedFvgMinAssetClassSamples) + "|" + InpNormalizedFvgAssetClassPolicyFile + "|";
      s += _PolicyIdentityContentHash(InpNormalizedFvgAssetClassPolicyFile) + "|";
      s += (InpBrokerCostHistoryEnable ? "1" : "0") + "|" + IntegerToString(InpBrokerCostMinSamples) + "|";
      s += DoubleToString(InpBrokerCostStressedPercentile, 4) + "|" + DoubleToString(InpCommissionFallbackPerLotRoundTurn, 6) + "|";
      s += (InpMaxTotalRiskEnable ? "1" : "0") + "|" + DoubleToString(InpMaxTotalRiskPct, 4) + "|" + DoubleToString(InpMaxTotalRiskMoney, 4) + "|";
      s += (InpRiskFactorGateEnable ? "1" : "0") + "|" + InpRiskFactorPolicyFile + "|";
      s += IntegerToString((int)InpThesisInvalidationPolicy) + "|" + IntegerToString((int)InpInvalidationConfirmationMode) + "|";
      s += IntegerToString(InpInvalidationPersistenceSeconds) + "|" + DoubleToString(InpInvalidationSpreadBufferMult, 4) + "|";
      s += IntegerToString(InpManagementActionRetryCooldownSec) + "|" + IntegerToString(InpManagementActionMaxRetries) + "|";
      s += (InpInvalidationAssetClassPolicyEnable ? "1" : "0") + "|" + InpInvalidationAssetClassPolicyFile + "|";
      s += _PolicyIdentityContentHash(InpInvalidationAssetClassPolicyFile) + "|";
      s += IntegerToString(InpCounterfactualHorizonMinutes) + "|" + IntegerToString(InpShadowCandidateHorizonMinutes) + "|";
      s += (InpUseBrokerSymbolSessions ? "1" : "0") + "|" + IntegerToString(InpSymbolNoEntryBeforeCloseMin) + "|" + IntegerToString(InpSymbolFlattenBeforeCloseMin);
      return IntegerToString((int)(_Fnv1a(s) % 2147483647));
   }

   string _ComputeDecisionInputHash() const {
      // Tester workflow/debug controls intentionally do not participate in this
      // economic decision identity. A decision recorded in RECORD_ONLY or
      // LIVE_WAIT_DEBUG must bind to the same setup during CACHE_ONLY replay.
      string s = ENGINE_INPUT_SCHEMA + "|" + AI_DECISION_SCHEMA_VERSION + "|";
      s += IntegerToString((int)InpStrategyMode) + "|" + InpStrategyPreset + "|";
      s += IntegerToString((int)InpStopModel) + "|" + IntegerToString((int)PO3EffectiveHTF()) + "|" + IntegerToString((int)PO3EffectiveEntryTF()) + "|";
      s += (InpTradeOnlyKillzones ? "1" : "0") + "|" + IntegerToString(InpLondonKillzoneStartHour) + ":" + IntegerToString(InpLondonKillzoneStartMinute) + "-" + IntegerToString(InpLondonKillzoneEndHour) + ":" + IntegerToString(InpLondonKillzoneEndMinute) + "|";
      s += IntegerToString(InpNewYorkKillzoneStartHour) + ":" + IntegerToString(InpNewYorkKillzoneStartMinute) + "-" + IntegerToString(InpNewYorkKillzoneEndHour) + ":" + IntegerToString(InpNewYorkKillzoneEndMinute) + "|";
      s += (InpEnableAsiaKillzone ? "1" : "0") + "|" + IntegerToString(InpAsiaKillzoneStartHour) + ":" + IntegerToString(InpAsiaKillzoneStartMinute) + "-" + IntegerToString(InpAsiaKillzoneEndHour) + ":" + IntegerToString(InpAsiaKillzoneEndMinute) + "|";
      s += (InpEnableMPCTrading ? "1" : "0") + "|" + (InpEnableBucketRiskPolicy ? "1" : "0") + "|" + InpBucketRiskPolicyFile + "|";
      s += (InpSuppressMicroBisiSibiEdge ? "1" : "0") + "|" + (InpSuppressStaleFvgBranches ? "1" : "0") + "|";
      s += (InpSuppressTouchedContinuationUnlessRetested ? "1" : "0") + "|" + (InpSuppressContinuationTouchedFvg ? "1" : "0") + "|" + (InpSuppressContinuationStaleFvg ? "1" : "0") + "|";
      s += DoubleToString(InpExecutionRejectCostR, 4) + "|" + DoubleToString(InpExecutionReduceRiskCostR, 4) + "|" + DoubleToString(InpMicroScalpMaxCostFracOfPlannedR, 4) + "|";
      s += (InpRejectSyntheticFallbackAfterCrossedObstacle ? "1" : "0") + "|" + (InpRequireAITargetArbitrationOnObstacle ? "1" : "0") + "|" + (InpHardRejectCrossedObstacleTarget ? "1" : "0") + "|";
      s += (InpAllowAIToUseLiquidityTargetBehindMinorBlocker ? "1" : "0") + "|" + (InpAllowPartialBeforeObstacle ? "1" : "0") + "|";
      s += DoubleToString(InpStandardTradeLiquidityRRFloor, 4) + "|" + DoubleToString(InpMaxTargetAdrFrac, 4) + "|" + DoubleToString(InpMaxTargetAtrMult, 4) + "|";
      s += (InpRequireDisplacement ? "1" : "0") + "|" + (InpAllowSyntheticRRTarget ? "1" : "0") + "|";
      s += DoubleToString(InpMinLiveRR2, 4) + "|" + DoubleToString(InpFallbackRR2, 4) + "|" + DoubleToString(InpFallbackRRBufferR, 4) + "|" + DoubleToString(InpObstacleRejectR, 4) + "|";
      s += (InpAiStrict ? "1" : "0") + "|" + DoubleToString(InpMinAiScoreTrend, 4) + "|";
      s += (InpAiVetoEnable ? "1" : "0") + "|" + DoubleToString(InpAiMinFollowThroughProb, 4) + "|" + DoubleToString(InpAiMaxInvalidationRisk, 4) + "|" + DoubleToString(InpAiMaxChopRisk, 4) + "|" + DoubleToString(InpAiMaxPostEntryFailureRisk, 4) + "|" + DoubleToString(InpAiMinFinalExpectancyScore, 4) + "|";
      s += DoubleToString(InpAiScoreFullPO3, 4) + "|" + DoubleToString(InpAiScoreMicroPO3, 4) + "|" + DoubleToString(InpAiScoreContinuation, 4) + "|" + DoubleToString(InpAiScoreRange, 4) + "|" + DoubleToString(InpAiScoreFailedBreakout, 4) + "|" + (InpGlobalAiScoreAsHardFloor ? "1" : "0") + "|";
      s += (InpOnlyBreakerRetestVirginStrongOrigin ? "1" : "0") + "|" + AI_TARGET_ARBITRATION_SCHEMA_VERSION + "|" + AI_PROMPT_CONTRACT_VERSION;
      s += "|" + HIERARCHICAL_PRIOR_SCHEMA_VERSION + "|" + REPEATABILITY_SCHEMA_VERSION + "|" + COMMISSION_MODEL_SCHEMA_VERSION + "|";
      s += IntegerToString((int)InpNormalizedFvgMode) + "|" + DoubleToString(InpNormalizedFvgSpreadMult, 4) + "|";
      s += DoubleToString(InpNormalizedFvgAtrFrac, 6) + "|" + DoubleToString(InpNormalizedFvgSessionNoiseFrac, 6) + "|";
      s += IntegerToString(InpNormalizedFvgMinAssetClassSamples) + "|" + InpNormalizedFvgAssetClassPolicyFile + "|";
      s += _PolicyIdentityContentHash(InpNormalizedFvgAssetClassPolicyFile) + "|";
      s += (InpBrokerCostHistoryEnable ? "1" : "0") + "|" + IntegerToString(InpBrokerCostMinSamples) + "|";
      s += DoubleToString(InpBrokerCostStressedPercentile, 4) + "|" + DoubleToString(InpCommissionFallbackPerLotRoundTurn, 6) + "|";
      s += MANAGEMENT_SCHEMA_VERSION + "|" + IntegerToString((int)InpThesisInvalidationPolicy) + "|";
      s += IntegerToString((int)InpInvalidationConfirmationMode) + "|" + IntegerToString(InpCounterfactualHorizonMinutes);
      return IntegerToString((int)(_Fnv1a(s) % 2147483647));
   }

   // Frozen accessors.  Every caller -- request payloads, the policy manifest,
   // the plan cohort stamp and the tester cache signature -- goes through these,
   // so they cannot disagree with each other the way the recomputing versions
   // did.  Before FreezeReplayIdentity() runs they fall back to computing, so an
   // early caller still gets a correct value rather than an empty string.
   string RuntimeInputHash() const {
      if(m_identity_frozen) return m_frozen_runtime_input_hash;
      return _ComputeRuntimeInputHash();
   }

   string DecisionInputHash() const {
      if(m_identity_frozen) return m_frozen_decision_input_hash;
      return _ComputeDecisionInputHash();
   }

   string RuntimeInputsJson() const {
      string j = "{";
      j += JsonKVStr("engine_version", ENGINE_VERSION) + ",";
      j += JsonKVStr("engine_input_schema", ENGINE_INPUT_SCHEMA) + ",";
      j += JsonKVStr("ai_decision_schema_version", AI_DECISION_SCHEMA_VERSION) + ",";
      j += JsonKVStr("ai_target_arbitration_schema_version", AI_TARGET_ARBITRATION_SCHEMA_VERSION) + ",";
      j += JsonKVStr("ai_prompt_contract_version", AI_PROMPT_CONTRACT_VERSION) + ",";
      j += JsonKVStr("repeatability_schema_version", REPEATABILITY_SCHEMA_VERSION) + ",";
      j += JsonKVStr("hierarchical_prior_schema_version", HIERARCHICAL_PRIOR_SCHEMA_VERSION) + ",";
      j += JsonKVStr("risk_factor_schema_version", RISK_FACTOR_SCHEMA_VERSION) + ",";
      j += JsonKVStr("commission_model_schema_version", COMMISSION_MODEL_SCHEMA_VERSION) + ",";
      j += JsonKVStr("management_schema_version", MANAGEMENT_SCHEMA_VERSION) + ",";
      j += JsonKVStr("management_experiment_schema_version", MANAGEMENT_EXPERIMENT_SCHEMA_VERSION) + ",";
      j += JsonKVStr("management_counterfactual_schema_version", MANAGEMENT_COUNTERFACTUAL_SCHEMA_VERSION) + ",";
      j += JsonKVStr("invalidation_policy_schema_version", INVALIDATION_POLICY_SCHEMA_VERSION) + ",";
      j += JsonKVStr("shadow_candidate_schema_version", SHADOW_CANDIDATE_SCHEMA_VERSION) + ",";
      j += JsonKVStr("normalized_fvg_schema_version", NORMALIZED_FVG_SCHEMA_VERSION) + ",";
      j += JsonKVStr("runtime_input_hash", RuntimeInputHash()) + ",";
      j += JsonKVStr("decision_input_hash", DecisionInputHash()) + ",";
      // The terminal's own wait budget. Python derives its response deadline and
      // write margin from this instead of assuming a literal, so both sides
      // enforce one absolute deadline. Operational only: deliberately excluded
      // from RuntimeInputHash(), because changing the wait budget must not
      // invalidate decision identity or the replay-cache cohort.
      j += JsonKVInt("ai_wait_timeout_ms", MathMax(1000, InpAiWaitTimeoutRealMin * 60 * 1000)) + ",";
      j += JsonKVInt("ai_wait_poll_ms", MathMax(50, InpAiWaitPollMs)) + ",";
      j += JsonKVInt("strategy_mode", (int)InpStrategyMode) + ",";
      j += JsonKVStr("strategy_preset", InpStrategyPreset) + ",";
      j += JsonKVStr("stop_model", _StopModelName()) + ",";
      j += JsonKVInt("htf", (int)PO3EffectiveHTF()) + ",";
      j += JsonKVInt("entry_tf", (int)PO3EffectiveEntryTF()) + ",";
      j += JsonKVInt("confirm_tf", (int)PO3EffectiveConfirmTF()) + ",";
      j += JsonKVBool("trade_only_killzones", InpTradeOnlyKillzones) + ",";
      j += JsonKVBool("enable_asia_killzone", InpEnableAsiaKillzone) + ",";
      j += JsonKVInt("asia_killzone_start_hour", InpAsiaKillzoneStartHour) + ",";
      j += JsonKVInt("asia_killzone_start_minute", InpAsiaKillzoneStartMinute) + ",";
      j += JsonKVInt("asia_killzone_end_hour", InpAsiaKillzoneEndHour) + ",";
      j += JsonKVInt("asia_killzone_end_minute", InpAsiaKillzoneEndMinute) + ",";
      j += JsonKVInt("london_killzone_start_hour", InpLondonKillzoneStartHour) + ",";
      j += JsonKVInt("london_killzone_start_minute", InpLondonKillzoneStartMinute) + ",";
      j += JsonKVInt("london_killzone_end_hour", InpLondonKillzoneEndHour) + ",";
      j += JsonKVInt("london_killzone_end_minute", InpLondonKillzoneEndMinute) + ",";
      j += JsonKVInt("newyork_killzone_start_hour", InpNewYorkKillzoneStartHour) + ",";
      j += JsonKVInt("newyork_killzone_start_minute", InpNewYorkKillzoneStartMinute) + ",";
      j += JsonKVInt("newyork_killzone_end_hour", InpNewYorkKillzoneEndHour) + ",";
      j += JsonKVInt("newyork_killzone_end_minute", InpNewYorkKillzoneEndMinute) + ",";
      j += JsonKVBool("enable_mpc_trading", InpEnableMPCTrading) + ",";
      j += JsonKVBool("enable_bucket_risk_policy", InpEnableBucketRiskPolicy) + ",";
      j += JsonKVStr("bucket_risk_policy_file", InpBucketRiskPolicyFile) + ",";
      j += JsonKVBool("suppress_micro_bisi_sibi_edge", InpSuppressMicroBisiSibiEdge) + ",";
      j += JsonKVBool("suppress_stale_fvg_branches", InpSuppressStaleFvgBranches) + ",";
      j += JsonKVBool("suppress_touched_continuation_unless_retested", InpSuppressTouchedContinuationUnlessRetested) + ",";
      j += JsonKVBool("suppress_continuation_touched_fvg", InpSuppressContinuationTouchedFvg) + ",";
      j += JsonKVBool("suppress_continuation_stale_fvg", InpSuppressContinuationStaleFvg) + ",";
      j += JsonKVBool("reject_synthetic_fallback_after_crossed_obstacle", InpRejectSyntheticFallbackAfterCrossedObstacle) + ",";
      j += JsonKVBool("require_ai_target_arbitration_on_obstacle", InpRequireAITargetArbitrationOnObstacle) + ",";
      j += JsonKVBool("hard_reject_crossed_obstacle_target", InpHardRejectCrossedObstacleTarget) + ",";
      j += JsonKVBool("allow_ai_to_use_liquidity_target_behind_minor_blocker", InpAllowAIToUseLiquidityTargetBehindMinorBlocker) + ",";
      j += JsonKVBool("allow_partial_before_obstacle", InpAllowPartialBeforeObstacle) + ",";
      j += JsonKVNum("blocker_kill_severity", InpBlockerKillSeverity, 4) + ",";
      j += JsonKVNum("blocker_major_severity", InpBlockerMajorSeverity, 4) + ",";
      j += JsonKVNum("blocker_minor_max_severity", InpBlockerMinorMaxSeverity, 4) + ",";
      j += JsonKVNum("obstacle_reject_r", InpObstacleRejectR, 4) + ",";
      j += JsonKVNum("standard_trade_liquidity_rr_floor", InpStandardTradeLiquidityRRFloor, 4) + ",";
      j += JsonKVNum("max_target_adr_frac", InpMaxTargetAdrFrac, 4) + ",";
      j += JsonKVNum("max_target_atr_mult", InpMaxTargetAtrMult, 4) + ",";
      j += JsonKVNum("execution_reject_cost_r", InpExecutionRejectCostR, 4) + ",";
      j += JsonKVNum("execution_reduce_risk_cost_r", InpExecutionReduceRiskCostR, 4) + ",";
      j += JsonKVNum("micro_scalp_max_cost_frac_of_planned_r", InpMicroScalpMaxCostFracOfPlannedR, 4) + ",";
      j += JsonKVBool("require_displacement", InpRequireDisplacement) + ",";
      j += JsonKVBool("allow_synthetic_rr_target", InpAllowSyntheticRRTarget) + ",";
      j += JsonKVNum("min_live_rr2", InpMinLiveRR2, 4) + ",";
      j += JsonKVNum("fallback_rr2", InpFallbackRR2, 4) + ",";
      j += JsonKVNum("fallback_rr_buffer_r", InpFallbackRRBufferR, 4) + ",";
      j += JsonKVBool("tester_ai_cache", InpTesterAiCache) + ",";
      j += JsonKVInt("tester_ai_mode", (int)InpTesterAiMode) + ",";
      j += JsonKVBool("tester_allow_live_wait_debug_trading", InpTesterAllowLiveWaitDebugTrading) + ",";
      j += JsonKVBool("tester_reject_stale_ai_results", InpTesterRejectStaleAiResults) + ",";
      j += JsonKVInt("tester_max_ai_result_age_sim_minutes", InpTesterMaxAiResultAgeSimMinutes) + ",";
      j += JsonKVBool("tester_freeze_ai_execution_snapshot", InpTesterFreezeAiExecutionSnapshot) + ",";
      j += JsonKVBool("use_ai", InpUseAI) + ",";
      j += JsonKVBool("ai_strict", InpAiStrict) + ",";
      j += JsonKVNum("min_llm_quality_score_trend", InpMinAiScoreTrend, 4) + ",";
      j += JsonKVBool("ai_veto_enable", InpAiVetoEnable) + ",";
      j += JsonKVNum("ai_min_follow_through_prob", InpAiMinFollowThroughProb, 4) + ",";
      j += JsonKVNum("ai_max_invalidation_risk", InpAiMaxInvalidationRisk, 4) + ",";
      j += JsonKVNum("ai_max_chop_risk", InpAiMaxChopRisk, 4) + ",";
      j += JsonKVNum("ai_max_post_entry_failure_risk", InpAiMaxPostEntryFailureRisk, 4) + ",";
      j += JsonKVNum("ai_min_final_expectancy_score", InpAiMinFinalExpectancyScore, 4) + ",";
      j += JsonKVNum("llm_quality_score_full_po3", InpAiScoreFullPO3, 4) + ",";
      j += JsonKVNum("llm_quality_score_micro_po3", InpAiScoreMicroPO3, 4) + ",";
      j += JsonKVNum("llm_quality_score_continuation", InpAiScoreContinuation, 4) + ",";
      j += JsonKVNum("llm_quality_score_range", InpAiScoreRange, 4) + ",";
      j += JsonKVNum("llm_quality_score_failed_breakout", InpAiScoreFailedBreakout, 4) + ",";
      j += JsonKVBool("global_llm_quality_as_hard_floor", InpGlobalAiScoreAsHardFloor) + ",";
      j += JsonKVNum("legacy_min_ai_confidence_diagnostic", 0.0, 4) + ",";
      j += JsonKVBool("use_snapshot_ai", InpUseSnapshotAI) + ",";
      j += JsonKVBool("require_snapshots", InpRequireSnapshots) + ",";
      j += JsonKVBool("exclusive_trading_enabled", InpOnlyBreakerRetestVirginStrongOrigin) + ",";
      j += JsonKVBool("only_breaker_retest_virgin_strong_origin", InpOnlyBreakerRetestVirginStrongOrigin) + ",";
      j += JsonKVInt("max_open_positions", InpMaxOpenPositions) + ",";
      j += JsonKVInt("max_trades_per_scan", InpMaxTradesPerScan) + ",";
      j += JsonKVInt("max_trades_per_sweep", InpMaxTradesPerSweep) + ",";
      j += JsonKVNum("risk_per_trade_pct", InpRiskPerTradePct, 4) + ",";
      j += JsonKVNum("risk_per_trade_money", InpRiskPerTradeMoney, 4) + ",";
      j += JsonKVBool("max_total_risk_enable", InpMaxTotalRiskEnable) + ",";
      j += JsonKVNum("max_total_risk_money", InpMaxTotalRiskMoney, 4) + ",";
      j += JsonKVNum("max_total_risk_pct", InpMaxTotalRiskPct, 4) + ",";
      j += JsonKVBool("risk_factor_gate_enable", InpRiskFactorGateEnable) + ",";
      j += JsonKVStr("risk_factor_policy_file", InpRiskFactorPolicyFile) + ",";
      j += JsonKVBool("broker_cost_history_enable", InpBrokerCostHistoryEnable) + ",";
      j += JsonKVInt("broker_cost_min_samples", InpBrokerCostMinSamples) + ",";
      j += JsonKVNum("broker_cost_stressed_percentile", InpBrokerCostStressedPercentile, 4) + ",";
      j += JsonKVNum("commission_fallback_per_lot_round_turn", InpCommissionFallbackPerLotRoundTurn, 6) + ",";
      j += JsonKVInt("normalized_fvg_mode", (int)InpNormalizedFvgMode) + ",";
      j += JsonKVNum("normalized_fvg_spread_mult", InpNormalizedFvgSpreadMult, 4) + ",";
      j += JsonKVNum("normalized_fvg_atr_frac", InpNormalizedFvgAtrFrac, 6) + ",";
      j += JsonKVNum("normalized_fvg_session_noise_frac", InpNormalizedFvgSessionNoiseFrac, 6) + ",";
      j += JsonKVInt("normalized_fvg_min_asset_class_samples", InpNormalizedFvgMinAssetClassSamples) + ",";
      j += JsonKVStr("normalized_fvg_asset_class_policy_file", InpNormalizedFvgAssetClassPolicyFile) + ",";
      j += JsonKVStr("normalized_fvg_asset_class_policy_hash", _PolicyIdentityContentHash(InpNormalizedFvgAssetClassPolicyFile)) + ",";
      j += JsonKVInt("invalidation_confirmation_mode", (int)InpInvalidationConfirmationMode) + ",";
      j += JsonKVInt("thesis_invalidation_policy", (int)InpThesisInvalidationPolicy) + ",";
      j += JsonKVInt("invalidation_persistence_seconds", InpInvalidationPersistenceSeconds) + ",";
      j += JsonKVNum("invalidation_spread_buffer_mult", InpInvalidationSpreadBufferMult, 4) + ",";
      j += JsonKVInt("management_action_retry_cooldown_sec", InpManagementActionRetryCooldownSec) + ",";
      j += JsonKVInt("management_action_max_retries", InpManagementActionMaxRetries) + ",";
      j += JsonKVBool("invalidation_asset_class_policy_enable", InpInvalidationAssetClassPolicyEnable) + ",";
      j += JsonKVStr("invalidation_asset_class_policy_file", InpInvalidationAssetClassPolicyFile) + ",";
      j += JsonKVStr("invalidation_asset_class_policy_hash", _PolicyIdentityContentHash(InpInvalidationAssetClassPolicyFile)) + ",";
      j += JsonKVInt("counterfactual_horizon_minutes", InpCounterfactualHorizonMinutes) + ",";
      j += JsonKVInt("shadow_candidate_horizon_minutes", InpShadowCandidateHorizonMinutes) + ",";
      j += JsonKVBool("use_broker_symbol_sessions", InpUseBrokerSymbolSessions) + ",";
      j += JsonKVInt("symbol_no_entry_before_close_min", InpSymbolNoEntryBeforeCloseMin) + ",";
      j += JsonKVInt("symbol_flatten_before_close_min", InpSymbolFlattenBeforeCloseMin) + ",";
      j += JsonKVInt("max_spread_ticks", InpMaxSpreadTicks) + ",";
      j += JsonKVNum("max_spread_risk_frac", InpMaxSpreadRiskFrac, 4) + ",";
      j += JsonKVNum("max_entry_drift_r", InpMaxEntryDriftR, 4) + ",";
      j += JsonKVStr("virtual_ledger_mode", "fixed_virtual_balance") + ",";
      j += JsonKVNum("analytics_virtual_balance", InpAnalyticsVirtualBalance, 2) + ",";
      j += JsonKVStr("backend_pnl_mode", "closed_deal_pnl");
      j += "}";
      return j;
   }

   void _AppendSchemaField(string &fields, const string field) const {
      if(StringLen(fields) > 0) fields += ",";
      fields += field;
   }

   bool _SchemaRequireString(const string json, const string key, string &value,
                             string &missing, string &invalid, const bool allow_empty=false) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1 || !JsonGetStringStrict(json, key, value, allow_empty)){
         _AppendSchemaField(invalid, key);
         return false;
      }
      return true;
   }

   bool _SchemaRequireBool(const string json, const string key, bool &value,
                           string &missing, string &invalid) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1 || !JsonGetBoolStrict(json, key, value)){
         _AppendSchemaField(invalid, key);
         return false;
      }
      return true;
   }

   bool _SchemaRequireNumber(const string json, const string key, double &value,
                             const double low, const double high,
                             string &missing, string &invalid) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1 || !JsonGetNumberStrict(json, key, value) || value < low || value > high){
         _AppendSchemaField(invalid, key);
         return false;
      }
      return true;
   }

   bool _SchemaRequireNullableNumber(const string json, const string key,
                                     double &value, bool &available,
                                     const double low, const double high,
                                     string &missing, string &invalid) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1){ _AppendSchemaField(invalid, key); return false; }
      if(JsonValueIsNullStrict(json, key)){
         value = 0.0;
         available = false;
         return true;
      }
      if(!JsonGetNumberStrict(json, key, value) || value < low || value > high){
         _AppendSchemaField(invalid, key);
         return false;
      }
      available = true;
      return true;
   }

   bool _SchemaRequireNull(const string json, const string key,
                           string &missing, string &invalid) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1 || !JsonValueIsNullStrict(json, key)){
         _AppendSchemaField(invalid, key);
         return false;
      }
      return true;
   }

   bool _SchemaRequireArray(const string json, const string key, string &value,
                            string &missing, string &invalid) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1 || !JsonGetArrayStrict(json, key, value)){
         _AppendSchemaField(invalid, key);
         return false;
      }
      return true;
   }

   bool _SchemaRequireObject(const string json, const string key, string &value,
                             string &missing, string &invalid) const {
      int count = JsonTopLevelKeyCount(json, key);
      if(count == 0){ _AppendSchemaField(missing, key); return false; }
      if(count != 1 || !JsonGetObjectStrict(json, key, value)){
         _AppendSchemaField(invalid, key);
         return false;
      }
      return true;
   }

   bool _ValidateStrictTargetArbitration(const string assessment,
                                         string &missing, string &invalid) const {
      string arb = "";
      if(!_SchemaRequireObject(assessment, "target_arbitration", arb, missing, invalid)) return false;
      string schema = "", prompt = "", chosen = "", blocker_kind = "", blocker_class = "", reason = "";
      bool required = false, killer = false;
      double tp1 = 0.0, tp2 = 0.0, rr1 = 0.0, rr2 = 0.0, severity = 0.0;
      _SchemaRequireString(arb, "target_arbitration_schema_version", schema, missing, invalid);
      _SchemaRequireString(arb, "prompt_contract_version", prompt, missing, invalid);
      _SchemaRequireBool(arb, "arbitration_required", required, missing, invalid);
      _SchemaRequireString(arb, "chosen_target_model", chosen, missing, invalid);
      _SchemaRequireNumber(arb, "chosen_tp1", tp1, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(arb, "chosen_tp2", tp2, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(arb, "chosen_rr1", rr1, 0.0, 1.0e6, missing, invalid);
      _SchemaRequireNumber(arb, "chosen_rr2", rr2, 0.0, 1.0e6, missing, invalid);
      string rejected = "", comparison = "";
      _SchemaRequireArray(arb, "rejected_target_models", rejected, missing, invalid);
      _SchemaRequireString(arb, "blocker_kind", blocker_kind, missing, invalid, true);
      _SchemaRequireNumber(arb, "blocker_severity", severity, -1.0, 10.0, missing, invalid);
      _SchemaRequireString(arb, "blocker_class", blocker_class, missing, invalid);
      _SchemaRequireBool(arb, "blocker_is_trade_killer", killer, missing, invalid);
      _SchemaRequireString(arb, "target_decision_reason", reason, missing, invalid);
      string why = "";
      _SchemaRequireString(arb, "why_not_liquidity_target", why, missing, invalid, true);
      _SchemaRequireString(arb, "why_not_partial_before_obstacle", why, missing, invalid, true);
      _SchemaRequireString(arb, "why_not_capped_before_obstacle", why, missing, invalid, true);
      _SchemaRequireString(arb, "why_not_synthetic_fallback", why, missing, invalid, true);
      _SchemaRequireObject(arb, "target_comparison", comparison, missing, invalid);
      if(schema != AI_TARGET_ARBITRATION_SCHEMA_VERSION) _AppendSchemaField(invalid, "target_arbitration_schema_version");
      if(prompt != AI_PROMPT_CONTRACT_VERSION) _AppendSchemaField(invalid, "prompt_contract_version");
      return (StringLen(missing) == 0 && StringLen(invalid) == 0);
   }

   bool _ValidateStrictCandidateAssessment(const string assessment,
                                           string &candidate_id,
                                           string &candidate_hash,
                                           string &fingerprint,
                                           string &missing,
                                           string &invalid) const {
      double number = 0.0;
      bool raw_allow_value = false;
      bool model_raw_allow_value = false;
      bool python_final_allow_value = false;
      bool veto_enabled_value = true;
      double risk_multiplier_value = -1.0;
      string veto_code_value = "";
      string veto_evidence_value = "";
       string veto_reason_value = "";
       string text_value = "";
       string request_id_value = "", request_identity_value = "";
       string provider_id_value = "", model_id_value = "", role_schema_value = "";
       _SchemaRequireString(assessment, "request_id", request_id_value, missing, invalid);
       _SchemaRequireString(assessment, "request_identity_hash", request_identity_value, missing, invalid);
       _SchemaRequireString(assessment, "provider_id", provider_id_value, missing, invalid);
       _SchemaRequireString(assessment, "model_id", model_id_value, missing, invalid);
       _SchemaRequireString(assessment, "role_schema_version", role_schema_value, missing, invalid);
       if(role_schema_value != AI_ROLE_CONTRACT_VERSION) _AppendSchemaField(invalid, "role_schema_version");
       _SchemaRequireNumber(assessment, "candidate_index", number, 0.0, 100000.0, missing, invalid);
      if(MathAbs(number - MathRound(number)) > 0.000001) _AppendSchemaField(invalid, "candidate_index");
      _SchemaRequireString(assessment, "candidate_id", candidate_id, missing, invalid);
      _SchemaRequireString(assessment, "candidate_hash", candidate_hash, missing, invalid);
      string taxonomy_version = "", taxonomy_enum = "", taxonomy_source = "";
      _SchemaRequireString(assessment, "setup_taxonomy_version", taxonomy_version, missing, invalid);
      _SchemaRequireString(assessment, "setup_taxonomy_enum", taxonomy_enum, missing, invalid);
      _SchemaRequireString(assessment, "taxonomy_mapping_source", taxonomy_source, missing, invalid);
      if(taxonomy_version != SETUP_TAXONOMY_VERSION) _AppendSchemaField(invalid, "setup_taxonomy_version");
      if(taxonomy_enum == "UNKNOWN_UNCLASSIFIED" || StringLen(taxonomy_enum) == 0)
         _AppendSchemaField(invalid, "setup_taxonomy_enum");
      if(StringLen(taxonomy_source) == 0) _AppendSchemaField(invalid, "taxonomy_mapping_source");
      _SchemaRequireString(assessment, "request_execution_fingerprint", text_value, missing, invalid);
      _SchemaRequireString(assessment, "assessed_execution_fingerprint", fingerprint, missing, invalid);
      _SchemaRequireNumber(assessment, "rule_score", number, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(assessment, "llm_quality_score", number, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(assessment, "blended_legacy_score", number, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(assessment, "legacy_agreement_confidence", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "llm_self_reported_confidence", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireBool(assessment, "raw_allow", raw_allow_value, missing, invalid);
      _SchemaRequireBool(assessment, "model_raw_allow", model_raw_allow_value, missing, invalid);
      _SchemaRequireBool(assessment, "python_final_allow", python_final_allow_value, missing, invalid);
      _SchemaRequireNull(assessment, "mql_final_allow", missing, invalid);
      if(model_raw_allow_value != raw_allow_value) _AppendSchemaField(invalid, "model_raw_allow_alias_mismatch");
      string state = "";
      _SchemaRequireString(assessment, "decision_state", state, missing, invalid);
      StringToUpper(state);
      if(state != "APPROVE" && state != "REJECT" && state != "ABSTAIN") _AppendSchemaField(invalid, "decision_state");
      string role_contract = "", role = "", verdict = "", historical_state = "", confidence_band = "", summary = "";
      bool thesis_supported = false;
      _SchemaRequireString(assessment, "role_contract_version", role_contract, missing, invalid);
      _SchemaRequireString(assessment, "role", role, missing, invalid);
      _SchemaRequireString(assessment, "verdict", verdict, missing, invalid);
      _SchemaRequireBool(assessment, "thesis_supported", thesis_supported, missing, invalid);
      string role_array = "";
      _SchemaRequireArray(assessment, "material_contradictions", role_array, missing, invalid);
      _SchemaRequireArray(assessment, "missing_required_evidence", role_array, missing, invalid);
      _SchemaRequireString(assessment, "historical_evidence_state", historical_state, missing, invalid);
      _SchemaRequireArray(assessment, "major_risks", role_array, missing, invalid);
      string evidence_refs = "";
      _SchemaRequireArray(assessment, "evidence_refs", evidence_refs, missing, invalid);
      _SchemaRequireString(assessment, "confidence_band", confidence_band, missing, invalid);
      _SchemaRequireString(assessment, "summary", summary, missing, invalid);
      StringToLower(role);
      StringToUpper(verdict);
      StringToUpper(historical_state);
      StringToUpper(confidence_band);
      string compact_refs = evidence_refs;
      StringReplace(compact_refs, " ", "");
      StringReplace(compact_refs, "\r", "");
      StringReplace(compact_refs, "\n", "");
      if(role_contract != AI_ROLE_CONTRACT_VERSION) _AppendSchemaField(invalid, "role_contract_version");
      if(role != "analyst") _AppendSchemaField(invalid, "role");
      if(verdict != state) _AppendSchemaField(invalid, "verdict");
      if(state == "APPROVE" && !thesis_supported) _AppendSchemaField(invalid, "approve_without_supported_thesis");
      if(historical_state != "SUPPORTIVE" && historical_state != "MIXED" &&
         historical_state != "ADVERSE" && historical_state != "INSUFFICIENT_SAMPLE")
         _AppendSchemaField(invalid, "historical_evidence_state");
      if(confidence_band != "LOW" && confidence_band != "MEDIUM" && confidence_band != "HIGH")
         _AppendSchemaField(invalid, "confidence_band");
      if(compact_refs == "[]") _AppendSchemaField(invalid, "evidence_refs");
      _SchemaRequireNumber(assessment, "structure_quality_score", number, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(assessment, "entry_timing_score", number, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(assessment, "follow_through_probability", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "invalidation_risk", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "chop_risk", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "cost_risk", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "symbol_bucket_risk", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "session_bucket_risk", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "post_entry_failure_risk", number, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(assessment, "final_trade_expectancy_score", number, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(assessment, "suggested_risk_multiplier", risk_multiplier_value, 0.0, 1.0, missing, invalid);
      _SchemaRequireString(assessment, "selected_target_identity", text_value, missing, invalid);
      _SchemaRequireNumber(assessment, "selected_target_price", number, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(assessment, "entry", number, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(assessment, "sl", number, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(assessment, "tp1", number, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(assessment, "tp2", number, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireString(assessment, "model_version", text_value, missing, invalid);
      _SchemaRequireString(assessment, "reasons", text_value, missing, invalid, true);
      string array_value = "";
      _SchemaRequireArray(assessment, "rejection_codes", array_value, missing, invalid);
      _SchemaRequireArray(assessment, "invalidation_risks", array_value, missing, invalid);
      _SchemaRequireArray(assessment, "missing_confirmations", array_value, missing, invalid);
      _SchemaRequireString(assessment, "narrative_state", text_value, missing, invalid, true);
      _SchemaRequireString(assessment, "bucket_prior_override_justification", text_value, missing, invalid, true);
      string veto = "";
      if(_SchemaRequireObject(assessment, "veto", veto, missing, invalid)){
         _SchemaRequireBool(veto, "enabled", veto_enabled_value, missing, invalid);
         _SchemaRequireString(veto, "code", veto_code_value, missing, invalid, true);
         _SchemaRequireArray(veto, "evidence_fields", veto_evidence_value, missing, invalid);
         _SchemaRequireString(veto, "reason", veto_reason_value, missing, invalid, true);
         string compact_evidence = veto_evidence_value;
         StringReplace(compact_evidence, " ", "");
         StringReplace(compact_evidence, "\r", "");
         StringReplace(compact_evidence, "\n", "");
         if(veto_enabled_value){
            if(!_AllowedQualitativeVetoCode(veto_code_value)) _AppendSchemaField(invalid, "veto.code");
            if(compact_evidence == "[]") _AppendSchemaField(invalid, "veto.evidence_fields");
            if(StringLen(veto_reason_value) == 0) _AppendSchemaField(invalid, "veto.reason");
         } else if(StringLen(veto_code_value) > 0 || compact_evidence != "[]" || StringLen(veto_reason_value) > 0){
            _AppendSchemaField(invalid, "veto.disabled_payload");
         }
      }
      bool calibration_available = true;
      _SchemaRequireBool(assessment, "calibration_available", calibration_available, missing, invalid);
      if(calibration_available) _AppendSchemaField(invalid, "calibration_available");
      _SchemaRequireNull(assessment, "calibrated_win_probability", missing, invalid);
      _SchemaRequireNull(assessment, "expected_net_r", missing, invalid);
      _SchemaRequireNull(assessment, "oos_predicted_probability", missing, invalid);
      string calibration_bucket = "", calibration_model = "", calibration_start = "", calibration_end = "";
      _SchemaRequireString(assessment, "calibration_bucket", calibration_bucket, missing, invalid, true);
      _SchemaRequireNumber(assessment, "calibration_sample_size", number, 0.0, 1.0e9, missing, invalid);
      if(MathAbs(number - MathRound(number)) > 0.000001 || MathRound(number) != 0.0)
         _AppendSchemaField(invalid, "calibration_sample_size");
      _SchemaRequireNull(assessment, "calibration_lower_bound", missing, invalid);
      _SchemaRequireNull(assessment, "calibration_upper_bound", missing, invalid);
      _SchemaRequireString(assessment, "calibration_model_version", calibration_model, missing, invalid, true);
      _SchemaRequireString(assessment, "calibration_data_window_start", calibration_start, missing, invalid, true);
      _SchemaRequireString(assessment, "calibration_data_window_end", calibration_end, missing, invalid, true);
      if(StringLen(calibration_bucket) > 0) _AppendSchemaField(invalid, "calibration_bucket");
      if(StringLen(calibration_model) > 0) _AppendSchemaField(invalid, "calibration_model_version");
      if(StringLen(calibration_start) > 0) _AppendSchemaField(invalid, "calibration_data_window_start");
      if(StringLen(calibration_end) > 0) _AppendSchemaField(invalid, "calibration_data_window_end");
      _ValidateStrictTargetArbitration(assessment, missing, invalid);
      if(state == "APPROVE" && (!raw_allow_value || veto_enabled_value || risk_multiplier_value <= 0.0))
         _AppendSchemaField(invalid, "approve_state_contract");
      if((state == "REJECT" || state == "ABSTAIN") && raw_allow_value)
         _AppendSchemaField(invalid, "non_approve_raw_allow");
      if(state == "REJECT" && !veto_enabled_value)
         _AppendSchemaField(invalid, "reject_requires_evidence_backed_veto");
      if(state == "ABSTAIN" && MathAbs(risk_multiplier_value) > 0.00000001)
         _AppendSchemaField(invalid, "abstain_risk_multiplier");
      return (StringLen(missing) == 0 && StringLen(invalid) == 0);
   }

public:
   bool ValidateReplayIdentityMode(string &reason) const {
      reason = "ok";
      if(!InpTesterLegacyPolicyFingerprint) return true;
      if(!(bool)MQLInfoInteger(MQL_TESTER) || InpTesterAiMode != TESTER_AI_CACHE_ONLY){
         reason = "legacy_policy_identity_requires_cache_only_tester";
         return false;
      }
      if(StringLen(InpTesterLegacyNormalizedPolicyRawHash) == 0 ||
         StringLen(InpTesterLegacyInvalidationPolicyRawHash) == 0 ||
         InpTesterLegacyNormalizedPolicyRawHash == "UNAVAILABLE" ||
         InpTesterLegacyInvalidationPolicyRawHash == "UNAVAILABLE"){
         reason = "legacy_policy_identity_requires_full_byte_pins";
         return false;
      }
      if(_CommonFileContentHash(InpNormalizedFvgAssetClassPolicyFile) != InpTesterLegacyNormalizedPolicyRawHash ||
         _CommonFileContentHash(InpInvalidationAssetClassPolicyFile) != InpTesterLegacyInvalidationPolicyRawHash){
         reason = "legacy_policy_identity_raw_bytes_mismatch";
         return false;
      }
      return true;
   }

   CAIGateBridge(CFileBus &bus){
      m_bus=&bus;
      m_next_allowed=0;
      m_session_id = IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) + "_"
                     + IntegerToString((int)TimeLocal()) + "_" + IntegerToString((int)GetTickCount());
      m_identity_frozen = false;
      m_frozen_runtime_input_hash = "";
      m_frozen_decision_input_hash = "";
      m_frozen_policy_content_hash = "";
      m_frozen_invalidation_content_hash = "";
      m_identity_drift_events = 0;
   }

   // Called once from OnInit, after inputs are bound and before the first plan
   // is built.  From here on the replay identity is immutable for this process.
   string FreezeReplayIdentity() {
      m_frozen_policy_content_hash = _CommonFileContentHash(InpNormalizedFvgAssetClassPolicyFile);
      m_frozen_invalidation_content_hash = _CommonFileContentHash(InpInvalidationAssetClassPolicyFile);
      m_frozen_runtime_input_hash = _ComputeRuntimeInputHash();
      m_frozen_decision_input_hash = _ComputeDecisionInputHash();
      m_identity_frozen = true;
      return IdentityDiagnostics();
   }

   bool IdentityFrozen() const { return m_identity_frozen; }
   int  IdentityDriftEvents() const { return m_identity_drift_events; }

   string IdentityDiagnostics() const {
      return "frozen=" + (m_identity_frozen ? "true" : "false")
             + " policy_identity_version=" + (InpTesterLegacyPolicyFingerprint ? "legacy_utf16_units_v1_pinned" : "raw_bytes_v2")
             + " decision_input_hash=" + m_frozen_decision_input_hash
             + " runtime_input_hash=" + m_frozen_runtime_input_hash
             + " normalized_fvg_policy_content=" + m_frozen_policy_content_hash
             + " invalidation_policy_content=" + m_frozen_invalidation_content_hash
             + " normalized_fvg_policy_file=" + InpNormalizedFvgAssetClassPolicyFile
             + " invalidation_policy_file=" + InpInvalidationAssetClassPolicyFile
             + " drift_events=" + IntegerToString(m_identity_drift_events);
   }

   // Recomputes the identity and reports disagreement with the frozen value.
   // It deliberately does NOT adopt the new value: adopting it is precisely the
   // failure this freeze exists to prevent.  Returns true when drift is seen.
   bool CheckIdentityDrift(string &detail) {
      detail = "";
      if(!m_identity_frozen) return false;
      string policy_now = _CommonFileContentHash(InpNormalizedFvgAssetClassPolicyFile);
      string invalidation_now = _CommonFileContentHash(InpInvalidationAssetClassPolicyFile);
      string decision_now = _ComputeDecisionInputHash();
      string runtime_now = _ComputeRuntimeInputHash();
      if(decision_now == m_frozen_decision_input_hash &&
         runtime_now == m_frozen_runtime_input_hash &&
         policy_now == m_frozen_policy_content_hash &&
         invalidation_now == m_frozen_invalidation_content_hash)
         return false;
      m_identity_drift_events++;
      detail = "frozen_decision_input_hash=" + m_frozen_decision_input_hash
               + " observed_decision_input_hash=" + decision_now
               + " frozen_runtime_input_hash=" + m_frozen_runtime_input_hash
               + " observed_runtime_input_hash=" + runtime_now
               + " frozen_normalized_fvg_policy_content=" + m_frozen_policy_content_hash
               + " observed_normalized_fvg_policy_content=" + policy_now
               + " frozen_invalidation_policy_content=" + m_frozen_invalidation_content_hash
               + " observed_invalidation_policy_content=" + invalidation_now
               + " drift_events=" + IntegerToString(m_identity_drift_events)
               + " action=frozen_identity_retained";
      return true;
   }

   string RuntimeHash() const {
      return RuntimeInputHash();
   }

   string DecisionHash() const {
      return DecisionInputHash();
   }

   string WorkloadMode() const { return _WorkloadMode(); }
   string BehaviorContractHash() const { return _BehaviorContractHash(); }
   string SessionId() const { return m_session_id; }
   string RequestNonce(const string req_id) const { return _RequestNonce(req_id); }
   string ResponseBindingHash(const string req_id, const AiDecision &dec) const {
       string material = req_id + "|" + m_session_id + "|" + _RequestNonce(req_id)
                          + "|" + dec.request_identity_hash
                          + "|" + dec.contract_manifest_hash
                          + "|" + AI_DECISION_SCHEMA_VERSION + "|CACHE_OF_FULL_STRUCTURED"
                         + "|" + dec.provider_mode
                        + "|" + dec.provider_id
                        + "|" + dec.actual_model_id
                        + "|" + dec.model_fingerprint
                        + "|" + dec.generation_settings_hash
                        + "|" + dec.decision_state
                        + "|" + (dec.model_raw_allow ? "1" : "0")
                        + "|" + (dec.python_final_allow ? "1" : "0")
                        + "|" + dec.selected_candidate_id + "|" + dec.selected_candidate_hash
                        + "|" + dec.assessed_execution_fingerprint + "|" + dec.selected_target_identity
                        + "|" + DoubleToString(dec.selected_target_price, 8)
                        + "|" + DoubleToString(dec.llm_quality_score, 6)
                        + "|" + DoubleToString(dec.suggested_risk_multiplier, 6);
      return IntegerToString((int)(_Fnv1a(material) % 2147483647));
   }

   bool CanSendNow() {
      return (TimeLocal() >= m_next_allowed);
   }

   string BuildRequestJson(const TradePlan &plans[],
                           const string tester_cache_signature="",
                           const string tester_cache_key="") {
       int count = ArraySize(plans);
       if(count <= 0) return "{}";

       TradePlan p = plans[0];
       // Rich payload: PO3 sequence, regime alignment, session/liquidity context, and execution plan.
       double risk_dist = MathAbs(p.entry_est - p.sl);
       double rr2 = 0.0;
       if(risk_dist > 0) rr2 = MathAbs(p.tp2 - p.entry_est) / risk_dist;
       double liquidity_rr = 0.0;
       if(risk_dist > 0 && p.po3.liquidity_target > 0){
          double reward = (p.is_buy ? p.po3.liquidity_target - p.entry_est : p.entry_est - p.po3.liquidity_target);
          if(reward > 0) liquidity_rr = reward / risk_dist;
       }
       string runtime_input_hash = RuntimeInputHash();
       datetime server_time = TimeTradeServer();
       if(server_time <= 0) server_time = TimeLocal();
       datetime request_sim_time = (p.ai_request_time > 0 ? p.ai_request_time : server_time);
       long request_wall_time = (long)GetTickCount64();

       string j="{";
       j += JsonKVStr("id", p.req_id) + ",";
       j += JsonKVStr("session_id", m_session_id) + ",";
       j += JsonKVStr("request_nonce", _RequestNonce(p.req_id)) + ",";
       j += JsonKVStr("request_identity_version", AI_REQUEST_IDENTITY_VERSION) + ",";
       j += JsonKVInt("request_created_sim_time", (int)request_sim_time) + ",";
       j += "\"request_created_wall_time\":" + IntegerToString(request_wall_time) + ",";
       j += JsonKVStr("engine_version", ENGINE_VERSION) + ",";
       j += JsonKVStr("input_schema_version", ENGINE_INPUT_SCHEMA) + ",";
       j += JsonKVStr("contract_manifest_version", CONTRACT_MANIFEST_VERSION) + ",";
       j += JsonKVStr("contract_manifest_hash", PO3ContractManifestHash()) + ",";
       j += "\"contract_manifest\":" + _ContractManifestJson() + ",";
       j += JsonKVStr("workload_mode", _WorkloadMode()) + ",";
       j += JsonKVStr("live_forward_contract_version", LIVE_FORWARD_CONTRACT_VERSION) + ",";
       j += JsonKVStr("behavior_contract_hash_mql", _BehaviorContractHash()) + ",";
       j += JsonKVStr("symbol", p.symbol) + ",";
       j += JsonKVBool("is_buy", p.is_buy) + ",";
       j += JsonKVStr("candidate_hash", p.candidate_hash) + ",";
       j += JsonKVStr("request_execution_fingerprint", p.request_execution_fingerprint) + ",";
       j += JsonKVStr("assessed_execution_fingerprint", p.assessed_execution_fingerprint) + ",";
       j += JsonKVStr("runtime_input_hash", runtime_input_hash) + ",";
       j += JsonKVStr("decision_input_hash", DecisionInputHash()) + ",";
       j += JsonKVStr("decision_schema_version", AI_DECISION_SCHEMA_VERSION) + ",";
       if(StringLen(tester_cache_signature) > 0)
          j += JsonKVStr("tester_cache_signature", tester_cache_signature) + ",";
       if(StringLen(tester_cache_key) > 0)
          j += JsonKVStr("tester_cache_key", tester_cache_key) + ",";

       MqlTick tick;
       ZeroMemory(tick);
       SymbolInfoTick(p.symbol, tick);

       j += "\"runtime\":{";
       j += JsonKVBool("tester", (bool)MQLInfoInteger(MQL_TESTER)) + ",";
       j += JsonKVInt("tester_ai_mode", (int)InpTesterAiMode) + ",";
       j += JsonKVStr("workload_mode", _WorkloadMode()) + ",";
       j += JsonKVStr("account_trade_mode", _AccountTradeModeLabel()) + ",";
       j += JsonKVBool("live_fail_closed_on_ai_failure", InpLiveFailClosedOnAIFailure) + ",";
       j += JsonKVBool("allow_rule_only_fallback", InpAllowRuleOnlyFallback) + ",";
       j += JsonKVBool("allow_rule_only_live", InpAllowRuleOnlyLive) + ",";
       j += JsonKVNum("fallback_risk_multiplier", InpFallbackRiskMultiplier, 4) + ",";
       j += JsonKVBool("snapshot_ai_enabled", InpUseSnapshotAI) + ",";
       j += JsonKVBool("require_snapshots", InpRequireSnapshots) + ",";
       j += JsonKVNum("legacy_min_ai_confidence_diagnostic", 0.0, 4);
       j += "},";

       j += "\"runtime_inputs\":" + RuntimeInputsJson() + ",";

       j += "\"snapshot_metadata\":{";
       j += JsonKVStr("symbol", p.symbol) + ",";
       j += JsonKVInt("timeframe", (int)p.ltf) + ",";
       j += JsonKVInt("server_time", (int)server_time) + ",";
       j += JsonKVInt("candle_time", (int)(p.last_confirm_bar_time > 0 ? p.last_confirm_bar_time : p.fvg.t_form)) + ",";
       j += JsonKVInt("setup_snapshot_time", (int)p.setup_snapshot_time) + ",";
       j += JsonKVInt("ai_request_time", (int)p.ai_request_time) + ",";
       j += JsonKVNum("bid", tick.bid, 8) + ",";
       j += JsonKVNum("ask", tick.ask, 8) + ",";
       j += JsonKVStr("setup_id", p.setup_id) + ",";
       j += JsonKVStr("po3_state", p.po3.po3_state) + ",";
       j += JsonKVInt("po3_state_id", (int)p.po3.state) + ",";
       j += JsonKVNum("fvg_lower", p.fvg.lower, 8) + ",";
       j += JsonKVNum("fvg_upper", p.fvg.upper, 8) + ",";
       j += JsonKVNum("fvg_mid", p.fvg.mid, 8);
       j += "},";

      // PO3
       j += "\"po3\":{";
       j += JsonKVStr("po3_state", p.po3.po3_state) + ",";
       j += JsonKVInt("po3_state_id", (int)p.po3.state) + ",";
       j += JsonKVStr("po3_state_reason", p.po3.po3_state_reason) + ",";
       j += JsonKVStr("sweep_side", p.po3.sweep_side) + ",";
        j += JsonKVStr("structure_type", p.po3.structure_type) + ",";
        j += JsonKVStr("htf_structure_type", p.po3.htf_structure_type) + ",";
        j += JsonKVStr("ltf_structure_type", p.po3.ltf_structure_type) + ",";
        j += JsonKVStr("final_setup_class", p.po3.final_setup_class) + ",";
        j += JsonKVStr("po3_scope", p.po3.po3_scope) + ",";
        j += JsonKVBool("has_sweep", p.po3.has_sweep) + ",";
       j += JsonKVBool("has_displacement", p.po3.has_displacement) + ",";
       j += JsonKVBool("has_bos", p.po3.has_bos) + ",";
       j += JsonKVBool("has_follow_through", p.po3.has_follow_through) + ",";
       j += JsonKVStr("context_tier", p.po3.context_tier) + ",";
       j += JsonKVBool("developing_bos", p.po3.developing_bos) + ",";
       j += JsonKVInt("context_age_bars", p.po3.context_age_bars) + ",";
       j += JsonKVBool("htf_bos", p.po3.htf_bos) + ",";
       j += JsonKVBool("htf_internal_bos", p.po3.htf_internal_bos) + ",";
       j += JsonKVBool("htf_swing_bos", p.po3.htf_swing_bos) + ",";
       j += JsonKVInt("htf_pretrend_dir", p.po3.htf_pretrend_dir) + ",";
       j += JsonKVInt("t_sweep", (int)p.po3.t_sweep) + ",";
       j += JsonKVInt("t_disp", (int)p.po3.t_disp) + ",";
       j += JsonKVInt("t_bos", (int)p.po3.t_bos) + ",";
       j += JsonKVInt("t_follow", (int)p.po3.t_follow) + ",";
       j += JsonKVNum("dr_high", p.po3.dr_high, 8) + ",";
       j += JsonKVNum("dr_low", p.po3.dr_low, 8) + ",";
       j += JsonKVNum("dr_mid", p.po3.dr_mid, 8) + ",";
       j += JsonKVNum("manip_low", p.po3.manip_low, 8) + ",";
       j += JsonKVNum("manip_high", p.po3.manip_high, 8) + ",";
       j += JsonKVNum("bos_level", p.po3.bos_level, 8) + ",";
       j += JsonKVNum("sweep_strength", p.po3.sweep_strength, 6) + ",";
       j += JsonKVNum("context_score", p.po3.context_score, 4) + ",";
       j += JsonKVNum("displacement_score", p.po3.displacement_score, 4) + ",";
       j += JsonKVNum("displacement_body_frac", p.po3.displacement_body_frac, 4) + ",";
       j += JsonKVNum("displacement_range_atr", p.po3.displacement_range_atr, 4) + ",";
       j += JsonKVNum("displacement_volume_ratio", p.po3.displacement_volume_ratio, 4) + ",";
       j += JsonKVNum("displacement_speed_score", p.po3.displacement_speed_score, 4) + ",";
       j += JsonKVNum("displacement_follow_score", p.po3.displacement_follow_score, 4) + ",";
       j += JsonKVStr("session_name", p.po3.session_name) + ",";
       j += JsonKVStr("session_code", p.po3.session_code) + ",";
       j += JsonKVStr("killzone_name", p.po3.killzone_name) + ",";
       j += JsonKVBool("in_killzone", p.po3.in_killzone) + ",";
       j += JsonKVInt("session_start", (int)p.po3.session_start) + ",";
       j += JsonKVInt("session_end", (int)p.po3.session_end) + ",";
       j += JsonKVNum("session_high", p.po3.session_high, 8) + ",";
       j += JsonKVNum("session_low", p.po3.session_low, 8) + ",";
       j += JsonKVNum("asia_high", p.po3.asia_high, 8) + ",";
       j += JsonKVNum("asia_low", p.po3.asia_low, 8) + ",";
       j += JsonKVNum("london_high", p.po3.london_high, 8) + ",";
       j += JsonKVNum("london_low", p.po3.london_low, 8) + ",";
       j += JsonKVNum("newyork_high", p.po3.newyork_high, 8) + ",";
       j += JsonKVNum("newyork_low", p.po3.newyork_low, 8) + ",";
       j += JsonKVNum("prev_day_high", p.po3.prev_day_high, 8) + ",";
       j += JsonKVNum("prev_day_low", p.po3.prev_day_low, 8) + ",";
       j += JsonKVNum("prev_week_high", p.po3.prev_week_high, 8) + ",";
       j += JsonKVNum("prev_week_low", p.po3.prev_week_low, 8) + ",";
       j += JsonKVInt("daily_bias_dir", p.po3.daily_bias_dir) + ",";
       j += JsonKVInt("h4_bias_dir", p.po3.h4_bias_dir) + ",";
       j += JsonKVInt("h1_bias_dir", p.po3.h1_bias_dir) + ",";
       j += JsonKVNum("liquidity_target", p.po3.liquidity_target, 8) + ",";
       j += JsonKVBool("liquidity_target_high", p.po3.liquidity_target_high) + ",";
       j += JsonKVStr("liquidity_kind", p.po3.liquidity_kind) + ",";
       j += JsonKVInt("liquidity_cluster_count", p.po3.liquidity_cluster_count) + ",";
       j += JsonKVBool("htf_mss", p.po3.htf_mss) + ",";
       j += JsonKVBool("htf_choch", p.po3.htf_choch) + ",";
       j += JsonKVBool("ltf_bos", p.po3.ltf_bos) + ",";
       j += JsonKVBool("ltf_internal_bos", p.po3.ltf_internal_bos) + ",";
       j += JsonKVBool("ltf_swing_bos", p.po3.ltf_swing_bos) + ",";
       j += JsonKVInt("ltf_pretrend_dir", p.po3.ltf_pretrend_dir) + ",";
       j += JsonKVBool("ltf_mss", p.po3.ltf_mss) + ",";
       j += JsonKVBool("ltf_choch", p.po3.ltf_choch) + ",";
       j += JsonKVInt("ltf_structure_time", (int)p.po3.ltf_structure_time) + ",";
       j += JsonKVNum("ltf_structure_level", p.po3.ltf_structure_level, 8) + ",";
       j += JsonKVNum("swing_low", p.po3.swing_low, 8) + ",";
       j += JsonKVNum("swing_high", p.po3.swing_high, 8);
       j += "},";

       // Regime
       j += "\"regime\":{";
       j += JsonKVNum("atr_pct", p.atr_pct, 6) + ",";
       j += JsonKVNum("trend_strength", p.trend_strength, 6) + ",";
       j += JsonKVNum("trend_slope_pct", p.trend_slope_pct, 8) + ",";
       j += JsonKVNum("adx_value", p.adx_value, 4) + ",";
       j += JsonKVNum("adr_pct", p.adr_pct, 6) + ",";
       j += JsonKVNum("session_vol_ratio", p.session_vol_ratio, 6) + ",";
       j += JsonKVNum("vwap_dist_atr", p.vwap_dist_atr, 6) + ",";
       j += JsonKVNum("compression_score", p.compression_score, 6) + ",";
       j += JsonKVNum("expansion_score", p.expansion_score, 6) + ",";
       j += JsonKVNum("news_risk", p.news_risk, 6);
       j += "},";

       // Plan
       j += "\"plan\":{";
       j += JsonKVStr("setup_id", p.setup_id) + ",";
       j += JsonKVStr("fvg_id", p.fvg_id) + ",";
       j += JsonKVStr("candidate_id", p.candidate_id) + ",";
       j += JsonKVStr("trade_key", p.trade_key) + ",";
       j += JsonKVStr("broker_comment", p.broker_comment) + ",";
       j += JsonKVStr("model_code", p.model_code) + ",";
       j += JsonKVStr("setup_code", p.model_code) + ",";
       j += JsonKVBool("is_mpc", p.model_code == "MPC" || StringFind(p.broker_comment, "MPC-") == 0) + ",";
       j += JsonKVStr("session_code", p.session_code) + ",";
       j += JsonKVStr("killzone_code", p.killzone_code) + ",";
       j += JsonKVStr("runtime_input_hash", runtime_input_hash) + ",";
       j += JsonKVStr("policy_version", p.policy_version) + ",";
       j += JsonKVStr("risk_version", p.risk_version) + ",";
       j += JsonKVStr("configured_stop_model", _StopModelName()) + ",";
       j += JsonKVStr("stop_model", _StopModelName()) + ",";
       j += JsonKVNum("entry_est", p.entry_est, 8) + ",";
       j += JsonKVStr("entry_model", p.entry_model) + ",";
       j += JsonKVStr("entry_branch", p.entry_branch) + ",";
       j += JsonKVStr("tp_model", p.tp_model) + ",";
       j += JsonKVStr("target_source", p.target_source) + ",";
       j += JsonKVNum("sl", p.sl, 8) + ",";
       j += JsonKVNum("tp1", p.tp1, 8) + ",";
       j += JsonKVNum("tp2", p.tp2, 8) + ",";
       j += JsonKVNum("rr2", rr2, 4) + ",";
       j += JsonKVNum("effective_rr2", p.effective_rr2, 4) + ",";
       j += JsonKVNum("estimated_cost_price", p.estimated_cost_price, 8) + ",";
       j += JsonKVNum("estimated_slippage_price", p.estimated_slippage_price, 8) + ",";
       j += JsonKVNum("estimated_commission_money", p.estimated_commission_money, 4) + ",";
       j += JsonKVNum("execution_cost_r", p.execution_cost_r, 4) + ",";
       j += JsonKVNum("slippage_r", p.slippage_r, 4) + ",";
       j += JsonKVNum("commission_r", p.commission_r, 4) + ",";
       j += JsonKVBool("execution_cost_risk_reduced", p.execution_cost_risk_reduced) + ",";
       j += JsonKVNum("execution_cost_risk_multiplier", p.execution_cost_risk_multiplier, 4) + ",";
       j += JsonKVNum("diagnostic_legacy_setup_score", p.setup_score, 4) + ",";
       j += JsonKVNum("heuristic_quality_estimate", p.heuristic_quality_estimate, 4) + ",";
       j += JsonKVStr("setup_family", p.setup_family) + ",";
       j += JsonKVStr("setup_class", p.setup_class) + ",";
       j += JsonKVStr("fvg_execution_class", p.fvg_execution_class) + ",";
       j += JsonKVStr("management_profile", p.management_profile) + ",";
       j += JsonKVStr("target_model", p.target_model) + ",";
       j += JsonKVStr("analytics_key", p.analytics_key) + ",";
       j += JsonKVStr("bucket_policy_action", p.bucket_policy_action) + ",";
       j += JsonKVStr("bucket_policy_reason", p.bucket_policy_reason) + ",";
       j += JsonKVStr("bucket_policy_key", p.bucket_policy_key) + ",";
       j += JsonKVNum("bucket_policy_risk_multiplier", p.bucket_policy_risk_multiplier, 4) + ",";
       j += JsonKVNum("liquidity_rr", liquidity_rr, 4) + ",";
       j += JsonKVStr("asset_class", p.asset_class) + ",";
       j += JsonKVStr("regime_profile", p.regime_profile) + ",";
       j += JsonKVStr("volatility_profile", p.volatility_profile) + ",";
       j += JsonKVStr("policy_bucket", p.policy_bucket) + ",";
       j += JsonKVBool("runner_trade", p.runner_trade) + ",";
       j += JsonKVBool("runner_downgraded", p.runner_downgraded) + ",";
       j += JsonKVStr("runner_downgrade_reason", p.runner_downgrade_reason) + ",";
       j += JsonKVNum("original_runner_target", p.original_runner_target, 8) + ",";
       j += JsonKVNum("standard_target_after_downgrade", p.standard_target_after_downgrade, 8) + ",";
       j += JsonKVNum("sequence_quality", p.sequence_quality, 4) + ",";
       j += JsonKVNum("htf_alignment_score", p.htf_alignment_score, 4) + ",";
       j += JsonKVNum("adverse_context_score", p.adverse_context_score, 4) + ",";
       j += JsonKVNum("net_reward_after_cost_r", p.net_reward_after_cost_r, 4) + ",";
       j += JsonKVStr("subtype_policy_action", p.subtype_policy_action) + ",";
       j += JsonKVNum("subtype_policy_penalty", p.subtype_policy_penalty, 4) + ",";
       j += JsonKVNum("subtype_shrunk_win_rate", p.subtype_shrunk_win_rate, 4) + ",";
       j += JsonKVNum("subtype_avg_r", p.subtype_avg_r, 4) + ",";
       j += JsonKVNum("setup_floor_score", p.setup_floor_score, 4) + ",";
       j += JsonKVNum("setup_floor_penalty", p.setup_floor_penalty, 4) + ",";
       j += JsonKVStr("setup_floor_action", p.setup_floor_action) + ",";
       j += JsonKVStr("policy_snapshot_id", p.policy_snapshot_id) + ",";
       j += JsonKVStr("stop_floor_reason", p.stop_floor_reason) + ",";
       j += JsonKVNum("stop_floor_distance", p.stop_floor_distance, 8) + ",";
       j += JsonKVNum("stop_noise_band", p.stop_noise_band, 8) + ",";
       j += JsonKVNum("stop_quality_score", p.stop_quality_score, 4) + ",";
       j += JsonKVStr("obstacle_kind", p.obstacle_kind) + ",";
       j += JsonKVNum("obstacle_price", p.obstacle_price, 8) + ",";
       j += JsonKVNum("obstacle_r", p.obstacle_r, 4) + ",";
       j += JsonKVNum("obstacle_distance_r", p.obstacle_distance_r, 4) + ",";
       j += JsonKVBool("target_arbitration_required", p.target_arbitration_required) + ",";
       j += JsonKVNum("liquidity_target_preserved", p.liquidity_target_preserved, 8) + ",";
       j += JsonKVStr("liquidity_target_model", p.liquidity_target_model) + ",";
       j += JsonKVBool("liquidity_target_valid_structurally", p.liquidity_target_valid_structurally) + ",";
       j += JsonKVBool("liquidity_target_blocked_by_obstacle", p.liquidity_target_blocked_by_obstacle) + ",";
       j += JsonKVNum("fallback_tp", p.fallback_tp, 8) + ",";
       j += JsonKVNum("fallback_rr", p.fallback_rr, 4) + ",";
       j += JsonKVStr("fallback_source", p.fallback_source) + ",";
       j += JsonKVNum("capped_before_obstacle_tp", p.capped_before_obstacle_tp, 8) + ",";
       j += JsonKVNum("capped_before_obstacle_rr", p.capped_before_obstacle_rr, 4) + ",";
       j += JsonKVStr("capped_before_obstacle_source", p.capped_before_obstacle_source) + ",";
       j += JsonKVNum("original_planned_tp_before_ai", p.original_planned_tp_before_ai, 8) + ",";
       j += JsonKVNum("original_planned_rr_before_ai", p.original_planned_rr_before_ai, 4) + ",";
       j += JsonKVNum("tp1_r_multiple", p.tp1_r_multiple, 4) + ",";
       j += JsonKVNum("tp1_partial_pct", p.tp1_partial_pct, 4) + ",";
       j += JsonKVStr("be_rule", p.be_rule) + ",";
       j += JsonKVNum("be_trigger_r", p.be_trigger_r, 4);
       j += "},";

       j += "\"target_candidates\":" + _TargetCandidatesJson(p) + ",";

       j += "\"watchlist\":{";
       j += JsonKVBool("armed", p.armed) + ",";
       j += JsonKVBool("mid_touched", p.mid_touched) + ",";
       j += JsonKVBool("b50_touched", p.b50_touched) + ",";
       j += JsonKVInt("bars_waited", p.bars_waited) + ",";
       j += JsonKVStr("setup_id", p.setup_id) + ",";
       j += JsonKVStr("lineage_root_id", p.lineage_root_id) + ",";
       j += JsonKVStr("parent_setup_id", p.parent_setup_id) + ",";
       j += JsonKVInt("lineage_version", p.lineage_version) + ",";
        j += JsonKVStr("narrative_state", p.narrative_state) + ",";
        j += JsonKVStr("superseded_by", p.superseded_by) + ",";
        j += JsonKVStr("invalidation_cause", p.invalidation_cause) + ",";
        j += JsonKVInt("attempt_number_for_sweep", p.attempt_number_for_sweep) + ",";
        j += JsonKVStr("ote_state", p.ote_state) + ",";
       j += JsonKVNum("ote_distance_frac", p.ote_distance_frac, 4) + ",";
       j += JsonKVNum("ote_softness_frac", p.ote_softness_frac, 4);
       j += "},";

       j += "\"portfolio\":{";
       j += JsonKVNum("portfolio_score", p.portfolio_score, 4) + ",";
       j += JsonKVInt("portfolio_rank", p.portfolio_rank) + ",";
       j += JsonKVStr("portfolio_cluster", p.portfolio_cluster) + ",";
       j += JsonKVStr("usd_exposure_key", p.usd_exposure_key) + ",";
       j += JsonKVStr("scheduler_action", p.scheduler_action) + ",";
       j += JsonKVStr("scheduler_reason", p.scheduler_reason) + ",";
       j += JsonKVNum("session_concentration", p.session_concentration, 4) + ",";
       j += JsonKVNum("usd_concentration", p.usd_concentration, 4) + ",";
       j += JsonKVNum("cluster_concentration", p.cluster_concentration, 4) + ",";
       j += JsonKVNum("diversity_bonus", p.diversity_bonus, 4);
       j += "},";

       j += "\"snapshots\":{";
       j += JsonKVStr("htf_path", p.snapshot_htf_path) + ",";
       j += JsonKVStr("ltf_path", p.snapshot_ltf_path);
       j += "},";

       // Current best FVG as a dedicated object plus candidate list for backwards compatibility
       j += "\"fvg\":{";
       j += JsonKVInt("t_form", (int)p.fvg.t_form) + ",";
       j += JsonKVNum("lower", p.fvg.lower, 8) + ",";
       j += JsonKVNum("upper", p.fvg.upper, 8) + ",";
       j += JsonKVNum("mid", p.fvg.mid, 8) + ",";
       j += JsonKVBool("touched", p.fvg.touched) + ",";
       j += JsonKVBool("mid_mitigated", p.fvg.mid_mitigated) + ",";
       j += JsonKVBool("fully_filled", p.fvg.fully_filled) + ",";
       j += JsonKVBool("invalidated", p.fvg.invalidated) + ",";
       j += JsonKVBool("entry_invalid", p.fvg.entry_invalid) + ",";
       j += JsonKVBool("structure_invalidated", p.fvg.structure_invalidated) + ",";
       j += JsonKVStr("mitigation_state", p.fvg.mitigation_state) + ",";
       j += JsonKVStr("invalidation_reason", p.fvg.invalidation_reason) + ",";
       j += JsonKVBool("continuation", p.fvg.continuation) + ",";
       j += JsonKVBool("reversal", p.fvg.reversal) + ",";
       j += JsonKVStr("context_type", p.fvg.context_type) + ",";
       j += JsonKVNum("continuation_score", p.fvg.continuation_score, 4) + ",";
       j += JsonKVNum("reversal_score", p.fvg.reversal_score, 4) + ",";
       j += JsonKVNum("score", p.fvg.score, 4) + ",";
       j += JsonKVStr("execution_class", p.fvg.execution_class) + ",";
       j += JsonKVNum("mitigation_depth_frac", p.fvg.mitigation_depth_frac, 4) + ",";
       j += JsonKVInt("age_bars", p.fvg.age_bars) + ",";
       j += JsonKVNum("displacement_candle_score", p.fvg.displacement_candle_score, 4) + ",";
       j += JsonKVNum("middle_candle_body_score", p.fvg.middle_candle_body_score, 4) + ",";
       j += JsonKVNum("volume_impulse_score", p.fvg.volume_impulse_score, 4) + ",";
       j += JsonKVNum("gap_width_atr_score", p.fvg.gap_width_atr_score, 4) + ",";
       j += JsonKVNum("manipulation_distance_score", p.fvg.manipulation_distance_score, 4) + ",";
       j += JsonKVNum("premium_discount_score", p.fvg.premium_discount_score, 4) + ",";
       j += JsonKVNum("htf_nesting_score", p.fvg.htf_nesting_score, 4) + ",";
       j += JsonKVNum("freshness_score", p.fvg.freshness_score, 4) + ",";
       j += JsonKVNum("retest_quality_score", p.fvg.retest_quality_score, 4) + ",";
       j += JsonKVNum("opposing_obstruction_score", p.fvg.opposing_obstruction_score, 4);
       j += "},";

       // Explicit PO3 narrative blocks help the Python gate reason about AMD-style structure.
        j += "\"story\":{";
        j += JsonKVStr("source_context_tier", p.source_context_tier) + ",";
        j += JsonKVStr("source_sweep_side", p.source_sweep_side) + ",";
        j += JsonKVInt("source_t_sweep", (int)p.source_t_sweep) + ",";
        j += JsonKVInt("source_t_disp", (int)p.source_t_disp) + ",";
        j += JsonKVInt("source_t_bos", (int)p.source_t_bos) + ",";
        j += JsonKVNum("source_manip_low", p.source_manip_low, 8) + ",";
        j += JsonKVNum("source_manip_high", p.source_manip_high, 8) + ",";
        j += JsonKVNum("source_dr_high", p.source_dr_high, 8) + ",";
        j += JsonKVNum("source_dr_low", p.source_dr_low, 8) + ",";
        j += JsonKVNum("source_liquidity_target", p.source_liquidity_target, 8) + ",";
        j += JsonKVStr("source_liquidity_kind", p.source_liquidity_kind) + ",";
        j += "\"accumulation\":{";
       j += JsonKVNum("range_high", p.po3.dr_high, 8) + ",";
       j += JsonKVNum("range_low", p.po3.dr_low, 8) + ",";
       j += JsonKVNum("range_mid", p.po3.dr_mid, 8);
       j += "},";
       j += "\"manipulation\":{";
       j += JsonKVInt("time", (int)p.po3.t_sweep) + ",";
       j += JsonKVBool("toward_sellside", p.is_buy) + ",";
       j += JsonKVNum("strength", p.po3.sweep_strength, 6);
       j += "},";
       j += "\"distribution\":{";
       j += JsonKVInt("time", (int)p.po3.t_bos) + ",";
       j += JsonKVNum("bos_level", p.po3.bos_level, 8) + ",";
       j += JsonKVNum("target", p.po3.liquidity_target, 8) + ",";
       j += JsonKVBool("target_is_high", p.po3.liquidity_target_high) + ",";
       j += JsonKVStr("target_kind", p.po3.liquidity_kind) + ",";
       j += JsonKVInt("cluster_count", p.po3.liquidity_cluster_count);
       j += "}";
       j += "},";

       // Candidate list: multiple entry plans for the same symbol so AI can pick the best return path.
       j += "\"candidates\":[";
       for(int i=0; i<count; i++){
          TradePlan c = plans[i];
          double c_risk = MathAbs(c.entry_est - c.sl);
          double c_rr2 = (c_risk > 0 ? MathAbs(c.tp2 - c.entry_est) / c_risk : 0.0);
          double c_liq_rr = 0.0;
          if(c_risk > 0 && c.po3.liquidity_target > 0){
             double reward = (c.is_buy ? c.po3.liquidity_target - c.entry_est : c.entry_est - c.po3.liquidity_target);
             if(reward > 0) c_liq_rr = reward / c_risk;
          }

          if(i > 0) j += ",";
          j += "{";
          j += JsonKVInt("candidate_index", i) + ",";
          j += JsonKVStr("setup_id", c.setup_id) + ",";
          j += JsonKVStr("fvg_id", c.fvg_id) + ",";
           j += JsonKVStr("candidate_id", c.candidate_id) + ",";
           j += JsonKVStr("candidate_hash", c.candidate_hash) + ",";
           j += JsonKVStr("setup_taxonomy_version", c.setup_taxonomy_version) + ",";
           j += JsonKVStr("setup_taxonomy_enum", c.setup_taxonomy_enum) + ",";
           j += JsonKVStr("taxonomy_mapping_source", c.taxonomy_mapping_source) + ",";
           j += JsonKVStr("request_execution_fingerprint", c.request_execution_fingerprint) + ",";
           j += JsonKVStr("assessed_execution_fingerprint", c.assessed_execution_fingerprint) + ",";
           j += JsonKVStr("decision_input_hash", DecisionInputHash()) + ",";
           j += JsonKVInt("symbol_digits", (int)SymbolInfoInteger(c.symbol, SYMBOL_DIGITS)) + ",";
           j += JsonKVNum("symbol_tick_size", SymbolInfoDouble(c.symbol, SYMBOL_TRADE_TICK_SIZE), 10) + ",";
          j += JsonKVStr("trade_key", c.trade_key) + ",";
          j += JsonKVInt("setup_snapshot_time", (int)c.setup_snapshot_time) + ",";
          j += JsonKVInt("ai_request_time", (int)c.ai_request_time) + ",";
          j += JsonKVStr("broker_comment", c.broker_comment) + ",";
          j += JsonKVStr("model_code", c.model_code) + ",";
          j += JsonKVStr("setup_code", c.model_code) + ",";
          j += JsonKVBool("is_mpc", c.model_code == "MPC" || StringFind(c.broker_comment, "MPC-") == 0) + ",";
          j += JsonKVStr("session_code", c.session_code) + ",";
          j += JsonKVStr("killzone_code", c.killzone_code) + ",";
          j += JsonKVStr("runtime_input_hash", runtime_input_hash) + ",";
          j += JsonKVStr("po3_state", c.po3.po3_state) + ",";
          j += JsonKVInt("po3_state_id", (int)c.po3.state) + ",";
          j += JsonKVStr("po3_state_reason", c.po3.po3_state_reason) + ",";
          j += JsonKVStr("sweep_side", c.po3.sweep_side) + ",";
           j += JsonKVStr("structure_type", c.po3.structure_type) + ",";
           j += JsonKVStr("final_setup_class", c.po3.final_setup_class) + ",";
           j += JsonKVStr("po3_scope", c.po3.po3_scope) + ",";
           j += JsonKVStr("session_name", c.po3.session_name) + ",";
           j += JsonKVStr("po3_session_code", c.po3.session_code) + ",";
           j += JsonKVStr("killzone_name", c.po3.killzone_name) + ",";
           j += JsonKVBool("in_killzone", c.po3.in_killzone) + ",";
           j += JsonKVStr("source_context_tier", c.source_context_tier) + ",";
           j += JsonKVInt("source_t_sweep", (int)c.source_t_sweep) + ",";
           j += JsonKVInt("source_t_disp", (int)c.source_t_disp) + ",";
           j += JsonKVInt("source_t_bos", (int)c.source_t_bos) + ",";
           j += JsonKVInt("attempt_number_for_sweep", c.attempt_number_for_sweep) + ",";
           j += JsonKVStr("configured_stop_model", _StopModelName()) + ",";
           j += JsonKVStr("stop_model", _StopModelName()) + ",";
           j += JsonKVStr("entry_model", c.entry_model) + ",";
          j += JsonKVStr("entry_branch", c.entry_branch) + ",";
          j += JsonKVStr("tp_model", c.tp_model) + ",";
          j += JsonKVStr("target_source", c.target_source) + ",";
          j += JsonKVNum("entry_est", c.entry_est, 8) + ",";
          j += JsonKVNum("sl", c.sl, 8) + ",";
          j += JsonKVNum("tp1", c.tp1, 8) + ",";
          j += JsonKVNum("tp2", c.tp2, 8) + ",";
          j += JsonKVNum("rr2", c_rr2, 4) + ",";
          j += JsonKVNum("effective_rr2", c.effective_rr2, 4) + ",";
          j += JsonKVNum("liquidity_rr", c_liq_rr, 4) + ",";
          j += JsonKVNum("diagnostic_legacy_setup_score", c.setup_score, 4) + ",";
          j += JsonKVNum("heuristic_quality_estimate", c.heuristic_quality_estimate, 4) + ",";
          j += JsonKVStr("setup_family", c.setup_family) + ",";
          j += JsonKVStr("setup_class", c.setup_class) + ",";
          j += JsonKVStr("fvg_execution_class", c.fvg_execution_class) + ",";
          j += JsonKVStr("volatility_profile", c.volatility_profile) + ",";
          j += JsonKVStr("policy_bucket", c.policy_bucket) + ",";
          j += JsonKVBool("runner_trade", c.runner_trade) + ",";
          j += JsonKVBool("runner_downgraded", c.runner_downgraded) + ",";
          j += JsonKVStr("runner_downgrade_reason", c.runner_downgrade_reason) + ",";
          j += JsonKVStr("management_profile", c.management_profile) + ",";
          j += JsonKVStr("target_model", c.target_model) + ",";
          j += JsonKVStr("analytics_key", c.analytics_key) + ",";
          j += JsonKVStr("bucket_policy_action", c.bucket_policy_action) + ",";
          j += JsonKVStr("bucket_policy_reason", c.bucket_policy_reason) + ",";
          j += JsonKVStr("bucket_policy_key", c.bucket_policy_key) + ",";
          j += JsonKVNum("bucket_policy_risk_multiplier", c.bucket_policy_risk_multiplier, 4) + ",";
          j += JsonKVNum("sequence_quality", c.sequence_quality, 4) + ",";
          j += JsonKVNum("htf_alignment_score", c.htf_alignment_score, 4) + ",";
          j += JsonKVNum("adverse_context_score", c.adverse_context_score, 4) + ",";
           j += JsonKVNum("execution_cost_r", c.execution_cost_r, 4) + ",";
           j += JsonKVNum("spread_r", c.spread_r, 6) + ",";
          j += JsonKVNum("slippage_r", c.slippage_r, 4) + ",";
          j += JsonKVNum("commission_r", c.commission_r, 4) + ",";
          j += JsonKVStr("estimated_cost_source", c.estimated_cost_source) + ",";
          j += JsonKVInt("estimated_cost_sample_size", c.estimated_cost_sample_size) + ",";
          j += JsonKVNum("estimated_cost_stressed_per_lot", c.estimated_cost_stressed_per_lot, 6) + ",";
          j += JsonKVStr("commission_model_version", c.commission_model_version) + ",";
          j += JsonKVBool("execution_cost_risk_reduced", c.execution_cost_risk_reduced) + ",";
          j += JsonKVNum("execution_cost_risk_multiplier", c.execution_cost_risk_multiplier, 4) + ",";
          j += JsonKVNum("net_reward_after_cost_r", c.net_reward_after_cost_r, 4) + ",";
          j += JsonKVStr("subtype_policy_action", c.subtype_policy_action) + ",";
          j += JsonKVNum("subtype_policy_penalty", c.subtype_policy_penalty, 4) + ",";
          j += JsonKVNum("setup_floor_penalty", c.setup_floor_penalty, 4) + ",";
          j += JsonKVStr("setup_floor_action", c.setup_floor_action) + ",";
          j += JsonKVStr("portfolio_cluster", c.portfolio_cluster) + ",";
          j += JsonKVStr("usd_exposure_key", c.usd_exposure_key) + ",";
          j += JsonKVNum("portfolio_score", c.portfolio_score, 4) + ",";
          j += JsonKVInt("portfolio_rank", c.portfolio_rank) + ",";
          j += JsonKVStr("scheduler_action", c.scheduler_action) + ",";
          j += JsonKVStr("scheduler_reason", c.scheduler_reason) + ",";
          j += JsonKVStr("stop_floor_reason", c.stop_floor_reason) + ",";
          j += JsonKVNum("stop_floor_distance", c.stop_floor_distance, 8) + ",";
          j += JsonKVNum("stop_noise_band", c.stop_noise_band, 8) + ",";
          j += JsonKVNum("stop_quality_score", c.stop_quality_score, 4) + ",";
          j += JsonKVStr("obstacle_kind", c.obstacle_kind) + ",";
          j += JsonKVNum("obstacle_price", c.obstacle_price, 8) + ",";
          j += JsonKVNum("obstacle_r", c.obstacle_r, 4) + ",";
          j += JsonKVNum("obstacle_distance_r", c.obstacle_distance_r, 4) + ",";
          j += JsonKVBool("target_arbitration_required", c.target_arbitration_required) + ",";
          j += JsonKVNum("liquidity_target_preserved", c.liquidity_target_preserved, 8) + ",";
          j += JsonKVStr("liquidity_target_model", c.liquidity_target_model) + ",";
          j += JsonKVBool("liquidity_target_valid_structurally", c.liquidity_target_valid_structurally) + ",";
          j += JsonKVBool("liquidity_target_blocked_by_obstacle", c.liquidity_target_blocked_by_obstacle) + ",";
          j += JsonKVNum("fallback_tp", c.fallback_tp, 8) + ",";
          j += JsonKVNum("fallback_rr", c.fallback_rr, 4) + ",";
          j += JsonKVStr("fallback_source", c.fallback_source) + ",";
          j += JsonKVNum("capped_before_obstacle_tp", c.capped_before_obstacle_tp, 8) + ",";
          j += JsonKVNum("capped_before_obstacle_rr", c.capped_before_obstacle_rr, 4) + ",";
          j += JsonKVStr("capped_before_obstacle_source", c.capped_before_obstacle_source) + ",";
          j += JsonKVNum("original_planned_tp_before_ai", c.original_planned_tp_before_ai, 8) + ",";
          j += JsonKVNum("original_planned_rr_before_ai", c.original_planned_rr_before_ai, 4) + ",";
          j += "\"target_candidates\":" + _TargetCandidatesJson(c) + ",";
          j += JsonKVNum("fvg_lower", c.fvg.lower, 8) + ",";
          j += JsonKVNum("fvg_upper", c.fvg.upper, 8) + ",";
          j += JsonKVNum("fvg_mid", c.fvg.mid, 8) + ",";
          j += JsonKVBool("fvg_touched", c.fvg.touched) + ",";
          j += JsonKVBool("fvg_mid_mitigated", c.fvg.mid_mitigated) + ",";
          j += JsonKVBool("fvg_fully_filled", c.fvg.fully_filled) + ",";
          j += JsonKVBool("fvg_invalidated", c.fvg.invalidated) + ",";
          j += JsonKVBool("fvg_entry_invalid", c.fvg.entry_invalid) + ",";
          j += JsonKVBool("fvg_structure_invalidated", c.fvg.structure_invalidated) + ",";
          j += JsonKVStr("fvg_mitigation_state", c.fvg.mitigation_state) + ",";
          j += JsonKVStr("fvg_invalidation_reason", c.fvg.invalidation_reason) + ",";
          j += JsonKVBool("fvg_continuation", c.fvg.continuation) + ",";
          j += JsonKVBool("fvg_reversal", c.fvg.reversal) + ",";
          j += JsonKVStr("fvg_context_type", c.fvg.context_type) + ",";
          j += JsonKVNum("fvg_score", c.fvg.score, 4) + ",";
          j += JsonKVStr("fvg_zone_execution_class", c.fvg.execution_class) + ",";
          j += JsonKVNum("origin_score", c.fvg.origin_score, 4) + ",";
          j += JsonKVNum("cleanliness_score", c.fvg.cleanliness_score, 4) + ",";
          j += JsonKVNum("age_score", c.fvg.age_score, 4) + ",";
          j += JsonKVNum("nesting_score", c.fvg.nesting_score, 4) + ",";
          j += JsonKVNum("htf_overlap_score", c.fvg.htf_overlap_score, 4) + ",";
          j += JsonKVNum("retest_score", c.fvg.retest_score, 4) + ",";
          j += JsonKVNum("continuation_score", c.fvg.continuation_score, 4) + ",";
          j += JsonKVNum("reversal_score", c.fvg.reversal_score, 4) + ",";
          j += JsonKVNum("displacement_candle_score", c.fvg.displacement_candle_score, 4) + ",";
          j += JsonKVNum("middle_candle_body_score", c.fvg.middle_candle_body_score, 4) + ",";
          j += JsonKVNum("volume_impulse_score", c.fvg.volume_impulse_score, 4) + ",";
          j += JsonKVNum("gap_width_atr_score", c.fvg.gap_width_atr_score, 4) + ",";
          j += JsonKVNum("manipulation_distance_score", c.fvg.manipulation_distance_score, 4) + ",";
          j += JsonKVNum("premium_discount_score", c.fvg.premium_discount_score, 4) + ",";
          j += JsonKVNum("htf_nesting_score", c.fvg.htf_nesting_score, 4) + ",";
          j += JsonKVNum("freshness_score", c.fvg.freshness_score, 4) + ",";
          j += JsonKVNum("retest_quality_score", c.fvg.retest_quality_score, 4) + ",";
          j += JsonKVNum("opposing_obstruction_score", c.fvg.opposing_obstruction_score, 4) + ",";
          j += JsonKVNum("fvg_raw_width", MathAbs(c.fvg.upper - c.fvg.lower), 8) + ",";
          j += JsonKVNum("fvg_normalized_min_ticks_price", c.fvg.normalized_min_ticks_price, 8) + ",";
          j += JsonKVNum("fvg_normalized_min_spread_price", c.fvg.normalized_min_spread_price, 8) + ",";
          j += JsonKVNum("fvg_normalized_min_atr_price", c.fvg.normalized_min_atr_price, 8) + ",";
          j += JsonKVNum("fvg_normalized_min_session_noise_price", c.fvg.normalized_min_session_noise_price, 8) + ",";
          j += JsonKVNum("fvg_normalized_minimum_price", c.fvg.normalized_minimum_price, 8) + ",";
          j += JsonKVBool("fvg_normalized_shadow_pass", c.fvg.normalized_minimum_shadow_pass) + ",";
          j += JsonKVStr("fvg_normalized_mode", c.fvg.normalized_fvg_mode) + ",";
          j += JsonKVStr("fvg_normalized_asset_class", c.fvg.normalized_fvg_asset_class) + ",";
          j += JsonKVStr("fvg_normalized_policy_version", c.fvg.normalized_fvg_policy_version) + ",";
          j += JsonKVStr("fvg_normalized_policy_source", c.fvg.normalized_fvg_policy_source) + ",";
          j += JsonKVInt("fvg_normalized_sample_size", c.fvg.normalized_fvg_sample_size) + ",";
          j += JsonKVBool("fvg_normalized_asset_class_evidence_sufficient", c.fvg.normalized_fvg_asset_class_evidence_sufficient);
          j += "}";
       }
       j += "],";

       // Root-level aliases keep the Python scorer backward compatible with older payload readers.
       j += JsonKVInt("t_sweep", (int)p.po3.t_sweep) + ",";
       j += JsonKVInt("t_disp", (int)p.po3.t_disp) + ",";
       j += JsonKVInt("t_bos", (int)p.po3.t_bos) + ",";
       j += JsonKVStr("po3_state", p.po3.po3_state) + ",";
       j += JsonKVInt("po3_state_id", (int)p.po3.state) + ",";
       j += JsonKVStr("po3_state_reason", p.po3.po3_state_reason) + ",";
       j += JsonKVStr("sweep_side", p.po3.sweep_side) + ",";
        j += JsonKVStr("structure_type", p.po3.structure_type) + ",";
        j += JsonKVStr("final_setup_class", p.po3.final_setup_class) + ",";
        j += JsonKVStr("po3_scope", p.po3.po3_scope) + ",";
       j += JsonKVNum("dr_high", p.po3.dr_high, 8) + ",";
       j += JsonKVNum("dr_low", p.po3.dr_low, 8) + ",";
       j += JsonKVNum("dr_mid", p.po3.dr_mid, 8) + ",";
       j += JsonKVNum("manip_low", p.po3.manip_low, 8) + ",";
       j += JsonKVNum("manip_high", p.po3.manip_high, 8) + ",";
       j += JsonKVNum("bos_level", p.po3.bos_level, 8) + ",";
       j += JsonKVNum("sweep_strength", p.po3.sweep_strength, 6) + ",";
       j += JsonKVStr("session_name", p.po3.session_name) + ",";
       j += JsonKVStr("po3_session_code", p.po3.session_code) + ",";
       j += JsonKVStr("killzone_name", p.po3.killzone_name) + ",";
       j += JsonKVBool("in_killzone", p.po3.in_killzone) + ",";
       j += JsonKVNum("session_high", p.po3.session_high, 8) + ",";
       j += JsonKVNum("session_low", p.po3.session_low, 8) + ",";
       j += JsonKVNum("asia_high", p.po3.asia_high, 8) + ",";
       j += JsonKVNum("asia_low", p.po3.asia_low, 8) + ",";
       j += JsonKVNum("london_high", p.po3.london_high, 8) + ",";
       j += JsonKVNum("london_low", p.po3.london_low, 8) + ",";
       j += JsonKVNum("newyork_high", p.po3.newyork_high, 8) + ",";
       j += JsonKVNum("newyork_low", p.po3.newyork_low, 8) + ",";
       j += JsonKVNum("prev_day_high", p.po3.prev_day_high, 8) + ",";
       j += JsonKVNum("prev_day_low", p.po3.prev_day_low, 8) + ",";
       j += JsonKVNum("prev_week_high", p.po3.prev_week_high, 8) + ",";
       j += JsonKVNum("prev_week_low", p.po3.prev_week_low, 8) + ",";
       j += JsonKVNum("liquidity_target", p.po3.liquidity_target, 8) + ",";
       j += JsonKVStr("liquidity_kind", p.po3.liquidity_kind) + ",";
       j += JsonKVInt("liquidity_cluster_count", p.po3.liquidity_cluster_count) + ",";
       j += JsonKVBool("htf_mss", p.po3.htf_mss) + ",";
       j += JsonKVBool("htf_choch", p.po3.htf_choch) + ",";
       j += JsonKVBool("ltf_bos", p.po3.ltf_bos) + ",";
       j += JsonKVBool("ltf_mss", p.po3.ltf_mss) + ",";
       j += JsonKVBool("ltf_choch", p.po3.ltf_choch) + ",";
       j += JsonKVNum("atr_pct", p.atr_pct, 6) + ",";
       j += JsonKVNum("trend_strength", p.trend_strength, 6) + ",";
       j += JsonKVNum("trend_slope_pct", p.trend_slope_pct, 8) + ",";
       j += JsonKVNum("adx_value", p.adx_value, 4) + ",";
       j += JsonKVNum("adr_pct", p.adr_pct, 6) + ",";
       j += JsonKVNum("session_vol_ratio", p.session_vol_ratio, 6) + ",";
       j += JsonKVNum("vwap_dist_atr", p.vwap_dist_atr, 6) + ",";
       j += JsonKVNum("compression_score", p.compression_score, 6) + ",";
       j += JsonKVNum("expansion_score", p.expansion_score, 6) + ",";
       j += JsonKVNum("news_risk", p.news_risk, 6) + ",";
       j += JsonKVNum("entry_est", p.entry_est, 8) + ",";
       j += JsonKVStr("configured_stop_model", _StopModelName()) + ",";
       j += JsonKVStr("stop_model", _StopModelName()) + ",";
       j += JsonKVStr("entry_model", p.entry_model) + ",";
       j += JsonKVStr("entry_branch", p.entry_branch) + ",";
       j += JsonKVStr("tp_model", p.tp_model) + ",";
       j += JsonKVStr("target_source", p.target_source) + ",";
       j += JsonKVStr("model_code", p.model_code) + ",";
       j += JsonKVStr("setup_code", p.model_code) + ",";
       j += JsonKVBool("is_mpc", p.model_code == "MPC" || StringFind(p.broker_comment, "MPC-") == 0) + ",";
       j += JsonKVStr("session_code", p.session_code) + ",";
       j += JsonKVStr("killzone_code", p.killzone_code) + ",";
       j += JsonKVStr("broker_comment", p.broker_comment) + ",";
       j += JsonKVNum("sl", p.sl, 8) + ",";
       j += JsonKVNum("tp2", p.tp2, 8) + ",";
       j += JsonKVNum("diagnostic_legacy_setup_score", p.setup_score, 4) + ",";
       j += JsonKVNum("heuristic_quality_estimate", p.heuristic_quality_estimate, 4) + ",";
       j += JsonKVStr("setup_taxonomy_version", p.setup_taxonomy_version) + ",";
       j += JsonKVStr("setup_taxonomy_enum", p.setup_taxonomy_enum) + ",";
       j += JsonKVStr("taxonomy_mapping_source", p.taxonomy_mapping_source) + ",";
       j += JsonKVStr("setup_family", p.setup_family) + ",";
       j += JsonKVStr("setup_class", p.setup_class) + ",";
       j += JsonKVStr("fvg_execution_class", p.fvg_execution_class) + ",";
       j += JsonKVStr("management_profile", p.management_profile) + ",";
       j += JsonKVStr("target_model", p.target_model) + ",";
       j += JsonKVStr("analytics_key", p.analytics_key) + ",";
       j += JsonKVNum("execution_cost_r", p.execution_cost_r, 4) + ",";
       j += JsonKVNum("net_reward_after_cost_r", p.net_reward_after_cost_r, 4) + ",";
       j += JsonKVBool("runner_downgraded", p.runner_downgraded) + ",";
       j += JsonKVNum("liquidity_rr", liquidity_rr, 4) + ",";
       j += JsonKVStr("setup_id", p.setup_id) + ",";
       j += JsonKVStr("fvg_id", p.fvg_id) + ",";
       j += JsonKVStr("candidate_id", p.candidate_id);

       j += "}";
       return j;
   }

   string BuildRequestJson(const TradePlan &p) {
      TradePlan tmp[];
      ArrayResize(tmp, 1);
      tmp[0] = p;
      return BuildRequestJson(tmp);
   }

   bool SendRequestCandidates(TradePlan &plans[], string &out_req_id,
                              const string tester_cache_signature="",
                              const string tester_cache_key="") {
      if(!InpUseAI) return false;
      if(!CanSendNow()) return false;
      if(ArraySize(plans) <= 0) return false;

      out_req_id = plans[0].req_id;
      if(StringLen(out_req_id) == 0) out_req_id = _NowId(plans[0].symbol);
      for(int i=0; i<ArraySize(plans); i++) plans[i].req_id = out_req_id;
      string path = m_bus.ReqDir() + "\\" + out_req_id + ".json";
      string payload = BuildRequestJson(plans, tester_cache_signature, tester_cache_key);
      return m_bus.WriteText(path, payload);
   }

   bool SendRequest(TradePlan &p) {
      TradePlan tmp[];
      ArrayResize(tmp, 1);
      tmp[0] = p;
      string req_id;
      bool ok = SendRequestCandidates(tmp, req_id);
      if(ok) p.req_id = req_id;
      return ok;
   }

   bool TryReadDecision(const string req_id, AiDecision &out) {
      ZeroMemory(out);
      out.ok=false;
      string resp_path = m_bus.RespDir() + "\\" + req_id + ".json";
      string txt;
      if(!m_bus.ReadText(resp_path, txt)) return false;
      string missing = "", invalid = "";
      string document_reason = "";
      if(!JsonValidateDocumentStrict(txt, document_reason)){
         out.ok = true;
         out.allow = false;
         out.raw_allow = false;
         out.model_raw_allow = false;
         out.python_final_allow = false;
         out.mql_final_allow = false;
         out.decision_state = "REJECT";
         out.llm_quality_reject_reason = "ai_quality_schema_incomplete";
         Print("[ai_schema_validation] valid=false decision_schema_version=unknown missing_fields= invalid_fields=", document_reason);
         m_bus.ArchiveTerminal(resp_path, "quarantined");
         return true;
      }
      string rid = "", decision_quality_tier = "", response_quality_alias = "", decision_schema = "";
      _SchemaRequireString(txt, "id", rid, missing, invalid);
      out.response_request_id = rid;
       _SchemaRequireString(txt, "session_id", out.response_session_id, missing, invalid);
       _SchemaRequireString(txt, "request_nonce", out.response_request_nonce, missing, invalid);
       _SchemaRequireString(txt, "request_identity_version", out.request_identity_version, missing, invalid);
      _SchemaRequireString(txt, "request_identity_hash", out.request_identity_hash, missing, invalid);
      _SchemaRequireString(txt, "contract_manifest_hash", out.contract_manifest_hash, missing, invalid);
       double request_sim_time_value = 0.0;
       double request_wall_time_value = 0.0;
       double candidate_count_value = 0.0;
       _SchemaRequireNumber(txt, "request_created_sim_time", request_sim_time_value, 1.0, 2147483647.0, missing, invalid);
       _SchemaRequireNumber(txt, "request_created_wall_time", request_wall_time_value, 1.0, 9.0e15, missing, invalid);
       _SchemaRequireNumber(txt, "candidate_count", candidate_count_value, 1.0, 100000.0, missing, invalid);
       _SchemaRequireArray(txt, "ordered_candidate_identities", out.ordered_candidate_identities_json, missing, invalid);
       out.request_created_sim_time = (datetime)request_sim_time_value;
       out.request_created_wall_time = (long)request_wall_time_value;
       out.response_candidate_count = (int)candidate_count_value;
      if(out.request_identity_version != AI_REQUEST_IDENTITY_VERSION) _AppendSchemaField(invalid, "request_identity_version");
      if(out.contract_manifest_hash != PO3ContractManifestHash()) _AppendSchemaField(invalid, "contract_manifest_hash");
       if(MathAbs(candidate_count_value - MathRound(candidate_count_value)) > 0.000001)
          _AppendSchemaField(invalid, "candidate_count");
       _SchemaRequireString(txt, "workload_mode", out.workload_mode, missing, invalid);
      _SchemaRequireString(txt, "behavior_contract_hash", out.behavior_contract_hash, missing, invalid);
      _SchemaRequireString(txt, "reasoning_configuration", out.reasoning_configuration, missing, invalid);
      _SchemaRequireString(txt, "bucket_prior_hash", out.bucket_prior_hash, missing, invalid, true);
      _SchemaRequireString(txt, "calibration_artifact_id", out.calibration_artifact_id, missing, invalid, true);
      _SchemaRequireString(txt, "response_binding_hash", out.response_binding_hash, missing, invalid);
      _SchemaRequireString(txt, "decision_schema_version", decision_schema, missing, invalid);
      _SchemaRequireString(txt, "decision_quality_tier", decision_quality_tier, missing, invalid);
      bool response_quality_alias_present = JsonGetStringStrict(txt, "response_quality", response_quality_alias);
      if(response_quality_alias_present && response_quality_alias != decision_quality_tier)
         _AppendSchemaField(invalid, "response_quality_alias_mismatch");
      if(rid != req_id) _AppendSchemaField(invalid, "id_mismatch");
      if(out.response_session_id != m_session_id) _AppendSchemaField(invalid, "response_session_id_mismatch");
      if(out.response_request_nonce != _RequestNonce(req_id)) _AppendSchemaField(invalid, "response_nonce_mismatch");
      if(out.workload_mode != _WorkloadMode()) _AppendSchemaField(invalid, "response_workload_mode_mismatch");

      out.decision_schema_version = decision_schema;
      out.decision_quality_tier = decision_quality_tier;
      out.response_quality_alias = response_quality_alias;
      bool trading_quality = (decision_quality_tier == "FULL_STRUCTURED" || decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
      if(!trading_quality){
         out.ok = true;
         out.allow = false;
         out.raw_allow = false;
         out.decision_state = "REJECT";
         out.mandatory_fields_complete = false;
         out.suggested_risk_multiplier = 0.0;
         out.llm_quality_reject_reason = "degraded_ai_response_non_trading";
         out.decision_source = JsonGetString(txt, "decision_source", "degraded_non_trading");
         out.reasons_json = JsonGetString(txt, "reasons", "degraded response cannot authorize trading");
         out.rejection_codes_json = JsonGetArray(txt, "rejection_codes", "[\"degraded_ai_response_non_trading\"]");
         if(StringLen(missing) > 0 || StringLen(invalid) > 0){
            Print("[ai_schema_validation] valid=false decision_schema_version=", decision_schema,
                  " decision_quality_tier=", decision_quality_tier,
                  " error_envelope=true missing_fields=", missing,
                  " invalid_fields=", invalid);
            m_bus.ArchiveTerminal(resp_path, "quarantined");
         } else {
            Print("[ai_schema_validation] valid=true decision_schema_version=", decision_schema,
                  " decision_quality_tier=", decision_quality_tier,
                  " error_envelope=true trading_authority=false identity_bound=true");
            m_bus.ArchiveTerminal(resp_path, "rejected");
         }
         return true;
      }

      if(decision_schema != AI_DECISION_SCHEMA_VERSION) _AppendSchemaField(invalid, "decision_schema_version");
      _SchemaRequireString(txt, "provider_contract_version", out.provider_contract_version, missing, invalid);
      _SchemaRequireString(txt, "provider_mode", out.provider_mode, missing, invalid);
      _SchemaRequireString(txt, "provider_id", out.provider_id, missing, invalid);
      _SchemaRequireString(txt, "endpoint_class", out.endpoint_class, missing, invalid);
      _SchemaRequireString(txt, "endpoint_identity_hash", out.endpoint_identity_hash, missing, invalid);
      _SchemaRequireString(txt, "configured_models_hash", out.configured_models_hash, missing, invalid);
      _SchemaRequireString(txt, "actual_model_id", out.actual_model_id, missing, invalid);
      _SchemaRequireString(txt, "fallback_model", out.fallback_model, missing, invalid, true);
      _SchemaRequireString(txt, "model_fingerprint", out.model_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "evidence_envelope_version", out.evidence_envelope_version, missing, invalid);
      _SchemaRequireString(txt, "family_profile_version", out.family_profile_version, missing, invalid);
      _SchemaRequireString(txt, "memory_schema_version", out.memory_schema_version, missing, invalid);
      _SchemaRequireString(txt, "retrieval_policy_version", out.retrieval_policy_version, missing, invalid);
      _SchemaRequireString(txt, "role_contract_version", out.role_contract_version, missing, invalid);
      _SchemaRequireString(txt, "consensus_resolver_version", out.consensus_resolver_version, missing, invalid);
      _SchemaRequireString(txt, "generation_settings_hash", out.generation_settings_hash, missing, invalid);
      _SchemaRequireString(txt, "input_fingerprint", out.input_fingerprint, missing, invalid);
      _SchemaRequireArray(txt, "retrieved_analogue_ids", out.retrieved_analogue_ids_json, missing, invalid);
      _SchemaRequireString(txt, "historical_evidence_state", out.historical_evidence_state, missing, invalid);
      _SchemaRequireString(txt, "analyst_response_fingerprint", out.analyst_response_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "critic_response_fingerprint", out.critic_response_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "adjudicator_response_fingerprint", out.adjudicator_response_fingerprint, missing, invalid, true);
      _SchemaRequireString(txt, "final_resolver_reason", out.final_resolver_reason, missing, invalid);
      _SchemaRequireString(txt, "provider_health_state", out.provider_health_state, missing, invalid);
      _SchemaRequireObject(txt, "role_latencies", out.role_latencies_json, missing, invalid);
      _SchemaRequireObject(txt, "provider_retry_counts", out.provider_retry_counts_json, missing, invalid);
      _SchemaRequireObject(txt, "provider_usage", out.provider_usage_json, missing, invalid);
      _SchemaRequireNullableNumber(txt, "estimated_context_tokens", out.estimated_context_tokens,
                                   out.estimated_context_tokens_available, 0.0, 1.0e9, missing, invalid);
      _SchemaRequireArray(txt, "unsupported_generation_parameters", out.unsupported_generation_parameters_json, missing, invalid);
      _SchemaRequireObject(txt, "analyst_output", out.analyst_output_json, missing, invalid);
      _SchemaRequireObject(txt, "critic_output", out.critic_output_json, missing, invalid);
      _SchemaRequireObject(txt, "adjudicator_output", out.adjudicator_output_json, missing, invalid);
      if(out.provider_contract_version != AI_PROVIDER_CONTRACT_VERSION) _AppendSchemaField(invalid, "provider_contract_version");
      if(!AiProviderModeIsTradeable(out.provider_mode))
         _AppendSchemaField(invalid, "provider_mode");
      if(out.endpoint_class != "official_remote" && out.endpoint_class != "loopback" && out.endpoint_class != "non_loopback")
         _AppendSchemaField(invalid, "endpoint_class");
      if(out.evidence_envelope_version != AI_EVIDENCE_ENVELOPE_VERSION) _AppendSchemaField(invalid, "evidence_envelope_version");
      if(out.family_profile_version != AI_FAMILY_PROFILE_VERSION) _AppendSchemaField(invalid, "family_profile_version");
      if(out.memory_schema_version != AI_TRADE_MEMORY_SCHEMA_VERSION) _AppendSchemaField(invalid, "memory_schema_version");
      if(out.retrieval_policy_version != AI_RETRIEVAL_POLICY_VERSION) _AppendSchemaField(invalid, "retrieval_policy_version");
      if(out.role_contract_version != AI_ROLE_CONTRACT_VERSION) _AppendSchemaField(invalid, "role_contract_version");
      if(out.consensus_resolver_version != AI_CONSENSUS_RESOLVER_VERSION) _AppendSchemaField(invalid, "consensus_resolver_version");
      _SchemaRequireString(txt, "request_fingerprint", out.request_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "response_fingerprint", out.response_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "full_structured_response_hash", out.full_structured_response_hash, missing, invalid);
      _SchemaRequireString(txt, "hierarchical_prior_artifact_hash", out.hierarchical_prior_artifact_hash, missing, invalid, true);
      _SchemaRequireString(txt, "hierarchical_prior_schema_version", out.hierarchical_prior_schema_version, missing, invalid);
      _SchemaRequireString(txt, "repeatability_schema_version", out.repeatability_schema_version, missing, invalid);
      _SchemaRequireString(txt, "repeatability_status", out.repeatability_status, missing, invalid);
      _SchemaRequireBool(txt, "repeatability_required_live", out.repeatability_required_live, missing, invalid);
      _SchemaRequireString(txt, "repeatability_artifact_state", out.repeatability_artifact_state, missing, invalid);
      _SchemaRequireString(txt, "repeatability_rejection_code", out.repeatability_rejection_code, missing, invalid, true);
      _SchemaRequireBool(txt, "repeatability_score_threshold_authority", out.repeatability_score_threshold_authority, missing, invalid);
      _SchemaRequireBool(txt, "repeatability_trading_eligible", out.repeatability_trading_eligible, missing, invalid);
      _SchemaRequireString(txt, "repeatability_group_key", out.repeatability_group_key, missing, invalid, true);
      _SchemaRequireString(txt, "repeatability_authority_hash", out.repeatability_authority_hash, missing, invalid, true);
      if(out.hierarchical_prior_schema_version != HIERARCHICAL_PRIOR_SCHEMA_VERSION)
         _AppendSchemaField(invalid, "hierarchical_prior_schema_version");
      if(out.repeatability_schema_version != REPEATABILITY_SCHEMA_VERSION)
         _AppendSchemaField(invalid, "repeatability_schema_version");
      if(out.repeatability_status != "REPEATABLE" && out.repeatability_trading_eligible)
         _AppendSchemaField(invalid, "repeatability_trading_eligibility_inconsistent");
      if(out.repeatability_required_live && out.repeatability_status == "REPEATABLE" && !out.repeatability_trading_eligible)
         _AppendSchemaField(invalid, "repeatability_repeatable_not_eligible");
      _SchemaRequireBool(txt, "mandatory_fields_complete", out.mandatory_fields_complete, missing, invalid);
      if(!out.mandatory_fields_complete) _AppendSchemaField(invalid, "mandatory_fields_complete");
      _SchemaRequireObject(txt, "decision_field_authority", out.decision_field_authority_json, missing, invalid);
      string calibrated_authority = JsonGetObject(out.decision_field_authority_json, "calibrated_probability", "");
      string expected_r_authority = JsonGetObject(out.decision_field_authority_json, "expected_net_r", "");
      string llm_quality_authority = JsonGetObject(out.decision_field_authority_json, "llm_quality_score", "");
      string llm_risk_authority = JsonGetObject(out.decision_field_authority_json, "llm_risk_assessments", "");
      string llm_veto_authority = JsonGetObject(out.decision_field_authority_json, "llm_qualitative_veto", "");
      string mql_authority = JsonGetObject(out.decision_field_authority_json, "mql_final_allow", "");
      if(JsonGetString(calibrated_authority, "owner", "") != "statistical" ||
         JsonGetString(calibrated_authority, "authority", "") != "unavailable")
         _AppendSchemaField(invalid, "calibrated_probability_authority");
      if(JsonGetString(expected_r_authority, "owner", "") != "statistical" ||
         JsonGetString(expected_r_authority, "authority", "") != "unavailable")
         _AppendSchemaField(invalid, "expected_net_r_authority");
      if(JsonGetString(llm_quality_authority, "owner", "") != "llm" ||
         JsonGetString(llm_quality_authority, "authority", "") != "diagnostic_only_uncalibrated")
         _AppendSchemaField(invalid, "llm_quality_score_authority");
      if(JsonGetString(llm_risk_authority, "owner", "") != "llm" ||
         JsonGetString(llm_risk_authority, "authority", "") != "diagnostic_only_uncalibrated")
         _AppendSchemaField(invalid, "llm_risk_assessments_authority");
      if(JsonGetString(llm_veto_authority, "owner", "") != "llm" ||
         JsonGetString(llm_veto_authority, "authority", "") != "evidence_backed_enumerated_veto")
         _AppendSchemaField(invalid, "llm_qualitative_veto_authority");
      if(JsonGetString(mql_authority, "owner", "") != "mql_execution" ||
         JsonGetString(mql_authority, "authority", "") != "final")
         _AppendSchemaField(invalid, "mql_final_allow_authority");
      _SchemaRequireArray(txt, "missing_mandatory_fields", out.missing_mandatory_fields_json, missing, invalid);
      _SchemaRequireArray(txt, "invalid_mandatory_fields", out.invalid_mandatory_fields_json, missing, invalid);
      string compact_missing = out.missing_mandatory_fields_json;
      string compact_invalid = out.invalid_mandatory_fields_json;
      StringReplace(compact_missing, " ", ""); StringReplace(compact_missing, "\r", ""); StringReplace(compact_missing, "\n", "");
      StringReplace(compact_invalid, " ", ""); StringReplace(compact_invalid, "\r", ""); StringReplace(compact_invalid, "\n", "");
      if(compact_missing != "[]") _AppendSchemaField(invalid, "missing_mandatory_fields_not_empty");
      if(compact_invalid != "[]") _AppendSchemaField(invalid, "invalid_mandatory_fields_not_empty");

      _SchemaRequireString(txt, "decision_state", out.decision_state, missing, invalid);
      StringToUpper(out.decision_state);
      if(out.decision_state != "APPROVE" && out.decision_state != "REJECT" && out.decision_state != "ABSTAIN")
         _AppendSchemaField(invalid, "decision_state");
      if(out.repeatability_required_live && !out.repeatability_trading_eligible && out.decision_state == "APPROVE")
         _AppendSchemaField(invalid, "repeatability_nontrading_approve");
      _SchemaRequireString(txt, "selected_candidate_id", out.selected_candidate_id, missing, invalid);
      _SchemaRequireString(txt, "selected_candidate_hash", out.selected_candidate_hash, missing, invalid);
      _SchemaRequireString(txt, "request_execution_fingerprint", out.request_execution_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "assessed_execution_fingerprint", out.assessed_execution_fingerprint, missing, invalid);
      _SchemaRequireString(txt, "selected_target_identity", out.selected_target_identity, missing, invalid);
      _SchemaRequireNumber(txt, "selected_target_price", out.selected_target_price, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(txt, "assessed_entry", out.assessed_entry, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(txt, "assessed_sl", out.assessed_sl, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(txt, "assessed_tp1", out.assessed_tp1, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(txt, "assessed_tp2", out.assessed_tp2, -1.0e15, 1.0e15, missing, invalid);

      _SchemaRequireBool(txt, "allow", out.allow, missing, invalid);
      _SchemaRequireBool(txt, "raw_allow", out.raw_allow, missing, invalid);
      _SchemaRequireBool(txt, "model_raw_allow", out.model_raw_allow, missing, invalid);
      _SchemaRequireBool(txt, "python_final_allow", out.python_final_allow, missing, invalid);
      _SchemaRequireNull(txt, "mql_final_allow", missing, invalid);
      out.mql_final_allow = false;
      if(out.model_raw_allow != out.raw_allow) _AppendSchemaField(invalid, "model_raw_allow_alias_mismatch");
      if(out.python_final_allow != out.allow) _AppendSchemaField(invalid, "python_final_allow_alias_mismatch");
      double chosen_value = 0.0;
      _SchemaRequireNumber(txt, "chosen_index", chosen_value, 0.0, 100000.0, missing, invalid);
      if(MathAbs(chosen_value - MathRound(chosen_value)) > 0.000001) _AppendSchemaField(invalid, "chosen_index");
      out.chosen_index = (int)MathRound(chosen_value);
      _SchemaRequireNumber(txt, "rule_score", out.rule_score, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(txt, "llm_quality_score", out.llm_quality_score, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(txt, "blended_legacy_score", out.blended_legacy_score, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(txt, "legacy_agreement_confidence", out.legacy_agreement_confidence, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "llm_self_reported_confidence", out.llm_self_reported_confidence, 0.0, 1.0, missing, invalid);
      out.score = out.llm_quality_score;
      out.confidence = out.llm_self_reported_confidence;
      _SchemaRequireNull(txt, "calibrated_win_probability", missing, invalid);
      _SchemaRequireNull(txt, "expected_net_r", missing, invalid);
      _SchemaRequireNull(txt, "oos_predicted_probability", missing, invalid);
      _SchemaRequireBool(txt, "calibration_available", out.calibration_available, missing, invalid);
      if(out.calibration_available) _AppendSchemaField(invalid, "calibration_available");
      string calibration_text = "";
      double calibration_sample = 0.0;
      _SchemaRequireString(txt, "calibration_bucket", calibration_text, missing, invalid, true); out.calibration_bucket = calibration_text;
      _SchemaRequireNumber(txt, "calibration_sample_size", calibration_sample, 0.0, 1.0e9, missing, invalid); out.calibration_sample_size = (int)MathRound(calibration_sample);
      if(MathAbs(calibration_sample - MathRound(calibration_sample)) > 0.000001 || MathRound(calibration_sample) != 0.0)
         _AppendSchemaField(invalid, "calibration_sample_size");
      _SchemaRequireNull(txt, "calibration_lower_bound", missing, invalid);
      _SchemaRequireNull(txt, "calibration_upper_bound", missing, invalid);
      _SchemaRequireString(txt, "calibration_model_version", out.calibration_model_version, missing, invalid, true);
      _SchemaRequireString(txt, "calibration_data_window_start", out.calibration_data_window_start, missing, invalid, true);
      _SchemaRequireString(txt, "calibration_data_window_end", out.calibration_data_window_end, missing, invalid, true);
      if(StringLen(out.calibration_bucket) > 0) _AppendSchemaField(invalid, "calibration_bucket");
      if(StringLen(out.calibration_model_version) > 0) _AppendSchemaField(invalid, "calibration_model_version");
      if(StringLen(out.calibration_data_window_start) > 0) _AppendSchemaField(invalid, "calibration_data_window_start");
      if(StringLen(out.calibration_data_window_end) > 0) _AppendSchemaField(invalid, "calibration_data_window_end");

      _SchemaRequireString(txt, "reasons", out.reasons_json, missing, invalid, true);
      _SchemaRequireString(txt, "decision_source", out.decision_source, missing, invalid);
      _SchemaRequireString(txt, "decision_id", out.decision_id, missing, invalid);
      _SchemaRequireArray(txt, "rejection_codes", out.rejection_codes_json, missing, invalid);
      _SchemaRequireString(txt, "narrative_state", out.narrative_state, missing, invalid, true);
      _SchemaRequireArray(txt, "invalidation_risks", out.invalidation_risks_json, missing, invalid);
      _SchemaRequireArray(txt, "missing_confirmations", out.missing_confirmations_json, missing, invalid);
      _SchemaRequireNumber(txt, "suggested_risk_multiplier", out.suggested_risk_multiplier, 0.0, 1.0, missing, invalid);
      _SchemaRequireString(txt, "model_version", out.model_version, missing, invalid);

      _SchemaRequireNumber(txt, "structure_quality_score", out.structure_quality_score, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(txt, "entry_timing_score", out.entry_timing_score, 0.0, 10.0, missing, invalid);
      _SchemaRequireNumber(txt, "follow_through_probability", out.follow_through_probability, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "invalidation_risk", out.invalidation_risk, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "chop_risk", out.chop_risk, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "cost_risk", out.cost_risk, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "symbol_bucket_risk", out.symbol_bucket_risk, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "session_bucket_risk", out.session_bucket_risk, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "post_entry_failure_risk", out.post_entry_failure_risk, 0.0, 1.0, missing, invalid);
      _SchemaRequireNumber(txt, "final_trade_expectancy_score", out.final_trade_expectancy_score, 0.0, 10.0, missing, invalid);
      _SchemaRequireString(txt, "llm_numeric_diagnostics_authority", out.llm_numeric_diagnostics_authority, missing, invalid);
      if(out.llm_numeric_diagnostics_authority != "uncalibrated_diagnostic_only_no_direct_trade_authority")
         _AppendSchemaField(invalid, "llm_numeric_diagnostics_authority");
      _SchemaRequireBool(txt, "veto_enabled", out.veto_enabled, missing, invalid);
      _SchemaRequireString(txt, "veto_code", out.veto_code, missing, invalid, true);
      _SchemaRequireArray(txt, "veto_evidence_fields", out.veto_evidence_fields_json, missing, invalid);
      _SchemaRequireString(txt, "veto_reason", out.veto_reason, missing, invalid, true);
      string veto_obj = "";
      if(_SchemaRequireObject(txt, "veto", veto_obj, missing, invalid)){
         bool nested_veto = false; string nested_code = "", nested_evidence = "", nested_reason = "";
         _SchemaRequireBool(veto_obj, "enabled", nested_veto, missing, invalid);
         _SchemaRequireString(veto_obj, "code", nested_code, missing, invalid, true);
         _SchemaRequireArray(veto_obj, "evidence_fields", nested_evidence, missing, invalid);
         _SchemaRequireString(veto_obj, "reason", nested_reason, missing, invalid, true);
         if(nested_veto != out.veto_enabled || nested_code != out.veto_code ||
            nested_evidence != out.veto_evidence_fields_json || nested_reason != out.veto_reason)
            _AppendSchemaField(invalid, "veto_mismatch");
      }
      string compact_veto_evidence = out.veto_evidence_fields_json;
      StringReplace(compact_veto_evidence, " ", "");
      StringReplace(compact_veto_evidence, "\r", "");
      StringReplace(compact_veto_evidence, "\n", "");
      if(out.veto_enabled){
         if(!_AllowedQualitativeVetoCode(out.veto_code)) _AppendSchemaField(invalid, "veto_code");
         if(compact_veto_evidence == "[]") _AppendSchemaField(invalid, "veto_evidence_fields");
         if(StringLen(out.veto_reason) == 0) _AppendSchemaField(invalid, "veto_reason");
      } else if(StringLen(out.veto_code) > 0 || compact_veto_evidence != "[]" || StringLen(out.veto_reason) > 0){
         _AppendSchemaField(invalid, "veto_disabled_payload");
      }
      out.veto_fields_present = true;
      _SchemaRequireString(txt, "bucket_prior_override_justification", out.bucket_prior_override_justification, missing, invalid, true);

      _SchemaRequireString(txt, "chosen_target_model", out.chosen_target_model, missing, invalid);
      _SchemaRequireNumber(txt, "chosen_tp1", out.chosen_tp1, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(txt, "chosen_tp2", out.chosen_tp2, -1.0e15, 1.0e15, missing, invalid);
      _SchemaRequireNumber(txt, "chosen_rr1", out.chosen_rr1, 0.0, 1.0e6, missing, invalid);
      _SchemaRequireNumber(txt, "chosen_rr2", out.chosen_rr2, 0.0, 1.0e6, missing, invalid);
      _SchemaRequireArray(txt, "rejected_target_models", out.rejected_target_models_json, missing, invalid);
      _SchemaRequireString(txt, "target_blocker_kind", out.target_blocker_kind, missing, invalid, true);
      _SchemaRequireNumber(txt, "target_blocker_severity", out.target_blocker_severity, -1.0, 10.0, missing, invalid);
      _SchemaRequireString(txt, "target_blocker_class", out.target_blocker_class, missing, invalid);
      _SchemaRequireBool(txt, "target_blocker_is_trade_killer", out.target_blocker_is_trade_killer, missing, invalid);
      _SchemaRequireString(txt, "target_decision_reason", out.target_decision_reason, missing, invalid);
      _SchemaRequireBool(txt, "target_blocker_severity_present", out.target_blocker_severity_present, missing, invalid);
      _SchemaRequireBool(txt, "target_blocker_class_present", out.target_blocker_class_present, missing, invalid);
      _SchemaRequireBool(txt, "target_blocker_is_trade_killer_present", out.target_blocker_is_trade_killer_present, missing, invalid);
      _SchemaRequireBool(txt, "target_decision_reason_present", out.target_decision_reason_present, missing, invalid);
      _SchemaRequireString(txt, "why_not_liquidity_target", out.why_not_liquidity_target, missing, invalid, true);
      _SchemaRequireString(txt, "why_not_partial_before_obstacle", out.why_not_partial_before_obstacle, missing, invalid, true);
      _SchemaRequireString(txt, "why_not_capped_before_obstacle", out.why_not_capped_before_obstacle, missing, invalid, true);
      _SchemaRequireString(txt, "why_not_synthetic_fallback", out.why_not_synthetic_fallback, missing, invalid, true);
      _SchemaRequireString(txt, "target_arbitration_schema_version", out.target_arbitration_schema_version, missing, invalid);
      _SchemaRequireString(txt, "prompt_contract_version", out.prompt_contract_version, missing, invalid);
      _SchemaRequireObject(txt, "target_comparison", out.target_comparison_json, missing, invalid);
      if(out.target_arbitration_schema_version != AI_TARGET_ARBITRATION_SCHEMA_VERSION) _AppendSchemaField(invalid, "target_arbitration_schema_version");
      if(out.prompt_contract_version != AI_PROMPT_CONTRACT_VERSION) _AppendSchemaField(invalid, "prompt_contract_version");
      _ValidateStrictTargetArbitration(txt, missing, invalid);
      string top_arbitration = "";
      if(JsonGetObjectStrict(txt, "target_arbitration", top_arbitration)){
         bool arb_required = false, arb_killer = false;
         string arb_model = "", arb_kind = "", arb_class = "", arb_reason = "";
         string arb_why_liq = "", arb_why_partial = "", arb_why_capped = "", arb_why_synth = "";
         double arb_tp1 = 0.0, arb_tp2 = 0.0, arb_rr1 = 0.0, arb_rr2 = 0.0, arb_severity = -1.0;
         JsonGetBoolStrict(top_arbitration, "arbitration_required", arb_required);
         JsonGetStringStrict(top_arbitration, "chosen_target_model", arb_model);
         JsonGetNumberStrict(top_arbitration, "chosen_tp1", arb_tp1);
         JsonGetNumberStrict(top_arbitration, "chosen_tp2", arb_tp2);
         JsonGetNumberStrict(top_arbitration, "chosen_rr1", arb_rr1);
         JsonGetNumberStrict(top_arbitration, "chosen_rr2", arb_rr2);
         JsonGetStringStrict(top_arbitration, "blocker_kind", arb_kind);
         JsonGetNumberStrict(top_arbitration, "blocker_severity", arb_severity);
         JsonGetStringStrict(top_arbitration, "blocker_class", arb_class);
         JsonGetBoolStrict(top_arbitration, "blocker_is_trade_killer", arb_killer);
         JsonGetStringStrict(top_arbitration, "target_decision_reason", arb_reason);
         JsonGetStringStrict(top_arbitration, "why_not_liquidity_target", arb_why_liq);
         JsonGetStringStrict(top_arbitration, "why_not_partial_before_obstacle", arb_why_partial);
         JsonGetStringStrict(top_arbitration, "why_not_capped_before_obstacle", arb_why_capped);
         JsonGetStringStrict(top_arbitration, "why_not_synthetic_fallback", arb_why_synth);
         out.target_arbitration_required = arb_required;
         if(arb_model != out.chosen_target_model) _AppendSchemaField(invalid, "chosen_target_model_arbitration_mismatch");
         if(MathAbs(arb_tp1 - out.chosen_tp1) > 0.00000001) _AppendSchemaField(invalid, "chosen_tp1_arbitration_mismatch");
         if(MathAbs(arb_tp2 - out.chosen_tp2) > 0.00000001) _AppendSchemaField(invalid, "chosen_tp2_arbitration_mismatch");
         if(MathAbs(arb_rr1 - out.chosen_rr1) > 0.00000001) _AppendSchemaField(invalid, "chosen_rr1_arbitration_mismatch");
         if(MathAbs(arb_rr2 - out.chosen_rr2) > 0.00000001) _AppendSchemaField(invalid, "chosen_rr2_arbitration_mismatch");
         if(arb_kind != out.target_blocker_kind) _AppendSchemaField(invalid, "target_blocker_kind_arbitration_mismatch");
         if(MathAbs(arb_severity - out.target_blocker_severity) > 0.00000001) _AppendSchemaField(invalid, "target_blocker_severity_arbitration_mismatch");
         if(arb_class != out.target_blocker_class) _AppendSchemaField(invalid, "target_blocker_class_arbitration_mismatch");
         if(arb_killer != out.target_blocker_is_trade_killer) _AppendSchemaField(invalid, "target_blocker_killer_arbitration_mismatch");
         if(arb_reason != out.target_decision_reason) _AppendSchemaField(invalid, "target_decision_reason_arbitration_mismatch");
         if(arb_why_liq != out.why_not_liquidity_target) _AppendSchemaField(invalid, "why_not_liquidity_target_mismatch");
         if(arb_why_partial != out.why_not_partial_before_obstacle) _AppendSchemaField(invalid, "why_not_partial_before_obstacle_mismatch");
         if(arb_why_capped != out.why_not_capped_before_obstacle) _AppendSchemaField(invalid, "why_not_capped_before_obstacle_mismatch");
         if(arb_why_synth != out.why_not_synthetic_fallback) _AppendSchemaField(invalid, "why_not_synthetic_fallback_mismatch");
      }

       _SchemaRequireArray(txt, "candidate_assessments", out.candidate_assessments_json, missing, invalid);
       int assessment_count = JsonArrayObjectCount(out.candidate_assessments_json);
       if(assessment_count <= 0) _AppendSchemaField(invalid, "candidate_assessments_empty");
       if(assessment_count != out.response_candidate_count) _AppendSchemaField(invalid, "candidate_count_mismatch");
      string hashes[];
      ArrayResize(hashes, 0);
      int selected_matches = 0;
      string selected_assessment = "";
      for(int i=0; i<assessment_count; i++){
         string item = "", item_id = "", item_hash = "", item_fp = "";
         if(!JsonArrayGetObject(out.candidate_assessments_json, i, item)){
            _AppendSchemaField(invalid, "candidate_assessment_parse");
            continue;
          }
          _ValidateStrictCandidateAssessment(item, item_id, item_hash, item_fp, missing, invalid);
          string item_request_id = "", item_identity_hash = "", item_provider_id = "", item_model_id = "";
          JsonGetStringStrict(item, "request_id", item_request_id);
          JsonGetStringStrict(item, "request_identity_hash", item_identity_hash);
          JsonGetStringStrict(item, "provider_id", item_provider_id);
          JsonGetStringStrict(item, "model_id", item_model_id);
          if(item_request_id != rid) _AppendSchemaField(invalid, "candidate_request_id_mismatch");
          if(item_identity_hash != out.request_identity_hash) _AppendSchemaField(invalid, "candidate_request_identity_hash_mismatch");
          if(item_provider_id != out.provider_id) _AppendSchemaField(invalid, "candidate_provider_id_mismatch");
          if(item_model_id != out.actual_model_id && item_model_id != JsonGetString(txt, "model_returned", ""))
             _AppendSchemaField(invalid, "candidate_model_id_mismatch");
         for(int h=0; h<ArraySize(hashes); h++){
            if(hashes[h] == item_hash) _AppendSchemaField(invalid, "candidate_hash_duplicate");
         }
         int hn = ArraySize(hashes); ArrayResize(hashes, hn + 1); hashes[hn] = item_hash;
         if(item_hash == out.selected_candidate_hash){
            selected_matches++;
            selected_assessment = item;
            if(item_id != out.selected_candidate_id) _AppendSchemaField(invalid, "selected_candidate_id_mismatch");
            if(item_fp != out.assessed_execution_fingerprint) _AppendSchemaField(invalid, "assessed_execution_fingerprint_mismatch");
            string item_request_fp = "";
            JsonGetStringStrict(item, "request_execution_fingerprint", item_request_fp);
            if(item_request_fp != out.request_execution_fingerprint) _AppendSchemaField(invalid, "request_execution_fingerprint_mismatch");
         }
      }
      if(selected_matches != 1) _AppendSchemaField(invalid, "selected_candidate_hash_match_count");
      if(selected_matches == 1){
         double item_index = -1.0, item_number = 0.0;
         string item_state = "", item_target = "", item_text = "";
         bool item_raw_allow = false;
         JsonGetNumberStrict(selected_assessment, "candidate_index", item_index);
         JsonGetStringStrict(selected_assessment, "decision_state", item_state);
         StringToUpper(item_state);
         JsonGetBoolStrict(selected_assessment, "raw_allow", item_raw_allow);
         JsonGetStringStrict(selected_assessment, "selected_target_identity", item_target);
         if((int)MathRound(item_index) != out.chosen_index) _AppendSchemaField(invalid, "chosen_index_assessment_mismatch");
         if(item_state != out.decision_state) _AppendSchemaField(invalid, "decision_state_assessment_mismatch");
         if(item_raw_allow != out.raw_allow) _AppendSchemaField(invalid, "raw_allow_assessment_mismatch");
         if(item_target != out.selected_target_identity) _AppendSchemaField(invalid, "selected_target_identity_mismatch");
         JsonGetNumberStrict(selected_assessment, "selected_target_price", item_number);
         if(MathAbs(item_number - out.selected_target_price) > 0.00000001) _AppendSchemaField(invalid, "selected_target_price_mismatch");
         JsonGetNumberStrict(selected_assessment, "suggested_risk_multiplier", item_number);
         if(MathAbs(item_number - out.suggested_risk_multiplier) > 0.00000001) _AppendSchemaField(invalid, "risk_multiplier_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "rule_score", item_number);
         if(MathAbs(item_number - out.rule_score) > 0.00000001) _AppendSchemaField(invalid, "rule_score_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "llm_quality_score", item_number);
         if(MathAbs(item_number - out.llm_quality_score) > 0.00000001) _AppendSchemaField(invalid, "llm_quality_score_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "blended_legacy_score", item_number);
         if(MathAbs(item_number - out.blended_legacy_score) > 0.00000001) _AppendSchemaField(invalid, "blended_legacy_score_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "legacy_agreement_confidence", item_number);
         if(MathAbs(item_number - out.legacy_agreement_confidence) > 0.00000001) _AppendSchemaField(invalid, "legacy_agreement_confidence_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "llm_self_reported_confidence", item_number);
         if(MathAbs(item_number - out.llm_self_reported_confidence) > 0.00000001) _AppendSchemaField(invalid, "llm_self_reported_confidence_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "structure_quality_score", item_number);
         if(MathAbs(item_number - out.structure_quality_score) > 0.00000001) _AppendSchemaField(invalid, "structure_quality_score_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "entry_timing_score", item_number);
         if(MathAbs(item_number - out.entry_timing_score) > 0.00000001) _AppendSchemaField(invalid, "entry_timing_score_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "follow_through_probability", item_number);
         if(MathAbs(item_number - out.follow_through_probability) > 0.00000001) _AppendSchemaField(invalid, "follow_through_probability_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "invalidation_risk", item_number);
         if(MathAbs(item_number - out.invalidation_risk) > 0.00000001) _AppendSchemaField(invalid, "invalidation_risk_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "chop_risk", item_number);
         if(MathAbs(item_number - out.chop_risk) > 0.00000001) _AppendSchemaField(invalid, "chop_risk_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "cost_risk", item_number);
         if(MathAbs(item_number - out.cost_risk) > 0.00000001) _AppendSchemaField(invalid, "cost_risk_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "symbol_bucket_risk", item_number);
         if(MathAbs(item_number - out.symbol_bucket_risk) > 0.00000001) _AppendSchemaField(invalid, "symbol_bucket_risk_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "session_bucket_risk", item_number);
         if(MathAbs(item_number - out.session_bucket_risk) > 0.00000001) _AppendSchemaField(invalid, "session_bucket_risk_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "post_entry_failure_risk", item_number);
         if(MathAbs(item_number - out.post_entry_failure_risk) > 0.00000001) _AppendSchemaField(invalid, "post_entry_failure_risk_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "final_trade_expectancy_score", item_number);
         if(MathAbs(item_number - out.final_trade_expectancy_score) > 0.00000001) _AppendSchemaField(invalid, "final_trade_expectancy_score_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "entry", item_number);
         if(MathAbs(item_number - out.assessed_entry) > 0.00000001) _AppendSchemaField(invalid, "assessed_entry_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "sl", item_number);
         if(MathAbs(item_number - out.assessed_sl) > 0.00000001) _AppendSchemaField(invalid, "assessed_sl_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "tp1", item_number);
         if(MathAbs(item_number - out.assessed_tp1) > 0.00000001) _AppendSchemaField(invalid, "assessed_tp1_assessment_mismatch");
         JsonGetNumberStrict(selected_assessment, "tp2", item_number);
         if(MathAbs(item_number - out.assessed_tp2) > 0.00000001) _AppendSchemaField(invalid, "assessed_tp2_assessment_mismatch");
         JsonGetStringStrict(selected_assessment, "model_version", item_text);
         if(item_text != out.model_version) _AppendSchemaField(invalid, "model_version_assessment_mismatch");
         string item_veto = "", item_veto_code = "", item_veto_evidence = "", item_veto_reason = ""; bool item_veto_enabled = false;
         if(JsonGetObjectStrict(selected_assessment, "veto", item_veto)){
            JsonGetBoolStrict(item_veto, "enabled", item_veto_enabled);
            JsonGetStringStrict(item_veto, "code", item_veto_code);
            item_veto_evidence = JsonGetArray(item_veto, "evidence_fields", "[]");
            JsonGetStringStrict(item_veto, "reason", item_veto_reason);
            if(item_veto_enabled != out.veto_enabled || item_veto_code != out.veto_code ||
               item_veto_evidence != out.veto_evidence_fields_json || item_veto_reason != out.veto_reason)
               _AppendSchemaField(invalid, "veto_assessment_mismatch");
         }
         string item_arbitration = "", item_chosen_model = "", item_blocker_kind = "", item_blocker_class = "", item_target_reason = "";
         bool item_arbitration_required = false, item_blocker_killer = false;
         double item_tp1 = 0.0, item_tp2 = 0.0, item_rr1 = 0.0, item_rr2 = 0.0, item_severity = -1.0;
         if(JsonGetObjectStrict(selected_assessment, "target_arbitration", item_arbitration)){
            JsonGetBoolStrict(item_arbitration, "arbitration_required", item_arbitration_required);
            JsonGetStringStrict(item_arbitration, "chosen_target_model", item_chosen_model);
            JsonGetNumberStrict(item_arbitration, "chosen_tp1", item_tp1);
            JsonGetNumberStrict(item_arbitration, "chosen_tp2", item_tp2);
            JsonGetNumberStrict(item_arbitration, "chosen_rr1", item_rr1);
            JsonGetNumberStrict(item_arbitration, "chosen_rr2", item_rr2);
            JsonGetStringStrict(item_arbitration, "blocker_kind", item_blocker_kind);
            JsonGetNumberStrict(item_arbitration, "blocker_severity", item_severity);
            JsonGetStringStrict(item_arbitration, "blocker_class", item_blocker_class);
            JsonGetBoolStrict(item_arbitration, "blocker_is_trade_killer", item_blocker_killer);
            JsonGetStringStrict(item_arbitration, "target_decision_reason", item_target_reason);
            if(item_arbitration_required != out.target_arbitration_required ||
               item_chosen_model != out.chosen_target_model ||
               MathAbs(item_tp1 - out.chosen_tp1) > 0.00000001 ||
               MathAbs(item_tp2 - out.chosen_tp2) > 0.00000001 ||
               MathAbs(item_rr1 - out.chosen_rr1) > 0.00000001 ||
               MathAbs(item_rr2 - out.chosen_rr2) > 0.00000001 ||
               item_blocker_kind != out.target_blocker_kind ||
               MathAbs(item_severity - out.target_blocker_severity) > 0.00000001 ||
               item_blocker_class != out.target_blocker_class ||
               item_blocker_killer != out.target_blocker_is_trade_killer ||
               item_target_reason != out.target_decision_reason)
               _AppendSchemaField(invalid, "target_arbitration_assessment_mismatch");
         }
      }

      bool approve_consistent = (out.decision_state == "APPROVE" && out.allow && out.raw_allow && !out.veto_enabled && out.suggested_risk_multiplier > 0.0);
      if(out.allow && !approve_consistent) _AppendSchemaField(invalid, "approval_state_inconsistent");
      if(out.decision_state == "ABSTAIN" && out.allow) _AppendSchemaField(invalid, "abstain_allow_true");
      if(out.decision_state == "APPROVE" && (out.selected_target_price <= 0.0 || out.assessed_entry <= 0.0 || out.assessed_sl <= 0.0 || out.assessed_tp2 <= 0.0))
         _AppendSchemaField(invalid, "approve_plan_prices_invalid");
      if(out.decision_state == "APPROVE"){
         double risk_distance = MathAbs(out.assessed_entry - out.assessed_sl);
         bool is_buy = (out.assessed_sl < out.assessed_entry);
         double reward2 = (is_buy ? out.assessed_tp2 - out.assessed_entry
                                  : out.assessed_entry - out.assessed_tp2);
         double deterministic_rr2 = (risk_distance > 0.0 ? reward2 / risk_distance : 0.0);
         if(risk_distance <= 0.0 || deterministic_rr2 <= 0.0 || out.chosen_rr2 <= 0.0 ||
            MathAbs(deterministic_rr2 - out.chosen_rr2) > 0.00001)
            _AppendSchemaField(invalid, "approve_rr2_mismatch");
         if(out.assessed_tp1 > 0.0){
            double reward1 = (is_buy ? out.assessed_tp1 - out.assessed_entry
                                     : out.assessed_entry - out.assessed_tp1);
            double deterministic_rr1 = (risk_distance > 0.0 ? reward1 / risk_distance : 0.0);
            if(deterministic_rr1 <= 0.0 || out.chosen_rr1 <= 0.0 ||
               MathAbs(deterministic_rr1 - out.chosen_rr1) > 0.00001)
               _AppendSchemaField(invalid, "approve_rr1_mismatch");
         }
      }

       string binding_material = rid + "|" + out.response_session_id + "|" + out.response_request_nonce
                                 + "|" + out.request_identity_hash
                                 + "|" + out.contract_manifest_hash
                                 + "|" + decision_schema + "|" + decision_quality_tier
                                + "|" + out.provider_mode
                                + "|" + out.provider_id
                                + "|" + out.actual_model_id
                                + "|" + out.model_fingerprint
                                + "|" + out.generation_settings_hash
                                + "|" + out.decision_state
                                + "|" + (out.model_raw_allow ? "1" : "0")
                                + "|" + (out.python_final_allow ? "1" : "0")
                                + "|" + out.selected_candidate_id + "|" + out.selected_candidate_hash
                                + "|" + out.assessed_execution_fingerprint + "|" + out.selected_target_identity
                                + "|" + DoubleToString(out.selected_target_price, 8)
                                + "|" + DoubleToString(out.llm_quality_score, 6)
                                + "|" + DoubleToString(out.suggested_risk_multiplier, 6);
      string expected_binding_hash = IntegerToString((int)(_Fnv1a(binding_material) % 2147483647));
      if(out.response_binding_hash != expected_binding_hash) _AppendSchemaField(invalid, "response_hash_mismatch");

      if(StringLen(missing) > 0 || StringLen(invalid) > 0){
         out.ok = true;
         out.allow = false;
         out.raw_allow = false;
         out.mandatory_fields_complete = false;
         out.suggested_risk_multiplier = 0.0;
         out.llm_quality_reject_reason = "ai_quality_schema_incomplete";
         out.decision_quality_tier = "DEGRADED_NON_TRADING";
         out.response_quality_alias = "DEGRADED_NON_TRADING";
         out.decision_state = "REJECT";
         out.missing_mandatory_fields_json = missing;
         out.invalid_mandatory_fields_json = invalid;
         Print("[ai_schema_validation] valid=false decision_schema_version=", decision_schema,
               " missing_fields=", missing, " invalid_fields=", invalid);
         m_bus.ArchiveTerminal(resp_path, "quarantined");
         return true;
      }

      out.ok = true;
      Print("[ai_schema_validation] valid=true decision_schema_version=", decision_schema,
            " decision_quality_tier=", decision_quality_tier,
            " candidate_assessments=", assessment_count);
      m_bus.ArchiveTerminal(resp_path, out.allow ? "completed" : "rejected");
      return true;
   }

   void NotifyFailureBackoff() {
      // throttle retries if the python bridge is down or unresponsive
      m_next_allowed = TimeLocal() + InpAiRetryBackoffMin*60;
   }
};

#endif
