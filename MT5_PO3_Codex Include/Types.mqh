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

enum ENUM_INTERNAL_ACCOUNT_POSITION_MODE
{
   HEDGING_EXACT_POSITION_ID = 0,
   NETTING_VIRTUAL_SUBPOSITION_LEDGER = 1,
   NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK = 2,
   UNSUPPORTED_ACCOUNT_MODE = 3
};

enum ENUM_SETUP_TAXONOMY
{
   UNKNOWN_UNCLASSIFIED = 0,
   MICRO_FVG_MID_REVERSAL = 1,
   MICRO_FVG_EDGE_REVERSAL = 2,
   MICRO_BREAKER_RETEST = 3,
   MICRO_OTE_REVERSAL = 4,
   MICRO_CONTINUATION_FVG = 5,
   MICRO_NESTED_CONTINUATION = 6,
   MICRO_RANGE_REENTRY = 7,
   MICRO_SESSION_REENTRY = 8,
   FAILED_BREAKOUT_RECLAIM = 9,
   FULL_PO3_REVERSAL = 10,
   FULL_PO3_CONTINUATION = 11
};

string SetupTaxonomyToString(const ENUM_SETUP_TAXONOMY value) {
   if(value == MICRO_FVG_MID_REVERSAL) return "MICRO_FVG_MID_REVERSAL";
   if(value == MICRO_FVG_EDGE_REVERSAL) return "MICRO_FVG_EDGE_REVERSAL";
   if(value == MICRO_BREAKER_RETEST) return "MICRO_BREAKER_RETEST";
   if(value == MICRO_OTE_REVERSAL) return "MICRO_OTE_REVERSAL";
   if(value == MICRO_CONTINUATION_FVG) return "MICRO_CONTINUATION_FVG";
   if(value == MICRO_NESTED_CONTINUATION) return "MICRO_NESTED_CONTINUATION";
   if(value == MICRO_RANGE_REENTRY) return "MICRO_RANGE_REENTRY";
   if(value == MICRO_SESSION_REENTRY) return "MICRO_SESSION_REENTRY";
   if(value == FAILED_BREAKOUT_RECLAIM) return "FAILED_BREAKOUT_RECLAIM";
   if(value == FULL_PO3_REVERSAL) return "FULL_PO3_REVERSAL";
   if(value == FULL_PO3_CONTINUATION) return "FULL_PO3_CONTINUATION";
   return "UNKNOWN_UNCLASSIFIED";
}

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
   double raw_width_price;
   double raw_width_ticks;
   double normalized_min_ticks_price;
   double normalized_min_spread_price;
   double normalized_min_atr_price;
   double normalized_min_session_noise_price;
   double normalized_minimum_price;
   double normalized_minimum_ticks;
   bool   normalized_minimum_shadow_pass;
   bool   normalized_minimum_enforced_pass;
   string normalized_fvg_mode;
   string normalized_fvg_asset_class;
   string normalized_fvg_policy_version;
   string normalized_fvg_policy_source;
   int    normalized_fvg_sample_size;
   bool   normalized_fvg_asset_class_evidence_sufficient;
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
   bool   raw_allow;
   bool   model_raw_allow;
   bool   python_final_allow;
   bool   mql_final_allow;
   string decision_field_authority_json;
   double score;        // migration alias: llm_quality_score only
   int    chosen_index; // candidate selection
   double confidence;   // migration alias: llm_self_reported_confidence only
   string decision_schema_version;
   string decision_quality_tier;
   string response_quality_alias; // read-only migration alias
   string provider_contract_version;
   string provider_mode;
   string provider_id;
   string endpoint_class;
   string endpoint_identity_hash;
   string configured_models_hash;
   string actual_model_id;
   string fallback_model;
   string model_fingerprint;
   string evidence_envelope_version;
   string family_profile_version;
   string memory_schema_version;
   string retrieval_policy_version;
   string role_contract_version;
   string consensus_resolver_version;
   string generation_settings_hash;
   string input_fingerprint;
   string retrieved_analogue_ids_json;
   string historical_evidence_state;
   string analyst_response_fingerprint;
   string critic_response_fingerprint;
   string adjudicator_response_fingerprint;
   string final_resolver_reason;
   string provider_health_state;
   string role_latencies_json;
   string provider_retry_counts_json;
   string provider_usage_json;
   double estimated_context_tokens;
   bool   estimated_context_tokens_available;
   string unsupported_generation_parameters_json;
   string analyst_output_json;
   string critic_output_json;
   string adjudicator_output_json;
   string decision_state;
   bool   mandatory_fields_complete;
   string missing_mandatory_fields_json;
   string invalid_mandatory_fields_json;
   string selected_candidate_id;
   string selected_candidate_hash;
   string request_execution_fingerprint;
   string assessed_execution_fingerprint;
   string selected_target_identity;
   double selected_target_price;
   double assessed_entry;
   double assessed_sl;
   double assessed_tp1;
   double assessed_tp2;
   string candidate_assessments_json;
   double rule_score;
   double llm_quality_score;
   double blended_legacy_score;
   double legacy_agreement_confidence;
   double llm_self_reported_confidence;
   bool   calibration_available;
   double calibrated_win_probability;
   double expected_net_r;
   double oos_predicted_probability;
   string calibration_bucket;
   int    calibration_sample_size;
   double calibration_lower_bound;
   double calibration_upper_bound;
   string calibration_model_version;
   string calibration_data_window_start;
   string calibration_data_window_end;
   string reasons_json; // raw (or summarized) reasons map
   string decision_source;
   string decision_id;
   string rejection_codes_json;
   string narrative_state;
   string invalidation_risks_json;
   string missing_confirmations_json;
   double suggested_risk_multiplier;
   string model_version;
   double llm_quality_score_threshold;
   string llm_quality_threshold_source;
   bool   llm_quality_threshold_passed;
   string llm_quality_reject_reason;
   bool   global_llm_quality_as_hard_floor;
   double structure_quality_score;
   double entry_timing_score;
   double follow_through_probability;
   double invalidation_risk;
   double chop_risk;
   double cost_risk;
   double symbol_bucket_risk;
   double session_bucket_risk;
   double post_entry_failure_risk;
   double final_trade_expectancy_score;
   bool   veto_enabled;
   string veto_code;
   string veto_evidence_fields_json;
   string veto_reason;
   bool   veto_fields_present;
   string llm_numeric_diagnostics_authority;
   string bucket_prior_override_justification;
   string chosen_target_model;
   bool   target_arbitration_required;
   double chosen_tp1;
   double chosen_tp2;
   double chosen_rr1;
   double chosen_rr2;
   string rejected_target_models_json;
   string target_blocker_kind;
   double target_blocker_severity;
   string target_blocker_class;
   bool   target_blocker_is_trade_killer;
   string target_decision_reason;
   bool   target_blocker_severity_present;
   bool   target_blocker_class_present;
   bool   target_blocker_is_trade_killer_present;
   bool   target_decision_reason_present;
   string why_not_liquidity_target;
   string why_not_partial_before_obstacle;
   string why_not_capped_before_obstacle;
   string why_not_synthetic_fallback;
   string target_arbitration_schema_version;
   string prompt_contract_version;
   string target_comparison_json;
   string request_fingerprint;
   string response_fingerprint;
   string full_structured_response_hash;
   string response_binding_hash;
   string contract_manifest_hash;
   string response_request_id;
   string response_session_id;
   string response_request_nonce;
   string request_identity_version;
   string request_identity_hash;
   datetime request_created_sim_time;
   long   request_created_wall_time;
   int    response_candidate_count;
   string ordered_candidate_identities_json;
   string workload_mode;
   string behavior_contract_hash;
   string reasoning_configuration;
   string bucket_prior_hash;
   string calibration_artifact_id;
   string hierarchical_prior_artifact_hash;
   string hierarchical_prior_schema_version;
   string repeatability_schema_version;
   string repeatability_status;
   bool   repeatability_required_live;
   string repeatability_artifact_state;
   string repeatability_rejection_code;
   bool   repeatability_score_threshold_authority;
   bool   repeatability_trading_eligible;
   string repeatability_group_key;
   string repeatability_authority_hash;
};

struct PriceLevelCandidate {
   string kind;
   double price;
   int    priority;
   bool   obstacle;
};

// One evaluated target route considered by the obstacle-aware selector.
//
// The selector used to accept the first target that passed its checks, which let
// a synthetic fallback that crosses a major obstacle win over a clean target that
// merely sat one notch under the RR floor.  Every route is now scored into this
// struct first, so crossing a major obstacle is a hard demotion rather than a
// tie-break, and the whole ranking can be journalled for diagnosis.
struct TargetRankCandidate {
   string kind;                 // target model name written to tp_model/target_source
   double price;                // tp2
   double partial_tp;           // >0 only for partial-then-liquidity routing (tp1 leg)
   double rr;                   // reward-to-risk measured to `price`
   // The first leg has to be scored on its own terms.  Admitting and ranking a
   // partial-then-liquidity route on the *runner* alone let a 0.0108R partial win
   // on the strength of a 45.67R leg it was never going to take, and the reject
   // reason named after the partial leg never measured it.
   double partial_rr;           // reward-to-risk of `partial_tp` (0 when no partial leg)
   bool   partial_meets_floor;  // partial leg clears the execution layer's minimum TP1 reward
   bool   crosses_obstacle;     // reward path passes through an opposing obstacle
   double obstacle_severity;    // _ObstacleSeverity of the obstacle on the path
   string obstacle_kind;
   double obstacle_price;
   bool   meets_rr_floor;
   bool   meets_min_distance;
   bool   truncated_by_obstacle;// price was clamped back to the obstacle
   int    order;                // insertion order, preserves original priority
   string reject_reason;        // "" when eligible
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
   // True when the selected target model owns the first leg (partial-before-obstacle,
   // an AI-arbitrated tp1, or an approved assessed plan).  _BuildPlanPrices' generic
   // R-multiple TP1 builder must then leave tp1 alone: its 0.65R floor used to move a
   // partial that existed to stop *in front of* an obstacle to a price *beyond* it,
   // so the shipped plan contradicted its own tp_model and its own target_candidates.
   bool   tp1_from_target_model;
   // Minimum reward the execution layer will accept for a first leg, published by
   // _MinTp1Reward so route selection, price building AND the AI target-candidate
   // payload all judge a partial leg by the same number.  Three independent places
   // used to decide this; only one of them actually measured the leg.
   double min_tp1_reward;

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
   ENUM_SETUP_TAXONOMY setup_taxonomy;
   string setup_taxonomy_version;
   string setup_taxonomy_enum;
   string taxonomy_mapping_source;
   string taxonomy_mapping_failure_reason;
   string setup_type;
   string setup_subtype;
   string setup_story_scope;
   string fvg_execution_class;
   string model_code;
   string session_code;
   string killzone_code;
   string broker_comment;
   string input_snapshot_hash;
   string engine_version;
   string git_commit;
   string dirty_tree_status;
   string set_file_hash;
   string runtime_input_hash;
   string reasoning_configuration;
   string policy_hash;
   string bucket_prior_hash;
   string calibration_artifact_id;
   string repeatability_artifact_id;
   string feature_version;
   string cohort_id;
   bool   cohort_complete;
   string request_fingerprint;
   string response_fingerprint;
   string hierarchical_prior_artifact_hash;
   string hierarchical_prior_schema_version;
   string repeatability_status;
   bool   repeatability_required_live;
   string repeatability_artifact_state;
   string repeatability_rejection_code;
   bool   repeatability_score_threshold_authority;
   bool   repeatability_trading_eligible;
   string repeatability_group_key;
   string repeatability_authority_hash;
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
   string estimated_cost_source;
   int    estimated_cost_sample_size;
   double estimated_cost_stressed_per_lot;
   double actual_realized_cost;
   double cost_prediction_error;
   string commission_model_version;
   double spread_r;
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
   double heuristic_quality_estimate;
   double heuristic_quality_estimate_gross;
   double net_reward_after_cost_r;
   bool   runner_trade;
   bool   runner_downgraded;
   string runner_downgrade_reason;
   double original_runner_target;
   double standard_target_after_downgrade;
   string target_model;
   bool   target_arbitration_required;
   double liquidity_target_preserved;
   string liquidity_target_model;
   bool   liquidity_target_valid_structurally;
   bool   liquidity_target_blocked_by_obstacle;
   double obstacle_distance_r;
   string obstacle_tf;
   // The numeric severity behind obstacle_strength_features.  Kept as a number so
   // the execution contract can compare "is the live blocker worse than the one the
   // AI approved?" without parsing the display string.
   double obstacle_severity;
   string obstacle_strength_features;
   double fallback_tp;
   double fallback_rr;
   string fallback_source;
   bool   fallback_feasible_for_tp2;
   bool   fallback_feasible_for_tp1_only;
   string fallback_infeasible_reason;
   double fallback_reward_distance;
   double fallback_max_allowed_distance;
   double synthetic_capped_to_max_distance_tp;
   double synthetic_capped_to_max_distance_rr;
   bool   synthetic_capped_to_max_distance_feasible;
   string synthetic_capped_to_max_distance_reason;
   double capped_before_obstacle_tp;
   double capped_before_obstacle_rr;
   string capped_before_obstacle_source;
   double original_planned_tp_before_ai;
   double original_planned_rr_before_ai;
   string ai_chosen_target_model;
   double ai_chosen_tp1;
   double ai_chosen_tp2;
   double ai_chosen_rr2;
   string ai_rejected_target_models;
   double ai_blocker_severity;
   string ai_blocker_class;
   bool   ai_blocker_is_trade_killer;
   string target_decision_reason;
   bool   target_arbitration_normalized_valid;
   string target_arbitration_schema_version;
   string prompt_contract_version;
   string why_not_liquidity_target;
   string why_not_partial_before_obstacle;
   string why_not_capped_before_obstacle;
   string why_not_synthetic_fallback;
   string target_comparison_json;
   string original_target_candidates_json;
   string analytics_key;
   string bucket_policy_action;
   string bucket_policy_reason;
   string bucket_policy_key;
   double bucket_policy_risk_multiplier;
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
   string risk_factor_schema_version;
   string risk_factor_contributions_json;
   double original_initial_risk_money;
   double original_initial_risk_money_per_lot;
   double original_entry_for_risk;
   double original_sl_for_risk;
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
   double active_policy_risk_multiplier;
   bool   risk_multipliers_initialized;
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
   // Durable request binding for restart recovery (REC-003).  A response echoes
   // the session id and nonce the request was created under; both are minted per
   // process, so they must survive the restart or a pre-restart response can
   // never validate.
   string request_session_id;
   string request_nonce;
   AiDecision ai;
   bool   model_raw_allow;
   bool   python_final_allow;
   bool   mql_final_allow;
   string decision_field_authority_json;
   string python_decision_reasons;
   string mql_decision_reasons;
   int    candidate_index;
   int    candidate_count;
   string trade_key;
   string setup_id;
   string fvg_id;
   string candidate_id;
   string candidate_hash;
   string ai_selected_candidate_hash;
   string executed_candidate_hash;
   bool   candidate_hash_match;
   string request_execution_fingerprint;
   string assessed_execution_fingerprint;
   string final_execution_fingerprint;
   bool   execution_fingerprint_match;
   string execution_fingerprint_changed_components;
   double assessed_entry;
   double assessed_sl;
   double assessed_tp1;
   double assessed_tp2;
   double assessed_net_rr;
   double assessed_spread_r;
   double assessed_slippage_r;
   double assessed_execution_cost_r;
   string assessed_symbol;
   bool   assessed_is_buy;
   string assessed_setup_code;
   string assessed_setup_family;
   string assessed_setup_taxonomy_enum;
   string assessed_setup_taxonomy_version;
   string assessed_taxonomy_mapping_source;
   string assessed_entry_branch;
   datetime assessed_source_t_sweep;
   datetime assessed_source_t_disp;
   datetime assessed_source_t_bos;
   string assessed_target_source;
   string assessed_target_model;
   string assessed_obstacle_kind;
   string assessed_obstacle_tf;
   double assessed_obstacle_price;
   double assessed_obstacle_severity;
   //--- What the live landscape scan saw while the plan was locked.  Recorded
   //--- instead of overwriting the approved obstacle, so the revalidation can
   //--- compare the two rather than the fingerprint tripping on the label.
   string live_obstacle_kind;
   double live_obstacle_price;
   double live_obstacle_severity;
   string assessed_decision_input_hash;
   string assessed_strategy_schema_version;
   //--- AssessedTradePlan lock -------------------------------------------
   // Set once Python approves and MQL applies the approved target.  While it
   // is set, the live rebuild may not re-derive the target: it must carry the
   // approved identity and price forward through the execution adjustment
   // contract.  Without this the rebuild produced a materially different trade
   // and then rejected it for differing from the approved one.
   bool   assessed_plan_locked;
   string assessed_tp_model;
   string assessed_selected_target_identity;
   double assessed_selected_target_price;
   double assessed_stop_distance;
   //--- Execution adjustment validation (LiveExecutionPlan) --------------
   bool   semantic_plan_match;
   bool   execution_adjustment_valid;
   string semantic_immutable_fields_changed;
   string semantic_authorized_fields_changed;
   string semantic_unauthorized_fields_changed;
   string execution_adjustment_bounds;
   string execution_adjustment_reason;
   //--- Execution retry suppression ---------------------------------------
   string execution_failure_class;
   string execution_failure_state_fingerprint;
   int    execution_precheck_attempts;
   int    execution_order_construction_attempts;
   int    execution_attempts_suppressed;
   datetime execution_retry_not_before;
   // The spread measured at the last spread rejection, and how many consecutive
   // rejections have carried that same value.  A spread is only "transient" while
   // it is actually moving; retrying an unchanged one reproduces the rejection.
   double execution_failure_spread;
   int    execution_spread_unchanged_attempts;
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
   datetime setup_snapshot_time;
   datetime ai_request_time;
   datetime ai_advisory_time;
   int    ai_result_age_sim_minutes;
   bool   tester_ai_result_stale;
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
   ulong  result_order_ticket;
   ulong  result_deal_ticket;
   ulong  broker_position_ticket;
   long   broker_position_identifier;
   string intended_order_type;
   string execution_authority_state;
   bool   broker_submission_attempted;
   bool   broker_request_accepted;
   long   broker_retcode;
   string broker_retcode_description;
   bool   broker_partial_fill;
   bool   final_execution_success;
   bool   execution_identity_verified;
   bool   execution_identity_quarantined;
   string execution_identity_reason;
   string account_position_mode;
   string attribution_status;
   string attribution_error;
   bool   learning_eligible;
   bool   optimization_eligible;
   bool   suppression_eligible;
   string ledger_integrity_status;
   string ledger_integrity_reasons;
   string ledger_schema_version;
   double account_equity_at_entry;
   double account_balance_at_entry;
   double initial_risk_money;
   double initial_risk_pct_equity;
   double gross_price_pnl;
   double total_commission;
   double total_swap;
   double total_fees;
   double broker_net_pnl;
   double internal_net_pnl;
   double pnl_reconciliation_difference;
   double result_pct_fixed_initial_balance;
   double result_pct_equity_at_entry;
   double result_r_initial_risk;
   string outcome_direction_broker;
   string outcome_direction_r;
   string outcome_direction_equity_pct;
   bool   outcome_direction_match;
   string outcome_reconciliation_status;
   string management_version;
   string management_state;
   string management_previous_state;
   datetime management_transition_time;
   string management_transition_reason;
   string management_evidence_snapshot_json;
   string management_action_executed;
   string management_action_id;
   string management_action_lifecycle_state;
   string management_requested_action;
   double management_requested_volume;
   double management_normalized_volume;
   double management_position_volume_before;
   double management_requested_cut_fraction;
   int    management_action_retry_count;
   datetime management_next_retry_at;
   long   management_last_retcode;
   string management_last_retcode_description;
   string management_action_terminal_reason;
   string management_policy;
   datetime management_decision_at;
   bool   management_features_time_safe;
   string management_snapshot_action;
   double management_snapshot_mfe_r;
   double management_snapshot_mae_r;
   int    management_snapshot_minutes_open;
   double management_snapshot_distance_to_sl_r;
   double management_snapshot_distance_to_tp_r;
   double management_snapshot_spread_r;
   double management_snapshot_execution_cost_r;
   bool   management_snapshot_structure_valid;
   string invalidation_confirmation_mode;
   int    invalidation_reference_timeframe;
   double invalidation_trigger_level;
   double invalidation_spread;
   double invalidation_buffer;
   datetime invalidation_first_breach_time;
   datetime invalidation_confirmed_time;
   datetime invalidation_confirming_bar;
   double actual_managed_result;
   double actual_managed_result_r;
   double counterfactual_original_sl_tp_result;
   double counterfactual_original_sl_tp_result_r;
   bool   counterfactual_target_before_stop_available;
   bool   counterfactual_target_before_stop;
   double management_alpha;
   bool   counterfactual_ambiguous;
   bool   counterfactual_pending;
   string counterfactual_status;
   string counterfactual_resolution_reason;
   datetime counterfactual_horizon_at;
   datetime counterfactual_evaluated_at;
   bool   management_policy_selection_eligible;
   string broker_session_schedule_json;
   string broker_session_source;
   datetime broker_session_open;
   datetime broker_session_close;
   datetime broker_no_entry_from;
   datetime broker_flatten_from;
   datetime broker_next_tradable_session;
   int    broker_flatten_attempts;
   int    broker_flatten_failures;
   long   broker_flatten_last_retcode;
   datetime broker_flatten_next_retry;
   string shadow_candidate_record_hash;
   string shadow_candidate_schema_version;
   string shadow_decision_stage;
   string shadow_rejection_reason;
   datetime shadow_observed_at;
   datetime shadow_horizon_at;
   datetime shadow_evaluated_at;
   string shadow_outcome_status;
   string shadow_outcome_reason;
   double shadow_outcome_r;
   double shadow_mfe_r;
   double shadow_mae_r;
   int    shadow_time_to_event_sec;
   int    shadow_time_to_025r_sec;
   int    shadow_time_to_050r_sec;
   int    shadow_time_to_stop_sec;
   int    shadow_time_to_target_sec;
   bool   shadow_reached_025r;
   bool   shadow_reached_050r;
   bool   shadow_reached_025r_before_adverse;
   bool   shadow_reached_050r_before_adverse;
   int    shadow_time_to_adverse_threshold_sec;
   bool   shadow_025_order_ambiguous;
   bool   shadow_050_order_ambiguous;
   bool   shadow_target_before_stop;
   bool   shadow_stop_before_target;
   bool   shadow_mfe_before_adverse;
   string shadow_horizon_result;
   string shadow_censoring_status;
   string shadow_ambiguity_reason;
   bool   shadow_threshold_order_ambiguous;
   bool   shadow_outcome_ambiguous;
   //--- Shadow counterfactual lifecycle v4.  Research only: nothing below ever
   //--- carries trading authority.  The identity pair (opportunity, variant) is
   //--- what makes re-scans, AI retries and EA restarts idempotent, and the
   //--- entry-activation block is what stops a level touched before the
   //--- hypothetical entry was ever reached from being counted as a result.
   string shadow_sweep_opportunity_id;
   string shadow_sweep_opportunity_lineage;
   string shadow_candidate_variant_id;
   string shadow_variant_parent_id;
   int    shadow_variant_revision;
   int    shadow_observation_count;
   bool   shadow_plan_locked;
   datetime shadow_scan_cursor;
   datetime shadow_last_evaluated_at;
   int    shadow_data_retry_count;
   int    shadow_progress_mask;
   bool   shadow_entry_activated;
   datetime shadow_entry_activated_at;
   int    shadow_time_to_entry_sec;
   double shadow_entry_touch_price;
   bool   shadow_entry_order_ambiguous;
   bool   shadow_entry_never_reached;
   bool   shadow_tp1_hit;
   datetime shadow_tp1_hit_at;
   int    shadow_time_to_tp1_sec;
   bool   shadow_tp1_before_sl;
   bool   shadow_sl_before_tp1;
   bool   shadow_tp2_hit;
   datetime shadow_tp2_hit_at;
   int    shadow_time_to_tp2_sec;
   bool   shadow_tp2_before_sl;
   bool   shadow_sl_before_tp2;
   bool   shadow_tp1_then_sl;
   bool   shadow_tp1_then_tp2;
   bool   shadow_neither_target_nor_stop;
   double shadow_max_favorable_price;
   double shadow_max_adverse_price;
   double shadow_result_r_unmanaged;
   double shadow_result_r_tp1_partial;
   double shadow_tp1_partial_fraction;
   string shadow_terminal_event;
   datetime shadow_terminal_event_at;
   string shadow_data_quality_status;
   string shadow_ordering_source;
   string shadow_ambiguity_status;
   double shadow_assessed_entry;
   double shadow_assessed_sl;
   double shadow_assessed_tp1;
   double shadow_assessed_tp2;
   string shadow_decision_state;
   string shadow_decision_source;
   double mfe_price;
   double mae_price;
   double mfe_r;
   double mae_r;
   int    minutes_to_0_25r_mfe;
   int    minutes_to_0_50r_mfe;
   datetime first_0_25r_time;
   datetime first_0_50r_time;
   datetime first_adverse_threshold_time;
   datetime latest_observed_tick_time;
   long   latest_observed_tick_msc;
   string path_completeness_status;
   string path_observation_source;
   bool   path_data_gap;
   bool   path_order_ambiguous;
   bool   stuck_no_mfe_triggered;
   bool   dr_and_structural_invalid_triggered;
   int    penalty_reductions_count;
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
   string decision_schema_version;
   string decision_quality_tier;
   string response_quality_alias;
   string selected_candidate_hash;
   string assessed_execution_fingerprint;
   bool   allow;
   double score;
   int    chosen_index;
   double confidence;
   string reasons_json;
   datetime created_at;
};

struct PenaltyState {
   string symbol;
   long   position_identifier; // lifecycle identity: DEAL_POSITION_ID/POSITION_IDENTIFIER
   ulong  position_ticket;     // broker operation handle only
   datetime opened_at;
   double entry;
   double sl;
   double risk_dist;  // |entry - sl|
   bool   is_buy;

   double mfe_price;  // best favorable price seen
   double mae_price;  // worst adverse price seen
   double mfe_r;
   double mae_r;
   datetime first_0_25r_time;
   datetime first_0_50r_time;
   datetime first_adverse_threshold_time;
   datetime latest_observed_tick_time;
   long   latest_observed_tick_msc;
   string path_completeness_status;
   string path_observation_source;
   bool   path_data_gap;
   bool   path_order_ambiguous;
   datetime position_closed_observed_at;

   int    strikes;
   datetime last_reduction_at;
   string current_state;
   string previous_state;
   datetime transition_time;
   string transition_reason;
   string evidence_snapshot_json;
   string action_executed;
   string action_id;
   string action_lifecycle_state;
   string requested_action;
   double requested_volume;
   double normalized_volume;
   double action_position_volume_before;
   double requested_cut_fraction;
   int    action_retry_count;
   datetime next_eligible_retry_time;
   datetime action_last_attempt_at;
   long   action_last_retcode;
   string action_last_retcode_description;
   string action_terminal_reason;
   string management_version;
   string executed_action_ids;
   string confirmation_mode;
   int    confirmation_timeframe;
   double confirmation_trigger_level;
   double confirmation_spread;
   double confirmation_buffer;
   datetime first_breach_time;
   datetime confirmed_time;
   datetime confirming_bar;
};

#endif
