//+------------------------------------------------------------------+
//| Types.mqh - shared structs                                       |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_TYPES_MQH__
#define __PO3_AIGATE_TYPES_MQH__
#include <Trade/Trade.mqh>

enum PO3State
{
   PO3_IDLE = 0,
   PO3_RANGE_DEFINED = 1,
   PO3_SWEEP_CONFIRMED = 2,
   PO3_DISPLACEMENT_CONFIRMED = 3,
   PO3_STRUCTURE_CONFIRMED = 4,
   PO3_FVG_CONFIRMED = 5,
   PO3_ENTRY_WAITING = 6,
   PO3_CONFIRMED = 7,
   PO3_DEVELOPING = 8,
   PO3_EXPIRED = 9,
   PO3_INVALIDATED = 10
};

struct FVGZone {
   bool   bullish;
   datetime t_form;
   double lower;    // price lower boundary
   double upper;    // price upper boundary
   double mid;
   int    idx_newer; // bar index (series) of the newest bar in the 3-candle pattern
   bool   mitigated;
   bool   touched;
   bool   mid_mitigated;
   bool   fully_filled;
   bool   invalidated;
   bool   entry_invalid;
   bool   structure_invalidated;
   bool   continuation;
   bool   reversal;
   string mitigation_state;
   string invalidation_reason;
   string context_type;
   double origin_score;
   double cleanliness_score;
   double age_score;
   double nesting_score;
   double htf_overlap_score;
   double retest_score;
   double continuation_score;
   double reversal_score;
   double displacement_candle_score;
   double middle_candle_body_score;
   double volume_impulse_score;
   double gap_width_atr_score;
   double manipulation_distance_score;
   double premium_discount_score;
   double htf_nesting_score;
   double freshness_score;
   double retest_quality_score;
   double opposing_obstruction_score;
   double score;
   string execution_class;
   double mitigation_depth_frac;
   int    age_bars;
};

struct PO3Context {
   bool   valid;
   PO3State state;
   string po3_state;        // enum label, e.g. PO3_CONFIRMED
   string po3_state_reason;
   string sweep_side;       // buy_side / sell_side / running_buy_side / running_sell_side
   string structure_type;   // continuation_bos / reversal_mss / choch / internal_ltf_bos / htf_displacement_confirmation
   string htf_structure_type;
   string ltf_structure_type;
   string final_setup_class;
   string po3_scope;        // micro_po3 / intraday_po3 / institutional_po3
   bool   bias_long;
   bool   bias_short;
   double context_score;
   double dr_high;
   double dr_low;
   double dr_mid;
   double manip_low;
   double manip_high;
   bool   sweep_running;
   bool   has_sweep;
   bool   has_displacement;
   bool   has_bos;
   bool   has_follow_through;
   bool   htf_bos;
   bool   htf_internal_bos;
   bool   htf_swing_bos;
   bool   developing_bos;
   string context_tier;
   int    context_age_bars;
   int    htf_pretrend_dir;
   datetime t_sweep;
   datetime t_disp;
   datetime t_bos;
   datetime t_follow;
   double bos_level;
   double sweep_strength;
   double displacement_score;
   double displacement_body_frac;
   double displacement_range_atr;
   double displacement_volume_ratio;
   double displacement_speed_score;
   double displacement_follow_score;
   string session_name;
   string session_code;
   string killzone_name;
   bool   in_killzone;
   datetime session_start;
   datetime session_end;
   double session_high;
   double session_low;
   double asia_high;
   double asia_low;
   double london_high;
   double london_low;
   double newyork_high;
   double newyork_low;
   double prev_day_high;
   double prev_day_low;
   double prev_week_high;
   double prev_week_low;
   int    daily_bias_dir;
   int    h4_bias_dir;
   int    h1_bias_dir;
   double liquidity_target;
   bool   liquidity_target_high;
   string liquidity_kind;
   int    liquidity_cluster_count;
   bool   htf_mss;
   bool   htf_choch;
   bool   ltf_bos;
   bool   ltf_internal_bos;
   bool   ltf_swing_bos;
   int    ltf_pretrend_dir;
   bool   ltf_mss;
   bool   ltf_choch;
   datetime ltf_structure_time;
   double ltf_structure_level;
   // for OTE / swing references
   double swing_low;
   double swing_high;
};

string PO3StateToString(const PO3State state) {
   if(state == PO3_RANGE_DEFINED) return "PO3_RANGE_DEFINED";
   if(state == PO3_SWEEP_CONFIRMED) return "PO3_SWEEP_CONFIRMED";
   if(state == PO3_DISPLACEMENT_CONFIRMED) return "PO3_DISPLACEMENT_CONFIRMED";
   if(state == PO3_STRUCTURE_CONFIRMED) return "PO3_STRUCTURE_CONFIRMED";
   if(state == PO3_FVG_CONFIRMED) return "PO3_FVG_CONFIRMED";
   if(state == PO3_ENTRY_WAITING) return "PO3_ENTRY_WAITING";
   if(state == PO3_CONFIRMED) return "PO3_CONFIRMED";
   if(state == PO3_DEVELOPING) return "PO3_DEVELOPING";
   if(state == PO3_EXPIRED) return "PO3_EXPIRED";
   if(state == PO3_INVALIDATED) return "PO3_INVALIDATED";
   return "PO3_IDLE";
}

PO3State PO3StateFromString(string value) {
   StringToUpper(value);
   if(value == "PO3_RANGE_DEFINED" || value == "RANGE_DEFINED") return PO3_RANGE_DEFINED;
   if(value == "PO3_SWEEP_CONFIRMED" || value == "SWEEP_CONFIRMED") return PO3_SWEEP_CONFIRMED;
   if(value == "PO3_DISPLACEMENT_CONFIRMED" || value == "DISPLACEMENT_CONFIRMED") return PO3_DISPLACEMENT_CONFIRMED;
   if(value == "PO3_STRUCTURE_CONFIRMED" || value == "STRUCTURE_CONFIRMED") return PO3_STRUCTURE_CONFIRMED;
   if(value == "PO3_FVG_CONFIRMED" || value == "FVG_CONFIRMED") return PO3_FVG_CONFIRMED;
   if(value == "PO3_ENTRY_WAITING" || value == "ENTRY_WAITING") return PO3_ENTRY_WAITING;
   if(value == "PO3_CONFIRMED" || value == "CONFIRMED") return PO3_CONFIRMED;
   if(value == "PO3_DEVELOPING" || value == "DEVELOPING") return PO3_DEVELOPING;
   if(value == "PO3_EXPIRED" || value == "EXPIRED") return PO3_EXPIRED;
   if(value == "PO3_INVALIDATED" || value == "INVALIDATED") return PO3_INVALIDATED;
   return PO3_IDLE;
}

void PO3SetState(PO3Context &ctx, const PO3State state, const string reason="") {
   ctx.state = state;
   ctx.po3_state = PO3StateToString(state);
   if(StringLen(reason) > 0) ctx.po3_state_reason = reason;
}

bool PO3IsTerminalBadState(const PO3Context &ctx) {
   return (ctx.state == PO3_EXPIRED || ctx.state == PO3_INVALIDATED ||
           ctx.po3_state == "PO3_EXPIRED" || ctx.po3_state == "PO3_INVALIDATED" ||
           ctx.po3_state == "EXPIRED" || ctx.po3_state == "INVALIDATED");
}

struct AiDecision {
   bool   ok;           // was a response received & parsed
   bool   allow;        // AI verdict
   double score;        // 0..10
   int    chosen_index; // candidate selection
   double confidence;   // 0..1 confidence in the decision
   string reasons_json; // raw (or summarized) reasons map
   string decision_source;
   string decision_id;
   string rejection_codes_json;
   string narrative_state;
   string invalidation_risks_json;
   string missing_confirmations_json;
   double suggested_risk_multiplier;
   string model_version;
};

struct PriceLevelCandidate {
   string kind;
   double price;
   int    priority;
   bool   obstacle;
};

struct ActivePolicySnapshot {
   bool     valid;
   string   policy_id;
   int      version;
   datetime activated_at;
   double   evidence_score;
   bool     evidence_passed;
   bool     walk_forward_passed;
   bool     change_rate_passed;
   double   soft_setup_floor;
   double   hard_setup_floor;
   double   setup_floor_penalty_mult;
   double   ote_softness_frac;
   double   default_risk_multiplier;
   double   runner_sequence_floor;
   double   runner_liquidity_rr_floor;
   double   runner_cost_r_ceiling;
   double   runner_alignment_floor;
   double   runner_adverse_ceiling;
   double   runner_ev_floor;
};

struct SubtypePolicyEntry {
   string subtype_key;
   string action;
   double score_penalty;
   double risk_multiplier;
   double shrunk_win_rate;
   double avg_r;
   double evidence_score;
   int    sample_count;
   string policy_id;
};

struct ContextPolicyEntry {
   string policy_bucket;
   string action;
   double score_bias;
   double risk_multiplier;
   double expected_value_bias;
   int    sample_count;
   string policy_id;
};

struct SessionWeekdayPolicyEntry {
   string session_name;
   string weekday;
   string action;
   double risk_multiplier;
   double rr_floor_delta;
   double score_bias;
   int    sample_count;
   string policy_id;
};

struct TradePlan {
   string symbol;
   bool   is_buy;
   ENUM_TIMEFRAMES htf;
   ENUM_TIMEFRAMES ltf;
   ENUM_TIMEFRAMES confirm_tf;

   // Entry model
   double entry_est;
   double sl;
   double tp2;
   double tp1;

   // Context
   PO3Context po3;
   FVGZone    fvg;

   // Gates
   double atr_pct;
   double trend_strength;
   double trend_slope_pct;
   double adx_value;
   double adr_pct;
   double session_vol_ratio;
   double vwap_dist_atr;
   double compression_score;
   double expansion_score;
   double news_risk;
   string entry_model;
   string entry_branch;
   string tp_model;
   string target_source;
   double setup_score;
   string setup_family;
   string setup_class;
   string fvg_execution_class;
   string model_code;
   string session_code;
   string killzone_code;
   string broker_comment;
   string input_snapshot_hash;
   string origin_quality;
   bool   exclusive_model_mode;
   string exclusive_model_name;
   bool   exclusive_model_passed;
   string exclusive_fail_reason;
   string management_profile;
   string asset_class;
   string regime_profile;
   string volatility_profile;
   string policy_bucket;
   string obstacle_kind;
   double obstacle_price;
   double obstacle_r;
   double effective_rr2;
   double estimated_cost_price;
   double estimated_slippage_price;
   double estimated_commission_money;
   double execution_cost_r;
   double slippage_r;
   double commission_r;
   bool   execution_cost_risk_reduced;
   double execution_cost_risk_multiplier;
   double gross_expected_r;
   double net_expected_r;
   double liquidity_rr;
   double sequence_quality;
   double htf_alignment_score;
   double adverse_context_score;
   double expected_value_r;
   bool   runner_trade;
   bool   runner_downgraded;
   string runner_downgrade_reason;
   double original_runner_target;
   double standard_target_after_downgrade;
   string target_model;
   string analytics_key;
   string symbol_policy_action;
   string symbol_policy_reason;
   string family_policy_action;
   string family_policy_reason;
   string entry_miss_classification;
   double portfolio_score;
   int    portfolio_rank;
   string portfolio_cluster;
   string usd_exposure_key;
   string scheduler_action;
   string scheduler_reason;
   double portfolio_risk_weight;
   double session_concentration;
   double usd_concentration;
   double cluster_concentration;
   double diversity_bonus;
   string stop_floor_reason;
   double stop_floor_distance;
   double stop_noise_band;
   double broker_min_stop_distance;
   bool   stop_microstructure_distortion;
   double stop_quality_score;
   double realized_stop_quality;
   double realized_stop_buffer_r;
   double ote_distance_frac;
   double ote_softness_frac;
   string ote_state;
   string subtype_policy_action;
   double subtype_policy_penalty;
   double subtype_shrunk_win_rate;
   double subtype_avg_r;
   double subtype_risk_multiplier;
   double setup_floor_score;
   double setup_floor_penalty;
   string setup_floor_action;
   string policy_snapshot_id;
   string session_weekday_policy_action;
   double session_weekday_risk_multiplier;
   double session_weekday_rr_delta;
   double session_weekday_score_bias;

   // AI
   string req_id;
   AiDecision ai;
   int    candidate_index;
   int    candidate_count;
   string trade_key;
   string setup_id;
   string fvg_id;
   string candidate_id;
   string ai_decision_id;
   string policy_version;
   string risk_version;
   string reject_code;
   datetime source_t_sweep;
   datetime source_t_disp;
   datetime source_t_bos;
   string source_context_tier;
   string source_sweep_side;
   double source_manip_low;
   double source_manip_high;
   double source_dr_high;
   double source_dr_low;
   double source_liquidity_target;
   string source_liquidity_kind;
   string lineage_root_id;
   string parent_setup_id;
   int    lineage_version;
   int    attempt_number_for_sweep;
   string narrative_state;
   string superseded_by;
   string invalidation_cause;
   string snapshot_htf_path;
   string snapshot_ltf_path;
   datetime created_at;
   datetime ai_requested_at;
   ulong  ai_requested_wall_ms;
   datetime last_score_refresh;
   string ai_decision_source;

   // Watchlist / confirmation
   bool   armed;
   bool   mid_touched;
   bool   b50_touched;
   int    bars_waited;
   int    arm_max_bars;
   int    max_watch_minutes;
   datetime last_confirm_bar_time;
   datetime armed_at;

   // Setup-class lifecycle / management
   double tp1_r_multiple;
   double tp1_partial_pct;
   string be_rule;
   double be_trigger_r;
   double be_offset_points;
   double stop_buffer_points;
   double penalty_mae_trigger_r;
   double penalty_giveback_trigger_r;
   double penalty_giveback_floor_r;
   int    penalty_stuck_minutes;
   double penalty_stuck_min_mfe_r;
   double penalty_dr_invalid_cut_pct;
   double penalty_fvg_invalid_cut_pct;
   int    penalty_close_strikes;

   // Position lifecycle
   bool   tp1_done;

   // Analytics / empirical validation
   double planned_entry;
   double planned_sl;
   double planned_tp1;
   double planned_tp2;
   datetime planned_at;
   double filled_entry;
   datetime filled_at;
   double fill_slippage;
   double fill_slippage_r;
   double initial_volume;
   long   position_id;
   double mfe_price;
   double mae_price;
   double mfe_r;
   double mae_r;
   datetime closed_at;
   double realized_pnl;
   double realized_r;
   string exit_path;
   bool   analytics_logged;
   datetime tp1_hit_at;
   datetime tp2_hit_at;
   datetime sl_hit_at;
   bool   tp2_realistic_before_reversal;
   bool   be_move_helped;
   bool   trailing_stop_improved;
   double target_efficiency;
};

struct AiCacheEntry {
   string signature;
   string symbol;
   bool   allow;
   double score;
   int    chosen_index;
   double confidence;
   string reasons_json;
   datetime created_at;
};

struct PenaltyState {
   string symbol;
   long   position_ticket;
   datetime opened_at;
   double entry;
   double sl;
   double risk_dist;  // |entry - sl|
   bool   is_buy;

   double mfe_price;  // best favorable price seen
   double mae_price;  // worst adverse price seen

   int    strikes;
   datetime last_reduction_at;
};

#endif
