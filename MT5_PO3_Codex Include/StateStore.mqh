//+------------------------------------------------------------------+
//| StateStore.mqh - NDJSON persistence in Common folder              |
//| Keeps bot context across restarts (watchlist, pending AI, penalty) |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_STATESTORE_MQH__
#define __PO3_AIGATE_STATESTORE_MQH__
#include "FileBus.mqh"
#include "Types.mqh"
#include "Config.mqh"
#include "JsonLite.mqh"

class CStateStore {
private:
   CFileBus *m_bus;

   string _NL() { return "\n"; }

   bool _IsTrimChar(const ushort c) const {
      return (c == ' ' || c == '\t' || c == '\r' || c == '\n');
   }

   string _TrimCopy(const string s) {
      int len = (int)StringLen(s);
      int start = 0;
      while(start < len && _IsTrimChar((ushort)StringGetCharacter(s, start))) start++;
      int end = len - 1;
      while(end >= start && _IsTrimChar((ushort)StringGetCharacter(s, end))) end--;
      if(end < start) return "";
      return StringSubstr(s, start, end-start+1);
   }

   // NDJSON encoder/decoder for trade plans.
   string PlanToJson(const TradePlan &p) {
      string j = "{";
      j += JsonKVStr("symbol", p.symbol) + ",";
      j += JsonKVBool("is_buy", p.is_buy) + ",";
      j += JsonKVInt("htf", (int)p.htf) + ",";
      j += JsonKVInt("ltf", (int)p.ltf) + ",";
      j += JsonKVInt("confirm_tf", (int)p.confirm_tf) + ",";
      j += JsonKVNum("entry", p.entry_est, 8) + ",";
      j += JsonKVStr("entry_model", p.entry_model) + ",";
      j += JsonKVStr("entry_branch", p.entry_branch) + ",";
      j += JsonKVStr("tp_model", p.tp_model) + ",";
      j += JsonKVStr("target_source", p.target_source) + ",";
      j += JsonKVNum("sl", p.sl, 8) + ",";
      j += JsonKVNum("tp1", p.tp1, 8) + ",";
      j += JsonKVNum("tp2", p.tp2, 8) + ",";
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
      j += JsonKVNum("setup_score", p.setup_score, 6) + ",";
      j += JsonKVStr("setup_family", p.setup_family) + ",";
      j += JsonKVStr("setup_class", p.setup_class) + ",";
      j += JsonKVStr("fvg_execution_class", p.fvg_execution_class) + ",";
      j += JsonKVStr("model_code", p.model_code) + ",";
      j += JsonKVStr("session_code", p.session_code) + ",";
      j += JsonKVStr("killzone_code", p.killzone_code) + ",";
      j += JsonKVStr("broker_comment", p.broker_comment) + ",";
      j += JsonKVStr("input_snapshot_hash", p.input_snapshot_hash) + ",";
      j += JsonKVStr("origin_quality", p.origin_quality) + ",";
      j += JsonKVBool("exclusive_model_mode", p.exclusive_model_mode) + ",";
      j += JsonKVStr("exclusive_model_name", p.exclusive_model_name) + ",";
      j += JsonKVBool("exclusive_model_passed", p.exclusive_model_passed) + ",";
      j += JsonKVStr("exclusive_fail_reason", p.exclusive_fail_reason) + ",";
      j += JsonKVStr("management_profile", p.management_profile) + ",";
      j += JsonKVStr("asset_class", p.asset_class) + ",";
      j += JsonKVStr("regime_profile", p.regime_profile) + ",";
      j += JsonKVStr("volatility_profile", p.volatility_profile) + ",";
      j += JsonKVStr("policy_bucket", p.policy_bucket) + ",";
      j += JsonKVStr("obstacle_kind", p.obstacle_kind) + ",";
      j += JsonKVNum("obstacle_price", p.obstacle_price, 8) + ",";
      j += JsonKVNum("obstacle_r", p.obstacle_r, 6) + ",";
      j += JsonKVNum("effective_rr2", p.effective_rr2, 6) + ",";
      j += JsonKVNum("estimated_cost_price", p.estimated_cost_price, 8) + ",";
      j += JsonKVNum("estimated_slippage_price", p.estimated_slippage_price, 8) + ",";
      j += JsonKVNum("estimated_commission_money", p.estimated_commission_money, 4) + ",";
      j += JsonKVNum("execution_cost_r", p.execution_cost_r, 6) + ",";
      j += JsonKVNum("slippage_r", p.slippage_r, 6) + ",";
      j += JsonKVNum("commission_r", p.commission_r, 6) + ",";
      j += JsonKVBool("execution_cost_risk_reduced", p.execution_cost_risk_reduced) + ",";
      j += JsonKVNum("execution_cost_risk_multiplier", p.execution_cost_risk_multiplier, 6) + ",";
      j += JsonKVNum("gross_expected_r", p.gross_expected_r, 6) + ",";
      j += JsonKVNum("net_expected_r", p.net_expected_r, 6) + ",";
      j += JsonKVNum("liquidity_rr", p.liquidity_rr, 6) + ",";
      j += JsonKVNum("sequence_quality", p.sequence_quality, 6) + ",";
      j += JsonKVNum("htf_alignment_score", p.htf_alignment_score, 6) + ",";
      j += JsonKVNum("adverse_context_score", p.adverse_context_score, 6) + ",";
      j += JsonKVNum("expected_value_r", p.expected_value_r, 6) + ",";
      j += JsonKVBool("runner_trade", p.runner_trade) + ",";
      j += JsonKVBool("runner_downgraded", p.runner_downgraded) + ",";
      j += JsonKVStr("runner_downgrade_reason", p.runner_downgrade_reason) + ",";
      j += JsonKVNum("original_runner_target", p.original_runner_target, 8) + ",";
      j += JsonKVNum("standard_target_after_downgrade", p.standard_target_after_downgrade, 8) + ",";
      j += JsonKVStr("target_model", p.target_model) + ",";
      j += JsonKVBool("target_arbitration_required", p.target_arbitration_required) + ",";
      j += JsonKVNum("liquidity_target_preserved", p.liquidity_target_preserved, 8) + ",";
      j += JsonKVStr("liquidity_target_model", p.liquidity_target_model) + ",";
      j += JsonKVBool("liquidity_target_valid_structurally", p.liquidity_target_valid_structurally) + ",";
      j += JsonKVBool("liquidity_target_blocked_by_obstacle", p.liquidity_target_blocked_by_obstacle) + ",";
      j += JsonKVNum("obstacle_distance_r", p.obstacle_distance_r, 6) + ",";
      j += JsonKVStr("obstacle_tf", p.obstacle_tf) + ",";
      j += JsonKVStr("obstacle_strength_features", p.obstacle_strength_features) + ",";
      j += JsonKVNum("fallback_tp", p.fallback_tp, 8) + ",";
      j += JsonKVNum("fallback_rr", p.fallback_rr, 6) + ",";
      j += JsonKVStr("fallback_source", p.fallback_source) + ",";
      j += JsonKVNum("capped_before_obstacle_tp", p.capped_before_obstacle_tp, 8) + ",";
      j += JsonKVNum("capped_before_obstacle_rr", p.capped_before_obstacle_rr, 6) + ",";
      j += JsonKVStr("capped_before_obstacle_source", p.capped_before_obstacle_source) + ",";
      j += JsonKVNum("original_planned_tp_before_ai", p.original_planned_tp_before_ai, 8) + ",";
      j += JsonKVNum("original_planned_rr_before_ai", p.original_planned_rr_before_ai, 6) + ",";
      j += JsonKVStr("ai_chosen_target_model", p.ai_chosen_target_model) + ",";
      j += JsonKVNum("ai_chosen_tp1", p.ai_chosen_tp1, 8) + ",";
      j += JsonKVNum("ai_chosen_tp2", p.ai_chosen_tp2, 8) + ",";
      j += JsonKVNum("ai_chosen_rr2", p.ai_chosen_rr2, 6) + ",";
      j += JsonKVStr("ai_rejected_target_models", p.ai_rejected_target_models) + ",";
      j += JsonKVNum("ai_blocker_severity", p.ai_blocker_severity, 6) + ",";
      j += JsonKVStr("ai_blocker_class", p.ai_blocker_class) + ",";
      j += JsonKVBool("ai_blocker_is_trade_killer", p.ai_blocker_is_trade_killer) + ",";
      j += JsonKVStr("target_decision_reason", p.target_decision_reason) + ",";
      j += JsonKVBool("target_arbitration_normalized_valid", p.target_arbitration_normalized_valid) + ",";
      j += JsonKVStr("target_arbitration_schema_version", p.target_arbitration_schema_version) + ",";
      j += JsonKVStr("prompt_contract_version", p.prompt_contract_version) + ",";
      j += JsonKVStr("why_not_liquidity_target", p.why_not_liquidity_target) + ",";
      j += JsonKVStr("why_not_partial_before_obstacle", p.why_not_partial_before_obstacle) + ",";
      j += JsonKVStr("why_not_capped_before_obstacle", p.why_not_capped_before_obstacle) + ",";
      j += JsonKVStr("why_not_synthetic_fallback", p.why_not_synthetic_fallback) + ",";
      j += JsonKVStr("target_comparison_json", p.target_comparison_json) + ",";
      j += JsonKVStr("original_target_candidates_json", p.original_target_candidates_json) + ",";
      j += JsonKVStr("analytics_key", p.analytics_key) + ",";
      j += JsonKVStr("symbol_policy_action", p.symbol_policy_action) + ",";
      j += JsonKVStr("symbol_policy_reason", p.symbol_policy_reason) + ",";
      j += JsonKVStr("family_policy_action", p.family_policy_action) + ",";
      j += JsonKVStr("family_policy_reason", p.family_policy_reason) + ",";
      j += JsonKVStr("entry_miss_classification", p.entry_miss_classification) + ",";
      j += JsonKVNum("portfolio_score", p.portfolio_score, 6) + ",";
      j += JsonKVInt("portfolio_rank", p.portfolio_rank) + ",";
      j += JsonKVStr("portfolio_cluster", p.portfolio_cluster) + ",";
      j += JsonKVStr("usd_exposure_key", p.usd_exposure_key) + ",";
      j += JsonKVStr("scheduler_action", p.scheduler_action) + ",";
      j += JsonKVStr("scheduler_reason", p.scheduler_reason) + ",";
      j += JsonKVNum("portfolio_risk_weight", p.portfolio_risk_weight, 6) + ",";
      j += JsonKVNum("session_concentration", p.session_concentration, 6) + ",";
      j += JsonKVNum("usd_concentration", p.usd_concentration, 6) + ",";
      j += JsonKVNum("cluster_concentration", p.cluster_concentration, 6) + ",";
      j += JsonKVNum("diversity_bonus", p.diversity_bonus, 6) + ",";
      j += JsonKVStr("stop_floor_reason", p.stop_floor_reason) + ",";
      j += JsonKVNum("stop_floor_distance", p.stop_floor_distance, 8) + ",";
      j += JsonKVNum("stop_noise_band", p.stop_noise_band, 8) + ",";
      j += JsonKVNum("broker_min_stop_distance", p.broker_min_stop_distance, 8) + ",";
      j += JsonKVBool("stop_microstructure_distortion", p.stop_microstructure_distortion) + ",";
      j += JsonKVNum("stop_quality_score", p.stop_quality_score, 6) + ",";
      j += JsonKVNum("realized_stop_quality", p.realized_stop_quality, 6) + ",";
      j += JsonKVNum("realized_stop_buffer_r", p.realized_stop_buffer_r, 6) + ",";
      j += JsonKVNum("ote_distance_frac", p.ote_distance_frac, 6) + ",";
      j += JsonKVNum("ote_softness_frac", p.ote_softness_frac, 6) + ",";
      j += JsonKVStr("ote_state", p.ote_state) + ",";
      j += JsonKVStr("subtype_policy_action", p.subtype_policy_action) + ",";
      j += JsonKVNum("subtype_policy_penalty", p.subtype_policy_penalty, 6) + ",";
      j += JsonKVNum("subtype_shrunk_win_rate", p.subtype_shrunk_win_rate, 6) + ",";
      j += JsonKVNum("subtype_avg_r", p.subtype_avg_r, 6) + ",";
      j += JsonKVNum("subtype_risk_multiplier", p.subtype_risk_multiplier, 6) + ",";
      j += JsonKVNum("setup_floor_score", p.setup_floor_score, 6) + ",";
      j += JsonKVNum("setup_floor_penalty", p.setup_floor_penalty, 6) + ",";
      j += JsonKVStr("setup_floor_action", p.setup_floor_action) + ",";
      j += JsonKVStr("session_weekday_policy_action", p.session_weekday_policy_action) + ",";
      j += JsonKVNum("session_weekday_risk_multiplier", p.session_weekday_risk_multiplier, 6) + ",";
      j += JsonKVNum("session_weekday_rr_delta", p.session_weekday_rr_delta, 6) + ",";
      j += JsonKVNum("session_weekday_score_bias", p.session_weekday_score_bias, 6) + ",";
      j += JsonKVStr("policy_snapshot_id", p.policy_snapshot_id) + ",";
      j += JsonKVStr("policy_version", p.policy_version) + ",";
      j += JsonKVStr("risk_version", p.risk_version) + ",";
      j += JsonKVStr("reject_code", p.reject_code) + ",";
      j += JsonKVInt("source_t_sweep", (int)p.source_t_sweep) + ",";
      j += JsonKVInt("source_t_disp", (int)p.source_t_disp) + ",";
      j += JsonKVInt("source_t_bos", (int)p.source_t_bos) + ",";
      j += JsonKVStr("source_context_tier", p.source_context_tier) + ",";
      j += JsonKVStr("source_sweep_side", p.source_sweep_side) + ",";
      j += JsonKVNum("source_manip_low", p.source_manip_low, 8) + ",";
      j += JsonKVNum("source_manip_high", p.source_manip_high, 8) + ",";
      j += JsonKVNum("source_dr_high", p.source_dr_high, 8) + ",";
      j += JsonKVNum("source_dr_low", p.source_dr_low, 8) + ",";
      j += JsonKVNum("source_liquidity_target", p.source_liquidity_target, 8) + ",";
      j += JsonKVStr("source_liquidity_kind", p.source_liquidity_kind) + ",";
      j += JsonKVInt("candidate_index", p.candidate_index) + ",";
      j += JsonKVInt("candidate_count", p.candidate_count) + ",";
      j += JsonKVStr("trade_key", p.trade_key) + ",";
      j += JsonKVStr("setup_id", p.setup_id) + ",";
      j += JsonKVStr("fvg_id", p.fvg_id) + ",";
      j += JsonKVStr("candidate_id", p.candidate_id) + ",";
      j += JsonKVStr("ai_decision_id", p.ai_decision_id) + ",";
      j += JsonKVStr("lineage_root_id", p.lineage_root_id) + ",";
      j += JsonKVStr("parent_setup_id", p.parent_setup_id) + ",";
      j += JsonKVInt("lineage_version", p.lineage_version) + ",";
      j += JsonKVInt("attempt_number_for_sweep", p.attempt_number_for_sweep) + ",";
      j += JsonKVStr("narrative_state", p.narrative_state) + ",";
      j += JsonKVStr("superseded_by", p.superseded_by) + ",";
      j += JsonKVStr("invalidation_cause", p.invalidation_cause) + ",";
      j += JsonKVStr("snapshot_htf_path", p.snapshot_htf_path) + ",";
      j += JsonKVStr("snapshot_ltf_path", p.snapshot_ltf_path) + ",";
      j += JsonKVInt("created_at", (int)p.created_at) + ",";
      j += JsonKVInt("setup_snapshot_time", (int)p.setup_snapshot_time) + ",";
      j += JsonKVInt("ai_request_time", (int)p.ai_request_time) + ",";
      j += JsonKVInt("ai_advisory_time", (int)p.ai_advisory_time) + ",";
      j += JsonKVInt("ai_result_age_sim_minutes", p.ai_result_age_sim_minutes) + ",";
      j += JsonKVBool("tester_ai_result_stale", p.tester_ai_result_stale) + ",";
      j += JsonKVInt("ai_requested_at", (int)p.ai_requested_at) + ",";
      j += JsonKVInt("last_score_refresh", (int)p.last_score_refresh) + ",";
      j += JsonKVStr("ai_decision_source", p.ai_decision_source) + ",";
      j += JsonKVBool("ai_allow", p.ai.allow) + ",";
      j += JsonKVNum("ai_score", p.ai.score, 4) + ",";
      j += JsonKVInt("ai_chosen_index", p.ai.chosen_index) + ",";
      j += JsonKVNum("ai_confidence", p.ai.confidence, 4) + ",";
      j += JsonKVStr("ai_reasons_json", p.ai.reasons_json) + ",";
      j += JsonKVStr("ai_response_source", p.ai.decision_source) + ",";
      j += JsonKVStr("ai_decision_id_value", p.ai.decision_id) + ",";
      j += JsonKVStr("ai_rejection_codes_json", p.ai.rejection_codes_json) + ",";
      j += JsonKVStr("ai_narrative_state", p.ai.narrative_state) + ",";
      j += JsonKVStr("ai_invalidation_risks_json", p.ai.invalidation_risks_json) + ",";
      j += JsonKVStr("ai_missing_confirmations_json", p.ai.missing_confirmations_json) + ",";
      j += JsonKVNum("ai_suggested_risk_multiplier", p.ai.suggested_risk_multiplier, 6) + ",";
      j += JsonKVStr("ai_model_version", p.ai.model_version) + ",";
      j += JsonKVNum("ai_score_threshold", p.ai.score_threshold, 4) + ",";
      j += JsonKVStr("ai_threshold_source", p.ai.threshold_source) + ",";
      j += JsonKVBool("ai_threshold_passed", p.ai.threshold_passed) + ",";
      j += JsonKVStr("ai_reject_reason", p.ai.reject_reason) + ",";
      j += JsonKVBool("global_ai_score_as_hard_floor", p.ai.global_score_as_hard_floor) + ",";
      j += JsonKVStr("ai_chosen_target_model_value", p.ai.chosen_target_model) + ",";
      j += JsonKVNum("ai_chosen_tp1_value", p.ai.chosen_tp1, 8) + ",";
      j += JsonKVNum("ai_chosen_tp2_value", p.ai.chosen_tp2, 8) + ",";
      j += JsonKVNum("ai_chosen_rr1_value", p.ai.chosen_rr1, 6) + ",";
      j += JsonKVNum("ai_chosen_rr2_value", p.ai.chosen_rr2, 6) + ",";
      j += JsonKVStr("ai_rejected_target_models_json", p.ai.rejected_target_models_json) + ",";
      j += JsonKVStr("ai_target_blocker_kind", p.ai.target_blocker_kind) + ",";
      j += JsonKVNum("ai_target_blocker_severity", p.ai.target_blocker_severity, 6) + ",";
      j += JsonKVStr("ai_target_blocker_class", p.ai.target_blocker_class) + ",";
      j += JsonKVBool("ai_target_blocker_is_trade_killer", p.ai.target_blocker_is_trade_killer) + ",";
      j += JsonKVStr("ai_target_decision_reason", p.ai.target_decision_reason) + ",";
      j += JsonKVStr("ai_why_not_liquidity_target", p.ai.why_not_liquidity_target) + ",";
      j += JsonKVStr("ai_why_not_partial_before_obstacle", p.ai.why_not_partial_before_obstacle) + ",";
      j += JsonKVStr("ai_why_not_capped_before_obstacle", p.ai.why_not_capped_before_obstacle) + ",";
      j += JsonKVStr("ai_why_not_synthetic_fallback", p.ai.why_not_synthetic_fallback) + ",";
      j += JsonKVStr("ai_target_arbitration_schema_version", p.ai.target_arbitration_schema_version) + ",";
      j += JsonKVStr("ai_prompt_contract_version", p.ai.prompt_contract_version) + ",";
      j += JsonKVStr("ai_target_comparison_json", p.ai.target_comparison_json) + ",";
      j += JsonKVBool("armed", p.armed) + ",";
      j += JsonKVBool("mid_touched", p.mid_touched) + ",";
      j += JsonKVBool("b50_touched", p.b50_touched) + ",";
      j += JsonKVInt("bars_waited", p.bars_waited) + ",";
      j += JsonKVInt("arm_max_bars", p.arm_max_bars) + ",";
      j += JsonKVInt("max_watch_minutes", p.max_watch_minutes) + ",";
      j += JsonKVInt("last_confirm_bar_time", (int)p.last_confirm_bar_time) + ",";
      j += JsonKVInt("armed_at", (int)p.armed_at) + ",";
      j += JsonKVNum("tp1_r_multiple", p.tp1_r_multiple, 4) + ",";
      j += JsonKVNum("tp1_partial_pct", p.tp1_partial_pct, 4) + ",";
      j += JsonKVStr("be_rule", p.be_rule) + ",";
      j += JsonKVNum("be_trigger_r", p.be_trigger_r, 4) + ",";
      j += JsonKVNum("be_offset_points", p.be_offset_points, 4) + ",";
      j += JsonKVNum("stop_buffer_points", p.stop_buffer_points, 4) + ",";
      j += JsonKVNum("penalty_mae_trigger_r", p.penalty_mae_trigger_r, 4) + ",";
      j += JsonKVNum("penalty_giveback_trigger_r", p.penalty_giveback_trigger_r, 4) + ",";
      j += JsonKVNum("penalty_giveback_floor_r", p.penalty_giveback_floor_r, 4) + ",";
      j += JsonKVInt("penalty_stuck_minutes", p.penalty_stuck_minutes) + ",";
      j += JsonKVNum("penalty_stuck_min_mfe_r", p.penalty_stuck_min_mfe_r, 4) + ",";
      j += JsonKVNum("penalty_dr_invalid_cut_pct", p.penalty_dr_invalid_cut_pct, 4) + ",";
      j += JsonKVNum("penalty_fvg_invalid_cut_pct", p.penalty_fvg_invalid_cut_pct, 4) + ",";
      j += JsonKVInt("penalty_close_strikes", p.penalty_close_strikes) + ",";
      j += JsonKVBool("tp1_done", p.tp1_done) + ",";
      j += JsonKVNum("planned_entry", p.planned_entry, 8) + ",";
      j += JsonKVNum("planned_sl", p.planned_sl, 8) + ",";
      j += JsonKVNum("planned_tp1", p.planned_tp1, 8) + ",";
      j += JsonKVNum("planned_tp2", p.planned_tp2, 8) + ",";
      j += JsonKVInt("planned_at", (int)p.planned_at) + ",";
      j += JsonKVNum("filled_entry", p.filled_entry, 8) + ",";
      j += JsonKVInt("filled_at", (int)p.filled_at) + ",";
      j += JsonKVNum("fill_slippage", p.fill_slippage, 8) + ",";
      j += JsonKVNum("fill_slippage_r", p.fill_slippage_r, 6) + ",";
      j += JsonKVNum("initial_volume", p.initial_volume, 4) + ",";
      j += JsonKVNum("position_id", (double)p.position_id, 0) + ",";
      j += JsonKVNum("mfe_price", p.mfe_price, 8) + ",";
      j += JsonKVNum("mae_price", p.mae_price, 8) + ",";
      j += JsonKVNum("mfe_r", p.mfe_r, 6) + ",";
      j += JsonKVNum("mae_r", p.mae_r, 6) + ",";
      j += JsonKVInt("closed_at", (int)p.closed_at) + ",";
      j += JsonKVNum("realized_pnl", p.realized_pnl, 2) + ",";
      j += JsonKVNum("realized_r", p.realized_r, 6) + ",";
      j += JsonKVStr("exit_path", p.exit_path) + ",";
      j += JsonKVBool("analytics_logged", p.analytics_logged) + ",";
      j += JsonKVInt("tp1_hit_at", (int)p.tp1_hit_at) + ",";
      j += JsonKVInt("tp2_hit_at", (int)p.tp2_hit_at) + ",";
      j += JsonKVInt("sl_hit_at", (int)p.sl_hit_at) + ",";
      j += JsonKVBool("tp2_realistic_before_reversal", p.tp2_realistic_before_reversal) + ",";
      j += JsonKVBool("be_move_helped", p.be_move_helped) + ",";
      j += JsonKVBool("trailing_stop_improved", p.trailing_stop_improved) + ",";
      j += JsonKVNum("target_efficiency", p.target_efficiency, 6) + ",";
      j += JsonKVStr("req_id", p.req_id) + ",";
      // FVG
      j += "\"fvg\":{";
      j += JsonKVBool("bullish", p.fvg.bullish) + ",";
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
      j += JsonKVNum("origin_score", p.fvg.origin_score, 4) + ",";
      j += JsonKVNum("cleanliness_score", p.fvg.cleanliness_score, 4) + ",";
      j += JsonKVNum("age_score", p.fvg.age_score, 4) + ",";
      j += JsonKVNum("nesting_score", p.fvg.nesting_score, 4) + ",";
      j += JsonKVNum("htf_overlap_score", p.fvg.htf_overlap_score, 4) + ",";
      j += JsonKVNum("retest_score", p.fvg.retest_score, 4) + ",";
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
      j += JsonKVBool("bias_long", p.po3.bias_long) + ",";
      j += JsonKVBool("bias_short", p.po3.bias_short) + ",";
      j += JsonKVNum("context_score", p.po3.context_score, 6) + ",";
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
      j += JsonKVNum("dr_high", p.po3.dr_high, 8) + ",";
      j += JsonKVNum("dr_low", p.po3.dr_low, 8) + ",";
      j += JsonKVNum("dr_mid", p.po3.dr_mid, 8) + ",";
      j += JsonKVNum("manip_low", p.po3.manip_low, 8) + ",";
      j += JsonKVNum("manip_high", p.po3.manip_high, 8) + ",";
      j += JsonKVNum("swing_low", p.po3.swing_low, 8) + ",";
      j += JsonKVNum("swing_high", p.po3.swing_high, 8) + ",";
      j += JsonKVInt("t_sweep", (int)p.po3.t_sweep) + ",";
      j += JsonKVInt("t_disp", (int)p.po3.t_disp) + ",";
      j += JsonKVInt("t_bos", (int)p.po3.t_bos) + ",";
      j += JsonKVInt("t_follow", (int)p.po3.t_follow) + ",";
      j += JsonKVNum("bos_level", p.po3.bos_level, 8) + ",";
      j += JsonKVNum("sweep_strength", p.po3.sweep_strength, 6) + ",";
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
      j += JsonKVNum("ltf_structure_level", p.po3.ltf_structure_level, 8);
      j += "}";
      j += "}";
      return j;
   }

   string AiCacheToJson(const AiCacheEntry &entry) {
      string j = "{";
      j += JsonKVStr("signature", entry.signature) + ",";
      j += JsonKVStr("symbol", entry.symbol) + ",";
      j += JsonKVBool("allow", entry.allow) + ",";
      j += JsonKVNum("score", entry.score, 4) + ",";
      j += JsonKVInt("chosen_index", entry.chosen_index) + ",";
      j += JsonKVNum("confidence", entry.confidence, 4) + ",";
      j += JsonKVStr("reasons_json", entry.reasons_json) + ",";
      j += JsonKVInt("created_at", (int)entry.created_at);
      j += "}";
      return j;
   }

   string PenaltyToJson(const PenaltyState &st) {
      string j = "{";
      j += JsonKVStr("symbol", st.symbol) + ",";
      j += JsonKVNum("position_ticket", (double)st.position_ticket, 0) + ",";
      j += JsonKVInt("opened_at", (int)st.opened_at) + ",";
      j += JsonKVNum("entry", st.entry, 8) + ",";
      j += JsonKVNum("sl", st.sl, 8) + ",";
      j += JsonKVNum("risk_dist", st.risk_dist, 8) + ",";
      j += JsonKVBool("is_buy", st.is_buy) + ",";
      j += JsonKVNum("mfe_price", st.mfe_price, 8) + ",";
      j += JsonKVNum("mae_price", st.mae_price, 8) + ",";
      j += JsonKVInt("strikes", st.strikes) + ",";
      j += JsonKVInt("last_reduction_at", (int)st.last_reduction_at);
      j += "}";
      return j;
   }

   bool PlanFromJson(const string json, TradePlan &p) {
      p.symbol = JsonGetString(json, "symbol", "");
      if(StringLen(p.symbol)==0) return false;
      p.is_buy = JsonGetBool(json, "is_buy", true);
      p.htf = (ENUM_TIMEFRAMES)(int)JsonGetNumber(json, "htf", (double)PO3EffectiveHTF());
      p.ltf = (ENUM_TIMEFRAMES)(int)JsonGetNumber(json, "ltf", (double)PO3EffectiveEntryTF());
      p.confirm_tf = (ENUM_TIMEFRAMES)(int)JsonGetNumber(json, "confirm_tf", (double)PO3EffectiveConfirmTF());
      p.entry_est = JsonGetNumber(json, "entry", 0);
      p.entry_model = JsonGetString(json, "entry_model", "");
      p.entry_branch = JsonGetString(json, "entry_branch", p.entry_model);
      p.tp_model = JsonGetString(json, "tp_model", "");
      p.target_source = JsonGetString(json, "target_source", "");
      p.sl = JsonGetNumber(json, "sl", 0);
      p.tp1 = JsonGetNumber(json, "tp1", 0);
      p.tp2 = JsonGetNumber(json, "tp2", 0);
      p.atr_pct = JsonGetNumber(json, "atr_pct", 0);
      p.trend_strength = JsonGetNumber(json, "trend_strength", 0);
      p.trend_slope_pct = JsonGetNumber(json, "trend_slope_pct", 0);
      p.adx_value = JsonGetNumber(json, "adx_value", 0);
      p.adr_pct = JsonGetNumber(json, "adr_pct", 0);
      p.session_vol_ratio = JsonGetNumber(json, "session_vol_ratio", 0);
      p.vwap_dist_atr = JsonGetNumber(json, "vwap_dist_atr", 0);
      p.compression_score = JsonGetNumber(json, "compression_score", 0);
      p.expansion_score = JsonGetNumber(json, "expansion_score", 0);
      p.news_risk = JsonGetNumber(json, "news_risk", 0);
      p.setup_score = JsonGetNumber(json, "setup_score", 0);
      p.setup_family = JsonGetString(json, "setup_family", "");
      p.setup_class = JsonGetString(json, "setup_class", "");
      p.fvg_execution_class = JsonGetString(json, "fvg_execution_class", "");
      p.model_code = JsonGetString(json, "model_code", "");
      p.session_code = JsonGetString(json, "session_code", "");
      p.killzone_code = JsonGetString(json, "killzone_code", "");
      p.broker_comment = JsonGetString(json, "broker_comment", "");
      p.input_snapshot_hash = JsonGetString(json, "input_snapshot_hash", "");
      p.origin_quality = JsonGetString(json, "origin_quality", "");
      p.exclusive_model_mode = JsonGetBool(json, "exclusive_model_mode", false);
      p.exclusive_model_name = JsonGetString(json, "exclusive_model_name", "");
      p.exclusive_model_passed = JsonGetBool(json, "exclusive_model_passed", false);
      p.exclusive_fail_reason = JsonGetString(json, "exclusive_fail_reason", "");
      p.management_profile = JsonGetString(json, "management_profile", "");
      p.asset_class = JsonGetString(json, "asset_class", "");
      p.regime_profile = JsonGetString(json, "regime_profile", "");
      p.volatility_profile = JsonGetString(json, "volatility_profile", "");
      p.policy_bucket = JsonGetString(json, "policy_bucket", "");
      p.obstacle_kind = JsonGetString(json, "obstacle_kind", "");
      p.obstacle_price = JsonGetNumber(json, "obstacle_price", 0);
      p.obstacle_r = JsonGetNumber(json, "obstacle_r", 0);
      p.effective_rr2 = JsonGetNumber(json, "effective_rr2", 0);
      p.estimated_cost_price = JsonGetNumber(json, "estimated_cost_price", 0);
      p.estimated_slippage_price = JsonGetNumber(json, "estimated_slippage_price", 0);
      p.estimated_commission_money = JsonGetNumber(json, "estimated_commission_money", 0);
      p.execution_cost_r = JsonGetNumber(json, "execution_cost_r", 0);
      p.slippage_r = JsonGetNumber(json, "slippage_r", 0);
      p.commission_r = JsonGetNumber(json, "commission_r", 0);
      p.execution_cost_risk_reduced = JsonGetBool(json, "execution_cost_risk_reduced", false);
      p.execution_cost_risk_multiplier = JsonGetNumber(json, "execution_cost_risk_multiplier", 0);
      p.gross_expected_r = JsonGetNumber(json, "gross_expected_r", 0);
      p.net_expected_r = JsonGetNumber(json, "net_expected_r", 0);
      p.liquidity_rr = JsonGetNumber(json, "liquidity_rr", 0);
      p.sequence_quality = JsonGetNumber(json, "sequence_quality", 0);
      p.htf_alignment_score = JsonGetNumber(json, "htf_alignment_score", 0);
      p.adverse_context_score = JsonGetNumber(json, "adverse_context_score", 0);
      p.expected_value_r = JsonGetNumber(json, "expected_value_r", 0);
      p.runner_trade = JsonGetBool(json, "runner_trade", false);
      p.runner_downgraded = JsonGetBool(json, "runner_downgraded", false);
      p.runner_downgrade_reason = JsonGetString(json, "runner_downgrade_reason", "");
      p.original_runner_target = JsonGetNumber(json, "original_runner_target", 0);
      p.standard_target_after_downgrade = JsonGetNumber(json, "standard_target_after_downgrade", 0);
      p.target_model = JsonGetString(json, "target_model", "");
      p.target_arbitration_required = JsonGetBool(json, "target_arbitration_required", false);
      p.liquidity_target_preserved = JsonGetNumber(json, "liquidity_target_preserved", 0);
      p.liquidity_target_model = JsonGetString(json, "liquidity_target_model", "");
      p.liquidity_target_valid_structurally = JsonGetBool(json, "liquidity_target_valid_structurally", false);
      p.liquidity_target_blocked_by_obstacle = JsonGetBool(json, "liquidity_target_blocked_by_obstacle", false);
      p.obstacle_distance_r = JsonGetNumber(json, "obstacle_distance_r", 0);
      p.obstacle_tf = JsonGetString(json, "obstacle_tf", "");
      p.obstacle_strength_features = JsonGetString(json, "obstacle_strength_features", "");
      p.fallback_tp = JsonGetNumber(json, "fallback_tp", 0);
      p.fallback_rr = JsonGetNumber(json, "fallback_rr", 0);
      p.fallback_source = JsonGetString(json, "fallback_source", "");
      p.capped_before_obstacle_tp = JsonGetNumber(json, "capped_before_obstacle_tp", 0);
      p.capped_before_obstacle_rr = JsonGetNumber(json, "capped_before_obstacle_rr", 0);
      p.capped_before_obstacle_source = JsonGetString(json, "capped_before_obstacle_source", "");
      p.original_planned_tp_before_ai = JsonGetNumber(json, "original_planned_tp_before_ai", 0);
      p.original_planned_rr_before_ai = JsonGetNumber(json, "original_planned_rr_before_ai", 0);
      p.ai_chosen_target_model = JsonGetString(json, "ai_chosen_target_model", "");
      p.ai_chosen_tp1 = JsonGetNumber(json, "ai_chosen_tp1", 0);
      p.ai_chosen_tp2 = JsonGetNumber(json, "ai_chosen_tp2", 0);
      p.ai_chosen_rr2 = JsonGetNumber(json, "ai_chosen_rr2", 0);
      p.ai_rejected_target_models = JsonGetString(json, "ai_rejected_target_models", "");
      p.ai_blocker_severity = JsonGetNumber(json, "ai_blocker_severity", 0);
      p.ai_blocker_class = JsonGetString(json, "ai_blocker_class", "");
      p.ai_blocker_is_trade_killer = JsonGetBool(json, "ai_blocker_is_trade_killer", false);
      p.target_decision_reason = JsonGetString(json, "target_decision_reason", "");
      p.target_arbitration_normalized_valid = JsonGetBool(json, "target_arbitration_normalized_valid", false);
      p.target_arbitration_schema_version = JsonGetString(json, "target_arbitration_schema_version", "");
      p.prompt_contract_version = JsonGetString(json, "prompt_contract_version", "");
      p.why_not_liquidity_target = JsonGetString(json, "why_not_liquidity_target", "");
      p.why_not_partial_before_obstacle = JsonGetString(json, "why_not_partial_before_obstacle", "");
      p.why_not_capped_before_obstacle = JsonGetString(json, "why_not_capped_before_obstacle", "");
      p.why_not_synthetic_fallback = JsonGetString(json, "why_not_synthetic_fallback", "");
      p.target_comparison_json = JsonGetString(json, "target_comparison_json", "");
      p.original_target_candidates_json = JsonGetString(json, "original_target_candidates_json", "");
      p.analytics_key = JsonGetString(json, "analytics_key", "");
      p.symbol_policy_action = JsonGetString(json, "symbol_policy_action", "");
      p.symbol_policy_reason = JsonGetString(json, "symbol_policy_reason", "");
      p.family_policy_action = JsonGetString(json, "family_policy_action", "");
      p.family_policy_reason = JsonGetString(json, "family_policy_reason", "");
      p.entry_miss_classification = JsonGetString(json, "entry_miss_classification", "");
      p.portfolio_score = JsonGetNumber(json, "portfolio_score", 0);
      p.portfolio_rank = (int)JsonGetNumber(json, "portfolio_rank", 0);
      p.portfolio_cluster = JsonGetString(json, "portfolio_cluster", "");
      p.usd_exposure_key = JsonGetString(json, "usd_exposure_key", "");
      p.scheduler_action = JsonGetString(json, "scheduler_action", "");
      p.scheduler_reason = JsonGetString(json, "scheduler_reason", "");
      p.portfolio_risk_weight = JsonGetNumber(json, "portfolio_risk_weight", 0);
      p.session_concentration = JsonGetNumber(json, "session_concentration", 0);
      p.usd_concentration = JsonGetNumber(json, "usd_concentration", 0);
      p.cluster_concentration = JsonGetNumber(json, "cluster_concentration", 0);
      p.diversity_bonus = JsonGetNumber(json, "diversity_bonus", 0);
      p.stop_floor_reason = JsonGetString(json, "stop_floor_reason", "");
      p.stop_floor_distance = JsonGetNumber(json, "stop_floor_distance", 0);
      p.stop_noise_band = JsonGetNumber(json, "stop_noise_band", 0);
      p.broker_min_stop_distance = JsonGetNumber(json, "broker_min_stop_distance", 0);
      p.stop_microstructure_distortion = JsonGetBool(json, "stop_microstructure_distortion", false);
      p.stop_quality_score = JsonGetNumber(json, "stop_quality_score", 0);
      p.realized_stop_quality = JsonGetNumber(json, "realized_stop_quality", 0);
      p.realized_stop_buffer_r = JsonGetNumber(json, "realized_stop_buffer_r", 0);
      p.ote_distance_frac = JsonGetNumber(json, "ote_distance_frac", 0);
      p.ote_softness_frac = JsonGetNumber(json, "ote_softness_frac", 0);
      p.ote_state = JsonGetString(json, "ote_state", "");
      p.subtype_policy_action = JsonGetString(json, "subtype_policy_action", "");
      p.subtype_policy_penalty = JsonGetNumber(json, "subtype_policy_penalty", 0);
      p.subtype_shrunk_win_rate = JsonGetNumber(json, "subtype_shrunk_win_rate", 0);
      p.subtype_avg_r = JsonGetNumber(json, "subtype_avg_r", 0);
      p.subtype_risk_multiplier = JsonGetNumber(json, "subtype_risk_multiplier", 0);
      p.setup_floor_score = JsonGetNumber(json, "setup_floor_score", 0);
      p.setup_floor_penalty = JsonGetNumber(json, "setup_floor_penalty", 0);
      p.setup_floor_action = JsonGetString(json, "setup_floor_action", "");
      p.session_weekday_policy_action = JsonGetString(json, "session_weekday_policy_action", "");
      p.session_weekday_risk_multiplier = JsonGetNumber(json, "session_weekday_risk_multiplier", 0);
      p.session_weekday_rr_delta = JsonGetNumber(json, "session_weekday_rr_delta", 0);
      p.session_weekday_score_bias = JsonGetNumber(json, "session_weekday_score_bias", 0);
      p.policy_snapshot_id = JsonGetString(json, "policy_snapshot_id", "");
      p.policy_version = JsonGetString(json, "policy_version", "");
      p.risk_version = JsonGetString(json, "risk_version", "");
      p.reject_code = JsonGetString(json, "reject_code", "");
      p.source_t_sweep = (datetime)(int)JsonGetNumber(json, "source_t_sweep", 0);
      p.source_t_disp = (datetime)(int)JsonGetNumber(json, "source_t_disp", 0);
      p.source_t_bos = (datetime)(int)JsonGetNumber(json, "source_t_bos", 0);
      p.source_context_tier = JsonGetString(json, "source_context_tier", "");
      p.source_sweep_side = JsonGetString(json, "source_sweep_side", "");
      p.source_manip_low = JsonGetNumber(json, "source_manip_low", 0);
      p.source_manip_high = JsonGetNumber(json, "source_manip_high", 0);
      p.source_dr_high = JsonGetNumber(json, "source_dr_high", 0);
      p.source_dr_low = JsonGetNumber(json, "source_dr_low", 0);
      p.source_liquidity_target = JsonGetNumber(json, "source_liquidity_target", 0);
      p.source_liquidity_kind = JsonGetString(json, "source_liquidity_kind", "");
      p.candidate_index = (int)JsonGetNumber(json, "candidate_index", 0);
      p.candidate_count = (int)JsonGetNumber(json, "candidate_count", 1);
      p.trade_key = JsonGetString(json, "trade_key", "");
      p.setup_id = JsonGetString(json, "setup_id", "");
      p.fvg_id = JsonGetString(json, "fvg_id", "");
      p.candidate_id = JsonGetString(json, "candidate_id", "");
      p.ai_decision_id = JsonGetString(json, "ai_decision_id", "");
      p.lineage_root_id = JsonGetString(json, "lineage_root_id", "");
      p.parent_setup_id = JsonGetString(json, "parent_setup_id", "");
      p.lineage_version = (int)JsonGetNumber(json, "lineage_version", 0);
      p.attempt_number_for_sweep = (int)JsonGetNumber(json, "attempt_number_for_sweep", 0);
      p.narrative_state = JsonGetString(json, "narrative_state", "");
      p.superseded_by = JsonGetString(json, "superseded_by", "");
      p.invalidation_cause = JsonGetString(json, "invalidation_cause", "");
      p.snapshot_htf_path = JsonGetString(json, "snapshot_htf_path", "");
      p.snapshot_ltf_path = JsonGetString(json, "snapshot_ltf_path", "");
      p.created_at = (datetime)(int)JsonGetNumber(json, "created_at", 0);
      p.setup_snapshot_time = (datetime)(int)JsonGetNumber(json, "setup_snapshot_time", 0);
      p.ai_request_time = (datetime)(int)JsonGetNumber(json, "ai_request_time", 0);
      p.ai_advisory_time = (datetime)(int)JsonGetNumber(json, "ai_advisory_time", 0);
      p.ai_result_age_sim_minutes = (int)JsonGetNumber(json, "ai_result_age_sim_minutes", 0);
      p.tester_ai_result_stale = JsonGetBool(json, "tester_ai_result_stale", false);
      p.ai_requested_at = (datetime)(int)JsonGetNumber(json, "ai_requested_at", 0);
      p.last_score_refresh = (datetime)(int)JsonGetNumber(json, "last_score_refresh", 0);
      p.ai_decision_source = JsonGetString(json, "ai_decision_source", "");
      p.ai.allow = JsonGetBool(json, "ai_allow", false);
      p.ai.score = JsonGetNumber(json, "ai_score", 0);
      p.ai.chosen_index = (int)JsonGetNumber(json, "ai_chosen_index", 0);
      p.ai.confidence = JsonGetNumber(json, "ai_confidence", 0);
      p.ai.reasons_json = JsonGetString(json, "ai_reasons_json", "");
      p.ai.decision_source = JsonGetString(json, "ai_response_source", "");
      p.ai.decision_id = JsonGetString(json, "ai_decision_id_value", p.ai_decision_id);
      p.ai.rejection_codes_json = JsonGetString(json, "ai_rejection_codes_json", "[]");
      p.ai.narrative_state = JsonGetString(json, "ai_narrative_state", "");
      p.ai.invalidation_risks_json = JsonGetString(json, "ai_invalidation_risks_json", "[]");
      p.ai.missing_confirmations_json = JsonGetString(json, "ai_missing_confirmations_json", "[]");
      p.ai.suggested_risk_multiplier = JsonGetNumber(json, "ai_suggested_risk_multiplier", 1.0);
      p.ai.model_version = JsonGetString(json, "ai_model_version", "");
      p.ai.score_threshold = JsonGetNumber(json, "ai_score_threshold", 0.0);
      p.ai.threshold_source = JsonGetString(json, "ai_threshold_source", "");
      p.ai.threshold_passed = JsonGetBool(json, "ai_threshold_passed", true);
      p.ai.reject_reason = JsonGetString(json, "ai_reject_reason", "");
      p.ai.global_score_as_hard_floor = JsonGetBool(json, "global_ai_score_as_hard_floor", false);
      p.ai.chosen_target_model = JsonGetString(json, "ai_chosen_target_model_value", p.ai_chosen_target_model);
      p.ai.chosen_tp1 = JsonGetNumber(json, "ai_chosen_tp1_value", p.ai_chosen_tp1);
      p.ai.chosen_tp2 = JsonGetNumber(json, "ai_chosen_tp2_value", p.ai_chosen_tp2);
      p.ai.chosen_rr1 = JsonGetNumber(json, "ai_chosen_rr1_value", 0);
      p.ai.chosen_rr2 = JsonGetNumber(json, "ai_chosen_rr2_value", p.ai_chosen_rr2);
      p.ai.rejected_target_models_json = JsonGetString(json, "ai_rejected_target_models_json", p.ai_rejected_target_models);
      p.ai.target_blocker_kind = JsonGetString(json, "ai_target_blocker_kind", p.obstacle_kind);
      p.ai.target_blocker_severity = JsonGetNumber(json, "ai_target_blocker_severity", p.ai_blocker_severity);
      p.ai.target_blocker_class = JsonGetString(json, "ai_target_blocker_class", p.ai_blocker_class);
      p.ai.target_blocker_is_trade_killer = JsonGetBool(json, "ai_target_blocker_is_trade_killer", p.ai_blocker_is_trade_killer);
      p.ai.target_decision_reason = JsonGetString(json, "ai_target_decision_reason", p.target_decision_reason);
      p.ai.why_not_liquidity_target = JsonGetString(json, "ai_why_not_liquidity_target", p.why_not_liquidity_target);
      p.ai.why_not_partial_before_obstacle = JsonGetString(json, "ai_why_not_partial_before_obstacle", p.why_not_partial_before_obstacle);
      p.ai.why_not_capped_before_obstacle = JsonGetString(json, "ai_why_not_capped_before_obstacle", p.why_not_capped_before_obstacle);
      p.ai.why_not_synthetic_fallback = JsonGetString(json, "ai_why_not_synthetic_fallback", p.why_not_synthetic_fallback);
      p.ai.target_arbitration_schema_version = JsonGetString(json, "ai_target_arbitration_schema_version", p.target_arbitration_schema_version);
      p.ai.prompt_contract_version = JsonGetString(json, "ai_prompt_contract_version", p.prompt_contract_version);
      p.ai.target_comparison_json = JsonGetString(json, "ai_target_comparison_json", p.target_comparison_json);
      p.ai.ok = (StringLen(p.ai.reasons_json) > 0 || p.ai.score > 0 || StringLen(p.ai.decision_source) > 0);
      p.armed = JsonGetBool(json, "armed", false);
      p.mid_touched = JsonGetBool(json, "mid_touched", false);
      p.b50_touched = JsonGetBool(json, "b50_touched", false);
      p.bars_waited = (int)JsonGetNumber(json, "bars_waited", 0);
      p.arm_max_bars = (int)JsonGetNumber(json, "arm_max_bars", 0);
      p.max_watch_minutes = (int)JsonGetNumber(json, "max_watch_minutes", 0);
      p.last_confirm_bar_time = (datetime)(int)JsonGetNumber(json, "last_confirm_bar_time", 0);
      p.armed_at = (datetime)(int)JsonGetNumber(json, "armed_at", 0);
      p.tp1_r_multiple = JsonGetNumber(json, "tp1_r_multiple", 0);
      p.tp1_partial_pct = JsonGetNumber(json, "tp1_partial_pct", 0);
      p.be_rule = JsonGetString(json, "be_rule", "");
      p.be_trigger_r = JsonGetNumber(json, "be_trigger_r", 0);
      p.be_offset_points = JsonGetNumber(json, "be_offset_points", 0);
      p.stop_buffer_points = JsonGetNumber(json, "stop_buffer_points", 0);
      p.penalty_mae_trigger_r = JsonGetNumber(json, "penalty_mae_trigger_r", 0);
      p.penalty_giveback_trigger_r = JsonGetNumber(json, "penalty_giveback_trigger_r", 0);
      p.penalty_giveback_floor_r = JsonGetNumber(json, "penalty_giveback_floor_r", 0);
      p.penalty_stuck_minutes = (int)JsonGetNumber(json, "penalty_stuck_minutes", 0);
      p.penalty_stuck_min_mfe_r = JsonGetNumber(json, "penalty_stuck_min_mfe_r", 0);
      p.penalty_dr_invalid_cut_pct = JsonGetNumber(json, "penalty_dr_invalid_cut_pct", 0);
      p.penalty_fvg_invalid_cut_pct = JsonGetNumber(json, "penalty_fvg_invalid_cut_pct", 0);
      p.penalty_close_strikes = (int)JsonGetNumber(json, "penalty_close_strikes", 0);
      p.tp1_done = JsonGetBool(json, "tp1_done", false);
      p.planned_entry = JsonGetNumber(json, "planned_entry", 0);
      p.planned_sl = JsonGetNumber(json, "planned_sl", 0);
      p.planned_tp1 = JsonGetNumber(json, "planned_tp1", 0);
      p.planned_tp2 = JsonGetNumber(json, "planned_tp2", 0);
      p.planned_at = (datetime)(int)JsonGetNumber(json, "planned_at", 0);
      p.filled_entry = JsonGetNumber(json, "filled_entry", 0);
      p.filled_at = (datetime)(int)JsonGetNumber(json, "filled_at", 0);
      p.fill_slippage = JsonGetNumber(json, "fill_slippage", 0);
      p.fill_slippage_r = JsonGetNumber(json, "fill_slippage_r", 0);
      p.initial_volume = JsonGetNumber(json, "initial_volume", 0);
      p.position_id = (long)JsonGetNumber(json, "position_id", 0);
      p.mfe_price = JsonGetNumber(json, "mfe_price", 0);
      p.mae_price = JsonGetNumber(json, "mae_price", 0);
      p.mfe_r = JsonGetNumber(json, "mfe_r", 0);
      p.mae_r = JsonGetNumber(json, "mae_r", 0);
      p.closed_at = (datetime)(int)JsonGetNumber(json, "closed_at", 0);
      p.realized_pnl = JsonGetNumber(json, "realized_pnl", 0);
      p.realized_r = JsonGetNumber(json, "realized_r", 0);
      p.exit_path = JsonGetString(json, "exit_path", "");
      p.analytics_logged = JsonGetBool(json, "analytics_logged", false);
      p.tp1_hit_at = (datetime)(int)JsonGetNumber(json, "tp1_hit_at", 0);
      p.tp2_hit_at = (datetime)(int)JsonGetNumber(json, "tp2_hit_at", 0);
      p.sl_hit_at = (datetime)(int)JsonGetNumber(json, "sl_hit_at", 0);
      p.tp2_realistic_before_reversal = JsonGetBool(json, "tp2_realistic_before_reversal", false);
      p.be_move_helped = JsonGetBool(json, "be_move_helped", false);
      p.trailing_stop_improved = JsonGetBool(json, "trailing_stop_improved", false);
      p.target_efficiency = JsonGetNumber(json, "target_efficiency", 0);
      p.req_id = JsonGetString(json, "req_id", "");

      string fvg_json = JsonGetObject(json, "fvg", "");
      if(StringLen(fvg_json) == 0) fvg_json = json;
      p.fvg.bullish = JsonGetBool(fvg_json, "bullish", p.is_buy);
      p.fvg.t_form = (datetime)(int)JsonGetNumber(fvg_json, "t_form", 0);
      p.fvg.lower = JsonGetNumber(fvg_json, "lower", 0);
      p.fvg.upper = JsonGetNumber(fvg_json, "upper", 0);
      p.fvg.mid   = JsonGetNumber(fvg_json, "mid", 0);
      p.fvg.touched = JsonGetBool(fvg_json, "touched", false);
      p.fvg.mid_mitigated = JsonGetBool(fvg_json, "mid_mitigated", false);
      p.fvg.fully_filled = JsonGetBool(fvg_json, "fully_filled", false);
      p.fvg.invalidated = JsonGetBool(fvg_json, "invalidated", false);
      p.fvg.entry_invalid = JsonGetBool(fvg_json, "entry_invalid", false);
      p.fvg.structure_invalidated = JsonGetBool(fvg_json, "structure_invalidated", false);
      p.fvg.mitigation_state = JsonGetString(fvg_json, "mitigation_state", "");
      p.fvg.invalidation_reason = JsonGetString(fvg_json, "invalidation_reason", "");
      p.fvg.continuation = JsonGetBool(fvg_json, "continuation", false);
      p.fvg.reversal = JsonGetBool(fvg_json, "reversal", false);
      p.fvg.context_type = JsonGetString(fvg_json, "context_type", "");
      p.fvg.mitigated = p.fvg.mid_mitigated;
      p.fvg.origin_score = JsonGetNumber(fvg_json, "origin_score", 0);
      p.fvg.cleanliness_score = JsonGetNumber(fvg_json, "cleanliness_score", 0);
      p.fvg.age_score = JsonGetNumber(fvg_json, "age_score", 0);
      p.fvg.nesting_score = JsonGetNumber(fvg_json, "nesting_score", 0);
      p.fvg.htf_overlap_score = JsonGetNumber(fvg_json, "htf_overlap_score", 0);
      p.fvg.retest_score = JsonGetNumber(fvg_json, "retest_score", 0);
      p.fvg.continuation_score = JsonGetNumber(fvg_json, "continuation_score", 0);
      p.fvg.reversal_score = JsonGetNumber(fvg_json, "reversal_score", 0);
      p.fvg.score = JsonGetNumber(fvg_json, "score", JsonGetNumber(fvg_json, "fvg_score", 0));
      p.fvg.execution_class = JsonGetString(fvg_json, "execution_class", "");
      p.fvg.mitigation_depth_frac = JsonGetNumber(fvg_json, "mitigation_depth_frac", 0);
      p.fvg.age_bars = (int)JsonGetNumber(fvg_json, "age_bars", 0);
      p.fvg.displacement_candle_score = JsonGetNumber(fvg_json, "displacement_candle_score", 0);
      p.fvg.middle_candle_body_score = JsonGetNumber(fvg_json, "middle_candle_body_score", 0);
      p.fvg.volume_impulse_score = JsonGetNumber(fvg_json, "volume_impulse_score", 0);
      p.fvg.gap_width_atr_score = JsonGetNumber(fvg_json, "gap_width_atr_score", 0);
      p.fvg.manipulation_distance_score = JsonGetNumber(fvg_json, "manipulation_distance_score", 0);
      p.fvg.premium_discount_score = JsonGetNumber(fvg_json, "premium_discount_score", 0);
      p.fvg.htf_nesting_score = JsonGetNumber(fvg_json, "htf_nesting_score", 0);
      p.fvg.freshness_score = JsonGetNumber(fvg_json, "freshness_score", 0);
      p.fvg.retest_quality_score = JsonGetNumber(fvg_json, "retest_quality_score", 0);
      p.fvg.opposing_obstruction_score = JsonGetNumber(fvg_json, "opposing_obstruction_score", 0);

      string po3_json = JsonGetObject(json, "po3", "");
      if(StringLen(po3_json) == 0) po3_json = json;
      p.po3.po3_state = JsonGetString(po3_json, "po3_state", "");
      p.po3.state = (PO3State)(int)JsonGetNumber(po3_json, "po3_state_id", (double)(int)PO3StateFromString(p.po3.po3_state));
      p.po3.po3_state = PO3StateToString(p.po3.state);
      p.po3.po3_state_reason = JsonGetString(po3_json, "po3_state_reason", "");
      p.po3.sweep_side = JsonGetString(po3_json, "sweep_side", "");
      p.po3.structure_type = JsonGetString(po3_json, "structure_type", "");
      p.po3.htf_structure_type = JsonGetString(po3_json, "htf_structure_type", "");
      p.po3.ltf_structure_type = JsonGetString(po3_json, "ltf_structure_type", "");
      p.po3.final_setup_class = JsonGetString(po3_json, "final_setup_class", "");
      p.po3.po3_scope = JsonGetString(po3_json, "po3_scope", "");
      p.po3.bias_long  = JsonGetBool(po3_json, "bias_long", p.is_buy);
      p.po3.bias_short = JsonGetBool(po3_json, "bias_short", !p.is_buy);
      p.po3.context_score = JsonGetNumber(po3_json, "context_score", 0);
      p.po3.has_sweep = JsonGetBool(po3_json, "has_sweep", false);
      p.po3.has_displacement = JsonGetBool(po3_json, "has_displacement", false);
      p.po3.has_bos = JsonGetBool(po3_json, "has_bos", false);
      p.po3.has_follow_through = JsonGetBool(po3_json, "has_follow_through", false);
      p.po3.context_tier = JsonGetString(po3_json, "context_tier", "");
      p.po3.developing_bos = JsonGetBool(po3_json, "developing_bos", false);
      p.po3.context_age_bars = (int)JsonGetNumber(po3_json, "context_age_bars", 0);
      p.po3.htf_bos = JsonGetBool(po3_json, "htf_bos", p.po3.has_bos);
      p.po3.htf_internal_bos = JsonGetBool(po3_json, "htf_internal_bos", false);
      p.po3.htf_swing_bos = JsonGetBool(po3_json, "htf_swing_bos", p.po3.has_bos);
      p.po3.htf_pretrend_dir = (int)JsonGetNumber(po3_json, "htf_pretrend_dir", 0);
      p.po3.dr_high = JsonGetNumber(po3_json, "dr_high", 0);
      p.po3.dr_low  = JsonGetNumber(po3_json, "dr_low", 0);
      p.po3.dr_mid = JsonGetNumber(po3_json, "dr_mid", 0);
      p.po3.manip_low = JsonGetNumber(po3_json, "manip_low", 0);
      p.po3.manip_high = JsonGetNumber(po3_json, "manip_high", 0);
      p.po3.swing_low = JsonGetNumber(po3_json, "swing_low", 0);
      p.po3.swing_high = JsonGetNumber(po3_json, "swing_high", 0);
      p.po3.t_sweep = (datetime)(int)JsonGetNumber(po3_json, "t_sweep", 0);
      p.po3.t_disp = (datetime)(int)JsonGetNumber(po3_json, "t_disp", 0);
      p.po3.t_bos = (datetime)(int)JsonGetNumber(po3_json, "t_bos", 0);
      p.po3.t_follow = (datetime)(int)JsonGetNumber(po3_json, "t_follow", 0);
      p.po3.bos_level = JsonGetNumber(po3_json, "bos_level", 0);
      p.po3.sweep_strength = JsonGetNumber(po3_json, "sweep_strength", 0);
      p.po3.displacement_score = JsonGetNumber(po3_json, "displacement_score", 0);
      p.po3.displacement_body_frac = JsonGetNumber(po3_json, "displacement_body_frac", 0);
      p.po3.displacement_range_atr = JsonGetNumber(po3_json, "displacement_range_atr", 0);
      p.po3.displacement_volume_ratio = JsonGetNumber(po3_json, "displacement_volume_ratio", 0);
      p.po3.displacement_speed_score = JsonGetNumber(po3_json, "displacement_speed_score", 0);
      p.po3.displacement_follow_score = JsonGetNumber(po3_json, "displacement_follow_score", 0);
      p.po3.session_name = JsonGetString(po3_json, "session_name", "");
      p.po3.session_code = JsonGetString(po3_json, "session_code", "");
      p.po3.killzone_name = JsonGetString(po3_json, "killzone_name", "");
      p.po3.in_killzone = JsonGetBool(po3_json, "in_killzone", false);
      p.po3.session_start = (datetime)(int)JsonGetNumber(po3_json, "session_start", 0);
      p.po3.session_end = (datetime)(int)JsonGetNumber(po3_json, "session_end", 0);
      p.po3.session_high = JsonGetNumber(po3_json, "session_high", 0);
      p.po3.session_low = JsonGetNumber(po3_json, "session_low", 0);
      p.po3.asia_high = JsonGetNumber(po3_json, "asia_high", 0);
      p.po3.asia_low = JsonGetNumber(po3_json, "asia_low", 0);
      p.po3.london_high = JsonGetNumber(po3_json, "london_high", 0);
      p.po3.london_low = JsonGetNumber(po3_json, "london_low", 0);
      p.po3.newyork_high = JsonGetNumber(po3_json, "newyork_high", 0);
      p.po3.newyork_low = JsonGetNumber(po3_json, "newyork_low", 0);
      p.po3.prev_day_high = JsonGetNumber(po3_json, "prev_day_high", 0);
      p.po3.prev_day_low = JsonGetNumber(po3_json, "prev_day_low", 0);
      p.po3.prev_week_high = JsonGetNumber(po3_json, "prev_week_high", 0);
      p.po3.prev_week_low = JsonGetNumber(po3_json, "prev_week_low", 0);
      p.po3.daily_bias_dir = (int)JsonGetNumber(po3_json, "daily_bias_dir", 0);
      p.po3.h4_bias_dir = (int)JsonGetNumber(po3_json, "h4_bias_dir", 0);
      p.po3.h1_bias_dir = (int)JsonGetNumber(po3_json, "h1_bias_dir", 0);
      p.po3.liquidity_target = JsonGetNumber(po3_json, "liquidity_target", 0);
      p.po3.liquidity_target_high = JsonGetBool(po3_json, "liquidity_target_high", p.is_buy);
      p.po3.liquidity_kind = JsonGetString(po3_json, "liquidity_kind", "");
      p.po3.liquidity_cluster_count = (int)JsonGetNumber(po3_json, "liquidity_cluster_count", 0);
      p.po3.htf_mss = JsonGetBool(po3_json, "htf_mss", false);
      p.po3.htf_choch = JsonGetBool(po3_json, "htf_choch", false);
      p.po3.ltf_bos = JsonGetBool(po3_json, "ltf_bos", false);
      p.po3.ltf_internal_bos = JsonGetBool(po3_json, "ltf_internal_bos", false);
      p.po3.ltf_swing_bos = JsonGetBool(po3_json, "ltf_swing_bos", p.po3.ltf_bos);
      p.po3.ltf_pretrend_dir = (int)JsonGetNumber(po3_json, "ltf_pretrend_dir", 0);
      p.po3.ltf_mss = JsonGetBool(po3_json, "ltf_mss", false);
      p.po3.ltf_choch = JsonGetBool(po3_json, "ltf_choch", false);
      p.po3.ltf_structure_time = (datetime)(int)JsonGetNumber(po3_json, "ltf_structure_time", 0);
      p.po3.ltf_structure_level = JsonGetNumber(po3_json, "ltf_structure_level", 0);

      return true;
   }

   bool AiCacheFromJson(const string json, AiCacheEntry &entry) {
      entry.signature = JsonGetString(json, "signature", "");
      if(StringLen(entry.signature) == 0) return false;
      entry.symbol = JsonGetString(json, "symbol", "");
      entry.allow = JsonGetBool(json, "allow", false);
      entry.score = JsonGetNumber(json, "score", 0);
      entry.chosen_index = (int)JsonGetNumber(json, "chosen_index", 0);
      entry.confidence = JsonGetNumber(json, "confidence", 0);
      entry.reasons_json = JsonGetString(json, "reasons_json", "");
      entry.created_at = (datetime)(int)JsonGetNumber(json, "created_at", 0);
      return true;
   }

   bool PenaltyFromJson(const string json, PenaltyState &st) {
      st.symbol = JsonGetString(json, "symbol", "");
      if(StringLen(st.symbol) == 0) return false;
      st.position_ticket = (long)JsonGetNumber(json, "position_ticket", 0);
      st.opened_at = (datetime)(int)JsonGetNumber(json, "opened_at", 0);
      st.entry = JsonGetNumber(json, "entry", 0);
      st.sl = JsonGetNumber(json, "sl", 0);
      st.risk_dist = JsonGetNumber(json, "risk_dist", 0);
      st.is_buy = JsonGetBool(json, "is_buy", true);
      st.mfe_price = JsonGetNumber(json, "mfe_price", st.entry);
      st.mae_price = JsonGetNumber(json, "mae_price", st.entry);
      st.strikes = (int)JsonGetNumber(json, "strikes", 0);
      st.last_reduction_at = (datetime)(int)JsonGetNumber(json, "last_reduction_at", 0);
      return (st.position_ticket != 0);
   }

public:
   CStateStore(CFileBus &bus) { m_bus = &bus; }

   string WatchlistPath() const { return m_bus.LogDir() + "\\watchlist.ndjson"; }
   string PendingAiPath() const { return m_bus.LogDir() + "\\pending_ai.ndjson"; }
   string PenaltyPath() const { return m_bus.LogDir() + "\\penalty.ndjson"; }
   string AiCachePath() const { return m_bus.LogDir() + "\\ai_cache.ndjson"; }
   string TradePlanToJson(const TradePlan &p) { return PlanToJson(p); }
   bool ParseTradePlanJson(const string json, TradePlan &p) { return PlanFromJson(json, p); }

   bool LoadPlans(const string rel_path, TradePlan &out_arr[]) {
      ArrayResize(out_arr, 0);
      string txt;
      if(!m_bus.ReadText(rel_path, txt)) return false;
      int len = (int)StringLen(txt);
      int start = 0;
       for(int i=0; i<=len; i++){
          if(i==len || StringGetCharacter(txt, i)=='\n'){
             string line = _TrimCopy(StringSubstr(txt, start, i-start));
             start = i+1;
             if(StringLen(line)==0) continue;
            TradePlan p;
            ZeroMemory(p);
            if(PlanFromJson(line, p)){
               int n = ArraySize(out_arr);
               ArrayResize(out_arr, n+1);
               out_arr[n] = p;
            }
         }
      }
      return true;
   }

   bool SavePlans(const string rel_path, const TradePlan &arr[]) {
      string out="";
      for(int i=0; i<ArraySize(arr); i++){
         out += PlanToJson(arr[i]) + "\n";
      }
      return m_bus.WriteText(rel_path, out);
   }

   bool LoadPenaltyStates(const string rel_path, PenaltyState &out_arr[]) {
      ArrayResize(out_arr, 0);
      string txt;
      if(!m_bus.ReadText(rel_path, txt)) return false;
      int len = (int)StringLen(txt);
      int start = 0;
      for(int i=0; i<=len; i++){
         if(i==len || StringGetCharacter(txt, i)=='\n'){
            string line = _TrimCopy(StringSubstr(txt, start, i-start));
            start = i+1;
            if(StringLen(line)==0) continue;
            PenaltyState st;
            ZeroMemory(st);
            if(PenaltyFromJson(line, st)){
               int n = ArraySize(out_arr);
               ArrayResize(out_arr, n+1);
               out_arr[n] = st;
            }
         }
      }
      return true;
   }

   bool SavePenaltyStates(const string rel_path, const PenaltyState &arr[]) {
      string out="";
      for(int i=0; i<ArraySize(arr); i++){
         out += PenaltyToJson(arr[i]) + "\n";
      }
      return m_bus.WriteText(rel_path, out);
   }

   bool LoadAiCacheEntries(const string rel_path, AiCacheEntry &out_arr[]) {
      ArrayResize(out_arr, 0);
      string txt;
      if(!m_bus.ReadText(rel_path, txt)) return false;
      int len = (int)StringLen(txt);
      int start = 0;
      for(int i=0; i<=len; i++){
         if(i==len || StringGetCharacter(txt, i)=='\n'){
            string line = _TrimCopy(StringSubstr(txt, start, i-start));
            start = i+1;
            if(StringLen(line)==0) continue;
            AiCacheEntry entry;
            ZeroMemory(entry);
            if(AiCacheFromJson(line, entry)){
               int n = ArraySize(out_arr);
               ArrayResize(out_arr, n+1);
               out_arr[n] = entry;
            }
         }
      }
      return true;
   }

   bool SaveAiCacheEntries(const string rel_path, const AiCacheEntry &arr[]) {
      string out = "";
      for(int i=0; i<ArraySize(arr); i++){
         out += AiCacheToJson(arr[i]) + "\n";
      }
      return m_bus.WriteText(rel_path, out);
   }
};

#endif
