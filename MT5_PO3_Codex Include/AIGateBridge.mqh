//+------------------------------------------------------------------+
//| AIGateBridge.mqh - JSON request/response via Common folder         |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_AIGATEBRIDGE_MQH__
#define __PO3_AIGATE_AIGATEBRIDGE_MQH__
#include "FileBus.mqh"
#include "Types.mqh"
#include "Config.mqh"
#include "JsonLite.mqh"

class CAIGateBridge {
private:
   CFileBus *m_bus;
   datetime m_next_allowed; // backoff if AI failed recently

   string _NowId(const string symbol) {
      // unique-ish id
      uint r = (uint)MathRand();
      return IntegerToString((int)TimeLocal()) + "_" + symbol + "_" + IntegerToString((int)r);
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
      bool tp1_before_obstacle_possible = (InpAllowPartialBeforeObstacle && capped_tp > 0.0 && capped_rr > 0.0);

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
      j += JsonKVNum("setup_quality", p.setup_score, 4) + ",";
      j += JsonKVNum("htf_alignment_score", p.htf_alignment_score, 4) + ",";
      j += JsonKVStr("session", p.po3.session_name) + ",";
      j += JsonKVStr("killzone", p.killzone_code) + ",";
      j += JsonKVBool("is_htf_obstacle", StringFind(p.obstacle_tf, "htf") >= 0) + ",";
      j += JsonKVBool("is_ltf_obstacle", (StringFind(p.obstacle_tf, "ltf") >= 0 || p.obstacle_tf == "entry_tf"));
      j += "},";
      j += "\"liquidity_target\":{";
      j += JsonKVBool("available", liquidity_tp > 0.0) + ",";
      j += JsonKVStr("model", liquidity_model) + ",";
      j += JsonKVNum("tp2", liquidity_tp, 8) + ",";
      j += JsonKVNum("rr2", liquidity_rr, 4) + ",";
      j += JsonKVBool("valid_structurally", p.liquidity_target_valid_structurally) + ",";
      j += JsonKVBool("blocked_by_obstacle", p.liquidity_target_blocked_by_obstacle);
      j += "},";
      j += "\"capped_before_obstacle\":{";
      j += JsonKVBool("available", capped_tp > 0.0) + ",";
      j += JsonKVStr("model", (StringLen(p.capped_before_obstacle_source) > 0 ? p.capped_before_obstacle_source : "capped_before_obstacle")) + ",";
      j += JsonKVNum("tp2", capped_tp, 8) + ",";
      j += JsonKVNum("rr2", capped_rr, 4) + ",";
      j += JsonKVBool("partial_allowed", InpAllowPartialBeforeObstacle);
      j += "},";
      j += "\"synthetic_rr_fallback\":{";
      j += JsonKVBool("available", fallback_tp > 0.0) + ",";
      j += JsonKVStr("model", (StringLen(p.fallback_source) > 0 ? p.fallback_source : "synthetic_rr_fallback")) + ",";
      j += JsonKVNum("tp2", fallback_tp, 8) + ",";
      j += JsonKVNum("rr2", fallback_rr, 4) + ",";
      j += JsonKVNum("effective_rr2", effective_fallback_rr, 4) + ",";
      j += JsonKVBool("crosses_obstacle", StringFind(p.obstacle_kind, "crossed") >= 0);
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

   string RuntimeInputHash() const {
      string s = ENGINE_INPUT_SCHEMA + "|";
      s += IntegerToString((int)InpStrategyMode) + "|" + InpStrategyPreset + "|";
      s += IntegerToString((int)InpStopModel) + "|" + IntegerToString((int)PO3EffectiveHTF()) + "|" + IntegerToString((int)PO3EffectiveEntryTF()) + "|";
      s += (InpTradeOnlyKillzones ? "1" : "0") + "|" + IntegerToString(InpLondonKillzoneStartHour) + ":" + IntegerToString(InpLondonKillzoneStartMinute) + "-" + IntegerToString(InpLondonKillzoneEndHour) + ":" + IntegerToString(InpLondonKillzoneEndMinute) + "|";
      s += IntegerToString(InpNewYorkKillzoneStartHour) + ":" + IntegerToString(InpNewYorkKillzoneStartMinute) + "-" + IntegerToString(InpNewYorkKillzoneEndHour) + ":" + IntegerToString(InpNewYorkKillzoneEndMinute) + "|";
      s += (InpEnableAsiaKillzone ? "1" : "0") + "|" + IntegerToString(InpAsiaKillzoneStartHour) + ":" + IntegerToString(InpAsiaKillzoneStartMinute) + "-" + IntegerToString(InpAsiaKillzoneEndHour) + ":" + IntegerToString(InpAsiaKillzoneEndMinute) + "|";
      s += (InpSuppressMicroBisiSibiEdge ? "1" : "0") + "|" + (InpSuppressStaleFvgBranches ? "1" : "0") + "|";
      s += (InpSuppressTouchedContinuationUnlessRetested ? "1" : "0") + "|" + (InpSuppressContinuationTouchedFvg ? "1" : "0") + "|" + (InpSuppressContinuationStaleFvg ? "1" : "0") + "|";
      s += DoubleToString(InpExecutionRejectCostR, 4) + "|" + DoubleToString(InpExecutionReduceRiskCostR, 4) + "|" + DoubleToString(InpMicroScalpMaxCostFracOfPlannedR, 4) + "|";
      s += (InpRejectSyntheticFallbackAfterCrossedObstacle ? "1" : "0") + "|";
      s += (InpRequireAITargetArbitrationOnObstacle ? "1" : "0") + "|" + (InpHardRejectCrossedObstacleTarget ? "1" : "0") + "|" + (InpAllowAIToUseLiquidityTargetBehindMinorBlocker ? "1" : "0") + "|" + (InpAllowPartialBeforeObstacle ? "1" : "0") + "|";
      s += DoubleToString(InpBlockerKillSeverity, 4) + "|" + DoubleToString(InpBlockerMajorSeverity, 4) + "|" + DoubleToString(InpBlockerMinorMaxSeverity, 4) + "|";
      s += DoubleToString(InpStandardTradeLiquidityRRFloor, 4) + "|" + DoubleToString(InpMaxTargetAdrFrac, 4) + "|" + DoubleToString(InpMaxTargetAtrMult, 4) + "|";
      s += (InpRequireDisplacement ? "1" : "0") + "|" + (InpAllowSyntheticRRTarget ? "1" : "0") + "|" + DoubleToString(InpMinLiveRR2, 4) + "|" + DoubleToString(InpFallbackRR2, 4) + "|" + DoubleToString(InpFallbackRRBufferR, 4) + "|" + DoubleToString(InpObstacleRejectR, 4) + "|";
      s += (InpUseAI ? "1" : "0") + "|" + (InpAiStrict ? "1" : "0") + "|" + DoubleToString(InpMinAiScoreTrend, 4) + "|" + DoubleToString(InpMinAiConfidence, 4) + "|";
      s += DoubleToString(InpAiScoreFullPO3, 4) + "|" + DoubleToString(InpAiScoreMicroPO3, 4) + "|" + DoubleToString(InpAiScoreContinuation, 4) + "|" + DoubleToString(InpAiScoreRange, 4) + "|" + DoubleToString(InpAiScoreFailedBreakout, 4) + "|" + (InpGlobalAiScoreAsHardFloor ? "1" : "0") + "|";
      s += (InpUseSnapshotAI ? "1" : "0") + "|" + (InpRequireSnapshots ? "1" : "0") + "|" + (InpOnlyBreakerRetestVirginStrongOrigin ? "1" : "0") + "|";
      s += DoubleToString(InpRiskPerTradePct, 4) + "|" + DoubleToString(InpRiskPerTradeMoney, 4) + "|" + IntegerToString(InpMaxOpenPositions) + "|" + IntegerToString(InpMaxTradesPerScan) + "|" + IntegerToString(InpMaxTradesPerSweep);
      return IntegerToString((int)(_Fnv1a(s) % 2147483647));
   }

   string RuntimeInputsJson() const {
      string j = "{";
      j += JsonKVStr("engine_version", ENGINE_VERSION) + ",";
      j += JsonKVStr("engine_input_schema", ENGINE_INPUT_SCHEMA) + ",";
      j += JsonKVStr("ai_target_arbitration_schema_version", AI_TARGET_ARBITRATION_SCHEMA_VERSION) + ",";
      j += JsonKVStr("ai_prompt_contract_version", AI_PROMPT_CONTRACT_VERSION) + ",";
      j += JsonKVStr("runtime_input_hash", RuntimeInputHash()) + ",";
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
      j += JsonKVNum("synthetic_fallback_min_clean_capture_ratio", InpSyntheticFallbackMinCleanCaptureRatio, 4) + ",";
      j += JsonKVInt("synthetic_fallback_min_stats_count", InpSyntheticFallbackMinStatsCount) + ",";
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
      j += JsonKVBool("tester_reject_stale_ai_results", InpTesterRejectStaleAiResults) + ",";
      j += JsonKVInt("tester_max_ai_result_age_sim_minutes", InpTesterMaxAiResultAgeSimMinutes) + ",";
      j += JsonKVBool("tester_freeze_ai_execution_snapshot", InpTesterFreezeAiExecutionSnapshot) + ",";
      j += JsonKVBool("use_ai", InpUseAI) + ",";
      j += JsonKVBool("ai_strict", InpAiStrict) + ",";
      j += JsonKVNum("min_ai_score_trend", InpMinAiScoreTrend, 4) + ",";
      j += JsonKVNum("ai_score_full_po3", InpAiScoreFullPO3, 4) + ",";
      j += JsonKVNum("ai_score_micro_po3", InpAiScoreMicroPO3, 4) + ",";
      j += JsonKVNum("ai_score_continuation", InpAiScoreContinuation, 4) + ",";
      j += JsonKVNum("ai_score_range", InpAiScoreRange, 4) + ",";
      j += JsonKVNum("ai_score_failed_breakout", InpAiScoreFailedBreakout, 4) + ",";
      j += JsonKVBool("global_ai_score_as_hard_floor", InpGlobalAiScoreAsHardFloor) + ",";
      j += JsonKVNum("min_ai_confidence", InpMinAiConfidence, 4) + ",";
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
      j += JsonKVInt("max_spread_ticks", InpMaxSpreadTicks) + ",";
      j += JsonKVNum("max_spread_risk_frac", InpMaxSpreadRiskFrac, 4) + ",";
      j += JsonKVNum("max_entry_drift_r", InpMaxEntryDriftR, 4) + ",";
      j += JsonKVStr("virtual_ledger_mode", "fixed_virtual_balance") + ",";
      j += JsonKVNum("analytics_virtual_balance", InpAnalyticsVirtualBalance, 2) + ",";
      j += JsonKVStr("backend_pnl_mode", "closed_deal_pnl");
      j += "}";
      return j;
   }

public:
   CAIGateBridge(CFileBus &bus){ m_bus=&bus; m_next_allowed=0; }

   bool CanSendNow() {
      return (TimeLocal() >= m_next_allowed);
   }

   string BuildRequestJson(const TradePlan &plans[]) {
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

       string j="{";
       j += JsonKVStr("id", p.req_id) + ",";
       j += JsonKVStr("symbol", p.symbol) + ",";
       j += JsonKVBool("is_buy", p.is_buy) + ",";
       j += JsonKVStr("configured_stop_model", _StopModelName()) + ",";
       j += JsonKVStr("runtime_input_hash", runtime_input_hash) + ",";

       datetime server_time = TimeTradeServer();
       if(server_time <= 0) server_time = TimeLocal();
       MqlTick tick;
       ZeroMemory(tick);
       SymbolInfoTick(p.symbol, tick);

       j += "\"runtime\":{";
       j += JsonKVBool("tester", (bool)MQLInfoInteger(MQL_TESTER)) + ",";
       j += JsonKVStr("account_trade_mode", _AccountTradeModeLabel()) + ",";
       j += JsonKVBool("live_fail_closed_on_ai_failure", InpLiveFailClosedOnAIFailure) + ",";
       j += JsonKVBool("allow_rule_only_fallback", InpAllowRuleOnlyFallback) + ",";
       j += JsonKVBool("allow_rule_only_live", InpAllowRuleOnlyLive) + ",";
       j += JsonKVNum("fallback_risk_multiplier", InpFallbackRiskMultiplier, 4) + ",";
       j += JsonKVBool("snapshot_ai_enabled", InpUseSnapshotAI) + ",";
       j += JsonKVBool("require_snapshots", InpRequireSnapshots) + ",";
       j += JsonKVNum("min_ai_confidence", InpMinAiConfidence, 4);
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
       j += JsonKVNum("gross_expected_r", p.gross_expected_r, 4) + ",";
       j += JsonKVNum("net_expected_r", p.net_expected_r, 4) + ",";
       j += JsonKVNum("setup_score", p.setup_score, 4) + ",";
       j += JsonKVStr("setup_family", p.setup_family) + ",";
       j += JsonKVStr("setup_class", p.setup_class) + ",";
       j += JsonKVStr("fvg_execution_class", p.fvg_execution_class) + ",";
       j += JsonKVStr("management_profile", p.management_profile) + ",";
       j += JsonKVStr("target_model", p.target_model) + ",";
       j += JsonKVStr("analytics_key", p.analytics_key) + ",";
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
       j += JsonKVNum("expected_value_r", p.expected_value_r, 4) + ",";
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
          j += JsonKVStr("trade_key", c.trade_key) + ",";
          j += JsonKVInt("setup_snapshot_time", (int)c.setup_snapshot_time) + ",";
          j += JsonKVInt("ai_request_time", (int)c.ai_request_time) + ",";
          j += JsonKVStr("broker_comment", c.broker_comment) + ",";
          j += JsonKVStr("model_code", c.model_code) + ",";
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
          j += JsonKVNum("setup_score", c.setup_score, 4) + ",";
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
          j += JsonKVNum("sequence_quality", c.sequence_quality, 4) + ",";
          j += JsonKVNum("htf_alignment_score", c.htf_alignment_score, 4) + ",";
          j += JsonKVNum("adverse_context_score", c.adverse_context_score, 4) + ",";
          j += JsonKVNum("execution_cost_r", c.execution_cost_r, 4) + ",";
          j += JsonKVNum("slippage_r", c.slippage_r, 4) + ",";
          j += JsonKVNum("commission_r", c.commission_r, 4) + ",";
          j += JsonKVBool("execution_cost_risk_reduced", c.execution_cost_risk_reduced) + ",";
          j += JsonKVNum("execution_cost_risk_multiplier", c.execution_cost_risk_multiplier, 4) + ",";
          j += JsonKVNum("gross_expected_r", c.gross_expected_r, 4) + ",";
          j += JsonKVNum("net_expected_r", c.net_expected_r, 4) + ",";
          j += JsonKVNum("expected_value_r", c.expected_value_r, 4) + ",";
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
          j += JsonKVNum("opposing_obstruction_score", c.fvg.opposing_obstruction_score, 4);
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
       j += JsonKVStr("session_code", p.session_code) + ",";
       j += JsonKVStr("killzone_code", p.killzone_code) + ",";
       j += JsonKVStr("broker_comment", p.broker_comment) + ",";
       j += JsonKVNum("sl", p.sl, 8) + ",";
       j += JsonKVNum("tp2", p.tp2, 8) + ",";
       j += JsonKVNum("setup_score", p.setup_score, 4) + ",";
       j += JsonKVStr("setup_family", p.setup_family) + ",";
       j += JsonKVStr("setup_class", p.setup_class) + ",";
       j += JsonKVStr("fvg_execution_class", p.fvg_execution_class) + ",";
       j += JsonKVStr("management_profile", p.management_profile) + ",";
       j += JsonKVStr("target_model", p.target_model) + ",";
       j += JsonKVStr("analytics_key", p.analytics_key) + ",";
       j += JsonKVNum("execution_cost_r", p.execution_cost_r, 4) + ",";
       j += JsonKVNum("net_expected_r", p.net_expected_r, 4) + ",";
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

   bool SendRequestCandidates(TradePlan &plans[], string &out_req_id) {
      if(!InpUseAI) return false;
      if(!CanSendNow()) return false;
      if(ArraySize(plans) <= 0) return false;

      out_req_id = plans[0].req_id;
      if(StringLen(out_req_id) == 0) out_req_id = _NowId(plans[0].symbol);
      for(int i=0; i<ArraySize(plans); i++) plans[i].req_id = out_req_id;
      string path = m_bus.ReqDir() + "\\" + out_req_id + ".json";
      string payload = BuildRequestJson(plans);
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
      out.ok=false;
      string resp_path = m_bus.RespDir() + "\\" + req_id + ".json";
      string txt;
      if(!m_bus.ReadText(resp_path, txt)) return false;

      // Parse response
      string rid = JsonGetString(txt, "id", "");
      if(rid != req_id){
         string needle1 = "\"id\":\"" + req_id + "\"";
         string needle2 = "\"id\": \"" + req_id + "\"";
         if(StringFind(txt, needle1) < 0 && StringFind(txt, needle2) < 0) return false;
      }

      if(StringFind(txt, "\"allow\"") < 0) return false;
      double parsed_score = JsonGetNumber(txt, "score", -1.0e100);
      double parsed_confidence = JsonGetNumber(txt, "confidence", -1.0e100);
      double parsed_choice = JsonGetNumber(txt, "chosen_index", -1.0e100);
      if(parsed_score <= -1.0e90 || parsed_confidence <= -1.0e90 || parsed_choice <= -1.0e90) return false;

      out.score = parsed_score;
      out.allow = JsonGetBool(txt, "allow", false);
      out.chosen_index = (int)parsed_choice;
      out.confidence = parsed_confidence;
      out.reasons_json = JsonGetString(txt, "reasons", "");
      out.decision_source = JsonGetString(txt, "decision_source", "");
      out.decision_id = JsonGetString(txt, "decision_id", req_id);
      out.rejection_codes_json = JsonGetArray(txt, "rejection_codes", "[]");
      out.narrative_state = JsonGetString(txt, "narrative_state", "");
      out.invalidation_risks_json = JsonGetArray(txt, "invalidation_risks", "[]");
      out.missing_confirmations_json = JsonGetArray(txt, "missing_confirmations", "[]");
      out.suggested_risk_multiplier = JsonGetNumber(txt, "suggested_risk_multiplier", 1.0);
      out.model_version = JsonGetString(txt, "model_version", "");
      out.score_threshold = JsonGetNumber(txt, "ai_score_threshold", 0.0);
      out.threshold_source = JsonGetString(txt, "ai_threshold_source", "");
      out.threshold_passed = JsonGetBool(txt, "ai_threshold_passed", true);
      out.reject_reason = JsonGetString(txt, "ai_reject_reason", "");
      out.global_score_as_hard_floor = JsonGetBool(txt, "global_ai_score_as_hard_floor", false);
      out.chosen_target_model = JsonGetString(txt, "chosen_target_model", "");
      out.chosen_tp1 = JsonGetNumber(txt, "chosen_tp1", 0.0);
      out.chosen_tp2 = JsonGetNumber(txt, "chosen_tp2", 0.0);
      out.chosen_rr1 = JsonGetNumber(txt, "chosen_rr1", 0.0);
      out.chosen_rr2 = JsonGetNumber(txt, "chosen_rr2", 0.0);
      out.rejected_target_models_json = JsonGetArray(txt, "rejected_target_models", "[]");
      out.target_blocker_kind = JsonGetString(txt, "target_blocker_kind", "");
      out.target_blocker_severity_present = JsonGetBool(txt, "target_blocker_severity_present", JsonHasKey(txt, "target_blocker_severity"));
      out.target_blocker_class_present = JsonGetBool(txt, "target_blocker_class_present", JsonHasKey(txt, "target_blocker_class"));
      out.target_blocker_is_trade_killer_present = JsonGetBool(txt, "target_blocker_is_trade_killer_present", JsonHasKey(txt, "target_blocker_is_trade_killer"));
      out.target_decision_reason_present = JsonGetBool(txt, "target_decision_reason_present", JsonHasKey(txt, "target_decision_reason"));
      out.target_blocker_severity = JsonGetNumber(txt, "target_blocker_severity", -1.0);
      out.target_blocker_class = JsonGetString(txt, "target_blocker_class", "");
      out.target_blocker_is_trade_killer = JsonGetBool(txt, "target_blocker_is_trade_killer", false);
      out.target_decision_reason = JsonGetString(txt, "target_decision_reason", "");
      out.why_not_liquidity_target = JsonGetString(txt, "why_not_liquidity_target", "");
      out.why_not_partial_before_obstacle = JsonGetString(txt, "why_not_partial_before_obstacle", "");
      out.why_not_capped_before_obstacle = JsonGetString(txt, "why_not_capped_before_obstacle", "");
      out.why_not_synthetic_fallback = JsonGetString(txt, "why_not_synthetic_fallback", "");
      out.target_arbitration_schema_version = JsonGetString(txt, "target_arbitration_schema_version", "");
      out.prompt_contract_version = JsonGetString(txt, "prompt_contract_version", "");
      out.target_comparison_json = JsonGetObject(txt, "target_comparison", "{}");

      out.ok = true;

      // Move response to stale/bin after consuming
      m_bus.CopyToStale(resp_path);

      return true;
   }

   void NotifyFailureBackoff() {
      // throttle retries if the python bridge is down or unresponsive
      m_next_allowed = TimeLocal() + InpAiRetryBackoffMin*60;
   }
};

#endif
