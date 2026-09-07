//+------------------------------------------------------------------+
//| TradeEngine.mqh - build plans, manage watchlist, execute trades    |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_TRADEENGINE_MQH__
#define __PO3_AIGATE_TRADEENGINE_MQH__
#include <Trade/Trade.mqh>
#include "Config.mqh"
#include "Types.mqh"
#include "ExecutionAdjustmentContract.mqh"
#include "Indicators.mqh"
#include "PO3.mqh"
#include "FVG.mqh"
#include "Risk.mqh"
#include "AIGateBridge.mqh"
#include "StateStore.mqh"
#include "PenaltyWatcher.mqh"
#include "JsonLite.mqh"

// The broker boundary is the only thing a harness build replaces. Every stage
// above it -- response parsing, identity validation, candidate binding, target
// arbitration, watchlist admission, confirmation, risk sizing, and order
// construction -- stays the production implementation in all builds.
// PO3_TEST_ORDER_ADAPTER is never defined by a production compile, so a
// production binary is unchanged.
#ifdef PO3_TEST_ORDER_ADAPTER
   #include "TestOrderAdapter.mqh"
   #define PO3_TRADE_CLASS CPO3TestTrade
#else
   #define PO3_TRADE_CLASS CTrade
#endif

class CTradeEngine {
private:
   PO3_TRADE_CLASS m_trade;
   CFileBus m_bus;
   CAIGateBridge m_ai;
   CStateStore m_state;

   CPO3 m_po3;
   CFVG m_fvg;
   CPenaltyWatcher m_penalty;

   TradePlan m_watchlist[];
   TradePlan m_pending_ai[];
   TradePlan m_scan_candidates[];
   TradePlan m_counterfactual_pending[];
   TradePlan m_shadow_pending[];
   string m_ai_cooldown_symbols[];
   string m_ai_cooldown_signatures[];
   datetime m_ai_cooldown_until[];
   string m_tester_ai_cache_signatures[];
   AiDecision m_tester_ai_cache_decisions[];
   // Replay cohort probe: which decision_input_hash the artifacts on disk were
   // recorded under, sampled once.  Without it a whole-cohort mismatch is
   // indistinguishable from thousands of individually unrecorded setups.
   string m_tester_cache_cohort_summary;
   string m_tester_cache_cohort_dominant;
   bool   m_tester_cache_cohort_probed;
   bool   m_tester_cache_cohort_matches;
   // The histogram above is a bounded sample, but "no artifact carries my
   // identity" is used to abort a run, so it must be a census rather than a
   // guess: a cohort of 5 inside 1,497 artifacts would be invisible to a
   // 200-file sample.  Scanning past the sample cap only ever happens when no
   // match has been found yet, i.e. in the run that is about to be rejected.
   int    m_tester_cache_cohort_total;
   int    m_tester_cache_cohort_sampled;
   string m_tester_recorded_cache_signatures[];
   string m_tester_snapshot_cache_keys[];
   string m_tester_snapshot_cache_paths[];
   string m_consumed_sweep_keys[];
   ActivePolicySnapshot m_active_policy;
   SubtypePolicyEntry m_subtype_policy[];
   ContextPolicyEntry m_context_policy[];
   SessionWeekdayPolicyEntry m_session_weekday_policy[];
   datetime m_policy_loaded_at;
   bool m_active_policy_schema_valid;
   int m_funnel_scans;
   int m_funnel_po3_context_created;
   int m_funnel_closed_sweeps_found;
   int m_funnel_displacement_passed;
   int m_funnel_tier_a_contexts;
   int m_funnel_tier_b_contexts;
   int m_funnel_raw_fvgs;
   int m_funnel_accepted_fvgs;
   int m_funnel_fvg_candidates_created;
   int m_funnel_branch_candidates;
   int m_funnel_plan_prices_valid;
   int m_funnel_ai_requests;
   int m_funnel_watchlist_added;
   int m_funnel_market_entries_attempted;
   int m_funnel_pending_entries_attempted;
   int m_funnel_pending_orders_placed;
   int m_funnel_orders_filled;
   int m_funnel_pending_orders_expired;
   int m_funnel_pending_orders_deleted;
   int m_funnel_trades_opened;
   int m_funnel_exclusive_model_filter_checked;
   int m_funnel_exclusive_model_filter_passed;
   int m_funnel_exclusive_model_filter_rejected;
   int m_funnel_exclusive_model_filter_reject_not_breaker;
   int m_funnel_exclusive_model_filter_reject_not_virgin;
   int m_funnel_exclusive_model_filter_reject_not_strong_origin;
   int m_funnel_ai_final_allow;
   int m_funnel_ai_advisories;
   int m_funnel_ai_result_stale_in_tester;
   int m_funnel_ai_cache_hit;
   int m_funnel_ai_cache_miss_due_to_schema_version;
   int m_funnel_invalid_ai_target_arbitration_response;
   int m_funnel_pending_relax_invalid_ai_target_arbitration_response;
   int m_funnel_pending_relax_using_stored_target_arbitration;
   int m_funnel_blocker_severity_7_defaults_suspected;
   int m_funnel_watchlist_precheck_rejects;
   int m_funnel_watchlist_instant_invalidations_bars0;
   int m_total_ai_requests_queued;
   int m_total_tester_ai_wait_started;
   int m_total_tester_ai_wait_completed;
   int m_total_tester_ai_wait_timeout;
   int m_total_ai_results_rejected_stale;
   int m_total_ai_results_accepted_after_blocking_wait;
   int m_total_ai_final_allow_true;
   int m_total_watchlist_added;
   int m_total_orders_placed;
   int m_total_scans;
   int m_total_po3_context_created;
   int m_total_fvg_candidates_created;
   int m_total_plans_valid;
   int m_total_ai_cache_hits;
   int m_total_ai_cache_misses;
   int m_total_ai_cache_miss_due_to_schema_version;
   // Misses where no artifact exists at the computed key at all -- the silent
   // majority of the 2026.09.06 replay, which produced no journal line of any
   // kind because the read simply returned false.
   int m_total_ai_cache_miss_no_artifact;
   int m_total_ai_advisories;
   int m_total_trades_opened;
   int m_total_record_only_requests_exported;
   int m_total_record_only_duplicate_signatures_skipped;
   int m_total_tester_live_wait_non_tradeable_sim_jump;
   int m_total_tester_live_wait_debug_trading_disabled;
   int m_total_target_feasibility_synthetic_infeasible_max_distance;
   int m_total_target_feasibility_synthetic_capped_to_max_distance;
   int m_total_reject_tester_ai_cache_miss;
   int m_total_reject_ai_chose_infeasible_target;
   int m_total_mpc_candidates_blocked;
   int m_total_mpc_ai_calls_saved;
   int m_total_mpc_watchlist_blocks;
   int m_total_mpc_execution_blocks;
   string m_funnel_pending_delete_reasons[];
   int m_funnel_pending_delete_counts[];
   string m_funnel_reject_stages[];
   string m_funnel_reject_reasons[];
   int m_funnel_reject_counts[];
   string m_funnel_target_choice_models[];
   int m_funnel_target_choice_counts[];
   string m_funnel_blocker_classes[];
   int m_funnel_blocker_class_counts[];
   string m_funnel_target_validation_reasons[];
   int m_funnel_target_validation_counts[];
   string m_funnel_watchlist_precheck_reasons[];
   int m_funnel_watchlist_precheck_counts[];
   string m_total_target_choice_models[];
   int m_total_target_choice_counts[];
   string m_total_blocker_classes[];
   int m_total_blocker_class_counts[];
   string m_total_watchlist_precheck_reasons[];
   int m_total_watchlist_precheck_counts[];
   string m_total_target_validation_reasons[];
   int m_total_target_validation_counts[];

   datetime m_last_positions_tick;
   datetime m_last_penalty_persist;
   datetime m_last_rollover_log;
   string m_last_execution_reject_reason;
   // The class decided by the detector DURING the current attempt, and the spread
   // it measured.  _ClassifyExecutionFailure used to honour p.execution_failure_class
   // instead, but that field holds the PREVIOUS attempt's class -- so once a plan
   // was classified it could never be reclassified, and the first failure's label
   // stuck for the life of the plan.  These are per-attempt and are cleared at the
   // top of every placement path.
   string m_last_execution_failure_class;
   double m_last_execution_spread;
   // Counted separately from pre-order execution checks: "attempted execution"
   // was previously indistinguishable from "constructed an order", so 1,149
   // attempts and 0 order constructions looked like the same number.
   bool   m_last_order_construction_attempted;
   string m_ai_wait_log_req_ids[];
   ulong m_ai_wait_log_ms[];
   string m_bucket_policy_json;
   datetime m_bucket_policy_loaded_at;
   datetime m_bucket_policy_last_attempt;
   string m_account_position_mode;
   ENUM_INTERNAL_ACCOUNT_POSITION_MODE m_internal_account_position_mode;
   bool m_force_one_managed_trade_per_symbol;
   string m_source_git_commit;
   string m_source_dirty_tree_status;
   string m_set_file_hash;
   string m_deployment_manifest_hash;
   bool m_deployment_manifest_valid;

   datetime _LastClosedBarTime(const string symbol, const ENUM_TIMEFRAMES tf) {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, tf, 0, 2, rates);
      if(got < 2) return 0;
      return rates[1].time;
   }

   void _CopyPlans(TradePlan &dst[], const TradePlan &src[]) {
      int n = ArraySize(src);
      ArrayResize(dst, n);
      for(int i=0; i<n; i++) dst[i] = src[i];
   }

   void _PruneAiCooldowns() {
      datetime now = TimeLocal();
      for(int i=ArraySize(m_ai_cooldown_until)-1; i>=0; i--){
         if(m_ai_cooldown_until[i] > now) continue;
         int last = ArraySize(m_ai_cooldown_until) - 1;
         if(i != last){
            m_ai_cooldown_symbols[i] = m_ai_cooldown_symbols[last];
            m_ai_cooldown_signatures[i] = m_ai_cooldown_signatures[last];
            m_ai_cooldown_until[i] = m_ai_cooldown_until[last];
         }
         ArrayResize(m_ai_cooldown_symbols, last);
         ArrayResize(m_ai_cooldown_signatures, last);
         ArrayResize(m_ai_cooldown_until, last);
      }
   }

   bool _HasStringValue(const string &arr[], const string value) const {
      for(int i=0; i<ArraySize(arr); i++){
         if(arr[i] == value) return true;
      }
      return false;
   }

   bool _PendingResearchRecordExists(const TradePlan &arr[],
                                     const string record_key,
                                     const bool use_shadow_hash) const {
      if(StringLen(record_key) == 0) return false;
      for(int i=0; i<ArraySize(arr); i++){
         string existing = (use_shadow_hash ? arr[i].shadow_candidate_record_hash : arr[i].trade_key);
         if(existing == record_key) return true;
      }
      return false;
   }

   void _PersistResearchQueues() {
      m_state.SavePlans(m_state.CounterfactualPendingPath(), m_counterfactual_pending);
      m_state.SavePlans(m_state.ShadowPendingPath(), m_shadow_pending);
   }

   bool _IsTesterRuntime() const {
      return (MQLInfoInteger(MQL_TESTER) != 0);
   }

   bool _TesterLiveWaitDebugMode() const {
      return (_IsTesterRuntime() && _EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG);
   }

   bool _TesterBootstrapMode() const {
      return (_IsTesterRuntime() && _EffectiveTesterAiMode() == TESTER_AI_BOOTSTRAP_RULE_ONLY);
   }

   bool _IsTesterBootstrapPlan(const TradePlan &p) const {
      return (_TesterBootstrapMode() &&
              p.ai.decision_quality_tier == "BOOTSTRAP_RULE_ONLY" &&
              p.ai.decision_source == "bootstrap_rule_only" &&
              p.ai.provider_mode == "TESTER_BOOTSTRAP_RULE_ONLY");
   }

   bool _TesterLiveAiBlockingWaitMode() const {
      return (_IsTesterRuntime() && InpUseAI && InpAiWaitInTester && _EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG);
   }

   TesterAiMode _EffectiveTesterAiMode() const {
      return InpTesterAiMode;
   }

   string _TesterAiModeName(const TesterAiMode mode) const {
      if(mode == TESTER_AI_BOOTSTRAP_RULE_ONLY) return "TESTER_AI_BOOTSTRAP_RULE_ONLY";
      if(mode == TESTER_AI_LIVE_WAIT_DEBUG) return "TESTER_AI_LIVE_WAIT_DEBUG";
      if(mode == TESTER_AI_CACHE_ONLY) return "TESTER_AI_CACHE_ONLY";
      if(mode == TESTER_AI_RECORD_ONLY) return "TESTER_AI_RECORD_ONLY";
      return "TESTER_AI_UNKNOWN";
   }

   string _TesterAiModeLabel(const TesterAiMode mode) const {
      if(mode == TESTER_AI_BOOTSTRAP_RULE_ONLY) return "bootstrap_rule_only";
      if(mode == TESTER_AI_LIVE_WAIT_DEBUG) return "live_wait_debug";
      if(mode == TESTER_AI_CACHE_ONLY) return "cache_only";
      if(mode == TESTER_AI_RECORD_ONLY) return "record_only";
      return "unknown";
   }

   bool _InvalidTesterCacheOnlyConfig() const {
      return (MQLInfoInteger(MQL_TESTER) &&
              InpUseAI &&
              _EffectiveTesterAiMode() == TESTER_AI_CACHE_ONLY &&
              !InpTesterAiCache);
   }

   bool _InvalidTesterBootstrapConfig() const {
      if(!_TesterBootstrapMode()) return false;
      return (InpUseAI || InpAiWaitInTester || InpTesterAllowLiveWaitDebugTrading ||
              InpTesterBootstrapRiskMultiplier <= 0.0 ||
              InpTesterBootstrapRiskMultiplier > 1.0);
   }

   bool _TesterCacheDecisionSourceLoadable(const string decision_source) const {
      string source = decision_source;
      StringToLower(source);
      if(StringFind(source, "bridge_error") >= 0) return false;
      if(StringFind(source, "transport") >= 0) return false;
      if(StringFind(source, "timeout") >= 0) return false;
      if(StringFind(source, "malformed") >= 0) return false;
      if(StringFind(source, "invalid_json") >= 0) return false;
      if(StringFind(source, "parser") >= 0) return false;
      if(StringFind(source, "unavailable") >= 0) return false;
      if(StringFind(source, "placeholder") >= 0) return false;
      return true;
   }

   bool _InfrastructureAiRejection(const string reason) const {
      string r = _ReasonCode(reason);
      return (r == "ai_result_stale_in_tester" ||
              r == "ai_wait_timeout_real_time" ||
              r == "ai_transport_error" ||
              r == "ai_response_missing_file" ||
              r == "response_unreadable" ||
              r == "request_missing" ||
              r == "timeout");
   }

   int _CountStringValue(const string &arr[], const string value) const {
      int count = 0;
      for(int i=0; i<ArraySize(arr); i++){
         if(arr[i] == value) count++;
      }
      return count;
   }

   string _TradeKeyPath(const string key) {
      return m_bus.LogDir() + "\\trade_key_" + key + ".json";
   }

   string _TradeTicketPath(const ulong ticket) {
      return m_bus.LogDir() + "\\trade_ticket_" + IntegerToString((long)ticket) + ".json";
   }

   string _TradeOrderPath(const ulong ticket) {
      return m_bus.LogDir() + "\\trade_order_" + IntegerToString((long)ticket) + ".json";
   }

   string _TradeDealPath(const ulong ticket) {
      return m_bus.LogDir() + "\\trade_deal_" + IntegerToString((long)ticket) + ".json";
   }

   string _TradePositionPath(const ulong ticket) {
      return m_bus.LogDir() + "\\trade_position_" + IntegerToString((long)ticket) + ".json";
   }

   string _TradePositionIdentifierPath(const long identifier) {
      return m_bus.LogDir() + "\\trade_position_id_" + IntegerToString(identifier) + ".json";
   }

   string _ExecutionIdentityQuarantineDir() const {
      return m_bus.LogDir() + "\\execution_identity_quarantine";
   }

   ENUM_INTERNAL_ACCOUNT_POSITION_MODE _ResolveAccountPositionMode() const {
      long mode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
      if(mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) return HEDGING_EXACT_POSITION_ID;
      if(mode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING || mode == ACCOUNT_MARGIN_MODE_EXCHANGE){
         if(InpNettingPositionPolicy == NETTING_FORCE_ONE_MANAGED_POSITION_PER_SYMBOL)
            return NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK;
         return UNSUPPORTED_ACCOUNT_MODE;
      }
      return UNSUPPORTED_ACCOUNT_MODE;
   }

   string _AccountPositionModeLabel(const ENUM_INTERNAL_ACCOUNT_POSITION_MODE mode) const {
      if(mode == HEDGING_EXACT_POSITION_ID) return "HEDGING_EXACT_POSITION_ID";
      if(mode == NETTING_VIRTUAL_SUBPOSITION_LEDGER) return "NETTING_VIRTUAL_SUBPOSITION_LEDGER";
      if(mode == NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK) return "NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK";
      return "UNSUPPORTED_ACCOUNT_MODE";
   }

   string _TradeResultsDir() {
      return m_bus.LogDir() + "\\trade_results";
   }

   string _TradeResultPath(const string key) {
      return _TradeResultsDir() + "\\trade_result_" + key + ".json";
   }

   string _CompletedPositionMarkerPath(const long position_identifier) {
      // Position identifiers are reused when Strategy Tester starts a new run.
      // Tester state is scoped to its run session; live state is scoped to the
      // stable account+magic identity so a terminal restart cannot finalize the
      // same broker position twice.
      return m_bus.LogDir() + "\\completed_scope_" + m_state.RuntimeScope()
             + "_position_id_" + IntegerToString(position_identifier) + ".json";
   }

   datetime _AnalyticsResetAfter() {
      if(MQLInfoInteger(MQL_TESTER)) return 0;
      string txt;
      if(!_ReadText(m_bus.LogDir() + "\\analytics_reset.json", txt)) return 0;
      return (datetime)(int)JsonGetNumber(txt, "reset_after", 0.0);
   }

   datetime _NowServerOrLocal() const {
      datetime now = TimeTradeServer();
      if(now <= 0) now = TimeLocal();
      return now;
   }

   string _WeekdayName(const datetime ts) const {
      MqlDateTime dt;
      TimeToStruct(ts, dt);
      switch(dt.day_of_week){
         case 0: return "Sun";
         case 1: return "Mon";
         case 2: return "Tue";
         case 3: return "Wed";
         case 4: return "Thu";
         case 5: return "Fri";
         case 6: return "Sat";
      }
      return "unknown";
   }

   ulong _WallClockMs() const {
      return (ulong)GetTickCount64();
   }

   ulong _WallElapsedMs(const ulong started_ms) const {
      ulong now = _WallClockMs();
      if(now >= started_ms) return (now - started_ms);
      return 0;
   }

   int _RequiredConfirmationSignals() const {
      int signals = 0;
      if(InpRequireFvgMidMitigation) signals++;
      if(InpWaitB50OnM1) signals++;
      if(InpWaitCandleConfirm) signals++;
      return signals;
   }

   int _EffectiveWatchlistMaxBars() const {
      int bars = MathMax(1, InpWatchlistMaxBars);
      if(PO3EffectiveConfirmTF() == PERIOD_M1){
         if(InpWaitB50OnM1 && InpWaitCandleConfirm) return MathMax(bars, 60);
         int signals = _RequiredConfirmationSignals();
         if(signals >= 3) bars = MathMax(bars, 60);
         else if(signals == 2) bars = MathMax(bars, 40);
      }
      return bars;
   }

   int _EffectiveWatchlistMaxMinutes(const ENUM_TIMEFRAMES tf) const {
      int minutes = MathMax(1, InpWatchlistMaxMinutes);
      if(tf == PERIOD_H1) minutes = MathMax(minutes, 120);
      else if(tf == PERIOD_H4) minutes = MathMax(minutes, 720);
      else if(tf >= PERIOD_D1) minutes = MathMax(minutes, 1440);
      return minutes;
   }

   int _PendingExpiryMinutes(const ENUM_TIMEFRAMES tf) const {
      int minutes = MathMax(1, InpPendingOrderExpiryMin);
      if(PO3PresetMicroIntraday() && tf <= PERIOD_M15)
         minutes = MathMin(minutes, MathMax(1, InpMicroPendingExpiryMin));
      if(tf == PERIOD_H1)
         minutes = MathMax(120, MathMax(minutes, InpPendingExpiryH1Minutes));
      else if(tf == PERIOD_H4)
         minutes = MathMax(480, MathMax(minutes, InpPendingExpiryH4Minutes));
      else if(tf >= PERIOD_D1)
         minutes = MathMax(1440, MathMax(minutes, InpPendingExpiryD1Minutes));
      return minutes;
   }

   int _PendingAiTimeoutMinutes() const {
      if(MQLInfoInteger(MQL_TESTER) && InpAiWaitTimeoutRealMin > 0) return InpAiWaitTimeoutRealMin;
      return InpPendingAiTimeoutMin;
   }

   bool _PathExists(const string rel_path) {
      string txt;
      return m_bus.ReadText(rel_path, txt);
   }

   bool _ReadText(const string rel_path, string &out) {
      return m_bus.ReadText(rel_path, out);
   }

   void _LoadDeploymentManifest() {
      m_source_git_commit = "UNAVAILABLE";
      m_source_dirty_tree_status = "UNKNOWN";
      m_set_file_hash = "UNAVAILABLE";
      m_deployment_manifest_hash = "UNAVAILABLE";
      m_deployment_manifest_valid = false;
      string path = m_bus.Root() + "\\config\\deployment_manifest.json";
      string text = "";
      string reason = "";
      if(!m_bus.ReadText(path, text)) reason = "deployment_manifest_missing";
      else if(!JsonValidateDocumentStrict(text, reason)) {}
      else if(JsonGetString(text, "schema_version", "") != DEPLOYMENT_MANIFEST_SCHEMA_VERSION)
         reason = "deployment_manifest_schema_incompatible";
      else {
         m_source_git_commit = JsonGetString(text, "git_commit", "");
         m_source_dirty_tree_status = JsonGetString(text, "dirty_tree_status", "");
         m_set_file_hash = JsonGetString(text, "set_file_hash", "");
         m_deployment_manifest_hash = _IntegrityHash(text);
         m_deployment_manifest_valid = (StringLen(m_source_git_commit) > 0 &&
                                        (m_source_dirty_tree_status == "CLEAN" || m_source_dirty_tree_status == "DIRTY") &&
                                        StringLen(m_set_file_hash) > 0);
         if(!m_deployment_manifest_valid) reason = "deployment_manifest_fields_incomplete";
      }
      _Journal("[deployment_manifest] schema=" + DEPLOYMENT_MANIFEST_SCHEMA_VERSION
               + " valid=" + (m_deployment_manifest_valid ? "true" : "false")
               + " git_commit=" + m_source_git_commit
               + " dirty_tree_status=" + m_source_dirty_tree_status
               + " set_file_hash=" + m_set_file_hash
               + " manifest_hash=" + m_deployment_manifest_hash
               + " reason=" + (StringLen(reason) > 0 ? reason : "none"));
   }

   bool _ParseSubtypePolicyLine(const string line, SubtypePolicyEntry &entry) const {
      entry.subtype_key = JsonGetString(line, "subtype_key", "");
      if(StringLen(entry.subtype_key) == 0) return false;
      entry.action = JsonGetString(line, "action", "");
      entry.score_penalty = JsonGetNumber(line, "score_penalty", 0.0);
      if(!JsonGetNumberStrict(line, "risk_multiplier", entry.risk_multiplier)) return false;
      entry.shrunk_win_rate = JsonGetNumber(line, "shrunk_win_rate", 0.0);
      entry.avg_r = JsonGetNumber(line, "avg_r", 0.0);
      entry.evidence_score = JsonGetNumber(line, "evidence_score", 0.0);
      entry.sample_count = (int)JsonGetNumber(line, "sample_count", 0.0);
      entry.policy_id = JsonGetString(line, "policy_id", "");
      return (entry.risk_multiplier >= 0.0 && entry.risk_multiplier <= 1.0);
   }

   bool _ParseContextPolicyLine(const string line, ContextPolicyEntry &entry) const {
      entry.policy_bucket = JsonGetString(line, "policy_bucket", "");
      if(StringLen(entry.policy_bucket) == 0) return false;
      entry.action = JsonGetString(line, "action", "");
      entry.score_bias = JsonGetNumber(line, "score_bias", 0.0);
      if(!JsonGetNumberStrict(line, "risk_multiplier", entry.risk_multiplier)) return false;
      entry.expected_value_bias = JsonGetNumber(line, "expected_value_bias", 0.0);
      entry.sample_count = (int)JsonGetNumber(line, "sample_count", 0.0);
      entry.policy_id = JsonGetString(line, "policy_id", "");
      return (entry.risk_multiplier >= 0.0 && entry.risk_multiplier <= 1.0);
   }

   bool _ParseSessionWeekdayPolicyLine(const string line, SessionWeekdayPolicyEntry &entry) const {
      entry.session_name = JsonGetString(line, "session_name", "");
      entry.weekday = JsonGetString(line, "weekday", "");
      if(StringLen(entry.session_name) == 0 || StringLen(entry.weekday) == 0) return false;
      entry.action = JsonGetString(line, "action", "monitor");
      if(!JsonGetNumberStrict(line, "risk_multiplier", entry.risk_multiplier)) return false;
      entry.rr_floor_delta = JsonGetNumber(line, "rr_floor_delta", 0.0);
      entry.score_bias = JsonGetNumber(line, "score_bias", 0.0);
      entry.sample_count = (int)JsonGetNumber(line, "sample_count", 0.0);
      entry.policy_id = JsonGetString(line, "policy_id", "");
      return (entry.risk_multiplier >= 0.0 && entry.risk_multiplier <= 1.0);
   }

   bool _LoadActivePolicyFiles() {
      ZeroMemory(m_active_policy);
      m_active_policy_schema_valid = true;
      ArrayResize(m_subtype_policy, 0);
      ArrayResize(m_context_policy, 0);
      ArrayResize(m_session_weekday_policy, 0);

      string txt;
      if(_ReadText(_ActivePolicyPath(), txt)){
         string policy_doc_reason = "";
         if(!JsonValidateDocumentStrict(txt, policy_doc_reason)){
            m_active_policy_schema_valid = false;
            _Journal("[policy_schema] valid=false section=active reason=" + policy_doc_reason);
         }
         m_active_policy.policy_id = JsonGetString(txt, "policy_id", "");
         m_active_policy.version = (int)JsonGetNumber(txt, "version", 0.0);
         m_active_policy.activated_at = (datetime)(int)JsonGetNumber(txt, "activated_at", 0.0);
         m_active_policy.evidence_score = JsonGetNumber(txt, "evidence_score", 0.0);
         m_active_policy.evidence_passed = JsonGetBool(txt, "evidence_passed", false);
         m_active_policy.walk_forward_passed = JsonGetBool(txt, "walk_forward_passed", false);
         m_active_policy.change_rate_passed = JsonGetBool(txt, "change_rate_passed", false);
         m_active_policy.soft_setup_floor = JsonGetNumber(txt, "soft_setup_floor", 0.0);
         m_active_policy.hard_setup_floor = JsonGetNumber(txt, "hard_setup_floor", 0.0);
         if(JsonHasKey(txt, "setup_floor_penalty_mult")){
            if(!JsonGetNumberStrict(txt, "setup_floor_penalty_mult", m_active_policy.setup_floor_penalty_mult) ||
               m_active_policy.setup_floor_penalty_mult < 0.0 || m_active_policy.setup_floor_penalty_mult > 1.0){
               m_active_policy_schema_valid = false;
               _Journal("[policy_schema] valid=false section=active reason=setup_floor_penalty_mult_invalid");
            }
         } else m_active_policy.setup_floor_penalty_mult = 1.0;
         m_active_policy.ote_softness_frac = JsonGetNumber(txt, "ote_softness_frac", InpOteSoftnessFrac);
         bool default_multiplier_present = JsonGetNumberStrict(txt, "default_risk_multiplier", m_active_policy.default_risk_multiplier);
         if(!default_multiplier_present || m_active_policy.default_risk_multiplier < 0.0 || m_active_policy.default_risk_multiplier > 1.0){
            m_active_policy_schema_valid = false;
            _Journal("[policy_schema] valid=false section=active reason=default_risk_multiplier_missing_or_invalid");
         }
         m_active_policy.runner_sequence_floor = JsonGetNumber(txt, "runner_sequence_floor", InpRunnerSequenceQualityFloor);
         m_active_policy.runner_liquidity_rr_floor = JsonGetNumber(txt, "runner_liquidity_rr_floor", InpRunnerLiquidityRRFloor);
         m_active_policy.runner_cost_r_ceiling = JsonGetNumber(txt, "runner_cost_r_ceiling", InpRunnerCostRCeiling);
         m_active_policy.runner_alignment_floor = JsonGetNumber(txt, "runner_alignment_floor", InpRunnerHtfAlignmentFloor);
         m_active_policy.runner_adverse_ceiling = JsonGetNumber(txt, "runner_adverse_ceiling", InpRunnerAdverseContextCeiling);
         m_active_policy.runner_ev_floor = JsonGetNumber(txt, "runner_ev_floor", 0.0);
         bool shadow_policy = JsonGetBool(txt, "shadow_mode", false);
         string activation_state = JsonGetString(txt, "activation_state", "");
         string ledger_integrity = JsonGetString(txt, "ledger_integrity_status", "");
         string policy_decision_schema = JsonGetString(txt, "decision_schema_version", "");
         string policy_taxonomy = JsonGetString(txt, "taxonomy_version", "");
         m_active_policy.valid = (StringLen(m_active_policy.policy_id) > 0 &&
                                  m_active_policy_schema_valid &&
                                  default_multiplier_present &&
                                  m_active_policy.evidence_passed &&
                                  m_active_policy.walk_forward_passed &&
                                  m_active_policy.change_rate_passed &&
                                  ledger_integrity == "CLEAN" &&
                                  policy_decision_schema == AI_DECISION_SCHEMA_VERSION &&
                                  policy_taxonomy == SETUP_TAXONOMY_VERSION &&
                                  !shadow_policy &&
                                  activation_state != "shadow" &&
                                  !InpPolicyShadowMode);
      }

      string ndjson;
      bool allow_policy_lines = (m_active_policy.valid && !InpPolicyShadowMode);
      if(allow_policy_lines && _ReadText(_SubtypePolicyPath(), ndjson)){
         int len = (int)StringLen(ndjson);
         int start = 0;
         for(int i=0; i<=len; i++){
            if(i == len || StringGetCharacter(ndjson, i) == '\n'){
               string line = _TrimCopy(StringSubstr(ndjson, start, i - start));
               start = i + 1;
               if(StringLen(line) == 0) continue;
               SubtypePolicyEntry entry;
               ZeroMemory(entry);
               if(!_ParseSubtypePolicyLine(line, entry)){
                  m_active_policy_schema_valid = false;
                  _Journal("[policy_schema] valid=false section=subtype reason=risk_multiplier_missing_or_invalid");
                  continue;
               }
               int n = ArraySize(m_subtype_policy);
               ArrayResize(m_subtype_policy, n + 1);
               m_subtype_policy[n] = entry;
            }
         }
      }

      ndjson = "";
      if(allow_policy_lines && _ReadText(_ContextPolicyPath(), ndjson)){
         int len2 = (int)StringLen(ndjson);
         int start2 = 0;
         for(int i=0; i<=len2; i++){
            if(i == len2 || StringGetCharacter(ndjson, i) == '\n'){
               string line = _TrimCopy(StringSubstr(ndjson, start2, i - start2));
               start2 = i + 1;
               if(StringLen(line) == 0) continue;
               ContextPolicyEntry entry;
               ZeroMemory(entry);
               if(!_ParseContextPolicyLine(line, entry)){
                  m_active_policy_schema_valid = false;
                  _Journal("[policy_schema] valid=false section=context reason=risk_multiplier_missing_or_invalid");
                  continue;
               }
               int n = ArraySize(m_context_policy);
               ArrayResize(m_context_policy, n + 1);
               m_context_policy[n] = entry;
            }
         }
      }

      ndjson = "";
      if(allow_policy_lines && _ReadText(_SessionWeekdayPolicyPath(), ndjson)){
         int len3 = (int)StringLen(ndjson);
         int start3 = 0;
         for(int i=0; i<=len3; i++){
            if(i == len3 || StringGetCharacter(ndjson, i) == '\n'){
               string line = _TrimCopy(StringSubstr(ndjson, start3, i - start3));
               start3 = i + 1;
               if(StringLen(line) == 0) continue;
               SessionWeekdayPolicyEntry entry;
               ZeroMemory(entry);
               if(!_ParseSessionWeekdayPolicyLine(line, entry)){
                  m_active_policy_schema_valid = false;
                  _Journal("[policy_schema] valid=false section=session_weekday reason=risk_multiplier_missing_or_invalid");
                  continue;
               }
               int n = ArraySize(m_session_weekday_policy);
               ArrayResize(m_session_weekday_policy, n + 1);
               m_session_weekday_policy[n] = entry;
            }
         }
      }

      m_policy_loaded_at = TimeLocal();
      return m_active_policy.valid || ArraySize(m_subtype_policy) > 0 || ArraySize(m_context_policy) > 0 || ArraySize(m_session_weekday_policy) > 0;
   }

   void _LoadActivePolicyIfNeeded() {
      if(!InpAnalyticsAutoActivate) return;
      if(m_policy_loaded_at > 0 && (TimeLocal() - m_policy_loaded_at) < MathMax(1, InpPolicyReloadMinutes) * 60) return;
      _LoadActivePolicyFiles();
   }

   int _FindSubtypePolicy(const string subtype_key) const {
      for(int i=0; i<ArraySize(m_subtype_policy); i++){
         if(m_subtype_policy[i].subtype_key == subtype_key) return i;
      }
      return -1;
   }

   int _FindContextPolicy(const string policy_bucket) const {
      for(int i=0; i<ArraySize(m_context_policy); i++){
         if(m_context_policy[i].policy_bucket == policy_bucket) return i;
      }
      return -1;
   }

   int _FindSessionWeekdayPolicy(const string session_name, const string weekday) const {
      for(int i=0; i<ArraySize(m_session_weekday_policy); i++){
         if(m_session_weekday_policy[i].session_name == session_name && m_session_weekday_policy[i].weekday == weekday) return i;
      }
      return -1;
   }

   string _ReqPath(const string req_id) const {
      return m_bus.ReqDir() + "\\" + req_id + ".json";
   }

   string _RespPath(const string req_id) const {
      return m_bus.RespDir() + "\\" + req_id + ".json";
   }

   string _PolicyDir() const {
      return m_bus.LogDir() + "\\policies";
   }

   string _ActivePolicyPath() const {
      return _PolicyDir() + "\\active_policy.json";
   }

   string _SubtypePolicyPath() const {
      return _PolicyDir() + "\\subtype_policy.ndjson";
   }

   string _ContextPolicyPath() const {
      return _PolicyDir() + "\\context_policy.ndjson";
   }

   string _SessionWeekdayPolicyPath() const {
      return _PolicyDir() + "\\session_weekday_policy.ndjson";
   }

   string _PolicyAbsolutePath(const string rel_path) const {
      return TerminalInfoString(TERMINAL_COMMONDATA_PATH) + "\\Files\\" + rel_path;
   }

   string _CurrentLedgerIntegrityStatus() {
      string ledger_text = "";
      if(!m_bus.ReadText(_AnalyticsDir() + "\\ledger_integrity_report.json", ledger_text))
         return "UNAVAILABLE";
      string status = JsonGetString(ledger_text, "global_status", "UNAVAILABLE");
      StringToUpper(status);
      int clean_count = (int)JsonGetNumber(ledger_text, "clean", 0.0);
      if(status == "CLEAN" && clean_count <= 0) return "QUARANTINED_NO_CLEAN_ROWS";
      return status;
   }

   int _ManifestRowCount(const string text) const {
      if(StringLen(text) == 0) return 0;
      int count = 0;
      bool has_text = false;
      for(int i=0; i<StringLen(text); i++){
         ushort c = (ushort)StringGetCharacter(text, i);
         if(c == '\n'){
            if(has_text) count++;
            has_text = false;
         } else if(c != ' ' && c != '\r' && c != '\t') has_text = true;
      }
      if(has_text) count++;
      return count;
   }

   string _StartupPolicyManifestRow(const string policy_type,
                                    const string policy_id,
                                    const string rel_path,
                                    const string required_schema,
                                    const bool enabled,
                                    const bool shadow_requested,
                                    const string ledger_status,
                                    const bool code_compatible) {
      string text = "";
      bool exists = m_bus.ReadText(rel_path, text);
      string document_reason = "";
      bool json_doc = (StringFind(rel_path, ".ndjson") < 0);
      bool document_valid = (!exists ? false : (!json_doc || JsonValidateDocumentStrict(text, document_reason)));
      string schema = (json_doc ? JsonGetString(text, "schema_version", "") : required_schema);
      if(StringLen(schema) == 0 && json_doc) schema = JsonGetString(text, "calibration_contract_version", "");
      bool schema_valid = (StringLen(required_schema) == 0 || schema == required_schema);
      int rows_loaded = (exists ? (json_doc ? 1 : _ManifestRowCount(text)) : 0);
      string policy_runtime_hash = (json_doc ? JsonGetString(text, "runtime_input_hash", "") : "");
      string policy_decision_schema = (json_doc ? JsonGetString(text, "decision_schema_version", "") : "");
      string policy_taxonomy = (json_doc ? JsonGetString(text, "taxonomy_version", "") : "");
      bool runtime_required = (policy_type != "risk_factors" && policy_type != "invalidation");
      bool decision_required = (policy_type == "active" || policy_type == "subtype" || policy_type == "context" ||
                                policy_type == "session" || policy_type == "management" || policy_type == "calibration" ||
                                policy_type == "repeatability" || policy_type == "hierarchical_priors");
      bool taxonomy_required = (policy_type == "active" || policy_type == "subtype" || policy_type == "context" ||
                                policy_type == "session" || policy_type == "management" || policy_type == "calibration" ||
                                policy_type == "hierarchical_priors");
      bool runtime_compatible = (!runtime_required || (StringLen(policy_runtime_hash) > 0 && policy_runtime_hash == m_ai.RuntimeHash()));
      bool decision_compatible = (!decision_required || policy_decision_schema == AI_DECISION_SCHEMA_VERSION);
      bool taxonomy_compatible = (!taxonomy_required || policy_taxonomy == SETUP_TAXONOMY_VERSION);
      datetime expires_at = (datetime)(json_doc ? JsonGetNumber(text, "expires_at", 0) : 0);
      bool explicit_stale = (json_doc ? JsonGetBool(text, "stale", false) : false);
      bool stale = explicit_stale || expires_at <= 0 || _NowServerOrLocal() > expires_at;
      string stale_status = (explicit_stale ? "EXPLICIT_STALE" :
                             (expires_at <= 0 ? "UNKNOWN_NO_EXPIRY" :
                              (_NowServerOrLocal() > expires_at ? "EXPIRED" : "FRESH")));
      int rows_rejected = ((!document_valid || !schema_valid || !runtime_compatible ||
                            !decision_compatible || !taxonomy_compatible || stale) && exists ? rows_loaded : 0);
      bool clean = (ledger_status == "CLEAN");
      string status = (!enabled ? "disabled" : (!exists ? "missing" : (!document_valid ? "invalid_json" : (!schema_valid ? "incompatible_schema" : "loaded"))));
      string authority = "blocked";
      string reason = "none";
      if(!enabled) reason = "policy_disabled";
      else if(!exists) reason = "policy_file_missing";
      else if(!document_valid) reason = document_reason;
      else if(!schema_valid) reason = "schema_incompatible";
      else if(!code_compatible) reason = "code_incompatible";
      else if(!runtime_compatible) reason = "runtime_input_incompatible";
      else if(!decision_compatible) reason = "decision_schema_incompatible";
      else if(!taxonomy_compatible) reason = "taxonomy_incompatible";
      else if(stale) reason = "policy_stale_or_freshness_unproven";
      else if(!clean) reason = "ledger_not_clean";
      else if(shadow_requested){ authority = "shadow"; reason = "shadow_requested"; }
      else authority = "active";
      if(enabled && reason != "none" && reason != "shadow_requested") status = "blocked";
      string rejection_reasons = (reason == "none" || reason == "shadow_requested"
                                  ? "[]" : "[\"" + JsonEscape(reason) + "\"]");
      string row = "{";
      row += JsonKVStr("policy_type", policy_type) + ",";
      row += JsonKVStr("policy_id", policy_id) + ",";
      row += JsonKVBool("enabled", enabled) + ",";
      row += JsonKVStr("absolute_path", _PolicyAbsolutePath(rel_path)) + ",";
      row += JsonKVStr("file_hash", exists ? _IntegrityHash(text) : "UNAVAILABLE") + ",";
      row += JsonKVStr("schema_version", schema) + ",";
      row += JsonKVStr("required_schema_version", required_schema) + ",";
      row += JsonKVStr("status", status) + ",";
      row += JsonKVStr("authority", authority) + ",";
      row += JsonKVStr("activation_state", authority == "active" ? "active" : (enabled ? "blocked" : "disabled")) + ",";
      row += JsonKVBool("shadow_state", shadow_requested) + ",";
      row += JsonKVStr("reason", reason) + ",";
      row += "\"rejection_reasons\":" + rejection_reasons + ",";
      row += JsonKVInt("rows_loaded", rows_loaded) + ",";
      row += JsonKVInt("rows_rejected", rows_rejected) + ",";
      row += JsonKVBool("code_compatibility", code_compatible) + ",";
      row += JsonKVBool("runtime_input_compatibility", runtime_compatible) + ",";
      row += JsonKVBool("decision_schema_compatibility", decision_compatible) + ",";
      row += JsonKVBool("taxonomy_compatibility", taxonomy_compatible) + ",";
      row += JsonKVStr("ledger_integrity_status", ledger_status) + ",";
      row += JsonKVInt("data_window_start", (int)(json_doc ? JsonGetNumber(text, "data_window_start", 0) : 0)) + ",";
      row += JsonKVInt("data_window_end", (int)(json_doc ? JsonGetNumber(text, "data_window_end", 0) : 0)) + ",";
      row += JsonKVInt("expires_at", (int)expires_at) + ",";
      row += JsonKVStr("stale_status", stale_status) + ",";
      row += JsonKVBool("stale", stale);
      row += "}";
      _Journal("[startup_policy_manifest] policy_type=" + policy_type
               + " policy_id=" + policy_id + " status=" + status
               + " authority=" + authority + " reason=" + reason);
      return row;
   }

   void _WriteStartupPolicyManifest() {
      string ledger_status = _CurrentLedgerIntegrityStatus();
      string policies = "[";
      policies += _StartupPolicyManifestRow("active", m_active_policy.policy_id, _ActivePolicyPath(), "", InpAnalyticsAutoActivate, InpPolicyShadowMode, ledger_status, m_active_policy_schema_valid) + ",";
      policies += _StartupPolicyManifestRow("subtype", "subtype_policy", _SubtypePolicyPath(), "", InpAnalyticsAutoActivate, InpPolicyShadowMode, ledger_status, m_active_policy_schema_valid) + ",";
      policies += _StartupPolicyManifestRow("context", "context_policy", _ContextPolicyPath(), "", InpAnalyticsAutoActivate, InpPolicyShadowMode, ledger_status, m_active_policy_schema_valid) + ",";
      policies += _StartupPolicyManifestRow("session", "session_weekday_policy", _SessionWeekdayPolicyPath(), "", InpAnalyticsAutoActivate, InpPolicyShadowMode, ledger_status, m_active_policy_schema_valid) + ",";
      policies += _StartupPolicyManifestRow("risk_factors", "risk_factor_policy", InpRiskFactorPolicyFile, RISK_FACTOR_SCHEMA_VERSION, InpRiskFactorGateEnable, !InpRiskFactorGateEnable, ledger_status, true) + ",";
      policies += _StartupPolicyManifestRow("invalidation", "invalidation_policy", InpInvalidationAssetClassPolicyFile, INVALIDATION_POLICY_SCHEMA_VERSION, InpInvalidationAssetClassPolicyEnable, !InpInvalidationAssetClassPolicyEnable, ledger_status, true) + ",";
      policies += _StartupPolicyManifestRow("normalized_fvg", "normalized_fvg_policy", InpNormalizedFvgAssetClassPolicyFile, NORMALIZED_FVG_SCHEMA_VERSION, InpNormalizedFvgMode != NORMALIZED_FVG_OFF, InpNormalizedFvgMode != NORMALIZED_FVG_ENFORCE, ledger_status, true) + ",";
      policies += _StartupPolicyManifestRow("management", "management_policy", _AnalyticsDir() + "\\management_policy.json", MANAGEMENT_SCHEMA_VERSION, true, true, ledger_status, true) + ",";
      policies += _StartupPolicyManifestRow("calibration", "calibration_artifact", _AnalyticsDir() + "\\calibration_artifact.json", CALIBRATION_CONTRACT_VERSION, true, true, ledger_status, true) + ",";
      policies += _StartupPolicyManifestRow("repeatability", "repeatability_artifact", _AnalyticsDir() + "\\ai_repeatability_artifact.json", REPEATABILITY_SCHEMA_VERSION, true, true, ledger_status, true) + ",";
      policies += _StartupPolicyManifestRow("hierarchical_priors", "hierarchical_priors", _AnalyticsDir() + "\\hierarchical_outcome_artifact.json", HIERARCHICAL_OUTCOME_MODEL_VERSION, true, true, ledger_status, true);
      policies += "]";
      string manifest = "{";
      manifest += JsonKVStr("schema_version", POLICY_MANIFEST_SCHEMA_VERSION) + ",";
      manifest += JsonKVStr("engine_version", ENGINE_VERSION) + ",";
      manifest += JsonKVStr("runtime_input_hash", m_ai.RuntimeHash()) + ",";
      manifest += JsonKVStr("decision_schema_version", AI_DECISION_SCHEMA_VERSION) + ",";
      manifest += JsonKVStr("taxonomy_version", SETUP_TAXONOMY_VERSION) + ",";
      manifest += JsonKVStr("ledger_integrity_status", ledger_status) + ",";
      manifest += "\"policies\":" + policies;
      manifest += "}";
      m_bus.WriteText(m_bus.LogDir() + "\\startup_policy_manifest_mql.json", manifest);
   }

   string _AnalyticsDir() const {
      return m_bus.LogDir() + "\\analytics";
   }

   string _AnalyticsJobsDir() const {
      return m_bus.LogDir() + "\\analytics_jobs";
   }

   bool _IsTrimChar(const ushort c) const {
      return (c == ' ' || c == '\t' || c == '\r' || c == '\n');
   }

   string _TrimCopy(const string s) const {
      int len = (int)StringLen(s);
      int start = 0;
      while(start < len && _IsTrimChar((ushort)StringGetCharacter(s, start))) start++;
      int end = len - 1;
      while(end >= start && _IsTrimChar((ushort)StringGetCharacter(s, end))) end--;
      if(end < start) return "";
      return StringSubstr(s, start, end - start + 1);
   }

   bool _ShouldJournal() const {
      if(!InpVerboseJournal) return false;
      if(InpJournalTesterOnly && !MQLInfoInteger(MQL_TESTER)) return false;
      return true;
   }

   void _Journal(const string msg) const {
      if(!_ShouldJournal()) return;
      Print("[PO3_AIGate] ", msg);
   }

   //--- Graded diagnostics.  See InpJournalDetailLevel in Config.mqh for the levels
   //--- and the measurement that motivated them.  Callers must test
   //--- _JournalDetailEnabled() BEFORE building the message: at this volume the string
   //--- concatenation is itself part of the cost, so a guard that only suppresses the
   //--- Print still pays for the line it does not emit.
   bool _JournalDetailEnabled(const int level) const {
      if(!_ShouldJournal()) return false;
      return (InpJournalDetailLevel >= level);
   }

   void _JournalDetail(const int level, const string msg) const {
      if(InpJournalDetailLevel < level) return;
      _Journal(msg);
   }

   //--- Repeat-identical diagnostics are SUPPRESSED, never dropped.
   // A line whose text has not changed since the last time it was printed carries no
   // new evidence.  [broker_cost_estimate] emitted 2,215 lines in one scan cycle --
   // 10.8% of the journal's bytes -- and every one of them said
   // "source=configured_fallback samples=0 median_per_lot=0.000000
   //  stressed_per_lot=0.010000", i.e. 13 distinct facts, one per symbol, restated
   // thousands of times.  Suppression is per key and is counted, and
   // _LogJournalDedupSummary() reports the counts at the end of the run, so the audit
   // trail states exactly how many repeats each key absorbed.  A line that changes is
   // always printed.
   string m_jd_key[];
   string m_jd_last[];
   long   m_jd_printed[];
   long   m_jd_suppressed[];

   void _JournalOnChange(const string key, const string msg) {
      if(!_ShouldJournal()) return;
      int idx = -1;
      for(int i=0; i<ArraySize(m_jd_key); i++)
         if(m_jd_key[i] == key){ idx = i; break; }
      if(idx < 0){
         idx = ArraySize(m_jd_key);
         ArrayResize(m_jd_key, idx+1);
         ArrayResize(m_jd_last, idx+1);
         ArrayResize(m_jd_printed, idx+1);
         ArrayResize(m_jd_suppressed, idx+1);
         m_jd_key[idx]        = key;
         m_jd_last[idx]       = "";
         m_jd_printed[idx]    = 0;
         m_jd_suppressed[idx] = 0;
      }
      if(m_jd_printed[idx] > 0 && m_jd_last[idx] == msg){
         m_jd_suppressed[idx]++;
         return;
      }
      m_jd_last[idx]    = msg;
      m_jd_printed[idx] = m_jd_printed[idx] + 1;
      _Journal(msg);
   }

   void _LogJournalDedupSummary() const {
      for(int i=0; i<ArraySize(m_jd_key); i++){
         if(m_jd_suppressed[i] <= 0) continue;
         _Journal("[journal_dedup] key=" + m_jd_key[i]
                  + " printed=" + IntegerToString(m_jd_printed[i])
                  + " suppressed_identical=" + IntegerToString(m_jd_suppressed[i]));
      }
   }

   string _ReasonCode(string reason) const {
      StringToLower(reason);
      StringReplace(reason, " ", "_");
      StringReplace(reason, ":", "_");
      StringReplace(reason, "=", "_");
      StringReplace(reason, "<", "_lt_");
      StringReplace(reason, ">", "_gt_");
      StringReplace(reason, ".", "_");
      StringReplace(reason, ",", "_");
      while(StringFind(reason, "__") >= 0) StringReplace(reason, "__", "_");
      if(StringLen(reason) == 0) reason = "unknown_reject";
      return reason;
   }

   void _TrackNamedCounter(string &names[], int &counts[], const string key) {
      string code = _ReasonCode(key);
      if(StringLen(code) == 0) code = "unknown";
      for(int i=0; i<ArraySize(names); i++){
         if(names[i] != code) continue;
         counts[i]++;
         return;
      }
      int n = ArraySize(names);
      ArrayResize(names, n + 1);
      ArrayResize(counts, n + 1);
      names[n] = code;
      counts[n] = 1;
   }

   int _NamedCounterValue(const string &names[], const int &counts[], const string key) const {
      string code = _ReasonCode(key);
      for(int i=0; i<ArraySize(names); i++){
         if(names[i] == code) return counts[i];
      }
      return 0;
   }

   string _NamedCounterSummary(const string &names[], const int &counts[], const int limit=8) const {
      int n = ArraySize(names);
      if(n <= 0) return "{}";
      bool used[];
      ArrayResize(used, n);
      string out = "{";
      int emitted = 0;
      for(int rank=0; rank<limit && emitted<n; rank++){
         int best = -1;
         for(int i=0; i<n; i++){
            if(used[i]) continue;
            if(best < 0 || counts[i] > counts[best]) best = i;
         }
         if(best < 0) break;
         used[best] = true;
         if(emitted > 0) out += ";";
         out += names[best] + ":" + IntegerToString(counts[best]);
         emitted++;
      }
      out += "}";
      return out;
   }

   void _TrackSetupReject(const string stage, const string reason) {
      string s = _ReasonCode(stage);
      string r = _ReasonCode(reason);
      for(int i=0; i<ArraySize(m_funnel_reject_reasons); i++){
         if(m_funnel_reject_stages[i] == s && m_funnel_reject_reasons[i] == r){
            m_funnel_reject_counts[i]++;
            return;
         }
      }
      int n = ArraySize(m_funnel_reject_reasons);
      ArrayResize(m_funnel_reject_stages, n + 1);
      ArrayResize(m_funnel_reject_reasons, n + 1);
      ArrayResize(m_funnel_reject_counts, n + 1);
      m_funnel_reject_stages[n] = s;
      m_funnel_reject_reasons[n] = r;
      m_funnel_reject_counts[n] = 1;
   }

   void _LogSetupReject(const string symbol, const string stage, const string reason, const string detail="") {
      _TrackSetupReject(stage, reason);
      _Journal(symbol + " setup_reject reject_stage=" + _ReasonCode(stage)
               + " reject_reason=" + _ReasonCode(reason)
               + (StringLen(detail) > 0 ? " " + detail : ""));
   }

   void _WriteShadowCandidateRecord(const TradePlan &source,
                                    const string decision_stage,
                                    const string rejection_reason) {
      if(!InpShadowCandidateLedgerEnable) return;
      TradePlan p = source;
      _InitializeNarrativeFields(p);
      if(StringLen(p.candidate_id) == 0)
         p.candidate_id = p.symbol + "|" + IntegerToString((int)p.fvg.t_form) + "|" + p.entry_branch;
      if(StringLen(p.candidate_hash) == 0) p.candidate_hash = _CandidateHash(p);
      if(StringLen(p.request_execution_fingerprint) == 0)
         p.request_execution_fingerprint = _ExecutionFingerprint(p);
      datetime observed_at = _NowServerOrLocal();
      string record_hash = _IntegrityHash(SHADOW_CANDIDATE_SCHEMA_VERSION + "|" + p.candidate_hash + "|"
                                          + p.request_execution_fingerprint + "|"
                                          + decision_stage + "|" + rejection_reason + "|"
                                          + IntegerToString((int)observed_at));
      double risk_dist = MathAbs(p.entry_est - p.sl);
      bool trackable = (p.entry_est > 0.0 && p.sl > 0.0 && p.tp2 > 0.0 && risk_dist > 0.0 &&
                        ((p.is_buy && p.sl < p.entry_est && p.tp2 > p.entry_est) ||
                         (!p.is_buy && p.sl > p.entry_est && p.tp2 < p.entry_est)));
      p.shadow_candidate_record_hash = record_hash;
      p.shadow_candidate_schema_version = SHADOW_CANDIDATE_SCHEMA_VERSION;
      p.shadow_decision_stage = decision_stage;
      p.shadow_rejection_reason = rejection_reason;
      p.shadow_observed_at = observed_at;
      p.shadow_horizon_at = observed_at + MathMax(1, InpShadowCandidateHorizonMinutes) * 60;
      datetime session_open = 0, session_close = 0, no_entry_from = 0, flatten_from = 0, next_tradable = 0;
      string schedule_reason = "";
      if(InpUseBrokerSymbolSessions &&
         QueryBrokerSymbolSessionSchedule(p.symbol, observed_at, session_open, session_close,
                                          no_entry_from, flatten_from, next_tradable, schedule_reason) &&
         session_close > observed_at && session_close < p.shadow_horizon_at){
         p.broker_session_open = session_open;
         p.broker_session_close = session_close;
         p.shadow_horizon_at = session_close;
      }
      p.shadow_outcome_status = (trackable ? "PENDING" : "UNTRACKABLE");
      p.shadow_outcome_reason = (trackable ? "awaiting_hypothetical_price_path" : "invalid_entry_stop_target_contract");
      p.shadow_time_to_event_sec = -1;
      p.shadow_time_to_025r_sec = -1;
      p.shadow_time_to_050r_sec = -1;
      p.shadow_time_to_stop_sec = -1;
      p.shadow_time_to_target_sec = -1;
      p.shadow_reached_025r = false;
      p.shadow_reached_050r = false;
      p.shadow_reached_025r_before_adverse = false;
      p.shadow_reached_050r_before_adverse = false;
      p.shadow_time_to_adverse_threshold_sec = -1;
      p.shadow_025_order_ambiguous = false;
      p.shadow_050_order_ambiguous = false;
      p.shadow_target_before_stop = false;
      p.shadow_stop_before_target = false;
      p.shadow_mfe_before_adverse = false;
      p.shadow_horizon_result = "PENDING";
      p.shadow_censoring_status = (trackable ? "PENDING" : "EXCLUDED_INVALID_CONTRACT");
      p.shadow_ambiguity_reason = "";
      p.shadow_threshold_order_ambiguous = false;
      p.shadow_outcome_ambiguous = !trackable;
      string row = "{";
      row += JsonKVStr("schema_version", SHADOW_CANDIDATE_SCHEMA_VERSION) + ",";
      row += JsonKVStr("event_type", "candidate_observed") + ",";
      row += JsonKVStr("record_hash", record_hash) + ",";
      row += JsonKVInt("candidate_timestamp", (int)observed_at) + ",";
      row += JsonKVInt("setup_timestamp", (int)p.fvg.t_form) + ",";
      row += JsonKVInt("source_sweep_timestamp", (int)(p.source_t_sweep > 0 ? p.source_t_sweep : p.po3.t_sweep)) + ",";
      row += JsonKVInt("observed_at", (int)observed_at) + ",";
      row += JsonKVInt("horizon_at", (int)p.shadow_horizon_at) + ",";
      row += JsonKVInt("broker_session_close", (int)p.broker_session_close) + ",";
      row += JsonKVStr("horizon_termination_policy", p.broker_session_close > 0 && p.shadow_horizon_at == p.broker_session_close
                       ? "broker_session_close" : "configured_horizon") + ",";
      row += JsonKVStr("candidate_id", p.candidate_id) + ",";
      row += JsonKVStr("candidate_hash", p.candidate_hash) + ",";
      row += JsonKVBool("trading_authority", false) + ",";
      row += JsonKVBool("can_trade", false) + ",";
      row += JsonKVStr("decision_stage", decision_stage) + ",";
      row += JsonKVStr("rejection_reason", rejection_reason) + ",";
      bool ai_decision_available = (StringLen(p.ai.decision_schema_version) > 0 &&
                                    StringLen(p.ai.decision_state) > 0);
      if(ai_decision_available){
         row += JsonKVBool("model_raw_allow", p.model_raw_allow) + ",";
         row += JsonKVBool("python_final_allow", p.python_final_allow) + ",";
      } else {
         row += "\"model_raw_allow\":null,\"python_final_allow\":null,";
      }
      if(decision_stage == "pre_ai_reject" || StringFind(decision_stage, "mql_") == 0)
         row += JsonKVBool("mql_final_allow", p.mql_final_allow) + ",";
      else
         row += "\"mql_final_allow\":null,";
      row += JsonKVStr("python_decision_reasons", p.python_decision_reasons) + ",";
      row += JsonKVStr("mql_decision_reasons", p.mql_decision_reasons) + ",";
      row += JsonKVStr("data_quality_status", trackable ? "PENDING_HYPOTHETICAL_OUTCOME" : "UNTRACKABLE_INVALID_CONTRACT") + ",";
      row += JsonKVStr("symbol", p.symbol) + ",";
      row += JsonKVBool("is_buy", p.is_buy) + ",";
      row += JsonKVStr("direction", p.is_buy ? "BUY" : "SELL") + ",";
      row += JsonKVStr("setup_taxonomy_version", p.setup_taxonomy_version) + ",";
      row += JsonKVStr("setup_taxonomy_enum", p.setup_taxonomy_enum) + ",";
      row += JsonKVStr("setup_family", p.setup_family) + ",";
      row += JsonKVStr("setup_class", p.setup_class) + ",";
      row += JsonKVStr("entry_branch", p.entry_branch) + ",";
      row += JsonKVStr("session", p.po3.session_name) + ",";
      row += JsonKVStr("killzone", p.killzone_code) + ",";
      row += JsonKVNum("entry", p.entry_est, 8) + ",";
      row += JsonKVNum("sl", p.sl, 8) + ",";
      row += JsonKVNum("tp1", p.tp1, 8) + ",";
      row += JsonKVNum("tp2", p.tp2, 8) + ",";
      row += JsonKVNum("net_rr", p.effective_rr2, 6) + ",";
      row += JsonKVStr("target_source", p.target_source) + ",";
      row += JsonKVStr("target_model", p.target_model) + ",";
      row += JsonKVStr("tp_model", p.tp_model) + ",";
      row += JsonKVStr("candidate_execution_fingerprint", p.request_execution_fingerprint) + ",";
      row += JsonKVStr("execution_fingerprint", p.request_execution_fingerprint) + ",";
      row += JsonKVStr("assessed_execution_fingerprint", p.assessed_execution_fingerprint) + ",";
      row += JsonKVStr("final_execution_fingerprint", p.final_execution_fingerprint) + ",";
      row += JsonKVNum("execution_cost_r", p.execution_cost_r, 6) + ",";
      row += JsonKVNum("spread_r", p.spread_r, 6) + ",";
      row += JsonKVNum("slippage_r", p.slippage_r, 6) + ",";
      row += JsonKVNum("commission_r", p.commission_r, 6) + ",";
      row += JsonKVStr("cost_source", p.estimated_cost_source) + ",";
      row += JsonKVStr("engine_version", p.engine_version) + ",";
      row += JsonKVStr("git_commit", p.git_commit) + ",";
      row += JsonKVStr("dirty_tree_status", p.dirty_tree_status) + ",";
      row += JsonKVStr("set_file_hash", p.set_file_hash) + ",";
      row += JsonKVStr("runtime_input_hash", p.runtime_input_hash) + ",";
      row += JsonKVStr("prompt_contract_version", p.prompt_contract_version) + ",";
      row += JsonKVStr("model_version", p.ai.model_version) + ",";
      row += JsonKVStr("provider_mode", p.ai.provider_mode) + ",";
      row += JsonKVStr("provider_id", p.ai.provider_id) + ",";
      row += JsonKVStr("model_fingerprint", p.ai.model_fingerprint) + ",";
      row += JsonKVStr("family_profile_version", p.ai.family_profile_version) + ",";
      row += JsonKVStr("retrieval_policy_version", p.ai.retrieval_policy_version) + ",";
      row += JsonKVStr("generation_settings_hash", p.ai.generation_settings_hash) + ",";
      row += JsonKVStr("reasoning_configuration", p.reasoning_configuration) + ",";
      row += JsonKVStr("decision_schema_version", p.ai.decision_schema_version) + ",";
      row += JsonKVStr("target_schema_version", p.target_arbitration_schema_version) + ",";
      row += JsonKVStr("policy_id", p.policy_snapshot_id) + ",";
      row += JsonKVStr("policy_hash", p.policy_hash) + ",";
      row += JsonKVStr("bucket_prior_hash", p.bucket_prior_hash) + ",";
      row += JsonKVStr("management_version", p.management_version) + ",";
      row += JsonKVStr("taxonomy_version", p.setup_taxonomy_version) + ",";
      row += JsonKVStr("feature_version", p.feature_version) + ",";
      row += JsonKVStr("calibration_artifact_id", p.calibration_artifact_id) + ",";
      row += JsonKVStr("repeatability_artifact_id", p.repeatability_artifact_id) + ",";
      row += JsonKVStr("cohort_id", p.cohort_id) + ",";
      row += JsonKVBool("cohort_complete", p.cohort_complete) + ",";
      row += JsonKVNum("fvg_width", MathAbs(p.fvg.upper - p.fvg.lower), 8) + ",";
      row += JsonKVNum("normalized_fvg_minimum", p.fvg.normalized_minimum_price, 8) + ",";
      row += JsonKVBool("normalized_fvg_shadow_pass", p.fvg.normalized_minimum_shadow_pass) + ",";
      row += JsonKVStr("normalized_fvg_mode", p.fvg.normalized_fvg_mode) + ",";
      row += JsonKVStr("normalized_fvg_asset_class", p.fvg.normalized_fvg_asset_class) + ",";
      row += JsonKVStr("normalized_fvg_policy_version", p.fvg.normalized_fvg_policy_version) + ",";
      row += JsonKVInt("normalized_fvg_sample_size", p.fvg.normalized_fvg_sample_size) + ",";
      row += JsonKVBool("pre_entry_features_complete", true) + ",";
      row += JsonKVNum("effective_rr2", p.effective_rr2, 6) + ",";
      row += JsonKVNum("liquidity_rr", p.liquidity_rr, 6) + ",";
      row += JsonKVNum("sequence_quality", p.sequence_quality, 6) + ",";
      row += JsonKVNum("htf_alignment_score", p.htf_alignment_score, 6) + ",";
      row += JsonKVNum("stop_quality_score", p.stop_quality_score, 6) + ",";
      row += JsonKVNum("trend_strength", p.trend_strength, 6) + ",";
      row += JsonKVNum("session_vol_ratio", p.session_vol_ratio, 6) + ",";
      row += JsonKVNum("fvg_score", p.fvg.score, 6) + ",";
      row += JsonKVNum("adverse_context_score", p.adverse_context_score, 6) + ",";
      row += JsonKVNum("vwap_dist_atr", p.vwap_dist_atr, 6) + ",";
      row += JsonKVNum("news_risk", p.news_risk, 6) + ",";
      row += "\"hypothetical_outcome\":null,\"target_before_stop\":null,\"stop_before_target\":null,";
      row += JsonKVNum("adverse_threshold_r", SHADOW_ADVERSE_THRESHOLD_R, 4) + ",";
      row += "\"reached_0_25r_before_adverse_threshold\":null,\"reached_0_50r_before_adverse_threshold\":null,";
      row += "\"reached_0_25r_before_adverse\":null,\"reached_0_50r_before_adverse\":null,";
      row += "\"mfe_r\":null,\"mae_r\":null,\"time_to_0_25r_sec\":null,\"time_to_0_50r_sec\":null,";
      row += "\"time_to_stop_sec\":null,\"time_to_target_sec\":null,\"time_to_adverse_threshold_sec\":null,\"time_to_event_sec\":null,";
      row += "\"horizon_result\":null,\"ambiguity_status\":null,\"censoring_status\":\"PENDING\"";
      row += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\shadow_candidates.jsonl", row + "\n");
      if(trackable && !_PendingResearchRecordExists(m_shadow_pending, record_hash, true)){
         int n = ArraySize(m_shadow_pending);
         ArrayResize(m_shadow_pending, n + 1);
         m_shadow_pending[n] = p;
      }
   }

   bool _FindCandidateAssessmentJson(const AiDecision &dec,
                                     const string candidate_hash,
                                     string &assessment) const {
      assessment = "";
      int count = JsonArrayObjectCount(dec.candidate_assessments_json);
      for(int i=0; i<count; i++){
         string item = "", item_hash = "";
         if(!JsonArrayGetObject(dec.candidate_assessments_json, i, item)) continue;
         if(!JsonGetStringStrict(item, "candidate_hash", item_hash)) continue;
         if(item_hash != candidate_hash) continue;
         assessment = item;
         return true;
      }
      return false;
   }

   void _WriteShadowDecisionUpdate(const TradePlan &source,
                                   const AiDecision &dec,
                                   const string decision_stage,
                                   const string rejection_reason,
                                   const bool mql_final_allow) {
      if(!InpShadowCandidateLedgerEnable) return;
      string assessment = "";
      bool assessment_found = _FindCandidateAssessmentJson(dec, source.candidate_hash, assessment);
      bool model_allow = false, python_allow = false;
      string decision_state = "UNAVAILABLE";
      if(assessment_found){
         if(!JsonGetBoolStrict(assessment, "model_raw_allow", model_allow) ||
            !JsonGetBoolStrict(assessment, "python_final_allow", python_allow) ||
            !JsonGetStringStrict(assessment, "decision_state", decision_state)){
            assessment_found = false;
            model_allow = false;
            python_allow = false;
            decision_state = "INVALID";
         }
      }
      string row = "{";
      row += JsonKVStr("schema_version", SHADOW_CANDIDATE_SCHEMA_VERSION) + ",";
      row += JsonKVStr("event_type", "candidate_decision_update") + ",";
      row += JsonKVInt("observed_at", (int)_NowServerOrLocal()) + ",";
      row += JsonKVStr("candidate_id", source.candidate_id) + ",";
      row += JsonKVStr("candidate_hash", source.candidate_hash) + ",";
      row += JsonKVStr("execution_fingerprint", source.request_execution_fingerprint) + ",";
      row += JsonKVStr("assessed_execution_fingerprint", source.assessed_execution_fingerprint) + ",";
      row += JsonKVStr("final_execution_fingerprint", source.final_execution_fingerprint) + ",";
      row += JsonKVNum("entry", source.entry_est, 8) + ",";
      row += JsonKVNum("sl", source.sl, 8) + ",";
      row += JsonKVNum("tp1", source.tp1, 8) + ",";
      row += JsonKVNum("tp2", source.tp2, 8) + ",";
      row += JsonKVNum("net_rr", source.effective_rr2, 6) + ",";
      row += JsonKVStr("target_source", source.target_source) + ",";
      row += JsonKVStr("target_model", source.target_model) + ",";
      row += JsonKVNum("execution_cost_r", source.execution_cost_r, 6) + ",";
      row += JsonKVNum("spread_r", source.spread_r, 6) + ",";
      row += JsonKVStr("decision_stage", decision_stage) + ",";
      row += JsonKVStr("decision_state", decision_state) + ",";
      row += JsonKVStr("rejection_reason", rejection_reason) + ",";
      row += JsonKVBool("assessment_found", assessment_found) + ",";
      if(assessment_found){
         row += JsonKVBool("model_raw_allow", model_allow) + ",";
         row += JsonKVBool("python_final_allow", python_allow) + ",";
      } else {
         row += "\"model_raw_allow\":null,\"python_final_allow\":null,";
      }
      row += JsonKVBool("mql_final_allow", mql_final_allow) + ",";
      row += JsonKVStr("python_reasons", dec.reasons_json) + ",";
      row += JsonKVStr("mql_reasons", rejection_reason) + ",";
      row += JsonKVStr("decision_schema_version", dec.decision_schema_version) + ",";
      row += JsonKVStr("model_version", dec.model_version) + ",";
      row += JsonKVStr("prompt_contract_version", dec.prompt_contract_version) + ",";
      row += JsonKVStr("provider_mode", dec.provider_mode) + ",";
      row += JsonKVStr("provider_id", dec.provider_id) + ",";
      row += JsonKVStr("actual_model_id", dec.actual_model_id) + ",";
      row += JsonKVStr("model_fingerprint", dec.model_fingerprint) + ",";
      row += JsonKVStr("family_profile_version", dec.family_profile_version) + ",";
      row += JsonKVStr("retrieval_policy_version", dec.retrieval_policy_version) + ",";
      row += JsonKVStr("generation_settings_hash", dec.generation_settings_hash) + ",";
      row += JsonKVStr("input_fingerprint", dec.input_fingerprint) + ",";
      row += JsonKVStr("target_schema_version", dec.target_arbitration_schema_version) + ",";
      row += "\"decision_field_authority\":" + (StringLen(dec.decision_field_authority_json) > 0 ? dec.decision_field_authority_json : "{}") + ",";
      row += JsonKVStr("cohort_id", source.cohort_id) + ",";
      row += JsonKVBool("cohort_complete", source.cohort_complete) + ",";
      row += "\"candidate_assessment\":" + (assessment_found ? assessment : "null") + ",";
      row += JsonKVBool("trading_authority", false);
      row += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\shadow_candidates.jsonl", row + "\n");

      bool changed = false;
      for(int i=0; i<ArraySize(m_shadow_pending); i++){
         if(m_shadow_pending[i].candidate_hash != source.candidate_hash) continue;
         if(m_shadow_pending[i].request_execution_fingerprint != source.request_execution_fingerprint) continue;
         m_shadow_pending[i].shadow_decision_stage = decision_stage;
         m_shadow_pending[i].shadow_rejection_reason = rejection_reason;
         m_shadow_pending[i].model_raw_allow = model_allow;
         m_shadow_pending[i].python_final_allow = python_allow;
         m_shadow_pending[i].mql_final_allow = mql_final_allow;
         m_shadow_pending[i].python_decision_reasons = dec.reasons_json;
         m_shadow_pending[i].mql_decision_reasons = rejection_reason;
         m_shadow_pending[i].ai = dec;
         m_shadow_pending[i].decision_field_authority_json = dec.decision_field_authority_json;
         m_shadow_pending[i].entry_est = source.entry_est;
         m_shadow_pending[i].sl = source.sl;
         m_shadow_pending[i].tp1 = source.tp1;
         m_shadow_pending[i].tp2 = source.tp2;
         m_shadow_pending[i].effective_rr2 = source.effective_rr2;
         m_shadow_pending[i].target_source = source.target_source;
         m_shadow_pending[i].target_model = source.target_model;
         m_shadow_pending[i].tp_model = source.tp_model;
         m_shadow_pending[i].request_execution_fingerprint = source.request_execution_fingerprint;
         m_shadow_pending[i].assessed_execution_fingerprint = source.assessed_execution_fingerprint;
         m_shadow_pending[i].final_execution_fingerprint = source.final_execution_fingerprint;
         m_shadow_pending[i].execution_cost_r = source.execution_cost_r;
         m_shadow_pending[i].spread_r = source.spread_r;
         m_shadow_pending[i].slippage_r = source.slippage_r;
         m_shadow_pending[i].engine_version = source.engine_version;
         m_shadow_pending[i].git_commit = source.git_commit;
         m_shadow_pending[i].dirty_tree_status = source.dirty_tree_status;
         m_shadow_pending[i].set_file_hash = source.set_file_hash;
         m_shadow_pending[i].runtime_input_hash = source.runtime_input_hash;
         m_shadow_pending[i].prompt_contract_version = source.prompt_contract_version;
         m_shadow_pending[i].reasoning_configuration = source.reasoning_configuration;
         m_shadow_pending[i].policy_snapshot_id = source.policy_snapshot_id;
         m_shadow_pending[i].policy_hash = source.policy_hash;
         m_shadow_pending[i].bucket_prior_hash = source.bucket_prior_hash;
         m_shadow_pending[i].management_version = source.management_version;
         m_shadow_pending[i].feature_version = source.feature_version;
         m_shadow_pending[i].calibration_artifact_id = source.calibration_artifact_id;
         m_shadow_pending[i].repeatability_artifact_id = source.repeatability_artifact_id;
         m_shadow_pending[i].cohort_id = source.cohort_id;
         m_shadow_pending[i].cohort_complete = source.cohort_complete;
         changed = true;
      }
      if(changed) m_state.SavePlans(m_state.ShadowPendingPath(), m_shadow_pending);
   }

   bool _EvaluateShadowCandidate(TradePlan &p, string &reason) {
      reason = "";
      p.shadow_outcome_ambiguous = false;
      p.shadow_threshold_order_ambiguous = false;
      p.shadow_ambiguity_reason = "";
      p.shadow_reached_025r = false;
      p.shadow_reached_050r = false;
      p.shadow_reached_025r_before_adverse = false;
      p.shadow_reached_050r_before_adverse = false;
      p.shadow_time_to_adverse_threshold_sec = -1;
      p.shadow_025_order_ambiguous = false;
      p.shadow_050_order_ambiguous = false;
      p.shadow_target_before_stop = false;
      p.shadow_stop_before_target = false;
      p.shadow_mfe_before_adverse = false;
      p.shadow_time_to_event_sec = -1;
      p.shadow_time_to_025r_sec = -1;
      p.shadow_time_to_050r_sec = -1;
      p.shadow_time_to_stop_sec = -1;
      p.shadow_time_to_target_sec = -1;
      p.shadow_horizon_result = "PENDING";
      p.shadow_censoring_status = "PENDING";
      double entry = p.entry_est;
      double sl = p.sl;
      double tp = p.tp2;
      double risk_dist = MathAbs(entry - sl);
      if(entry <= 0.0 || sl <= 0.0 || tp <= 0.0 || risk_dist <= 0.0 ||
         (p.is_buy && !(sl < entry && tp > entry)) ||
         (!p.is_buy && !(sl > entry && tp < entry))){
         p.shadow_outcome_status = "UNTRACKABLE";
         p.shadow_outcome_reason = "invalid_entry_stop_target_contract";
         p.shadow_outcome_ambiguous = true;
         p.shadow_ambiguity_reason = "invalid_entry_stop_target_contract";
         p.shadow_horizon_result = "UNTRACKABLE";
         p.shadow_censoring_status = "EXCLUDED_INVALID_CONTRACT";
         reason = p.shadow_outcome_reason;
         return true;
      }
      datetime now = _NowServerOrLocal();
      datetime evaluation_end = now;
      if(p.shadow_horizon_at > 0 && evaluation_end > p.shadow_horizon_at)
         evaluation_end = p.shadow_horizon_at;
      if(evaluation_end <= p.shadow_observed_at){
         reason = "awaiting_first_closed_price_bar";
         return false;
      }
      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      int got = CopyRates(p.symbol, PERIOD_M1, p.shadow_observed_at, evaluation_end, rates);
      if(got <= 0){
         if(now < p.shadow_horizon_at){ reason = "price_path_temporarily_unavailable"; return false; }
         p.shadow_outcome_status = "UNAVAILABLE";
         p.shadow_outcome_reason = "price_path_unavailable_at_horizon";
         p.shadow_outcome_ambiguous = true;
         p.shadow_ambiguity_reason = "price_path_unavailable_at_horizon";
         p.shadow_horizon_result = "DATA_LOSS";
         p.shadow_censoring_status = "EXCLUDED_DATA_LOSS";
         reason = p.shadow_outcome_reason;
         return true;
      }

      double max_favorable = 0.0;
      double max_adverse = 0.0;
      double last_close = entry;
      datetime event_at = 0;
      datetime favorable_025_at = 0;
      datetime favorable_050_at = 0;
      datetime adverse_threshold_at = 0;
      bool resolved = false;
      for(int i=0; i<got; i++){
         last_close = rates[i].close;
         double favorable = (p.is_buy ? rates[i].high - entry : entry - rates[i].low) / risk_dist;
         double adverse = (p.is_buy ? rates[i].low - entry : entry - rates[i].high) / risk_dist;
         max_favorable = MathMax(max_favorable, favorable);
         max_adverse = MathMin(max_adverse, adverse);
         bool favorable_025_hit = (favorable >= 0.25);
         bool favorable_050_hit = (favorable >= 0.50);
         bool adverse_threshold_hit = (adverse <= -SHADOW_ADVERSE_THRESHOLD_R);
         if(favorable_025_at == 0 && adverse_threshold_at == 0 && favorable_025_hit && adverse_threshold_hit){
            p.shadow_025_order_ambiguous = true;
            p.shadow_ambiguity_reason = "favorable_0_25r_and_adverse_threshold_same_m1_bar";
         }
         if(favorable_050_at == 0 && adverse_threshold_at == 0 && favorable_050_hit && adverse_threshold_hit){
            p.shadow_050_order_ambiguous = true;
            if(StringLen(p.shadow_ambiguity_reason) > 0) p.shadow_ambiguity_reason += ";";
            p.shadow_ambiguity_reason += "favorable_0_50r_and_adverse_threshold_same_m1_bar";
         }
         if(favorable_025_at == 0 && favorable_025_hit) favorable_025_at = rates[i].time;
         if(favorable_050_at == 0 && favorable_050_hit) favorable_050_at = rates[i].time;
         if(adverse_threshold_at == 0 && adverse_threshold_hit) adverse_threshold_at = rates[i].time;
         p.shadow_threshold_order_ambiguous = (p.shadow_025_order_ambiguous || p.shadow_050_order_ambiguous);
         bool sl_hit = (p.is_buy ? rates[i].low <= sl : rates[i].high >= sl);
         bool tp_hit = (p.is_buy ? rates[i].high >= tp : rates[i].low <= tp);
         if(sl_hit && tp_hit){
            p.shadow_outcome_status = "AMBIGUOUS";
            p.shadow_outcome_reason = "sl_and_tp_touched_without_tick_sequence";
            p.shadow_outcome_ambiguous = true;
            p.shadow_ambiguity_reason = "sl_and_tp_touched_without_tick_sequence";
            p.shadow_time_to_stop_sec = (int)MathMax(0, (long)(rates[i].time - p.shadow_observed_at));
            p.shadow_time_to_target_sec = p.shadow_time_to_stop_sec;
            p.shadow_horizon_result = "AMBIGUOUS_SAME_BAR_TERMINAL";
            p.shadow_censoring_status = "EXCLUDED_AMBIGUOUS";
            event_at = rates[i].time;
            resolved = true;
            break;
         }
         if(sl_hit || tp_hit){
            p.shadow_outcome_status = (sl_hit ? "RESOLVED_STOP" : "RESOLVED_TARGET");
            p.shadow_outcome_reason = "terminal_price_level_reached";
            p.shadow_outcome_r = (sl_hit ? -1.0 : MathAbs(tp - entry) / risk_dist)
                                 - MathMax(0.0, p.execution_cost_r);
            if(sl_hit){
               p.shadow_stop_before_target = true;
               p.shadow_time_to_stop_sec = (int)MathMax(0, (long)(rates[i].time - p.shadow_observed_at));
               p.shadow_horizon_result = "STOP_FIRST";
            } else {
               p.shadow_target_before_stop = true;
               p.shadow_time_to_target_sec = (int)MathMax(0, (long)(rates[i].time - p.shadow_observed_at));
               p.shadow_horizon_result = "TARGET_FIRST";
            }
            p.shadow_censoring_status = "UNCENSORED_TERMINAL";
            event_at = rates[i].time;
            resolved = true;
            break;
         }
      }
      p.shadow_mfe_r = max_favorable;
      p.shadow_mae_r = max_adverse;
      p.shadow_reached_025r = (favorable_025_at > 0);
      p.shadow_reached_050r = (favorable_050_at > 0);
      p.shadow_time_to_025r_sec = (favorable_025_at > 0
                                   ? (int)MathMax(0, (long)(favorable_025_at - p.shadow_observed_at)) : -1);
      p.shadow_time_to_050r_sec = (favorable_050_at > 0
                                   ? (int)MathMax(0, (long)(favorable_050_at - p.shadow_observed_at)) : -1);
      p.shadow_time_to_adverse_threshold_sec = (adverse_threshold_at > 0
                                                ? (int)MathMax(0, (long)(adverse_threshold_at - p.shadow_observed_at)) : -1);
      if(!p.shadow_025_order_ambiguous && favorable_025_at > 0 &&
         (adverse_threshold_at == 0 || favorable_025_at < adverse_threshold_at))
         p.shadow_reached_025r_before_adverse = true;
      if(!p.shadow_050_order_ambiguous && favorable_050_at > 0 &&
         (adverse_threshold_at == 0 || favorable_050_at < adverse_threshold_at))
         p.shadow_reached_050r_before_adverse = true;
      p.shadow_mfe_before_adverse = p.shadow_reached_025r_before_adverse;
      if(!resolved && now < p.shadow_horizon_at){
         p.shadow_outcome_status = "PENDING";
         p.shadow_outcome_reason = "awaiting_hypothetical_price_path";
         reason = p.shadow_outcome_reason;
         return false;
      }
      if(!resolved){
         p.shadow_outcome_status = "RESOLVED_HORIZON";
         bool ended_at_session_close = (p.broker_session_close > 0 && p.shadow_horizon_at == p.broker_session_close);
         p.shadow_outcome_reason = (ended_at_session_close ? "broker_session_close_reached" : "configured_horizon_reached");
         p.shadow_outcome_r = (p.is_buy ? last_close - entry : entry - last_close) / risk_dist
                              - MathMax(0.0, p.execution_cost_r);
         string horizon_prefix = (ended_at_session_close ? "SESSION_CLOSE" : "HORIZON");
         p.shadow_horizon_result = horizon_prefix + (p.shadow_outcome_r > 0.0 ? "_POSITIVE" :
                                    (p.shadow_outcome_r < 0.0 ? "_NEGATIVE" : "_FLAT"));
         p.shadow_censoring_status = (ended_at_session_close
                                      ? "RIGHT_CENSORED_SESSION_CLOSE"
                                      : "RIGHT_CENSORED_HORIZON");
         event_at = evaluation_end;
      }
      p.shadow_evaluated_at = now;
      p.shadow_time_to_event_sec = (event_at >= p.shadow_observed_at
                                    ? (int)(event_at - p.shadow_observed_at) : -1);
      reason = p.shadow_outcome_reason;
      return true;
   }

   void _AppendShadowOutcomeResolution(const TradePlan &p) {
      string row = "{";
      row += JsonKVStr("schema_version", SHADOW_CANDIDATE_SCHEMA_VERSION) + ",";
      row += JsonKVStr("event_type", "hypothetical_outcome_resolution") + ",";
      row += JsonKVStr("parent_record_hash", p.shadow_candidate_record_hash) + ",";
      row += JsonKVStr("candidate_id", p.candidate_id) + ",";
      row += JsonKVStr("candidate_hash", p.candidate_hash) + ",";
      row += JsonKVStr("execution_fingerprint", p.request_execution_fingerprint) + ",";
      row += JsonKVStr("assessed_execution_fingerprint", p.assessed_execution_fingerprint) + ",";
      row += JsonKVStr("final_execution_fingerprint", p.final_execution_fingerprint) + ",";
      row += JsonKVStr("direction", p.is_buy ? "BUY" : "SELL") + ",";
      row += JsonKVNum("entry", p.entry_est, 8) + ",";
      row += JsonKVNum("sl", p.sl, 8) + ",";
      row += JsonKVNum("tp1", p.tp1, 8) + ",";
      row += JsonKVNum("tp2", p.tp2, 8) + ",";
      row += JsonKVNum("net_rr", p.effective_rr2, 6) + ",";
      row += JsonKVNum("execution_cost_r", p.execution_cost_r, 6) + ",";
      row += JsonKVStr("target_source", p.target_source) + ",";
      row += JsonKVStr("target_model", p.target_model) + ",";
      row += JsonKVStr("decision_stage", p.shadow_decision_stage) + ",";
      row += JsonKVStr("decision_state", p.ai.decision_state) + ",";
      row += JsonKVStr("rejection_reason", p.shadow_rejection_reason) + ",";
      row += JsonKVBool("model_raw_allow", p.model_raw_allow) + ",";
      row += JsonKVBool("python_final_allow", p.python_final_allow) + ",";
      row += JsonKVBool("mql_final_allow", p.mql_final_allow) + ",";
      row += JsonKVBool("trading_authority", false) + ",";
      row += JsonKVBool("can_trade", false) + ",";
      row += JsonKVInt("evaluated_at", (int)p.shadow_evaluated_at) + ",";
      row += JsonKVStr("hypothetical_outcome", p.shadow_outcome_status) + ",";
      row += JsonKVStr("outcome_reason", p.shadow_outcome_reason) + ",";
      if(p.shadow_outcome_ambiguous) row += "\"result_r\":null,";
      else row += JsonKVNum("result_r", p.shadow_outcome_r, 6) + ",";
      row += JsonKVNum("mfe_r", p.shadow_mfe_r, 6) + ",";
      row += JsonKVNum("mae_r", p.shadow_mae_r, 6) + ",";
      row += JsonKVInt("time_to_event_sec", p.shadow_time_to_event_sec) + ",";
      if(p.shadow_time_to_025r_sec >= 0) row += JsonKVInt("time_to_0_25r_sec", p.shadow_time_to_025r_sec) + ",";
      else row += "\"time_to_0_25r_sec\":null,";
      if(p.shadow_time_to_050r_sec >= 0) row += JsonKVInt("time_to_0_50r_sec", p.shadow_time_to_050r_sec) + ",";
      else row += "\"time_to_0_50r_sec\":null,";
      if(p.shadow_time_to_stop_sec >= 0) row += JsonKVInt("time_to_stop_sec", p.shadow_time_to_stop_sec) + ",";
      else row += "\"time_to_stop_sec\":null,";
      if(p.shadow_time_to_target_sec >= 0) row += JsonKVInt("time_to_target_sec", p.shadow_time_to_target_sec) + ",";
      else row += "\"time_to_target_sec\":null,";
      if(p.shadow_time_to_adverse_threshold_sec >= 0) row += JsonKVInt("time_to_adverse_threshold_sec", p.shadow_time_to_adverse_threshold_sec) + ",";
      else row += "\"time_to_adverse_threshold_sec\":null,";
      if(p.shadow_censoring_status == "UNCENSORED_TERMINAL"){
         row += JsonKVBool("target_before_stop", p.shadow_target_before_stop) + ",";
         row += JsonKVBool("stop_before_target", p.shadow_stop_before_target) + ",";
      } else {
         row += "\"target_before_stop\":null,\"stop_before_target\":null,";
      }
      row += JsonKVNum("adverse_threshold_r", SHADOW_ADVERSE_THRESHOLD_R, 4) + ",";
      if(p.shadow_025_order_ambiguous){
         row += "\"reached_0_25r_before_adverse_threshold\":null,\"reached_0_25r_before_adverse\":null,";
      } else {
         row += JsonKVBool("reached_0_25r_before_adverse_threshold", p.shadow_reached_025r_before_adverse) + ",";
         row += JsonKVBool("reached_0_25r_before_adverse", p.shadow_reached_025r_before_adverse) + ",";
      }
      if(p.shadow_050_order_ambiguous){
         row += "\"reached_0_50r_before_adverse_threshold\":null,\"reached_0_50r_before_adverse\":null,";
      } else {
         row += JsonKVBool("reached_0_50r_before_adverse_threshold", p.shadow_reached_050r_before_adverse) + ",";
         row += JsonKVBool("reached_0_50r_before_adverse", p.shadow_reached_050r_before_adverse) + ",";
      }
      row += JsonKVBool("threshold_0_25_order_ambiguous", p.shadow_025_order_ambiguous) + ",";
      row += JsonKVBool("threshold_0_50_order_ambiguous", p.shadow_050_order_ambiguous) + ",";
      row += JsonKVBool("reached_0_25r", p.shadow_reached_025r) + ",";
      row += JsonKVBool("reached_0_50r", p.shadow_reached_050r) + ",";
      row += JsonKVStr("horizon_result", p.shadow_horizon_result) + ",";
      row += JsonKVStr("censoring_status", p.shadow_censoring_status) + ",";
      row += JsonKVStr("ambiguity_reason", p.shadow_ambiguity_reason) + ",";
      row += JsonKVStr("cohort_id", p.cohort_id) + ",";
      row += JsonKVBool("cohort_complete", p.cohort_complete) + ",";
      row += JsonKVBool("ambiguous", p.shadow_outcome_ambiguous) + ",";
      row += JsonKVStr("data_quality_status",
                       (p.shadow_outcome_ambiguous ? "EXCLUDED_AMBIGUOUS" :
                        (p.shadow_censoring_status == "UNCENSORED_TERMINAL" ? "RESOLVED_CLEAN_PRICE_PATH" :
                         "RESOLVED_CENSORED_PRICE_PATH")));
      row += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\shadow_candidates.jsonl", row + "\n");
   }

   void _MaintainShadowCandidateOutcomes() {
      bool changed = false;
      for(int i=ArraySize(m_shadow_pending)-1; i>=0; i--){
         TradePlan p = m_shadow_pending[i];
         string reason = "";
         if(!_EvaluateShadowCandidate(p, reason)){
            m_shadow_pending[i] = p;
            continue;
         }
         _AppendShadowOutcomeResolution(p);
         int last = ArraySize(m_shadow_pending) - 1;
         if(i != last) m_shadow_pending[i] = m_shadow_pending[last];
         ArrayResize(m_shadow_pending, last);
         changed = true;
      }
      if(changed) m_state.SavePlans(m_state.ShadowPendingPath(), m_shadow_pending);
   }

   void _WriteRejectedShadowCandidate(const TradePlan &base,
                                      const FVGZone &zone,
                                      const string branch,
                                      const string reason) {
      TradePlan shadow = base;
      shadow.fvg = zone;
      shadow.entry_branch = branch;
      shadow.entry_model = branch;
      _WriteShadowCandidateRecord(shadow, "pre_ai_reject", reason);
   }

   string _NormToken(string value) const {
      value = _TrimCopy(value);
      StringToLower(value);
      return value;
   }

   string _ExclusiveModelName() const {
      return "breaker_retest_virgin_strong_origin";
   }

   string _OriginQuality(const TradePlan &plan, const FVGZone &fvg) const {
      string quality = _NormToken(plan.origin_quality);
      if(quality == "strong_origin") return "strong_origin";
      double score = MathMax(plan.fvg.origin_score, fvg.origin_score);
      if(score >= InpStrongOriginMinScore) return "strong_origin";
      if(score > 0.0) return "weak_origin";
      return "unknown_origin";
   }

   bool _ExclusiveRejectedVirginState(const string value) const {
      string v = _NormToken(value);
      return (v == "touched_fvg" ||
              v == "edge_touched" ||
              v == "mid_mitigated_fvg" ||
              v == "mid_mitigated_reentry" ||
              v == "fully_mitigated" ||
              v == "fully_mitigated_fvg" ||
              v == "deep_filled" ||
              v == "stale" ||
              v == "stale_fvg" ||
              v == "structure_invalidated" ||
              v == "invalidated_fvg" ||
              v == "entry_invalid_fvg" ||
              v == "inverse_fvg");
   }

   bool IsBreakerRetestVirginStrongOrigin(const TradePlan &plan,
                                          const FVGZone &fvg,
                                          const PO3Context &ctx,
                                          string &fail_reason) const {
      fail_reason = "ok";
      bool breaker = (_NormToken(plan.entry_branch) == "breaker_retest" ||
                      _NormToken(plan.entry_model) == "breaker_retest" ||
                      _NormToken(plan.setup_family) == "breaker_retest");
      if(!breaker){
         fail_reason = "not_breaker_retest";
         return false;
      }

      bool blocked_fvg = (fvg.touched || fvg.mitigated || fvg.mid_mitigated ||
                          fvg.fully_filled || fvg.invalidated ||
                          fvg.entry_invalid || fvg.structure_invalidated);
      if(_ExclusiveRejectedVirginState(fvg.mitigation_state) ||
         _ExclusiveRejectedVirginState(plan.fvg_execution_class) ||
         _ExclusiveRejectedVirginState(fvg.execution_class))
         blocked_fvg = true;

      bool virgin = (!blocked_fvg &&
                     (_NormToken(fvg.mitigation_state) == "virgin" ||
                      _NormToken(fvg.mitigation_state) == "untouched" ||
                      _NormToken(plan.fvg_execution_class) == "virgin_fvg" ||
                      _NormToken(fvg.execution_class) == "virgin_fvg"));
      if(!virgin){
         fail_reason = "not_virgin_fvg";
         return false;
      }

      bool strong_origin = (_OriginQuality(plan, fvg) == "strong_origin");
      if(!strong_origin){
         fail_reason = "not_strong_origin";
         return false;
      }

      return true;
   }

   void _StampExclusiveModelFields(TradePlan &p, const bool passed, const string fail_reason) {
      if(StringLen(p.exclusive_model_name) == 0) p.exclusive_model_name = _ExclusiveModelName();
      p.origin_quality = _OriginQuality(p, p.fvg);
      p.exclusive_model_mode = InpOnlyBreakerRetestVirginStrongOrigin;
      p.exclusive_model_passed = (InpOnlyBreakerRetestVirginStrongOrigin && passed);
      p.exclusive_fail_reason = (passed ? "" : fail_reason);
   }

   string _ExclusiveModelRejectDetail(const TradePlan &p, const string fail_reason) const {
      string origin_quality = _OriginQuality(p, p.fvg);
      return "exclusive_fail_reason=" + _ReasonCode(fail_reason)
             + " entry_branch=" + _ReasonCode(p.entry_branch)
             + " entry_model=" + _ReasonCode(p.entry_model)
             + " setup_family=" + _ReasonCode(p.setup_family)
             + " setup_class=" + _ReasonCode(p.setup_class)
             + " fvg_execution_class=" + _ReasonCode(p.fvg_execution_class)
             + " fvg_mitigation_state=" + _ReasonCode(p.fvg.mitigation_state)
             + " origin_score=" + DoubleToString(p.fvg.origin_score, 4)
             + " origin_quality=" + _ReasonCode(origin_quality);
   }

   void _TrackExclusiveModelFilterResult(const bool passed, const string fail_reason, const bool count_funnel) {
      if(!count_funnel) return;
      m_funnel_exclusive_model_filter_checked++;
      if(passed){
         m_funnel_exclusive_model_filter_passed++;
         return;
      }
      m_funnel_exclusive_model_filter_rejected++;
      if(fail_reason == "not_breaker_retest") m_funnel_exclusive_model_filter_reject_not_breaker++;
      else if(fail_reason == "not_virgin_fvg") m_funnel_exclusive_model_filter_reject_not_virgin++;
      else if(fail_reason == "not_strong_origin") m_funnel_exclusive_model_filter_reject_not_strong_origin++;
   }

   bool _ApplyExclusiveModelFilter(TradePlan &p, const string stage, const bool count_funnel=true) {
      if(!InpOnlyBreakerRetestVirginStrongOrigin){
         if(StringLen(p.origin_quality) == 0) p.origin_quality = _OriginQuality(p, p.fvg);
         if(StringLen(p.exclusive_model_name) == 0) p.exclusive_model_name = _ExclusiveModelName();
         return true;
      }

      string fail_reason = "";
      bool passed = IsBreakerRetestVirginStrongOrigin(p, p.fvg, p.po3, fail_reason);
      _StampExclusiveModelFields(p, passed, fail_reason);
      _TrackExclusiveModelFilterResult(passed, fail_reason, count_funnel);
      if(passed) return true;

      if(InpExclusiveModelFilterLogRejected){
         _LogSetupReject(p.symbol, stage, "exclusive_breaker_retest_virgin_strong_origin_only",
                         _ExclusiveModelRejectDetail(p, fail_reason));
      }
      return false;
   }

   bool _ExclusiveModelPreExecutionOk(const TradePlan &p) {
      if(!InpOnlyBreakerRetestVirginStrongOrigin) return true;
      TradePlan check = p;
      _InitializeNarrativeFields(check);
      string fail_reason = "";
      if(IsBreakerRetestVirginStrongOrigin(check, check.fvg, check.po3, fail_reason))
         return true;
      _LogSetupReject(check.symbol, "pre_execution_integrity", "exclusive_model_integrity_failed",
                      _ExclusiveModelRejectDetail(check, fail_reason));
      _Journal(check.symbol + " pre-execution exclusive model integrity failed reason=" + fail_reason);
      return false;
   }

   bool _IsStaleFvgPlan(const TradePlan &p) const {
      string cls = _NormToken(p.fvg_execution_class);
      string zone_cls = _NormToken(p.fvg.execution_class);
      string state = _NormToken(p.fvg.mitigation_state);
      return (cls == "stale_fvg" || zone_cls == "stale_fvg" || state == "stale" || state == "stale_fvg");
   }

   bool _IsTouchedFvgPlan(const TradePlan &p) const {
      string cls = _NormToken(p.fvg_execution_class);
      string zone_cls = _NormToken(p.fvg.execution_class);
      string state = _NormToken(p.fvg.mitigation_state);
      return (p.fvg.touched || p.fvg.mid_mitigated ||
              cls == "touched_fvg" || cls == "mid_mitigated_fvg" || cls == "mid_mitigated_reentry" ||
              zone_cls == "touched_fvg" || zone_cls == "mid_mitigated_fvg" ||
              state == "touched" || state == "edge_touched" || state == "mid_mitigated");
   }

   bool _ObstacleIsCrossedOpposing(const string obstacle_kind) const {
      string k = _NormToken(obstacle_kind);
      return (StringFind(k, "crossed") >= 0 &&
              (StringFind(k, "opposing") >= 0 || StringFind(k, "imbalance") >= 0));
   }

   bool _TokenIsSyntheticFallback(const string value) const {
      string v = _NormToken(value);
      return (v == "synthetic_rr_fallback" ||
              v == "synthetic_rr_capped_to_max_distance" ||
              v == "ai_selected_synthetic_rr_capped_to_max_distance" ||
              v == "ai_selected_synthetic_rr_fallback" ||
              v == "fallback" ||
              v == "synthetic" ||
              StringFind(v, "synthetic_rr_fallback") >= 0 ||
              StringFind(v, "synthetic_rr_capped") >= 0);
   }

   bool _TokenIsCappedTarget(const string value) const {
      string v = _NormToken(value);
      return (v == "capped_before_obstacle" ||
              v == "ai_selected_capped_before_obstacle" ||
              StringFind(v, "capped_before") >= 0 ||
              StringFind(v, "cap_before") >= 0);
   }

   bool _TokenIsPartialThenLiquidity(const string value) const {
      string v = _NormToken(value);
      return (v == "partial_before_obstacle_then_liquidity" ||
              v == "partial_then_liquidity" ||
              v == "ai_selected_partial_then_liquidity");
   }

   bool _PlanUsesSyntheticFallback(const TradePlan &p) const {
      return (_TokenIsSyntheticFallback(p.ai_chosen_target_model) ||
              _TokenIsSyntheticFallback(p.target_source) ||
              _TokenIsSyntheticFallback(p.tp_model) ||
              _TokenIsSyntheticFallback(p.target_model));
   }

   bool _AiTargetArbitrationHasAuthority(const TradePlan &p) const {
      return (InpRequireAITargetArbitrationOnObstacle &&
              !InpHardRejectCrossedObstacleTarget &&
              StringLen(p.ai_chosen_target_model) > 0 &&
              (p.liquidity_target_blocked_by_obstacle || _ObstacleIsCrossedOpposing(p.obstacle_kind)));
   }

   bool _HardSuppressionGate(const TradePlan &p, string &reason) const {
      reason = "";
      string family_group = _FamilyGroup(p);
      string family = _NormToken(p.setup_family);
      string setup_class = _NormToken(p.setup_class);
      string branch = _NormToken(p.entry_branch);
      bool stale = _IsStaleFvgPlan(p);
      bool touched = _IsTouchedFvgPlan(p);

      if(InpTradeOnlyKillzones && !p.po3.in_killzone){
         reason = "trade_only_killzone_block";
         return false;
      }
      bool micro_bisi_sibi_family = (family == "micro_bisi_sibi" ||
                                     family == "micro_bisi_sibi_edge" ||
                                     StringFind(family, "micro_bisi_sibi") >= 0 ||
                                     setup_class == "micro_bisi_sibi" ||
                                     setup_class == "micro_bisi_sibi_edge" ||
                                     StringFind(setup_class, "micro_bisi_sibi") >= 0);
      if(InpSuppressMicroBisiSibiEdge && micro_bisi_sibi_family){
         reason = "suppressed_micro_bisi_sibi_edge";
         return false;
      }
      if(InpSuppressStaleFvgBranches && stale){
         reason = "suppressed_stale_fvg_branch";
         return false;
      }
      if(family_group == "continuation"){
         if(InpSuppressContinuationStaleFvg && stale){
            reason = "suppressed_continuation_stale_fvg";
            return false;
         }
         if(InpSuppressContinuationTouchedFvg && touched){
            reason = "suppressed_continuation_touched_fvg";
            return false;
         }
         if(InpSuppressTouchedContinuationUnlessRetested && touched && p.fvg.retest_score < 6.0 && p.fvg.retest_quality_score < 6.0){
            reason = "suppressed_touched_continuation_not_retested";
            return false;
         }
      }
      if(_PlanUsesSyntheticFallback(p) && _ObstacleIsCrossedOpposing(p.obstacle_kind)){
         if(InpHardRejectCrossedObstacleTarget){
            reason = "synthetic_fallback_crossed_obstacle_blocked";
            return false;
         }
         if(InpRejectSyntheticFallbackAfterCrossedObstacle &&
            !p.target_arbitration_required &&
            !_AiTargetArbitrationHasAuthority(p)){
            reason = "synthetic_fallback_crossed_obstacle_blocked";
            return false;
         }
      }
      return true;
   }

   double _TotalExecutionCostR(const TradePlan &p) const {
      return MathMax(0.0, p.execution_cost_r) + MathMax(0.0, p.slippage_r) + MathMax(0.0, p.commission_r);
   }

   double _CanonicalNetRewardAfterCostR(const TradePlan &p) const {
      // TP2 is the authoritative full-position objective. Partial and runner
      // metadata remain separate; costs are deducted exactly once here.
      double gross_reward_r = MathMax(0.0, _ExecutionRR2(p));
      return gross_reward_r - _TotalExecutionCostR(p);
   }

   void _FinalizePlanEconomics(TradePlan &p, const string stage, const bool emit_log) {
      // The field is authoritative and is always recomputed; only the diagnostic line
      // is graded.  The early return must therefore stay AFTER the assignment above.
      p.net_reward_after_cost_r = _CanonicalNetRewardAfterCostR(p);
      if(!emit_log) return;
      if(!_JournalDetailEnabled(2)) return;
      _Journal("[plan_economics] symbol=" + p.symbol
               + " stage=" + stage
               + " gross_reward_r=" + DoubleToString(MathMax(0.0, _ExecutionRR2(p)), 6)
               + " spread_r=" + DoubleToString(MathMax(0.0, p.execution_cost_r), 6)
               + " slippage_r=" + DoubleToString(MathMax(0.0, p.slippage_r), 6)
               + " commission_r=" + DoubleToString(MathMax(0.0, p.commission_r), 6)
               + " net_reward_after_cost_r=" + DoubleToString(p.net_reward_after_cost_r, 6));
   }

   bool _ExecutionCostHardGate(TradePlan &p, string &reason) {
      reason = "";
      double total_cost_r = _TotalExecutionCostR(p);
      if(InpExecutionRejectCostR > 0.0 && total_cost_r >= InpExecutionRejectCostR){
         reason = "execution_cost_r_too_high";
         return false;
      }
      if(_IsMicroFamily(p) && InpMicroScalpMaxCostFracOfPlannedR > 0.0 && total_cost_r >= InpMicroScalpMaxCostFracOfPlannedR){
         reason = "micro_scalp_cost_exceeds_10pct_r";
         return false;
      }
      if(InpExecutionReduceRiskCostR > 0.0 && total_cost_r >= InpExecutionReduceRiskCostR){
         double reject = (InpExecutionRejectCostR > InpExecutionReduceRiskCostR ? InpExecutionRejectCostR : InpExecutionReduceRiskCostR * 1.5);
         double span = MathMax(0.0001, reject - InpExecutionReduceRiskCostR);
         double pressure = _ClampRange((total_cost_r - InpExecutionReduceRiskCostR) / span, 0.0, 1.0);
         p.execution_cost_risk_reduced = true;
         p.execution_cost_risk_multiplier = _ClampRange(1.0 - 0.50 * pressure, 0.50, 1.0);
      } else {
         p.execution_cost_risk_reduced = false;
         p.execution_cost_risk_multiplier = 1.0;
      }
      return true;
   }

   bool _ObjectiveHardPreTradeGate(TradePlan &p, const string stage, string &reason) {
      reason = "";
      string taxonomy_reason = "";
      if(!_ResolveSetupTaxonomy(p, taxonomy_reason)){
         reason = "unknown_setup_taxonomy";
         _LogUnknownSetupTaxonomy(p, stage);
         _LogSetupReject(p.symbol, stage, reason, p.taxonomy_mapping_failure_reason);
         return false;
      }
      bool was_mpc = _PlanIsMPC(p);
      if(!_ApplyOutcomePolicy(p, stage, reason)){
         if(was_mpc && (stage == "pre_order_hard_gate" || stage == "final_order_hard_safety"))
            m_total_mpc_execution_blocks++;
         return false;
      }
      if(!_HardSuppressionGate(p, reason)){
         string detail = "branch=" + p.entry_branch + " family=" + p.setup_family
                         + " session=" + p.po3.session_name + " killzone=" + (p.po3.in_killzone ? "true" : "false");
         if(reason == "suppressed_micro_bisi_sibi_edge")
            detail += " suppression_scope=micro_bisi_sibi_only";
         _LogSetupReject(p.symbol, stage, reason, detail);
         return false;
      }
      if(!_ExecutionCostHardGate(p, reason)){
         _LogSetupReject(p.symbol, stage, reason,
                         "total_cost_r=" + DoubleToString(_TotalExecutionCostR(p), 4)
                         + " reject_cost_r=" + DoubleToString(InpExecutionRejectCostR, 4)
                         + " micro_cost_cap_r=" + DoubleToString(InpMicroScalpMaxCostFracOfPlannedR, 4));
         return false;
      }
      return true;
   }

   bool CanPlaceOrderHardSafety(const TradePlan &plan, string &reason) {
      TradePlan check = plan;
      _InitializeNarrativeFields(check);
      if(!_ObjectiveHardPreTradeGate(check, "final_order_hard_safety", reason))
         return false;
      string target_reason = "";
      string target_detail = "";
      if(!ValidateAiChosenTargetBeforeWatchlist(check, target_reason, target_detail)){
         reason = target_reason;
         return false;
      }
      return true;
   }

   bool _ApplyRuleOnlyNonTradingDiagnostic(TradePlan &p, const string reason) {
      p.ai.ok = false;
      p.ai.allow = false;
      p.ai.raw_allow = false;
      p.ai.decision_state = "REJECT";
      p.ai.decision_quality_tier = "RULE_ONLY_NON_TRADING";
      p.ai.response_quality_alias = "RULE_ONLY_NON_TRADING";
      p.ai.mandatory_fields_complete = false;
      p.ai.suggested_risk_multiplier = 0.0;
      p.ai.reasons_json = reason;
      p.ai.decision_source = "rule_only_non_trading";
      p.ai.rejection_codes_json = "[\"degraded_ai_response_non_trading\"]";
      p.ai.narrative_state = "rule_only_non_trading";
      p.ai.model_version = "rule_only_non_trading";
      p.ai_decision_source = "rule_only_non_trading";
      p.reject_code = "degraded_ai_response_non_trading";
      _LogSetupReject(p.symbol, "ai", "degraded_ai_response_non_trading", "fallback_reason=" + reason);
      _Journal(p.symbol + " rule-only diagnostic cannot authorize trading reason=" + reason);
      return false;
   }

   string _SetupCodeForPlan(const TradePlan &p) const {
      string code = p.model_code;
      if(StringLen(code) <= 0) code = _ModelCodeForPlan(p);
      StringToUpper(code);
      return code;
   }

   bool _PlanIsMPC(const TradePlan &p) const {
      string code = _SetupCodeForPlan(p);
      if(code == "MPC") return true;
      string comment = p.broker_comment;
      StringToUpper(comment);
      if(StringFind(comment, "MPC-") == 0) return true;
      string family = _NormToken(p.setup_family + " " + p.setup_class + " " + p.entry_branch + " " + p.model_code);
      return (StringFind(family, "mpc") >= 0);
   }

   string _SetupSessionKillzoneBucket(const TradePlan &p) const {
      string setup_code = _SetupCodeForPlan(p);
      string session = (StringLen(p.session_code) > 0 ? p.session_code : p.po3.session_code);
      string killzone = (StringLen(p.killzone_code) > 0 ? p.killzone_code : (p.po3.in_killzone ? "K" : "NK"));
      StringToUpper(session);
      StringToUpper(killzone);
      if(StringLen(session) <= 0) session = "OFF";
      if(StringLen(killzone) <= 0) killzone = "NK";
      return setup_code + "-" + session + "-" + killzone;
   }

   bool _LoadBucketRiskPolicy(string &json) {
      json = "";
      if(!InpEnableBucketRiskPolicy || StringLen(InpBucketRiskPolicyFile) <= 0) return false;
      datetime now = TimeLocal();
      if(StringLen(m_bucket_policy_json) > 0 && (now - m_bucket_policy_loaded_at) < 60){
         json = m_bucket_policy_json;
         return true;
      }
      if((now - m_bucket_policy_last_attempt) < 10 && StringLen(m_bucket_policy_json) <= 0)
         return false;
      m_bucket_policy_last_attempt = now;
      string txt;
      if(!m_bus.ReadText(InpBucketRiskPolicyFile, txt))
         return false;
      m_bucket_policy_json = txt;
      m_bucket_policy_loaded_at = now;
      json = m_bucket_policy_json;
      return (StringLen(json) > 0);
   }

   bool _ApplyBucketPolicyObject(const TradePlan &p, const string section_name, const string key,
                                 double &risk_multiplier, string &block_reason, string &matched_key) {
      if(StringLen(key) <= 0) return true;
      string policy_json;
      if(!_LoadBucketRiskPolicy(policy_json)) return true;
      string section = JsonGetObject(policy_json, section_name, "");
      if(StringLen(section) <= 0) return true;
      string obj = JsonGetObject(section, key, "");
      if(StringLen(obj) <= 0) return true;
      matched_key = key;
      string reason = JsonGetString(obj, "reason", "");
      bool enabled = JsonGetBool(obj, "enabled", true);
      double mult = 0.0;
      if(!JsonGetNumberStrict(obj, "risk_multiplier", mult)){
         block_reason = "risk_multiplier_missing_or_invalid";
         return false;
      }
      if(mult < 0.0 || mult > 1.0){
         block_reason = "risk_multiplier_out_of_range";
         return false;
      }
      if(!enabled || mult <= 0.0){
         block_reason = (StringLen(reason) > 0 ? reason : section_name + "_disabled");
         return false;
      }
      if(mult > 0.0 && mult < risk_multiplier)
         risk_multiplier = MathMax(0.0, MathMin(1.0, mult));
      return true;
   }

   bool _ApplyOutcomePolicy(TradePlan &p, const string stage, string &reason) {
      reason = "";
      if(StringLen(p.model_code) <= 0) p.model_code = _ModelCodeForPlan(p);
      if(!InpEnableMPCTrading && _PlanIsMPC(p)){
         reason = "mpc_trading_disabled";
         p.bucket_policy_action = "block";
         p.bucket_policy_reason = reason;
         p.bucket_policy_key = "MPC";
         p.bucket_policy_risk_multiplier = 0.0;
         string detail = "setup_code=MPC setup_class=" + p.setup_class
                         + " session=" + (StringLen(p.session_code) > 0 ? p.session_code : p.po3.session_code)
                         + " killzone=" + (StringLen(p.killzone_code) > 0 ? p.killzone_code : (p.po3.in_killzone ? "K" : "NK"));
         _LogSetupReject(p.symbol, stage, reason, detail);
         _Journal("[mpc_block] symbol=" + p.symbol
                  + " setup_code=MPC setup_class=" + p.setup_class
                  + " session=" + (StringLen(p.session_code) > 0 ? p.session_code : p.po3.session_code)
                  + " killzone=" + (StringLen(p.killzone_code) > 0 ? p.killzone_code : (p.po3.in_killzone ? "K" : "NK"))
                  + " action=" + (stage == "pre_ai_hard_gate" ? "blocked_before_ai" : (stage == "watchlist" ? "blocked_before_watchlist" : "blocked_at_execution")));
         return false;
      }

      double mult = 1.0;
      string block_reason = "";
      string matched = "";
      string setup_code = _SetupCodeForPlan(p);
      string session_bucket = _SetupSessionKillzoneBucket(p);
      string symbol = p.symbol;
      StringToUpper(symbol);
      if(!_ApplyBucketPolicyObject(p, "setup_code", setup_code, mult, block_reason, matched) ||
         !_ApplyBucketPolicyObject(p, "setup_session_killzone", session_bucket, mult, block_reason, matched) ||
         !_ApplyBucketPolicyObject(p, "symbol", symbol, mult, block_reason, matched)){
         reason = "bucket_risk_policy_block";
         p.bucket_policy_action = "block";
         p.bucket_policy_reason = block_reason;
         p.bucket_policy_key = matched;
         p.bucket_policy_risk_multiplier = 0.0;
         _LogSetupReject(p.symbol, stage, reason,
                         "bucket=" + matched + " reason=" + block_reason + " setup_code=" + setup_code);
         _Journal("[bucket_policy_block] bucket=" + matched
                  + " symbol=" + p.symbol
                  + " reason=" + block_reason
                  + " action=" + (stage == "pre_ai_hard_gate" ? "blocked_before_ai" : (stage == "watchlist" ? "blocked_before_watchlist" : "blocked_at_execution")));
         return false;
      }
      if(mult > 0.0 && mult < 1.0){
         p.bucket_policy_action = "risk_reduce";
         p.bucket_policy_reason = "policy_multiplier";
         p.bucket_policy_key = matched;
         p.bucket_policy_risk_multiplier = mult;
         _Journal("[bucket_policy_risk_reduce] symbol=" + p.symbol
                  + " old_risk_pct=" + DoubleToString(InpRiskPerTradePct, 4)
                  + " new_risk_pct=" + DoubleToString(InpRiskPerTradePct * mult, 4)
                  + " multiplier=" + DoubleToString(mult, 4)
                  + " bucket=" + matched);
      }
      return true;
   }

   bool _FilterOutcomePolicyBeforeAI(TradePlan &cands[]) {
      int count = ArraySize(cands);
      int write = 0;
      for(int i=0; i<count; i++){
         TradePlan staged = cands[i];
         string reason = "";
         bool is_mpc = _PlanIsMPC(staged);
         if(!_ApplyOutcomePolicy(staged, "pre_ai_hard_gate", reason)){
            if(is_mpc){
               m_total_mpc_candidates_blocked++;
               m_total_mpc_ai_calls_saved++;
            }
            continue;
         }
         if(write != i) cands[write] = staged;
         else cands[i] = staged;
         write++;
      }
      if(write != count) ArrayResize(cands, write);
      return (write > 0);
   }

   int _EnabledEntryFamilyCount() const {
      int count = 0;
      if(InpEnableFvgMid) count++;
      if(InpEnableFvgEdge) count++;
      if(InpEnableBreakerRetest) count++;
      if(InpEnableOteInsideFvg) count++;
      if(InpEnableNestedFvg) count++;
      if(InpEnableSessionReentry) count++;
      if(InpEnableRangeReentry) count++;
      if(InpEnableContinuationReentry) count++;
      return count;
   }

   bool _AllEntryFamiliesDisabled() const {
      return (_EnabledEntryFamilyCount() <= 0);
   }

   string _EntryFamilyConfigSummary() const {
      return "fvg_mid=" + (InpEnableFvgMid ? "1" : "0")
             + " fvg_edge=" + (InpEnableFvgEdge ? "1" : "0")
             + " breaker_retest=" + (InpEnableBreakerRetest ? "1" : "0")
             + " ote_inside_fvg=" + (InpEnableOteInsideFvg ? "1" : "0")
             + " nested_fvg=" + (InpEnableNestedFvg ? "1" : "0")
             + " session_reentry=" + (InpEnableSessionReentry ? "1" : "0")
             + " range_reentry=" + (InpEnableRangeReentry ? "1" : "0")
             + " continuation_reentry=" + (InpEnableContinuationReentry ? "1" : "0");
   }

   bool _BranchEnabledByConfig(const string branch, string &reason) const {
      reason = "ok";
      if(branch == "fvg_mid" && !InpEnableFvgMid) reason = "branch_disabled_fvg_mid";
      else if(branch == "fvg_edge" && !InpEnableFvgEdge) reason = "branch_disabled_fvg_edge";
      else if(branch == "breaker_retest" && !InpEnableBreakerRetest) reason = "branch_disabled_breaker_retest";
      else if(branch == "ote_inside_fvg" && !InpEnableOteInsideFvg) reason = "branch_disabled_ote_inside_fvg";
      else if((branch == "nested_htf_ltf_fvg" || branch == "nested_fvg_edge") && !InpEnableNestedFvg) reason = "branch_disabled_nested_fvg";
      else if(branch == "session_reentry" && !InpEnableSessionReentry) reason = "branch_disabled_session_reentry";
      else if(branch == "range_reentry" && !InpEnableRangeReentry) reason = "branch_disabled_range_reentry";
      else if(branch == "continuation_reentry" && !InpEnableContinuationReentry) reason = "branch_disabled_continuation_reentry";
      return (reason == "ok");
   }

   bool _IsFvgEntryNonTradable(const string fvg_class) const {
      return (fvg_class == "invalidated_fvg" ||
              fvg_class == "fully_mitigated_fvg" ||
              fvg_class == "entry_invalid_fvg" ||
              fvg_class == "fully_mitigated_invalidation");
   }

   bool _IsEntryZoneOnlyInvalidation(const string reason) const {
      return (reason == "price_broke_fvg_low" ||
              reason == "price_broke_fvg_high" ||
              reason == "fvg_invalidated_or_fully_filled" ||
              reason == "fvg_fully_mitigated_before_entry" ||
              reason == "fvg_structure_invalidated");
   }

   void _ResetSetupFunnel() {
      m_funnel_scans = 0;
      m_funnel_po3_context_created = 0;
      m_funnel_closed_sweeps_found = 0;
      m_funnel_displacement_passed = 0;
      m_funnel_tier_a_contexts = 0;
      m_funnel_tier_b_contexts = 0;
      m_funnel_raw_fvgs = 0;
      m_funnel_accepted_fvgs = 0;
      m_funnel_fvg_candidates_created = 0;
      m_funnel_branch_candidates = 0;
      m_funnel_plan_prices_valid = 0;
      m_funnel_ai_requests = 0;
      m_funnel_watchlist_added = 0;
      m_funnel_market_entries_attempted = 0;
      m_funnel_pending_entries_attempted = 0;
      m_funnel_pending_orders_placed = 0;
      m_funnel_orders_filled = 0;
      m_funnel_pending_orders_expired = 0;
      m_funnel_pending_orders_deleted = 0;
      m_funnel_trades_opened = 0;
      m_funnel_exclusive_model_filter_checked = 0;
      m_funnel_exclusive_model_filter_passed = 0;
      m_funnel_exclusive_model_filter_rejected = 0;
      m_funnel_exclusive_model_filter_reject_not_breaker = 0;
      m_funnel_exclusive_model_filter_reject_not_virgin = 0;
      m_funnel_exclusive_model_filter_reject_not_strong_origin = 0;
      m_funnel_ai_final_allow = 0;
      m_funnel_ai_advisories = 0;
      m_funnel_ai_result_stale_in_tester = 0;
      m_funnel_ai_cache_hit = 0;
      m_funnel_ai_cache_miss_due_to_schema_version = 0;
      m_funnel_invalid_ai_target_arbitration_response = 0;
      m_funnel_pending_relax_invalid_ai_target_arbitration_response = 0;
      m_funnel_pending_relax_using_stored_target_arbitration = 0;
      m_funnel_blocker_severity_7_defaults_suspected = 0;
      m_funnel_watchlist_precheck_rejects = 0;
      m_funnel_watchlist_instant_invalidations_bars0 = 0;
      ArrayResize(m_funnel_pending_delete_reasons, 0);
      ArrayResize(m_funnel_pending_delete_counts, 0);
      ArrayResize(m_funnel_reject_stages, 0);
      ArrayResize(m_funnel_reject_reasons, 0);
      ArrayResize(m_funnel_reject_counts, 0);
      ArrayResize(m_funnel_target_choice_models, 0);
      ArrayResize(m_funnel_target_choice_counts, 0);
      ArrayResize(m_funnel_blocker_classes, 0);
      ArrayResize(m_funnel_blocker_class_counts, 0);
      ArrayResize(m_funnel_target_validation_reasons, 0);
      ArrayResize(m_funnel_target_validation_counts, 0);
      ArrayResize(m_funnel_watchlist_precheck_reasons, 0);
      ArrayResize(m_funnel_watchlist_precheck_counts, 0);
   }

   void _ResetFinalCounters() {
      m_total_ai_requests_queued = 0;
      m_total_tester_ai_wait_started = 0;
      m_total_tester_ai_wait_completed = 0;
      m_total_tester_ai_wait_timeout = 0;
      m_total_ai_results_rejected_stale = 0;
      m_total_ai_results_accepted_after_blocking_wait = 0;
      m_total_ai_final_allow_true = 0;
      m_total_watchlist_added = 0;
      m_total_orders_placed = 0;
      m_total_scans = 0;
      m_total_po3_context_created = 0;
      m_total_fvg_candidates_created = 0;
      m_total_plans_valid = 0;
      m_total_ai_cache_hits = 0;
      m_total_ai_cache_misses = 0;
      m_total_ai_cache_miss_due_to_schema_version = 0;
      m_total_ai_cache_miss_no_artifact = 0;
      m_tester_cache_cohort_summary = "";
      m_tester_cache_cohort_dominant = "";
      m_tester_cache_cohort_probed = false;
      m_tester_cache_cohort_matches = false;
      m_tester_cache_cohort_total = 0;
      m_tester_cache_cohort_sampled = 0;
      m_total_ai_advisories = 0;
      m_total_trades_opened = 0;
      m_total_record_only_requests_exported = 0;
      m_total_record_only_duplicate_signatures_skipped = 0;
      ArrayResize(m_tester_recorded_cache_signatures, 0);
      m_total_tester_live_wait_non_tradeable_sim_jump = 0;
      m_total_tester_live_wait_debug_trading_disabled = 0;
      m_total_target_feasibility_synthetic_infeasible_max_distance = 0;
      m_total_target_feasibility_synthetic_capped_to_max_distance = 0;
      m_total_reject_tester_ai_cache_miss = 0;
      m_total_reject_ai_chose_infeasible_target = 0;
      m_total_mpc_candidates_blocked = 0;
      m_total_mpc_ai_calls_saved = 0;
      m_total_mpc_watchlist_blocks = 0;
      m_total_mpc_execution_blocks = 0;
      ArrayResize(m_total_target_choice_models, 0);
      ArrayResize(m_total_target_choice_counts, 0);
      ArrayResize(m_total_blocker_classes, 0);
      ArrayResize(m_total_blocker_class_counts, 0);
      ArrayResize(m_total_watchlist_precheck_reasons, 0);
      ArrayResize(m_total_watchlist_precheck_counts, 0);
      ArrayResize(m_total_target_validation_reasons, 0);
      ArrayResize(m_total_target_validation_counts, 0);
   }

   // Emitted once per scan as well as at shutdown.  The cost of holding a position is
   // paid between journal lines, so a counter that only prints at the end cannot be
   // read while a run is still deciding whether it will ever finish -- which is
   // exactly the situation this instrumentation exists for.  One line per scan is
   // ~1 line per 15 simulated minutes.
   void _LogTradeMetaPerf(const string stage) const {
      _Journal("[perf_trade_meta] stage=" + stage
               + " serializes=" + IntegerToString(m_tm_serializes)
               + " serialize_seconds=" + DoubleToString((double)m_tm_serialize_us / 1000000.0, 3)
               + " serialize_us_per_call="
               + DoubleToString(m_tm_serializes > 0 ? (double)m_tm_serialize_us / (double)m_tm_serializes : 0.0, 1)
               + " writes=" + IntegerToString(m_tm_writes)
               + " writes_skipped=" + IntegerToString(m_tm_writes_skipped_identical)
               // Why the skips did not happen, so a collapsing hit rate names its own
               // cause instead of being inferred from the gap between the two counters.
               + " miss_content=" + IntegerToString(m_tm_miss_content)
               + " miss_watermark=" + IntegerToString(m_tm_miss_watermark)
               + " miss_absent=" + IntegerToString(m_tm_miss_absent)
               + " miss_new_path=" + IntegerToString(m_tm_miss_new_path)
               + " write_seconds=" + DoubleToString((double)m_tm_write_us / 1000000.0, 3)
               + " parses=" + IntegerToString(m_tm_parses)
               + " parses_skipped=" + IntegerToString(m_tm_parses_skipped_identical)
               + " parse_seconds=" + DoubleToString((double)m_tm_parse_us / 1000000.0, 3)
               + " maintain_positions_calls=" + IntegerToString(m_mp_calls)
               + " maintain_positions_seconds=" + DoubleToString((double)m_mp_us / 1000000.0, 3)
               // Segments of the same total.  load + meta + penalty + tail is what the
               // stage spends inside its four named steps; the difference against
               // maintain_positions_seconds is the position loop itself.  serialize,
               // write and parse above are sub-costs of load and meta, not additions.
               + " mp_load_seconds=" + DoubleToString((double)m_mp_load_us / 1000000.0, 3)
               + " mp_meta_seconds=" + DoubleToString((double)m_mp_meta_us / 1000000.0, 3)
               + " mp_penalty_seconds=" + DoubleToString((double)m_mp_penalty_us / 1000000.0, 3)
               + " mp_tail_seconds=" + DoubleToString((double)m_mp_tail_us / 1000000.0, 3)
               + " mp_other_seconds="
               + DoubleToString((double)(m_mp_us - m_mp_load_us - m_mp_meta_us
                                         - m_mp_penalty_us - m_mp_tail_us) / 1000000.0, 3));
   }

   void _LogFinalSummary() const {
      _Journal("[final_summary] scans_total=" + IntegerToString(m_total_scans)
               + " po3_context_created_total=" + IntegerToString(m_total_po3_context_created)
               + " fvg_candidates_created_total=" + IntegerToString(m_total_fvg_candidates_created)
               + " plans_valid_total=" + IntegerToString(m_total_plans_valid));
      if(MQLInfoInteger(MQL_TESTER) && _EffectiveTesterAiMode() == TESTER_AI_RECORD_ONLY)
         _Journal("[final_summary] no_trades_expected=true next_step=run_python_cache_export_then_cache_only");
      _Journal("[final_summary] ai_cache_hits_total=" + IntegerToString(m_total_ai_cache_hits)
               + " ai_cache_misses_total=" + IntegerToString(m_total_ai_cache_misses)
               + " ai_cache_miss_due_to_schema_version_total=" + IntegerToString(m_total_ai_cache_miss_due_to_schema_version)
               + " tester_ai_cache_miss_total=" + IntegerToString(m_total_reject_tester_ai_cache_miss)
               + " ai_cache_miss_no_artifact_total=" + IntegerToString(m_total_ai_cache_miss_no_artifact)
               + " record_only_requests_exported=" + IntegerToString(m_total_record_only_requests_exported)
               + " record_only_duplicate_signatures_skipped=" + IntegerToString(m_total_record_only_duplicate_signatures_skipped));
      _Journal("[final_summary] decision_input_hash=" + m_ai.DecisionHash()
               + " decision_identity_frozen=" + (m_ai.IdentityFrozen() ? "true" : "false")
               + " decision_identity_drift_events=" + IntegerToString(m_ai.IdentityDriftEvents())
               + " cache_cohort=" + _TesterCacheCohortSummaryCached()
               + " cache_cohort_match=" + (_TesterCacheCohortMatchesCached() ? "true" : "false"));
      _Journal("[final_summary] ai_requests_queued_total=" + IntegerToString(m_total_ai_requests_queued)
               + " tester_ai_wait_started_total=" + IntegerToString(m_total_tester_ai_wait_started)
               + " tester_ai_wait_completed_total=" + IntegerToString(m_total_tester_ai_wait_completed)
               + " tester_ai_wait_timeout_total=" + IntegerToString(m_total_tester_ai_wait_timeout)
               + " ai_results_rejected_stale_total=" + IntegerToString(m_total_ai_results_rejected_stale)
               + " ai_results_accepted_after_blocking_wait_total=" + IntegerToString(m_total_ai_results_accepted_after_blocking_wait)
               + " ai_advisories_total=" + IntegerToString(m_total_ai_advisories)
               + " ai_final_allow_true_total=" + IntegerToString(m_total_ai_final_allow_true)
               + " watchlist_added_total=" + IntegerToString(m_total_watchlist_added)
               + " orders_placed_total=" + IntegerToString(m_total_orders_placed)
               + " trades_opened_total=" + IntegerToString(m_total_trades_opened));
      _Journal("[final_summary] target_choice.synthetic_rr_fallback=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "synthetic_rr_fallback"))
               + " target_choice.synthetic_rr_capped_to_max_distance=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "synthetic_rr_capped_to_max_distance"))
               + " target_choice.partial_before_obstacle_then_liquidity=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "partial_before_obstacle_then_liquidity"))
               + " target_choice.liquidity_target=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "liquidity_target"))
               + " target_choice.capped_before_obstacle=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "capped_before_obstacle"))
               + " target_choice.cap_before_opposing_imbalance=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "cap_before_opposing_imbalance"))
               + " target_choice.capped_before_opposing_imbalance=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "capped_before_opposing_imbalance"))
               + " target_choice.capped_before_htf_opposing_imbalance=" + IntegerToString(_NamedCounterValue(m_total_target_choice_models, m_total_target_choice_counts, "capped_before_htf_opposing_imbalance")));
      _Journal("[final_summary] blocker_class.minor=" + IntegerToString(_NamedCounterValue(m_total_blocker_classes, m_total_blocker_class_counts, "minor"))
               + " blocker_class.moderate=" + IntegerToString(_NamedCounterValue(m_total_blocker_classes, m_total_blocker_class_counts, "moderate"))
               + " blocker_class.major=" + IntegerToString(_NamedCounterValue(m_total_blocker_classes, m_total_blocker_class_counts, "major"))
               + " blocker_class.killer=" + IntegerToString(_NamedCounterValue(m_total_blocker_classes, m_total_blocker_class_counts, "killer"))
               + " blocker_class.unknown=" + IntegerToString(_NamedCounterValue(m_total_blocker_classes, m_total_blocker_class_counts, "unknown")));
      _Journal("[final_summary] watchlist_precheck.reject.price_broke_fvg_high=" + IntegerToString(_NamedCounterValue(m_total_watchlist_precheck_reasons, m_total_watchlist_precheck_counts, "price_broke_fvg_high"))
               + " watchlist_precheck.reject.price_broke_fvg_low=" + IntegerToString(_NamedCounterValue(m_total_watchlist_precheck_reasons, m_total_watchlist_precheck_counts, "price_broke_fvg_low"))
               + " watchlist_precheck.reject.opposite_confirmed_po3=" + IntegerToString(_NamedCounterValue(m_total_watchlist_precheck_reasons, m_total_watchlist_precheck_counts, "opposite_confirmed_po3"))
               + " watchlist_precheck.reject.structural_invalidation_high=" + IntegerToString(_NamedCounterValue(m_total_watchlist_precheck_reasons, m_total_watchlist_precheck_counts, "structural_invalidation_high"))
               + " tester_ai.live_wait_debug.non_tradeable_due_to_sim_time_jump=" + IntegerToString(m_total_tester_live_wait_non_tradeable_sim_jump)
               + " tester_ai.live_wait_debug.trading_disabled=" + IntegerToString(m_total_tester_live_wait_debug_trading_disabled));
      _Journal("[final_summary] target_feasibility.synthetic_infeasible_max_distance=" + IntegerToString(m_total_target_feasibility_synthetic_infeasible_max_distance)
               + " target_feasibility.synthetic_capped_to_max_distance=" + IntegerToString(m_total_target_feasibility_synthetic_capped_to_max_distance)
               + " reject.ai_chosen_target_exceeds_max_distance=" + IntegerToString(_NamedCounterValue(m_total_target_validation_reasons, m_total_target_validation_counts, "ai_chosen_target_exceeds_max_distance"))
               + " reject.ai_chose_infeasible_target=" + IntegerToString(m_total_reject_ai_chose_infeasible_target)
               + " reject.tester_ai_cache_miss=" + IntegerToString(m_total_reject_tester_ai_cache_miss));
      _Journal("[final_summary] mpc_candidates_blocked_total=" + IntegerToString(m_total_mpc_candidates_blocked)
               + " mpc_ai_calls_saved_total=" + IntegerToString(m_total_mpc_ai_calls_saved)
               + " mpc_watchlist_blocks_total=" + IntegerToString(m_total_mpc_watchlist_blocks)
               + " mpc_execution_blocks_total=" + IntegerToString(m_total_mpc_execution_blocks));
      long bc_hits = 0, bc_misses = 0, bc_epoch = 0, bc_us = 0;
      BrokerCostCacheStats(bc_hits, bc_misses, bc_epoch, bc_us);
      _Journal("[final_summary] broker_cost_cache_hits_total=" + IntegerToString(bc_hits)
               + " broker_cost_cache_misses_total=" + IntegerToString(bc_misses)
               + " broker_cost_history_epoch=" + IntegerToString(bc_epoch)
               + " broker_cost_cache_seconds=" + IntegerToString(InpBrokerCostCacheSeconds)
               + " broker_cost_compute_seconds=" + DoubleToString((double)bc_us / 1000000.0, 3)
               + " broker_cost_compute_us_per_call="
               + DoubleToString(bc_misses > 0 ? (double)bc_us / (double)bc_misses : 0.0, 1));
      _LogTradeMetaPerf("final_summary");
      _Journal("[final_summary] trade_meta_writes_total=" + IntegerToString(m_tm_writes)
               + " trade_meta_writes_skipped_identical=" + IntegerToString(m_tm_writes_skipped_identical)
               + " trade_meta_parses_total=" + IntegerToString(m_tm_parses)
               + " trade_meta_parses_skipped_identical=" + IntegerToString(m_tm_parses_skipped_identical)
               + " trade_meta_memo_entries=" + IntegerToString(InpTradeMetaMemoEntries)
               + " trade_meta_write_seconds=" + DoubleToString((double)m_tm_write_us / 1000000.0, 3)
               + " trade_meta_parse_seconds=" + DoubleToString((double)m_tm_parse_us / 1000000.0, 3)
               + " trade_meta_parse_us_per_call="
               + DoubleToString(m_tm_parses > 0 ? (double)m_tm_parse_us / (double)m_tm_parses : 0.0, 1));
      _Journal("[final_summary] maintain_positions_calls_total=" + IntegerToString(m_mp_calls)
               + " maintain_positions_seconds=" + DoubleToString((double)m_mp_us / 1000000.0, 3)
               + " maintain_positions_us_per_call="
               + DoubleToString(m_mp_calls > 0 ? (double)m_mp_us / (double)m_mp_calls : 0.0, 1));
      _LogJournalDedupSummary();
   }

   void _TrackPendingOrderDelete(const string reason, const bool expired=false) {
      string code = _ReasonCode(reason);
      m_funnel_pending_orders_deleted++;
      if(expired) m_funnel_pending_orders_expired++;
      for(int i=0; i<ArraySize(m_funnel_pending_delete_reasons); i++){
         if(m_funnel_pending_delete_reasons[i] != code) continue;
         m_funnel_pending_delete_counts[i]++;
         return;
      }
      int n = ArraySize(m_funnel_pending_delete_reasons);
      ArrayResize(m_funnel_pending_delete_reasons, n + 1);
      ArrayResize(m_funnel_pending_delete_counts, n + 1);
      m_funnel_pending_delete_reasons[n] = code;
      m_funnel_pending_delete_counts[n] = 1;
   }

   string _PendingOrderDeleteSummary() const {
      if(ArraySize(m_funnel_pending_delete_reasons) <= 0) return "{}";
      string out = "{";
      for(int i=0; i<ArraySize(m_funnel_pending_delete_reasons); i++){
         if(i > 0) out += ";";
         out += m_funnel_pending_delete_reasons[i] + ":" + IntegerToString(m_funnel_pending_delete_counts[i]);
      }
      out += "}";
      return out;
   }

   string _TopRejectSummary(const int limit=5) const {
      int n = ArraySize(m_funnel_reject_reasons);
      if(n <= 0) return "{}";
      bool used[];
      ArrayResize(used, n);
      string out = "{";
      int emitted = 0;
      for(int rank=0; rank<limit && emitted<n; rank++){
         int best = -1;
         for(int i=0; i<n; i++){
            if(used[i]) continue;
            if(best < 0 || m_funnel_reject_counts[i] > m_funnel_reject_counts[best]) best = i;
         }
         if(best < 0) break;
         used[best] = true;
         if(emitted > 0) out += ";";
         out += m_funnel_reject_stages[best] + "/" + m_funnel_reject_reasons[best]
                + ":" + IntegerToString(m_funnel_reject_counts[best]);
         emitted++;
      }
      out += "}";
      return out;
   }

   void _LogSetupFunnel() const {
      _LogTradeMetaPerf("scan");
      _Journal("setup_funnel scans=" + IntegerToString(m_funnel_scans)
               + " closed_sweeps_found=" + IntegerToString(m_funnel_closed_sweeps_found)
               + " displacement_passed=" + IntegerToString(m_funnel_displacement_passed)
               + " tier_a_contexts=" + IntegerToString(m_funnel_tier_a_contexts)
               + " tier_b_contexts=" + IntegerToString(m_funnel_tier_b_contexts)
               + " raw_fvgs=" + IntegerToString(m_funnel_raw_fvgs)
               + " accepted_fvgs=" + IntegerToString(m_funnel_accepted_fvgs)
               + " branch_candidates=" + IntegerToString(m_funnel_branch_candidates)
               + " plan_prices_valid=" + IntegerToString(m_funnel_plan_prices_valid)
               + " exclusive_model_filter_checked=" + IntegerToString(m_funnel_exclusive_model_filter_checked)
               + " exclusive_model_filter_passed=" + IntegerToString(m_funnel_exclusive_model_filter_passed)
               + " exclusive_model_filter_rejected=" + IntegerToString(m_funnel_exclusive_model_filter_rejected)
               + " exclusive_model_filter_reject_not_breaker=" + IntegerToString(m_funnel_exclusive_model_filter_reject_not_breaker)
               + " exclusive_model_filter_reject_not_virgin=" + IntegerToString(m_funnel_exclusive_model_filter_reject_not_virgin)
               + " exclusive_model_filter_reject_not_strong_origin=" + IntegerToString(m_funnel_exclusive_model_filter_reject_not_strong_origin)
               + " ai_requests=" + IntegerToString(m_funnel_ai_requests)
               + " watchlist_added=" + IntegerToString(m_funnel_watchlist_added)
               + " market_entries_attempted=" + IntegerToString(m_funnel_market_entries_attempted)
               + " pending_entries_attempted=" + IntegerToString(m_funnel_pending_entries_attempted)
               + " pending_orders_placed=" + IntegerToString(m_funnel_pending_orders_placed)
               + " pending_orders_filled=" + IntegerToString(m_funnel_orders_filled)
               + " pending_orders_expired=" + IntegerToString(m_funnel_pending_orders_expired)
               + " pending_orders_deleted_by_reason=" + _PendingOrderDeleteSummary()
               + " top_blocking_stages=" + _TopRejectSummary(5)
               + " po3_context_created=" + IntegerToString(m_funnel_po3_context_created)
               + " fvg_candidates_created=" + IntegerToString(m_funnel_fvg_candidates_created)
               + " trades_opened=" + IntegerToString(m_funnel_trades_opened));
      _Journal("[summary] scans=" + IntegerToString(m_funnel_scans)
               + " ai_requests_queued=" + IntegerToString(m_funnel_ai_requests)
               + " ai_advisories=" + IntegerToString(m_funnel_ai_advisories)
               + " ai_final_allow_true=" + IntegerToString(m_funnel_ai_final_allow)
               + " ai_result_stale_in_tester=" + IntegerToString(m_funnel_ai_result_stale_in_tester)
               + " ai_cache_hit=" + IntegerToString(m_funnel_ai_cache_hit)
               + " ai_cache_miss_due_to_schema_version=" + IntegerToString(m_funnel_ai_cache_miss_due_to_schema_version)
               + " invalid_ai_target_arbitration_response=" + IntegerToString(m_funnel_invalid_ai_target_arbitration_response)
               + " pending_entry_relax_skipped.invalid_ai_target_arbitration_response=" + IntegerToString(m_funnel_pending_relax_invalid_ai_target_arbitration_response)
               + " pending_entry_relax.using_stored_target_arbitration=" + IntegerToString(m_funnel_pending_relax_using_stored_target_arbitration)
               + " watchlist_added=" + IntegerToString(m_funnel_watchlist_added)
               + " watchlist_precheck_rejects=" + IntegerToString(m_funnel_watchlist_precheck_rejects)
               + " instant_invalidations_bars0=" + IntegerToString(m_funnel_watchlist_instant_invalidations_bars0)
               + " orders_placed=" + IntegerToString(m_funnel_pending_orders_placed + m_funnel_trades_opened));
      _Journal("[summary] target_choices_by_model=" + _NamedCounterSummary(m_funnel_target_choice_models, m_funnel_target_choice_counts)
               + " blocker_classes=" + _NamedCounterSummary(m_funnel_blocker_classes, m_funnel_blocker_class_counts)
               + " suspected_7_defaults=" + IntegerToString(m_funnel_blocker_severity_7_defaults_suspected)
               + " target_validation_rejects=" + _NamedCounterSummary(m_funnel_target_validation_reasons, m_funnel_target_validation_counts)
               + " watchlist_precheck_rejects_by_reason=" + _NamedCounterSummary(m_funnel_watchlist_precheck_reasons, m_funnel_watchlist_precheck_counts));
      _Journal("[summary] target_choice.synthetic_rr_fallback=" + IntegerToString(_NamedCounterValue(m_funnel_target_choice_models, m_funnel_target_choice_counts, "synthetic_rr_fallback"))
               + " target_choice.partial_before_obstacle_then_liquidity=" + IntegerToString(_NamedCounterValue(m_funnel_target_choice_models, m_funnel_target_choice_counts, "partial_before_obstacle_then_liquidity"))
               + " target_choice.liquidity_target=" + IntegerToString(_NamedCounterValue(m_funnel_target_choice_models, m_funnel_target_choice_counts, "liquidity_target"))
               + " target_choice.capped_before_obstacle=" + IntegerToString(_NamedCounterValue(m_funnel_target_choice_models, m_funnel_target_choice_counts, "capped_before_obstacle"))
               + " blocker_class.minor=" + IntegerToString(_NamedCounterValue(m_funnel_blocker_classes, m_funnel_blocker_class_counts, "minor"))
               + " blocker_class.moderate=" + IntegerToString(_NamedCounterValue(m_funnel_blocker_classes, m_funnel_blocker_class_counts, "moderate"))
               + " blocker_class.major=" + IntegerToString(_NamedCounterValue(m_funnel_blocker_classes, m_funnel_blocker_class_counts, "major"))
               + " blocker_class.kill=" + IntegerToString(_NamedCounterValue(m_funnel_blocker_classes, m_funnel_blocker_class_counts, "kill"))
               + " pending_orders_expired=" + IntegerToString(m_funnel_pending_orders_expired));
   }

   bool _ShouldLogAiWait(const string req_id, const ulong now_ms) {
      for(int i=0; i<ArraySize(m_ai_wait_log_req_ids); i++){
         if(m_ai_wait_log_req_ids[i] != req_id) continue;
         if(now_ms >= m_ai_wait_log_ms[i] && (now_ms - m_ai_wait_log_ms[i]) < 1000) return false;
         m_ai_wait_log_ms[i] = now_ms;
         return true;
      }
      int n = ArraySize(m_ai_wait_log_req_ids);
      ArrayResize(m_ai_wait_log_req_ids, n+1);
      ArrayResize(m_ai_wait_log_ms, n+1);
      m_ai_wait_log_req_ids[n] = req_id;
      m_ai_wait_log_ms[n] = now_ms;
      return true;
   }

   void _ForgetAiWaitLog(const string req_id) {
      for(int i=ArraySize(m_ai_wait_log_req_ids)-1; i>=0; i--){
         if(m_ai_wait_log_req_ids[i] != req_id) continue;
         int last = ArraySize(m_ai_wait_log_req_ids)-1;
         if(i != last){
            m_ai_wait_log_req_ids[i] = m_ai_wait_log_req_ids[last];
            m_ai_wait_log_ms[i] = m_ai_wait_log_ms[last];
         }
         ArrayResize(m_ai_wait_log_req_ids, last);
         ArrayResize(m_ai_wait_log_ms, last);
      }
   }

   void _LogWatchlistWaiting(const TradePlan &p, const bool zone_ready, const bool b50_ready) {
      _Journal(p.symbol + " watchlist waiting mid=" + (zone_ready ? "1" : "0")
               + " b50=" + (b50_ready ? "1" : "0")
               + " mid_touched=" + (p.mid_touched ? "1" : "0")
               + " b50_touched=" + (p.b50_touched ? "1" : "0")
               + " bars_waited=" + IntegerToString(p.bars_waited)
               + " entry=" + _FmtPrice(p.symbol, p.entry_est));
   }

   void _LogWatchlistConfirmWaiting(const TradePlan &p) {
      _Journal(p.symbol + " watchlist waiting candle_confirm bars_waited="
               + IntegerToString(p.bars_waited)
               + " entry=" + _FmtPrice(p.symbol, p.entry_est));
   }

   string _FmtPrice(const string symbol, const double price) const {
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      if(digits < 0) digits = 5;
      return DoubleToString(price, digits);
   }

   string _PriceSignature(const string symbol, const double price) const {
      if(price <= 0) return "0";
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      if(digits < 0) digits = 5;
      return DoubleToString(price, digits);
   }

   uint _IntegrityFnv1a(const string value, const uint seed=2166136261) const {
      uint h = seed;
      for(int i=0; i<StringLen(value); i++)
         h = (h ^ (uint)StringGetCharacter(value, i)) * 16777619;
      return h;
   }

   string _IntegrityHash(const string value) const {
      uint h1 = _IntegrityFnv1a(value, 2166136261);
      uint h2 = _IntegrityFnv1a("po3|" + value, 2246822519);
      return StringFormat("%08X%08X", h1, h2);
   }

   bool _CohortValueKnown(const string value) const {
      if(StringLen(value) == 0 || value == "UNAVAILABLE" || value == "UNKNOWN") return false;
      if(StringFind(value, "NO_") == 0 || StringFind(value, "NOT_") == 0 ||
         StringFind(value, "MISSING") == 0) return false;
      return true;
   }

   string _CohortIdForPlan(const TradePlan &p) const {
      string material = COHORT_SCHEMA_VERSION + "|" + p.engine_version + "|" + p.git_commit + "|"
                        + p.dirty_tree_status + "|" + p.set_file_hash + "|" + p.runtime_input_hash + "|"
                        + p.prompt_contract_version + "|" + p.ai.provider_mode + "|" + p.ai.provider_id + "|"
                        + p.ai.actual_model_id + "|" + p.ai.model_fingerprint + "|" + p.ai.generation_settings_hash + "|"
                        + p.ai.family_profile_version + "|" + p.ai.retrieval_policy_version + "|"
                        + p.ai.model_version + "|" + p.reasoning_configuration + "|"
                        + p.ai.decision_schema_version + "|" + AI_TARGET_ARBITRATION_SCHEMA_VERSION + "|"
                        + p.policy_snapshot_id + "|" + p.policy_hash + "|" + p.bucket_prior_hash + "|"
                        + p.management_version + "|" + p.setup_taxonomy_version + "|" + p.feature_version + "|"
                        + p.calibration_artifact_id + "|" + p.repeatability_artifact_id;
      return _IntegrityHash(material);
   }

   void _PopulateCohortMetadata(TradePlan &p) {
      p.engine_version = ENGINE_VERSION;
      p.git_commit = m_source_git_commit;
      p.dirty_tree_status = m_source_dirty_tree_status;
      p.set_file_hash = m_set_file_hash;
      p.runtime_input_hash = m_ai.RuntimeHash();
      p.prompt_contract_version = (StringLen(p.ai.prompt_contract_version) > 0 ? p.ai.prompt_contract_version : AI_PROMPT_CONTRACT_VERSION);
      if(StringLen(p.ai.model_version) == 0) p.ai.model_version = (p.ai.ok ? "UNAVAILABLE" : "NOT_CALLED_PRE_AI");
      p.reasoning_configuration = (StringLen(p.ai.reasoning_configuration) > 0 ? p.ai.reasoning_configuration : (p.ai.ok ? "UNAVAILABLE" : "NOT_CALLED_PRE_AI"));
      if(StringLen(p.policy_snapshot_id) == 0) p.policy_snapshot_id = "NONE";
      p.policy_hash = (StringLen(m_active_policy.policy_id) > 0 ? _IntegrityHash(m_active_policy.policy_id + "|" + IntegerToString(m_active_policy.version)) : "NONE");
      p.bucket_prior_hash = (StringLen(p.ai.bucket_prior_hash) > 0 ? p.ai.bucket_prior_hash : (p.ai.ok ? "NO_BUCKET_PRIOR_ARTIFACT" : "NOT_APPLICABLE_PRE_AI"));
      p.calibration_artifact_id = (StringLen(p.ai.calibration_artifact_id) > 0 ? p.ai.calibration_artifact_id : "NO_CALIBRATION_ARTIFACT");
      p.repeatability_artifact_id = (StringLen(p.ai.repeatability_authority_hash) > 0 ? p.ai.repeatability_authority_hash : (p.ai.ok ? "NO_REPEATABILITY_ARTIFACT" : "NOT_APPLICABLE_PRE_AI"));
      p.feature_version = FEATURE_LINEAGE_VERSION;
      p.cohort_complete = (m_deployment_manifest_valid &&
                           _CohortValueKnown(p.engine_version) &&
                           _CohortValueKnown(p.git_commit) &&
                           _CohortValueKnown(p.dirty_tree_status) &&
                           _CohortValueKnown(p.set_file_hash) &&
                           _CohortValueKnown(p.runtime_input_hash) &&
                           _CohortValueKnown(p.prompt_contract_version) &&
                           _CohortValueKnown(p.ai.provider_mode) &&
                           _CohortValueKnown(p.ai.provider_id) &&
                           _CohortValueKnown(p.ai.actual_model_id) &&
                           _CohortValueKnown(p.ai.model_fingerprint) &&
                           _CohortValueKnown(p.ai.generation_settings_hash) &&
                           _CohortValueKnown(p.ai.family_profile_version) &&
                           _CohortValueKnown(p.ai.retrieval_policy_version) &&
                           _CohortValueKnown(p.ai.model_version) &&
                           _CohortValueKnown(p.reasoning_configuration) &&
                           _CohortValueKnown(p.ai.decision_schema_version) &&
                           _CohortValueKnown(AI_TARGET_ARBITRATION_SCHEMA_VERSION) &&
                           _CohortValueKnown(p.policy_snapshot_id) &&
                           _CohortValueKnown(p.policy_hash) &&
                           _CohortValueKnown(p.bucket_prior_hash) &&
                           _CohortValueKnown(p.management_version) &&
                           _CohortValueKnown(p.setup_taxonomy_version) &&
                           _CohortValueKnown(p.feature_version) &&
                           _CohortValueKnown(p.calibration_artifact_id) &&
                           _CohortValueKnown(p.repeatability_artifact_id));
      p.cohort_id = _CohortIdForPlan(p);
   }

   string _CanonicalPrice(const string symbol, const double price) const {
      double tick = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(tick <= 0.0) tick = (point > 0.0 ? point : 0.00001);
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      if(digits < 0) digits = 5;
      double rounded = MathRound(price / tick) * tick;
      return DoubleToString(NormalizeDouble(rounded, digits), digits);
   }

   string _CandidateHash(const TradePlan &p) const {
      string symbol_upper = p.symbol;
      StringToUpper(symbol_upper);
      string canonical = symbol_upper + "|" + (p.is_buy ? "BUY" : "SELL") + "|";
      canonical += p.candidate_id + "|" + p.setup_id + "|" + p.fvg_id + "|";
      canonical += p.model_code + "|" + p.setup_family + "|" + p.setup_taxonomy_enum + "|";
      canonical += p.setup_taxonomy_version + "|" + p.taxonomy_mapping_source + "|" + p.entry_branch + "|";
      canonical += IntegerToString((int)p.source_t_sweep) + "|" + IntegerToString((int)p.source_t_disp) + "|" + IntegerToString((int)p.source_t_bos) + "|";
      canonical += _CanonicalPrice(p.symbol, p.fvg.lower) + "|" + _CanonicalPrice(p.symbol, p.fvg.upper) + "|";
      canonical += _CanonicalPrice(p.symbol, p.entry_est) + "|" + _CanonicalPrice(p.symbol, p.sl) + "|";
      canonical += _CanonicalPrice(p.symbol, p.tp1) + "|" + _CanonicalPrice(p.symbol, p.tp2) + "|";
      canonical += p.target_source + "|" + p.target_model + "|" + p.obstacle_kind + "|";
      canonical += p.obstacle_tf + "|" + _CanonicalPrice(p.symbol, p.obstacle_price) + "|";
      canonical += m_ai.DecisionHash() + "|" + ENGINE_INPUT_SCHEMA + "|" + AI_DECISION_SCHEMA_VERSION;
      return _IntegrityHash(canonical);
   }

   string _ExecutionFingerprint(const TradePlan &p) const {
      double risk = MathAbs(p.entry_est - p.sl);
      double rr = 0.0;
      if(risk > 0.0){
         double reward = (p.is_buy ? p.tp2 - p.entry_est : p.entry_est - p.tp2);
         rr = reward / risk;
      }
      string symbol_upper = p.symbol;
      StringToUpper(symbol_upper);
      string canonical = symbol_upper + "|" + (p.is_buy ? "BUY" : "SELL") + "|";
      canonical += p.candidate_id + "|" + p.candidate_hash + "|" + p.model_code + "|" + p.setup_family + "|";
      canonical += p.setup_taxonomy_enum + "|" + p.setup_taxonomy_version + "|" + p.taxonomy_mapping_source + "|" + p.entry_branch + "|";
      canonical += IntegerToString((int)p.source_t_sweep) + "|" + IntegerToString((int)p.source_t_disp) + "|" + IntegerToString((int)p.source_t_bos) + "|";
      canonical += _CanonicalPrice(p.symbol, p.entry_est) + "|" + _CanonicalPrice(p.symbol, p.sl) + "|";
      canonical += _CanonicalPrice(p.symbol, p.tp1) + "|" + _CanonicalPrice(p.symbol, p.tp2) + "|";
      canonical += DoubleToString(rr, 6) + "|" + DoubleToString(p.spread_r, 6) + "|";
      canonical += DoubleToString(p.slippage_r, 6) + "|" + DoubleToString(p.execution_cost_r, 6) + "|";
      canonical += DoubleToString(p.net_reward_after_cost_r, 6) + "|";
      canonical += p.target_source + "|" + p.target_model + "|" + p.obstacle_kind + "|";
      canonical += p.obstacle_tf + "|" + _CanonicalPrice(p.symbol, p.obstacle_price) + "|";
      canonical += m_ai.DecisionHash() + "|" + ENGINE_INPUT_SCHEMA + "|" + AI_DECISION_SCHEMA_VERSION;
      return _IntegrityHash(canonical);
   }

   void _PrepareDecisionIdentity(TradePlan &p) {
      _InitializeNarrativeFields(p);
      p.candidate_hash = _CandidateHash(p);
      p.request_execution_fingerprint = _ExecutionFingerprint(p);
      p.assessed_execution_fingerprint = p.request_execution_fingerprint;
      p.assessed_entry = p.entry_est;
      p.assessed_sl = p.sl;
      p.assessed_tp1 = p.tp1;
      p.assessed_tp2 = p.tp2;
      double risk = MathAbs(p.entry_est - p.sl);
      p.assessed_net_rr = (risk > 0.0 ? (p.is_buy ? p.tp2 - p.entry_est : p.entry_est - p.tp2) / risk : 0.0);
      p.assessed_spread_r = p.spread_r;
      p.assessed_slippage_r = p.slippage_r;
      p.assessed_execution_cost_r = p.execution_cost_r;
      p.assessed_symbol = p.symbol;
      p.assessed_is_buy = p.is_buy;
      p.assessed_setup_code = p.model_code;
      p.assessed_setup_family = p.setup_family;
      p.assessed_setup_taxonomy_enum = p.setup_taxonomy_enum;
      p.assessed_setup_taxonomy_version = p.setup_taxonomy_version;
      p.assessed_taxonomy_mapping_source = p.taxonomy_mapping_source;
      p.assessed_entry_branch = p.entry_branch;
      p.assessed_source_t_sweep = p.source_t_sweep;
      p.assessed_source_t_disp = p.source_t_disp;
      p.assessed_source_t_bos = p.source_t_bos;
      p.assessed_target_source = p.target_source;
      p.assessed_target_model = p.target_model;
      p.assessed_obstacle_kind = p.obstacle_kind;
      p.assessed_obstacle_tf = p.obstacle_tf;
      p.assessed_obstacle_price = p.obstacle_price;
      p.assessed_obstacle_severity = _EffectiveObstacleSeverity(p.obstacle_kind, p.obstacle_severity);
      p.live_obstacle_kind = "";
      p.live_obstacle_price = 0.0;
      p.live_obstacle_severity = 0.0;
      p.assessed_decision_input_hash = m_ai.DecisionHash();
      p.assessed_strategy_schema_version = ENGINE_INPUT_SCHEMA;
      p.ai_selected_candidate_hash = "";
      p.executed_candidate_hash = "";
      p.candidate_hash_match = false;
      p.final_execution_fingerprint = "";
      p.execution_fingerprint_match = false;
      p.execution_fingerprint_changed_components = "";
      // The assessed plan is not locked until Python approves and the approved
      // target has been applied.  Until then this is still a request plan.
      p.assessed_plan_locked = false;
      p.assessed_tp_model = p.tp_model;
      p.assessed_selected_target_identity = "";
      p.assessed_selected_target_price = 0.0;
      p.assessed_stop_distance = MathAbs(p.entry_est - p.sl);
      p.semantic_plan_match = false;
      p.execution_adjustment_valid = false;
      p.semantic_immutable_fields_changed = "";
      p.semantic_authorized_fields_changed = "";
      p.semantic_unauthorized_fields_changed = "";
      p.execution_adjustment_bounds = "";
      p.execution_adjustment_reason = "";
      p.execution_failure_class = EXEC_FAIL_NONE;
      p.execution_failure_state_fingerprint = "";
      p.execution_precheck_attempts = 0;
      p.execution_order_construction_attempts = 0;
      p.execution_attempts_suppressed = 0;
      p.execution_retry_not_before = 0;
   }

   string _AssessedFingerprintFromDecision(const TradePlan &p, const AiDecision &dec) const {
      string canonical = p.request_execution_fingerprint + "|" + p.candidate_hash + "|";
      canonical += dec.selected_target_identity + "|";
      canonical += _CanonicalPrice(p.symbol, dec.assessed_entry) + "|";
      canonical += _CanonicalPrice(p.symbol, dec.assessed_sl) + "|";
      canonical += _CanonicalPrice(p.symbol, dec.assessed_tp1) + "|";
      canonical += _CanonicalPrice(p.symbol, dec.assessed_tp2) + "|";
      canonical += _CanonicalPrice(p.symbol, dec.selected_target_price) + "|";
      canonical += AI_DECISION_SCHEMA_VERSION;
      return _IntegrityHash(canonical);
   }

   //+---------------------------------------------------------------+
   //| Exact mismatch diagnostics for candidate-assessment binding.    |
   //|                                                                 |
   //| The binding used to fail with a single unqualified              |
   //| "candidate_hash_mismatch" and no values, which is why 79 of the |
   //| 1256 decisions in the 2026-09-07 replay -- including an APPROVE |
   //| -- could not be diagnosed from the journal at all.  Bounded so a |
   //| systematic failure cannot flood a 1.7 GB tester log.            |
   //+---------------------------------------------------------------+
   void _LogAssessmentIdentityMismatch(const AiDecision &dec, const int assessment_index,
                                       const int assessment_count, const int plan_count,
                                       const string field, const string assessment_value,
                                       const string plan_value, const string context) const {
      static int logged = 0;
      if(logged >= 40) return;
      logged++;
      _Journal("[assessment_identity_mismatch] req_id=" + dec.response_request_id
               + " assessment_index=" + IntegerToString(assessment_index)
               + " assessment_count=" + IntegerToString(assessment_count)
               + " group_plan_count=" + IntegerToString(plan_count)
               + " chosen_index=" + IntegerToString(dec.chosen_index)
               + " field=" + field
               + " assessment_value=" + assessment_value
               + " plan_value=" + plan_value
               + " context=" + context
               + " decision_quality_tier=" + dec.decision_quality_tier);
   }

   bool _DecisionAssessmentsMatchGroup(const AiDecision &dec, const TradePlan &plans[], string &reason) const {
      reason = "ok";
      int plan_count = ArraySize(plans);
      int assessment_count = JsonArrayObjectCount(dec.candidate_assessments_json);
      // Python deliberately caps the provider cohort (normally to three
      // candidates).  The MQL group can contain more plans, so requiring one
      // assessment for every original plan rejects an otherwise complete,
      // identity-bound response.  Validate the returned subset instead: every
      // assessment must map exactly once to an original plan and duplicates are
      // forbidden.  Deferred plans have no AI authority and cannot be selected.
      if(assessment_count <= 0){
         // A Python error / degraded envelope is contractually allowed to carry
         // no candidate assessments at all.  Calling that a "count mismatch"
         // blamed the candidate cohort for what is really a non-trading error
         // envelope, and hid the true failure category from the journal.
         bool trading_tier = (dec.decision_quality_tier == "FULL_STRUCTURED"
                              || dec.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
         reason = (trading_tier
                   ? "candidate_assessment_count_mismatch"
                   : "error_envelope_no_candidate_assessments");
         return false;
      }
      if(plan_count <= 0 || assessment_count > plan_count){
         reason = "candidate_assessment_count_mismatch";
         return false;
      }
      bool plan_seen[];
      ArrayResize(plan_seen, plan_count);
      ArrayInitialize(plan_seen, false);
      bool selected_seen = false;
      for(int aidx=0; aidx<assessment_count; aidx++){
         string item = "";
         if(!JsonArrayGetObject(dec.candidate_assessments_json, aidx, item)){
            reason = "candidate_assessment_parse_failed";
            return false;
         }
         string item_id = "", item_hash = "", request_fp = "";
         string taxonomy_version = "", taxonomy_enum = "", taxonomy_source = "";
         if(!JsonGetStringStrict(item, "candidate_id", item_id) ||
            !JsonGetStringStrict(item, "candidate_hash", item_hash) ||
            !JsonGetStringStrict(item, "request_execution_fingerprint", request_fp) ||
            !JsonGetStringStrict(item, "setup_taxonomy_version", taxonomy_version) ||
            !JsonGetStringStrict(item, "setup_taxonomy_enum", taxonomy_enum) ||
            !JsonGetStringStrict(item, "taxonomy_mapping_source", taxonomy_source)){
            reason = "candidate_assessment_identity_missing";
            return false;
         }
         int matches = 0;
         int matched_plan = -1;
         for(int pidx=0; pidx<plan_count; pidx++){
            if(item_hash != plans[pidx].candidate_hash) continue;
            matches++;
            matched_plan = pidx;
         }
         // One reason string used to cover four different failures -- the hash was
         // absent from the group, the hash was ambiguous, a sibling identity field
         // disagreed, or the cost-snapshot fingerprint moved -- and it never named
         // the field nor printed the two values.  The 2026-09-07 CACHE_ONLY replay
         // lost 79 of 1256 decisions to it, one of them the USDCAD 20:05 APPROVE,
         // with nothing in the journal to say which field moved.  Each failure now
         // carries its own reason and both values.
         if(matches == 0){
            reason = "assessment_candidate_hash_not_in_group";
            _LogAssessmentIdentityMismatch(dec, aidx, assessment_count, plan_count, "candidate_hash",
                                           item_hash, "absent_from_group", item_id);
            return false;
         }
         if(matches > 1){
            reason = "assessment_candidate_hash_ambiguous_in_group";
            _LogAssessmentIdentityMismatch(dec, aidx, assessment_count, plan_count, "candidate_hash",
                                           item_hash, "group_matches=" + IntegerToString(matches), item_id);
            return false;
         }
         string mismatch_field = "", assessment_value = "", plan_value = "";
         if(item_id != plans[matched_plan].candidate_id){
            mismatch_field = "candidate_id";
            assessment_value = item_id; plan_value = plans[matched_plan].candidate_id;
         } else if(taxonomy_version != plans[matched_plan].setup_taxonomy_version){
            mismatch_field = "setup_taxonomy_version";
            assessment_value = taxonomy_version; plan_value = plans[matched_plan].setup_taxonomy_version;
         } else if(taxonomy_enum != plans[matched_plan].setup_taxonomy_enum){
            mismatch_field = "setup_taxonomy_enum";
            assessment_value = taxonomy_enum; plan_value = plans[matched_plan].setup_taxonomy_enum;
         } else if(taxonomy_source != plans[matched_plan].taxonomy_mapping_source){
            mismatch_field = "taxonomy_mapping_source";
            assessment_value = taxonomy_source; plan_value = plans[matched_plan].taxonomy_mapping_source;
         }
         if(StringLen(mismatch_field) > 0){
            reason = "assessment_" + mismatch_field + "_mismatch";
            _LogAssessmentIdentityMismatch(dec, aidx, assessment_count, plan_count, mismatch_field,
                                           assessment_value, plan_value, item_hash);
            return false;
         }
         // request_execution_fingerprint is a COST SNAPSHOT, not candidate identity.
         // _ExecutionFingerprint hashes spread_r, slippage_r, execution_cost_r and
         // net_reward_after_cost_r at six decimals on top of fields that are ALL
         // already inputs of _CandidateHash, so an equality test on it adds exactly
         // one thing over the hash that just matched: bit-exact equality of four live
         // broker measurements which the execution contract explicitly tolerates
         // drifting by max_cost_deterioration_r and re-checks with that tolerance in
         // _EvaluateSemanticPlanMatch.  It must still be PRESENT -- a blank one means
         // the assessment was never identity-bound -- and any divergence is journalled
         // and separately revalidated by _RestoreTesterRequestProvenance, but it is
         // not what decides whether this assessment belongs to this candidate.
         if(StringLen(request_fp) == 0){
            reason = "assessment_request_fingerprint_missing";
            _LogAssessmentIdentityMismatch(dec, aidx, assessment_count, plan_count, "request_execution_fingerprint",
                                           "empty", plans[matched_plan].request_execution_fingerprint, item_hash);
            return false;
         }
         if(request_fp != plans[matched_plan].request_execution_fingerprint){
            _LogAssessmentIdentityMismatch(dec, aidx, assessment_count, plan_count,
                                           "request_execution_fingerprint_cost_snapshot",
                                           request_fp, plans[matched_plan].request_execution_fingerprint, item_hash);
         }
         if(matched_plan < 0 || plan_seen[matched_plan]){
            reason = "candidate_assessment_duplicate";
            return false;
         }
         plan_seen[matched_plan] = true;
         if(item_hash == dec.selected_candidate_hash)
            selected_seen = true;
      }
      if(!selected_seen){
         reason = "selected_candidate_not_assessed";
         return false;
      }
      return true;
   }

   void _AppendChangedComponent(string &list, const string name) const {
      if(StringLen(list) > 0) list += ",";
      list += name;
   }

   //+---------------------------------------------------------------+
   //| Composition of the two checks.                                 |
   //|                                                                |
   //| Kept under the original name and signature so every existing    |
   //| call site keeps its behaviour, but the decision is now made by  |
   //| _EvaluateSemanticPlanMatch: immutable semantic identity is      |
   //| compared exactly, authorized adjustments are compared against   |
   //| the contract's bounds, and the two are reported separately.     |
   //| A separate fingerprint is still computed for the final order.   |
   //+---------------------------------------------------------------+
   bool _SemanticExecutionCheck(TradePlan &p, const string stage, SemanticPlanMatchResult &out) {
      // Last writer before the comparison, so it does not matter which of the three
      // _PublishObstacleEvidence call sites the live rebuild happened to reach: the
      // blocker that gets judged is always the one on the APPROVED route.
      _ObserveLiveObstacleOnApprovedRoute(p);
      ExecutionAdjustmentContract contract;
      _BuildExecutionAdjustmentContract(p, contract);
      _EvaluateSemanticPlanMatch(p, contract, out);
      _LogSemanticPlanMatch(p, contract, out, stage);

      p.semantic_plan_match                   = out.semantic_match;
      p.execution_adjustment_valid            = out.adjustment_valid;
      p.semantic_immutable_fields_changed     = out.immutable_fields_changed;
      p.semantic_authorized_fields_changed    = out.authorized_fields_changed;
      p.semantic_unauthorized_fields_changed  = out.unauthorized_fields_changed;
      p.execution_adjustment_bounds           = out.adjustment_bounds;
      p.execution_adjustment_reason           = out.result;
      if(!out.Ok()) p.execution_failure_class = out.failure_class;

      // The final order gets its own fingerprint, distinct from the assessment
      // fingerprint it was validated against.
      p.final_execution_fingerprint = _ExecutionFingerprint(p);
      p.candidate_hash_match        = (p.candidate_hash == p.ai_selected_candidate_hash);
      return out.Ok();
   }

   bool _ExecutionFingerprintWithinTolerance(TradePlan &p, string &changed_components) {
      if(p.assessed_plan_locked){
         SemanticPlanMatchResult result;
         bool ok = _SemanticExecutionCheck(p, "execution_fingerprint", result);
         changed_components = result.immutable_fields_changed;
         if(StringLen(result.unauthorized_fields_changed) > 0){
            if(StringLen(changed_components) > 0) changed_components += ",";
            changed_components += result.unauthorized_fields_changed;
         }
         p.execution_fingerprint_changed_components = changed_components;
         p.execution_fingerprint_match = ok;
         return ok;
      }
      changed_components = "";
      double tick = SymbolInfoDouble(p.symbol, SYMBOL_TRADE_TICK_SIZE);
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(tick <= 0.0) tick = (point > 0.0 ? point : 0.00001);
      double risk = MathAbs(p.assessed_entry - p.assessed_sl);
      double price_tol = MathMax(2.0 * tick, 0.05 * risk);
      double cost_tol_r = 0.02;
      double rr_tol = 0.05;
      if(p.candidate_hash != p.ai_selected_candidate_hash) _AppendChangedComponent(changed_components, "candidate_hash");
      if(p.candidate_id != p.ai.selected_candidate_id) _AppendChangedComponent(changed_components, "candidate_id");
      if(p.symbol != p.assessed_symbol) _AppendChangedComponent(changed_components, "symbol");
      if(p.is_buy != p.assessed_is_buy) _AppendChangedComponent(changed_components, "direction");
      if(p.model_code != p.assessed_setup_code) _AppendChangedComponent(changed_components, "setup_code");
      if(p.setup_family != p.assessed_setup_family) _AppendChangedComponent(changed_components, "setup_family");
      if(p.setup_taxonomy_enum != p.assessed_setup_taxonomy_enum) _AppendChangedComponent(changed_components, "setup_taxonomy_enum");
      if(p.setup_taxonomy_version != p.assessed_setup_taxonomy_version) _AppendChangedComponent(changed_components, "setup_taxonomy_version");
      if(p.taxonomy_mapping_source != p.assessed_taxonomy_mapping_source) _AppendChangedComponent(changed_components, "taxonomy_mapping_source");
      if(p.entry_branch != p.assessed_entry_branch) _AppendChangedComponent(changed_components, "entry_branch");
      if(p.source_t_sweep != p.assessed_source_t_sweep) _AppendChangedComponent(changed_components, "source_t_sweep");
      if(p.source_t_disp != p.assessed_source_t_disp) _AppendChangedComponent(changed_components, "source_t_disp");
      if(p.source_t_bos != p.assessed_source_t_bos) _AppendChangedComponent(changed_components, "source_t_bos");
      if(p.target_source != p.assessed_target_source) _AppendChangedComponent(changed_components, "target_source");
      if(p.target_model != p.assessed_target_model) _AppendChangedComponent(changed_components, "target_model");
      // Same partition as _EvaluateSemanticPlanMatch: the base obstacle identity is
      // immutable, the live crossing state and the derived timeframe label are not.
      // This branch only runs for a plan that was never locked, but it is the same
      // comparison and must not disagree with the locked one.
      bool legacy_crossing_changed = false;
      bool legacy_tf_changed = false;
      bool legacy_label_changed = false;
      double legacy_live_sev = 0.0, legacy_assessed_sev = 0.0;
      if(!_ObstacleIdentityPreserved(_LiveObstacleKindForComparison(p), _LiveObstacleTfForComparison(p),
                                     _LiveObstacleSeverityForComparison(p),
                                     p.assessed_obstacle_kind, p.assessed_obstacle_tf,
                                     p.assessed_obstacle_severity,
                                     legacy_crossing_changed, legacy_tf_changed,
                                     legacy_label_changed, legacy_live_sev, legacy_assessed_sev))
         _AppendChangedComponent(changed_components, "obstacle_more_severe");
      // The price only identifies the obstacle while it IS the same obstacle.  When a
      // different, no-more-severe blocker was accepted above, its price is a different
      // market level, so comparing it would reintroduce the rejection just removed.
      if(!legacy_label_changed &&
         MathAbs(_LiveObstaclePriceForComparison(p) - p.assessed_obstacle_price) > price_tol)
         _AppendChangedComponent(changed_components, "obstacle_price");
      if(m_ai.DecisionHash() != p.assessed_decision_input_hash) _AppendChangedComponent(changed_components, "decision_input_hash");
      if(ENGINE_INPUT_SCHEMA != p.assessed_strategy_schema_version) _AppendChangedComponent(changed_components, "strategy_schema_version");
      if(MathAbs(p.entry_est - p.assessed_entry) > price_tol) _AppendChangedComponent(changed_components, "entry");
      if(MathAbs(p.sl - p.assessed_sl) > price_tol) _AppendChangedComponent(changed_components, "sl");
      if(MathAbs(p.tp1 - p.assessed_tp1) > price_tol) _AppendChangedComponent(changed_components, "tp1");
      if(MathAbs(p.tp2 - p.assessed_tp2) > price_tol) _AppendChangedComponent(changed_components, "tp2");
      double current_risk = MathAbs(p.entry_est - p.sl);
      double current_rr = (current_risk > 0.0 ? (p.is_buy ? p.tp2 - p.entry_est : p.entry_est - p.tp2) / current_risk : 0.0);
      if(MathAbs(current_rr - p.assessed_net_rr) > rr_tol) _AppendChangedComponent(changed_components, "net_rr");
      if(MathAbs(p.spread_r - p.assessed_spread_r) > cost_tol_r) _AppendChangedComponent(changed_components, "spread_r");
      if(MathAbs(p.slippage_r - p.assessed_slippage_r) > cost_tol_r) _AppendChangedComponent(changed_components, "slippage_r");
      if(MathAbs(p.execution_cost_r - p.assessed_execution_cost_r) > cost_tol_r) _AppendChangedComponent(changed_components, "execution_cost_r");
      p.final_execution_fingerprint = _ExecutionFingerprint(p);
      p.execution_fingerprint_changed_components = changed_components;
      p.candidate_hash_match = (p.candidate_hash == p.ai_selected_candidate_hash);
      p.execution_fingerprint_match = (StringLen(changed_components) == 0);
      return p.execution_fingerprint_match;
   }

   double _Clamp01(const double v) const {
      return MathMax(0.0, MathMin(1.0, v));
   }

   double _ClampRange(const double v, const double lo, const double hi) const {
      return MathMax(lo, MathMin(hi, v));
   }

   bool _IsUpperAlpha(const ushort c) const {
      return (c >= 'A' && c <= 'Z');
   }

   string _SymbolLetters(const string symbol) const {
      string out = "";
      int len = (int)StringLen(symbol);
      for(int i=0; i<len; i++){
         ushort c = (ushort)StringGetCharacter(symbol, i);
         if(_IsUpperAlpha(c)) out += StringSubstr(symbol, i, 1);
         if(StringLen(out) >= 6) break;
      }
      return out;
   }

   bool _ExtractFxPair(const string symbol, string &base, string &quote) const {
      string letters = _SymbolLetters(symbol);
      if(StringLen(letters) < 6) return false;
      base = StringSubstr(letters, 0, 3);
      quote = StringSubstr(letters, 3, 3);
      return true;
   }

   string _AssetClassForSymbol(const string symbol) const {
      string base = "", quote = "";
      if(_ExtractFxPair(symbol, base, quote)){
         if(base == "BTC" || base == "ETH" || base == "LTC" || base == "XRP") return "crypto";
         if(base == "XAU" || base == "XAG" || base == "XPT" || base == "XPD") return "metal";
         return "forex";
      }
      if(StringFind(symbol, "BTC") >= 0 || StringFind(symbol, "ETH") >= 0) return "crypto";
      if(StringFind(symbol, "XAU") >= 0 || StringFind(symbol, "XAG") >= 0) return "metal";
      if(StringFind(symbol, "US30") >= 0 || StringFind(symbol, "NAS") >= 0 || StringFind(symbol, "DE40") >= 0 ||
         StringFind(symbol, "JP225") >= 0 || StringFind(symbol, "UK100") >= 0) return "index";
      return "cfd";
   }

   string _UsdExposureKey(const TradePlan &p) const {
      string base = "", quote = "";
      if(!_ExtractFxPair(p.symbol, base, quote)) return "non_usd";
      if(base != "USD" && quote != "USD") return "non_usd";
      bool usd_long = false;
      if(base == "USD") usd_long = p.is_buy;
      else if(quote == "USD") usd_long = !p.is_buy;
      return (usd_long ? "usd_long" : "usd_short");
   }

   string _PortfolioClusterKey(const TradePlan &p) const {
      string asset = _AssetClassForSymbol(p.symbol);
      string base = "", quote = "";
      if(asset == "forex" || asset == "metal" || asset == "crypto"){
         if(_ExtractFxPair(p.symbol, base, quote)){
            if(base == "USD" || quote == "USD") return asset + "_usd";
            return asset + "_" + base + "_" + quote;
         }
      }
      return asset + "_" + p.symbol;
   }

   string _TradeRetcodeText() {
      return IntegerToString((int)m_trade.ResultRetcode()) + " " + m_trade.ResultRetcodeDescription();
   }

   bool _BrokerRetcodeAccepted(const uint retcode) const {
      return (retcode == TRADE_RETCODE_DONE ||
              retcode == TRADE_RETCODE_PLACED ||
              retcode == TRADE_RETCODE_DONE_PARTIAL);
   }

   void _SetExecutionAuthority(TradePlan &meta,
                               const string state,
                               const bool mql_allow,
                               const string reason) {
      meta.execution_authority_state = state;
      meta.mql_final_allow = mql_allow;
      meta.ai.mql_final_allow = mql_allow;
      meta.mql_decision_reasons = reason;
      _Journal("[execution_authority] req_id=" + meta.req_id
               + " candidate_id=" + meta.candidate_id
               + " candidate_hash=" + meta.candidate_hash
               + " intended_order_type=" + meta.intended_order_type
               + " state=" + state
               + " broker_submission_attempted=" + (meta.broker_submission_attempted ? "true" : "false")
               + " broker_request_accepted=" + (meta.broker_request_accepted ? "true" : "false")
               + " broker_retcode=" + IntegerToString((int)meta.broker_retcode)
               + " broker_retcode_description=" + meta.broker_retcode_description
               + " order_ticket=" + IntegerToString((long)meta.result_order_ticket)
               + " deal_ticket=" + IntegerToString((long)meta.result_deal_ticket)
               + " attribution_verified=" + (meta.execution_identity_verified ? "true" : "false")
               + " quarantine=" + (meta.execution_identity_quarantined ? "true" : "false")
               + " final_execution_success=" + (meta.final_execution_success ? "true" : "false")
               + " mql_final_allow=" + (mql_allow ? "true" : "false")
               + " reason=" + reason);
      bool terminal = (state == "BROKER_REQUEST_REJECTED" ||
                       state == "BROKER_ACCEPTED_IDENTITY_QUARANTINED" ||
                       state == "EXECUTION_IDENTITY_VERIFIED" ||
                       state == "POSITION_PARTIALLY_FILLED_IDENTITY_VERIFIED" ||
                       state == "POSITION_FILLED_IDENTITY_VERIFIED");
      string event = "{";
      event += JsonKVInt("timestamp", (int)_NowServerOrLocal()) + ",";
      event += JsonKVStr("request_id", meta.req_id) + ",";
      event += JsonKVStr("trade_key", meta.trade_key) + ",";
      event += JsonKVStr("candidate_id", meta.candidate_id) + ",";
      event += JsonKVStr("candidate_hash", meta.candidate_hash) + ",";
      event += JsonKVStr("execution_fingerprint", meta.final_execution_fingerprint) + ",";
      event += JsonKVStr("intended_order_type", meta.intended_order_type) + ",";
      event += JsonKVStr("execution_authority_state", state) + ",";
      event += JsonKVBool("terminal", terminal) + ",";
      event += JsonKVBool("broker_submission_attempted", meta.broker_submission_attempted) + ",";
      event += JsonKVBool("broker_request_accepted", meta.broker_request_accepted) + ",";
      event += JsonKVNum("broker_retcode", (double)meta.broker_retcode, 0) + ",";
      event += JsonKVStr("broker_retcode_description", meta.broker_retcode_description) + ",";
      event += JsonKVNum("result_order_ticket", (double)meta.result_order_ticket, 0) + ",";
      event += JsonKVNum("result_deal_ticket", (double)meta.result_deal_ticket, 0) + ",";
      event += JsonKVNum("broker_position_identifier", (double)meta.broker_position_identifier, 0) + ",";
      event += JsonKVBool("broker_partial_fill", meta.broker_partial_fill) + ",";
      event += JsonKVBool("execution_identity_verified", meta.execution_identity_verified) + ",";
      event += JsonKVBool("execution_identity_quarantined", meta.execution_identity_quarantined) + ",";
      event += JsonKVBool("final_execution_success", meta.final_execution_success) + ",";
      event += JsonKVBool("model_raw_allow", meta.model_raw_allow) + ",";
      event += JsonKVBool("python_final_allow", meta.python_final_allow) + ",";
      event += JsonKVBool("mql_final_allow", mql_allow) + ",";
      event += JsonKVStr("reason", reason);
      event += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\execution_authority_events.jsonl", event + "\n");
   }

   bool _RangesOverlap(const double low_a, const double high_a, const double low_b, const double high_b) const {
      return (low_a <= high_b && high_a >= low_b);
   }

   double _ClampToRange(const double value, const double low, const double high) const {
      double lo = MathMin(low, high);
      double hi = MathMax(low, high);
      return MathMax(lo, MathMin(hi, value));
   }

   bool _ResolveOteZone(const TradePlan &p, double &zone_low, double &zone_high) const {
      zone_low = 0.0;
      zone_high = 0.0;
      double a = p.po3.swing_low;
      double b = p.po3.swing_high;
      if(a <= 0 || b <= 0 || a == b) return false;
      double low = MathMin(a, b), high = MathMax(a, b);
      double range = high - low;
      if(range <= 0) return false;
      if(p.is_buy){
         zone_low = high - range * InpOTEHigh;
         zone_high = high - range * InpOTELow;
      } else {
         zone_low = low + range * InpOTELow;
         zone_high = low + range * InpOTEHigh;
      }
      if(zone_low > zone_high){
         double tmp = zone_low;
         zone_low = zone_high;
         zone_high = tmp;
      }
      return true;
   }

   string _FvgExecutionClass(const TradePlan &p) const {
      if(StringLen(p.fvg_execution_class) > 0) return p.fvg_execution_class;
      if(StringLen(p.fvg.execution_class) > 0) return p.fvg.execution_class;
      if(p.fvg.invalidated || p.fvg.structure_invalidated) return "invalidated_fvg";
      if(p.fvg.fully_filled) return "fully_mitigated_fvg";
      if(p.fvg.entry_invalid) return "entry_invalid_fvg";
      if(p.fvg.mid_mitigated) return "mid_mitigated_fvg";
      if(p.fvg.touched) return "touched_fvg";
      if(p.fvg.age_bars > 24) return "stale_fvg";
      return "virgin_fvg";
   }

   string _FamilyForTaxonomy(const ENUM_SETUP_TAXONOMY taxonomy) const {
      if(taxonomy == FULL_PO3_REVERSAL) return "full_po3_reversal";
      if(taxonomy == FULL_PO3_CONTINUATION) return "full_po3_continuation";
      if(taxonomy == FAILED_BREAKOUT_RECLAIM) return "micro_failed_breakout_reclaim";
      if(taxonomy == MICRO_RANGE_REENTRY || taxonomy == MICRO_SESSION_REENTRY) return "micro_range_reentry";
      if(taxonomy == MICRO_CONTINUATION_FVG || taxonomy == MICRO_NESTED_CONTINUATION) return "micro_continuation_fvg";
      if(taxonomy == MICRO_FVG_EDGE_REVERSAL || taxonomy == MICRO_BREAKER_RETEST) return "micro_bisi_sibi_edge";
      if(taxonomy == MICRO_FVG_MID_REVERSAL || taxonomy == MICRO_OTE_REVERSAL) return "micro_po3_reversal";
      return "";
   }

   bool _ResolveSetupTaxonomy(TradePlan &p, string &reason) {
      reason = "";
      if(p.setup_taxonomy != UNKNOWN_UNCLASSIFIED &&
         p.setup_taxonomy_version == SETUP_TAXONOMY_VERSION &&
         p.setup_taxonomy_enum == SetupTaxonomyToString(p.setup_taxonomy)){
         if(StringLen(p.taxonomy_mapping_source) == 0) p.taxonomy_mapping_source = "persisted_exact_taxonomy";
         if(StringLen(p.setup_type) == 0)
            p.setup_type = (p.setup_taxonomy == FULL_PO3_REVERSAL || p.setup_taxonomy == FULL_PO3_CONTINUATION ? "full_po3" : "micro_po3");
         if(StringLen(p.setup_subtype) == 0) p.setup_subtype = p.setup_taxonomy_enum;
         if(StringLen(p.setup_story_scope) == 0) p.setup_story_scope = p.po3.po3_scope;
         return true;
      }

      string branch = _NormToken(StringLen(p.entry_branch) > 0 ? p.entry_branch : p.entry_model);
      string family = _NormToken(p.setup_family);
      string setup_class = _NormToken(p.setup_class);
      string scope = _NormToken(p.po3.po3_scope);
      string structure = _NormToken(p.po3.structure_type);
      string fvg_class = _NormToken(_FvgExecutionClass(p));
      bool full_scope = (scope == "institutional_po3" || family == "full_po3" ||
                         family == "full_po3_reversal" || family == "full_po3_continuation" ||
                         setup_class == "full_po3" || setup_class == "full_po3_reversal" ||
                         setup_class == "full_po3_continuation");
      bool continuation = (branch == "continuation_reentry" || structure == "continuation" ||
                           structure == "continuation_bos" || structure == "micro_continuation" ||
                           fvg_class == "continuation_reentry" || p.fvg.continuation);
      bool failed_breakout = (structure == "micro_failed_breakout_reclaim" ||
                              family == "micro_failed_breakout_reclaim" ||
                              setup_class == "failed_breakout_reclaim");

      ENUM_SETUP_TAXONOMY resolved = UNKNOWN_UNCLASSIFIED;
      string source = "exact_branch_structure_scope";
      if(failed_breakout && (branch == "fvg_mid" || branch == "fvg_edge" || branch == "range_reentry"))
         resolved = FAILED_BREAKOUT_RECLAIM;
      else if(full_scope && continuation)
         resolved = FULL_PO3_CONTINUATION;
      else if(full_scope && (branch == "fvg_mid" || branch == "fvg_edge" || branch == "breaker_retest" ||
                             branch == "ote_inside_fvg" || branch == "nested_htf_ltf_fvg" || branch == "nested_fvg_edge"))
         resolved = FULL_PO3_REVERSAL;
      else if(branch == "session_reentry") resolved = MICRO_SESSION_REENTRY;
      else if(branch == "range_reentry") resolved = MICRO_RANGE_REENTRY;
      else if(branch == "nested_htf_ltf_fvg" || branch == "nested_fvg_edge") resolved = MICRO_NESTED_CONTINUATION;
      else if(continuation) resolved = MICRO_CONTINUATION_FVG;
      else if(branch == "breaker_retest") resolved = MICRO_BREAKER_RETEST;
      else if(branch == "ote_inside_fvg") resolved = MICRO_OTE_REVERSAL;
      else if(branch == "fvg_edge") resolved = MICRO_FVG_EDGE_REVERSAL;
      else if(branch == "fvg_mid") resolved = MICRO_FVG_MID_REVERSAL;

      p.setup_taxonomy = resolved;
      p.setup_taxonomy_version = SETUP_TAXONOMY_VERSION;
      p.setup_taxonomy_enum = SetupTaxonomyToString(resolved);
      p.taxonomy_mapping_source = (resolved == UNKNOWN_UNCLASSIFIED ? "" : source);
      p.setup_type = (resolved == FULL_PO3_REVERSAL || resolved == FULL_PO3_CONTINUATION ? "full_po3" : "micro_po3");
      p.setup_subtype = p.setup_taxonomy_enum;
      p.setup_story_scope = p.po3.po3_scope;
      if(resolved != UNKNOWN_UNCLASSIFIED){
         p.taxonomy_mapping_failure_reason = "";
         return true;
      }
      reason = "unknown_setup_taxonomy";
      p.setup_type = "unknown";
      p.taxonomy_mapping_failure_reason = "branch=" + branch + ";family=" + family +
                                         ";class=" + setup_class + ";scope=" + scope +
                                         ";structure=" + structure + ";fvg_class=" + fvg_class;
      return false;
   }

   void _LogUnknownSetupTaxonomy(const TradePlan &p, const string stage) {
      string row = "{";
      row += JsonKVInt("timestamp", (int)_NowServerOrLocal()) + ",";
      row += JsonKVStr("stage", stage) + ",";
      row += JsonKVStr("symbol", p.symbol) + ",";
      row += JsonKVStr("candidate_id", p.candidate_id) + ",";
      row += JsonKVStr("entry_branch", p.entry_branch) + ",";
      row += JsonKVStr("setup_family", p.setup_family) + ",";
      row += JsonKVStr("setup_class", p.setup_class) + ",";
      row += JsonKVStr("po3_scope", p.po3.po3_scope) + ",";
      row += JsonKVStr("structure_type", p.po3.structure_type) + ",";
      row += JsonKVStr("fvg_execution_class", p.fvg_execution_class) + ",";
      row += JsonKVStr("reason", p.taxonomy_mapping_failure_reason);
      row += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\unknown_setup_taxonomy.jsonl", row + "\n");
      _Journal("[setup_taxonomy] valid=false action=blocked_before_ai symbol=" + p.symbol +
               " candidate_id=" + p.candidate_id + " reason=" + p.taxonomy_mapping_failure_reason);
   }

   string _DeriveSetupFamily(const TradePlan &p) const {
      return _FamilyForTaxonomy(p.setup_taxonomy);
   }

   string _FamilyGroup(const string family) const {
      if(family == "full_po3_reversal" || family == "micro_po3_reversal" || family == "reversal") return "reversal";
      if(family == "full_po3_continuation" || family == "micro_continuation_fvg" || family == "continuation") return "continuation";
      if(family == "micro_failed_breakout_reclaim") return "failed_breakout";
      if(family == "micro_range_reentry" || family == "range" || family == "session") return "range";
      if(family == "micro_bisi_sibi_edge" || family == "breaker") return "edge";
      return family;
   }

   string _FamilyGroup(const TradePlan &p) const {
      return _FamilyGroup(StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
   }

   bool _FamilyRequiresFullPO3Sequence(const TradePlan &p) const {
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      if(InpStrategyMode == STRATEGY_FULL_PO3) return true;
      return (family == "full_po3_reversal" || family == "full_po3_continuation" ||
              family == "micro_po3_reversal" || family == "reversal");
   }

   bool _FamilyAllowedByStrategy(const TradePlan &p, string &reason) const {
      reason = "ok";
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      if(InpStrategyMode == STRATEGY_FULL_PO3){
         if(family == "full_po3_reversal" || family == "full_po3_continuation") return true;
         reason = "strategy_mode_full_po3_blocks_" + family;
         return false;
      }
      if(InpStrategyMode == STRATEGY_MICRO_PO3){
         if(family == "micro_po3_reversal" || family == "micro_range_reentry" || family == "micro_bisi_sibi_edge") return true;
         reason = "strategy_mode_micro_po3_blocks_" + family;
         return false;
      }
      if(InpStrategyMode == STRATEGY_SCALP_CONTINUATION){
         if(family == "micro_continuation_fvg" || family == "micro_failed_breakout_reclaim" ||
            family == "micro_range_reentry" || family == "micro_bisi_sibi_edge") return true;
         reason = "strategy_mode_scalp_blocks_" + family;
         return false;
      }
      return true;
   }

   bool _IsMicroFamily(const TradePlan &p) const {
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      return (StringFind(family, "micro_") == 0);
   }

   double _FamilySetupFloor(const TradePlan &p) const {
      if(_NormToken(p.entry_branch) == "session_reentry") return InpSetupFloorSession;
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      if(family == "full_po3_reversal" || family == "full_po3_continuation") return InpSetupFloorFullPO3;
      if(family == "micro_po3_reversal") return InpSetupFloorMicroPO3;
      if(family == "micro_continuation_fvg") return InpSetupFloorContinuationFamily;
      if(family == "micro_failed_breakout_reclaim") return InpSetupFloorFailedBreakout;
      if(family == "micro_range_reentry") return InpSetupFloorRangeFamily;
      if(family == "micro_bisi_sibi_edge") return InpSetupFloorMicroBisiSibi;
      string group = _FamilyGroup(family);
      if(group == "reversal") return InpSetupFloorReversal;
      if(group == "continuation") return InpSetupFloorContinuation;
      if(group == "edge") return InpSetupFloorBreaker;
      if(group == "range") return InpSetupFloorRange;
      return InpSetupFloorRange;
   }

   double _FamilyLlmQualityFloor(const TradePlan &p) const {
      string source = "";
      return EffectiveLlmQualityScoreThreshold(p, source);
   }

   double EffectiveLlmQualityScoreThreshold(const TradePlan &p, string &threshold_source) const {
      string family = _NormToken(StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      string setup_class = _NormToken(p.setup_class);
      string branch = _NormToken(p.entry_branch);

      double threshold = InpMinAiScoreTrend;
      threshold_source = "InpMinAiScoreTrend";

      if(family == "full_po3" ||
         family == "full_po3_reversal" ||
         family == "full_po3_continuation" ||
         StringFind(setup_class, "full_po3") >= 0){
         threshold = InpAiScoreFullPO3;
         threshold_source = "InpAiScoreFullPO3";
      } else if(family == "micro_po3" ||
                family == "micro_po3_reversal" ||
                family == "micro_bisi_sibi_edge" ||
                StringFind(setup_class, "micro_po3") >= 0 ||
                StringFind(setup_class, "micro_bisi_sibi") >= 0){
         threshold = InpAiScoreMicroPO3;
         threshold_source = "InpAiScoreMicroPO3";
      } else if(family == "micro_continuation_fvg" ||
                family == "continuation" ||
                branch == "continuation_reentry" ||
                StringFind(setup_class, "continuation") >= 0){
         threshold = InpAiScoreContinuation;
         threshold_source = "InpAiScoreContinuation";
      } else if(family == "micro_range_reentry" ||
                family == "range" ||
                branch == "range_reentry" ||
                StringFind(setup_class, "range_reentry") >= 0){
         threshold = InpAiScoreRange;
         threshold_source = "InpAiScoreRange";
      } else if(family == "micro_failed_breakout_reclaim" ||
                family == "failed_breakout" ||
                StringFind(setup_class, "failed_breakout") >= 0 ||
                StringFind(setup_class, "reclaim") >= 0){
         threshold = InpAiScoreFailedBreakout;
         threshold_source = "InpAiScoreFailedBreakout";
      }

      if(InpGlobalAiScoreAsHardFloor){
         threshold = MathMax(InpMinAiScoreTrend, threshold);
         threshold_source = threshold_source + "+global_floor";
      }
      return threshold;
   }

   double _FamilyMinRR(const TradePlan &p) const {
      string group = _FamilyGroup(p);
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      double base = InpMinRRMicroPO3;
      if(family == "full_po3_reversal" || family == "full_po3_continuation") base = InpMinRRFullPO3;
      else if(group == "failed_breakout") base = InpMinRRFailedBreakout;
      else if(group == "continuation" || group == "edge") base = InpMinRRContinuation;
      else if(group == "range") base = InpMinRRRange;
      return MathMax(0.0, base + p.session_weekday_rr_delta);
   }

   double _FamilyCostCeiling(const TradePlan &p) const {
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      if(family == "full_po3_reversal" || family == "full_po3_continuation") return MathMax(0.18, InpStandardTradeCostRCeiling);
      if(family == "micro_continuation_fvg" || family == "micro_failed_breakout_reclaim" ||
         family == "micro_range_reentry" || family == "micro_bisi_sibi_edge")
         return InpStandardTradeCostRCeiling;
      return MathMin(0.35, MathMax(0.12, _FamilyMinRR(p) * 0.16));
   }

   string _FamilyTargetModel(const TradePlan &p) const {
      string group = _FamilyGroup(p);
      if(group == "failed_breakout") return "range_mid_or_opposite_side";
      if(group == "continuation" || group == "edge") return "next_liquidity_session_range";
      if(group == "range") return "range_mid_opposite_side";
      return "liquidity_or_synthetic_rr";
   }

   string _FamilyManagementProfile(const TradePlan &p) const {
      if(p.runner_trade) return "runner";
      if(_IsMicroFamily(p)) return "standard_micro";
      return "standard_po3";
   }

   string _DeriveSetupClass(const TradePlan &p) const {
      string branch = p.entry_branch;
      if(StringLen(branch) == 0) branch = p.entry_model;
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      string fvg_class = _FvgExecutionClass(p);
      return family + "." + branch + "." + fvg_class;
   }

   string _NarrativeIdFromPlan(const TradePlan &p) const {
      string branch = p.entry_branch;
      if(StringLen(branch) == 0) branch = p.entry_model;
      datetime t_sweep = (p.source_t_sweep > 0 ? p.source_t_sweep : p.po3.t_sweep);
      datetime t_disp = (p.source_t_disp > 0 ? p.source_t_disp : p.po3.t_disp);
      datetime t_bos = (p.source_t_bos > 0 ? p.source_t_bos : p.po3.t_bos);
      return p.symbol + "|" + IntegerToString((int)t_sweep)
             + "|" + IntegerToString((int)t_disp)
             + "|" + IntegerToString((int)t_bos)
             + "|" + branch
             + "|" + _PriceSignature(p.symbol, p.fvg.mid);
   }

   string _PO3StoryId(const TradePlan &p) const {
      datetime t_sweep = (p.source_t_sweep > 0 ? p.source_t_sweep : p.po3.t_sweep);
      datetime t_disp = (p.source_t_disp > 0 ? p.source_t_disp : p.po3.t_disp);
      datetime t_bos = (p.source_t_bos > 0 ? p.source_t_bos : p.po3.t_bos);
      string side = (StringLen(p.source_sweep_side) > 0 ? p.source_sweep_side : p.po3.sweep_side);
      return p.symbol + "|" + side + "|" + IntegerToString((int)t_sweep)
             + "|" + IntegerToString((int)t_disp) + "|" + IntegerToString((int)t_bos);
   }

   string _PO3ContextId(const string symbol, const PO3Context &ctx) const {
      return symbol + "|" + ctx.sweep_side + "|" + IntegerToString((int)ctx.t_sweep)
             + "|" + IntegerToString((int)ctx.t_disp)
             + "|" + IntegerToString((int)ctx.t_bos);
   }

   bool _FreezeSourcePO3Story(TradePlan &p) {
      if(StringLen(p.po3.po3_scope) == 0) p.po3.po3_scope = PO3ScopeLabel(p.htf);
      bool first_freeze = (p.source_t_sweep <= 0);
      if(first_freeze){
         p.source_t_sweep = p.po3.t_sweep;
         p.source_t_disp = p.po3.t_disp;
         p.source_t_bos = p.po3.t_bos;
         p.source_context_tier = p.po3.context_tier;
         p.source_sweep_side = p.po3.sweep_side;
         p.source_manip_low = p.po3.manip_low;
         p.source_manip_high = p.po3.manip_high;
         p.source_dr_high = p.po3.dr_high;
         p.source_dr_low = p.po3.dr_low;
         p.source_liquidity_target = p.po3.liquidity_target;
         p.source_liquidity_kind = p.po3.liquidity_kind;
      } else {
         if(StringLen(p.source_context_tier) == 0) p.source_context_tier = p.po3.context_tier;
         if(StringLen(p.source_sweep_side) == 0) p.source_sweep_side = p.po3.sweep_side;
         if(p.source_t_disp <= 0) p.source_t_disp = p.po3.t_disp;
         if(p.source_t_bos <= 0) p.source_t_bos = p.po3.t_bos;
         if(p.source_manip_low <= 0) p.source_manip_low = p.po3.manip_low;
         if(p.source_manip_high <= 0) p.source_manip_high = p.po3.manip_high;
         if(p.source_dr_high <= 0) p.source_dr_high = p.po3.dr_high;
         if(p.source_dr_low <= 0) p.source_dr_low = p.po3.dr_low;
         if(p.source_liquidity_target <= 0) p.source_liquidity_target = p.po3.liquidity_target;
         if(StringLen(p.source_liquidity_kind) == 0) p.source_liquidity_kind = p.po3.liquidity_kind;
      }
      if(StringLen(p.setup_id) == 0) p.setup_id = _NarrativeIdFromPlan(p);
      if(StringLen(p.lineage_root_id) == 0) p.lineage_root_id = _PO3StoryId(p);
      if(p.attempt_number_for_sweep <= 0) p.attempt_number_for_sweep = 1;
      return (p.source_t_sweep > 0);
   }

   bool _SamePO3Story(const TradePlan &p, const PO3Context &live) const {
      datetime source_sweep = (p.source_t_sweep > 0 ? p.source_t_sweep : p.po3.t_sweep);
      if(source_sweep <= 0 || live.t_sweep <= 0) return false;
      if(live.t_sweep != source_sweep) return false;
      string source_side = (StringLen(p.source_sweep_side) > 0 ? p.source_sweep_side : p.po3.sweep_side);
      if(StringLen(source_side) > 0 && StringLen(live.sweep_side) > 0 && source_side != live.sweep_side) return false;
      if(live.bias_long != p.is_buy) return false;
      if(p.source_t_disp > 0 && live.t_disp > 0 && live.t_disp != p.source_t_disp) return false;
      if(p.source_t_bos > 0 && live.t_bos > 0 && live.t_bos != p.source_t_bos) return false;
      return true;
   }

   string _CompactSymbolCode(string symbol) const {
      StringToUpper(symbol);
      StringReplace(symbol, ".", "");
      StringReplace(symbol, "_", "");
      StringReplace(symbol, "-", "");
      if(StringLen(symbol) > 6) symbol = StringSubstr(symbol, 0, 6);
      if(StringLen(symbol) == 0) symbol = "SYM";
      return symbol;
   }

   string _ModelCodeForPlan(const TradePlan &p) const {
      string branch = _NormToken(p.entry_branch);
      string family = _NormToken(p.setup_family);
      if(StringFind(family, "failed_breakout") >= 0) return "FPR";
      if(branch == "breaker_retest") return "MBC";
      if(branch == "fvg_mid" || branch == "fvg_edge" || branch == "ote_inside_fvg") return "MPC";
      if(branch == "range_reentry") return "RNG";
      if(branch == "session_reentry") return "SES";
      if(branch == "continuation_reentry" || StringFind(family, "continuation") >= 0) return "MPC";
      if(StringFind(family, "micro") >= 0) return "MPC";
      if(StringFind(family, "full") >= 0 || StringFind(p.po3.po3_scope, "institutional") >= 0 || InpStrategyMode == STRATEGY_FULL_PO3) return "FULL";
      return "FULL";
   }

   string _SessionCodeForPlan(const TradePlan &p) const {
      if(StringLen(p.po3.session_code) > 0) return p.po3.session_code;
      string session = _NormToken(p.po3.session_name);
      if(session == "asia") return "ASIA";
      if(session == "london") return "LON";
      if(session == "new_york" || session == "newyork") return "NY";
      return "OFF";
   }

   string _SessionCodeForEntryTime(const datetime entry_time) const {
      datetime t = (entry_time > 0 ? entry_time : _NowServerOrLocal());
      return m_po3.SessionCodeAt(t);
   }

   string _KillzoneCodeForEntryTime(const datetime entry_time) const {
      datetime t = (entry_time > 0 ? entry_time : _NowServerOrLocal());
      return m_po3.KillzoneCodeAt(t);
   }

   string _ShortPlanId(const TradePlan &p) const {
      string raw = p.symbol + "|" + IntegerToString((int)p.source_t_sweep) + "|" +
                   IntegerToString((int)p.fvg.t_form) + "|" + IntegerToString(p.candidate_index) + "|" + p.entry_branch;
      uint h = 2166136261;
      for(int i=0; i<StringLen(raw); i++){
         h = (h ^ (uint)StringGetCharacter(raw, i)) * 16777619;
      }
      int id = (int)(h % 100000);
      return StringFormat("%05d", id);
   }

   string _BrokerCommentForPlanAtEntryTime(const TradePlan &p, const datetime entry_time) const {
      string model = (StringLen(p.model_code) > 0 ? p.model_code : _ModelCodeForPlan(p));
      string session = _SessionCodeForEntryTime(entry_time);
      string kz = _KillzoneCodeForEntryTime(entry_time);
      string comment = model + "-" + session + "-" + kz + "-" + _CompactSymbolCode(p.symbol) + "-" + _ShortPlanId(p);
      if(StringLen(comment) > 31)
         comment = model + "-" + session + "-" + kz + "-" + StringSubstr(_CompactSymbolCode(p.symbol), 0, 4) + "-" + _ShortPlanId(p);
      return comment;
   }

   string _BrokerCommentForPlan(const TradePlan &p) const {
      datetime entry_time = (p.filled_at > 0 ? p.filled_at : (p.planned_at > 0 ? p.planned_at : (p.created_at > 0 ? p.created_at : _NowServerOrLocal())));
      return _BrokerCommentForPlanAtEntryTime(p, entry_time);
   }

   void _ApplyEntryTimeCommentIdentity(TradePlan &p, const datetime entry_time) const {
      datetime t = (entry_time > 0 ? entry_time : _NowServerOrLocal());
      p.session_code = _SessionCodeForEntryTime(t);
      p.killzone_code = _KillzoneCodeForEntryTime(t);
      p.broker_comment = _BrokerCommentForPlanAtEntryTime(p, t);
   }

   void _InitializeNarrativeFields(TradePlan &p) {
      if(!p.risk_multipliers_initialized){
         p.subtype_risk_multiplier = 1.0;
         p.active_policy_risk_multiplier = 1.0;
         p.bucket_policy_risk_multiplier = 1.0;
         p.session_weekday_risk_multiplier = 1.0;
         p.risk_multipliers_initialized = true;
      }
      if(StringLen(p.entry_branch) == 0) p.entry_branch = p.entry_model;
      if(StringLen(p.fvg_execution_class) == 0) p.fvg_execution_class = _FvgExecutionClass(p);
      string taxonomy_reason = "";
      _ResolveSetupTaxonomy(p, taxonomy_reason);
      if(StringLen(p.setup_family) == 0) p.setup_family = _DeriveSetupFamily(p);
      if(StringLen(p.setup_class) == 0) p.setup_class = _DeriveSetupClass(p);
      if(StringLen(p.model_code) == 0) p.model_code = _ModelCodeForPlan(p);
      if(StringLen(p.session_code) == 0) p.session_code = _SessionCodeForPlan(p);
      if(StringLen(p.killzone_code) == 0) p.killzone_code = (p.po3.in_killzone ? "K" : "NK");
      if(StringLen(p.broker_comment) == 0) p.broker_comment = _BrokerCommentForPlan(p);
      if(StringLen(p.origin_quality) == 0) p.origin_quality = _OriginQuality(p, p.fvg);
      if(StringLen(p.exclusive_model_name) == 0) p.exclusive_model_name = _ExclusiveModelName();
      if(StringLen(p.management_profile) == 0) p.management_profile = _FamilyManagementProfile(p);
      if(StringLen(p.target_model) == 0) p.target_model = _FamilyTargetModel(p);
      if(StringLen(p.analytics_key) == 0) p.analytics_key = p.symbol + "|" + p.setup_family + "|" + p.entry_branch;
      _FreezeSourcePO3Story(p);
      if(StringLen(p.setup_id) == 0) p.setup_id = _NarrativeIdFromPlan(p);
      if(StringLen(p.fvg_id) == 0)
         p.fvg_id = p.symbol + "|fvg|" + IntegerToString((int)p.fvg.t_form)
                    + "|" + _PriceSignature(p.symbol, p.fvg.lower)
                    + "|" + _PriceSignature(p.symbol, p.fvg.upper);
      if(StringLen(p.candidate_id) == 0)
         p.candidate_id = p.setup_id + "|" + p.entry_branch + "|" + _PriceSignature(p.symbol, p.entry_est);
      if(StringLen(p.ai_decision_id) == 0 && StringLen(p.ai.decision_id) > 0) p.ai_decision_id = p.ai.decision_id;
      if(StringLen(p.policy_version) == 0) p.policy_version = ENGINE_INPUT_SCHEMA;
      if(StringLen(p.risk_version) == 0) p.risk_version = RISK_MODEL_VERSION;
      if(StringLen(p.lineage_root_id) == 0) p.lineage_root_id = p.setup_id;
      if(p.lineage_version <= 0) p.lineage_version = 1;
      if(p.attempt_number_for_sweep <= 0) p.attempt_number_for_sweep = 1;
      if(StringLen(p.narrative_state) == 0) p.narrative_state = "staged";
      _PopulateCohortMetadata(p);
   }

   void _CarryNarrativeForward(TradePlan &next, const TradePlan &prev, const bool material_refresh) {
      _InitializeNarrativeFields(next);
      string prior_setup_id = prev.setup_id;
      if(StringLen(prior_setup_id) == 0) prior_setup_id = _NarrativeIdFromPlan(prev);
      if(StringLen(next.lineage_root_id) == 0)
         next.lineage_root_id = (StringLen(prev.lineage_root_id) > 0 ? prev.lineage_root_id : prior_setup_id);
      next.parent_setup_id = (material_refresh ? prior_setup_id : prev.parent_setup_id);
      next.lineage_version = MathMax(1, material_refresh ? (prev.lineage_version + 1) : MathMax(prev.lineage_version, 1));
      next.setup_id = _NarrativeIdFromPlan(next) + "|" + IntegerToString(next.lineage_version);
      next.narrative_state = (StringLen(prev.narrative_state) > 0 ? prev.narrative_state : next.narrative_state);
      next.superseded_by = prev.superseded_by;
      next.invalidation_cause = prev.invalidation_cause;
      next.source_t_sweep = prev.source_t_sweep;
      next.source_t_disp = prev.source_t_disp;
      next.source_t_bos = prev.source_t_bos;
      next.source_context_tier = prev.source_context_tier;
      next.source_sweep_side = prev.source_sweep_side;
      next.source_manip_low = prev.source_manip_low;
      next.source_manip_high = prev.source_manip_high;
      next.source_dr_high = prev.source_dr_high;
      next.source_dr_low = prev.source_dr_low;
      next.source_liquidity_target = prev.source_liquidity_target;
      next.source_liquidity_kind = prev.source_liquidity_kind;
      next.attempt_number_for_sweep = MathMax(1, prev.attempt_number_for_sweep);
   }

   void _ApplySetupManagementProfile(TradePlan &p) {
      _InitializeNarrativeFields(p);

      p.tp1_r_multiple = 1.0;
      p.tp1_partial_pct = InpTP1PartialPct;
      p.be_rule = (InpBEEnable && InpBEOnTP1 ? "tp1" : "off");
      p.be_trigger_r = InpBETriggerR;
      p.be_offset_points = InpBEOffsetPts;
      p.stop_buffer_points = InpSLBufferPts;
      if(p.arm_max_bars <= 0) p.arm_max_bars = _EffectiveWatchlistMaxBars();
      p.max_watch_minutes = _EffectiveWatchlistMaxMinutes(p.htf);
      p.penalty_mae_trigger_r = InpPenaltyMaeTriggerR;
      p.penalty_giveback_trigger_r = InpPenaltyGivebackTrigMfeR;
      p.penalty_giveback_floor_r = InpPenaltyGivebackFloorR;
      p.penalty_stuck_minutes = InpPenaltyStuckMinutes;
      p.penalty_stuck_min_mfe_r = InpPenaltyStuckMinMfeR;
      p.penalty_dr_invalid_cut_pct = InpPenaltyDrInvalidCutPct;
      p.penalty_fvg_invalid_cut_pct = InpPenaltyFvgInvalidCutPct;
      p.penalty_close_strikes = InpPenaltyCloseStrikes;

      string family_group = _FamilyGroup(p);
      if(family_group == "reversal"){
         p.tp1_r_multiple = 1.00;
         p.tp1_partial_pct = 0.50;
         p.be_rule = "tp1";
         p.be_trigger_r = 1.00;
         p.stop_buffer_points = InpSLBufferPts + 1;
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 80);
         p.penalty_mae_trigger_r = -0.65;
         p.penalty_giveback_trigger_r = 1.00;
         p.penalty_giveback_floor_r = 0.18;
         p.penalty_stuck_minutes = 40;
         p.penalty_stuck_min_mfe_r = 0.18;
      } else if(family_group == "continuation"){
         p.tp1_r_multiple = 0.80;
         p.tp1_partial_pct = 0.33;
         p.be_rule = "structure";
         p.be_trigger_r = 1.35;
         p.stop_buffer_points = InpSLBufferPts + 2;
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 150);
         p.penalty_mae_trigger_r = -0.95;
         p.penalty_giveback_trigger_r = 1.60;
         p.penalty_giveback_floor_r = 0.45;
         p.penalty_stuck_minutes = 70;
         p.penalty_stuck_min_mfe_r = 0.35;
         p.penalty_close_strikes = InpPenaltyCloseStrikes + 1;
      } else if(family_group == "edge"){
         p.tp1_r_multiple = 0.90;
         p.tp1_partial_pct = 0.40;
         p.be_rule = "bos_level";
         p.be_trigger_r = 0.90;
         p.stop_buffer_points = InpSLBufferPts + 2;
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 120);
         p.penalty_mae_trigger_r = -0.70;
         p.penalty_giveback_trigger_r = 1.10;
         p.penalty_giveback_floor_r = 0.22;
         p.penalty_stuck_minutes = 45;
         p.penalty_stuck_min_mfe_r = 0.22;
      } else if(family_group == "failed_breakout"){
         p.tp1_r_multiple = 0.72;
         p.tp1_partial_pct = 0.45;
         p.be_rule = "range_reclaim";
         p.be_trigger_r = 0.80;
         p.stop_buffer_points = InpSLBufferPts;
         p.max_watch_minutes = MathMin(MathMax(p.max_watch_minutes, 45), 90);
         p.penalty_mae_trigger_r = -0.62;
         p.penalty_giveback_trigger_r = 0.90;
         p.penalty_giveback_floor_r = 0.16;
         p.penalty_stuck_minutes = 32;
         p.penalty_stuck_min_mfe_r = 0.14;
      } else if(family_group == "range"){
         p.tp1_r_multiple = 0.85;
         p.tp1_partial_pct = 0.35;
         p.be_rule = "session_level";
         p.be_trigger_r = 0.95;
         p.stop_buffer_points = InpSLBufferPts + 1;
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 110);
         p.penalty_mae_trigger_r = -0.72;
         p.penalty_giveback_trigger_r = 1.05;
         p.penalty_giveback_floor_r = 0.20;
         p.penalty_stuck_minutes = 42;
         p.penalty_stuck_min_mfe_r = 0.20;
      }

      if(_IsMicroFamily(p)){
         p.management_profile = (p.runner_trade ? "runner" : "standard_micro");
         p.arm_max_bars = MathMin(MathMax(p.arm_max_bars, 8), MathMax(8, InpWatchlistMaxBars));
         p.max_watch_minutes = MathMin(MathMax(p.max_watch_minutes, 35), MathMax(35, InpWatchlistMaxMinutes));
         p.penalty_stuck_minutes = MathMin(p.penalty_stuck_minutes, 45);
      }

      if(p.fvg_execution_class == "fresh_fvg" || p.fvg_execution_class == "virgin_fvg"){
         p.arm_max_bars = MathMax(p.arm_max_bars, 18);
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 75);
      } else if(p.fvg_execution_class == "touched_fvg" || p.fvg_execution_class == "mid_mitigated_fvg" ||
                p.fvg_execution_class == "mid_mitigated_reentry"){
         p.arm_max_bars = MathMax(p.arm_max_bars, 14);
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 60);
         p.penalty_fvg_invalid_cut_pct = MathMax(p.penalty_fvg_invalid_cut_pct, 0.55);
      } else if(p.fvg_execution_class == "continuation_reentry"){
         p.arm_max_bars = MathMax(p.arm_max_bars, 30);
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 150);
         p.tp1_partial_pct = MathMin(p.tp1_partial_pct, 0.33);
      } else if(p.fvg_execution_class == "stale_fvg"){
         p.arm_max_bars = MathMax(p.arm_max_bars, 10);
         p.max_watch_minutes = MathMax(p.max_watch_minutes, 45);
      } else if(_IsFvgEntryNonTradable(p.fvg_execution_class)){
         p.arm_max_bars = 1;
         p.max_watch_minutes = 10;
      }

      p.tp1_partial_pct = InpTP1PartialPct;
      p.be_trigger_r = MathMax(p.be_trigger_r, InpBETriggerR);
      p.penalty_mae_trigger_r = MathMin(p.penalty_mae_trigger_r, InpPenaltyMaeTriggerR);
      p.penalty_giveback_trigger_r = MathMax(p.penalty_giveback_trigger_r, InpPenaltyGivebackTrigMfeR);
      p.penalty_giveback_floor_r = MathMax(p.penalty_giveback_floor_r, InpPenaltyGivebackFloorR);
      p.penalty_stuck_minutes = MathMax(p.penalty_stuck_minutes, InpPenaltyStuckMinutes);
      p.penalty_stuck_min_mfe_r = MathMax(p.penalty_stuck_min_mfe_r, InpPenaltyStuckMinMfeR);
      p.penalty_dr_invalid_cut_pct = MathMin(p.penalty_dr_invalid_cut_pct, InpPenaltyDrInvalidCutPct);
      p.penalty_fvg_invalid_cut_pct = MathMin(p.penalty_fvg_invalid_cut_pct, InpPenaltyFvgInvalidCutPct);
      p.penalty_close_strikes = MathMax(p.penalty_close_strikes, InpPenaltyCloseStrikes);
   }

   string _VolatilityProfile(const TradePlan &p) const {
      if(p.news_risk >= 1.0 || p.expansion_score > InpExpansionRatioMax) return "shock";
      if(p.po3.session_name == "OFF_HOURS") return "off_hours";
      if(p.expansion_score > 0 && p.expansion_score < InpExpansionRatioMin) return "compressed";
      if(p.session_vol_ratio >= 0.70 || p.atr_pct >= MathMax(InpAtrMinPct * 4.0, 0.0060)) return "high";
      if(p.session_vol_ratio > 0 && p.session_vol_ratio < InpSessionVolMinAdrFrac) return "subdued";
      return "balanced";
   }

   double _SequenceQuality(const TradePlan &p) const {
      double score = 0.0;
      if(p.po3.has_sweep) score += 2.1;
      if(p.po3.has_displacement) score += 2.0;
      if(p.po3.has_bos) score += 1.8;
      if(p.po3.has_follow_through) score += 1.0;
      score += MathMin(1.2, p.po3.sweep_strength * 0.9);
      score += MathMin(0.8, p.po3.displacement_score * 0.08);
      if(p.po3.htf_bos || p.po3.htf_mss || p.po3.htf_choch) score += 1.0;
      if(p.po3.ltf_bos || p.po3.ltf_mss || p.po3.ltf_choch) score += 0.8;
      if(p.po3.in_killzone) score += 0.5;
      return _ClampRange(score, 0.0, 10.0);
   }

   double _HtfAlignmentScore(const TradePlan &p) const {
      double score = 4.0;
      int want = (p.is_buy ? 1 : -1);
      if(p.po3.daily_bias_dir == want) score += 1.8;
      else if(p.po3.daily_bias_dir == -want) score -= 1.6;
      if(p.po3.h4_bias_dir == want) score += 1.6;
      else if(p.po3.h4_bias_dir == -want) score -= 1.2;
      if(p.po3.h1_bias_dir == want) score += 1.2;
      else if(p.po3.h1_bias_dir == -want) score -= 1.0;
      if(p.po3.htf_mss || p.po3.htf_choch) score += 1.1;
      if((p.is_buy && p.trend_slope_pct > 0) || (!p.is_buy && p.trend_slope_pct < 0)) score += 0.9;
      else if(MathAbs(p.trend_slope_pct) > 0.00015) score -= 1.1;
      if(p.adx_value >= InpAdxMin) score += 0.6;
      return _ClampRange(score, 0.0, 10.0);
   }

   double _AdverseContextScore(const TradePlan &p) const {
      double score = 0.0;
      if(p.po3.session_name == "OFF_HOURS") score += 1.4;
      score += MathMin(2.5, p.news_risk * 2.5);
      if(StringFind(p.obstacle_kind, "htf_opposing_imbalance") >= 0) score += 1.8;
      else if(StringLen(p.obstacle_kind) > 0) score += 0.7;
      if(p.vwap_dist_atr > InpVwapMaxDistAtr) score += MathMin(2.0, (p.vwap_dist_atr - InpVwapMaxDistAtr) * 0.9);
      if(p.expansion_score > InpExpansionRatioMax) score += 1.5;
      if(p.session_vol_ratio > 0 && p.session_vol_ratio < InpSessionVolMinAdrFrac) score += 0.9;
      return _ClampRange(score, 0.0, 10.0);
   }

   bool _IsRunnerTrade(const TradePlan &p) const {
      if(p.target_source == "liquidity_target") return true;
      if(StringFind(p.target_source, "prev_week") >= 0) return true;
      if(StringFind(p.target_source, "prev_day") >= 0 && _ExecutionRR2(p) >= 2.2) return true;
      if(_FamilyGroup(p) == "continuation" && _ExecutionRR2(p) >= 2.4) return true;
      return (p.effective_rr2 >= 2.8);
   }

   double _OteDistanceFrac(const TradePlan &p) const {
      double zone_low = 0.0, zone_high = 0.0;
      if(!_ResolveOteZone(p, zone_low, zone_high)) return 0.0;
      double width = MathAbs(zone_high - zone_low);
      if(width <= 0.0) return 0.0;
      double entry = p.entry_est;
      if(entry >= zone_low && entry <= zone_high) return 0.0;
      if(entry < zone_low) return (zone_low - entry) / width;
      return (entry - zone_high) / width;
   }

   string _OteState(const TradePlan &p) const {
      string branch = p.entry_branch;
      if(StringLen(branch) == 0) branch = p.entry_model;
      string family = p.setup_family;
      bool needs_ote = (_FamilyGroup(family) == "reversal" || branch == "ote_inside_fvg" ||
                        (branch == "range_reentry" && family == "reversal"));
      if(!InpRequireOTE || !needs_ote) return "not_required";
      if(_FamilyGroup(family) != "reversal") return "not_required";
      double dist = _OteDistanceFrac(p);
      double soft = (p.ote_softness_frac > 0.0 ? p.ote_softness_frac : InpOteSoftnessFrac);
      if(dist <= 0.0) return "inside";
      if(dist <= soft) return "soft_lost";
      return "lost";
   }

   double _EstimatedSessionSlippagePts(const TradePlan &p) const {
      if(p.news_risk >= 1.0 || p.expansion_score > InpExpansionRatioMax) return (double)InpSessionFillPenaltyPtsShock;
      if(p.po3.session_name == "OFF_HOURS") return (double)InpSessionFillPenaltyPtsOffHours;
      return (double)InpSessionFillPenaltyPtsActive;
   }

   void _EstimateExecutionCosts(TradePlan &p) {
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double spread = _CurrentSpreadPrice(p.symbol);
      double slip_pts = _EstimatedSessionSlippagePts(p);
      if(p.runner_trade) slip_pts += 1.0;
      p.estimated_slippage_price = slip_pts * point;
      string cost_source = "";
      int cost_samples = 0;
      double median_cost = 0.0;
      datetime cost_window_start = 0, cost_window_end = 0;
      double stressed_cost_per_lot = BrokerCostEstimatePerLot(p.symbol,
                                                              cost_source,
                                                              cost_samples,
                                                              median_cost,
                                                              cost_window_start,
                                                              cost_window_end);
      p.estimated_commission_money = stressed_cost_per_lot;
      p.estimated_cost_source = cost_source;
      p.estimated_cost_sample_size = cost_samples;
      p.estimated_cost_stressed_per_lot = stressed_cost_per_lot;
      p.commission_model_version = COMMISSION_MODEL_SCHEMA_VERSION;
      p.estimated_cost_price = spread + p.estimated_slippage_price;
      double risk = MathAbs(p.entry_est - p.sl);
      p.execution_cost_r = (risk > 0.0 ? spread / risk : 0.0);
      p.spread_r = p.execution_cost_r;
      p.slippage_r = (risk > 0.0 ? p.estimated_slippage_price / risk : 0.0);
      p.commission_r = 0.0;
      if(risk > 0.0 && stressed_cost_per_lot > 0.0){
         double tick_size = SymbolInfoDouble(p.symbol, SYMBOL_TRADE_TICK_SIZE);
         double tick_value = SymbolInfoDouble(p.symbol, SYMBOL_TRADE_TICK_VALUE);
         if(tick_size > 0.0 && tick_value > 0.0){
            double risk_money_per_lot = (risk / tick_size) * tick_value;
            if(risk_money_per_lot > 0.0) p.commission_r = stressed_cost_per_lot / risk_money_per_lot;
         }
      }
      // Keyed per symbol: a change in any field still prints, an exact repeat is
      // counted and suppressed.  This is the line the operator was watching scroll
      // past once per simulated second on a value that never moved.
      _JournalOnChange("broker_cost_estimate|" + p.symbol,
               "[broker_cost_estimate] symbol=" + p.symbol
               + " source=" + cost_source
               + " samples=" + IntegerToString(cost_samples)
               + " median_per_lot=" + DoubleToString(median_cost, 6)
               + " stressed_per_lot=" + DoubleToString(stressed_cost_per_lot, 6)
               + " percentile=" + DoubleToString(InpBrokerCostStressedPercentile, 4)
               + " window_start=" + TimeToString(cost_window_start, TIME_DATE)
               + " window_end=" + TimeToString(cost_window_end, TIME_DATE));
   }

   void _ApplyStopAudit(TradePlan &p, const double initial_sl, const double entry_price) {
      double initial_dist = MathAbs(entry_price - initial_sl);
      double final_dist = MathAbs(entry_price - p.sl);
      double ltf_rates[];
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(p.symbol, p.ltf, 0, MathMax(40, InpStopAuditNoiseBars + 10), rates);
      double ltf_atr = ATRFromRates(rates, got, 14);

      p.broker_min_stop_distance = _BrokerBufferPrice(p.symbol);
      p.stop_noise_band = MathMax(_CurrentSpreadPrice(p.symbol) * InpMinStopSpreadMult,
                                  ltf_atr * InpStopAuditNoiseAtrFrac);
      p.stop_floor_distance = _MinPlanStopDistance(p, entry_price);
      double zone_width = MathAbs(p.fvg.upper - p.fvg.lower);
      double distortion_band = MathMax(zone_width * 0.35, p.stop_noise_band * 0.75);
      p.stop_microstructure_distortion = (initial_dist > 0.0 && initial_dist <= distortion_band);

      p.stop_floor_reason = "structural";
      if(initial_dist > 0.0 && p.broker_min_stop_distance > 0.0 && initial_dist <= p.broker_min_stop_distance * 1.02)
         p.stop_floor_reason = "broker_min_distance";
      else if(initial_dist > 0.0 && p.stop_noise_band > 0.0 && initial_dist <= p.stop_noise_band * 1.02)
         p.stop_floor_reason = "spread_noise_floor";
      else if(p.stop_microstructure_distortion)
         p.stop_floor_reason = "microstructure_distortion";
      else if(final_dist > initial_dist + (p.stop_floor_distance * 0.05))
         p.stop_floor_reason = "raised_to_stop_floor";

      double quality = 7.0;
      if(p.stop_floor_reason == "broker_min_distance") quality -= 1.4;
      else if(p.stop_floor_reason == "spread_noise_floor") quality -= 1.1;
      else if(p.stop_floor_reason == "microstructure_distortion") quality -= 1.6;
      if(final_dist > 0.0 && p.stop_floor_distance > 0.0) quality += MathMin(1.5, (final_dist / p.stop_floor_distance) - 1.0);
      p.stop_quality_score = _ClampRange(quality, 0.0, 10.0);
   }

   double _HeuristicWinRateDiagnostic(const TradePlan &p) const {
      double win_rate = 0.34;
      win_rate += (p.setup_score - 40.0) * 0.0045;
      win_rate += (p.sequence_quality - 5.0) * 0.018;
      win_rate += (p.htf_alignment_score - 5.0) * 0.014;
      win_rate -= p.execution_cost_r * 0.18;
      win_rate -= p.adverse_context_score * 0.016;
      if(p.subtype_shrunk_win_rate > 0.0)
         win_rate = 0.55 * p.subtype_shrunk_win_rate + 0.45 * win_rate;
      return _ClampRange(win_rate, 0.15, 0.82);
   }

   double _HeuristicWinRateGrossDiagnostic(const TradePlan &p) const {
      TradePlan tmp = p;
      tmp.execution_cost_r = 0.0;
      return _HeuristicWinRateDiagnostic(tmp);
   }

   double _HeuristicQualityEstimateDiagnostic(const TradePlan &p) const {
      double win_rate = _HeuristicWinRateDiagnostic(p);
      double rr = MathMax(0.0, _ExecutionRR2(p));
      double base = win_rate * rr - (1.0 - win_rate);
      if(p.subtype_avg_r != 0.0) base = 0.65 * base + 0.35 * p.subtype_avg_r;
      return base;
   }

   double _HeuristicQualityEstimateGrossDiagnostic(const TradePlan &p) const {
      double win_rate = _HeuristicWinRateGrossDiagnostic(p);
      double rr = MathMax(0.0, _ExecutionRR2(p));
      double base = win_rate * rr - (1.0 - win_rate);
      if(p.subtype_avg_r != 0.0) base = 0.65 * base + 0.35 * p.subtype_avg_r;
      return base;
   }

   void _PopulateDerivedPlanFields(TradePlan &p) {
      _InitializeNarrativeFields(p);
      p.asset_class = _AssetClassForSymbol(p.symbol);
      p.regime_profile = _RegimeBucket(p);
      p.volatility_profile = _VolatilityProfile(p);
      p.policy_bucket = p.regime_profile + "|" + p.po3.session_name + "|" + p.setup_class + "|" + p.volatility_profile;
      p.portfolio_cluster = _PortfolioClusterKey(p);
      p.usd_exposure_key = _UsdExposureKey(p);
      p.liquidity_rr = _LiquidityRR(p);
      p.sequence_quality = _SequenceQuality(p);
      p.htf_alignment_score = _HtfAlignmentScore(p);
      p.adverse_context_score = _AdverseContextScore(p);
      p.runner_trade = _IsRunnerTrade(p);
      if(p.ote_softness_frac <= 0.0){
         p.ote_softness_frac = (m_active_policy.ote_softness_frac > 0.0 ? m_active_policy.ote_softness_frac : InpOteSoftnessFrac);
      }
      p.ote_distance_frac = _OteDistanceFrac(p);
      p.ote_state = _OteState(p);
      _EstimateExecutionCosts(p);
      p.heuristic_quality_estimate_gross = _HeuristicQualityEstimateGrossDiagnostic(p);
      p.heuristic_quality_estimate = _HeuristicQualityEstimateDiagnostic(p);
      _FinalizePlanEconomics(p, "derived_plan_fields", false);
      // Explicit migration aliases: persisted for old diagnostics, never authoritative.
      p.gross_expected_r = p.heuristic_quality_estimate_gross;
      p.net_expected_r = p.heuristic_quality_estimate;
      p.expected_value_r = p.heuristic_quality_estimate;
      p.target_model = _FamilyTargetModel(p);
      p.management_profile = _FamilyManagementProfile(p);
      p.analytics_key = p.symbol + "|" + p.setup_family + "|" + p.entry_branch + "|" + p.po3.session_name;
      if(StringLen(p.symbol_policy_action) == 0) p.symbol_policy_action = "allow";
      if(StringLen(p.symbol_policy_reason) == 0) p.symbol_policy_reason = "exploration_until_symbol_sample_threshold";
      if(StringLen(p.family_policy_action) == 0) p.family_policy_action = "allow";
      if(StringLen(p.family_policy_reason) == 0) p.family_policy_reason = "exploration_until_family_sample_threshold";
   }

   bool _ApplySubtypeEvidence(TradePlan &p, string &reason) {
      reason = "";
      _LoadActivePolicyIfNeeded();
      if(!m_active_policy_schema_valid){ reason = "active_policy_schema_invalid"; return false; }
      p.active_policy_risk_multiplier = (m_active_policy.valid ? m_active_policy.default_risk_multiplier : 1.0);
      if(p.active_policy_risk_multiplier <= 0.0){ reason = "active_policy_risk_multiplier_zero"; return false; }
      if(p.active_policy_risk_multiplier > 1.0){ reason = "active_policy_risk_multiplier_invalid"; return false; }
      string subtype_key = _Po3Subtype(p) + "|" + _FvgSubtype(p) + "|" + p.setup_class + "|" + _RegimeBucket(p);
      int idx = _FindSubtypePolicy(subtype_key);
      p.subtype_policy_action = "allow";
      p.subtype_policy_penalty = 0.0;
      p.subtype_shrunk_win_rate = 0.0;
      p.subtype_avg_r = 0.0;
      if(idx < 0) return true;

      SubtypePolicyEntry entry = m_subtype_policy[idx];
      p.subtype_policy_action = (StringLen(entry.action) > 0 ? entry.action : "allow");
      p.subtype_policy_penalty = entry.score_penalty;
      p.subtype_shrunk_win_rate = entry.shrunk_win_rate;
      p.subtype_avg_r = entry.avg_r;
      p.subtype_risk_multiplier = entry.risk_multiplier;
      if(StringLen(entry.policy_id) > 0) p.policy_snapshot_id = entry.policy_id;

      if(p.subtype_policy_action == "suppress"){
         reason = "subtype_suppressed";
         return false;
      }
      if(p.subtype_risk_multiplier <= 0.0){
         reason = "subtype_risk_multiplier_zero";
         return false;
      }
      if(p.subtype_policy_action == "downrank" && p.subtype_policy_penalty > 0.0){
         p.setup_score -= p.subtype_policy_penalty;
      }
      return true;
   }

   bool _ApplyContextPolicy(TradePlan &p, string &reason) {
      reason = "";
      _LoadActivePolicyIfNeeded();
      if(!m_active_policy_schema_valid){ reason = "active_policy_schema_invalid"; return false; }
      int idx = _FindContextPolicy(p.policy_bucket);
      if(idx < 0) return true;

      ContextPolicyEntry entry = m_context_policy[idx];
      if(StringLen(entry.policy_id) > 0) p.policy_snapshot_id = entry.policy_id;
      if(entry.action == "suppress"){
         reason = "context_suppressed";
         return false;
      }
      if(entry.risk_multiplier <= 0.0){
         reason = "context_risk_multiplier_zero";
         return false;
      }
      p.setup_score += entry.score_bias;
      p.heuristic_quality_estimate += entry.expected_value_bias;
      p.expected_value_r = p.heuristic_quality_estimate;
      p.subtype_risk_multiplier *= entry.risk_multiplier;
      return true;
   }

   bool _ApplySessionWeekdayPolicy(TradePlan &p, string &reason) {
      reason = "";
      _LoadActivePolicyIfNeeded();
      if(!m_active_policy_schema_valid){ reason = "active_policy_schema_invalid"; return false; }

      if(p.session_weekday_risk_multiplier > 0.0 && MathAbs(p.session_weekday_risk_multiplier - 1.0) > 0.0001){
         p.subtype_risk_multiplier /= p.session_weekday_risk_multiplier;
      }

      p.session_weekday_policy_action = "";
      p.session_weekday_risk_multiplier = 1.0;
      p.session_weekday_rr_delta = 0.0;
      p.session_weekday_score_bias = 0.0;

      string session = p.po3.session_name;
      if(StringLen(session) == 0) session = "unknown";
      datetime basis = (p.planned_at > 0 ? p.planned_at : _NowServerOrLocal());
      string weekday = _WeekdayName(basis);
      if(session == "unknown" || weekday == "unknown") return true;

      int idx = _FindSessionWeekdayPolicy(session, weekday);
      if(idx < 0) return true;

      SessionWeekdayPolicyEntry entry = m_session_weekday_policy[idx];
      string action = (StringLen(entry.action) > 0 ? entry.action : "monitor");
      p.session_weekday_policy_action = action;
      if(entry.risk_multiplier < 0.0 || entry.risk_multiplier > 1.0){
         reason = "session_weekday_risk_multiplier_invalid";
         return false;
      }
      p.session_weekday_risk_multiplier = entry.risk_multiplier;
      p.session_weekday_rr_delta = _ClampRange(entry.rr_floor_delta, -0.25, 0.50);
      p.session_weekday_score_bias = _ClampRange(entry.score_bias, -12.0, 8.0);
      if(StringLen(entry.policy_id) > 0) p.policy_snapshot_id = entry.policy_id;

      if(action == "suppress"){
         reason = "session_weekday_suppressed";
         return false;
      }
      if(p.session_weekday_risk_multiplier <= 0.0){
         reason = "session_weekday_risk_multiplier_zero";
         return false;
      }

      p.setup_score += p.session_weekday_score_bias;
      p.subtype_risk_multiplier *= p.session_weekday_risk_multiplier;
      return true;
   }

   bool _ApplyPreAiSetupFloor(TradePlan &p, string &reason) {
      reason = "";
      _LoadActivePolicyIfNeeded();
      double default_floor = _DeterministicSetupScoreFloor(p);
      double soft_floor = (m_active_policy.soft_setup_floor > 0.0 ? m_active_policy.soft_setup_floor : MathMax(24.0, default_floor - 5.0));
      double hard_floor = (m_active_policy.hard_setup_floor > 0.0 ? m_active_policy.hard_setup_floor : MathMax(18.0, soft_floor - 6.0));
      double mult = (m_active_policy.setup_floor_penalty_mult > 0.0 ? m_active_policy.setup_floor_penalty_mult : 0.75);

      p.setup_floor_score = soft_floor;
      p.setup_floor_penalty = 0.0;
      p.setup_floor_action = "pass";

      if(p.setup_score < hard_floor){
         p.setup_floor_action = "reject";
         // Report the floor that actually bound.  Leaving soft_floor here made the
         // reject line unreadable: the score was compared against hard_floor but the
         // log showed the (higher) soft floor beside it.
         p.setup_floor_score = hard_floor;
         reason = "pre_ai_floor_hard";
         _Journal("[setup_floor_gate] symbol=" + p.symbol
                  + " branch=" + p.entry_branch
                  + " family=" + p.setup_family
                  + " setup_score=" + DoubleToString(p.setup_score, 2)
                  + " hard_floor=" + DoubleToString(hard_floor, 2)
                  + " soft_floor=" + DoubleToString(soft_floor, 2)
                  + " default_floor=" + DoubleToString(default_floor, 2)
                  + " floor_source=" + (m_active_policy.hard_setup_floor > 0.0 ? "active_policy" : "deterministic_default")
                  + " action=reject_hard");
         return false;
      }
      if(p.setup_score < soft_floor){
         p.setup_floor_penalty = (soft_floor - p.setup_score) * mult;
         p.setup_score -= p.setup_floor_penalty;
         p.setup_floor_action = (InpAiStrict ? "strict_reject" : "soft_penalty");
         if(InpAiStrict){
            reason = "pre_ai_floor_strict";
            return false;
         }
      }
      return true;
   }

   bool _OteSoftGate(TradePlan &p, string &reason) {
      reason = "ok";
      if(m_po3.CheckOTE(p)){
         p.ote_distance_frac = 0.0;
         p.ote_state = _OteState(p);
         return true;
      }
      p.ote_distance_frac = _OteDistanceFrac(p);
      p.ote_state = _OteState(p);
      double soft = (p.ote_softness_frac > 0.0 ? p.ote_softness_frac : InpOteSoftnessFrac);
      if(p.ote_distance_frac <= soft){
         reason = "ote_soft_loss";
         return true;
      }
      reason = "ote_softness_exceeded";
      return false;
   }

   bool _EntryBranchAllowed(const TradePlan &p, const string branch, string &reason) const {
      reason = "ok";
      if(branch == "breaker_retest"){
         if(_FamilyGroup(p) == "edge" && p.po3.structure_type == "micro_failed_breakout_reclaim") return true;
         if(!(p.po3.has_bos || p.po3.htf_mss || p.po3.htf_choch || p.po3.ltf_bos || p.po3.ltf_mss || p.po3.ltf_choch)){
            reason = "missing_breaker_structure";
            return false;
         }
         if(p.po3.bos_level <= 0){
            reason = "missing_bos_level";
            return false;
         }
         return true;
      }
      if(branch == "ote_inside_fvg"){
         double ote_low = 0.0, ote_high = 0.0;
         if(!_ResolveOteZone(p, ote_low, ote_high)){
            reason = "ote_unavailable";
            return false;
         }
         if(!_RangesOverlap(p.fvg.lower, p.fvg.upper, ote_low, ote_high)){
            reason = "ote_outside_fvg";
            return false;
         }
         return true;
      }
      if(branch == "nested_htf_ltf_fvg" || branch == "nested_fvg_edge"){
         if(p.fvg.nesting_score < 4.0 && p.fvg.htf_overlap_score < 4.0){
            reason = "missing_nested_htf_context";
            return false;
         }
         return true;
      }
      if(branch == "session_reentry"){
         if(p.po3.session_name == "OFF_HOURS"){
            reason = "off_hours_session_reentry";
            return false;
         }
         if(p.po3.session_high <= 0 || p.po3.session_low <= 0){
            reason = "missing_session_bounds";
            return false;
         }
         return true;
      }
      if(branch == "range_reentry"){
         if(p.po3.dr_high <= p.po3.dr_low){
            reason = "missing_dealing_range";
            return false;
         }
         return true;
      }
      if(branch == "continuation_reentry"){
         if(p.po3.structure_type == "micro_continuation") return true;
         if(!(p.fvg.continuation || _FvgExecutionClass(p) == "continuation_reentry" ||
              p.fvg.continuation_score >= p.fvg.reversal_score)){
            reason = "not_continuation_context";
            return false;
         }
         return true;
      }
      return true;
   }

   bool _ResolveBranchEntryPrice(const TradePlan &p, const string branch, double &entry_price, string &reason) const {
      reason = "ok";
      entry_price = 0.0;
      if(!_EntryBranchAllowed(p, branch, reason)) return false;

      if(branch == "breaker_retest"){
         entry_price = _ClampToRange(p.po3.bos_level, p.fvg.lower, p.fvg.upper);
      } else if(branch == "ote_inside_fvg"){
         double ote_low = 0.0, ote_high = 0.0;
         if(!_ResolveOteZone(p, ote_low, ote_high)){
            reason = "ote_unavailable";
            return false;
         }
         double lo = MathMax(p.fvg.lower, ote_low);
         double hi = MathMin(p.fvg.upper, ote_high);
         if(hi <= lo){
            reason = "ote_no_overlap";
            return false;
         }
         entry_price = (lo + hi) * 0.5;
      } else if(branch == "nested_htf_ltf_fvg"){
         entry_price = p.fvg.mid;
      } else if(branch == "nested_fvg_edge"){
         entry_price = (p.is_buy ? p.fvg.upper : p.fvg.lower);
      } else if(branch == "session_reentry"){
         double anchor = (p.is_buy ? p.po3.session_low : p.po3.session_high);
         entry_price = _ClampToRange(anchor, p.fvg.lower, p.fvg.upper);
      } else if(branch == "range_reentry"){
         double dr_range = p.po3.dr_high - p.po3.dr_low;
         double probe = (p.is_buy ? (p.po3.dr_low + dr_range * 0.35)
                                  : (p.po3.dr_high - dr_range * 0.35));
         entry_price = _ClampToRange(probe, p.fvg.lower, p.fvg.upper);
      } else if(branch == "continuation_reentry"){
         entry_price = (p.fvg.mid_mitigated ? p.fvg.mid : (p.is_buy ? p.fvg.upper : p.fvg.lower));
      } else if(branch == "fvg_mid"){
         entry_price = p.fvg.mid;
      } else {
         entry_price = (p.is_buy ? p.fvg.upper : p.fvg.lower);
      }

      if(entry_price <= 0){
         reason = "entry_price_invalid";
         return false;
      }
      return true;
   }

   bool _RejectPlacement(const TradePlan &p, const string reason) {
      m_last_execution_reject_reason = reason;
      if(StringLen(p.ai.decision_schema_version) > 0 && StringLen(p.candidate_hash) > 0)
         _WriteShadowDecisionUpdate(p, p.ai, "mql_execution_rejected", reason, false);
      _LogSetupReject(p.symbol, "execution", reason,
                      "entry=" + _FmtPrice(p.symbol, p.entry_est)
                      + " sl=" + _FmtPrice(p.symbol, p.sl)
                      + " tp2=" + _FmtPrice(p.symbol, p.tp2)
                      + " setup_score=" + DoubleToString(p.setup_score, 2)
                      + " rr2=" + DoubleToString(_ExecutionRR2(p), 2));
      _Journal(p.symbol + " entry skipped reject_code=" + _ReasonCode(reason) + " reason=" + reason);
      return false;
   }

   string _SnapshotSkippedMarker() const {
      return "__tester_skipped__";
   }

   string _SnapshotCaptureFailedMarker() const {
      return "__capture_failed__";
   }

   bool _HasUsableSnapshots(const TradePlan &p) const {
      if(StringLen(p.snapshot_htf_path) == 0 || StringLen(p.snapshot_ltf_path) == 0) return false;
      if(p.snapshot_htf_path == _SnapshotSkippedMarker() || p.snapshot_ltf_path == _SnapshotSkippedMarker()) return false;
      if(p.snapshot_htf_path == _SnapshotCaptureFailedMarker() || p.snapshot_ltf_path == _SnapshotCaptureFailedMarker()) return false;
      return true;
   }

   bool _NormalizePlanPO3State(TradePlan &p, const string context) {
      bool changed = false;
      if(p.po3.t_disp <= 0 && p.source_t_disp > 0){
         p.po3.t_disp = p.source_t_disp;
         _Journal("[po3_lineage] symbol=" + p.symbol
                  + " stage=" + context
                  + " field=t_disp action=restored_from_source_story"
                  + " value=" + IntegerToString((int)p.po3.t_disp));
      }
      if(p.po3.t_bos <= 0 && p.source_t_bos > 0){
         p.po3.t_bos = p.source_t_bos;
         _Journal("[po3_lineage] symbol=" + p.symbol
                  + " stage=" + context
                  + " field=t_bos action=restored_from_source_story"
                  + " value=" + IntegerToString((int)p.po3.t_bos));
      }
      if(StringLen(p.po3.po3_state) > 0)
         p.po3.state = PO3StateFromString(p.po3.po3_state);

      if(p.po3.state == PO3_IDLE && StringLen(p.po3.po3_state) == 0){
         PO3State inferred_state = PO3_IDLE;
         if(p.po3.has_sweep && p.po3.has_displacement && p.po3.has_bos) inferred_state = PO3_STRUCTURE_CONFIRMED;
         else if(p.po3.has_sweep && p.po3.has_displacement) inferred_state = PO3_DISPLACEMENT_CONFIRMED;
         else if(p.po3.has_sweep) inferred_state = PO3_SWEEP_CONFIRMED;
         else inferred_state = PO3_DEVELOPING;
         PO3SetState(p.po3, inferred_state, "legacy_state_inferred");
         changed = true;
      } else {
         p.po3.po3_state = PO3StateToString(p.po3.state);
      }

      if(p.po3.has_displacement && p.po3.t_disp <= 0){
         _Journal("[po3_lineage] symbol=" + p.symbol
                  + " stage=" + context
                  + " action=reject reason=has_displacement_missing_t_disp"
                  + " sweep=" + IntegerToString((int)p.po3.t_sweep)
                  + " disp=" + IntegerToString((int)p.po3.t_disp)
                  + " bos=" + IntegerToString((int)p.po3.t_bos));
         return false;
      }

      if(p.po3.has_bos && p.po3.t_bos <= 0){
         _Journal("[po3_lineage] symbol=" + p.symbol
                  + " stage=" + context
                  + " action=reject reason=has_bos_missing_t_bos"
                  + " sweep=" + IntegerToString((int)p.po3.t_sweep)
                  + " disp=" + IntegerToString((int)p.po3.t_disp)
                  + " bos=" + IntegerToString((int)p.po3.t_bos));
         return false;
      }

      bool requires_complete_sequence = _FamilyRequiresFullPO3Sequence(p);
      if(requires_complete_sequence && p.po3.has_displacement &&
         p.po3.t_sweep > 0 && p.po3.t_disp <= p.po3.t_sweep){
         _Journal("[po3_lineage] symbol=" + p.symbol
                  + " stage=" + context
                  + " action=reject reason=t_disp_not_after_t_sweep"
                  + " sweep=" + IntegerToString((int)p.po3.t_sweep)
                  + " disp=" + IntegerToString((int)p.po3.t_disp)
                  + " bos=" + IntegerToString((int)p.po3.t_bos));
         return false;
      }

      if(requires_complete_sequence && p.po3.has_bos &&
         p.po3.t_disp > 0 && p.po3.t_bos <= p.po3.t_disp){
         _Journal("[po3_lineage] symbol=" + p.symbol
                  + " stage=" + context
                  + " action=reject reason=t_bos_not_after_t_disp"
                  + " sweep=" + IntegerToString((int)p.po3.t_sweep)
                  + " disp=" + IntegerToString((int)p.po3.t_disp)
                  + " bos=" + IntegerToString((int)p.po3.t_bos));
         return false;
      }

      if(changed){
         _Journal(p.symbol + " " + context + " normalized invalid PO3 timestamps"
                  + " sweep=" + IntegerToString((int)p.po3.t_sweep)
                  + " disp=" + IntegerToString((int)p.po3.t_disp)
                  + " bos=" + IntegerToString((int)p.po3.t_bos));
      }

      if(InpRequireDisplacement && !p.po3.has_displacement){
         _Journal(p.symbol + " " + context + " dropped: displacement required but no valid t_disp remains");
         return false;
      }

      if(p.po3.state == PO3_CONFIRMED && !(p.po3.has_sweep && p.po3.has_displacement && p.po3.has_bos)){
         if(p.po3.has_sweep && p.po3.has_displacement)
            PO3SetState(p.po3, PO3_DISPLACEMENT_CONFIRMED, "missing_bos_after_normalize");
         else if(p.po3.has_sweep)
            PO3SetState(p.po3, PO3_SWEEP_CONFIRMED, "missing_displacement_after_normalize");
         else
            PO3SetState(p.po3, PO3_DEVELOPING, "missing_closed_sequence_after_normalize");
      }

      if(StringLen(p.po3.context_tier) == 0)
         p.po3.context_tier = (p.po3.has_bos ? "A" : (p.po3.has_displacement ? "B" : "C"));
      if(StringLen(p.po3.po3_scope) == 0) p.po3.po3_scope = PO3ScopeLabel(p.htf);
      _FreezeSourcePO3Story(p);

      return true;
   }

   string _SnapshotDirRel() const {
      return InpBusRoot + "\\snapshots";
   }

   string _SnapshotDirAbs() const {
      return TerminalInfoString(TERMINAL_COMMONDATA_PATH) + "\\Files\\" + InpBusRoot + "\\snapshots";
   }

   string _SnapshotLocalDirAbs() const {
      return TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + InpBusRoot + "\\snapshots";
   }

   string _SnapshotLocalAbs(const string fname) const {
      return _SnapshotLocalDirAbs() + "\\" + fname;
   }

   string _SnapshotRel(const string fname) const {
      return _SnapshotDirRel() + "\\" + fname;
   }

   string _SnapshotTesterRel(const string fname) const {
      return _SnapshotDirRel() + "\\" + fname;
   }

   string _SnapshotTesterAbs(const string fname) const {
      return _SnapshotLocalDirAbs() + "\\" + fname;
   }

   void _EnsureSnapshotDirs() const {
      FolderCreate(InpBusRoot);
      FolderCreate(_SnapshotDirRel());
      FolderCreate(InpBusRoot, FILE_COMMON);
      FolderCreate(_SnapshotDirRel(), FILE_COMMON);
   }

   void _JournalSnapshotError(const string symbol, const ENUM_TIMEFRAMES tf, const string stage, const int err, const string extra) const {
      string msg = symbol + " " + stage + " tf=" + IntegerToString((int)tf) + " err=" + IntegerToString(err);
      if(StringLen(extra) > 0) msg += " " + extra;
      _Journal(msg);
   }

   bool _CopyFileLocalToCommon(const string rel_path, int &out_err) const {
      out_err = 0;
      ResetLastError();
      int src = FileOpen(rel_path, FILE_READ|FILE_BIN|FILE_SHARE_READ|FILE_SHARE_WRITE);
      out_err = GetLastError();
      if(src == INVALID_HANDLE){
         _JournalSnapshotError("", PERIOD_CURRENT, "snapshot local FileOpen failed", out_err, "path=" + rel_path);
         return false;
      }

      int size = (int)FileSize(src);
      if(size <= 0){
         FileClose(src);
         return false;
      }

      uchar bytes[];
      ArrayResize(bytes, size);
      uint got = FileReadArray(src, bytes, 0, size);
      FileClose(src);
      if(got <= 0) return false;

      ResetLastError();
      int dst = FileOpen(rel_path, FILE_WRITE|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      out_err = GetLastError();
      if(dst == INVALID_HANDLE){
         _JournalSnapshotError("", PERIOD_CURRENT, "snapshot common FileOpen failed", out_err, "path=" + rel_path);
         return false;
      }
      uint wrote = FileWriteArray(dst, bytes, 0, got);
      FileClose(dst);
      return (wrote == got);
   }

   bool _WaitForSnapshotFileReady(const string rel_path, const string symbol, const ENUM_TIMEFRAMES tf) const {
      if(StringLen(rel_path) == 0) return false;
      int wait_ms = MathMax(1000, InpSnapshotReadyTimeoutMs);
      int attempts = MathMax(20, wait_ms / 100);
      int consecutive_successes = 0;
      int last_err = 0;
      int last_size = 0;
      for(int attempt=0; attempt<attempts; attempt++){
         ResetLastError();
         int h = FileOpen(rel_path, FILE_READ|FILE_BIN|FILE_SHARE_READ|FILE_SHARE_WRITE);
         last_err = GetLastError();
         if(h != INVALID_HANDLE){
            int size = (int)FileSize(h);
            last_size = size;
            FileClose(h);
            if(size > 0){
               consecutive_successes++;
               if(consecutive_successes >= 2) return true;
            } else {
               consecutive_successes = 0;
            }
         } else {
            consecutive_successes = 0;
         }
         Sleep(100);
      }
      _JournalSnapshotError(symbol, tf, "snapshot file not ready", last_err, "path=" + rel_path + " size=" + IntegerToString(last_size));
      return false;
   }

   bool _WaitForChartReady(const long chart_id, const string expected_symbol, const ENUM_TIMEFRAMES expected_tf, const string stage) const {
      if(chart_id == 0) return false;
      int wait_ms = MathMax(500, InpSnapshotChartReadyTimeoutMs);
      int attempts = MathMax(5, wait_ms / 100);
      for(int attempt=0; attempt<attempts; attempt++){
         string actual_symbol = ChartSymbol(chart_id);
         int actual_tf = (int)ChartPeriod(chart_id);
         int bars = Bars(expected_symbol, expected_tf);
         if(actual_symbol == expected_symbol && actual_tf == (int)expected_tf && bars > 100) return true;
         ChartRedraw(chart_id);
         Sleep(100);
      }
      string final_symbol = ChartSymbol(chart_id);
      int final_tf = (int)ChartPeriod(chart_id);
      int final_bars = Bars(expected_symbol, expected_tf);
      _Journal(expected_symbol + " " + stage
               + " tf=" + IntegerToString((int)expected_tf)
               + " chart_symbol=" + final_symbol
               + " chart_tf=" + IntegerToString(final_tf)
               + " bars=" + IntegerToString(final_bars));
      return false;
   }

   void _RestoreSnapshotChartState(const long chart_id, const string symbol, const ENUM_TIMEFRAMES tf) const {
      if(chart_id == 0) return;
      ResetLastError();
      if(!ChartSetSymbolPeriod(chart_id, symbol, tf)){
         int err = GetLastError();
         _JournalSnapshotError(symbol, tf, "snapshot restore switch failed", err, "");
         return;
      }
      int switch_err = GetLastError();
      if(switch_err != 0) _JournalSnapshotError(symbol, tf, "snapshot restore ChartSetSymbolPeriod warning", switch_err, "");
      _WaitForChartReady(chart_id, symbol, tf, "snapshot restore not ready");
      ChartRedraw(chart_id);
   }

   bool _CaptureChartSnapshotFile(const long chart_id, const string symbol, const ENUM_TIMEFRAMES tf, const string rel_path) const {
      if(chart_id == 0 || StringLen(rel_path) == 0) return false;
      ResetLastError();
      FileDelete(rel_path);
      for(int attempt=0; attempt<3; attempt++){
         ChartRedraw(chart_id);
         if(InpSnapshotDelayMs > 0) Sleep(InpSnapshotDelayMs);
         ResetLastError();
         bool ok = ChartScreenShot(chart_id, rel_path, InpSnapshotWidth, InpSnapshotHeight, ALIGN_RIGHT);
         int err = GetLastError();
         if(!ok){
            _JournalSnapshotError(symbol, tf, "snapshot screenshot failed", err, "attempt=" + IntegerToString(attempt+1) + " path=" + rel_path);
            Sleep(150);
            continue;
         }
         if(_WaitForSnapshotFileReady(rel_path, symbol, tf)) return true;
         Sleep(150);
      }
      return false;
   }

   bool _PublishSnapshotToCommon(const string rel_path, const string abs_common_path, const string symbol, const ENUM_TIMEFRAMES tf) const {
      if(StringLen(rel_path) == 0 || StringLen(abs_common_path) == 0) return false;
      _EnsureSnapshotDirs();
      FileDelete(rel_path, FILE_COMMON);
      int filecopy_err = 0;
      int fallback_err = 0;
      for(int attempt=0; attempt<8; attempt++){
         ResetLastError();
         if(FileCopy(rel_path, 0, rel_path, FILE_COMMON)) return true;
         filecopy_err = GetLastError();
         if(attempt == 0)
            _JournalSnapshotError(symbol, tf, "snapshot FileCopy failed", filecopy_err, "path=" + rel_path);
         if(_CopyFileLocalToCommon(rel_path, fallback_err)) return true;
         Sleep(50);
      }
      _JournalSnapshotError(symbol, tf, "snapshot publish failed", filecopy_err, "path=" + rel_path + " common=" + abs_common_path + " fallback_err=" + IntegerToString(fallback_err));
      return false;
   }

   string _CandidateSignature(const TradePlan &p) const {
      string sig = p.symbol + "|" + (p.is_buy ? "BUY" : "SELL");
      sig += "|" + IntegerToString((int)(p.source_t_sweep > 0 ? p.source_t_sweep : p.po3.t_sweep));
      sig += "|" + IntegerToString((int)(p.source_t_disp > 0 ? p.source_t_disp : p.po3.t_disp));
      sig += "|" + IntegerToString((int)(p.source_t_bos > 0 ? p.source_t_bos : p.po3.t_bos));
      sig += "|" + IntegerToString((int)p.fvg.t_form);
      sig += "|" + _PriceSignature(p.symbol, p.fvg.lower);
      sig += "|" + _PriceSignature(p.symbol, p.fvg.upper);
      sig += "|" + _PriceSignature(p.symbol, p.fvg.mid);
      sig += "|" + _PriceSignature(p.symbol, p.entry_est);
      sig += "|" + _PriceSignature(p.symbol, p.sl);
      sig += "|" + _PriceSignature(p.symbol, p.tp2);
      sig += "|" + p.entry_model;
      sig += "|" + p.po3.session_name;
      return sig;
   }

   string _GroupSignature(const TradePlan &plans[]) const {
      int count = ArraySize(plans);
      string sig = IntegerToString(count);
      for(int idx=0; idx<count; idx++){
         bool found = false;
         for(int i=0; i<count; i++){
            if(plans[i].candidate_index != idx) continue;
            sig += "#" + _CandidateSignature(plans[i]);
            found = true;
            break;
         }
         if(!found && idx < count) sig += "#" + _CandidateSignature(plans[idx]);
      }
      return sig;
   }

   string _TesterAiCacheSignature(const TradePlan &plans[]) const {
      string sig = AI_DECISION_SCHEMA_VERSION + "|" + AI_TARGET_ARBITRATION_SCHEMA_VERSION + "|" + AI_PROMPT_CONTRACT_VERSION + "|" + m_ai.DecisionHash() + "|" + _GroupSignature(plans);
      for(int i=0; i<ArraySize(plans); i++){
         sig += "|" + plans[i].candidate_hash;
         // assessed_execution_fingerprint deliberately NOT included.  It is
         // _ExecutionFingerprint, which mixes identity with four LIVE cost
         // measurements (spread_r, slippage_r, execution_cost_r,
         // net_reward_after_cost_r) rendered at 6 decimals.  Those are exactly the
         // values the execution contract tolerates drifting by max_cost_deterioration_r
         // (0.02) and re-checks with that tolerance in _EvaluateSemanticPlanMatch, so
         // hashing them into a key compared for EQUALITY is self-contradicting.
         // Measured on the 2026-09-05 week replay: 61 of the 159 tester_ai_cache_miss
         // rejections carried a signature byte-identical to a recorded one except for
         // this field, and the USDCAD 2026.08.05 20:04:59 APPROVE was lost because
         // net_reward_after_cost_r read 1.693652 when recorded and 1.693651 on replay
         // -- one unit in the sixth decimal of a diagnostic number.
         // Identity is already fully covered: candidate_hash above binds symbol,
         // direction, ids, taxonomy, branch, sweep/disp/bos times, entry/sl/tp1/tp2,
         // target source and model, obstacle kind/tf/price and the schema versions.
         sig += "|" + plans[i].setup_family;
         sig += "|" + plans[i].setup_class;
         sig += "|" + plans[i].setup_taxonomy_version;
         sig += "|" + plans[i].setup_taxonomy_enum;
         sig += "|" + plans[i].taxonomy_mapping_source;
         sig += "|" + plans[i].entry_branch;
         sig += "|" + plans[i].target_source;
         sig += "|" + plans[i].obstacle_kind;
         sig += "|" + DoubleToString(plans[i].liquidity_target_preserved, 8);
         sig += "|" + DoubleToString(plans[i].fallback_tp, 8);
         sig += "|" + DoubleToString(plans[i].capped_before_obstacle_tp, 8);
         sig += "|" + (plans[i].target_arbitration_required ? "1" : "0");
         sig += "|" + DoubleToString(plans[i].po3.context_score, 4);
         sig += "|" + DoubleToString(plans[i].po3.displacement_score, 4);
      }
      return sig;
   }

   string _TesterAiCacheKey(const string signature) const {
      uint hash = 2166136261;
      for(int i=0; i<StringLen(signature); i++){
         hash ^= (uint)StringGetCharacter(signature, i);
         hash *= 16777619;
      }
      string key = IntegerToString((int)hash);
      StringReplace(key, "-", "n");
      return key;
   }

   string _TesterAiCacheDir() const {
      return m_bus.LogDir() + "\\tester_ai_cache";
   }

   bool _TesterAiCacheHasFiles() const {
      string found = "";
      long handle = FileFindFirst(_TesterAiCacheDir() + "\\*.json", found, FILE_COMMON);
      if(handle == INVALID_HANDLE) return false;
      FileFindClose(handle);
      return true;
   }

   string _TesterAiCachePath(const string signature) const {
      return _TesterAiCacheDir() + "\\" + _TesterAiCacheKey(signature) + ".json";
   }

   // Component 4 of a cache signature is the decision_input_hash the artifact was
   // recorded under.  Layout:
   //   decision_schema|target_arbitration_schema|prompt_contract|decision_hash|group...
   string _SignatureDecisionHashComponent(const string signature) const {
      int start = 0;
      for(int field=0; field<3; field++){
         int pos = StringFind(signature, "|", start);
         if(pos < 0) return "";
         start = pos + 1;
      }
      int end = StringFind(signature, "|", start);
      if(end < 0) return "";
      return StringSubstr(signature, start, end - start);
   }

   // Samples the replay cache once and records which decision_input_hash values
   // its artifacts carry.  A run whose frozen hash is absent from that set can
   // never hit anything, and that is a startup fact, not something to rediscover
   // once per rejected setup.
   void _ProbeTesterCacheCohort() {
      if(m_tester_cache_cohort_probed) return;
      m_tester_cache_cohort_probed = true;
      m_tester_cache_cohort_summary = "none";
      m_tester_cache_cohort_dominant = "";
      m_tester_cache_cohort_matches = false;

      string mine = m_ai.DecisionHash();
      string hashes[]; int counts[];
      ArrayResize(hashes, 0);
      ArrayResize(counts, 0);
      string found = "";
      long handle = FileFindFirst(_TesterAiCacheDir() + "\\*.json", found, FILE_COMMON);
      if(handle == INVALID_HANDLE) return;
      int sampled = 0;
      int total = 0;
      do {
         string txt = "";
         if(!m_bus.ReadText(_TesterAiCacheDir() + "\\" + found, txt)) continue;
         string sig = "";
         if(!JsonGetStringStrict(txt, "cache_signature", sig)) continue;
         string h = _SignatureDecisionHashComponent(sig);
         if(StringLen(h) == 0) continue;
         total++;
         // Settled on every artifact, not on the sample: the match decides
         // whether the run is aborted, so it may not depend on where the cap
         // happened to fall.
         if(h == mine) m_tester_cache_cohort_matches = true;
         if(sampled >= 200){
            // Histogram is full.  Keep walking only while the match is still
            // unknown -- once it is settled either way there is nothing left
            // for this loop to learn.
            if(m_tester_cache_cohort_matches) break;
            continue;
         }
         sampled++;
         bool seen = false;
         for(int i=0; i<ArraySize(hashes); i++){
            if(hashes[i] == h){ counts[i]++; seen = true; break; }
         }
         if(!seen){
            int n = ArraySize(hashes);
            ArrayResize(hashes, n+1);
            ArrayResize(counts, n+1);
            hashes[n] = h;
            counts[n] = 1;
         }
      } while(FileFindNext(handle, found));
      FileFindClose(handle);

      m_tester_cache_cohort_total = total;
      m_tester_cache_cohort_sampled = sampled;
      if(sampled <= 0) return;
      string summary = "";
      int best = -1;
      for(int i=0; i<ArraySize(hashes); i++){
         if(StringLen(summary) > 0) summary += ",";
         summary += hashes[i] + "x" + IntegerToString(counts[i]);
         if(best < 0 || counts[i] > counts[best]) best = i;
      }
      m_tester_cache_cohort_summary = summary;
      if(best >= 0) m_tester_cache_cohort_dominant = hashes[best];
   }

   int _TesterCacheCohortTotal() {
      _ProbeTesterCacheCohort();
      return m_tester_cache_cohort_total;
   }

   int _TesterCacheCohortSampled() {
      _ProbeTesterCacheCohort();
      return m_tester_cache_cohort_sampled;
   }

   string _TesterCacheCohortDecisionHash() {
      _ProbeTesterCacheCohort();
      return (StringLen(m_tester_cache_cohort_dominant) > 0 ? m_tester_cache_cohort_dominant : "none");
   }

   bool _TesterCacheCohortMatches() {
      _ProbeTesterCacheCohort();
      return m_tester_cache_cohort_matches;
   }

   string _TesterCacheCohortSummary() {
      _ProbeTesterCacheCohort();
      return m_tester_cache_cohort_summary;
   }

   // Read-only views for const reporting paths.  They never trigger the probe,
   // so a value of "unprobed" honestly means the cohort was never sampled (a
   // live run) rather than that no artifacts exist.
   string _TesterCacheCohortSummaryCached() const {
      return (m_tester_cache_cohort_probed ? m_tester_cache_cohort_summary : "unprobed");
   }

   bool _TesterCacheCohortMatchesCached() const {
      return m_tester_cache_cohort_matches;
   }

   string _DecisionFieldAuthorityJson() const {
      string j = "{";
      j += JsonKVStr("schema_version", ARCHITECTURE_CONTRACT_VERSION) + ",";
      j += "\"candidate_entry_sl_tp\":{\"owner\":\"deterministic\",\"authority\":\"active\"},";
      j += "\"broker_feasibility\":{\"owner\":\"deterministic\",\"authority\":\"active\"},";
      j += "\"exact_risk_size\":{\"owner\":\"deterministic_portfolio\",\"authority\":\"active\"},";
      j += "\"calibrated_probability\":{\"owner\":\"statistical\",\"authority\":\"unavailable\"},";
      j += "\"expected_net_r\":{\"owner\":\"statistical\",\"authority\":\"unavailable\"},";
      j += "\"llm_quality_score\":{\"owner\":\"llm\",\"authority\":\"diagnostic_only_uncalibrated\"},";
      j += "\"llm_risk_assessments\":{\"owner\":\"llm\",\"authority\":\"diagnostic_only_uncalibrated\"},";
      j += "\"llm_qualitative_veto\":{\"owner\":\"llm\",\"authority\":\"evidence_backed_enumerated_veto\"},";
      j += "\"llm_narrative\":{\"owner\":\"llm\",\"authority\":\"diagnostic\"},";
      j += "\"python_final_allow\":{\"owner\":\"python_policy\",\"authority\":\"intermediate\"},";
      j += "\"mql_final_allow\":{\"owner\":\"mql_execution\",\"authority\":\"final\"},";
      j += "\"portfolio_exposure\":{\"owner\":\"portfolio\",\"authority\":\"active\"},";
      j += "\"management_action\":{\"owner\":\"management\",\"authority\":\"shadow_until_validated\"}";
      j += "}";
      return j;
   }

   string _AiDecisionJson(const string req_id, const string signature, const AiDecision &dec) const {
      string rejection_codes = dec.rejection_codes_json;
      if(StringLen(rejection_codes) == 0) rejection_codes = "[]";
      string invalidation_risks = dec.invalidation_risks_json;
      if(StringLen(invalidation_risks) == 0) invalidation_risks = "[]";
      string missing_confirmations = dec.missing_confirmations_json;
      if(StringLen(missing_confirmations) == 0) missing_confirmations = "[]";
      string candidate_assessments = dec.candidate_assessments_json;
      if(StringLen(candidate_assessments) == 0) candidate_assessments = "[]";
      string target_comparison = dec.target_comparison_json;
      if(StringLen(target_comparison) == 0) target_comparison = "{}";
      string field_authority = dec.decision_field_authority_json;
      if(StringLen(field_authority) <= 2) field_authority = _DecisionFieldAuthorityJson();
      string j = "{";
      j += JsonKVStr("id", req_id) + ",";
      j += JsonKVStr("cache_signature", signature) + ",";
      j += JsonKVStr("session_id", m_ai.SessionId()) + ",";
      j += JsonKVStr("request_nonce", m_ai.RequestNonce(req_id)) + ",";
      j += JsonKVStr("request_identity_version", dec.request_identity_version) + ",";
      j += JsonKVStr("request_identity_hash", dec.request_identity_hash) + ",";
      j += JsonKVStr("contract_manifest_hash", dec.contract_manifest_hash) + ",";
      j += JsonKVInt("request_created_sim_time", (int)dec.request_created_sim_time) + ",";
      j += "\"request_created_wall_time\":" + IntegerToString(dec.request_created_wall_time) + ",";
      j += JsonKVInt("candidate_count", dec.response_candidate_count) + ",";
      j += "\"ordered_candidate_identities\":"
           + (StringLen(dec.ordered_candidate_identities_json) > 2
              ? dec.ordered_candidate_identities_json : "[]") + ",";
      j += JsonKVStr("workload_mode", m_ai.WorkloadMode()) + ",";
      j += JsonKVStr("live_forward_contract_version", LIVE_FORWARD_CONTRACT_VERSION) + ",";
      j += JsonKVStr("behavior_contract_hash", m_ai.BehaviorContractHash()) + ",";
      j += JsonKVStr("architecture_contract_version", ARCHITECTURE_CONTRACT_VERSION) + ",";
      j += JsonKVStr("reasoning_configuration", dec.reasoning_configuration) + ",";
      j += JsonKVStr("bucket_prior_hash", dec.bucket_prior_hash) + ",";
      j += JsonKVStr("calibration_artifact_id", dec.calibration_artifact_id) + ",";
      j += JsonKVStr("response_binding_hash", m_ai.ResponseBindingHash(req_id, dec)) + ",";
      j += JsonKVStr("decision_schema_version", AI_DECISION_SCHEMA_VERSION) + ",";
      j += JsonKVStr("decision_quality_tier", "CACHE_OF_FULL_STRUCTURED") + ",";
      j += JsonKVStr("response_quality", "CACHE_OF_FULL_STRUCTURED") + ",";
      j += JsonKVStr("provider_contract_version", dec.provider_contract_version) + ",";
      j += JsonKVStr("provider_mode", dec.provider_mode) + ",";
      j += JsonKVStr("provider_id", dec.provider_id) + ",";
      j += JsonKVStr("endpoint_class", dec.endpoint_class) + ",";
      j += JsonKVStr("endpoint_identity_hash", dec.endpoint_identity_hash) + ",";
      j += JsonKVStr("configured_models_hash", dec.configured_models_hash) + ",";
      j += JsonKVStr("actual_model_id", dec.actual_model_id) + ",";
      j += JsonKVStr("fallback_model", dec.fallback_model) + ",";
      j += JsonKVStr("model_fingerprint", dec.model_fingerprint) + ",";
      j += JsonKVStr("evidence_envelope_version", dec.evidence_envelope_version) + ",";
      j += JsonKVStr("family_profile_version", dec.family_profile_version) + ",";
      j += JsonKVStr("memory_schema_version", dec.memory_schema_version) + ",";
      j += JsonKVStr("retrieval_policy_version", dec.retrieval_policy_version) + ",";
      j += JsonKVStr("role_contract_version", dec.role_contract_version) + ",";
      j += JsonKVStr("consensus_resolver_version", dec.consensus_resolver_version) + ",";
      j += JsonKVStr("generation_settings_hash", dec.generation_settings_hash) + ",";
      j += JsonKVStr("input_fingerprint", dec.input_fingerprint) + ",";
      j += "\"retrieved_analogue_ids\":" + (StringLen(dec.retrieved_analogue_ids_json) > 0 ? dec.retrieved_analogue_ids_json : "[]") + ",";
      j += JsonKVStr("historical_evidence_state", dec.historical_evidence_state) + ",";
      j += JsonKVStr("analyst_response_fingerprint", dec.analyst_response_fingerprint) + ",";
      j += JsonKVStr("critic_response_fingerprint", dec.critic_response_fingerprint) + ",";
      j += JsonKVStr("adjudicator_response_fingerprint", dec.adjudicator_response_fingerprint) + ",";
      j += JsonKVStr("final_resolver_reason", dec.final_resolver_reason) + ",";
      j += JsonKVStr("provider_health_state", dec.provider_health_state) + ",";
      j += "\"role_latencies\":" + (StringLen(dec.role_latencies_json) > 0 ? dec.role_latencies_json : "{}") + ",";
      j += "\"provider_retry_counts\":" + (StringLen(dec.provider_retry_counts_json) > 0 ? dec.provider_retry_counts_json : "{}") + ",";
      j += "\"provider_usage\":" + (StringLen(dec.provider_usage_json) > 0 ? dec.provider_usage_json : "{}") + ",";
      j += "\"estimated_context_tokens\":" + (dec.estimated_context_tokens_available ? DoubleToString(dec.estimated_context_tokens, 0) : "null") + ",";
      j += "\"unsupported_generation_parameters\":" + (StringLen(dec.unsupported_generation_parameters_json) > 0 ? dec.unsupported_generation_parameters_json : "[]") + ",";
      j += "\"analyst_output\":" + (StringLen(dec.analyst_output_json) > 0 ? dec.analyst_output_json : "{}") + ",";
      j += "\"critic_output\":" + (StringLen(dec.critic_output_json) > 0 ? dec.critic_output_json : "{}") + ",";
      j += "\"adjudicator_output\":" + (StringLen(dec.adjudicator_output_json) > 0 ? dec.adjudicator_output_json : "{}") + ",";
      j += JsonKVStr("request_fingerprint", dec.request_fingerprint) + ",";
      j += JsonKVStr("response_fingerprint", dec.response_fingerprint) + ",";
      j += JsonKVStr("full_structured_response_hash", dec.full_structured_response_hash) + ",";
      j += JsonKVStr("hierarchical_prior_artifact_hash", dec.hierarchical_prior_artifact_hash) + ",";
      j += JsonKVStr("hierarchical_prior_schema_version", dec.hierarchical_prior_schema_version) + ",";
      j += JsonKVStr("repeatability_schema_version", dec.repeatability_schema_version) + ",";
      j += JsonKVStr("repeatability_status", dec.repeatability_status) + ",";
      j += JsonKVBool("repeatability_required_live", dec.repeatability_required_live) + ",";
      j += JsonKVStr("repeatability_artifact_state", dec.repeatability_artifact_state) + ",";
      j += JsonKVStr("repeatability_rejection_code", dec.repeatability_rejection_code) + ",";
      j += JsonKVBool("repeatability_score_threshold_authority", dec.repeatability_score_threshold_authority) + ",";
      j += JsonKVBool("repeatability_trading_eligible", dec.repeatability_trading_eligible) + ",";
      j += JsonKVStr("repeatability_group_key", dec.repeatability_group_key) + ",";
      j += JsonKVStr("repeatability_authority_hash", dec.repeatability_authority_hash) + ",";
      j += JsonKVBool("mandatory_fields_complete", dec.mandatory_fields_complete) + ",";
      j += "\"decision_field_authority\":" + field_authority + ",";
      j += "\"missing_mandatory_fields\":" + (StringLen(dec.missing_mandatory_fields_json) > 0 ? dec.missing_mandatory_fields_json : "[]") + ",";
      j += "\"invalid_mandatory_fields\":" + (StringLen(dec.invalid_mandatory_fields_json) > 0 ? dec.invalid_mandatory_fields_json : "[]") + ",";
      j += JsonKVStr("decision_state", dec.decision_state) + ",";
      j += JsonKVStr("selected_candidate_id", dec.selected_candidate_id) + ",";
      j += JsonKVStr("selected_candidate_hash", dec.selected_candidate_hash) + ",";
      j += JsonKVStr("request_execution_fingerprint", dec.request_execution_fingerprint) + ",";
      j += JsonKVStr("assessed_execution_fingerprint", dec.assessed_execution_fingerprint) + ",";
      j += JsonKVStr("selected_target_identity", dec.selected_target_identity) + ",";
      j += JsonKVNum("selected_target_price", dec.selected_target_price, 8) + ",";
      j += JsonKVNum("assessed_entry", dec.assessed_entry, 8) + ",";
      j += JsonKVNum("assessed_sl", dec.assessed_sl, 8) + ",";
      j += JsonKVNum("assessed_tp1", dec.assessed_tp1, 8) + ",";
      j += JsonKVNum("assessed_tp2", dec.assessed_tp2, 8) + ",";
      j += "\"candidate_assessments\":" + candidate_assessments + ",";
      j += JsonKVBool("allow", dec.allow) + ",";
      j += JsonKVBool("raw_allow", dec.raw_allow) + ",";
      j += JsonKVBool("model_raw_allow", dec.model_raw_allow) + ",";
      j += JsonKVBool("python_final_allow", dec.python_final_allow) + ",";
      j += "\"mql_final_allow\":null,";
      j += JsonKVNum("rule_score", dec.rule_score, 6) + ",";
      j += JsonKVNum("llm_quality_score", dec.llm_quality_score, 6) + ",";
      j += JsonKVNum("blended_legacy_score", dec.blended_legacy_score, 6) + ",";
      j += JsonKVNum("legacy_agreement_confidence", dec.legacy_agreement_confidence, 6) + ",";
      j += JsonKVNum("llm_self_reported_confidence", dec.llm_self_reported_confidence, 6) + ",";
      j += "\"calibrated_win_probability\":null,";
      j += "\"expected_net_r\":null,";
      j += "\"oos_predicted_probability\":null,";
      j += JsonKVStr("calibration_bucket", dec.calibration_bucket) + ",";
      j += JsonKVInt("calibration_sample_size", dec.calibration_sample_size) + ",";
      j += "\"calibration_lower_bound\":null,";
      j += "\"calibration_upper_bound\":null,";
      j += JsonKVStr("calibration_model_version", dec.calibration_model_version) + ",";
      j += JsonKVStr("calibration_data_window_start", dec.calibration_data_window_start) + ",";
      j += JsonKVStr("calibration_data_window_end", dec.calibration_data_window_end) + ",";
      j += JsonKVBool("calibration_available", false) + ",";
      j += JsonKVNum("score", dec.llm_quality_score, 6) + ",";
      j += JsonKVInt("chosen_index", dec.chosen_index) + ",";
      j += JsonKVNum("confidence", dec.llm_self_reported_confidence, 6) + ",";
      j += JsonKVStr("decision_source", dec.decision_source) + ",";
      j += JsonKVStr("reasons", dec.reasons_json) + ",";
      j += JsonKVStr("decision_id", dec.decision_id) + ",";
      j += "\"rejection_codes\":" + rejection_codes + ",";
      j += JsonKVStr("narrative_state", dec.narrative_state) + ",";
      j += "\"invalidation_risks\":" + invalidation_risks + ",";
      j += "\"missing_confirmations\":" + missing_confirmations + ",";
      j += JsonKVNum("suggested_risk_multiplier", dec.suggested_risk_multiplier, 6) + ",";
      j += JsonKVStr("model_version", dec.model_version) + ",";
      j += JsonKVNum("structure_quality_score", dec.structure_quality_score, 6) + ",";
      j += JsonKVNum("entry_timing_score", dec.entry_timing_score, 6) + ",";
      j += JsonKVNum("follow_through_probability", dec.follow_through_probability, 6) + ",";
      j += JsonKVNum("invalidation_risk", dec.invalidation_risk, 6) + ",";
      j += JsonKVNum("chop_risk", dec.chop_risk, 6) + ",";
      j += JsonKVNum("cost_risk", dec.cost_risk, 6) + ",";
      j += JsonKVNum("symbol_bucket_risk", dec.symbol_bucket_risk, 6) + ",";
      j += JsonKVNum("session_bucket_risk", dec.session_bucket_risk, 6) + ",";
      j += JsonKVNum("post_entry_failure_risk", dec.post_entry_failure_risk, 6) + ",";
      j += JsonKVNum("final_trade_expectancy_score", dec.final_trade_expectancy_score, 6) + ",";
      j += JsonKVStr("llm_numeric_diagnostics_authority", dec.llm_numeric_diagnostics_authority) + ",";
      j += JsonKVBool("veto_enabled", dec.veto_enabled) + ",";
      j += JsonKVStr("veto_code", dec.veto_code) + ",";
      j += "\"veto_evidence_fields\":" + (StringLen(dec.veto_evidence_fields_json) > 0 ? dec.veto_evidence_fields_json : "[]") + ",";
      j += JsonKVStr("veto_reason", dec.veto_reason) + ",";
      j += "\"veto\":{" + JsonKVBool("enabled", dec.veto_enabled) + ","
           + JsonKVStr("code", dec.veto_code) + ","
           + "\"evidence_fields\":" + (StringLen(dec.veto_evidence_fields_json) > 0 ? dec.veto_evidence_fields_json : "[]") + ","
           + JsonKVStr("reason", dec.veto_reason) + "},";
      j += JsonKVStr("bucket_prior_override_justification", dec.bucket_prior_override_justification) + ",";
      j += "\"target_arbitration\":{";
      j += JsonKVStr("target_arbitration_schema_version", dec.target_arbitration_schema_version) + ",";
      j += JsonKVStr("prompt_contract_version", dec.prompt_contract_version) + ",";
      j += JsonKVBool("arbitration_required", dec.target_arbitration_required) + ",";
      j += JsonKVStr("chosen_target_model", dec.chosen_target_model) + ",";
      j += JsonKVNum("chosen_tp1", dec.chosen_tp1, 8) + ",";
      j += JsonKVNum("chosen_tp2", dec.chosen_tp2, 8) + ",";
      j += JsonKVNum("chosen_rr1", dec.chosen_rr1, 6) + ",";
      j += JsonKVNum("chosen_rr2", dec.chosen_rr2, 6) + ",";
      j += "\"rejected_target_models\":" + (StringLen(dec.rejected_target_models_json) > 0 ? dec.rejected_target_models_json : "[]") + ",";
      j += JsonKVStr("blocker_kind", dec.target_blocker_kind) + ",";
      j += JsonKVNum("blocker_severity", dec.target_blocker_severity, 6) + ",";
      j += JsonKVStr("blocker_class", dec.target_blocker_class) + ",";
      j += JsonKVBool("blocker_is_trade_killer", dec.target_blocker_is_trade_killer) + ",";
      j += JsonKVStr("why_not_liquidity_target", dec.why_not_liquidity_target) + ",";
      j += JsonKVStr("why_not_partial_before_obstacle", dec.why_not_partial_before_obstacle) + ",";
      j += JsonKVStr("why_not_capped_before_obstacle", dec.why_not_capped_before_obstacle) + ",";
      j += JsonKVStr("why_not_synthetic_fallback", dec.why_not_synthetic_fallback) + ",";
      j += JsonKVStr("target_decision_reason", dec.target_decision_reason) + ",";
      j += "\"target_comparison\":" + target_comparison;
      j += "},";
      j += JsonKVStr("chosen_target_model", dec.chosen_target_model) + ",";
      j += JsonKVNum("chosen_tp1", dec.chosen_tp1, 8) + ",";
      j += JsonKVNum("chosen_tp2", dec.chosen_tp2, 8) + ",";
      j += JsonKVNum("chosen_rr1", dec.chosen_rr1, 6) + ",";
      j += JsonKVNum("chosen_rr2", dec.chosen_rr2, 6) + ",";
      j += "\"rejected_target_models\":" + (StringLen(dec.rejected_target_models_json) > 0 ? dec.rejected_target_models_json : "[]") + ",";
      j += JsonKVStr("target_blocker_kind", dec.target_blocker_kind) + ",";
      j += JsonKVNum("target_blocker_severity", dec.target_blocker_severity, 6) + ",";
      j += JsonKVStr("target_blocker_class", dec.target_blocker_class) + ",";
      j += JsonKVBool("target_blocker_is_trade_killer", dec.target_blocker_is_trade_killer) + ",";
      j += JsonKVStr("target_decision_reason", dec.target_decision_reason) + ",";
      j += JsonKVBool("target_blocker_severity_present", dec.target_blocker_severity_present) + ",";
      j += JsonKVBool("target_blocker_class_present", dec.target_blocker_class_present) + ",";
      j += JsonKVBool("target_blocker_is_trade_killer_present", dec.target_blocker_is_trade_killer_present) + ",";
      j += JsonKVBool("target_decision_reason_present", dec.target_decision_reason_present) + ",";
      j += JsonKVStr("why_not_liquidity_target", dec.why_not_liquidity_target) + ",";
      j += JsonKVStr("why_not_partial_before_obstacle", dec.why_not_partial_before_obstacle) + ",";
      j += JsonKVStr("why_not_capped_before_obstacle", dec.why_not_capped_before_obstacle) + ",";
      j += JsonKVStr("why_not_synthetic_fallback", dec.why_not_synthetic_fallback) + ",";
      j += JsonKVStr("target_arbitration_schema_version", dec.target_arbitration_schema_version) + ",";
      j += JsonKVStr("prompt_contract_version", dec.prompt_contract_version) + ",";
      j += "\"target_comparison\":" + target_comparison;
      j += "}";
      return j;
   }

   int _FindTesterAiCache(const string signature) const {
      for(int i=0; i<ArraySize(m_tester_ai_cache_signatures); i++){
         if(m_tester_ai_cache_signatures[i] == signature) return i;
      }
      return -1;
   }

   void _PutTesterAiCacheMemory(const string signature, const AiDecision &dec) {
      int idx = _FindTesterAiCache(signature);
      if(idx < 0){
         idx = ArraySize(m_tester_ai_cache_signatures);
         ArrayResize(m_tester_ai_cache_signatures, idx + 1);
         ArrayResize(m_tester_ai_cache_decisions, idx + 1);
      }
      m_tester_ai_cache_signatures[idx] = signature;
      m_tester_ai_cache_decisions[idx] = dec;
   }

   bool _TesterRecordSignatureSeen(const string signature) const {
      if(StringLen(signature) == 0) return false;
      for(int i=0; i<ArraySize(m_tester_recorded_cache_signatures); i++){
         if(m_tester_recorded_cache_signatures[i] == signature) return true;
      }
      return false;
   }

   void _RememberTesterRecordSignature(const string signature) {
      if(StringLen(signature) == 0 || _TesterRecordSignatureSeen(signature)) return;
      int n = ArraySize(m_tester_recorded_cache_signatures);
      ArrayResize(m_tester_recorded_cache_signatures, n + 1);
      m_tester_recorded_cache_signatures[n] = signature;
   }

   bool _CacheBindingHashFromJson(const string txt, string &out_hash) const {
      out_hash = "";
      string id = "", session_id = "", request_nonce = "", request_identity_hash = "";
      string contract_manifest_hash = "";
      string decision_schema = "", quality_tier = "";
      string provider_mode = "", provider_id = "", actual_model_id = "", model_fingerprint = "";
      string generation_settings_hash = "", decision_state = "", selected_candidate_id = "";
      string selected_candidate_hash = "", assessed_fingerprint = "", selected_target_identity = "";
      bool model_raw_allow = false, python_final_allow = false;
      double selected_target_price = 0.0, llm_quality_score = 0.0, risk_multiplier = 0.0;
      if(!JsonGetStringStrict(txt, "id", id) ||
         !JsonGetStringStrict(txt, "session_id", session_id) ||
         !JsonGetStringStrict(txt, "request_nonce", request_nonce) ||
         !JsonGetStringStrict(txt, "request_identity_hash", request_identity_hash) ||
         !JsonGetStringStrict(txt, "contract_manifest_hash", contract_manifest_hash) ||
         !JsonGetStringStrict(txt, "decision_schema_version", decision_schema) ||
         !JsonGetStringStrict(txt, "decision_quality_tier", quality_tier) ||
         !JsonGetStringStrict(txt, "provider_mode", provider_mode) ||
         !JsonGetStringStrict(txt, "provider_id", provider_id) ||
         !JsonGetStringStrict(txt, "actual_model_id", actual_model_id) ||
         !JsonGetStringStrict(txt, "model_fingerprint", model_fingerprint) ||
         !JsonGetStringStrict(txt, "generation_settings_hash", generation_settings_hash) ||
         !JsonGetStringStrict(txt, "decision_state", decision_state) ||
         !JsonGetBoolStrict(txt, "model_raw_allow", model_raw_allow) ||
         !JsonGetBoolStrict(txt, "python_final_allow", python_final_allow) ||
         !JsonGetStringStrict(txt, "selected_candidate_id", selected_candidate_id) ||
         !JsonGetStringStrict(txt, "selected_candidate_hash", selected_candidate_hash) ||
         !JsonGetStringStrict(txt, "assessed_execution_fingerprint", assessed_fingerprint) ||
         !JsonGetStringStrict(txt, "selected_target_identity", selected_target_identity) ||
         !JsonGetNumberStrict(txt, "selected_target_price", selected_target_price) ||
         !JsonGetNumberStrict(txt, "llm_quality_score", llm_quality_score) ||
         !JsonGetNumberStrict(txt, "suggested_risk_multiplier", risk_multiplier)) return false;
      string material = id + "|" + session_id + "|" + request_nonce + "|"
                         + request_identity_hash + "|"
                         + contract_manifest_hash + "|"
                         + decision_schema + "|" + quality_tier + "|" + provider_mode + "|"
                        + provider_id + "|" + actual_model_id + "|" + model_fingerprint + "|"
                        + generation_settings_hash + "|" + decision_state + "|"
                        + (model_raw_allow ? "1" : "0") + "|" + (python_final_allow ? "1" : "0") + "|"
                        + selected_candidate_id + "|" + selected_candidate_hash + "|"
                        + assessed_fingerprint + "|" + selected_target_identity + "|"
                        + DoubleToString(selected_target_price, 8) + "|"
                        + DoubleToString(llm_quality_score, 6) + "|"
                        + DoubleToString(risk_multiplier, 6);
      out_hash = IntegerToString((int)(_IntegrityFnv1a(material) % 2147483647));
      return true;
   }

   bool _ReplaceTopLevelJsonStringField(string &txt, const string key, const string replacement) const {
      string current = "";
      if(!JsonGetStringStrict(txt, key, current)) return false;
      // Python's cache exporter emits `"key": "value"`, while MQL emits
      // compact `"key":"value"`. Some keys (notably session_id) are also
      // repeated inside nested fingerprint contracts. The top-level copy is
      // serialized first, so replace the first matching representation only.
      string compact = "\"" + key + "\":\"" + JsonEscape(current) + "\"";
      string spaced = "\"" + key + "\": \"" + JsonEscape(current) + "\"";
      int compact_pos = StringFind(txt, compact);
      int spaced_pos = StringFind(txt, spaced);
      bool use_spaced = (spaced_pos >= 0 && (compact_pos < 0 || spaced_pos < compact_pos));
      int first = (use_spaced ? spaced_pos : compact_pos);
      if(first < 0) return false;
      string token = (use_spaced ? spaced : compact);
      string next = "\"" + key + "\":" + (use_spaced ? " " : "")
                    + "\"" + JsonEscape(replacement) + "\"";
      txt = StringSubstr(txt, 0, first) + next + StringSubstr(txt, first + StringLen(token));
      return true;
   }

   bool _RebindTesterCacheResponse(string &txt, const string parse_id, string &reason) const {
      reason = "";
      string document_reason = "";
      if(!JsonValidateDocumentStrict(txt, document_reason)){
         reason = "cache_json_invalid:" + document_reason;
         return false;
      }
      string stored_hash = JsonGetString(txt, "response_binding_hash", "");
      string expected_hash = "";
      if(StringLen(stored_hash) == 0 || !_CacheBindingHashFromJson(txt, expected_hash) || stored_hash != expected_hash){
         reason = "cache_response_binding_mismatch";
         return false;
      }
      string cached_id = "";
      if(!JsonGetStringStrict(txt, "id", cached_id) || cached_id != parse_id){
         reason = "cache_request_identity_mismatch";
         return false;
      }
      if(!_ReplaceTopLevelJsonStringField(txt, "session_id", m_ai.SessionId()) ||
         !_ReplaceTopLevelJsonStringField(txt, "request_nonce", m_ai.RequestNonce(parse_id)) ||
         !_ReplaceTopLevelJsonStringField(txt, "workload_mode", m_ai.WorkloadMode()) ||
         !_ReplaceTopLevelJsonStringField(txt, "behavior_contract_hash", m_ai.BehaviorContractHash())){
         reason = "cache_transient_identity_rebind_failed";
         return false;
      }
      string rebound_hash = "";
      if(!_CacheBindingHashFromJson(txt, rebound_hash) ||
          !_ReplaceTopLevelJsonStringField(txt, "response_binding_hash", rebound_hash)){
         reason = "cache_response_binding_rebind_failed";
         return false;
      }
      return true;
   }

   bool _TryLoadTesterAiDecision(const string signature, AiDecision &out) {
      if(!MQLInfoInteger(MQL_TESTER) || !InpTesterAiCache || StringLen(signature) == 0) return false;
      int idx = _FindTesterAiCache(signature);
      if(idx >= 0){
         out = m_tester_ai_cache_decisions[idx];
         return (out.ok && out.mandatory_fields_complete &&
                 out.decision_schema_version == AI_DECISION_SCHEMA_VERSION &&
                 out.provider_contract_version == AI_PROVIDER_CONTRACT_VERSION &&
                 out.evidence_envelope_version == AI_EVIDENCE_ENVELOPE_VERSION &&
                 out.family_profile_version == AI_FAMILY_PROFILE_VERSION &&
                 out.memory_schema_version == AI_TRADE_MEMORY_SCHEMA_VERSION &&
                 out.retrieval_policy_version == AI_RETRIEVAL_POLICY_VERSION &&
                 out.role_contract_version == AI_ROLE_CONTRACT_VERSION &&
                 out.consensus_resolver_version == AI_CONSENSUS_RESOLVER_VERSION &&
                 (out.decision_quality_tier == "FULL_STRUCTURED" || out.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED"));
      }

      string txt;
      if(!m_bus.ReadText(_TesterAiCachePath(signature), txt)){
         // This was the silent path.  A key with no artifact behind it returned
         // false without a word, so a run addressing the wrong cohort looked
         // exactly like a run whose setups were genuinely never recorded.
         m_total_ai_cache_miss_no_artifact++;
         if(m_total_ai_cache_miss_no_artifact <= 20){
            _Journal("[ai_cache] hit=false reason=no_cache_artifact"
                     + " key=" + _TesterAiCacheKey(signature)
                     + " decision_input_hash=" + m_ai.DecisionHash()
                     + " cache_cohort=" + _TesterCacheCohortSummary()
                     + " cohort_match=" + (_TesterCacheCohortMatches() ? "true" : "false"));
         }
         return false;
      }
      string cached_signature = "";
      if(!JsonGetStringStrict(txt, "cache_signature", cached_signature) || cached_signature != signature){
         _Journal("[ai_cache] hit=false reason=cache_signature_mismatch");
         return false;
      }
      string cached_decision_schema = JsonGetString(txt, "decision_schema_version", "");
      string cached_schema = JsonGetString(txt, "target_arbitration_schema_version", "");
      string cached_prompt_contract = JsonGetString(txt, "prompt_contract_version", "");
      string cached_prior_schema = JsonGetString(txt, "hierarchical_prior_schema_version", "");
      string cached_repeatability_schema = JsonGetString(txt, "repeatability_schema_version", "");
      string cached_provider_contract = JsonGetString(txt, "provider_contract_version", "");
      string cached_evidence_version = JsonGetString(txt, "evidence_envelope_version", "");
      string cached_family_profile = JsonGetString(txt, "family_profile_version", "");
      string cached_memory_schema = JsonGetString(txt, "memory_schema_version", "");
      string cached_retrieval_policy = JsonGetString(txt, "retrieval_policy_version", "");
      string cached_role_contract = JsonGetString(txt, "role_contract_version", "");
      string cached_consensus_resolver = JsonGetString(txt, "consensus_resolver_version", "");
      if(cached_decision_schema != AI_DECISION_SCHEMA_VERSION ||
         cached_schema != AI_TARGET_ARBITRATION_SCHEMA_VERSION ||
         cached_prompt_contract != AI_PROMPT_CONTRACT_VERSION ||
         cached_prior_schema != HIERARCHICAL_PRIOR_SCHEMA_VERSION ||
         cached_repeatability_schema != REPEATABILITY_SCHEMA_VERSION ||
         cached_provider_contract != AI_PROVIDER_CONTRACT_VERSION ||
         cached_evidence_version != AI_EVIDENCE_ENVELOPE_VERSION ||
         cached_family_profile != AI_FAMILY_PROFILE_VERSION ||
         cached_memory_schema != AI_TRADE_MEMORY_SCHEMA_VERSION ||
         cached_retrieval_policy != AI_RETRIEVAL_POLICY_VERSION ||
         cached_role_contract != AI_ROLE_CONTRACT_VERSION ||
         cached_consensus_resolver != AI_CONSENSUS_RESOLVER_VERSION){
         m_funnel_ai_cache_miss_due_to_schema_version++;
         m_total_ai_cache_miss_due_to_schema_version++;
         m_total_ai_cache_misses++;
         _Journal("[ai_cache] hit=false reason=cache_miss_due_to_schema_version"
                  + " cached_decision_schema=" + (StringLen(cached_decision_schema) > 0 ? cached_decision_schema : "missing")
                  + " required_decision_schema=" + AI_DECISION_SCHEMA_VERSION
                  + " cached_schema=" + cached_schema
                  + " required_schema=" + AI_TARGET_ARBITRATION_SCHEMA_VERSION
                  + " cached_prompt_contract=" + cached_prompt_contract
                  + " required_prompt_contract=" + AI_PROMPT_CONTRACT_VERSION
                  + " cached_prior_schema=" + cached_prior_schema
                  + " required_prior_schema=" + HIERARCHICAL_PRIOR_SCHEMA_VERSION
                  + " cached_repeatability_schema=" + cached_repeatability_schema
                  + " required_repeatability_schema=" + REPEATABILITY_SCHEMA_VERSION
                  + " cached_provider_contract=" + cached_provider_contract
                  + " required_provider_contract=" + AI_PROVIDER_CONTRACT_VERSION
                  + " cached_evidence_version=" + cached_evidence_version
                  + " required_evidence_version=" + AI_EVIDENCE_ENVELOPE_VERSION
                  + " cached_family_profile=" + cached_family_profile
                  + " required_family_profile=" + AI_FAMILY_PROFILE_VERSION
                  + " cached_memory_schema=" + cached_memory_schema
                  + " required_memory_schema=" + AI_TRADE_MEMORY_SCHEMA_VERSION
                  + " cached_retrieval_policy=" + cached_retrieval_policy
                  + " required_retrieval_policy=" + AI_RETRIEVAL_POLICY_VERSION
                  + " cached_role_contract=" + cached_role_contract
                  + " required_role_contract=" + AI_ROLE_CONTRACT_VERSION
                  + " cached_consensus_resolver=" + cached_consensus_resolver
                  + " required_consensus_resolver=" + AI_CONSENSUS_RESOLVER_VERSION);
         return false;
      }

      string cached_id = JsonGetString(txt, "id", "");
      if(StringLen(cached_id) == 0) return false;
      // The request ID is part of the immutable AI request identity and every
      // candidate assessment echoes it. Rebind only transient bus-session
      // fields; synthesizing a new ID would detach the assessments.
      string parse_id = cached_id;
      string rebind_reason = "";
      if(!_RebindTesterCacheResponse(txt, parse_id, rebind_reason)){
         m_total_ai_cache_misses++;
         _Journal("[ai_cache] hit=false reason=" + rebind_reason
                  + " cache_signature=" + signature
                  + " original_id=" + cached_id);
         return false;
      }
      string parse_path = m_bus.RespDir() + "\\" + parse_id + ".json";
      if(!m_bus.WriteText(parse_path, txt)) return false;
      if(!m_ai.TryReadDecision(parse_id, out) || !out.ok || !out.mandatory_fields_complete ||
         out.decision_schema_version != AI_DECISION_SCHEMA_VERSION ||
         (out.decision_quality_tier != "FULL_STRUCTURED" && out.decision_quality_tier != "CACHE_OF_FULL_STRUCTURED") ||
         !_TesterCacheDecisionSourceLoadable(out.decision_source)){
         m_total_ai_cache_misses++;
         _Journal("[ai_cache] hit=false reason=strict_cached_decision_validation_failed");
         return false;
      }
      _PutTesterAiCacheMemory(signature, out);
      _Journal("[ai_cache] hit=true source=cache_of_full_structured signature=" + signature
               + " candidate_hash=" + out.selected_candidate_hash
               + " execution_fingerprint=" + out.assessed_execution_fingerprint);
      return true;
   }

   bool _RememberTesterAiDecision(const string signature, const AiDecision &dec) {
      if(!MQLInfoInteger(MQL_TESTER) || !InpTesterAiCache || !dec.ok || StringLen(signature) == 0) return false;
      if(_EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG){
         _Journal("[ai_cache] stored=false reason=live_wait_debug_not_replay_authoritative");
         return false;
      }
      if(!dec.mandatory_fields_complete || dec.decision_schema_version != AI_DECISION_SCHEMA_VERSION ||
         (dec.decision_quality_tier != "FULL_STRUCTURED" && dec.decision_quality_tier != "CACHE_OF_FULL_STRUCTURED") ||
         dec.provider_contract_version != AI_PROVIDER_CONTRACT_VERSION ||
         !AiProviderModeIsTradeable(dec.provider_mode) ||
         StringLen(dec.provider_id) == 0 || StringLen(dec.actual_model_id) == 0 ||
         StringLen(dec.model_fingerprint) == 0 || StringLen(dec.generation_settings_hash) == 0 ||
         dec.evidence_envelope_version != AI_EVIDENCE_ENVELOPE_VERSION ||
         dec.family_profile_version != AI_FAMILY_PROFILE_VERSION ||
         dec.memory_schema_version != AI_TRADE_MEMORY_SCHEMA_VERSION ||
         dec.retrieval_policy_version != AI_RETRIEVAL_POLICY_VERSION ||
         dec.role_contract_version != AI_ROLE_CONTRACT_VERSION ||
         dec.consensus_resolver_version != AI_CONSENSUS_RESOLVER_VERSION ||
         dec.hierarchical_prior_schema_version != HIERARCHICAL_PRIOR_SCHEMA_VERSION ||
         dec.repeatability_schema_version != REPEATABILITY_SCHEMA_VERSION ||
         dec.request_identity_version != AI_REQUEST_IDENTITY_VERSION ||
         StringLen(dec.request_identity_hash) == 0 ||
         dec.request_created_sim_time <= 0 || dec.request_created_wall_time <= 0 ||
         dec.response_candidate_count <= 0 || StringLen(dec.ordered_candidate_identities_json) <= 2 ||
         StringLen(dec.selected_candidate_hash) == 0 || StringLen(dec.assessed_execution_fingerprint) == 0 ||
         StringLen(dec.request_execution_fingerprint) == 0 ||
         StringLen(dec.decision_field_authority_json) <= 2 ||
         StringLen(dec.candidate_assessments_json) < 3 || dec.suggested_risk_multiplier < 0.0 || dec.suggested_risk_multiplier > 1.0){
         _Journal("[ai_cache] stored=false reason=incomplete_or_degraded_decision_contract");
         return false;
      }
      string source = dec.decision_source;
      StringToLower(source);
      if(StringFind(source, "error") >= 0 || StringFind(source, "fallback") >= 0) return false;
      _PutTesterAiCacheMemory(signature, dec);
      FolderCreate(_TesterAiCacheDir(), FILE_COMMON);
      string cache_path = _TesterAiCachePath(signature);
      // A replay hit is already an immutable cache artifact. Rewriting it after
      // consumption used to replace its real request ID with the literal
      // "cached", breaking all candidate-assessment bindings on the next run.
      if(_PathExists(cache_path)){
         _Journal("[ai_cache] stored=false reason=existing_cache_artifact_preserved signature=" + signature);
         return true;
      }
      string cache_request_id = dec.response_request_id;
      if(StringLen(cache_request_id) == 0){
         _Journal("[ai_cache] stored=false reason=missing_immutable_request_id signature=" + signature);
         return false;
      }
      m_bus.WriteText(cache_path, _AiDecisionJson(cache_request_id, signature, dec));
      return true;
   }

   bool _RestoreTesterRequestProvenance(TradePlan &plans[], const string signature, const AiDecision &dec) {
      string cache_text = "", provenance = "", snapshots = "";
      if(m_bus.ReadText(_TesterAiCachePath(signature), cache_text))
         provenance = JsonGetObject(cache_text, "replay_provenance", "");
      bool have_provenance = (StringLen(provenance) > 0);
      snapshots = JsonGetArray(provenance, "candidates", "[]");
      string recorded_request_id = JsonGetString(provenance, "request_id", "");
      if(have_provenance &&
         (JsonGetString(provenance, "schema", "") != "20260907_recorded_request_costs_v1" ||
          recorded_request_id != dec.response_request_id ||
          JsonArrayObjectCount(snapshots) <= 0)) return false;
      int assessment_count = JsonArrayObjectCount(dec.candidate_assessments_json);
      for(int a=0; a<assessment_count; a++){
         string assessment = "";
         if(!JsonArrayGetObject(dec.candidate_assessments_json, a, assessment)) return false;
         string candidate_hash = JsonGetString(assessment, "candidate_hash", "");
         string candidate_id = JsonGetString(assessment, "candidate_id", "");
         string recorded_fp = JsonGetString(assessment, "request_execution_fingerprint", "");
         int matched = -1, matches = 0;
         for(int i=0; i<ArraySize(plans); i++){
            if(plans[i].candidate_hash == candidate_hash && plans[i].candidate_id == candidate_id){
               matched = i; matches++;
            }
         }
         if(matches != 1 || StringLen(recorded_fp) == 0) return false;
         if(plans[matched].request_execution_fingerprint == recorded_fp) continue;
         if(!have_provenance){
            _Journal("[tester_replay_binding] valid=false reason=recorded_cost_provenance_missing req_id=" + dec.response_request_id);
            return false;
         }
         string snapshot = "";
         int snapshot_matches = 0;
         for(int s=0; s<JsonArrayObjectCount(snapshots); s++){
            string item = "";
            if(!JsonArrayGetObject(snapshots, s, item)) return false;
            if(JsonGetString(item, "candidate_hash", "") == candidate_hash &&
               JsonGetString(item, "candidate_id", "") == candidate_id &&
               JsonGetString(item, "request_execution_fingerprint", "") == recorded_fp){
               snapshot = item; snapshot_matches++;
            }
         }
         if(snapshot_matches != 1) return false;
         double spread=0, slippage=0, cost=0, net=0;
         if(!JsonGetNumberStrict(snapshot, "spread_r", spread) ||
            !JsonGetNumberStrict(snapshot, "slippage_r", slippage) ||
            !JsonGetNumberStrict(snapshot, "execution_cost_r", cost) ||
            !JsonGetNumberStrict(snapshot, "net_reward_after_cost_r", net) ||
            !MathIsValidNumber(spread) || !MathIsValidNumber(slippage) ||
            !MathIsValidNumber(cost) || !MathIsValidNumber(net) ||
            spread < 0.0 || slippage < 0.0 || cost < 0.0) return false;
         ExecutionAdjustmentContract contract;
         _BuildExecutionAdjustmentContract(plans[matched], contract);
         double tolerance = contract.max_cost_deterioration_r;
         // The static candidate hash is an exact match; only the cost component
         // of the recorded request fingerprint can differ. Validate those live
         // measurements with the same permission used at execution, then carry
         // the original request's fingerprint as provenance. Never replace the
         // live measurements or rewrite the approved response.
         if(MathAbs(plans[matched].spread_r-spread) > tolerance ||
            MathAbs(plans[matched].slippage_r-slippage) > tolerance ||
            MathAbs(plans[matched].execution_cost_r-cost) > tolerance ||
            MathAbs(plans[matched].net_reward_after_cost_r-net) > tolerance){
            _Journal("[tester_replay_binding] valid=false reason=recorded_cost_deterioration_exceeds_contract req_id=" + dec.response_request_id);
            return false;
         }
         _Journal("[tester_replay_binding] valid=true candidate_hash=" + candidate_hash
                  + " recorded_request_id=" + recorded_request_id
                  + " live_execution_fingerprint=" + plans[matched].request_execution_fingerprint
                  + " recorded_execution_fingerprint=" + recorded_fp
                  + " action=preserve_recorded_provenance_live_costs_revalidated");
         plans[matched].request_execution_fingerprint = recorded_fp;
         plans[matched].assessed_execution_fingerprint = recorded_fp;
         plans[matched].assessed_spread_r = spread;
         plans[matched].assessed_slippage_r = slippage;
         plans[matched].assessed_execution_cost_r = cost;
      }
      return true;
   }

   bool _QueueTesterCachedDecision(TradePlan &plans[], const string signature, const AiDecision &dec) {
      if(ArraySize(plans) <= 0) return false;
      if(!_RestoreTesterRequestProvenance(plans, signature, dec)) return false;
      string req_id = dec.response_request_id;
      if(StringLen(req_id) == 0){
         _Journal("[ai_cache] hit=false reason=missing_immutable_request_id signature=" + signature);
         return false;
      }
      string response_path = m_bus.RespDir() + "\\" + req_id + ".json";
      if(!m_bus.WriteText(response_path, _AiDecisionJson(req_id, signature, dec))) return false;

      int base = ArraySize(m_pending_ai);
      ArrayResize(m_pending_ai, base + ArraySize(plans));
      datetime sim_now = TimeCurrent();
      for(int i=0; i<ArraySize(plans); i++){
         plans[i].req_id = req_id;
         if(plans[i].setup_snapshot_time <= 0) plans[i].setup_snapshot_time = sim_now;
         plans[i].ai_request_time = sim_now;
         plans[i].ai_advisory_time = 0;
         plans[i].ai_result_age_sim_minutes = 0;
         plans[i].tester_ai_result_stale = false;
         plans[i].ai_requested_at = TimeLocal();
         plans[i].ai_requested_wall_ms = _WallClockMs();
         m_pending_ai[base + i] = plans[i];
      }
      m_funnel_ai_cache_hit++;
      m_total_ai_cache_hits++;
      _Journal(plans[0].symbol + " tester AI cache hit candidates=" + IntegerToString(ArraySize(plans))
               + " key=" + _TesterAiCacheKey(signature));
      _Journal("[ai_cache] hit=true req_id=" + req_id
               + " signature=" + signature
               + " sim_time=" + TimeToString(sim_now, TIME_DATE|TIME_MINUTES));
      return true;
   }

   int _CollectPendingGroupPlans(const string req_id, TradePlan &out[]) {
      ArrayResize(out, 0);
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(m_pending_ai[i].req_id != req_id) continue;
         int n = ArraySize(out);
         ArrayResize(out, n + 1);
         out[n] = m_pending_ai[i];
      }
      return ArraySize(out);
   }

   string _PendingGroupSignature(const string req_id, string &out_symbol) const {
      out_symbol = "";
      int candidate_count = 0;
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(m_pending_ai[i].req_id != req_id) continue;
         if(StringLen(out_symbol) == 0) out_symbol = m_pending_ai[i].symbol;
         candidate_count = MathMax(candidate_count, m_pending_ai[i].candidate_count);
      }
      if(candidate_count <= 0) candidate_count = 1;

      string sig = IntegerToString(candidate_count);
      bool any = false;
      for(int idx=0; idx<candidate_count; idx++){
         for(int i=0; i<ArraySize(m_pending_ai); i++){
            if(m_pending_ai[i].req_id != req_id || m_pending_ai[i].candidate_index != idx) continue;
            sig += "#" + _CandidateSignature(m_pending_ai[i]);
            any = true;
            break;
         }
      }
      if(any) return sig;

      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(m_pending_ai[i].req_id != req_id) continue;
         sig += "#" + _CandidateSignature(m_pending_ai[i]);
      }
      return sig;
   }

   int _FindAiCooldown(const string symbol, const string signature) const {
      for(int i=0; i<ArraySize(m_ai_cooldown_symbols); i++){
         if(m_ai_cooldown_symbols[i] == symbol && m_ai_cooldown_signatures[i] == signature) return i;
      }
      return -1;
   }

   bool _IsAiCooldownActive(const string symbol, const string signature, int &remaining_sec) {
      remaining_sec = 0;
      if(InpAiRetryBackoffMin <= 0 || StringLen(symbol) == 0 || StringLen(signature) == 0) return false;
      _PruneAiCooldowns();
      int idx = _FindAiCooldown(symbol, signature);
      if(idx < 0) return false;
      datetime now = TimeLocal();
      if(m_ai_cooldown_until[idx] <= now) return false;
      remaining_sec = (int)(m_ai_cooldown_until[idx] - now);
      return (remaining_sec > 0);
   }

   void _RememberAiCooldown(const string symbol, const string signature, const string reason) {
      if(InpAiRetryBackoffMin <= 0 || StringLen(symbol) == 0 || StringLen(signature) == 0) return;
      if(_InfrastructureAiRejection(reason)){
         _Journal("[ai_cooldown] skipped reason=" + _ReasonCode(reason) + " infrastructure_rejection=true");
         return;
      }
      _PruneAiCooldowns();
      datetime until = TimeLocal() + InpAiRetryBackoffMin * 60;
      int idx = _FindAiCooldown(symbol, signature);
      if(idx < 0){
         idx = ArraySize(m_ai_cooldown_symbols);
         ArrayResize(m_ai_cooldown_symbols, idx + 1);
         ArrayResize(m_ai_cooldown_signatures, idx + 1);
         ArrayResize(m_ai_cooldown_until, idx + 1);
      }
      m_ai_cooldown_symbols[idx] = symbol;
      m_ai_cooldown_signatures[idx] = signature;
      m_ai_cooldown_until[idx] = until;
      _Journal(symbol + " AI setup cooldown reason=" + reason
               + " until=" + TimeToString(until, TIME_MINUTES|TIME_SECONDS));
   }

   void _ArchiveBusArtifact(const string rel_path, const string terminal_state) {
      if(StringLen(rel_path) == 0) return;
      if(!_PathExists(rel_path)) return;
      m_bus.ArchiveTerminal(rel_path, terminal_state);
   }

   void _ArchivePendingArtifacts(const string req_id, const string terminal_state="stale") {
      if(StringLen(req_id) == 0) return;
      _ArchiveBusArtifact(m_bus.ReqDir() + "\\" + req_id + ".json", terminal_state);
      _ArchiveBusArtifact(m_bus.RespDir() + "\\" + req_id + ".json", terminal_state);
   }

   string _TesterSnapshotCacheKey(const string symbol, const ENUM_TIMEFRAMES tf) {
      datetime closed_bar = _LastClosedBarTime(symbol, tf);
      return symbol + "|" + IntegerToString((int)tf) + "|" + IntegerToString((int)closed_bar);
   }

   bool _TryTesterSnapshotCache(const string symbol, const ENUM_TIMEFRAMES tf, string &out_abs) {
      out_abs = "";
      if(!MQLInfoInteger(MQL_TESTER)) return false;
      string key = _TesterSnapshotCacheKey(symbol, tf);
      for(int i=0; i<ArraySize(m_tester_snapshot_cache_keys); i++){
         if(m_tester_snapshot_cache_keys[i] != key) continue;
         out_abs = m_tester_snapshot_cache_paths[i];
         return (StringLen(out_abs) > 0);
      }
      return false;
   }

   void _RememberTesterSnapshot(const string symbol, const ENUM_TIMEFRAMES tf, const string abs_path) {
      if(!MQLInfoInteger(MQL_TESTER) || StringLen(abs_path) == 0) return;
      string key = _TesterSnapshotCacheKey(symbol, tf);
      string prefix = symbol + "|" + IntegerToString((int)tf) + "|";
      for(int i=0; i<ArraySize(m_tester_snapshot_cache_keys); i++){
         if(StringFind(m_tester_snapshot_cache_keys[i], prefix) != 0) continue;
         m_tester_snapshot_cache_keys[i] = key;
         m_tester_snapshot_cache_paths[i] = abs_path;
         return;
      }
      int n = ArraySize(m_tester_snapshot_cache_keys);
      ArrayResize(m_tester_snapshot_cache_keys, n + 1);
      ArrayResize(m_tester_snapshot_cache_paths, n + 1);
      m_tester_snapshot_cache_keys[n] = key;
      m_tester_snapshot_cache_paths[n] = abs_path;
   }

   bool _CaptureSnapshot(const string symbol, const ENUM_TIMEFRAMES tf, const string suffix, string &out_abs) {
      out_abs = "";
      if(!InpUseSnapshotAI) return false;
      _EnsureSnapshotDirs();
      ResetLastError();
      long chart_id = ChartOpen(symbol, tf);
      int open_err = GetLastError();
      if(chart_id == 0){
         _JournalSnapshotError(symbol, tf, "snapshot open failed", open_err, "");
         return false;
      }
      if(open_err != 0) _JournalSnapshotError(symbol, tf, "snapshot ChartOpen warning", open_err, "");
      if(!_WaitForChartReady(chart_id, symbol, tf, "snapshot chart not ready")){
         ChartClose(chart_id);
         return false;
      }
      ChartRedraw(chart_id);
      string fname = symbol + "_" + IntegerToString((int)tf) + "_" + suffix + "_" + IntegerToString((int)TimeLocal()) + ".png";
      bool tester_mode = (bool)MQLInfoInteger(MQL_TESTER);
      string rel_path = (tester_mode ? _SnapshotTesterRel(fname) : _SnapshotRel(fname));
      string common_abs = _SnapshotDirAbs() + "\\" + fname;
      string local_abs = (tester_mode ? _SnapshotTesterAbs(fname) : _SnapshotLocalAbs(fname));
      bool ok = _CaptureChartSnapshotFile(chart_id, symbol, tf, rel_path);
      ChartClose(chart_id);
      if(!ok){
         out_abs = "";
         return false;
      }
      if(!_PublishSnapshotToCommon(rel_path, common_abs, symbol, tf)){
         out_abs = "";
         return false;
      }
      if(tester_mode){
         out_abs = common_abs;
         _Journal(symbol + " snapshot captured tf=" + IntegerToString((int)tf)
                  + " local=" + local_abs + " common=" + out_abs + " mode=tester_local_and_common");
         return true;
      }
      out_abs = common_abs;
      _Journal(symbol + " snapshot captured tf=" + IntegerToString((int)tf) + " path=" + out_abs);
      return true;
   }

   bool _CaptureSnapshotOnChart(const long chart_id, const string symbol, const ENUM_TIMEFRAMES tf, const string suffix, string &out_abs) {
      out_abs = "";
      if(!InpUseSnapshotAI || chart_id == 0) return false;
      if(_TryTesterSnapshotCache(symbol, tf, out_abs)) return true;

      _EnsureSnapshotDirs();

      string original_symbol = ChartSymbol(chart_id);
      ENUM_TIMEFRAMES original_tf = (ENUM_TIMEFRAMES)ChartPeriod(chart_id);
      bool switched = (original_symbol != symbol || original_tf != tf);
      if(switched){
         ResetLastError();
         if(!ChartSetSymbolPeriod(chart_id, symbol, tf)){
            int err = GetLastError();
            _JournalSnapshotError(symbol, tf, "tester snapshot switch failed", err, "");
            return false;
         }
         int switch_err = GetLastError();
         if(switch_err != 0) _JournalSnapshotError(symbol, tf, "tester snapshot ChartSetSymbolPeriod warning", switch_err, "");
      }

      if(!_WaitForChartReady(chart_id, symbol, tf, "tester snapshot chart not ready")){
         if(switched) _RestoreSnapshotChartState(chart_id, original_symbol, original_tf);
         return false;
      }

      ChartRedraw(chart_id);

      string fname = symbol + "_" + IntegerToString((int)tf) + "_" + suffix + "_" + IntegerToString((int)TimeLocal()) + ".png";
      bool tester_mode = (bool)MQLInfoInteger(MQL_TESTER);
      string rel_path = (tester_mode ? _SnapshotTesterRel(fname) : _SnapshotRel(fname));
      string common_abs = _SnapshotDirAbs() + "\\" + fname;
      string local_abs = (tester_mode ? _SnapshotTesterAbs(fname) : _SnapshotLocalAbs(fname));
      bool ok = _CaptureChartSnapshotFile(chart_id, symbol, tf, rel_path);

      if(switched) _RestoreSnapshotChartState(chart_id, original_symbol, original_tf);

      if(!ok){
         out_abs = "";
         return false;
      }

      if(!_PublishSnapshotToCommon(rel_path, common_abs, symbol, tf)){
         out_abs = "";
         return false;
      }

      out_abs = common_abs;
      _RememberTesterSnapshot(symbol, tf, out_abs);
      if(tester_mode){
         _Journal(symbol + " tester snapshot captured tf=" + IntegerToString((int)tf)
                  + " local=" + local_abs + " common=" + out_abs + " mode=tester_local_and_common");
      } else {
         _Journal(symbol + " tester snapshot captured tf=" + IntegerToString((int)tf) + " path=" + out_abs);
      }
      return true;
   }

   bool _CaptureSnapshotsForPlans(TradePlan &plans[]) {
      if(ArraySize(plans) <= 0) return true;
      // Always initialize snapshot paths, even if snaps are disabled
      if(!InpUseSnapshotAI){
         for(int i=0; i<ArraySize(plans); i++){
            plans[i].snapshot_htf_path = "";
            plans[i].snapshot_ltf_path = "";
         }
         return true;
      }
      if(MQLInfoInteger(MQL_TESTER)){
         string htf_path = "";
         string ltf_path = "";
         bool ok_htf = _CaptureSnapshotOnChart(ChartID(), plans[0].symbol, plans[0].htf, "htf", htf_path);
         bool ok_ltf = _CaptureSnapshotOnChart(ChartID(), plans[0].symbol, plans[0].ltf, "ltf", ltf_path);
         if(!ok_htf) htf_path = _SnapshotCaptureFailedMarker();
         if(!ok_ltf) ltf_path = _SnapshotCaptureFailedMarker();
         for(int i=0; i<ArraySize(plans); i++){
            plans[i].snapshot_htf_path = htf_path;
            plans[i].snapshot_ltf_path = ltf_path;
         }
         if(InpRequireSnapshots && !(ok_htf && ok_ltf)){
            _Journal(plans[0].symbol + " snapshot requirement failed in tester");
            return false;
         }
         return true;
      }
      string htf_path = "";
      string ltf_path = "";
      bool ok_htf = _CaptureSnapshot(plans[0].symbol, plans[0].htf, "htf", htf_path);
      bool ok_ltf = _CaptureSnapshot(plans[0].symbol, plans[0].ltf, "ltf", ltf_path);
      for(int i=0; i<ArraySize(plans); i++){
         plans[i].snapshot_htf_path = htf_path;
         plans[i].snapshot_ltf_path = ltf_path;
      }
      if(InpRequireSnapshots && !(ok_htf && ok_ltf)){
         _Journal(plans[0].symbol + " snapshot requirement failed");
         return false;
      }
      return true;
   }

   string _MakeTradeKey(const TradePlan &p) {
      if(StringLen(p.trade_key) > 0) return p.trade_key;
      return IntegerToString((int)TimeLocal()) + "_" + IntegerToString((int)MathRand()) + "_" + IntegerToString(p.candidate_index);
   }

   bool _PlanTooOld(const TradePlan &p, const int max_minutes) {
      int limit = max_minutes;
      if(p.max_watch_minutes > 0) limit = p.max_watch_minutes;
      if(limit <= 0) return false;
      if(p.created_at <= 0) return false;
      return ((TimeLocal() - p.created_at) > (limit * 60));
   }

   bool _PendingAiTimedOut(const TradePlan &p) const {
      int timeout_min = _PendingAiTimeoutMinutes();
      if(timeout_min <= 0) return false;
      if(MQLInfoInteger(MQL_TESTER) && p.ai_requested_wall_ms > 0){
         return (_WallElapsedMs(p.ai_requested_wall_ms) >= (ulong)(timeout_min * 60 * 1000));
      }
      if(p.ai_requested_at <= 0) return false;
      return ((TimeLocal() - p.ai_requested_at) >= (timeout_min * 60));
   }

   int _PendingAIRequestCountInternal() const {
      string req_ids[];
      ArrayResize(req_ids, 0);
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         string req_id = m_pending_ai[i].req_id;
         if(StringLen(req_id) == 0 || _HasStringValue(req_ids, req_id)) continue;
         int n = ArraySize(req_ids);
         ArrayResize(req_ids, n+1);
         req_ids[n] = req_id;
      }
      return ArraySize(req_ids);
   }

   void _PrunePlanArray(TradePlan &arr[], const bool pending_ai) {
      for(int i=ArraySize(arr)-1; i>=0; i--){
         bool stale = false;
         string reason = "";
         if(!_NormalizePlanPO3State(arr[i], pending_ai ? "pending_ai" : "watchlist")){
            stale = true;
            reason = "invalid_po3_state";
         } else if(arr[i].created_at <= 0){
            stale = true;
            reason = "missing_created_at";
         } else if(!pending_ai && _PlanTooOld(arr[i], _EffectiveWatchlistMaxMinutes(arr[i].htf))) {
            stale = true;
            reason = "max_age_minutes";
         }
         if(pending_ai && (StringLen(arr[i].req_id) == 0 || arr[i].ai_requested_at <= 0)){
            stale = true;
            reason = "missing_ai_request_state";
         }
         if(pending_ai && _PendingAiTimedOut(arr[i])){
            stale = true;
            reason = "ai_timeout";
         }
         if(!stale && InpUseAI){
            bool full_structured = (arr[i].ai.decision_quality_tier == "FULL_STRUCTURED" ||
                                    arr[i].ai.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
            if((!pending_ai && (!arr[i].ai.ok || !arr[i].ai.mandatory_fields_complete ||
                                arr[i].ai.decision_schema_version != AI_DECISION_SCHEMA_VERSION ||
                                !full_structured)) ||
               (pending_ai && (StringLen(arr[i].candidate_id) == 0 ||
                               StringLen(arr[i].candidate_hash) == 0 ||
                               StringLen(arr[i].request_execution_fingerprint) == 0))){
               stale = true;
               reason = "legacy_or_incomplete_ai_contract_non_trading";
            }
         }
         if(!stale) continue;
         _Journal(arr[i].symbol + (pending_ai ? " pending AI dropped: " : " watchlist dropped: ") + reason);
         int last = ArraySize(arr) - 1;
         arr[i] = arr[last];
         ArrayResize(arr, last);
      }
   }

   void _PersistPenaltyStates() {
      PenaltyState states[];
      m_penalty.SnapshotStates(states);
      m_state.SavePenaltyStates(m_state.PenaltyPath(), states);
      m_last_penalty_persist = TimeLocal();
   }

   bool _HasSymbolInPlans(const TradePlan &plans[], const string symbol) {
      for(int i=0; i<ArraySize(plans); i++){
         if(plans[i].symbol == symbol) return true;
      }
      return false;
   }

   bool _HasOpenPositionWithKey(const string key) {
      if(StringLen(key) == 0) return false;
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         if(PositionGetString(POSITION_COMMENT) == key) return true;
      }
      return false;
   }

   bool _PlanHasSweepKey(const TradePlan &p) const {
      return (StringLen(p.symbol) > 0 && p.po3.t_sweep > 0);
   }

   string _SweepKey(const TradePlan &p) const {
      if(!_PlanHasSweepKey(p)) return "";
      return p.symbol + "|" + IntegerToString((int)p.po3.t_sweep);
   }

   bool _SweepAlreadyConsumed(const TradePlan &p) const {
      string key = _SweepKey(p);
      if(StringLen(key) == 0) return false;
      return _HasStringValue(m_consumed_sweep_keys, key);
   }

   void _RememberConsumedSweep(const TradePlan &p) {
      string key = _SweepKey(p);
      if(StringLen(key) == 0 || _HasStringValue(m_consumed_sweep_keys, key)) return;
      int n = ArraySize(m_consumed_sweep_keys);
      ArrayResize(m_consumed_sweep_keys, n + 1);
      m_consumed_sweep_keys[n] = key;
      _Journal(p.symbol + " sweep marked consumed after filled entry sweep="
               + TimeToString(p.po3.t_sweep, TIME_DATE|TIME_MINUTES));
   }

   bool _SameSweep(const TradePlan &a, const TradePlan &b) const {
      if(!_PlanHasSweepKey(a) || !_PlanHasSweepKey(b)) return false;
      return (a.symbol == b.symbol && a.po3.t_sweep == b.po3.t_sweep);
   }

   int _CountSweepPlans(const TradePlan &plans[], const TradePlan &p, const string exclude_trade_key="") const {
      if(InpMaxTradesPerSweep <= 0 || !_PlanHasSweepKey(p)) return 0;
      int count = 0;
      for(int i=0; i<ArraySize(plans); i++){
         if(!_SameSweep(plans[i], p)) continue;
         if(StringLen(exclude_trade_key) > 0 && plans[i].trade_key == exclude_trade_key) continue;
         count++;
      }
      return count;
   }

   int _CountSweepLiveExposure(const TradePlan &p) {
      if(InpMaxTradesPerSweep <= 0 || !_PlanHasSweepKey(p)) return 0;
      int count = 0;
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         string sym = PositionGetString(POSITION_SYMBOL);
         if(sym != p.symbol) continue;
         string comment = PositionGetString(POSITION_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(!_NormalizePlanPO3State(meta, "sweep_position")) continue;
         if(_SameSweep(meta, p)) count++;
      }
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         string sym = OrderGetString(ORDER_SYMBOL);
         if(sym != p.symbol) continue;
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(!_NormalizePlanPO3State(meta, "sweep_order")) continue;
         if(_SameSweep(meta, p)) count++;
      }
      return count;
   }

   bool _SweepTradeCapReached(const TradePlan &p, const bool include_watchlist, string &reason) {
      reason = "";
      if(InpMaxTradesPerSweep <= 0 || !_PlanHasSweepKey(p)) return false;
      if(InpMaxTradesPerSweep == 1 && _SweepAlreadyConsumed(p)){
         reason = "sweep already consumed by a filled trade sweep="
                  + TimeToString(p.po3.t_sweep, TIME_DATE|TIME_MINUTES);
         return true;
      }
      int count = _CountSweepLiveExposure(p);
      if(include_watchlist) count += _CountSweepPlans(m_watchlist, p, p.trade_key);
      if(count < InpMaxTradesPerSweep) return false;
      reason = "sweep trade cap reached current=" + IntegerToString(count)
               + " max=" + IntegerToString(InpMaxTradesPerSweep)
               + " sweep=" + TimeToString(p.po3.t_sweep, TIME_DATE|TIME_MINUTES);
      return true;
   }

   double _RiskDistanceForMeta(const TradePlan &p) {
      double entry = p.filled_entry;
      if(entry <= 0) entry = p.planned_entry;
      if(entry <= 0) entry = p.entry_est;
      double sl = p.planned_sl;
      if(sl <= 0) sl = p.sl;
      if(entry <= 0 || sl <= 0) return 0.0;
      return MathAbs(entry - sl);
   }

   string _Po3Subtype(const TradePlan &p) {
      string out = (StringLen(p.po3.po3_scope) > 0 ? p.po3.po3_scope : PO3ScopeLabel(p.htf));
      out += (p.po3.sweep_running ? "+running_sweep" : "+closed_sweep");
      if(p.po3.t_disp > 0) out += "+disp";
      if(p.po3.t_bos > 0) out += "+bos";
      if(StringLen(p.po3.context_tier) > 0) out += "+tier_" + p.po3.context_tier;
      if(p.po3.htf_mss || p.po3.htf_choch) out += "+htf_shift";
      if(p.po3.ltf_bos || p.po3.ltf_mss || p.po3.ltf_choch) out += "+ltf_shift";
      return out;
   }

   string _FvgSubtype(const TradePlan &p) {
      string out = p.entry_model;
      if(StringLen(out) == 0) out = "fvg";
      if(StringLen(p.fvg_execution_class) > 0) out += "+" + p.fvg_execution_class;
      if(p.fvg.htf_overlap_score >= 6.0) out += "+htf_overlap";
      if(p.fvg.nesting_score >= 6.0) out += "+nested";
      if(p.fvg.origin_score >= 6.0) out += "+strong_origin";
      if(p.fvg.retest_score >= 6.0) out += "+retested";
      return out;
   }

   string _RegimeBucket(const TradePlan &p) {
      if(p.news_risk >= 1.0) return "news_shock";
      if(p.po3.session_name == "OFF_HOURS") return "off_hours";
      if(p.expansion_score > 0 && p.expansion_score < InpExpansionRatioMin) return "compression";
      if(p.expansion_score > InpExpansionRatioMax) return "shock_expansion";
      if(p.adx_value >= InpAdxMin && p.session_vol_ratio >= InpSessionVolMinAdrFrac){
         if((p.is_buy && p.trend_slope_pct > 0) || (!p.is_buy && p.trend_slope_pct < 0))
            return "directional_expansion";
      }
      if(p.trend_strength < InpTrendStrengthMin) return "weak_trend";
      return "balanced_trend";
   }

   string _DealReasonLabel(const long reason) {
      switch((int)reason){
         case DEAL_REASON_SL: return "stop_loss";
         case DEAL_REASON_TP: return "take_profit";
         case DEAL_REASON_SO: return "stop_out";
         case DEAL_REASON_EXPERT: return "expert_exit";
         case DEAL_REASON_CLIENT: return "manual_terminal";
         case DEAL_REASON_MOBILE: return "manual_mobile";
         case DEAL_REASON_WEB: return "manual_web";
         case DEAL_REASON_ROLLOVER: return "rollover";
         case DEAL_REASON_VMARGIN: return "variation_margin";
         case DEAL_REASON_SPLIT: return "split";
      }
      return "unknown";
   }

   string _DealEntryLabel(const ENUM_DEAL_ENTRY entry_kind) const {
      switch((int)entry_kind){
         case DEAL_ENTRY_IN: return "in";
         case DEAL_ENTRY_OUT: return "out";
         case DEAL_ENTRY_INOUT: return "inout";
         case DEAL_ENTRY_OUT_BY: return "out_by";
      }
      return "unknown";
   }

   string _ExitPathLabel(const TradePlan &meta, const double exit_price, const int out_count, const long final_reason) {
      double point = SymbolInfoDouble(meta.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double risk_dist = _RiskDistanceForMeta(meta);
      double tol = MathMax(point * 8.0, risk_dist * 0.08);

      string base;
      double sl = (meta.planned_sl > 0 ? meta.planned_sl : meta.sl);
      double tp2 = (meta.planned_tp2 > 0 ? meta.planned_tp2 : meta.tp2);
      double tp1 = (meta.planned_tp1 > 0 ? meta.planned_tp1 : meta.tp1);
      if(sl > 0 && MathAbs(exit_price - sl) <= tol) base = "stop_loss";
      else if(tp2 > 0 && MathAbs(exit_price - tp2) <= tol) base = "tp2";
      else if(tp1 > 0 && MathAbs(exit_price - tp1) <= tol) base = "tp1";
      else base = _DealReasonLabel(final_reason);

      if(meta.tp1_done || out_count > 1) return "partial_then_" + base;
      return base;
   }

   bool _ComputeOriginalPolicyCounterfactual(TradePlan &meta, string &reason) {
      reason = "";
      meta.actual_managed_result = meta.broker_net_pnl;
      meta.actual_managed_result_r = meta.result_r_initial_risk;
      meta.counterfactual_original_sl_tp_result = 0.0;
      meta.counterfactual_original_sl_tp_result_r = 0.0;
      meta.counterfactual_target_before_stop_available = false;
      meta.counterfactual_target_before_stop = false;
      meta.management_alpha = 0.0;
      meta.counterfactual_ambiguous = true;
      meta.counterfactual_pending = false;
      meta.counterfactual_status = "EVALUATING";
      meta.counterfactual_resolution_reason = "";
      meta.counterfactual_evaluated_at = 0;
      meta.management_policy_selection_eligible = false;

      double entry = (meta.original_entry_for_risk > 0.0 ? meta.original_entry_for_risk
                       : (meta.filled_entry > 0.0 ? meta.filled_entry : meta.planned_entry));
      double sl = (meta.original_sl_for_risk > 0.0 ? meta.original_sl_for_risk
                    : (meta.planned_sl > 0.0 ? meta.planned_sl : meta.sl));
      double tp1 = (meta.planned_tp1 > 0.0 ? meta.planned_tp1 : meta.tp1);
      double tp2 = (meta.planned_tp2 > 0.0 ? meta.planned_tp2 : meta.tp2);
      double risk_dist = MathAbs(entry - sl);
      if(entry <= 0.0 || sl <= 0.0 || tp2 <= 0.0 || risk_dist <= 0.0 || meta.initial_risk_money <= 0.0){
         reason = "counterfactual_original_contract_incomplete";
         return false;
      }
      if((meta.is_buy && (sl >= entry || tp2 <= entry)) ||
         (!meta.is_buy && (sl <= entry || tp2 >= entry))){
         reason = "counterfactual_original_direction_invalid";
         return false;
      }

      datetime start = _MetaOpenedAt(meta);
      datetime horizon = start + MathMax(1, InpCounterfactualHorizonMinutes) * 60;
      if(meta.broker_session_close > start && meta.broker_session_close < horizon)
         horizon = meta.broker_session_close;
      meta.counterfactual_horizon_at = horizon;
      if(horizon <= start){ reason = "counterfactual_horizon_invalid"; return false; }
      datetime now = _NowServerOrLocal();
      datetime evaluation_end = (now < horizon ? now : horizon);
      if(evaluation_end <= start){
         meta.counterfactual_pending = true;
         meta.counterfactual_status = "PENDING";
         meta.counterfactual_resolution_reason = "awaiting_original_policy_horizon";
         reason = meta.counterfactual_resolution_reason;
         return false;
      }
      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      int got = CopyRates(meta.symbol, PERIOD_M1, start, evaluation_end, rates);
      if(got <= 0){
         if(now < horizon){
            meta.counterfactual_pending = true;
            meta.counterfactual_status = "PENDING";
            meta.counterfactual_resolution_reason = "counterfactual_price_path_temporarily_unavailable";
         } else {
            meta.counterfactual_status = "UNAVAILABLE";
            meta.counterfactual_resolution_reason = "counterfactual_price_path_unavailable_at_horizon";
         }
         reason = meta.counterfactual_resolution_reason;
         return false;
      }

      double partial_pct = (meta.tp1_partial_pct > 0.0 ? meta.tp1_partial_pct : InpTP1PartialPct);
      bool tp1_valid = (partial_pct > 0.0 && partial_pct < 1.0 && tp1 > 0.0 &&
                        (meta.is_buy ? (tp1 > entry && tp1 < tp2) : (tp1 < entry && tp1 > tp2)));
      double remaining = 1.0;
      double result_r = 0.0;
      bool tp1_done = false;
      bool resolved = false;
      double last_close = entry;
      for(int i=0; i<got; i++){
         last_close = rates[i].close;
         bool sl_hit = (meta.is_buy ? rates[i].low <= sl : rates[i].high >= sl);
         bool tp1_hit = (tp1_valid && !tp1_done &&
                         (meta.is_buy ? rates[i].high >= tp1 : rates[i].low <= tp1));
         bool tp2_hit = (meta.is_buy ? rates[i].high >= tp2 : rates[i].low <= tp2);
         if(sl_hit && (tp2_hit || tp1_hit)){
            reason = "counterfactual_intrabar_sequence_ambiguous";
            meta.counterfactual_ambiguous = true;
            meta.counterfactual_pending = false;
            meta.counterfactual_status = "AMBIGUOUS";
            meta.counterfactual_resolution_reason = reason;
            meta.counterfactual_evaluated_at = now;
            return false;
         }
         if(sl_hit){
            result_r -= remaining;
            meta.counterfactual_target_before_stop_available = true;
            meta.counterfactual_target_before_stop = false;
            resolved = true;
            break;
         }
         if(tp1_hit){
            double tp1_r = (meta.is_buy ? tp1 - entry : entry - tp1) / risk_dist;
            result_r += tp1_r * partial_pct;
            remaining -= partial_pct;
            tp1_done = true;
         }
         if(tp2_hit){
            double tp2_r = (meta.is_buy ? tp2 - entry : entry - tp2) / risk_dist;
            result_r += tp2_r * remaining;
            remaining = 0.0;
            meta.counterfactual_target_before_stop_available = true;
            meta.counterfactual_target_before_stop = true;
            resolved = true;
            break;
         }
      }
      if(!resolved && now < horizon){
         meta.counterfactual_pending = true;
         meta.counterfactual_ambiguous = false;
         meta.counterfactual_status = "PENDING";
         meta.counterfactual_resolution_reason = "awaiting_original_policy_horizon";
         reason = meta.counterfactual_resolution_reason;
         return false;
      }
      if(!resolved && remaining > 0.0){
         double horizon_r = (meta.is_buy ? last_close - entry : entry - last_close) / risk_dist;
         result_r += horizon_r * remaining;
      }
      double cost_r = meta.actual_realized_cost / meta.initial_risk_money;
      result_r -= cost_r;
      meta.counterfactual_original_sl_tp_result_r = result_r;
      meta.counterfactual_original_sl_tp_result = result_r * meta.initial_risk_money;
      meta.management_alpha = meta.actual_managed_result_r - meta.counterfactual_original_sl_tp_result_r;
      meta.counterfactual_ambiguous = false;
      meta.counterfactual_pending = false;
      meta.counterfactual_status = (resolved ? "RESOLVED_ORIGINAL_LEVELS" : "RESOLVED_HORIZON");
      meta.counterfactual_evaluated_at = now;
      reason = (resolved ? "counterfactual_resolved_by_original_sl_tp" : "counterfactual_resolved_at_horizon");
      meta.counterfactual_resolution_reason = reason;
      return true;
   }

   void _QueueCounterfactualEvaluation(const TradePlan &meta) {
      if(!meta.counterfactual_pending || StringLen(meta.trade_key) == 0) return;
      if(_PendingResearchRecordExists(m_counterfactual_pending, meta.trade_key, false)) return;
      int n = ArraySize(m_counterfactual_pending);
      ArrayResize(m_counterfactual_pending, n + 1);
      m_counterfactual_pending[n] = meta;
      m_state.SavePlans(m_state.CounterfactualPendingPath(), m_counterfactual_pending);
      _Journal("[management_counterfactual] trade_key=" + meta.trade_key
               + " status=pending horizon_at=" + IntegerToString((int)meta.counterfactual_horizon_at)
               + " reason=" + meta.counterfactual_resolution_reason);
   }

   void _AppendCounterfactualUpdate(const TradePlan &meta) {
      string row = "{";
      row += JsonKVStr("schema_version", MANAGEMENT_COUNTERFACTUAL_SCHEMA_VERSION) + ",";
      row += JsonKVStr("event_type", "management_counterfactual_resolution") + ",";
      row += JsonKVStr("trade_key", meta.trade_key) + ",";
      row += JsonKVNum("position_id", (double)meta.position_id, 0) + ",";
      row += JsonKVStr("symbol", meta.symbol) + ",";
      row += JsonKVStr("management_version", meta.management_version) + ",";
      row += JsonKVInt("evaluated_at", (int)meta.counterfactual_evaluated_at) + ",";
      row += JsonKVInt("horizon_at", (int)meta.counterfactual_horizon_at) + ",";
      row += JsonKVBool("counterfactual_complete", !meta.counterfactual_pending) + ",";
      row += JsonKVStr("counterfactual_status", meta.counterfactual_status) + ",";
      row += JsonKVStr("counterfactual_resolution_reason", meta.counterfactual_resolution_reason) + ",";
      row += JsonKVBool("counterfactual_ambiguous", meta.counterfactual_ambiguous) + ",";
      row += JsonKVNum("actual_managed_result", meta.actual_managed_result, 4) + ",";
      row += JsonKVNum("actual_managed_result_r", meta.actual_managed_result_r, 6) + ",";
      if(meta.counterfactual_ambiguous){
         row += "\"counterfactual_original_sl_tp_result\":null,";
         row += "\"counterfactual_original_sl_tp_result_r\":null,";
         row += "\"management_alpha\":null,";
      } else {
         row += JsonKVNum("counterfactual_original_sl_tp_result", meta.counterfactual_original_sl_tp_result, 4) + ",";
         row += JsonKVNum("counterfactual_original_sl_tp_result_r", meta.counterfactual_original_sl_tp_result_r, 6) + ",";
         row += JsonKVNum("management_alpha", meta.management_alpha, 6) + ",";
      }
      row += JsonKVBool("management_policy_selection_eligible", meta.management_policy_selection_eligible) + ",";
      row += JsonKVBool("trading_authority", false);
      row += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\management_counterfactual_updates.jsonl", row + "\n");
   }

   void _MaintainCounterfactualEvaluations() {
      bool changed = false;
      for(int i=ArraySize(m_counterfactual_pending)-1; i>=0; i--){
         TradePlan meta = m_counterfactual_pending[i];
         string reason = "";
         bool evaluated = _ComputeOriginalPolicyCounterfactual(meta, reason);
         if(!evaluated && meta.counterfactual_pending){
            m_counterfactual_pending[i] = meta;
            continue;
         }
         meta.management_policy_selection_eligible = (evaluated && !meta.counterfactual_ambiguous &&
                                                       meta.learning_eligible && meta.execution_identity_verified &&
                                                       meta.candidate_hash_match && meta.execution_fingerprint_match &&
                                                       meta.management_version == MANAGEMENT_SCHEMA_VERSION);
         _AppendCounterfactualUpdate(meta);
         _Journal("[management_counterfactual] trade_key=" + meta.trade_key
                  + " status=" + meta.counterfactual_status
                  + " actual_r=" + DoubleToString(meta.actual_managed_result_r, 6)
                  + " counterfactual_r=" + (meta.counterfactual_ambiguous ? "null" : DoubleToString(meta.counterfactual_original_sl_tp_result_r, 6))
                  + " management_alpha=" + (meta.counterfactual_ambiguous ? "null" : DoubleToString(meta.management_alpha, 6))
                  + " policy_selection_eligible=" + (meta.management_policy_selection_eligible ? "true" : "false")
                  + " reason=" + reason);
         int last = ArraySize(m_counterfactual_pending) - 1;
         if(i != last) m_counterfactual_pending[i] = m_counterfactual_pending[last];
         ArrayResize(m_counterfactual_pending, last);
         changed = true;
      }
      if(changed) m_state.SavePlans(m_state.CounterfactualPendingPath(), m_counterfactual_pending);
   }

   void _CaptureManagementDecisionSnapshot(TradePlan &meta,
                                           const string action,
                                           const double live_px,
                                           const double current_sl,
                                           const double current_tp) {
      if(meta.management_features_time_safe || StringLen(action) == 0 || live_px <= 0.0) return;
      double risk_dist = _RiskDistanceForMeta(meta);
      if(risk_dist <= 0.0) return;
      datetime now = _NowServerOrLocal();
      datetime opened_at = _MetaOpenedAt(meta);
      meta.management_decision_at = now;
      meta.management_features_time_safe = true;
      meta.management_snapshot_action = action;
      meta.management_snapshot_mfe_r = meta.mfe_r;
      meta.management_snapshot_mae_r = meta.mae_r;
      meta.management_snapshot_minutes_open = (opened_at > 0 && now >= opened_at
                                                 ? (int)((now - opened_at) / 60) : 0);
      meta.management_snapshot_distance_to_sl_r = (current_sl > 0.0
                                                    ? MathAbs(live_px - current_sl) / risk_dist : 0.0);
      meta.management_snapshot_distance_to_tp_r = (current_tp > 0.0
                                                    ? MathAbs(current_tp - live_px) / risk_dist : 0.0);
      double bid = SymbolInfoDouble(meta.symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(meta.symbol, SYMBOL_ASK);
      meta.management_snapshot_spread_r = (bid > 0.0 && ask > 0.0
                                            ? MathAbs(ask - bid) / risk_dist
                                            : MathMax(0.0, meta.spread_r));
      meta.management_snapshot_execution_cost_r = MathMax(0.0, meta.execution_cost_r);
      meta.management_snapshot_structure_valid = !meta.dr_and_structural_invalid_triggered;
      _Journal("[management_snapshot] trade_key=" + meta.trade_key
               + " action=" + action
               + " captured_at=" + IntegerToString((int)now)
               + " mfe_r=" + DoubleToString(meta.management_snapshot_mfe_r, 4)
               + " mae_r=" + DoubleToString(meta.management_snapshot_mae_r, 4)
               + " time_safe=true model_authority=shadow_only");
   }

   // The earlier of two "first observed at" stamps, with 0 meaning "not observed yet".
   // A first-observation stamp is a latch: it may be created, and it may be corrected
   // backwards by an observer that saw the same event sooner, but it may never be
   // erased -- erasing it contradicts the derived minutes_to_* fields sitting beside
   // it in the same document.
   datetime _EarliestObservation(const datetime a, const datetime b) const {
      if(a <= 0) return b;
      if(b <= 0) return a;
      return (a < b ? a : b);
   }

   // The open time every analytics site must agree on.  Three sites used to spell this
   // out separately and one of them left out the planned_at fallback.
   datetime _MetaOpenedAt(const TradePlan &meta) const {
      return (meta.filled_at > 0 ? meta.filled_at : meta.planned_at);
   }

   // Minutes from the position opening to a first-observation stamp.  This is a
   // DERIVATION, not an observation: for a given (opened_at, stamp) pair it has exactly
   // one correct value, so every site that publishes it has to produce that value.
   // Returns -1 when it is not derivable yet, which callers read as "leave the field
   // alone" -- 0 is a legitimate answer (a threshold crossed inside the first minute)
   // and must not be confused with "not computed".
   int _MinutesFromOpenToStamp(const datetime opened_at, const datetime stamp) const {
      if(stamp <= 0 || opened_at <= 0) return -1;
      long delta = (long)stamp - (long)opened_at;
      if(delta < 0) delta = 0;
      return (int)(delta / 60);
   }

   // The outer of two excursion prices, with <= 0 meaning "not observed yet".
   // favourable=true asks for the best-case extreme (highest for a buy, lowest for a
   // sell); favourable=false asks for the worst-case one.  Same rule as the ratchet in
   // _UpdateAnalyticsSnapshot, so the two writers can no longer disagree about which
   // direction an extreme is allowed to move.
   double _MergeExcursionPrice(const double current, const double incoming,
                               const bool is_buy, const bool favourable) const {
      if(incoming <= 0.0) return current;
      if(current <= 0.0) return incoming;
      return ((is_buy == favourable) ? MathMax(current, incoming) : MathMin(current, incoming));
   }

   void _ApplyPenaltyStateToMeta(TradePlan &meta, const PenaltyState &state) {
      meta.management_version = state.management_version;
      meta.management_previous_state = state.previous_state;
      meta.management_state = state.current_state;
      meta.management_transition_time = state.transition_time;
      meta.management_transition_reason = state.transition_reason;
      meta.management_evidence_snapshot_json = state.evidence_snapshot_json;
      meta.management_action_executed = state.action_executed;
      meta.management_action_id = state.action_id;
      meta.management_action_lifecycle_state = state.action_lifecycle_state;
      meta.management_requested_action = state.requested_action;
      meta.management_requested_volume = state.requested_volume;
      meta.management_normalized_volume = state.normalized_volume;
      meta.management_position_volume_before = state.action_position_volume_before;
      meta.management_requested_cut_fraction = state.requested_cut_fraction;
      meta.management_action_retry_count = state.action_retry_count;
      meta.management_next_retry_at = state.next_eligible_retry_time;
      meta.management_last_retcode = state.action_last_retcode;
      meta.management_last_retcode_description = state.action_last_retcode_description;
      meta.management_action_terminal_reason = state.action_terminal_reason;
      meta.invalidation_confirmation_mode = state.confirmation_mode;
      meta.invalidation_reference_timeframe = state.confirmation_timeframe;
      meta.invalidation_trigger_level = state.confirmation_trigger_level;
      meta.invalidation_spread = state.confirmation_spread;
      meta.invalidation_buffer = state.confirmation_buffer;
      meta.invalidation_first_breach_time = state.first_breach_time;
      meta.invalidation_confirmed_time = state.confirmed_time;
      meta.invalidation_confirming_bar = state.confirming_bar;
      // PenaltyWatcher and _UpdateAnalyticsSnapshot both observe the same excursion,
      // but they measure it differently on purpose: the watcher divides by the broker
      // position's own risk distance (POSITION_PRICE_OPEN vs POSITION_SL) while the
      // snapshot divides by _RiskDistanceForMeta(), and entry slippage separates the
      // two.  So one of them can still read "0.25R not reached" after the other has
      // already latched the crossing.  Copying the watcher's copy straight over the
      // top therefore ERASED a latched observation, _UpdateAnalyticsSnapshot re-latched
      // it to "now" further down the same pass, and the trade meta flipped between 0
      // and a timestamp on every maintenance tick -- a document that never repeats,
      // which no content memo can coalesce, so all seven meta aliases were rewritten
      // every simulated second for the life of the position.
      // These are merges, not overwrites: every observation is kept and none is lost.
      meta.mfe_price = _MergeExcursionPrice(meta.mfe_price, state.mfe_price, meta.is_buy, true);
      meta.mae_price = _MergeExcursionPrice(meta.mae_price, state.mae_price, meta.is_buy, false);
      meta.mfe_r = MathMax(meta.mfe_r, state.mfe_r);
      meta.mae_r = MathMax(meta.mae_r, state.mae_r);
      meta.first_0_25r_time = _EarliestObservation(meta.first_0_25r_time, state.first_0_25r_time);
      meta.first_0_50r_time = _EarliestObservation(meta.first_0_50r_time, state.first_0_50r_time);
      meta.first_adverse_threshold_time =
         _EarliestObservation(meta.first_adverse_threshold_time, state.first_adverse_threshold_time);
      meta.latest_observed_tick_time = state.latest_observed_tick_time;
      meta.latest_observed_tick_msc = state.latest_observed_tick_msc;
      // An unset or UNKNOWN status is not an observation, so it must not erase one.
      // _UpdateAnalyticsSnapshot fills exactly those two values in when it finds the
      // field empty, which is the same fight the stamps above were losing.
      if(StringLen(state.path_completeness_status) > 0 &&
         state.path_completeness_status != "UNKNOWN")
         meta.path_completeness_status = state.path_completeness_status;
      if(StringLen(state.path_observation_source) > 0)
         meta.path_observation_source = state.path_observation_source;
      meta.path_data_gap = state.path_data_gap;
      meta.path_order_ambiguous = state.path_order_ambiguous;
      datetime opened_at = _MetaOpenedAt(meta);
      int minutes_to_25 = _MinutesFromOpenToStamp(opened_at, meta.first_0_25r_time);
      if(minutes_to_25 >= 0) meta.minutes_to_0_25r_mfe = minutes_to_25;
      int minutes_to_50 = _MinutesFromOpenToStamp(opened_at, meta.first_0_50r_time);
      if(minutes_to_50 >= 0) meta.minutes_to_0_50r_mfe = minutes_to_50;
      meta.penalty_reductions_count = MathMax(meta.penalty_reductions_count, state.strikes);
   }

   void _UpdateAnalyticsSnapshot(TradePlan &meta, const ulong ticket, const double live_px, const double live_vol) {
      if(!InpAnalyticsEnable) return;
      if(meta.planned_entry <= 0) meta.planned_entry = meta.entry_est;
      if(meta.planned_sl <= 0) meta.planned_sl = meta.sl;
      if(meta.planned_tp1 <= 0) meta.planned_tp1 = meta.tp1;
      if(meta.planned_tp2 <= 0) meta.planned_tp2 = meta.tp2;
      if(meta.planned_at <= 0) meta.planned_at = (meta.created_at > 0 ? meta.created_at : _NowServerOrLocal());
      if(meta.initial_volume <= 0 && live_vol > 0) meta.initial_volume = live_vol;
      if(ticket > 0 && PositionSelectByTicket(ticket)){
         meta.position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
         if(meta.filled_entry <= 0) meta.filled_entry = PositionGetDouble(POSITION_PRICE_OPEN);
         if(meta.filled_at <= 0) meta.filled_at = (datetime)PositionGetInteger(POSITION_TIME);
      }
      if(meta.filled_entry <= 0) meta.filled_entry = meta.planned_entry;
      datetime now = _NowServerOrLocal();
      double risk_dist = _RiskDistanceForMeta(meta);
      if(risk_dist > 0 && meta.planned_entry > 0){
         meta.fill_slippage = meta.filled_entry - meta.planned_entry;
         double adverse = (meta.is_buy ? (meta.filled_entry - meta.planned_entry) : (meta.planned_entry - meta.filled_entry));
         meta.fill_slippage_r = adverse / risk_dist;
      }
      if(meta.mfe_price <= 0) meta.mfe_price = meta.filled_entry;
      if(meta.mae_price <= 0) meta.mae_price = meta.filled_entry;
      if(meta.is_buy){
         meta.mfe_price = MathMax(meta.mfe_price, live_px);
         meta.mae_price = MathMin(meta.mae_price, live_px);
      } else {
         meta.mfe_price = MathMin(meta.mfe_price, live_px);
         meta.mae_price = MathMax(meta.mae_price, live_px);
      }
      if(risk_dist > 0){
         double sampled_mfe_r = (meta.is_buy ? (meta.mfe_price - meta.filled_entry) : (meta.filled_entry - meta.mfe_price)) / risk_dist;
         double sampled_mae_r = (meta.is_buy ? (meta.filled_entry - meta.mae_price) : (meta.mae_price - meta.filled_entry)) / risk_dist;
         meta.mfe_r = MathMax(meta.mfe_r, MathMax(0.0, sampled_mfe_r));
         meta.mae_r = MathMax(meta.mae_r, MathMax(0.0, sampled_mae_r));
         datetime opened_at = _MetaOpenedAt(meta);
         int minutes_open = (opened_at > 0 && now >= opened_at ? (int)((now - opened_at) / 60) : 0);
         if(meta.first_0_25r_time <= 0 && meta.mfe_r >= 0.25) meta.first_0_25r_time = now;
         if(meta.first_0_50r_time <= 0 && meta.mfe_r >= 0.50) meta.first_0_50r_time = now;
         if(meta.first_adverse_threshold_time <= 0 && meta.mae_r >= MathMax(0.01, MathAbs(InpPenaltyMaeTriggerR)))
            meta.first_adverse_threshold_time = now;
         // Derived from the stamp, never from "now".  These two lines used to publish
         // minutes_open under a "<= 0 means not computed yet" guard, but 0 is the right
         // answer whenever the threshold is crossed inside the first minute -- which is
         // what happened here -- so the guard re-fired on every pass and wrote a number
         // that grew with the clock, while _ApplyPenaltyStateToMeta recomputed the
         // correct 0 right after it.  At the instant of the crossing the stamp IS now,
         // so the intended case is unchanged; only the repeat is removed.
         int minutes_to_25 = _MinutesFromOpenToStamp(opened_at, meta.first_0_25r_time);
         if(minutes_to_25 >= 0) meta.minutes_to_0_25r_mfe = minutes_to_25;
         int minutes_to_50 = _MinutesFromOpenToStamp(opened_at, meta.first_0_50r_time);
         if(minutes_to_50 >= 0) meta.minutes_to_0_50r_mfe = minutes_to_50;
         if(StringLen(meta.path_completeness_status) == 0 || meta.path_completeness_status == "UNKNOWN"){
            meta.path_completeness_status = "TIMER_SAMPLED";
            meta.path_observation_source = "TRADE_ENGINE_TIMER_SNAPSHOT";
         }
         int stuck_minutes = (meta.penalty_stuck_minutes > 0 ? meta.penalty_stuck_minutes : InpPenaltyStuckMinutes);
         double stuck_min_mfe = (meta.penalty_stuck_min_mfe_r > 0.0 ? meta.penalty_stuck_min_mfe_r : InpPenaltyStuckMinMfeR);
         if(stuck_minutes > 0 && minutes_open >= stuck_minutes && meta.mfe_r < stuck_min_mfe)
            meta.stuck_no_mfe_triggered = true;
         bool dr_invalid = (meta.is_buy ? (meta.source_dr_low > 0 && live_px < meta.source_dr_low) :
                                          (meta.source_dr_high > 0 && live_px > meta.source_dr_high));
         bool structural_invalid = (meta.is_buy ? ((meta.source_manip_low > 0 && live_px < meta.source_manip_low) ||
                                                   (meta.po3.swing_low > 0 && live_px < meta.po3.swing_low)) :
                                                   ((meta.source_manip_high > 0 && live_px > meta.source_manip_high) ||
                                                    (meta.po3.swing_high > 0 && live_px > meta.po3.swing_high)));
         if(dr_invalid && structural_invalid)
            meta.dr_and_structural_invalid_triggered = true;
         if((meta.stuck_no_mfe_triggered || meta.dr_and_structural_invalid_triggered) &&
            meta.initial_volume > 0.0 && live_vol > 0.0 && live_vol < meta.initial_volume)
            meta.penalty_reductions_count = MathMax(meta.penalty_reductions_count, 1);
         double tp1_price = (meta.planned_tp1 > 0 ? meta.planned_tp1 : meta.tp1);
         double tp2_price = (meta.planned_tp2 > 0 ? meta.planned_tp2 : meta.tp2);
         double sl_price = (meta.planned_sl > 0 ? meta.planned_sl : meta.sl);
         if(meta.tp1_hit_at <= 0 && tp1_price > 0 && (meta.is_buy ? live_px >= tp1_price : live_px <= tp1_price))
            meta.tp1_hit_at = now;
         if(meta.tp2_hit_at <= 0 && tp2_price > 0 && (meta.is_buy ? live_px >= tp2_price : live_px <= tp2_price))
            meta.tp2_hit_at = now;
         if(meta.sl_hit_at <= 0 && sl_price > 0 && (meta.is_buy ? live_px <= sl_price : live_px >= sl_price))
            meta.sl_hit_at = now;
         double planned_rr = _PlanRR2(meta);
         if(planned_rr > 0.0) meta.target_efficiency = _ClampRange(meta.mfe_r / planned_rr, 0.0, 2.0);
         if(meta.tp2_hit_at > 0 || (planned_rr > 0.0 && meta.mfe_r >= planned_rr * 0.90))
            meta.tp2_realistic_before_reversal = true;
         if(meta.stop_floor_distance > 0.0){
            meta.realized_stop_buffer_r = (risk_dist - meta.stop_floor_distance) / risk_dist;
         }
         double realized_quality = meta.stop_quality_score;
         if(meta.fill_slippage_r > 0.0) realized_quality -= MathMin(1.5, meta.fill_slippage_r * 3.0);
         if(meta.mae_r >= 1.0 && meta.mfe_r >= 0.5) realized_quality -= 1.0;
         meta.realized_stop_quality = _ClampRange(realized_quality, 0.0, 10.0);
      }
   }

   bool _SymbolHasPendingOrder(const string symbol) {
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         if(OrderGetString(ORDER_SYMBOL) == symbol) return true;
      }
      return false;
   }

   bool _HasBlockingPendingOrderForPlan(const TradePlan &p) {
      if(!InpSkipIfSymbolOpen && !m_force_one_managed_trade_per_symbol) return false;
      bool saw_pending = false;
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         string sym = OrderGetString(ORDER_SYMBOL);
         if(sym != p.symbol) continue;
         saw_pending = true;
         if(InpMaxTradesPerSweep <= 1 || !_PlanHasSweepKey(p)) return true;
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) return true;
         if(!_NormalizePlanPO3State(meta, "symbol_pending_exposure")) return true;
         if(!_SameSweep(meta, p)) return true;
      }
      return false;
   }

   bool _ShouldSkipForGlobalOpenPositions() {
      return (InpSkipIfAnyPositionOpen && _CountOpenPositions() > 0);
   }

   bool _ShouldSkipForSymbolExposure(const string symbol) {
      if(m_force_one_managed_trade_per_symbol)
         return (_SymbolHasOpenPosition(symbol) || _SymbolHasPendingOrder(symbol));
      if(!InpSkipIfSymbolOpen) return false;
      return (_SymbolHasOpenPosition(symbol) || _SymbolHasPendingOrder(symbol));
   }

   bool _SymbolBusy(const string symbol) {
      if(_ShouldSkipForGlobalOpenPositions()) return true;
      if(m_force_one_managed_trade_per_symbol &&
         (_SymbolHasOpenPosition(symbol) || _SymbolHasPendingOrder(symbol))) return true;
      if(InpSkipIfSymbolOpen && _SymbolHasOpenPosition(symbol)) return true;
      if(InpSkipIfSymbolOpen && InpMaxTradesPerSweep <= 1 && _SymbolHasPendingOrder(symbol)) return true;
      if(_HasSymbolInPlans(m_watchlist, symbol)) return true;
      if(_HasSymbolInPlans(m_pending_ai, symbol)) return true;
      if(_HasSymbolInPlans(m_scan_candidates, symbol)) return true;
      return false;
   }

   int _ExistingClusterCount(const string cluster) {
      int count = 0;
      for(int i=0; i<ArraySize(m_watchlist); i++){
         if(m_watchlist[i].portfolio_cluster == cluster) count++;
      }
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(meta.portfolio_cluster == cluster) count++;
      }
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         string sym = OrderGetString(ORDER_SYMBOL);
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(meta.portfolio_cluster == cluster) count++;
      }
      return count;
   }

   int _ExistingSessionCount(const string session_name) {
      int count = 0;
      for(int i=0; i<ArraySize(m_watchlist); i++){
         if(m_watchlist[i].po3.session_name == session_name) count++;
      }
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(meta.po3.session_name == session_name) count++;
      }
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         string sym = OrderGetString(ORDER_SYMBOL);
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(meta.po3.session_name == session_name) count++;
      }
      return count;
   }

   int _ExistingUsdCount(const string usd_key) {
      int count = 0;
      for(int i=0; i<ArraySize(m_watchlist); i++){
         if(m_watchlist[i].usd_exposure_key == usd_key) count++;
      }
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(meta.usd_exposure_key == usd_key) count++;
      }
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         string sym = OrderGetString(ORDER_SYMBOL);
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         if(meta.usd_exposure_key == usd_key) count++;
      }
      return count;
   }

   double _NominalPortfolioRiskBudget() {
      if(InpMaxTotalRiskEnable && InpMaxTotalRiskMoney > 0.0) return InpMaxTotalRiskMoney;
      double one_trade = CalcDesiredRiskMoney();
      if(one_trade <= 0.0) return 0.0;
      return one_trade * MathMax(1, InpMaxOpenPositions);
   }

   void _AccumulateCorrelationRisk(const TradePlan &candidate, const TradePlan &meta, const double risk_money,
                                   double &cluster_risk, double &usd_risk, double &session_risk,
                                   double &currency_risk, double &direction_risk) const {
      if(risk_money <= 0.0) return;
      if(meta.portfolio_cluster == candidate.portfolio_cluster) cluster_risk += risk_money;
      if(meta.usd_exposure_key == candidate.usd_exposure_key) usd_risk += risk_money;
      if(meta.po3.session_name == candidate.po3.session_name) session_risk += risk_money;
      if(meta.is_buy == candidate.is_buy && meta.po3.sweep_side == candidate.po3.sweep_side) direction_risk += risk_money;

      string c_base = "", c_quote = "", m_base = "", m_quote = "";
      if(_ExtractFxPair(candidate.symbol, c_base, c_quote) && _ExtractFxPair(meta.symbol, m_base, m_quote)){
         if(c_base == m_base || c_base == m_quote || c_quote == m_base || c_quote == m_quote)
            currency_risk += risk_money;
      } else if(candidate.asset_class == meta.asset_class && candidate.asset_class != "cfd"){
         currency_risk += risk_money;
      }
   }

   bool _CorrelatedExposureOk(const TradePlan &candidate, const double new_risk_money, string &reason) {
      reason = "ok";
      if(!InpMaxPortfolioClusterEnable) return true;
      double budget = _NominalPortfolioRiskBudget();
      if(budget <= 0.0 || new_risk_money <= 0.0) return true;

      double cluster_risk = 0.0, usd_risk = 0.0, session_risk = 0.0, currency_risk = 0.0, direction_risk = 0.0;
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         double entry = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl = PositionGetDouble(POSITION_SL);
         double vol = PositionGetDouble(POSITION_VOLUME);
         bool is_buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double risk = _RiskMoneyForPosition(sym, is_buy, vol, entry, sl);
         _AccumulateCorrelationRisk(candidate, meta, risk, cluster_risk, usd_risk, session_risk, currency_risk, direction_risk);
      }

      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         bool is_pending_buy =
            (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY_STOP_LIMIT);
         bool is_pending_sell =
            (type == ORDER_TYPE_SELL_LIMIT || type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL_STOP_LIMIT);
         if(!is_pending_buy && !is_pending_sell) continue;
         string sym = OrderGetString(ORDER_SYMBOL);
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)) continue;
         double entry = OrderGetDouble(ORDER_PRICE_OPEN);
         double sl = OrderGetDouble(ORDER_SL);
         double vol = OrderGetDouble(ORDER_VOLUME_CURRENT);
         double risk = _RiskMoneyForPosition(sym, is_pending_buy, vol, entry, sl);
         _AccumulateCorrelationRisk(candidate, meta, risk, cluster_risk, usd_risk, session_risk, currency_risk, direction_risk);
      }

      if(cluster_risk + new_risk_money > budget * InpMaxClusterRiskFrac){
         reason = "cluster_exposure_cap";
         return false;
      }
      if(usd_risk + new_risk_money > budget * InpMaxUsdConcentrationFrac){
         reason = "usd_exposure_cap";
         return false;
      }
      if(session_risk + new_risk_money > budget * InpMaxSessionExposureFrac){
         reason = "session_exposure_cap";
         return false;
      }
      if(currency_risk + new_risk_money > budget * InpMaxCorrelatedRiskFrac){
         reason = "currency_correlation_cap";
         return false;
      }
      if(direction_risk + new_risk_money > budget * InpMaxCorrelatedRiskFrac){
         reason = "same_po3_direction_cap";
         return false;
      }
      return true;
   }

   void _CaptureEntryRiskContext(TradePlan &meta,
                                 const double volume,
                                 const double entry_price,
                                 const double stop_price) {
      meta.account_equity_at_entry = AccountInfoDouble(ACCOUNT_EQUITY);
      meta.account_balance_at_entry = AccountInfoDouble(ACCOUNT_BALANCE);
      meta.initial_risk_money = _RiskMoneyForPosition(meta.symbol, meta.is_buy, volume, entry_price, stop_price);
      meta.initial_risk_pct_equity = (meta.account_equity_at_entry > 0.0
                                      ? meta.initial_risk_money / meta.account_equity_at_entry * 100.0
                                      : 0.0);
      meta.original_initial_risk_money = meta.initial_risk_money;
      meta.original_initial_risk_money_per_lot = (volume > 0.0 ? meta.initial_risk_money / volume : 0.0);
      meta.original_entry_for_risk = entry_price;
      meta.original_sl_for_risk = stop_price;
      meta.risk_factor_schema_version = RISK_FACTOR_SCHEMA_VERSION;
      meta.ledger_schema_version = TRADE_LEDGER_SCHEMA_VERSION;
      meta.management_version = MANAGEMENT_SCHEMA_VERSION;
      if(StringLen(meta.management_state) == 0) meta.management_state = "HEALTHY";
      if(StringLen(meta.management_policy) == 0){
         if(InpThesisInvalidationPolicy == THESIS_INVALIDATION_FULL_EXIT)
            meta.management_policy = "full_exit_on_confirmed_thesis_invalidation";
         else if(InpThesisInvalidationPolicy == THESIS_INVALIDATION_ORIGINAL_SL_TP_ONLY)
            meta.management_policy = "original_sl_tp_only";
         else
            meta.management_policy = "partial_exit_on_confirmed_thesis_invalidation";
      }
      meta.invalidation_confirmation_mode = IntegerToString((int)InpInvalidationConfirmationMode);
   }

   bool _ApplyBrokerSessionEntryGate(TradePlan &meta, string &reason) {
      reason = "";
      string schedule_json = "{}";
      if(!SymbolEntryAllowedByBrokerSession(meta.symbol, _NowServerOrLocal(), reason, schedule_json))
         return false;
      meta.broker_session_schedule_json = schedule_json;
      meta.broker_session_source = (InpUseBrokerSymbolSessions ? "broker_session_api" : "disabled");
      meta.broker_session_open = (datetime)JsonGetNumber(schedule_json, "open", 0.0);
      meta.broker_session_close = (datetime)JsonGetNumber(schedule_json, "close", 0.0);
      meta.broker_no_entry_from = (datetime)JsonGetNumber(schedule_json, "no_entry_from", 0.0);
      meta.broker_flatten_from = (datetime)JsonGetNumber(schedule_json, "flatten_from", 0.0);
      meta.broker_next_tradable_session = (datetime)JsonGetNumber(schedule_json, "next_tradable", 0.0);
      return true;
   }

   bool _ApplyFinalPortfolioRiskGovernance(TradePlan &meta,
                                           const double proposed_risk_money,
                                           string &reason) {
      reason = "";
      double open_risk = 0.0;
      double pending_risk = 0.0;
      double post_risk = 0.0;
      double post_pct = 0.0;
      double cap_money = 0.0;
      bool aggregate_ok = PortfolioInitialRiskGate(proposed_risk_money,
                                                    open_risk,
                                                    pending_risk,
                                                    post_risk,
                                                    post_pct,
                                                    cap_money,
                                                    reason);
      double base_money = MathMax(AccountInfoDouble(ACCOUNT_EQUITY), 0.0);
      double open_pct = (base_money > 0.0 ? open_risk / base_money * 100.0 : 0.0);
      _Journal("[portfolio_initial_risk] open_risk_money=" + DoubleToString(open_risk, 2)
               + " open_risk_pct=" + DoubleToString(open_pct, 4)
               + " pending_risk_money=" + DoubleToString(pending_risk, 2)
               + " proposed_risk_money=" + DoubleToString(proposed_risk_money, 2)
               + " post_trade_risk_pct=" + DoubleToString(post_pct, 4)
               + " cap_pct=" + DoubleToString(InpMaxTotalRiskPct, 4)
               + " cap_money=" + DoubleToString(cap_money, 2)
               + " status=" + (aggregate_ok ? "pass" : "blocked")
               + (StringLen(reason) > 0 ? " reason=" + reason : ""));
      if(!aggregate_ok) return false;

      if(proposed_risk_money <= 0.0000001){
         meta.risk_factor_schema_version = RISK_FACTOR_SCHEMA_VERSION;
         if(StringLen(meta.risk_factor_contributions_json) == 0)
            meta.risk_factor_contributions_json = "{}";
         return true;
      }

      string factor_json = "{}";
      string factor_reason = "";
      string session = (StringLen(meta.session_code) > 0 ? meta.session_code : meta.po3.session_name);
      if(!RiskFactorGate(meta.symbol, meta.is_buy, session, proposed_risk_money, factor_json, factor_reason)){
         reason = factor_reason;
         return false;
      }
      meta.risk_factor_schema_version = RISK_FACTOR_SCHEMA_VERSION;
      meta.risk_factor_contributions_json = factor_json;
      return true;
   }

   double _MatchingPendingRiskForReplacement(const TradePlan &meta) const {
      double total = 0.0;
      for(int i=0; i<OrdersTotal(); i++){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         if(OrderGetString(ORDER_SYMBOL) != meta.symbol) continue;
         string comment = OrderGetString(ORDER_COMMENT);
         if(StringLen(meta.broker_comment) > 0 && comment != meta.broker_comment && comment != meta.trade_key) continue;
         ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         bool is_buy = (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY_STOP_LIMIT);
         bool is_sell = (type == ORDER_TYPE_SELL_LIMIT || type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL_STOP_LIMIT);
         if(!is_buy && !is_sell) continue;
         if(is_buy != meta.is_buy) continue;
         total += MathMax(0.0, _RiskMoneyForPosition(meta.symbol, meta.is_buy,
                                                    OrderGetDouble(ORDER_VOLUME_CURRENT),
                                                    OrderGetDouble(ORDER_PRICE_OPEN),
                                                    OrderGetDouble(ORDER_SL)));
      }
      return total;
   }

   void _MaintainBrokerSymbolSessionProtection() {
      if(!InpUseBrokerSymbolSessions) return;
      datetime now = _NowServerOrLocal();

      for(int i=OrdersTotal()-1; i>=0; i--){
         ulong order_ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(order_ticket)) continue;
         string symbol = OrderGetString(ORDER_SYMBOL);
         datetime session_open = 0, session_close = 0, no_entry_from = 0, flatten_from = 0, next_tradable = 0;
         string schedule_reason = "";
         if(!QueryBrokerSymbolSessionSchedule(symbol, now, session_open, session_close,
                                              no_entry_from, flatten_from, next_tradable, schedule_reason)) continue;
         if(flatten_from <= 0 || now < flatten_from || now >= session_close) continue;
         TradePlan meta;
         string comment = OrderGetString(ORDER_COMMENT);
         if(!_LoadTradeMeta(order_ticket, symbol, comment, meta)) continue;
         if(meta.broker_flatten_next_retry > now) continue;
         meta.broker_flatten_attempts++;
         bool deleted = m_trade.OrderDelete(order_ticket);
         meta.broker_flatten_last_retcode = (long)m_trade.ResultRetcode();
         if(!deleted){
            meta.broker_flatten_failures++;
            meta.broker_flatten_next_retry = now + MathMax(1, InpSymbolFlattenRetrySeconds);
         } else {
            meta.broker_flatten_next_retry = 0;
            meta.narrative_state = "deleted_before_broker_session_close";
         }
         _WriteTradeMeta(meta, order_ticket);
         _Journal("[pre_close_pending] symbol=" + symbol
                  + " order_ticket=" + IntegerToString((long)order_ticket)
                  + " attempt=" + IntegerToString(meta.broker_flatten_attempts)
                  + " success=" + (deleted ? "true" : "false")
                  + " retcode=" + IntegerToString(meta.broker_flatten_last_retcode)
                  + " next_retry=" + TimeToString(meta.broker_flatten_next_retry, TIME_DATE|TIME_SECONDS));
      }

      for(int i=PositionsTotal()-1; i>=0; i--){
         ulong position_ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(position_ticket)) continue;
         string symbol = PositionGetString(POSITION_SYMBOL);
         datetime session_open = 0, session_close = 0, no_entry_from = 0, flatten_from = 0, next_tradable = 0;
         string schedule_reason = "";
         if(!QueryBrokerSymbolSessionSchedule(symbol, now, session_open, session_close,
                                              no_entry_from, flatten_from, next_tradable, schedule_reason)) continue;
         if(flatten_from <= 0 || now < flatten_from || now >= session_close) continue;
         TradePlan meta;
         string comment = PositionGetString(POSITION_COMMENT);
         string identity_reason = "";
         if(!_LoadAndResolvePositionMeta(position_ticket, symbol, comment, meta, identity_reason)){
            _Journal("[pre_close_remaining_exposure] symbol=" + symbol
                     + " position_ids=" + IntegerToString((long)PositionGetInteger(POSITION_IDENTIFIER))
                     + " risk_money=unavailable close_failures=0 reason=identity_unavailable:" + identity_reason);
            continue;
         }
         if(meta.broker_flatten_next_retry > now) continue;
         meta.broker_session_open = session_open;
         meta.broker_session_close = session_close;
         meta.broker_no_entry_from = no_entry_from;
         meta.broker_flatten_from = flatten_from;
         meta.broker_next_tradable_session = next_tradable;
         meta.broker_session_source = "broker_session_api";
         meta.broker_flatten_attempts++;
         double remaining_volume = PositionGetDouble(POSITION_VOLUME);
         double risk_money = (meta.original_initial_risk_money_per_lot > 0.0
                              ? meta.original_initial_risk_money_per_lot * remaining_volume : 0.0);
         bool closed = m_trade.PositionClose(position_ticket);
         meta.broker_flatten_last_retcode = (long)m_trade.ResultRetcode();
         if(!closed){
            meta.broker_flatten_failures++;
            meta.broker_flatten_next_retry = now + MathMax(1, InpSymbolFlattenRetrySeconds);
         } else {
            meta.broker_flatten_next_retry = 0;
            meta.narrative_state = "flatten_submitted_before_broker_session_close";
         }
         _WriteTradeMeta(meta, position_ticket);
         _Journal("[pre_close_remaining_exposure] symbol=" + symbol
                  + " position_ids=" + IntegerToString(meta.broker_position_identifier)
                  + " risk_money=" + DoubleToString(risk_money, 2)
                  + " close_failures=" + IntegerToString(meta.broker_flatten_failures)
                  + " attempt=" + IntegerToString(meta.broker_flatten_attempts)
                  + " success=" + (closed ? "true" : "false")
                  + " retcode=" + IntegerToString(meta.broker_flatten_last_retcode));
      }
   }

   bool _SymbolEligible(const string symbol) {
      if(!SymbolSelect(symbol, true)) return false;
      if(!SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE)) return false;
      if(!SymbolInfoInteger(symbol, SYMBOL_SELECT)) return false;
      return true;
   }

   int _CountOpenPositions() {
      int count = 0;
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(PositionMatchesMagic(ticket)) count++;
      }
      return count;
   }

   bool _SymbolHasOpenPosition(const string symbol) {
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL) == symbol) return true;
      }
      return false;
   }

   bool _HasStructureShift(const PO3Context &po3) {
      return (po3.has_bos || po3.has_displacement ||
              po3.htf_mss || po3.htf_choch ||
              po3.ltf_bos || po3.ltf_mss || po3.ltf_choch);
   }

   bool _RegimeGateOkEx(const string symbol, const ENUM_TIMEFRAMES tf, const bool is_buy, const PO3Context &po3,
                        double &atr_pct, double &trend_strength, double &trend_slope_pct,
                        double &adx_value, double &adr_pct, double &session_vol_ratio,
                        double &vwap_dist_atr, double &compression_score, double &expansion_score,
                        double &news_risk, string &reason) {
      reason = "ok";
      atr_pct = 0; trend_strength = 0; trend_slope_pct = 0;
      adx_value = 0; adr_pct = 0; session_vol_ratio = 0;
      vwap_dist_atr = 0; compression_score = 0; expansion_score = 0; news_risk = 0;

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, tf, 0, 200, rates);
      if(got < 60){
         reason = "insufficient_htf_rates";
         return false;
      }

      atr_pct = ATRPctFromRates(rates, got, 14);
      trend_strength = TrendStrength(rates, got, 50);
      trend_slope_pct = TrendSlopePct(rates, got, 50);
      adx_value = ADXValue(symbol, tf, 14);
      adr_pct = AverageDailyRangePct(symbol, 20);
      expansion_score = ExpansionRatioFromRates(rates, got, 14, 50);
      compression_score = MathMax(0.0, 1.0 - expansion_score);

      double px = rates[1].close;
      double adr_abs = (adr_pct > 0 && px > 0 ? adr_pct * px : 0.0);
      double session_range = MathAbs(po3.session_high - po3.session_low);
      if(session_range <= 0 && po3.dr_high > po3.dr_low)
         session_range = MathAbs(po3.dr_high - po3.dr_low);
      if(adr_abs > 0 && session_range > 0) session_vol_ratio = session_range / adr_abs;

      MqlRates ltf_rates[];
      ArraySetAsSeries(ltf_rates, true);
      int got_ltf = CopyRates(symbol, PO3EffectiveEntryTF(), 0, 800, ltf_rates);
      double ltf_atr = ATRFromRates(ltf_rates, got_ltf, 14);
      datetime vwap_anchor = (po3.session_start > 0 ? po3.session_start : (po3.t_sweep > 0 ? po3.t_sweep : 0));
      double vwap = AnchoredVWAPFromRates(ltf_rates, got_ltf, vwap_anchor);
      if(vwap > 0 && ltf_atr > 0) vwap_dist_atr = MathAbs(ltf_rates[1].close - vwap) / ltf_atr;

      if(expansion_score > InpExpansionRatioMax && vwap_dist_atr > InpVwapMaxDistAtr && session_vol_ratio > 0.45)
         news_risk = 1.0;

      if(!InpRegimeGateEnable) return true;

      bool structure_shift = _HasStructureShift(po3);
      bool po3_impulse = (po3.has_sweep && po3.has_displacement);
      bool active_session = (po3.session_name != "OFF_HOURS");
      bool session_context = (po3.in_killzone || active_session);
      double soft_trend_floor = MathMax(0.18, InpTrendStrengthMin * 0.45);
      double impulse_trend_floor = MathMax(0.12, soft_trend_floor * 0.75);
      double neutral_slope_pct = 0.00015;
      bool slope_aligned = (is_buy ? (trend_slope_pct > neutral_slope_pct) : (trend_slope_pct < -neutral_slope_pct));
      bool slope_neutral = (MathAbs(trend_slope_pct) <= neutral_slope_pct);

      if(atr_pct < InpAtrMinPct){
         reason = "atr_too_low";
         return false;
      }
      if(atr_pct > InpAtrMaxPct){
         reason = "atr_too_high";
         return false;
      }
      if(adr_pct > 0 && adr_pct < InpAdrMinPct){
         reason = "adr_too_low";
         return false;
      }
      if(po3.session_name != "OFF_HOURS" && session_vol_ratio > 0 && session_vol_ratio < InpSessionVolMinAdrFrac){
         if(!(po3_impulse && session_context)){
            reason = "session_range_too_small";
            return false;
         }
      }
      if(expansion_score > 0 && expansion_score < InpExpansionRatioMin){
         if(!(po3_impulse && session_context)){
            reason = "compression_too_high";
            return false;
         }
      }
      if(expansion_score > InpExpansionRatioMax){
         reason = "shock_expansion";
         return false;
      }
      if(vwap_dist_atr > InpVwapMaxDistAtr){
         if(!(po3_impulse && structure_shift && session_context)){
            reason = "too_far_from_vwap";
            return false;
         }
      }
      if(news_risk >= 1.0){
         reason = "news_risk";
         return false;
      }

      bool allow_weak_trend = ((structure_shift && session_context && trend_strength >= soft_trend_floor) ||
                               (po3_impulse && session_context && trend_strength >= impulse_trend_floor) ||
                               (adx_value > 0 && adx_value >= InpAdxMin && po3_impulse && trend_strength >= impulse_trend_floor));
      if(trend_strength < InpTrendStrengthMin && !allow_weak_trend){
         reason = "trend_strength_low";
         return false;
      }

      if(adx_value > 0 && adx_value < InpAdxMin){
         bool allow_low_adx = (po3_impulse && structure_shift && session_context && trend_strength >= impulse_trend_floor);
         if(!allow_low_adx){
            reason = "adx_too_low";
            return false;
         }
      }

      if(!slope_aligned && !slope_neutral){
         bool allow_counter_slope = (structure_shift && po3.has_bos && po3.has_displacement && session_context && trend_strength >= soft_trend_floor);
         if(!allow_counter_slope){
            reason = "slope_against_trade";
            return false;
         }
      }

      return true;
   }

   bool _RegimeGateOk(const string symbol, const ENUM_TIMEFRAMES tf, const bool is_buy, const PO3Context &po3,
                      double &atr_pct, double &trend_strength, double &trend_slope_pct,
                      double &adx_value, double &adr_pct, double &session_vol_ratio,
                      double &vwap_dist_atr, double &compression_score, double &expansion_score,
                      double &news_risk) {
      string reason = "";
      return _RegimeGateOkEx(symbol, tf, is_buy, po3,
                             atr_pct, trend_strength, trend_slope_pct,
                             adx_value, adr_pct, session_vol_ratio,
                             vwap_dist_atr, compression_score, expansion_score,
                             news_risk, reason);
   }

   double _PlanRR2(const TradePlan &p) const {
      double risk = MathAbs(p.entry_est - p.sl);
      if(risk <= 0) return 0.0;
      return MathAbs(p.tp2 - p.entry_est) / risk;
   }

   double _ApproxAtrAbs(const TradePlan &p, const double entry_price) {
      if(entry_price > 0 && p.atr_pct > 0) return p.atr_pct * entry_price;
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(p.symbol, p.htf, 0, 80, rates);
      double atr = ATRFromRates(rates, got, 14);
      if(atr > 0) return atr;
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      return point * MathMax(10, InpMinStopTicks);
   }

   double _ApproxEntryAtrAbs(const TradePlan &p, const double entry_price) {
      ENUM_TIMEFRAMES tf = (p.ltf > 0 ? p.ltf : PO3EffectiveEntryTF());
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(p.symbol, tf, 0, 80, rates);
      double atr = ATRFromRates(rates, got, 14);
      if(atr > 0) return atr;
      if(entry_price > 0 && p.atr_pct > 0) return p.atr_pct * entry_price;
      return _ApproxAtrAbs(p, entry_price);
   }

   double _ApproxAdrAbs(const TradePlan &p, const double entry_price) {
      if(entry_price > 0 && p.adr_pct > 0) return p.adr_pct * entry_price;
      double adr_pct = AverageDailyRangePct(p.symbol, 20);
      if(entry_price > 0 && adr_pct > 0) return adr_pct * entry_price;
      return 0.0;
   }

   double _CurrentSpreadTicks(const string symbol) {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) return 0.0;
      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
      double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
      if(ask <= 0 || bid <= 0) return 0.0;
      return (ask - bid) / point;
   }

   double _CurrentSpreadPrice(const string symbol) {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      return _CurrentSpreadTicks(symbol) * point;
   }

   //+---------------------------------------------------------------+
   //| The absolute spread ceiling for one symbol.                     |
   //|                                                                 |
   //| InpMaxSpreadTicks alone cannot be this ceiling: it is a raw      |
   //| point count and point size spans five orders of magnitude across |
   //| this universe.  The price-fraction allowance is the scale-free    |
   //| floor under the ceiling, so no symbol can be made untradeable by  |
   //| its own quote precision; the tick cap survives and still binds    |
   //| wherever it is the larger of the two.                             |
   //+---------------------------------------------------------------+
   double _MaxSpreadPriceForSymbol(const string symbol, const double reference_price,
                                   string &cap_source) {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0.0) point = 0.00001;
      double tick_cap  = (InpMaxSpreadTicks > 0 ? (double)InpMaxSpreadTicks * point : 0.0);
      double price_cap = (InpMaxSpreadPriceFrac > 0.0 && reference_price > 0.0
                          ? reference_price * InpMaxSpreadPriceFrac : 0.0);
      if(tick_cap <= 0.0 && price_cap <= 0.0){ cap_source = "none"; return 0.0; }
      if(price_cap <= 0.0){ cap_source = "max_spread_ticks"; return tick_cap; }
      if(tick_cap <= 0.0){ cap_source = "max_spread_price_frac"; return price_cap; }
      if(tick_cap >= price_cap){ cap_source = "max_spread_ticks"; return tick_cap; }
      cap_source = "max_spread_price_frac";
      return price_cap;
   }

   //--- The whole spread decision, in one place, reported in every unit it is
   //--- judged in.  No journal line used to carry the absolute spread at all, so a
   //--- rejection that named only a raw point count could not be checked against
   //--- the cap that actually mattered without knowing the symbol's point size.
   bool _SpreadWithinLimits(const TradePlan &p, const double bid, const double ask,
                            const double planned_risk, string &reject_reason) {
      reject_reason = "";
      double spread = ask - bid;
      if(spread <= 0.0) return true;
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0.0) point = 0.00001;
      double reference_price = (p.entry_est > 0.0 ? p.entry_est : (bid + ask) * 0.5);
      string cap_source = "none";
      double abs_cap  = _MaxSpreadPriceForSymbol(p.symbol, reference_price, cap_source);
      double risk_cap = (InpMaxSpreadRiskFrac > 0.0 && planned_risk > 0.0
                         ? planned_risk * InpMaxSpreadRiskFrac : 0.0);
      bool abs_fail  = (abs_cap > 0.0 && spread > abs_cap);
      bool risk_fail = (risk_cap > 0.0 && spread > risk_cap);

      // Rejections always explain themselves; a pass only under verbose, because
      // this runs on every execution attempt.
      if(abs_fail || risk_fail || InpVerboseJournal)
         _Journal("[spread_gate] symbol=" + p.symbol
                  + " spread_price=" + _FmtPrice(p.symbol, spread)
                  + " spread_ticks=" + DoubleToString(spread / point, 1)
                  + " spread_frac_of_price=" + DoubleToString(reference_price > 0.0 ? spread / reference_price : 0.0, 8)
                  + " spread_frac_of_risk=" + DoubleToString(planned_risk > 0.0 ? spread / planned_risk : 0.0, 6)
                  + " abs_cap_price=" + _FmtPrice(p.symbol, abs_cap)
                  + " abs_cap_source=" + cap_source
                  + " risk_cap_price=" + _FmtPrice(p.symbol, risk_cap)
                  + " action=" + (abs_fail ? "reject_abs_cap" : (risk_fail ? "reject_risk_cap" : "pass")));

      if(abs_fail){
         // A stable token, deliberately.  Embedding the measured prices here would
         // give every rejection a unique reason string and shatter the funnel's
         // reject table into unbounded cardinality; the numbers belong in the
         // [spread_gate] line above, which carries all of them.
         reject_reason = "spread_above_symbol_cap";
         return false;
      }
      if(risk_fail){
         reject_reason = "spread too large relative to stop distance";
         return false;
      }
      return true;
   }

   double _LiveEntryPrice(const TradePlan &p) const {
      return SymbolInfoDouble(p.symbol, p.is_buy ? SYMBOL_ASK : SYMBOL_BID);
   }

   bool _EntryZoneTouched(const TradePlan &p, double &live_entry, double &tolerance) const {
      live_entry = _LiveEntryPrice(p);
      tolerance = 0.0;
      if(live_entry <= 0 || p.entry_est <= 0) return false;
      double risk = MathAbs(p.entry_est - p.sl);
      if(risk <= 0) return false;
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double tol_r = (_IsMicroFamily(p) ? MathMax(InpEntryZoneToleranceR, InpMicroEntryZoneToleranceR) : InpEntryZoneToleranceR);
      tolerance = MathMax(risk * MathMax(0.0, tol_r), point * MathMax(1, PO3EffectiveMinFvgWidthTicks()));
      return (MathAbs(live_entry - p.entry_est) <= tolerance);
   }

   double _PlannedEntryPrice(const TradePlan &p) {
      double entry_price = 0.0;
      string reason = "";
      if(_ResolveBranchEntryPrice(p, p.entry_branch, entry_price, reason)) return entry_price;
      if(InpRequireFvgMidMitigation) return p.fvg.mid;
      return (p.is_buy ? p.fvg.upper : p.fvg.lower);
   }

   double _BrokerBufferPrice(const string symbol) {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      int stops_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
      int freeze_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
      int total_pts = MathMax(stops_level, freeze_level) + MathMax(0, InpBrokerStopBufferPts);
      return total_pts * point;
   }

   double _ObstacleBufferPrice(const string symbol) const {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      return point * MathMax(0, InpObstacleBufferPts);
   }

   double _ExecutionRR2(const TradePlan &p) const {
      if(p.effective_rr2 > 0) return p.effective_rr2;
      return _PlanRR2(p);
   }

   string _StopModelName() const {
      if(InpStopModel == STOP_FVG_EDGE) return "fvg_edge";
      if(InpStopModel == STOP_STRUCTURAL_SWING) return "structural_swing";
      return "structural_sweep";
   }



   string _ExpectedDurationClass(const TradePlan &p, const double min_target_dist) const {
      int tf_seconds = PeriodSeconds(p.ltf > 0 ? p.ltf : PO3EffectiveEntryTF());
      int tf_minutes = (tf_seconds > 0 ? tf_seconds / 60 : 0);
      if(tf_minutes >= 15 && min_target_dist > 0 &&
         InpMinMinutesBeforeBE >= 45 && InpMinMinutesBeforePenaltyCuts >= 30)
         return "intraday_1h_plus";
      if(tf_minutes >= 5) return "intraday";
      return "scalp";
   }

   bool _BuildPlanReject(const TradePlan &p, const string reason,
                         const double entry_price, const double sl_price,
                         const double tp2_price) {
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double stop_pts = (entry_price > 0 && sl_price > 0 ? MathAbs(entry_price - sl_price) / point : 0.0);
      string extra = "";
      if(StringFind(reason, "target_too_close") >= 0){
         double reward = (entry_price > 0 && tp2_price > 0 ? _RewardToTarget(p.is_buy, entry_price, tp2_price) : 0.0);
         double min_target = (entry_price > 0 ? _MinSwingTargetDistance(p, entry_price) : 0.0);
         extra = " reward_points=" + DoubleToString(reward / point, 1)
                 + " min_target_points=" + DoubleToString(min_target / point, 1)
                 + " min_target_atr_mult=" + DoubleToString(InpMinTargetAtrMult, 3)
                 + " min_target_adr_frac=" + DoubleToString(InpMinTargetAdrFrac, 4);
      }
      _LogSetupReject(p.symbol, "plan_price", reason,
                      "entry=" + _FmtPrice(p.symbol, entry_price)
                      + " sl=" + _FmtPrice(p.symbol, sl_price)
                      + " tp2=" + _FmtPrice(p.symbol, tp2_price)
                      + " stop_distance_points=" + DoubleToString(stop_pts, 1)
                      + extra);
      _Journal(p.symbol + " plan rejected reject_code=" + _ReasonCode(reason)
               + " reason=" + reason
               + " entry=" + _FmtPrice(p.symbol, entry_price)
               + " sl=" + _FmtPrice(p.symbol, sl_price)
               + " tp2=" + _FmtPrice(p.symbol, tp2_price)
               + " stop_distance_points=" + DoubleToString(stop_pts, 1)
               + extra
               + " stop_model=" + _StopModelName()
               + " target_model=" + p.tp_model);
      return false;
   }

   void _LogBuildPlanAccepted(const TradePlan &p, const double min_target_dist) {
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double stop_dist = MathAbs(p.entry_est - p.sl);
      double htf_atr = _ApproxAtrAbs(p, p.entry_est);
      double entry_atr = _ApproxEntryAtrAbs(p, p.entry_est);
      double tp2_reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp2);
      double stop_atr = (htf_atr > 0 ? stop_dist / htf_atr : 0.0);
      double tp2_atr = (entry_atr > 0 ? tp2_reward / entry_atr : 0.0);
      double rr2 = (stop_dist > 0 ? tp2_reward / stop_dist : 0.0);
      _Journal(p.symbol + " plan accepted"
               + " entry=" + _FmtPrice(p.symbol, p.entry_est)
               + " sl=" + _FmtPrice(p.symbol, p.sl)
               + " tp1=" + _FmtPrice(p.symbol, p.tp1)
               + " tp2=" + _FmtPrice(p.symbol, p.tp2)
               + " stop_distance_points=" + DoubleToString(stop_dist / point, 1)
               + " stop_distance_atr=" + DoubleToString(stop_atr, 2)
               + " tp2_distance_atr=" + DoubleToString(tp2_atr, 2)
               + " rr2=" + DoubleToString(rr2, 2)
               + " setup_family=" + p.setup_family
               + " heuristic_quality_estimate=" + DoubleToString(p.heuristic_quality_estimate, 3)
               + " net_reward_after_cost_r=" + DoubleToString(p.net_reward_after_cost_r, 3)
               + " execution_cost_r=" + DoubleToString(p.execution_cost_r, 3)
               + " slippage_r=" + DoubleToString(p.slippage_r, 3)
               + " commission_r=" + DoubleToString(p.commission_r, 3)
               + " management_profile=" + p.management_profile
               + " stop_model=" + _StopModelName()
               + " tp_model=" + p.tp_model
               + " target_source=" + p.target_source
               + " expected_duration_class=" + _ExpectedDurationClass(p, min_target_dist));
   }

   bool _IsRewardSideLevel(const bool is_buy, const double entry_price, const double level) const {
      if(entry_price <= 0 || level <= 0) return false;
      if(is_buy) return (level > entry_price);
      return (level < entry_price);
   }

   bool _IsLevelBeforeTarget(const bool is_buy, const double entry_price, const double target_price, const double level) const {
      if(!_IsRewardSideLevel(is_buy, entry_price, level)) return false;
      if(is_buy) return (level < target_price);
      return (level > target_price);
   }

   void _PushPriceLevel(PriceLevelCandidate &levels[], const string kind, const double price,
                        const int priority, const bool obstacle,
                        const bool is_buy, const double entry_price) const {
      if(!_IsRewardSideLevel(is_buy, entry_price, price)) return;
      for(int i=0; i<ArraySize(levels); i++){
         if(levels[i].kind == kind && MathAbs(levels[i].price - price) <= MathMax(0.0000001, MathAbs(price) * 0.00001)) return;
      }
      int n = ArraySize(levels);
      ArrayResize(levels, n + 1);
      levels[n].kind = kind;
      levels[n].price = price;
      levels[n].priority = priority;
      levels[n].obstacle = obstacle;
   }

   void _SortPriceLevels(PriceLevelCandidate &levels[], const bool is_buy, const double entry_price) const {
      int n = ArraySize(levels);
      for(int i=0; i<n-1; i++){
         int best = i;
         for(int j=i+1; j<n; j++){
            double dist_best = MathAbs(levels[best].price - entry_price);
            double dist_j = MathAbs(levels[j].price - entry_price);
            bool better = false;
            if(levels[j].priority < levels[best].priority) better = true;
            else if(levels[j].priority == levels[best].priority && dist_j < dist_best) better = true;
            if(better) best = j;
         }
         if(best != i){
            PriceLevelCandidate tmp = levels[i];
            levels[i] = levels[best];
            levels[best] = tmp;
         }
      }
   }

   bool _FindNearestImbalanceBoundary(const string symbol, const ENUM_TIMEFRAMES tf, const bool is_buy,
                                      const double entry_price, double &out_price, string &out_kind) const {
      out_price = 0.0;
      out_kind = "";
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, tf, 0, MathMax(160, InpHTFFvgLookbackBars), rates);
      if(got < 10) return false;

      double best_dist = DBL_MAX;
      for(int idx=1; idx<got-2; idx++){
         int older = idx + 2;
         int newer = idx;
         if(older >= got || newer >= got) continue;

         bool bull = (rates[older].high < rates[newer].low);
         bool bear = (rates[older].low > rates[newer].high);
         if(is_buy && !bear) continue;
         if(!is_buy && !bull) continue;

         double lower = bear ? rates[newer].high : rates[older].high;
         double upper = bear ? rates[older].low : rates[newer].low;
         if(upper <= lower) continue;

         double boundary = (is_buy ? lower : upper);
         if(!_IsRewardSideLevel(is_buy, entry_price, boundary)) continue;
         double dist = MathAbs(boundary - entry_price);
         if(dist < best_dist){
            best_dist = dist;
            out_price = boundary;
            out_kind = (tf == PO3EffectiveHTF() ? "htf_opposing_imbalance" : "opposing_imbalance");
         }
      }
      return (out_price > 0);
   }

   bool _FindNearestOpposingFvgBoundary(const string symbol, const bool is_buy,
                                        const double entry_price, double &out_price, string &out_kind) const {
      return _FindNearestImbalanceBoundary(symbol, PO3EffectiveEntryTF(), is_buy, entry_price, out_price, out_kind);
   }

   void _CollectTargetLevels(const TradePlan &p, const double entry_price,
                             PriceLevelCandidate &targets[], PriceLevelCandidate &obstacles[]) const {
      ArrayResize(targets, 0);
      ArrayResize(obstacles, 0);

      if(InpTPPreferLiquidityTarget && p.po3.liquidity_target > 0){
         string liq_kind = (StringLen(p.po3.liquidity_kind) > 0 ? p.po3.liquidity_kind : "liquidity_target");
         _PushPriceLevel(targets, liq_kind, p.po3.liquidity_target, 1, false, p.is_buy, entry_price);
      }

      double level = 0.0;
      string kind = "";
      if(_FindNearestImbalanceBoundary(p.symbol, p.htf, p.is_buy, entry_price, level, kind)){
         _PushPriceLevel(targets, kind, level, 2, false, p.is_buy, entry_price);
         _PushPriceLevel(obstacles, kind, level, 1, true, p.is_buy, entry_price);
      }
      if(_FindNearestOpposingFvgBoundary(p.symbol, p.is_buy, entry_price, level, kind)){
         _PushPriceLevel(targets, kind, level, 3, false, p.is_buy, entry_price);
         _PushPriceLevel(obstacles, kind, level, 2, true, p.is_buy, entry_price);
      }

      _PushPriceLevel(targets, (p.is_buy ? "session_high" : "session_low"),
                      (p.is_buy ? p.po3.session_high : p.po3.session_low), 4, false, p.is_buy, entry_price);
      _PushPriceLevel(targets, (p.is_buy ? "asia_session_high" : "asia_session_low"),
                      (p.is_buy ? p.po3.asia_high : p.po3.asia_low), 4, false, p.is_buy, entry_price);
      _PushPriceLevel(targets, (p.is_buy ? "london_session_high" : "london_session_low"),
                      (p.is_buy ? p.po3.london_high : p.po3.london_low), 4, false, p.is_buy, entry_price);
      _PushPriceLevel(targets, (p.is_buy ? "newyork_session_high" : "newyork_session_low"),
                      (p.is_buy ? p.po3.newyork_high : p.po3.newyork_low), 4, false, p.is_buy, entry_price);
      _PushPriceLevel(targets, (p.is_buy ? "prev_day_high" : "prev_day_low"),
                      (p.is_buy ? p.po3.prev_day_high : p.po3.prev_day_low), 5, false, p.is_buy, entry_price);
      _PushPriceLevel(targets, (p.is_buy ? "prev_week_high" : "prev_week_low"),
                      (p.is_buy ? p.po3.prev_week_high : p.po3.prev_week_low), 5, false, p.is_buy, entry_price);

      _PushPriceLevel(obstacles, (p.is_buy ? "session_high" : "session_low"),
                      (p.is_buy ? p.po3.session_high : p.po3.session_low), 3, true, p.is_buy, entry_price);
      _PushPriceLevel(obstacles, (p.is_buy ? "prev_day_high" : "prev_day_low"),
                      (p.is_buy ? p.po3.prev_day_high : p.po3.prev_day_low), 4, true, p.is_buy, entry_price);
      _PushPriceLevel(obstacles, (p.is_buy ? "prev_week_high" : "prev_week_low"),
                      (p.is_buy ? p.po3.prev_week_high : p.po3.prev_week_low), 5, true, p.is_buy, entry_price);

      _SortPriceLevels(targets, p.is_buy, entry_price);
      _SortPriceLevels(obstacles, p.is_buy, entry_price);
   }

   bool _NearestObstacleBeforeTarget(const TradePlan &p, const double entry_price, const double target_price,
                                     const PriceLevelCandidate &obstacles[], string &out_kind,
                                     double &out_price, double &out_effective_target) const {
      out_kind = "";
      out_price = 0.0;
      out_effective_target = target_price;
      double best_dist = DBL_MAX;
      double buffer = _ObstacleBufferPrice(p.symbol);

      for(int i=0; i<ArraySize(obstacles); i++){
         double level = obstacles[i].price;
         if(!_IsLevelBeforeTarget(p.is_buy, entry_price, target_price, level)) continue;
         double dist = MathAbs(level - entry_price);
         if(dist < best_dist){
            best_dist = dist;
            out_kind = obstacles[i].kind;
            out_price = level;
         }
      }

      if(out_price <= 0) return false;
      out_effective_target = (p.is_buy ? out_price - buffer : out_price + buffer);
      if(!_IsRewardSideLevel(p.is_buy, entry_price, out_effective_target)) out_effective_target = out_price;
      return true;
   }

   //+---------------------------------------------------------------+
   //| The WORST blocker on a route, as opposed to the nearest one.    |
   //|                                                                 |
   //| _NearestObstacleBeforeTarget ranks purely by distance, and its  |
   //| answer is what gets published as obstacle_kind, scored into     |
   //| obstacle_severity, shown to the model as the route's blocker    |
   //| and fed to the killer-severity policy.  A mild level sitting in  |
   //| front of a severe one therefore MASKS it: #Germany40 on         |
   //| 2026-09-07 was sent to the model as a session high of severity  |
   //| 1.50 (pure distance bonus -- session_high scores 0.0 on its own) |
   //| while an opposing imbalance of severity 5.50 sat further along  |
   //| the very same route to 26452.90.                                 |
   //|                                                                 |
   //| Reporting only for now.  Making this the PUBLISHED blocker      |
   //| changes p.obstacle_kind, which is an input of _CandidateHash and |
   //| of _TesterAiCacheSignature, so it retires every recorded replay  |
   //| artifact and has to be sequenced with a cohort re-record -- the  |
   //| same call already made for the ai_selected_* naming fix.         |
   //+---------------------------------------------------------------+
   bool _WorstObstacleBeforeTarget(const TradePlan &p, const double entry_price, const double target_price,
                                   const double stop_dist, const PriceLevelCandidate &obstacles[],
                                   string &out_kind, double &out_price, double &out_severity) const {
      out_kind = "";
      out_price = 0.0;
      out_severity = 0.0;
      double best_severity = -1.0;
      double best_dist = DBL_MAX;
      for(int i=0; i<ArraySize(obstacles); i++){
         double level = obstacles[i].price;
         if(!_IsLevelBeforeTarget(p.is_buy, entry_price, target_price, level)) continue;
         double dist = MathAbs(level - entry_price);
         double distance_r = (stop_dist > 0.0 ? dist / stop_dist : 0.0);
         double severity = _ObstacleSeverity(obstacles[i].kind, distance_r);
         if(severity > best_severity + 0.0001 ||
            (MathAbs(severity - best_severity) <= 0.0001 && dist < best_dist)){
            best_severity = severity;
            best_dist = dist;
            out_kind = obstacles[i].kind;
            out_price = level;
            out_severity = severity;
         }
      }
      return (out_price > 0.0);
   }

   //--- Report, without changing any identity field, when the published (nearest)
   //--- blocker understates what the route actually has to cross.
   void _ReportMaskedRouteObstacle(const TradePlan &p, const double entry_price, const double target_price,
                                   const double stop_dist, const PriceLevelCandidate &obstacles[],
                                   const string published_kind, const double published_severity) const {
      static int logged = 0;
      if(logged >= 200) return;
      string worst_kind = "";
      double worst_price = 0.0, worst_severity = 0.0;
      if(!_WorstObstacleBeforeTarget(p, entry_price, target_price, stop_dist, obstacles,
                                     worst_kind, worst_price, worst_severity)) return;
      if(worst_severity <= published_severity + 0.0001) return;
      logged++;
      _Journal("[obstacle_route_scan] symbol=" + p.symbol
               + " entry=" + _FmtPrice(p.symbol, entry_price)
               + " target=" + _FmtPrice(p.symbol, target_price)
               + " published_kind=" + (StringLen(published_kind) > 0 ? published_kind : "none")
               + " published_severity=" + DoubleToString(published_severity, 2)
               + " worst_kind=" + worst_kind
               + " worst_price=" + _FmtPrice(p.symbol, worst_price)
               + " worst_severity=" + DoubleToString(worst_severity, 2)
               + " worst_class=" + _ObstacleSeverityClass(worst_severity)
               + " action=masked_by_nearer_blocker_reported_only");
   }

   bool _HasOpposingObstacleBeforeTarget(const TradePlan &p, const double entry_price, const double target_price,
                                         const PriceLevelCandidate &obstacles[], string &out_kind,
                                         double &out_price) const {
      out_kind = "";
      out_price = 0.0;
      double best_dist = DBL_MAX;
      for(int i=0; i<ArraySize(obstacles); i++){
         string kind = obstacles[i].kind;
         bool opposing = (StringFind(kind, "imbalance") >= 0 || StringFind(kind, "opposing") >= 0);
         if(!opposing) continue;
         double level = obstacles[i].price;
         if(!_IsLevelBeforeTarget(p.is_buy, entry_price, target_price, level)) continue;
         double dist = MathAbs(level - entry_price);
         if(dist < best_dist){
            best_dist = dist;
            out_kind = kind;
            out_price = level;
         }
      }
      return (out_price > 0.0);
   }

   void _ResetTargetArbitrationFields(TradePlan &p) {
      p.target_arbitration_required = false;
      p.liquidity_target_preserved = 0.0;
      p.liquidity_target_model = "";
      p.liquidity_target_valid_structurally = false;
      p.liquidity_target_blocked_by_obstacle = false;
      p.obstacle_distance_r = 0.0;
      p.obstacle_tf = "";
      p.obstacle_severity = 0.0;
      p.obstacle_strength_features = "";
      p.fallback_tp = 0.0;
      p.fallback_rr = 0.0;
      p.fallback_source = "";
      p.fallback_feasible_for_tp2 = false;
      p.fallback_feasible_for_tp1_only = false;
      p.fallback_infeasible_reason = "";
      p.fallback_reward_distance = 0.0;
      p.fallback_max_allowed_distance = 0.0;
      p.synthetic_capped_to_max_distance_tp = 0.0;
      p.synthetic_capped_to_max_distance_rr = 0.0;
      p.synthetic_capped_to_max_distance_feasible = false;
      p.synthetic_capped_to_max_distance_reason = "";
      p.capped_before_obstacle_tp = 0.0;
      p.capped_before_obstacle_rr = 0.0;
      p.capped_before_obstacle_source = "";
      p.original_planned_tp_before_ai = 0.0;
      p.original_planned_rr_before_ai = 0.0;
   }

   string _CrossedObstacleKind(const string obstacle_kind) const {
      if(StringLen(obstacle_kind) == 0) return "";
      if(StringFind(obstacle_kind, "crossed_") == 0) return obstacle_kind;
      return "crossed_" + obstacle_kind;
   }

   //+---------------------------------------------------------------+
   //| The obstacle's identity, with the crossing state removed.      |
   //|                                                                |
   //| "crossed_" is not part of what the obstacle IS.  It records    |
   //| where the live price sits relative to it, and                  |
   //| _PublishObstacleEvidence re-derives it on every rebuild, so it  |
   //| is guaranteed to flip exactly when price travels to the entry   |
   //| zone -- which is the movement the watchlist exists to wait for. |
   //| Comparing the prefixed string as immutable identity therefore   |
   //| rejects a plan for doing what it was armed to do.  The base     |
   //| kind is the identity and stays immutable.                       |
   //+---------------------------------------------------------------+
   string _BaseObstacleKind(const string obstacle_kind) const {
      if(StringLen(obstacle_kind) == 0) return "";
      if(StringFind(obstacle_kind, "crossed_") == 0)
         return StringSubstr(obstacle_kind, 8);
      return obstacle_kind;
   }

   bool _ObstacleIsCrossed(const string obstacle_kind) const {
      return (StringLen(obstacle_kind) > 0 && StringFind(obstacle_kind, "crossed_") == 0);
   }

   //+---------------------------------------------------------------+
   //| The obstacle's identity with the timeframe qualifier removed.  |
   //|                                                                |
   //| _PublishObstacleEvidence derives obstacle_tf from this very     |
   //| string ("htf" in the kind), and the execution contract already  |
   //| classifies an obstacle_tf change as AUTHORIZED.  Leaving "htf_" |
   //| inside the kind therefore made one and the same timeframe both  |
   //| immutable and authorized at the same time: GBPCHF was killed on |
   //| 2026-09-06 for reporting crossed_htf_opposing_imbalance at      |
   //| assessment and crossed_opposing_imbalance at execution -- the   |
   //| same obstacle, spelled at two levels of detail.  The severity   |
   //| difference the qualifier carries is not lost: it is compared    |
   //| numerically by _ObstacleIdentityPreserved instead.               |
   //+---------------------------------------------------------------+
   string _ObstacleKindWithoutTf(const string base_kind) const {
      if(StringFind(base_kind, "htf_") == 0) return StringSubstr(base_kind, 4);
      if(StringFind(base_kind, "ltf_") == 0) return StringSubstr(base_kind, 4);
      return base_kind;
   }

   //--- The severity to judge an obstacle by.  A stored numeric wins; otherwise the
   //--- kind alone is scored, with no distance bonus.  That is a LOWER bound on the
   //--- real severity, so a missing number can only ever make the comparison
   //--- stricter -- never permissive.  Plans restored from a state file written by
   //--- a build without the field land here.
   double _EffectiveObstacleSeverity(const string kind, const double stored) const {
      if(stored > 0.0) return stored;
      if(StringLen(kind) == 0) return 0.0;
      return _ObstacleSeverity(kind, 0.0);
   }

   //--- Which obstacle the contract comparison is about.  While the plan is locked
   //--- the engine-owned fields hold the APPROVED obstacle (frozen by
   //--- _PublishObstacleEvidence), and what the live scan saw sits in live_obstacle_*.
   //--- Comparing the frozen value against itself would make the check vacuous, so
   //--- the live observation is what gets judged whenever one exists.
   bool _HasLiveObstacleObservation(const TradePlan &p) const {
      return (p.assessed_plan_locked && StringLen(p.live_obstacle_kind) > 0);
   }
   string _LiveObstacleKindForComparison(const TradePlan &p) const {
      return (_HasLiveObstacleObservation(p) ? p.live_obstacle_kind : p.obstacle_kind);
   }
   string _LiveObstacleTfForComparison(const TradePlan &p) const {
      if(!_HasLiveObstacleObservation(p)) return p.obstacle_tf;
      return (StringFind(p.live_obstacle_kind, "htf") >= 0 ? "htf" : "entry_tf");
   }
   double _LiveObstacleSeverityForComparison(const TradePlan &p) const {
      return (_HasLiveObstacleObservation(p) ? p.live_obstacle_severity : p.obstacle_severity);
   }
   double _LiveObstaclePriceForComparison(const TradePlan &p) const {
      return (_HasLiveObstacleObservation(p) ? p.live_obstacle_price : p.obstacle_price);
   }

   //+---------------------------------------------------------------+
   //| Re-observe a locked plan's blocker ON THE ROUTE THAT WAS        |
   //| APPROVED, not on the drifted live geometry.                     |
   //|                                                                 |
   //| The live rebuild scans the landscape from p.entry_est.  The     |
   //| contract authorises the entry to drift and bounds it separately  |
   //| (max_entry_drift_r), so once price walks into the entry zone the |
   //| nearest obstacle simply falls BEHIND the live entry and drops    |
   //| out of the scan -- and the next blocker along the very same      |
   //| route is then reported as a brand new, more severe one.          |
   //| #Germany40 on 2026-09-07 was lost exactly that way, inside the   |
   //| SAME simulated second as its approval and with bars_waited=0:    |
   //|   assessed crossed_session_high @26206.6 severity 1.50           |
   //|   live     crossed_opposing_imbalance      severity 5.50         |
   //| after the entry moved 26193.60 -> 26207.10, one tick past the    |
   //| session high.  Nothing about the trade changed; only the point   |
   //| the question was asked from.  This is the same defect class as   |
   //| the first-leg floor, resolved the same way (_Tp1FloorStopDistance |
   //| / _Tp1GeometryLegReward): a property OF THE APPROVED PLAN is     |
   //| measured on the approved plan.                                   |
   //|                                                                 |
   //| Nothing is loosened.  A blocker that is genuinely new on the     |
   //| approved route, or genuinely more severe on it, still fails      |
   //| closed through _ObstacleIdentityPreserved, and the entry drift   |
   //| itself is still bounded and separately validated.                |
   //+---------------------------------------------------------------+
   void _ObserveLiveObstacleOnApprovedRoute(TradePlan &p) {
      if(!p.assessed_plan_locked) return;
      if(p.assessed_entry <= 0.0 || p.assessed_tp2 <= 0.0) return;
      double approved_stop = p.assessed_stop_distance;
      if(approved_stop <= 0.0) approved_stop = MathAbs(p.assessed_entry - p.assessed_sl);
      if(approved_stop <= 0.0) return;

      PriceLevelCandidate approved_targets[];
      PriceLevelCandidate approved_obstacles[];
      _CollectTargetLevels(p, p.assessed_entry, approved_targets, approved_obstacles);

      string kind = "";
      double price = 0.0, effective_target = 0.0;
      bool found = _NearestObstacleBeforeTarget(p, p.assessed_entry, p.assessed_tp2,
                                                approved_obstacles, kind, price, effective_target);
      // Always replace the observation: whatever the live rebuild happened to write
      // from the drifted entry is not the question the contract is asking.
      p.live_obstacle_kind = "";
      p.live_obstacle_price = 0.0;
      p.live_obstacle_severity = 0.0;
      if(!found){
         _JournalDetail(1, "[obstacle_route_observation] symbol=" + p.symbol
                        + " approved_entry=" + _FmtPrice(p.symbol, p.assessed_entry)
                        + " approved_tp2=" + _FmtPrice(p.symbol, p.assessed_tp2)
                        + " assessed_kind=" + (StringLen(p.assessed_obstacle_kind) > 0 ? p.assessed_obstacle_kind : "none")
                        + " live_kind=none action=no_obstacle_on_approved_route");
         return;
      }
      _PublishObstacleEvidence(p, kind, price, approved_stop, true, p.assessed_entry);
      _JournalDetail(1, "[obstacle_route_observation] symbol=" + p.symbol
                     + " approved_entry=" + _FmtPrice(p.symbol, p.assessed_entry)
                     + " live_entry=" + _FmtPrice(p.symbol, p.entry_est)
                     + " approved_tp2=" + _FmtPrice(p.symbol, p.assessed_tp2)
                     + " approved_stop=" + _FmtPrice(p.symbol, approved_stop)
                     + " assessed_kind=" + (StringLen(p.assessed_obstacle_kind) > 0 ? p.assessed_obstacle_kind : "none")
                     + " live_kind=" + p.live_obstacle_kind
                     + " live_price=" + _FmtPrice(p.symbol, p.live_obstacle_price)
                     + " live_severity=" + DoubleToString(p.live_obstacle_severity, 2)
                     + " action=observed_on_approved_route");
   }

   //--- Classify a live obstacle against its assessed one.  Identity is the base
   //--- kind without crossing state and without the timeframe qualifier; both of
   //--- those are carried in their own fields and are authorized to move.  A
   //--- genuinely different blocker is accepted only while it is not a WORSE one,
   //--- which is the property the AI actually reasoned about.
   bool _ObstacleIdentityPreserved(const string live_kind, const string live_tf,
                                   const double live_severity_stored,
                                   const string assessed_kind, const string assessed_tf,
                                   const double assessed_severity_stored,
                                   bool &crossing_changed, bool &tf_changed,
                                   bool &label_changed, double &live_severity,
                                   double &assessed_severity) const {
      crossing_changed = false;
      tf_changed = false;
      label_changed = false;
      live_severity     = _EffectiveObstacleSeverity(live_kind, live_severity_stored);
      assessed_severity = _EffectiveObstacleSeverity(assessed_kind, assessed_severity_stored);
      string live_base     = _ObstacleKindWithoutTf(_BaseObstacleKind(live_kind));
      string assessed_base = _ObstacleKindWithoutTf(_BaseObstacleKind(assessed_kind));
      crossing_changed = (_ObstacleIsCrossed(live_kind) != _ObstacleIsCrossed(assessed_kind));
      tf_changed       = (live_tf != assessed_tf);
      label_changed    = (live_base != assessed_base);
      if(!label_changed) return true;
      // Different blocker: allowed only when it is no more severe than the approved
      // one.  Fails closed when the live severity is unknown but the kind is not.
      return (live_severity <= assessed_severity + 0.0001);
   }

   double _TargetRR(const TradePlan &p, const double target_price) const {
      double risk = MathAbs(p.entry_est - p.sl);
      if(risk <= 0.0 || target_price <= 0.0) return 0.0;
      double reward = (p.is_buy ? target_price - p.entry_est : p.entry_est - target_price);
      if(reward <= 0.0) return 0.0;
      return reward / risk;
   }

   double _RREps() const {
      return 0.0001;
   }

   bool _RRMeetsFloor(const double rr, const double min_rr) const {
      return (rr + _RREps() >= min_rr);
   }

   double _EffectiveFallbackRR() const {
      return MathMax(InpFallbackRR2, InpMinLiveRR2 + MathMax(0.0, InpFallbackRRBufferR));
   }

   double _TpFromReward(const TradePlan &p, const double reward) const {
      if(reward <= 0.0) return 0.0;
      return (p.is_buy ? p.entry_est + reward : p.entry_est - reward);
   }

   bool _RecomputeSyntheticFallbackTarget(TradePlan &p, const string reason) {
      if(!_PlanUsesSyntheticFallback(p)) return true;
      double risk = MathAbs(p.entry_est - p.sl);
      if(risk <= 0.0 || p.entry_est <= 0.0 || p.sl <= 0.0) return false;
      double effective_rr = _EffectiveFallbackRR();
      if(InpMaxPlanRR2 > 0.0) effective_rr = MathMin(effective_rr, InpMaxPlanRR2);
      double old_entry = p.entry_est;
      double old_tp = p.tp2;
      double old_rr = _ExecutionRR2(p);
      double new_tp = _TpFromReward(p, risk * effective_rr);
      double new_rr = _TargetRR(p, new_tp);
      p.tp2 = new_tp;
      p.fallback_tp = new_tp;
      p.fallback_rr = new_rr;
      p.fallback_source = "synthetic_rr_fallback";
      p.tp_model = "synthetic_rr_fallback";
      p.target_model = "synthetic_rr_fallback";
      if(StringFind(_NormToken(p.target_source), "ai_selected") >= 0 || StringLen(p.ai_chosen_target_model) > 0)
         p.target_source = "ai_selected_synthetic_rr_fallback";
      else
         p.target_source = "synthetic_rr_fallback";
      p.effective_rr2 = new_rr;
      p.ai_chosen_tp2 = new_tp;
      p.ai_chosen_rr2 = new_rr;
      _RefreshTargetFeasibility(p, reason);
      if(!p.fallback_feasible_for_tp2 &&
         p.fallback_infeasible_reason == "exceeds_max_target_distance" &&
         p.synthetic_capped_to_max_distance_feasible){
         p.tp2 = p.synthetic_capped_to_max_distance_tp;
         p.fallback_tp = p.tp2;
         p.fallback_rr = p.synthetic_capped_to_max_distance_rr;
         p.fallback_source = "synthetic_rr_capped_to_max_distance";
         p.tp_model = "synthetic_rr_capped_to_max_distance";
         p.target_model = "synthetic_rr_capped_to_max_distance";
         p.target_source = "ai_selected_synthetic_rr_capped_to_max_distance";
         p.effective_rr2 = p.synthetic_capped_to_max_distance_rr;
         p.ai_chosen_target_model = "synthetic_rr_capped_to_max_distance";
         p.ai_chosen_tp2 = p.tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
         _Journal("[target_rebuild_downgrade] from=synthetic_rr_fallback"
                  + " to=synthetic_rr_capped_to_max_distance"
                  + " reason=max_distance_after_entry_change"
                  + " old_entry=" + _FmtPrice(p.symbol, old_entry)
                  + " new_entry=" + _FmtPrice(p.symbol, p.entry_est)
                  + " old_tp=" + _FmtPrice(p.symbol, old_tp)
                  + " new_tp=" + _FmtPrice(p.symbol, p.tp2)
                  + " old_rr=" + DoubleToString(old_rr, 6)
                  + " new_rr=" + DoubleToString(p.effective_rr2, 6));
      }
      _Journal("[target_rebuild_feasibility] old_entry=" + _FmtPrice(p.symbol, old_entry)
               + " new_entry=" + _FmtPrice(p.symbol, p.entry_est)
               + " chosen=" + p.target_model
               + " old_rr=" + DoubleToString(old_rr, 6)
               + " new_rr=" + DoubleToString(_ExecutionRR2(p), 6)
               + " feasible=" + ((p.fallback_feasible_for_tp2 || p.synthetic_capped_to_max_distance_feasible) ? "true" : "false")
               + " reason=" + (p.fallback_feasible_for_tp2 ? "ok" : p.fallback_infeasible_reason));
      _Journal("[fallback_recalc] reason=" + reason
               + " old_entry=" + _FmtPrice(p.symbol, old_entry)
               + " new_entry=" + _FmtPrice(p.symbol, p.entry_est)
               + " old_tp=" + _FmtPrice(p.symbol, old_tp)
               + " new_tp=" + _FmtPrice(p.symbol, p.tp2)
               + " old_rr=" + DoubleToString(old_rr, 6)
               + " new_rr=" + DoubleToString(_ExecutionRR2(p), 6)
               + " effective_fallback_rr=" + DoubleToString(effective_rr, 6));
      return (p.tp2 > 0.0 && _ExecutionRR2(p) > 0.0);
   }

   double _ObstacleSeverity(const string obstacle_kind, const double obstacle_r) const {
      string kind = _NormToken(obstacle_kind);
      double severity = 0.0;
      if(StringFind(kind, "htf") >= 0) severity += 3.0;
      if(StringFind(kind, "opposing") >= 0) severity += 2.0;
      if(StringFind(kind, "imbalance") >= 0) severity += 2.0;
      if(StringFind(kind, "crossed") >= 0) severity += 1.5;
      if(obstacle_r > 0.0){
         if(obstacle_r <= InpObstacleRejectR) severity += 1.5;
         else if(obstacle_r <= InpObstacleRejectR * 1.5) severity += 0.8;
      }
      if(severity > 10.0) severity = 10.0;
      return severity;
   }

   string _ObstacleSeverityClass(const double severity) const {
      if(severity >= InpBlockerKillSeverity) return "killer";
      if(severity >= InpBlockerMajorSeverity) return "major";
      if(severity > InpBlockerMinorMaxSeverity) return "moderate";
      if(severity > 0.0) return "minor";
      return "none";
   }

   // Every consumer of obstacle evidence -- the ranking, the AI target-candidate payload
   // and the model's own veto reasoning -- reads these fields together.  They used to be
   // written in one branch only (an obstacle found before the *liquidity* target), so a
   // plan with no valid liquidity target shipped obstacle_kind with
   // obstacle_strength_features EMPTY -- asking the model to classify an obstacle's
   // strength with the strength blank, which its own contract forbids.  One writer, so
   // the kind and its severity can never travel apart again.
   // `mark_crossed` distinguishes a route that passes THROUGH the obstacle from one that
   // stops in front of it; only the former earns the "crossed_" label.
   // `anchor_entry` is the entry the obstacle's distance is measured from.  It is the
   // live entry for a plan still being built; for the live re-observation of a LOCKED
   // plan it is the approved entry, because severity carries a distance bonus and an
   // authorized entry drift must not be able to inflate it.
   void _PublishObstacleEvidence(TradePlan &p, const string obstacle_kind,
                                 const double obstacle_price, const double stop_dist,
                                 const bool mark_crossed, const double anchor_entry = 0.0) {
      if(StringLen(obstacle_kind) == 0 || obstacle_price <= 0.0) return;
      double entry_ref = (anchor_entry > 0.0 ? anchor_entry : p.entry_est);
      double distance_r = (stop_dist > 0.0 ? MathAbs(obstacle_price - entry_ref) / stop_dist : 0.0);
      double severity = _ObstacleSeverity(obstacle_kind, distance_r);
      // An approved plan owns its obstacle evidence exactly as it already owns its
      // target identity (_ApplyAssessedTargetUnderContract) and its first leg
      // (tp1_from_target_model).  The live rebuild still runs the whole landscape
      // scan -- it must, to revalidate the approved target -- but whichever obstacle
      // that scan happens to surface must not overwrite the one the AI was shown and
      // the assessment fingerprint froze.  #Germany40 and GBPCHF were both lost this
      // way on 2026-09-06: identical route, identical tp2, identical target model,
      // and the rebuild published a different blocker inside the same simulated
      // instant, so the plan died as execution_fingerprint_mismatch:obstacle_kind.
      // The live observation is kept and judged by the same comparison, through
      // _LiveObstacleKindForComparison, which fails closed on a genuinely worse
      // blocker -- a stronger test than a string equality, because it is about
      // severity rather than vocabulary.
      if(p.assessed_plan_locked){
         p.live_obstacle_kind     = (mark_crossed ? _CrossedObstacleKind(obstacle_kind) : obstacle_kind);
         p.live_obstacle_price    = obstacle_price;
         p.live_obstacle_severity = severity;
         return;
      }
      p.obstacle_kind = (mark_crossed ? _CrossedObstacleKind(obstacle_kind) : obstacle_kind);
      p.obstacle_price = obstacle_price;
      p.obstacle_distance_r = distance_r;
      p.obstacle_tf = (StringFind(obstacle_kind, "htf") >= 0 ? "htf" : "entry_tf");
      p.obstacle_severity = severity;
      p.obstacle_strength_features = "severity=" + DoubleToString(severity, 2)
                                   + ";class=" + _ObstacleSeverityClass(severity);
   }

   void _SeedTargetArbitrationCandidates(TradePlan &p, const double stop_dist,
                                         const PriceLevelCandidate &obstacles[]) {
      if(p.po3.liquidity_target <= 0.0 || !_IsRewardSideLevel(p.is_buy, p.entry_est, p.po3.liquidity_target))
         return;

      p.liquidity_target_preserved = p.po3.liquidity_target;
      p.liquidity_target_model = (StringLen(p.target_model) > 0 ? p.target_model :
                                  (StringLen(p.po3.liquidity_kind) > 0 ? p.po3.liquidity_kind : "liquidity_target"));
      p.liquidity_rr = _TargetRR(p, p.liquidity_target_preserved);
      p.liquidity_target_valid_structurally = (p.liquidity_rr > 0.0);

      string obstacle_kind = "";
      double obstacle_price = 0.0;
      double effective_target = p.liquidity_target_preserved;
      if(_NearestObstacleBeforeTarget(p, p.entry_est, p.liquidity_target_preserved, obstacles,
                                      obstacle_kind, obstacle_price, effective_target)){
         p.liquidity_target_blocked_by_obstacle = true;
         _PublishObstacleEvidence(p, obstacle_kind, obstacle_price, stop_dist, true);
         p.obstacle_r = p.obstacle_distance_r;
         // Measure, without changing anything, how often the nearest blocker hides a
         // worse one on the same route.  See _WorstObstacleBeforeTarget.
         if(!p.assessed_plan_locked)
            _ReportMaskedRouteObstacle(p, p.entry_est, p.liquidity_target_preserved, stop_dist,
                                       obstacles, p.obstacle_kind, p.obstacle_severity);

         double capped_rr = _TargetRR(p, effective_target);
         if(capped_rr > 0.0){
            p.capped_before_obstacle_tp = effective_target;
            p.capped_before_obstacle_rr = capped_rr;
            p.capped_before_obstacle_source = "capped_before_" + obstacle_kind;
         }
      }
   }

   bool _CanAskAiForTargetArbitration(const TradePlan &p) const {
      if(!InpRequireAITargetArbitrationOnObstacle || InpHardRejectCrossedObstacleTarget)
         return false;
      string obstacle = _NormToken(p.obstacle_kind);
      bool obstacle_requires = (StringLen(obstacle) > 0 &&
                                (StringFind(obstacle, "crossed_opposing_imbalance") >= 0 ||
                                 StringFind(obstacle, "crossed_htf_opposing_imbalance") >= 0 ||
                                 StringFind(obstacle, "opposing_imbalance") >= 0 ||
                                 StringFind(obstacle, "opposing") >= 0 ||
                                 StringFind(obstacle, "imbalance") >= 0));
      if(p.liquidity_target_valid_structurally && p.liquidity_target_preserved > 0.0 && StringLen(obstacle) > 0)
         return true;
      if(p.liquidity_target_valid_structurally && p.liquidity_target_preserved > 0.0 && p.liquidity_target_blocked_by_obstacle)
         return true;
      if(p.capped_before_obstacle_tp > 0.0)
         return true;
      if(obstacle_requires)
         return true;
      if(_TokenIsSyntheticFallback(p.target_source) && StringLen(obstacle) > 0)
         return true;
      return false;
   }

   void _MaybeRequireTargetArbitration(TradePlan &p, const double chosen_tp, const string chosen_source) {
      if(!_CanAskAiForTargetArbitration(p)) return;
      p.target_arbitration_required = true;
      if(p.liquidity_target_blocked_by_obstacle && StringLen(p.obstacle_kind) > 0)
         p.obstacle_kind = _CrossedObstacleKind(p.obstacle_kind);
      if(p.original_planned_tp_before_ai <= 0.0){
         p.original_planned_tp_before_ai = chosen_tp;
         p.original_planned_rr_before_ai = _TargetRR(p, chosen_tp);
      }
      if(StringLen(chosen_source) > 0 && chosen_source == "synthetic_rr_fallback"){
         p.fallback_tp = chosen_tp;
         p.fallback_rr = _TargetRR(p, chosen_tp);
         p.fallback_source = "synthetic_rr_fallback";
      }
      _LogTargetCandidates(p);
   }

   bool _SyntheticTargetBlockedByObstacle(const string obstacle_kind) const {
      if(InpHardRejectCrossedObstacleTarget &&
         (StringFind(obstacle_kind, "opposing") >= 0 || StringFind(obstacle_kind, "imbalance") >= 0))
         return true;
      if(InpRejectSyntheticFallbackAfterCrossedObstacle &&
         (StringFind(obstacle_kind, "opposing") >= 0 || StringFind(obstacle_kind, "imbalance") >= 0))
         return true;
      if(InpBlockSyntheticTargetThroughOpposingImbalance) return true;
      if(InpRejectAgainstHtfImbalance && obstacle_kind == "htf_opposing_imbalance") return true;
      return false;
   }

   void _NormalizeTargetLabels(TradePlan &p) {
      if(_TokenIsPartialThenLiquidity(p.ai_chosen_target_model) ||
         _TokenIsPartialThenLiquidity(p.target_source) ||
         _TokenIsPartialThenLiquidity(p.tp_model) ||
         _TokenIsPartialThenLiquidity(p.target_model)){
         p.target_source = "ai_selected_partial_then_liquidity";
         p.target_model = "partial_before_obstacle_then_liquidity";
         p.tp_model = "partial_then_liquidity";
         return;
      }
      if(StringFind(_NormToken(p.ai_chosen_target_model), "synthetic_rr_capped") >= 0 ||
         StringFind(_NormToken(p.target_source), "synthetic_rr_capped") >= 0 ||
         StringFind(_NormToken(p.tp_model), "synthetic_rr_capped") >= 0 ||
         StringFind(_NormToken(p.target_model), "synthetic_rr_capped") >= 0){
         p.target_source = "ai_selected_synthetic_rr_capped_to_max_distance";
         p.target_model = "synthetic_rr_capped_to_max_distance";
         p.tp_model = "synthetic_rr_capped_to_max_distance";
         return;
      }
      if(_TokenIsSyntheticFallback(p.ai_chosen_target_model) ||
         _TokenIsSyntheticFallback(p.target_source) ||
         _TokenIsSyntheticFallback(p.tp_model) ||
         _TokenIsSyntheticFallback(p.target_model)){
         if(StringLen(p.ai_chosen_target_model) > 0 || StringFind(_NormToken(p.target_source), "ai_selected") >= 0)
            p.target_source = "ai_selected_synthetic_rr_fallback";
         else if(StringLen(p.target_source) == 0)
            p.target_source = "synthetic_rr_fallback";
         p.target_model = "synthetic_rr_fallback";
         p.tp_model = "synthetic_rr_fallback";
         return;
      }
      if(_TokenIsCappedTarget(p.ai_chosen_target_model) ||
         _TokenIsCappedTarget(p.target_source) ||
         _TokenIsCappedTarget(p.tp_model) ||
         _TokenIsCappedTarget(p.target_model)){
         if(StringLen(p.ai_chosen_target_model) > 0 || StringFind(_NormToken(p.target_source), "ai_selected") >= 0)
            p.target_source = "ai_selected_capped_before_obstacle";
         else if(StringLen(p.target_source) == 0)
            p.target_source = "capped_before_obstacle";
         if(StringLen(p.target_model) == 0 || _TokenIsCappedTarget(p.target_model))
            p.target_model = (StringLen(p.capped_before_obstacle_source) > 0 ? p.capped_before_obstacle_source : "cap_before_opposing_imbalance");
         p.tp_model = "capped_before_obstacle";
         return;
      }
      string source = _NormToken(p.target_source);
      string model = _NormToken(p.target_model);
      string tp_model = _NormToken(p.tp_model);
      if(source == "ai_selected_liquidity_target" ||
         model == "liquidity_target" ||
         tp_model == "liquidity_target" ||
         (StringLen(p.liquidity_target_model) > 0 && model == _NormToken(p.liquidity_target_model))){
         if(StringLen(p.ai_chosen_target_model) > 0 || source == "ai_selected_liquidity_target")
            p.target_source = "ai_selected_liquidity_target";
         else if(StringLen(p.target_source) == 0)
            p.target_source = "liquidity_target";
         if(StringLen(p.target_model) == 0)
            p.target_model = (StringLen(p.liquidity_target_model) > 0 ? p.liquidity_target_model : "next_liquidity_session_range");
         p.tp_model = "liquidity_target";
         return;
      }
      if(StringLen(p.tp_model) == 0 && StringLen(p.target_source) > 0) p.tp_model = p.target_source;
      if(StringLen(p.target_source) == 0 && StringLen(p.tp_model) > 0) p.target_source = p.tp_model;
      if(StringLen(p.target_model) == 0 && StringLen(p.target_source) > 0) p.target_model = p.target_source;
   }

   string _EffectiveTargetModel(const TradePlan &p) const {
      if(StringLen(p.ai_chosen_target_model) > 0) return _NormToken(p.ai_chosen_target_model);
      if(StringLen(p.target_model) > 0) return _NormToken(p.target_model);
      if(StringLen(p.tp_model) > 0) return _NormToken(p.tp_model);
      if(StringLen(p.target_source) > 0) return _NormToken(p.target_source);
      return "";
   }

   bool _HasKnownTargetModel(const TradePlan &p) const {
      string m = _EffectiveTargetModel(p);
      if(StringLen(m) == 0) return false;
      if(m == "unknown" || m == "tp_model_unknown" || m == "target_model_unknown") return false;
      return true;
   }

   void _LogTargetCandidates(const TradePlan &p) {
      if(!_JournalDetailEnabled(2)) return;
      _Journal("[target_candidates] " + p.symbol
               + " liquidity_tp=" + _FmtPrice(p.symbol, p.liquidity_target_preserved)
               + " liquidity_rr=" + DoubleToString(p.liquidity_rr, 2)
               + " fallback_tp=" + _FmtPrice(p.symbol, p.fallback_tp)
               + " fallback_rr=" + DoubleToString(p.fallback_rr, 2)
               + " capped_tp=" + _FmtPrice(p.symbol, p.capped_before_obstacle_tp)
               + " capped_rr=" + DoubleToString(p.capped_before_obstacle_rr, 2)
               + " obstacle_kind=" + p.obstacle_kind
               + " obstacle_tf=" + p.obstacle_tf
               + " arbitration_required=" + (p.target_arbitration_required ? "true" : "false"));
   }

   string _TargetCandidateSnapshotJson(const TradePlan &p) const {
      string j = "{";
      j += JsonKVNum("liquidity_target", p.liquidity_target_preserved, 8) + ",";
      j += JsonKVNum("liquidity_rr", p.liquidity_rr, 6) + ",";
      j += JsonKVNum("fallback_tp", p.fallback_tp, 8) + ",";
      j += JsonKVNum("fallback_rr", p.fallback_rr, 6) + ",";
      j += JsonKVNum("capped_before_obstacle_tp", p.capped_before_obstacle_tp, 8) + ",";
      j += JsonKVNum("capped_before_obstacle_rr", p.capped_before_obstacle_rr, 6) + ",";
      j += JsonKVStr("obstacle_kind", p.obstacle_kind) + ",";
      j += JsonKVStr("obstacle_tf", p.obstacle_tf) + ",";
      j += JsonKVNum("obstacle_distance_r", p.obstacle_distance_r, 6) + ",";
      j += JsonKVBool("arbitration_required", p.target_arbitration_required);
      j += "}";
      return j;
   }

   void _PersistNormalizedTargetArbitration(TradePlan &p, const AiDecision &dec) {
      p.target_arbitration_schema_version = dec.target_arbitration_schema_version;
      p.prompt_contract_version = dec.prompt_contract_version;
      p.target_arbitration_normalized_valid = (p.target_arbitration_schema_version == AI_TARGET_ARBITRATION_SCHEMA_VERSION &&
                                                p.prompt_contract_version == AI_PROMPT_CONTRACT_VERSION);
      p.why_not_liquidity_target = dec.why_not_liquidity_target;
      p.why_not_partial_before_obstacle = dec.why_not_partial_before_obstacle;
      p.why_not_capped_before_obstacle = dec.why_not_capped_before_obstacle;
      p.why_not_synthetic_fallback = dec.why_not_synthetic_fallback;
      p.target_comparison_json = dec.target_comparison_json;
      p.original_target_candidates_json = _TargetCandidateSnapshotJson(p);
      p.ai = dec;
      p.ai.chosen_target_model = p.ai_chosen_target_model;
      p.ai.chosen_tp1 = p.ai_chosen_tp1;
      p.ai.chosen_tp2 = p.ai_chosen_tp2;
      p.ai.chosen_rr2 = p.ai_chosen_rr2;
      p.ai.target_blocker_severity = p.ai_blocker_severity;
      p.ai.target_blocker_class = p.ai_blocker_class;
      p.ai.target_blocker_is_trade_killer = p.ai_blocker_is_trade_killer;
      p.ai.target_decision_reason = p.target_decision_reason;
      p.ai.why_not_liquidity_target = p.why_not_liquidity_target;
      p.ai.why_not_partial_before_obstacle = p.why_not_partial_before_obstacle;
      p.ai.why_not_capped_before_obstacle = p.why_not_capped_before_obstacle;
      p.ai.why_not_synthetic_fallback = p.why_not_synthetic_fallback;
      p.ai.target_arbitration_schema_version = p.target_arbitration_schema_version;
      p.ai.prompt_contract_version = p.prompt_contract_version;
      p.ai.target_comparison_json = p.target_comparison_json;
   }

   bool _HasStoredTargetArbitration(const TradePlan &p) const {
      if(!p.target_arbitration_normalized_valid) return false;
      if(p.target_arbitration_schema_version != AI_TARGET_ARBITRATION_SCHEMA_VERSION) return false;
      if(p.prompt_contract_version != AI_PROMPT_CONTRACT_VERSION) return false;
      if(StringLen(p.ai_chosen_target_model) == 0) return false;
      if(StringLen(p.target_decision_reason) == 0) return false;
      return true;
   }

   bool _ApplyStoredTargetArbitrationAfterRebuild(TradePlan &p, string &reason) {
      reason = "ok";
      if(!_HasStoredTargetArbitration(p)){
         reason = "missing_stored_target_arbitration_for_pending_relax";
         return false;
      }
      string chosen = _NormToken(p.ai_chosen_target_model);
      bool chose_partial = _TokenIsPartialThenLiquidity(chosen);
      bool chose_capped = _TokenIsCappedTarget(chosen);
      bool chose_fallback = _TokenIsSyntheticFallback(chosen);
      bool chose_liquidity = (!chose_partial && !chose_capped && !chose_fallback &&
                              (StringFind(chosen, "liquidity") >= 0 ||
                               (StringLen(p.liquidity_target_model) > 0 && chosen == _NormToken(p.liquidity_target_model))));

      if(chose_fallback){
         p.target_source = "ai_selected_synthetic_rr_fallback";
         p.target_model = "synthetic_rr_fallback";
         p.tp_model = "synthetic_rr_fallback";
         if(!_RecomputeSyntheticFallbackTarget(p, "pending_relax_stored_target_arbitration")){
            reason = "ai_selected_synthetic_fallback_invalid";
            return false;
         }
      } else if(chose_partial){
         double tp1 = (p.capped_before_obstacle_tp > 0.0 ? p.capped_before_obstacle_tp : p.ai_chosen_tp1);
         double tp2 = (p.liquidity_target_preserved > 0.0 ? p.liquidity_target_preserved : p.ai_chosen_tp2);
         if(tp2 <= 0.0){
            reason = "ai_partial_target_valid_but_runner_invalid";
            return false;
         }
         p.tp1 = tp1;
         p.tp2 = tp2;
         // Restoring a stored partial-then-liquidity decision restores its first leg
         // too, so the generic R-multiple builder must not rewrite it downstream.
         p.tp1_from_target_model = true;
         p.target_source = "ai_selected_partial_then_liquidity";
         p.target_model = "partial_before_obstacle_then_liquidity";
         p.tp_model = "partial_then_liquidity";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_tp1 = tp1;
         p.ai_chosen_tp2 = tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
      } else if(chose_capped){
         double tp2 = (p.capped_before_obstacle_tp > 0.0 ? p.capped_before_obstacle_tp : p.ai_chosen_tp2);
         if(tp2 <= 0.0){
            reason = "ai_chosen_target_missing_tp";
            return false;
         }
         p.tp2 = tp2;
         p.target_source = "ai_selected_capped_before_obstacle";
         p.target_model = (StringLen(p.capped_before_obstacle_source) > 0 ? p.capped_before_obstacle_source : "cap_before_opposing_imbalance");
         p.tp_model = "capped_before_obstacle";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_tp2 = tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
      } else if(chose_liquidity){
         double tp2 = (p.liquidity_target_preserved > 0.0 ? p.liquidity_target_preserved : p.ai_chosen_tp2);
         if(tp2 <= 0.0){
            reason = "ai_chosen_target_missing_tp";
            return false;
         }
         p.tp2 = tp2;
         p.target_source = "ai_selected_liquidity_target";
         p.target_model = (StringLen(p.liquidity_target_model) > 0 ? p.liquidity_target_model : "next_liquidity_session_range");
         p.tp_model = "liquidity_target";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_tp2 = tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
      } else {
         reason = "missing_stored_target_arbitration_for_pending_relax";
         return false;
      }
      p.target_arbitration_required = false;
      _NormalizeTargetLabels(p);
      return true;
   }

   //+---------------------------------------------------------------+
   //| AssessedTradePlan / ExecutionAdjustmentContract / LiveExecution |
   //+---------------------------------------------------------------+

   // A synthetic target is defined as a multiple of risk, so it is a function
   // of the entry and must be recomputed by the approved formula when the entry
   // moves.  Every other model names a structural price level, which does not
   // move because our entry did.
   bool _TargetModelIsSyntheticRr(const string model) const {
      string token = _NormToken(model);
      if(StringLen(token) == 0) return false;
      return (StringFind(token, "synthetic_rr") >= 0);
   }

   //--- Freeze the approved plan.  Called once, after the AI's approved target
   //--- has been applied, so the snapshot is of the trade that was actually
   //--- approved rather than the pre-arbitration request plan.
   void _LockAssessedPlan(TradePlan &p, const AiDecision &dec) {
      p.assessed_entry                       = p.entry_est;
      p.assessed_sl                          = p.sl;
      p.assessed_tp1                         = p.tp1;
      p.assessed_tp2                         = p.tp2;
      p.assessed_stop_distance               = MathAbs(p.entry_est - p.sl);
      p.assessed_tp_model                    = p.tp_model;
      p.assessed_target_source               = p.target_source;
      p.assessed_target_model                = p.target_model;
      p.assessed_selected_target_identity    = (StringLen(dec.selected_target_identity) > 0
                                                ? dec.selected_target_identity
                                                : _EffectiveTargetModel(p));
      p.assessed_selected_target_price       = p.tp2;
      p.assessed_obstacle_kind               = p.obstacle_kind;
      p.assessed_obstacle_tf                 = p.obstacle_tf;
      p.assessed_obstacle_price              = p.obstacle_price;
      p.assessed_obstacle_severity           = _EffectiveObstacleSeverity(p.obstacle_kind, p.obstacle_severity);
      // Nothing has been observed live yet; a value carried over from a previous
      // lifecycle would be compared against this brand new assessment.
      p.live_obstacle_kind                   = "";
      p.live_obstacle_price                  = 0.0;
      p.live_obstacle_severity               = 0.0;
      p.assessed_net_rr                      = (p.assessed_stop_distance > 0.0
                                                ? _RewardToTarget(p.is_buy, p.entry_est, p.tp2) / p.assessed_stop_distance
                                                : 0.0);
      p.assessed_plan_locked                 = true;
      p.execution_failure_class              = EXEC_FAIL_NONE;
      p.execution_failure_state_fingerprint  = "";
      p.execution_precheck_attempts          = 0;
      p.execution_order_construction_attempts= 0;
      p.execution_attempts_suppressed        = 0;
      p.execution_retry_not_before           = 0;

      // A plan whose frozen first leg is already under its own frozen geometry floor
      // can never pass _BuildPlanPrices, so it will sit on the watchlist until the
      // rebuild kills it as a "structural invalidation" that never happened.  AUDUSD
      // on 2026-09-06 waited 12 bars to be rejected for a leg of 0.00136 against a
      // floor of 0.00151 that it already failed the moment it was frozen.  Reported,
      // not acted on: which of the two values is the wrong one is not yet established,
      // and guessing would either force a trade or discard a real approval.
      double locked_leg   = _RewardToTarget(p.is_buy, p.assessed_entry, p.assessed_tp1);
      double locked_floor = p.assessed_stop_distance * 0.65;
      if(p.assessed_tp1 > 0.0 && locked_floor > 0.0 && locked_leg > 0.0 && locked_leg < locked_floor)
         _Journal("[assessed_leg_below_floor] symbol=" + p.symbol
                  + " assessed_tp1=" + _FmtPrice(p.symbol, p.assessed_tp1)
                  + " assessed_leg_reward=" + _FmtPrice(p.symbol, locked_leg)
                  + " geometry_floor=" + _FmtPrice(p.symbol, locked_floor)
                  + " assessed_stop_distance=" + _FmtPrice(p.symbol, p.assessed_stop_distance)
                  + " leg_r=" + DoubleToString(locked_leg / p.assessed_stop_distance, 4)
                  + " target_model=" + p.assessed_target_model
                  + " detected_at=lock action=diagnostic_only_plan_cannot_execute");

      _Journal("[assessed_plan_identity] symbol=" + p.symbol
               + " candidate_id=" + p.candidate_id
               + " candidate_hash=" + p.candidate_hash
               + " direction=" + (p.is_buy ? "BUY" : "SELL")
               + " setup_taxonomy_enum=" + p.setup_taxonomy_enum
               + " entry_model=" + p.entry_branch
               + " stop_model=" + p.tp_model
               + " assessed_entry=" + _FmtPrice(p.symbol, p.assessed_entry)
               + " assessed_sl=" + _FmtPrice(p.symbol, p.assessed_sl)
               + " assessed_tp1=" + _FmtPrice(p.symbol, p.assessed_tp1)
               + " assessed_tp2=" + _FmtPrice(p.symbol, p.assessed_tp2)
               + " target_identity=" + p.assessed_selected_target_identity
               + " target_source=" + p.assessed_target_source
               + " target_model=" + p.assessed_target_model
               + " obstacle_kind=" + p.assessed_obstacle_kind
               + " obstacle_tf=" + p.assessed_obstacle_tf
               + " assessed_net_rr=" + DoubleToString(p.assessed_net_rr, 6)
               + " assessment_fingerprint=" + p.assessed_execution_fingerprint);
   }

   //--- The deterministic permission derived from the approved plan.
   void _BuildExecutionAdjustmentContract(const TradePlan &p, ExecutionAdjustmentContract &c) {
      c.Reset();
      double tick = _PlanTickSize(p.symbol);
      double risk = (p.assessed_stop_distance > 0.0
                     ? p.assessed_stop_distance
                     : MathAbs(p.assessed_entry - p.assessed_sl));

      c.entry_adjustment_allowed = true;
      c.max_entry_drift_r        = (_IsMicroFamily(p)
                                    ? MathMax(InpMaxEntryDriftR, InpMicroMarketEntryToleranceR)
                                    : InpMaxEntryDriftR);
      c.max_entry_drift_ticks    = MathMax(2.0, (double)PO3EffectiveMinFvgWidthTicks());

      // The structural stop is derived from the FVG, not from the entry, so it
      // should not move at all.  A small allowance covers broker minimum-stop
      // normalisation only.
      c.sl_adjustment_allowed = true;
      c.max_sl_drift_r        = 0.10;

      bool synthetic = _TargetModelIsSyntheticRr(p.assessed_target_model);
      c.target_recalculation   = (synthetic ? TARGET_RECALC_DETERMINISTIC_RR
                                            : TARGET_RECALC_PRESERVE_FIXED_PRICE);
      // TP2 may only be recomputed for a synthetic fixed-RR model, and only by
      // the approved formula.  A structural target keeps its price.
      c.tp2_adjustment_allowed = synthetic;
      // TP1 is a derived partial level; it follows tp2 under the same rule.
      c.tp1_adjustment_allowed = synthetic;

      c.target_identity_preserved      = true;
      c.target_source_preserved        = true;
      c.target_model_preserved         = true;
      c.obstacle_revalidation_required = true;

      c.min_resulting_rr         = MathMax(0.0, InpMinLiveRR2);
      c.max_target_distance      = _MaxPlanTargetDistance(p, p.entry_est, (risk > 0.0 ? risk : MathAbs(p.entry_est - p.sl)));
      c.max_cost_deterioration_r = 0.02;
      c.expiry                   = p.armed_at;

      if(tick > 0.0 && c.max_entry_drift_ticks <= 0.0) c.max_entry_drift_ticks = 2.0;
   }

   double _ContractMaxEntryDrift(const TradePlan &p, const ExecutionAdjustmentContract &c) const {
      double risk = (p.assessed_stop_distance > 0.0
                     ? p.assessed_stop_distance
                     : MathAbs(p.assessed_entry - p.assessed_sl));
      double tick = _PlanTickSize(p.symbol);
      return MathMax(risk * c.max_entry_drift_r, tick * c.max_entry_drift_ticks);
   }

   //+---------------------------------------------------------------+
   //| Carry the approved target forward instead of re-deriving it.   |
   //|                                                                |
   //| This is the fix for the dominant zero-trade failure.  The live  |
   //| rebuild still runs the full obstacle/liquidity landscape scan   |
   //| (it is needed to revalidate the approved target against current |
   //| structure), but its *choice* no longer overrides the approved   |
   //| one.  Rejecting the trade remains possible -- and required --   |
   //| when the approved target is genuinely no longer reachable; what |
   //| is no longer possible is quietly substituting a different       |
   //| target and then complaining the plan changed.                   |
   //+---------------------------------------------------------------+
   bool _ApplyAssessedTargetUnderContract(TradePlan &p,
                                          const ExecutionAdjustmentContract &c,
                                          string &reason,
                                          string &failure_class) {
      reason = "ok";
      failure_class = EXEC_FAIL_NONE;

      double live_risk = MathAbs(p.entry_est - p.sl);
      if(live_risk <= 0.0){
         reason = "no_valid_stop";
         failure_class = EXEC_FAIL_STRUCTURAL_INVALIDATION;
         return false;
      }

      // Identity first: the approved source/model/identity are restored before
      // anything is priced, so no later normalisation can reinterpret them.
      p.target_source          = p.assessed_target_source;
      p.target_model           = p.assessed_target_model;
      p.tp_model               = p.assessed_tp_model;
      p.ai_chosen_target_model = p.assessed_selected_target_identity;

      if(c.target_recalculation == TARGET_RECALC_DETERMINISTIC_RR){
         // Synthetic fixed-RR target: same model, same source, price recomputed
         // from the new entry by the approved R multiple.
         double approved_rr = p.assessed_net_rr;
         if(approved_rr <= 0.0){
            reason = "assessed_rr_invalid_for_synthetic_recalculation";
            failure_class = EXEC_FAIL_STRUCTURAL_INVALIDATION;
            return false;
         }
         double reward = live_risk * approved_rr;
         // The contract authorises the entry to drift (up to max_entry_drift_r) and in
         // the same breath MANDATES that a synthetic target be recomputed as
         // approved_rr * live_risk.  With the stop held, an adverse entry drift
         // multiplies the reward while the max-distance cap -- built from ADR/ATR --
         // does not move at all, so the mandated recomputation can walk straight
         // through the cap and the plan is then killed for obeying its own contract.
         // EURUSD, 2026-09-06: entry 1.15321 -> 1.15386, a drift of 0.08R against a
         // 0.40R permission, grew the reward 0.00822 -> 0.00889 against a cap of
         // 0.00848458, and a live approval died as
         // ai_chosen_target_exceeds_max_distance three milliseconds after that very
         // contract had passed it.
         // Capping is the resolution the engine already applies to its own synthetic
         // targets (synthetic_rr_capped_to_max_distance).  It only ever shortens a
         // target, never widens or invents one, and when the cap would drop the trade
         // under the contract's own minimum RR the plan is genuinely no longer
         // feasible and still fails closed.
         double max_dist = _MaxPlanTargetDistance(p, p.entry_est, live_risk);
         if(max_dist > 0.0 && !RewardWithinMaxDistance(reward, max_dist, _PlanTickSize(p.symbol))){
            double capped_rr = max_dist / live_risk;
            _Journal("[deterministic_rr_target_cap] symbol=" + p.symbol
                     + " approved_rr=" + DoubleToString(approved_rr, 6)
                     + " live_risk=" + _FmtPrice(p.symbol, live_risk)
                     + " assessed_risk=" + _FmtPrice(p.symbol, p.assessed_stop_distance)
                     + " requested_reward=" + _FmtPrice(p.symbol, reward)
                     + " max_target_distance=" + _FmtPrice(p.symbol, max_dist)
                     + " capped_rr=" + DoubleToString(capped_rr, 6)
                     + " min_resulting_rr=" + DoubleToString(c.min_resulting_rr, 6)
                     + " action=" + (capped_rr + 0.0001 < c.min_resulting_rr
                                     ? "reject_capped_rr_below_contract_minimum"
                                     : "cap_reward_to_max_distance"));
            if(capped_rr + 0.0001 < c.min_resulting_rr){
               reason = "ai_chosen_target_exceeds_max_distance";
               failure_class = EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE;
               return false;
            }
            reward = max_dist;
         }
         p.tp2 = (p.is_buy ? p.entry_est + reward : p.entry_est - reward);
         double tp1_ratio = 0.0;
         double assessed_reward = _RewardToTarget(p.is_buy, p.assessed_entry, p.assessed_tp2);
         if(assessed_reward > 0.0)
            tp1_ratio = _RewardToTarget(p.is_buy, p.assessed_entry, p.assessed_tp1) / assessed_reward;
         if(tp1_ratio <= 0.0 || tp1_ratio >= 1.0) tp1_ratio = 0.70;
         p.tp1 = (p.is_buy ? p.entry_est + reward * tp1_ratio : p.entry_est - reward * tp1_ratio);
      } else {
         // Structural target: the price is a property of the market.
         p.tp2 = p.assessed_selected_target_price;
         p.tp1 = p.assessed_tp1;
      }

      p.ai_chosen_tp1  = p.tp1;
      p.ai_chosen_tp2  = p.tp2;
      p.effective_rr2  = _TargetRR(p, p.tp2);
      p.ai_chosen_rr2  = p.effective_rr2;
      p.target_arbitration_required = false;
      // An approved plan owns both legs.  Without this the generic TP1 builder in
      // _BuildPlanPrices rewrote the approved first leg from tp1_r_multiple, which is
      // exactly the fingerprint drift the comment at the top of this function describes.
      p.tp1_from_target_model = true;

      // Revalidate the *preserved* target against current structure.  This is
      // where a genuinely dead trade is still killed.
      TargetFeasibilityResult feasibility;
      _EvaluateTargetFeasibility(p, p.tp2, p.target_model,
                                 "live_execution_plan_under_contract", true, feasibility);
      _LogTargetFeasibilityResult("live_execution_rebuild", feasibility);
      if(!feasibility.feasible){
         reason = feasibility.reason;
         failure_class = (feasibility.reason == "ai_chosen_target_already_reached" ||
                          feasibility.reason == "ai_chosen_target_exceeds_max_distance" ||
                          feasibility.reason == "rr_below_live_floor" ||
                          feasibility.reason == "target_too_close_for_swing_duration")
                         ? EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE
                         : EXEC_FAIL_STRUCTURAL_INVALIDATION;
         return false;
      }

      _Journal("[live_execution_plan] symbol=" + p.symbol
               + " live_bid=" + _FmtPrice(p.symbol, SymbolInfoDouble(p.symbol, SYMBOL_BID))
               + " live_ask=" + _FmtPrice(p.symbol, SymbolInfoDouble(p.symbol, SYMBOL_ASK))
               + " live_entry=" + _FmtPrice(p.symbol, p.entry_est)
               + " live_sl=" + _FmtPrice(p.symbol, p.sl)
               + " live_tp1=" + _FmtPrice(p.symbol, p.tp1)
               + " live_tp2=" + _FmtPrice(p.symbol, p.tp2)
               + " target_identity=" + p.assessed_selected_target_identity
               + " target_source=" + p.target_source
               + " target_model=" + p.target_model
               + " target_recalculation=" + c.target_recalculation
               + " live_rr2=" + DoubleToString(p.effective_rr2, 6)
               + " spread_r=" + DoubleToString(p.spread_r, 6)
               + " execution_cost_r=" + DoubleToString(p.execution_cost_r, 6));
      return true;
   }

   //+---------------------------------------------------------------+
   //| Compare a live plan to its assessed plan, in three buckets.    |
   //+---------------------------------------------------------------+
   void _EvaluateSemanticPlanMatch(const TradePlan &p,
                                   const ExecutionAdjustmentContract &c,
                                   SemanticPlanMatchResult &out) {
      out.Reset();
      double tick = _PlanTickSize(p.symbol);
      double risk = (p.assessed_stop_distance > 0.0
                     ? p.assessed_stop_distance
                     : MathAbs(p.assessed_entry - p.assessed_sl));
      double price_tol   = MathMax(2.0 * tick, 0.05 * risk);
      double entry_tol   = _ContractMaxEntryDrift(p, c);
      double sl_tol      = MathMax(2.0 * tick, risk * c.max_sl_drift_r);
      double cost_tol    = c.max_cost_deterioration_r;

      out.adjustment_bounds = "entry_tol=" + DoubleToString(entry_tol, 8)
                            + " sl_tol=" + DoubleToString(sl_tol, 8)
                            + " price_tol=" + DoubleToString(price_tol, 8)
                            + " cost_tol_r=" + DoubleToString(cost_tol, 6)
                            + " min_rr=" + DoubleToString(c.min_resulting_rr, 6)
                            + " max_target_distance=" + DoubleToString(c.max_target_distance, 8);

      //--- immutable semantic identity -------------------------------------
      if(p.candidate_hash != p.ai_selected_candidate_hash) _AppendChangedComponent(out.immutable_fields_changed, "candidate_hash");
      if(p.candidate_id != p.ai.selected_candidate_id) _AppendChangedComponent(out.immutable_fields_changed, "candidate_id");
      if(p.symbol != p.assessed_symbol) _AppendChangedComponent(out.immutable_fields_changed, "symbol");
      if(p.is_buy != p.assessed_is_buy) _AppendChangedComponent(out.immutable_fields_changed, "direction");
      if(p.model_code != p.assessed_setup_code) _AppendChangedComponent(out.immutable_fields_changed, "setup_code");
      if(p.setup_family != p.assessed_setup_family) _AppendChangedComponent(out.immutable_fields_changed, "setup_family");
      if(p.setup_taxonomy_enum != p.assessed_setup_taxonomy_enum) _AppendChangedComponent(out.immutable_fields_changed, "setup_taxonomy_enum");
      if(p.setup_taxonomy_version != p.assessed_setup_taxonomy_version) _AppendChangedComponent(out.immutable_fields_changed, "setup_taxonomy_version");
      if(p.taxonomy_mapping_source != p.assessed_taxonomy_mapping_source) _AppendChangedComponent(out.immutable_fields_changed, "taxonomy_mapping_source");
      if(p.entry_branch != p.assessed_entry_branch) _AppendChangedComponent(out.immutable_fields_changed, "entry_branch");
      if(p.source_t_sweep != p.assessed_source_t_sweep) _AppendChangedComponent(out.immutable_fields_changed, "source_t_sweep");
      if(p.source_t_disp != p.assessed_source_t_disp) _AppendChangedComponent(out.immutable_fields_changed, "source_t_disp");
      if(p.source_t_bos != p.assessed_source_t_bos) _AppendChangedComponent(out.immutable_fields_changed, "source_t_bos");
      if(c.target_source_preserved && p.target_source != p.assessed_target_source) _AppendChangedComponent(out.immutable_fields_changed, "target_source");
      if(c.target_model_preserved && p.target_model != p.assessed_target_model) _AppendChangedComponent(out.immutable_fields_changed, "target_model");
      // The obstacle's IDENTITY is immutable; its live crossing state is not.
      // obstacle_price -- the primitive the kind is derived from -- has always been
      // compared with a tolerance and classified as authorized.  The derived label
      // was compared by exact string equality with none, so a plan died the moment
      // price crossed the obstacle it was waiting behind.  Three of the eleven
      // 2026-09-05 approvals were lost this way; USDCAD cycled
      // crossed_opposing_imbalance -> crossed_session_high ->
      // crossed_htf_opposing_imbalance inside one hour, and GBPCHF was killed on a
      // two-point entry move.  The base kind still fails closed: a genuinely
      // different obstacle is still a semantic change.
      bool obstacle_crossing_changed = false;
      bool obstacle_tf_changed = false;
      bool obstacle_label_changed = false;
      double live_obstacle_sev = 0.0, assessed_obstacle_sev = 0.0;
      bool obstacle_ok = _ObstacleIdentityPreserved(
                            _LiveObstacleKindForComparison(p), _LiveObstacleTfForComparison(p),
                            _LiveObstacleSeverityForComparison(p),
                            p.assessed_obstacle_kind, p.assessed_obstacle_tf,
                            p.assessed_obstacle_severity,
                            obstacle_crossing_changed, obstacle_tf_changed,
                            obstacle_label_changed, live_obstacle_sev, assessed_obstacle_sev);
      if(!obstacle_ok){
         _AppendChangedComponent(out.immutable_fields_changed, "obstacle_more_severe");
      } else {
         if(obstacle_crossing_changed)
            _AppendChangedComponent(out.authorized_fields_changed, "obstacle_crossing_state");
         if(obstacle_tf_changed)
            _AppendChangedComponent(out.authorized_fields_changed, "obstacle_tf");
         if(obstacle_label_changed)
            _AppendChangedComponent(out.authorized_fields_changed, "obstacle_label_no_worse");
      }
      // Always emitted, pass or fail: the 2026-09-06 rejections named the field and
      // nothing else, so neither side of the comparison was ever visible in a journal.
      if(obstacle_label_changed || obstacle_crossing_changed || obstacle_tf_changed || !obstacle_ok)
         _Journal("[obstacle_identity_gate] symbol=" + p.symbol
                  + " assessed_kind=" + (StringLen(p.assessed_obstacle_kind) > 0 ? p.assessed_obstacle_kind : "none")
                  + " assessed_tf=" + (StringLen(p.assessed_obstacle_tf) > 0 ? p.assessed_obstacle_tf : "none")
                  + " assessed_severity=" + DoubleToString(assessed_obstacle_sev, 2)
                  + " live_kind=" + (StringLen(_LiveObstacleKindForComparison(p)) > 0 ? _LiveObstacleKindForComparison(p) : "none")
                  + " live_tf=" + (StringLen(_LiveObstacleTfForComparison(p)) > 0 ? _LiveObstacleTfForComparison(p) : "none")
                  + " live_severity=" + DoubleToString(live_obstacle_sev, 2)
                  + " observation_source=" + (_HasLiveObstacleObservation(p) ? "live_scan_while_locked" : "plan_fields")
                  + " label_changed=" + (obstacle_label_changed ? "true" : "false")
                  + " crossing_changed=" + (obstacle_crossing_changed ? "true" : "false")
                  + " tf_changed=" + (obstacle_tf_changed ? "true" : "false")
                  + " action=" + (obstacle_ok ? "authorized" : "reject_live_obstacle_more_severe"));
      if(m_ai.DecisionHash() != p.assessed_decision_input_hash) _AppendChangedComponent(out.immutable_fields_changed, "decision_input_hash");
      if(ENGINE_INPUT_SCHEMA != p.assessed_strategy_schema_version) _AppendChangedComponent(out.immutable_fields_changed, "strategy_schema_version");
      // A structural target's price is immutable; a synthetic one is not.
      if(!c.tp2_adjustment_allowed &&
         MathAbs(p.tp2 - p.assessed_selected_target_price) > price_tol)
         _AppendChangedComponent(out.immutable_fields_changed, "selected_target_price");

      //--- authorized adjustment, and whether it stayed in bounds -----------
      double entry_delta = MathAbs(p.entry_est - p.assessed_entry);
      if(entry_delta > 0.0){
         _AppendChangedComponent(out.authorized_fields_changed, "entry");
         if(!c.entry_adjustment_allowed || entry_delta > entry_tol)
            _AppendChangedComponent(out.unauthorized_fields_changed, "entry_drift_exceeds_contract");
      }
      double sl_delta = MathAbs(p.sl - p.assessed_sl);
      if(sl_delta > 0.0){
         _AppendChangedComponent(out.authorized_fields_changed, "sl");
         if(!c.sl_adjustment_allowed || sl_delta > sl_tol)
            _AppendChangedComponent(out.unauthorized_fields_changed, "sl_drift_exceeds_contract");
      }
      if(MathAbs(p.tp1 - p.assessed_tp1) > price_tol){
         _AppendChangedComponent(out.authorized_fields_changed, "tp1");
         if(!c.tp1_adjustment_allowed)
            _AppendChangedComponent(out.unauthorized_fields_changed, "tp1_changed_without_permission");
      }
      if(MathAbs(p.tp2 - p.assessed_tp2) > price_tol){
         _AppendChangedComponent(out.authorized_fields_changed, "tp2");
         if(!c.tp2_adjustment_allowed)
            _AppendChangedComponent(out.unauthorized_fields_changed, "tp2_changed_without_permission");
      }
      if(MathAbs(_LiveObstaclePriceForComparison(p) - p.assessed_obstacle_price) > price_tol)
         _AppendChangedComponent(out.authorized_fields_changed, "obstacle_price");

      double live_risk = MathAbs(p.entry_est - p.sl);
      double live_rr   = (live_risk > 0.0 ? _RewardToTarget(p.is_buy, p.entry_est, p.tp2) / live_risk : 0.0);
      if(c.min_resulting_rr > 0.0 && !_RRMeetsFloor(live_rr, c.min_resulting_rr))
         _AppendChangedComponent(out.unauthorized_fields_changed, "resulting_rr_below_minimum");

      if(MathAbs(p.spread_r - p.assessed_spread_r) > cost_tol)
         _AppendChangedComponent(out.unauthorized_fields_changed, "spread_r_deterioration");
      if(MathAbs(p.slippage_r - p.assessed_slippage_r) > cost_tol)
         _AppendChangedComponent(out.unauthorized_fields_changed, "slippage_r_deterioration");
      if(MathAbs(p.execution_cost_r - p.assessed_execution_cost_r) > cost_tol)
         _AppendChangedComponent(out.unauthorized_fields_changed, "execution_cost_r_deterioration");

      out.semantic_match   = (StringLen(out.immutable_fields_changed) == 0);
      out.adjustment_valid = (StringLen(out.unauthorized_fields_changed) == 0);
      if(!out.semantic_match){
         out.failure_class = EXEC_FAIL_SEMANTIC_PLAN_CHANGED;
         out.result = "semantic_plan_changed";
      } else if(!out.adjustment_valid){
         out.failure_class = EXEC_FAIL_STRUCTURAL_INVALIDATION;
         out.result = "adjustment_outside_contract";
      } else {
         out.failure_class = EXEC_FAIL_NONE;
         out.result = "pass";
      }
   }

   void _LogSemanticPlanMatch(const TradePlan &p,
                              const ExecutionAdjustmentContract &c,
                              const SemanticPlanMatchResult &r,
                              const string stage) {
      _Journal("[execution_adjustment_contract] stage=" + stage + " symbol=" + p.symbol + " " + c.Describe());
      _Journal("[semantic_plan_match] stage=" + stage
               + " symbol=" + p.symbol
               + " semantic_plan_match=" + (r.semantic_match ? "true" : "false")
               + " immutable_fields_changed=" + (StringLen(r.immutable_fields_changed) > 0 ? r.immutable_fields_changed : "none")
               + " authorized_fields_changed=" + (StringLen(r.authorized_fields_changed) > 0 ? r.authorized_fields_changed : "none")
               + " unauthorized_fields_changed=" + (StringLen(r.unauthorized_fields_changed) > 0 ? r.unauthorized_fields_changed : "none")
               + " result=" + r.result);
      _Journal("[execution_adjustment_validation] stage=" + stage
               + " symbol=" + p.symbol
               + " execution_adjustment_validation=" + (r.adjustment_valid ? "true" : "false")
               + " adjustment_bounds=" + r.adjustment_bounds
               + " failure_class=" + r.failure_class
               + " action=" + ExecFailureAction(r.failure_class)
               + " result=" + r.result);
   }

   bool ValidateAiChosenTargetBeforeWatchlist(TradePlan &plan, string &reject_reason, string &reject_detail) {
      reject_reason = "ok";
      reject_detail = "";
      _NormalizeTargetLabels(plan);
      string sanitize_reason = "";
      if(!_ApplyFeasibleTargetSanitizer(plan, sanitize_reason)){
         reject_reason = sanitize_reason;
         reject_detail = "target_sanitizer_failed";
         _TrackNamedCounter(m_funnel_target_validation_reasons, m_funnel_target_validation_counts, reject_reason);
         _TrackNamedCounter(m_total_target_validation_reasons, m_total_target_validation_counts, reject_reason);
         _Journal("[target_validation] pass=false stage=pre_watchlist"
                  + " reject_reason=" + reject_reason
                  + " chosen=" + _EffectiveTargetModel(plan)
                  + " detail=" + reject_detail);
         return false;
      }

      bool awaiting_arbitration = (plan.target_arbitration_required &&
                                   StringLen(plan.ai_chosen_target_model) == 0 &&
                                   StringLen(plan.ai.chosen_target_model) == 0);
      if(awaiting_arbitration)
         return true;

      double min_rr = MathMax(0.0, InpMinLiveRR2);
      double risk = MathAbs(plan.entry_est - plan.sl);
      double rr2 = _ExecutionRR2(plan);
      double rr_eps = _RREps();
      double bid = SymbolInfoDouble(plan.symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(plan.symbol, SYMBOL_ASK);
      string chosen = _EffectiveTargetModel(plan);
      bool target_reached = false;
      bool direction_valid = true;
      bool max_distance_pass = true;
      bool partial_then_liquidity = (_TokenIsPartialThenLiquidity(chosen) || _TokenIsPartialThenLiquidity(plan.target_source) || _TokenIsPartialThenLiquidity(plan.tp_model));
      bool capped_final = (_TokenIsCappedTarget(chosen) || _TokenIsCappedTarget(plan.target_source) || _TokenIsCappedTarget(plan.tp_model));
      bool synthetic_final = (_PlanUsesSyntheticFallback(plan));
      bool rr_floor_pass = _RRMeetsFloor(rr2, min_rr);

      if(plan.tp2 <= 0.0){
         reject_reason = "ai_chosen_target_missing_tp";
         reject_detail = "chosen=" + chosen;
      } else if(risk <= 0.0 || plan.sl <= 0.0 || plan.entry_est <= 0.0){
         reject_reason = "no_valid_stop";
         reject_detail = "entry=" + _FmtPrice(plan.symbol, plan.entry_est) + " sl=" + _FmtPrice(plan.symbol, plan.sl);
      } else if(!_IsRewardSideLevel(plan.is_buy, plan.entry_est, plan.tp2)){
         reject_reason = "ai_chosen_target_invalid_direction";
         reject_detail = "entry=" + _FmtPrice(plan.symbol, plan.entry_est) + " tp2=" + _FmtPrice(plan.symbol, plan.tp2);
         direction_valid = false;
      } else if(!_HasKnownTargetModel(plan)){
         reject_reason = "target_model_unknown";
         reject_detail = "target_source=" + plan.target_source + " tp_model=" + plan.tp_model + " target_model=" + plan.target_model;
      } else {
         double point = SymbolInfoDouble(plan.symbol, SYMBOL_POINT);
         if(point <= 0.0) point = 0.00001;
         double eps = MathMax(point * 2.0, risk * 0.01);
         double px = (plan.is_buy ? bid : ask);
         if(px > 0.0 && ((plan.is_buy && px >= plan.tp2 - eps) || (!plan.is_buy && px <= plan.tp2 + eps))){
            reject_reason = "ai_chosen_target_already_reached";
            reject_detail = "px=" + _FmtPrice(plan.symbol, px) + " tp2=" + _FmtPrice(plan.symbol, plan.tp2);
            target_reached = true;
         } else if(!rr_floor_pass){
            if(partial_then_liquidity) reject_reason = "ai_partial_target_valid_but_runner_invalid";
            else if(capped_final) reject_reason = "ai_chosen_capped_target_rr_too_low";
            else if(synthetic_final) reject_reason = "ai_chosen_synthetic_fallback_rr_too_low";
            else reject_reason = "ai_chosen_target_rr_too_low";
            reject_detail = "rr2=" + DoubleToString(rr2, 6) + " min_rr=" + DoubleToString(min_rr, 6) + " eps=" + DoubleToString(rr_eps, 6);
         } else {
            double reward = _RewardToTarget(plan.is_buy, plan.entry_est, plan.tp2);
            double min_dist = _MinSwingTargetDistance(plan, plan.entry_est);
            if(min_dist > 0.0 && reward < min_dist){
               reject_reason = (partial_then_liquidity ? "ai_partial_target_valid_but_runner_invalid" : "ai_chosen_target_rr_too_low");
               reject_detail = "reward=" + _FmtPrice(plan.symbol, reward) + " min_target_distance=" + _FmtPrice(plan.symbol, min_dist);
            } else {
               double max_dist = _MaxPlanTargetDistance(plan, plan.entry_est, risk);
               bool liquidity_or_runner_allowed = (partial_then_liquidity ||
                                                   plan.runner_trade ||
                                                   plan.tp_model == "liquidity_target" ||
                                                   plan.target_source == "ai_selected_liquidity_target");
               if(max_dist > 0.0 && reward > max_dist && !liquidity_or_runner_allowed){
                  max_distance_pass = false;
                  reject_reason = "ai_chosen_target_exceeds_max_distance";
                  reject_detail = "reward=" + _FmtPrice(plan.symbol, reward) + " max_target_distance=" + _FmtPrice(plan.symbol, max_dist);
               }
            }
         }
      }

      _Journal("[target_validation_context] stage=pre_watchlist"
               + " entry=" + _FmtPrice(plan.symbol, plan.entry_est)
               + " sl=" + _FmtPrice(plan.symbol, plan.sl)
               + " tp2=" + _FmtPrice(plan.symbol, plan.tp2)
               + " bid=" + _FmtPrice(plan.symbol, bid)
               + " ask=" + _FmtPrice(plan.symbol, ask)
               + " rr2=" + DoubleToString(rr2, 6)
               + " rr2_raw=" + DoubleToString(rr2, 10)
               + " source=final_plan");
      _Journal("[target_validation_rr] rr2_raw=" + DoubleToString(rr2, 10)
               + " min_rr=" + DoubleToString(min_rr, 10)
               + " eps=" + DoubleToString(rr_eps, 6)
               + " rr_floor_pass=" + (rr_floor_pass ? "true" : "false"));
      _Journal("[target_validation_overall] pass=" + (reject_reason == "ok" ? "true" : "false")
               + " reason=" + reject_reason
               + " rr_floor_pass=" + (rr_floor_pass ? "true" : "false")
               + " target_reached_pass=" + (target_reached ? "false" : "true")
               + " max_distance_pass=" + (max_distance_pass ? "true" : "false")
               + " direction_pass=" + (direction_valid ? "true" : "false"));

      if(reject_reason != "ok"){
         _TrackNamedCounter(m_funnel_target_validation_reasons, m_funnel_target_validation_counts, reject_reason);
         _TrackNamedCounter(m_total_target_validation_reasons, m_total_target_validation_counts, reject_reason);
         _Journal("[target_validation] pass=false stage=pre_watchlist"
                  + " reject_reason=" + reject_reason
                  + " chosen=" + chosen
                  + " rr2=" + DoubleToString(rr2, 6)
                  + " min_rr=" + DoubleToString(min_rr, 6)
                  + " obstacle_kind=" + plan.obstacle_kind
                  + " target_reached=" + (target_reached ? "true" : "false")
                  + " tp_direction_valid=" + (direction_valid ? "true" : "false")
                  + " detail=" + reject_detail);
         return false;
      }

      _Journal("[target_validation] pass=true stage=pre_watchlist"
               + " chosen=" + chosen
               + " rr2=" + DoubleToString(rr2, 6)
               + " min_rr=" + DoubleToString(min_rr, 6)
               + " obstacle_kind=" + plan.obstacle_kind
               + " target_reached=false"
               + " tp_direction_valid=true");
      return true;
   }

   string _StructuralPlanRebuildReason(const string reason) const {
      string r = _NormToken(reason);
      if(StringFind(r, "synthetic_fallback_crossed_obstacle_blocked") >= 0) return "synthetic_fallback_crossed_obstacle_blocked";
      if(StringFind(r, "target_already_reached") >= 0) return "target_already_reached";
      if(StringFind(r, "rr_too_low_after_arbitration") >= 0) return "rr_too_low_after_arbitration";
      if(StringFind(r, "ai_chosen_target_rr_too_low") >= 0) return "ai_chosen_target_rr_too_low";
      if(StringFind(r, "ai_chosen_capped_target_rr_too_low") >= 0) return "ai_chosen_capped_target_rr_too_low";
      if(StringFind(r, "ai_chosen_synthetic_fallback_rr_too_low") >= 0) return "ai_chosen_synthetic_fallback_rr_too_low";
      if(StringFind(r, "ai_chosen_target_invalid_direction") >= 0) return "invalid_tp_direction";
      if(StringFind(r, "invalid_tp_direction") >= 0) return "invalid_tp_direction";
      if(StringFind(r, "target_crossed_before_entry") >= 0) return "target_crossed_before_entry";
      if(StringFind(r, "target_model_unknown") >= 0) return "target_model_unknown";
      if(StringFind(r, "missing_ai_chosen_target") >= 0) return "missing_ai_chosen_target";
      if(StringFind(r, "ai_chosen_target_missing_tp") >= 0) return "ai_chosen_target_missing_tp";
      if(StringFind(r, "ai_partial_target_valid_but_runner_invalid") >= 0) return "ai_partial_target_valid_but_runner_invalid";
      if(StringFind(r, "ai_chosen_target_exceeds_max_distance") >= 0) return "ai_chosen_target_exceeds_max_distance";
      if(StringFind(r, "no_valid_stop") >= 0) return "no_valid_stop";
      if(StringFind(r, "no_valid_entry") >= 0) return "no_valid_entry";
      if(StringFind(r, "failed_to_rebuild_live_plan_prices_when_inner_reason_structural") >= 0)
         return "failed_to_rebuild_live_plan_prices_when_inner_reason_structural";
      return "";
   }

   bool IsStructuralPlanRebuildFailure(const string reason) const {
      return (StringLen(_StructuralPlanRebuildReason(reason)) > 0);
   }

   //+---------------------------------------------------------------+
   //| Execution failure classification and retry suppression.        |
   //|                                                                |
   //| One confirmed watchlist item produced 91 execution attempts and |
   //| 91 identical rejections in the eleventh run because every tick  |
   //| re-ran a rebuild that could not possibly succeed.  A failure    |
   //| that cannot change without an input changing must not be        |
   //| retried until an input actually changes.                        |
   //+---------------------------------------------------------------+
   //+---------------------------------------------------------------+
   //| Is a spread rejection transient?  Only while the spread moves.  |
   //|                                                                 |
   //| The 2026-09-05 replay logged 213 rejections at a CONSTANT       |
   //| "ticks=800.0" over two hours, every one classified              |
   //| TRANSIENT_SPREAD_FAILURE and given a bounded backoff -- a label |
   //| that asserted the value was changing while it demonstrably was  |
   //| not.  A spread that has not moved across                        |
   //| InpMaxPersistentSpreadAttempts consecutive rejections is a      |
   //| broker constraint, not a transient widening, and is classified  |
   //| as one so the plan stops re-attempting a settled outcome.       |
   //+---------------------------------------------------------------+
   string _ClassifySpreadFailure(const TradePlan &p, const double spread) const {
      double tick = _PlanTickSize(p.symbol);
      bool unchanged = (p.execution_failure_spread > 0.0 && spread > 0.0 &&
                        MathAbs(spread - p.execution_failure_spread) <= tick * 0.5);
      int consecutive = (unchanged ? p.execution_spread_unchanged_attempts + 1 : 1);
      if(InpMaxPersistentSpreadAttempts > 0 && consecutive >= InpMaxPersistentSpreadAttempts)
         return EXEC_FAIL_PERMANENT_BROKER;
      return EXEC_FAIL_TRANSIENT_SPREAD;
   }

   string _ClassifyExecutionFailure(const TradePlan &p, const string reason) const {
      // A class decided by the code that detected the failure DURING THIS ATTEMPT
      // wins: it knows more than a string match ever can.  Reading
      // p.execution_failure_class here instead would read the PREVIOUS attempt's
      // verdict, which made the first classification permanent -- a plan that first
      // failed on spread kept reporting TRANSIENT_SPREAD_FAILURE even after the
      // reason became a semantic change.
      if(StringLen(m_last_execution_failure_class) > 0 &&
         m_last_execution_failure_class != EXEC_FAIL_NONE)
         return m_last_execution_failure_class;

      string r = _NormToken(reason);
      if(StringLen(r) == 0) return EXEC_FAIL_NONE;

      if(StringFind(r, "execution_fingerprint_mismatch") >= 0 ||
         StringFind(r, "semantic_plan_changed") >= 0 ||
         StringFind(r, "candidate_hash_mismatch") >= 0)
         return EXEC_FAIL_SEMANTIC_PLAN_CHANGED;

      if(StringFind(r, "exceeds_max_distance") >= 0 ||
         StringFind(r, "target_already_reached") >= 0 ||
         StringFind(r, "target_no_longer_feasible") >= 0 ||
         StringFind(r, "no_feasible_target") >= 0 ||
         StringFind(r, "rr_too_low") >= 0 ||
         StringFind(r, "rr_below_live_floor") >= 0)
         return EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE;

      if(StringFind(r, "missing_live_bid") >= 0 || StringFind(r, "quote") >= 0)
         return EXEC_FAIL_TRANSIENT_QUOTE;
      // Reached only when the spread detector did not classify this attempt (a
      // spread mentioned by some other reject text).  The spread gate itself
      // always sets m_last_execution_failure_class, so the persistent case is
      // decided by _ClassifySpreadFailure, not by this string match.
      if(StringFind(r, "spread") >= 0)
         return EXEC_FAIL_TRANSIENT_SPREAD;

      if(StringFind(r, "broker_distance_invalid") >= 0 ||
         StringFind(r, "stop_freeze_distance") >= 0 ||
         StringFind(r, "broker stop") >= 0 ||
         StringFind(r, "volume") >= 0 ||
         StringFind(r, "market_closed") >= 0 ||
         StringFind(r, "broker_session_gate") >= 0)
         return EXEC_FAIL_PERMANENT_BROKER;

      if(IsStructuralPlanRebuildFailure(reason))
         return EXEC_FAIL_STRUCTURAL_INVALIDATION;

      // Portfolio/risk capacity and "another position is open" genuinely can
      // clear on their own, so they stay retryable but bounded.
      if(StringFind(r, "capacity") >= 0 ||
         StringFind(r, "already_open") >= 0 ||
         StringFind(r, "already has") >= 0 ||
         StringFind(r, "max_open_positions") >= 0)
         return EXEC_FAIL_TRANSIENT_BROKER;

      return EXEC_FAIL_STRUCTURAL_INVALIDATION;
   }

   // The inputs that can change an execution outcome.  If none of them moved,
   // rebuilding produces byte-identical inputs and therefore an identical
   // rejection, so there is nothing to learn from repeating it.
   string _ExecutionStateFingerprint(const TradePlan &p) {
      double bid = SymbolInfoDouble(p.symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(p.symbol, SYMBOL_ASK);
      string canonical = p.symbol + "|" + p.candidate_hash + "|"
                       + _CanonicalPrice(p.symbol, bid) + "|"
                       + _CanonicalPrice(p.symbol, ask) + "|"
                       + _CanonicalPrice(p.symbol, p.sl) + "|"
                       + _CanonicalPrice(p.symbol, p.tp2) + "|"
                       + p.po3.po3_state + "|"
                       + IntegerToString((int)_CountOpenPositions()) + "|"
                       + p.execution_failure_class;
      return _IntegrityHash(canonical);
   }

   // Returns true when this attempt must be skipped.
   bool _SuppressExecutionRetry(TradePlan &p, string &suppress_reason) {
      suppress_reason = "";
      if(StringLen(p.execution_failure_class) == 0 || p.execution_failure_class == EXEC_FAIL_NONE)
         return false;

      string state = _ExecutionStateFingerprint(p);
      if(ExecFailureIsTransient(p.execution_failure_class)){
         // A spread rejection can only clear when the SPREAD moves.  The generic
         // state fingerprint moves with bid or ask, so a drifting quote at a
         // constant spread unsuppressed the retry every tick: 213 prechecks and
         // 5,597 duplicate execution attempts for one plan whose spread never
         // changed.  Gate this class on the quantity it was actually rejected on.
         if(p.execution_failure_class == EXEC_FAIL_TRANSIENT_SPREAD &&
            p.execution_failure_spread > 0.0){
            double live_spread = _CurrentSpreadPrice(p.symbol);
            double tick = _PlanTickSize(p.symbol);
            if(MathAbs(live_spread - p.execution_failure_spread) <= tick * 0.5){
               suppress_reason = "unchanged_spread_after_" + p.execution_failure_class;
               p.execution_attempts_suppressed++;
               if(p.execution_attempts_suppressed == 1)
                  _Journal("[execution_retry_suppressed] symbol=" + p.symbol
                           + " failure_class=" + p.execution_failure_class
                           + " action=" + ExecFailureAction(p.execution_failure_class)
                           + " spread=" + _FmtPrice(p.symbol, live_spread)
                           + " reason=" + suppress_reason);
               return true;
            }
         }
         datetime now = _NowServerOrLocal();
         if(p.execution_retry_not_before > 0 && now < p.execution_retry_not_before){
            suppress_reason = "transient_backoff";
            p.execution_attempts_suppressed++;
            return true;
         }
         return false;
      }

      if(state == p.execution_failure_state_fingerprint){
         suppress_reason = "unchanged_state_after_" + p.execution_failure_class;
         p.execution_attempts_suppressed++;
         // Log the suppression exactly once, not once per tick.
         if(p.execution_attempts_suppressed == 1)
            _Journal("[execution_retry_suppressed] symbol=" + p.symbol
                     + " failure_class=" + p.execution_failure_class
                     + " action=" + ExecFailureAction(p.execution_failure_class)
                     + " state_fingerprint=" + state
                     + " reason=" + suppress_reason);
         return true;
      }
      return false;
   }

   void _RecordExecutionFailure(TradePlan &p, const string reason) {
      p.execution_precheck_attempts++;
      // Track the spread this attempt was rejected on BEFORE classifying, so the
      // consecutive-unchanged count the classifier already computed is the one
      // that gets stored.
      if(m_last_execution_spread > 0.0){
         double tick = _PlanTickSize(p.symbol);
         bool unchanged = (p.execution_failure_spread > 0.0 &&
                           MathAbs(m_last_execution_spread - p.execution_failure_spread) <= tick * 0.5);
         p.execution_spread_unchanged_attempts = (unchanged ? p.execution_spread_unchanged_attempts + 1 : 1);
         p.execution_failure_spread = m_last_execution_spread;
      } else {
         p.execution_spread_unchanged_attempts = 0;
         p.execution_failure_spread = 0.0;
      }
      p.execution_failure_class = _ClassifyExecutionFailure(p, reason);
      p.execution_failure_state_fingerprint = _ExecutionStateFingerprint(p);
      if(ExecFailureIsTransient(p.execution_failure_class)){
         // Bounded backoff: 1 simulated/real second per attempt, capped.
         int backoff_sec = (int)MathMin(30, MathMax(1, p.execution_precheck_attempts));
         p.execution_retry_not_before = _NowServerOrLocal() + backoff_sec;
      }
      _Journal("[execution_failure_class] symbol=" + p.symbol
               + " class=" + p.execution_failure_class
               + " action=" + ExecFailureAction(p.execution_failure_class)
               + " precheck_attempts=" + IntegerToString(p.execution_precheck_attempts)
               + " order_construction_attempts=" + IntegerToString(p.execution_order_construction_attempts)
               + " duplicate_execution_attempts=" + IntegerToString(p.execution_attempts_suppressed)
               + " failure_spread=" + _FmtPrice(p.symbol, p.execution_failure_spread)
               + " spread_unchanged_attempts=" + IntegerToString(p.execution_spread_unchanged_attempts)
               + " state_fingerprint=" + p.execution_failure_state_fingerprint
               + " reason=" + reason);
   }

   bool _ApplyAiTargetArbitration(TradePlan &p, const AiDecision &dec, string &reason) {
      reason = "ok";
      if(!p.target_arbitration_required && StringLen(dec.chosen_target_model) == 0)
         return true;

      bool arbitration_was_required = p.target_arbitration_required;
      string chosen_model = _NormToken(dec.chosen_target_model);
      if(StringLen(chosen_model) == 0){
         reason = (arbitration_was_required ? "invalid_ai_target_arbitration_response" : "target_arbitration_missing");
         if(arbitration_was_required)
            _Journal("[target_arbitration_parse] severity_missing=" + (!dec.target_blocker_severity_present ? "true" : "false")
                     + " class_missing=" + (!dec.target_blocker_class_present ? "true" : "false")
                     + " killer_missing=" + (!dec.target_blocker_is_trade_killer_present ? "true" : "false")
                     + " action=reject reason=invalid_ai_target_arbitration_response");
         return false;
      }
      bool missing_required_arbitration_fields =
         (arbitration_was_required &&
          (!dec.target_blocker_severity_present ||
           !dec.target_blocker_class_present ||
           !dec.target_blocker_is_trade_killer_present ||
           !dec.target_decision_reason_present ||
           StringLen(dec.target_blocker_class) == 0 ||
           StringLen(dec.target_decision_reason) == 0));
      if(missing_required_arbitration_fields){
         reason = "invalid_ai_target_arbitration_response";
         _Journal("[target_arbitration_parse] severity_missing=" + (!dec.target_blocker_severity_present ? "true" : "false")
                  + " class_missing=" + (!dec.target_blocker_class_present ? "true" : "false")
                  + " killer_missing=" + (!dec.target_blocker_is_trade_killer_present ? "true" : "false")
                  + " reason_missing=" + (!dec.target_decision_reason_present || StringLen(dec.target_decision_reason) == 0 ? "true" : "false")
                  + " action=reject reason=invalid_ai_target_arbitration_response");
         return false;
      }
      if(chosen_model == "reject_due_to_blocker"){
         reason = "ai_blocker_too_severe";
         return false;
      }
      if(chosen_model == "reject_due_to_weak_target"){
         reason = "ai_target_too_weak";
         return false;
      }

      double chosen_tp = dec.chosen_tp2;
      double chosen_tp1 = dec.chosen_tp1;
      string applied_source = dec.chosen_target_model;
      string applied_target_model = dec.chosen_target_model;
      string applied_tp_model = dec.chosen_target_model;

      bool chose_liquidity = (chosen_model == "liquidity_target" ||
                              chosen_model == "real_liquidity" ||
                              chosen_model == "real_liquidity_target" ||
                              (StringLen(p.liquidity_target_model) > 0 && chosen_model == _NormToken(p.liquidity_target_model)));
      bool chose_partial_liquidity = (chosen_model == "partial_before_obstacle_then_liquidity" ||
                                      chosen_model == "partial_then_liquidity");
      bool chose_capped = (StringFind(chosen_model, "capped") >= 0 ||
                           chosen_model == "partial_before_obstacle" ||
                           chosen_model == "capped_before_obstacle");
      bool chose_synthetic_capped = (chosen_model == "synthetic_rr_capped_to_max_distance" ||
                                     chosen_model == "ai_selected_synthetic_rr_capped_to_max_distance");
      bool chose_fallback = (chosen_model == "synthetic_rr_fallback" ||
                             chosen_model == "fallback" ||
                             chosen_model == "synthetic" ||
                             chose_synthetic_capped);
      if(!arbitration_was_required && chose_fallback && _ObstacleIsCrossedOpposing(p.obstacle_kind)){
         reason = "invalid_target_arbitration_required_flag";
         _Journal("[target_arbitration] required=false chosen=" + chosen_model
                  + " obstacle_kind=" + p.obstacle_kind
                  + " action=reject reason=invalid_target_arbitration_required_flag");
         return false;
      }
      if(chose_fallback){
         _RefreshTargetFeasibility(p, "ai_target_choice_validation");
         bool raw_fallback_choice = !chose_synthetic_capped;
         if(raw_fallback_choice && !p.fallback_feasible_for_tp2){
            _Journal("[ai_target_choice_validation] chosen=synthetic_rr_fallback feasible=false"
                     + " action=defer_to_target_sanitizer"
                     + " infeasible_reason=" + p.fallback_infeasible_reason
                     + " capped_feasible=" + (p.synthetic_capped_to_max_distance_feasible ? "true" : "false"));
            if(p.fallback_infeasible_reason == "exceeds_max_target_distance" &&
               p.synthetic_capped_to_max_distance_feasible){
               chosen_model = "synthetic_rr_capped_to_max_distance";
               chose_synthetic_capped = true;
               chosen_tp = p.synthetic_capped_to_max_distance_tp;
               applied_source = "ai_selected_synthetic_rr_capped_to_max_distance";
               applied_target_model = "synthetic_rr_capped_to_max_distance";
               applied_tp_model = "synthetic_rr_capped_to_max_distance";
            }
         } else {
            _Journal("[ai_target_choice_validation] chosen=" + chosen_model
                     + " feasible=true");
         }
      }
      if(arbitration_was_required && chose_fallback){
         bool missing_synthetic_explanation =
            (StringLen(dec.why_not_liquidity_target) == 0 ||
             StringLen(dec.why_not_partial_before_obstacle) == 0 ||
             StringLen(dec.why_not_capped_before_obstacle) == 0 ||
             StringLen(dec.target_decision_reason) == 0);
         if(missing_synthetic_explanation){
            reason = "invalid_ai_target_arbitration_response";
            _Journal("[target_arbitration_parse] severity_missing=false action=reject"
                     + " reason=invalid_ai_target_arbitration_response"
                     + " missing_synthetic_why_not=true"
                     + " chosen_target_model=" + chosen_model);
            return false;
         }
      }

      if(chose_liquidity){
         if(chosen_tp <= 0.0) chosen_tp = p.liquidity_target_preserved;
         applied_source = "ai_selected_liquidity_target";
         applied_target_model = (StringLen(p.liquidity_target_model) > 0 ? p.liquidity_target_model : "next_liquidity_session_range");
         applied_tp_model = "liquidity_target";
      } else if(chose_partial_liquidity){
         if(!InpAllowPartialBeforeObstacle){
            reason = "partial_before_obstacle_disabled";
            return false;
         }
         if(chosen_tp <= 0.0) chosen_tp = p.liquidity_target_preserved;
         if(chosen_tp1 <= 0.0) chosen_tp1 = p.capped_before_obstacle_tp;
         applied_source = "ai_selected_partial_then_liquidity";
         applied_target_model = "partial_before_obstacle_then_liquidity";
         applied_tp_model = "partial_then_liquidity";
      } else if(chose_capped){
         if(chosen_tp <= 0.0) chosen_tp = p.capped_before_obstacle_tp;
         applied_source = "ai_selected_capped_before_obstacle";
         applied_target_model = (StringLen(p.capped_before_obstacle_source) > 0 ? p.capped_before_obstacle_source : "cap_before_opposing_imbalance");
         applied_tp_model = "capped_before_obstacle";
      } else if(chose_fallback){
         if(chose_synthetic_capped){
            if(chosen_tp <= 0.0) chosen_tp = p.synthetic_capped_to_max_distance_tp;
            applied_source = "ai_selected_synthetic_rr_capped_to_max_distance";
            applied_target_model = "synthetic_rr_capped_to_max_distance";
            applied_tp_model = "synthetic_rr_capped_to_max_distance";
         } else {
            if(chosen_tp <= 0.0) chosen_tp = p.fallback_tp;
            applied_source = "ai_selected_synthetic_rr_fallback";
            applied_target_model = "synthetic_rr_fallback";
            applied_tp_model = "synthetic_rr_fallback";
         }
      } else if(chosen_model == "current" || chosen_model == "current_plan" || chosen_model == "keep_current"){
         chosen_tp = p.tp2;
         applied_source = (StringLen(p.target_source) > 0 ? p.target_source : p.tp_model);
         applied_target_model = (StringLen(p.target_model) > 0 ? p.target_model : applied_source);
         applied_tp_model = (StringLen(p.tp_model) > 0 ? p.tp_model : applied_source);
      } else {
         reason = "invalid_ai_target_arbitration_response";
         return false;
      }

      double rr = _TargetRR(p, chosen_tp);
      if(chosen_tp <= 0.0 || rr <= 0.0){
         if(chose_fallback){
            p.ai_chosen_target_model = applied_target_model;
            string sanitize_reason = "";
            if(_ApplyFeasibleTargetSanitizer(p, sanitize_reason))
               return true;
            reason = sanitize_reason;
            return false;
         }
         reason = "target_arbitration_invalid_target";
         return false;
      }

      double severity = dec.target_blocker_severity;
      if(!arbitration_was_required && severity < 0.0) severity = _ObstacleSeverity(p.obstacle_kind, p.obstacle_distance_r);
      string blocker_class = (StringLen(dec.target_blocker_class) > 0 ? _NormToken(dec.target_blocker_class) : (arbitration_was_required ? "unknown" : _ObstacleSeverityClass(severity)));
      bool killer = dec.target_blocker_is_trade_killer || blocker_class == "killer" || severity >= InpBlockerKillSeverity;
      if(arbitration_was_required && MathAbs(severity - 7.0) < 0.000001 && blocker_class == "major")
         m_funnel_blocker_severity_7_defaults_suspected++;

      bool crossed_obstacle = _ObstacleIsCrossedOpposing(p.obstacle_kind);
      if(crossed_obstacle && InpHardRejectCrossedObstacleTarget){
         _Journal("[target_gate] hard_reject_crossed_obstacle active=true reason=InpHardRejectCrossedObstacleTarget"
                  + " chosen_target_model=" + chosen_model);
         reason = "synthetic_fallback_crossed_obstacle_blocked";
         return false;
      }
      bool final_synthetic_fallback = (chose_fallback || _TokenIsSyntheticFallback(applied_source) || _TokenIsSyntheticFallback(applied_tp_model));
      if(final_synthetic_fallback && crossed_obstacle && InpRejectSyntheticFallbackAfterCrossedObstacle){
         if(arbitration_was_required && InpRequireAITargetArbitrationOnObstacle && !InpHardRejectCrossedObstacleTarget){
            _Journal("[target_gate] legacy_synthetic_fallback_crossed_obstacle_gate_skipped"
                     + " reason=ai_arbitration_active hard_reject=false"
                     + " chosen_target_model=" + chosen_model);
         } else {
            reason = "synthetic_fallback_crossed_obstacle_blocked";
            return false;
         }
      }
      if(chose_liquidity && p.liquidity_target_blocked_by_obstacle){
         if(killer){
            reason = "liquidity_target_blocked_by_killer_obstacle";
            return false;
         }
         if(!InpAllowAIToUseLiquidityTargetBehindMinorBlocker && severity > InpBlockerMinorMaxSeverity){
            reason = "liquidity_target_blocker_not_minor";
            return false;
         }
      }
      if(chose_capped && !chose_partial_liquidity && !InpAllowPartialBeforeObstacle){
         reason = "partial_before_obstacle_disabled";
         return false;
      }

      p.tp2 = chosen_tp;
      p.tp_model = applied_tp_model;
      p.target_source = applied_source;
      p.target_model = applied_target_model;
      p.effective_rr2 = rr;
      p.ai_chosen_target_model = applied_target_model;
      p.ai_chosen_tp1 = chosen_tp1;
      p.ai_chosen_tp2 = chosen_tp;
      p.ai_chosen_rr2 = (dec.chosen_rr2 > 0.0 ? dec.chosen_rr2 : rr);
      p.ai_rejected_target_models = dec.rejected_target_models_json;
      p.ai_blocker_severity = severity;
      p.ai_blocker_class = blocker_class;
      p.ai_blocker_is_trade_killer = killer;
      p.target_decision_reason = dec.target_decision_reason;
      p.why_not_liquidity_target = dec.why_not_liquidity_target;
      p.why_not_partial_before_obstacle = dec.why_not_partial_before_obstacle;
      p.why_not_capped_before_obstacle = dec.why_not_capped_before_obstacle;
      p.why_not_synthetic_fallback = dec.why_not_synthetic_fallback;
      // The obstacle label is engine-owned evidence, never a model output.  Letting
      // dec.target_blocker_kind overwrite p.obstacle_kind made the model's free-text
      // spelling the plan's frozen identity (_LockAssessedPlan -> assessed_obstacle_kind),
      // while execution re-derived the engine's own spelling through
      // _PublishObstacleEvidence -- so the two could never match and the plan died as
      // execution_fingerprint_mismatch:obstacle_kind.  That killed 4 of the 11 approvals
      // in the 2026-09-05 week replay: the model wrote "opposing_htf_imbalance",
      // "opposing_imbalance", "fresh_htf_opposing_imbalance" and "crossed_session_high"
      // for obstacles this engine names "crossed_htf_opposing_imbalance" and
      // "crossed_opposing_imbalance" -- identical severity, different vocabulary.
      // The model's view stays available as p.ai.target_blocker_kind; its severity,
      // class and killer flag already had their own ai_* fields above.
      if(StringLen(dec.target_blocker_kind) > 0 &&
         _BaseObstacleKind(dec.target_blocker_kind) != _BaseObstacleKind(p.obstacle_kind))
         _Journal("[obstacle_label_authority] symbol=" + p.symbol
                  + " engine_obstacle_kind=" + (StringLen(p.obstacle_kind) > 0 ? p.obstacle_kind : "none")
                  + " model_target_blocker_kind=" + dec.target_blocker_kind
                  + " owner=deterministic action=model_label_kept_diagnostic_only");
      if(chosen_tp1 > 0.0){
         p.tp1 = chosen_tp1;
         // The AI arbitrated this first leg; the generic R-multiple builder downstream
         // must not silently move it.
         p.tp1_from_target_model = true;
      }
      if(!_FinalizeFirstLeg(p, false, reason)) return false;
      p.target_arbitration_required = false;
      _NormalizeTargetLabels(p);
      _PersistNormalizedTargetArbitration(p, dec);
      string target_choice_counter = p.tp_model;
      if(chose_liquidity) target_choice_counter = "liquidity_target";
      else if(chose_partial_liquidity) target_choice_counter = "partial_before_obstacle_then_liquidity";
      else if(chose_capped) target_choice_counter = "capped_before_obstacle";
      else if(chose_synthetic_capped) target_choice_counter = "synthetic_rr_capped_to_max_distance";
      else if(chose_fallback) target_choice_counter = "synthetic_rr_fallback";
      _TrackNamedCounter(m_funnel_target_choice_models, m_funnel_target_choice_counts, target_choice_counter);
      _TrackNamedCounter(m_funnel_blocker_classes, m_funnel_blocker_class_counts, blocker_class);
      _TrackNamedCounter(m_total_target_choice_models, m_total_target_choice_counts, target_choice_counter);
      if(chose_capped && StringLen(p.target_model) > 0)
         _TrackNamedCounter(m_total_target_choice_models, m_total_target_choice_counts, p.target_model);
      _TrackNamedCounter(m_total_blocker_classes, m_total_blocker_class_counts, blocker_class);
      _Journal("[target_arbitration] required=" + (arbitration_was_required ? "true" : "false")
               + " chosen=" + p.target_model
               + " blocker_class=" + blocker_class
               + " severity=" + DoubleToString(severity, 2)
               + " allow=true"
               + " llm_quality_score=" + DoubleToString(dec.llm_quality_score, 2)
               + " llm_self_reported_confidence_diagnostic=" + DoubleToString(dec.llm_self_reported_confidence, 2));
      _Journal("[target_apply] chosen=" + p.target_model
               + " tp1=" + _FmtPrice(p.symbol, p.tp1)
               + " tp2=" + _FmtPrice(p.symbol, p.tp2)
               + " rr1=" + DoubleToString(_TargetRR(p, p.tp1), 2)
               + " rr2=" + DoubleToString(_ExecutionRR2(p), 2)
               + " partial_pct=" + DoubleToString(InpTP1PartialPct, 2)
               + " source=" + p.target_source);
      return true;
   }

   // The single definition of "a first leg the execution layer will actually place".
   // _BuildPlanPrices has always enforced this when it builds TP1; target *selection*
   // did not know about it, so it could choose a partial-before-obstacle route whose
   // first leg the builder then had to move -- past the very obstacle the route existed
   // to respect.  One definition, two callers, so the route and the price cannot disagree.
   //+---------------------------------------------------------------+
   //| Which stop distance the geometry half of the TP1 floor measures. |
   //|                                                                 |
   //| The floor has two halves and they answer different questions:   |
   //|   spread * InpMinTP1SpreadMult -- can the broker place this leg |
   //|     at all?  A live question, always measured live.             |
   //|   0.65 of the stop distance -- is this leg a meaningful fraction |
   //|     of the risk taken?  A property of the PLAN's geometry.      |
   //|                                                                 |
   //| Measuring the geometry half against the *rebuilt* stop is what  |
   //| killed three of the eleven approvals in the 2026-09-05 replay.  |
   //| The contract authorises the entry to drift (InpMaxEntryDriftR)  |
   //| and the stop to move (max_sl_drift_r), so at execution time     |
   //| stop_dist grows while the approved TP1 price is frozen: the     |
   //| floor rises and the leg's reward falls, both from the same       |
   //| authorised drift.  Satisfying it would have required the plan to |
   //| carry tp1_reward >= 0.65R + 1.65d, i.e. TP1 at 1.31R for the     |
   //| permitted d = 0.4R -- unreachable for a partial first leg.       |
   //|                                                                 |
   //| AUDUSD:  stop 0.00233 -> 0.00293, floor 0.00190, leg 0.00171     |
   //|          (short by 1.9 points) while live_rr2 1.05 > min_rr 0.90.|
   //| GBPJPY:  floor 0.115 -> 0.138, leg 0.106, RR2 would have been 3.35|
   //|                                                                 |
   //| So for a locked plan the geometry half is measured against the   |
   //| stop the plan was APPROVED on.  The invariant this buys: a leg   |
   //| that cleared the floor at approval cannot fail it at execution   |
   //| because of drift the contract already authorised and separately  |
   //| validated (max_entry_drift_r, max_sl_drift_r, min_resulting_rr). |
   //| Nothing is loosened -- the live spread half still binds, and an  |
   //| unlocked plan is measured live exactly as before.                |
   //+---------------------------------------------------------------+
   double _Tp1FloorStopDistance(const TradePlan &p, const double live_stop_dist) const {
      if(p.assessed_plan_locked && p.assessed_stop_distance > 0.0)
         return p.assessed_stop_distance;
      return live_stop_dist;
   }

   //--- Half one: is this leg a meaningful fraction of the risk being taken?
   //--- A property of the PLAN, so a locked plan is measured against the stop it
   //--- was approved on.
   double _MinTp1GeometryReward(const TradePlan &p, const double live_stop_dist) const {
      double geometry_stop_dist = _Tp1FloorStopDistance(p, live_stop_dist);
      return geometry_stop_dist * 0.65;
   }

   //--- Half two: can the broker actually place this leg right now?  Always live,
   //--- in both states, because it is a live broker constraint.
   double _MinTp1SpreadReward(const TradePlan &p) {
      return _CurrentSpreadPrice(p.symbol) * InpMinTP1SpreadMult;
   }

   // Not const: _CurrentSpreadPrice queries live symbol state.  Both callers
   // (_SelectObstacleAwareTarget and _BuildPlanPrices) are non-const anyway.
   double _MinTp1Reward(const TradePlan &p, const double stop_dist) {
      return MathMax(_MinTp1GeometryReward(p, stop_dist), _MinTp1SpreadReward(p));
   }

   //+---------------------------------------------------------------+
   //| The first leg the floor is asking ABOUT, for the geometry half.  |
   //|                                                                 |
   //| Anchoring only the stop is not enough.  The contract also        |
   //| authorises the ENTRY to drift (InpMaxEntryDriftR = 0.4), and the |
   //| route-owned TP1 is a frozen price, so an adverse entry move      |
   //| shrinks the leg's measured reward on its own.  GBPJPY: the leg   |
   //| was 0.142 against a 0.115 plan-time floor -- comfortably clear   |
   //| -- and became 0.106 purely because the entry moved 212.693 ->    |
   //| 212.657.  Re-measuring an approval-time ratio at drifted prices  |
   //| is double jeopardy for a drift the contract already bounds and   |
   //| separately validates.                                            |
   //|                                                                 |
   //| So the geometry half asks its question of the plan that was      |
   //| approved.  What still protects the trade at execution, unchanged: |
   //|   - max_entry_drift_r / max_sl_drift_r bound the drift itself;    |
   //|   - min_resulting_rr re-checks the LIVE economics;                |
   //|   - the spread half below is measured from the LIVE entry;        |
   //|   - wrong-side and beyond-target are checked on LIVE prices.      |
   //+---------------------------------------------------------------+
   // Not const: _RewardToTarget is not const.
   double _Tp1GeometryLegReward(const TradePlan &p, const double live_leg_reward) {
      if(p.assessed_plan_locked && p.assessed_tp1 > 0.0 && p.assessed_entry > 0.0){
         double assessed_leg = _RewardToTarget(p.is_buy, p.assessed_entry, p.assessed_tp1);
         if(assessed_leg > 0.0) return assessed_leg;
      }
      return live_leg_reward;
   }

   bool _FinalizeFirstLeg(TradePlan &p, const bool allow_generic_adjustment, string &reason) {
      double risk = MathAbs(p.entry_est-p.sl);
      double target_reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp2);
      double leg = _RewardToTarget(p.is_buy, p.entry_est, p.tp1);
      double geometry_floor = _MinTp1GeometryReward(p, risk);
      double spread_floor = _MinTp1SpreadReward(p);
      double epsilon = MathMax(risk, 0.00000001)*0.00000001;
      if(allow_generic_adjustment && !p.assessed_plan_locked && !p.tp1_from_target_model){
         // Target sanitization can replace TP2 after the generic TP1 builder.
         // Reconcile the two legs before freezing/serializing the candidate.
         // Previously a 0.70*old-TP2 cap undid the 0.65R floor; the sanitizer
         // then extended TP2 and left a first leg the execution gate would reject.
         double minimum = MathMax(geometry_floor, spread_floor);
         double maximum = target_reward*0.70;
         if(minimum > maximum+epsilon){
            reason = "target_model_tp1_below_min_reward";
            return false;
         }
         double adjusted = MathMin(maximum, MathMax(minimum, leg));
         if(MathAbs(adjusted-leg) > epsilon){
            p.tp1 = (p.is_buy ? p.entry_est+adjusted : p.entry_est-adjusted);
            // Claim the leg.  Reconciling it and then leaving it unclaimed is the
            // exact hazard the tp1-authority contract exists to stop: the next
            // _BuildPlanPrices takes the generic branch, rebuilds the leg from
            // tp1_r_multiple and lands somewhere else, so the leg frozen into the
            // request is not the leg the execution gate later measures.  That is how
            // AUDUSD 2026.08.03 shipped a 0.583R first leg to the model and then met
            // its own 0.65R floor at execution.  From here the leg is validated, never
            // silently rebuilt -- and this value is already inside [minimum, maximum],
            // so the route-owned branch it now takes accepts it.
            //
            // This CHANGES p.tp1 and therefore candidate_hash on exactly the plans
            // whose first leg was mis-built, so it retires their recorded replay
            // artifacts: measured on the 2026.08.03-08 cohort, ai_cache_hits fell
            // 1256 -> 1102 and misses rose 109 -> 254.  That is the correct trade:
            // those artifacts recorded decisions about plans this engine no longer
            // builds.  Judging those setups again needs a fresh RECORD_ONLY export.
            p.tp1_from_target_model = true;
            _Journal("[first_leg_finalized] symbol=" + p.symbol
                     + " previous_reward=" + _FmtPrice(p.symbol, leg)
                     + " final_reward=" + _FmtPrice(p.symbol, adjusted)
                     + " geometry_floor=" + _FmtPrice(p.symbol, geometry_floor)
                     + " spread_floor=" + _FmtPrice(p.symbol, spread_floor)
                     + " max_leg=" + _FmtPrice(p.symbol, maximum)
                     + " owner=engine_reconciled"
                     + " stage=before_request_identity action=align_with_final_target_and_floor");
            leg = adjusted;
         }
      }
      if(risk <= 0.0 || leg <= 0.0){ reason = "target_model_tp1_wrong_side_of_entry"; return false; }
      if(leg >= target_reward){ reason = "target_model_tp1_beyond_target"; return false; }
      double geometry_leg = _Tp1GeometryLegReward(p, leg);
      if(geometry_leg+epsilon < geometry_floor || leg+epsilon < spread_floor){
         reason = "target_model_tp1_below_min_reward";
         _Journal("[first_leg_validation] valid=false symbol=" + p.symbol
                  + " leg_reward=" + _FmtPrice(p.symbol, geometry_leg)
                  + " geometry_floor=" + _FmtPrice(p.symbol, geometry_floor)
                  + " spread_floor=" + _FmtPrice(p.symbol, spread_floor)
                  + " approved_plan=" + (allow_generic_adjustment ? "false" : "true")
                  + " action=reject_without_rewriting_approval");
         return false;
      }
      reason = "ok";
      return true;
   }

   void _RankAddTarget(TargetRankCandidate &list[], const string kind, const double price,
                       const double partial_tp, const double partial_rr, const bool partial_ok,
                       const double rr, const bool crosses,
                       const double severity, const string obstacle_kind, const double obstacle_price,
                       const bool ok_rr, const bool ok_dist, const bool truncated,
                       const string reject_reason) const {
      int n = ArraySize(list);
      ArrayResize(list, n + 1);
      list[n].kind                  = kind;
      list[n].price                 = price;
      list[n].partial_tp            = partial_tp;
      list[n].partial_rr            = partial_rr;
      list[n].partial_meets_floor   = partial_ok;
      list[n].rr                    = rr;
      list[n].crosses_obstacle      = crosses;
      list[n].obstacle_severity     = severity;
      list[n].obstacle_kind         = obstacle_kind;
      list[n].obstacle_price        = obstacle_price;
      list[n].meets_rr_floor        = ok_rr;
      list[n].meets_min_distance    = ok_dist;
      list[n].truncated_by_obstacle = truncated;
      list[n].order                 = n;
      list[n].reject_reason         = reject_reason;
   }

   bool _TargetRankEligible(const TargetRankCandidate &c) const {
      // A route that declares a first leg is only eligible if that leg is one the
      // execution layer can place as-is.  Without this the runner's RR alone decided
      // admission and the partial was never measured at all.
      if(c.partial_tp > 0.0 && !c.partial_meets_floor) return false;
      return (c.price > 0.0 && c.rr > 0.0 && c.meets_rr_floor && c.meets_min_distance &&
              StringLen(c.reject_reason) == 0);
   }

   bool _TargetRankCrossesMajor(const TargetRankCandidate &c) const {
      return (c.crosses_obstacle && c.obstacle_severity >= InpBlockerMajorSeverity);
   }

   bool _TargetRankObstacleInvolved(const TargetRankCandidate &c) const {
      return (c.crosses_obstacle || c.truncated_by_obstacle);
   }

   // Ordering contract:
   //   1. eligible routes beat ineligible ones;
   //   2. a route crossing a MAJOR obstacle ranks below every non-crossing route no
   //      matter how much more RR it offers -- crossing is a demotion, not a tie-break;
   //   3. once an obstacle is in play and neither route crosses a *major* one, the
   //      obstacle has already been priced into each route's reward, so the higher RR
   //      wins.  Without this a 0.57R route that stops in front of a minor imbalance
   //      would beat a 3.07R partial route purely because it was collected first;
   //   4. otherwise keep collection order, which is priority order.  Rule 4 is what
   //      keeps the pre-existing choice identical whenever no obstacle is involved.
   bool _TargetRankBetter(const TargetRankCandidate &a, const TargetRankCandidate &b) const {
      bool ea = _TargetRankEligible(a);
      bool eb = _TargetRankEligible(b);
      if(ea != eb) return ea;
      bool ca = _TargetRankCrossesMajor(a);
      bool cb = _TargetRankCrossesMajor(b);
      if(ca != cb) return (!ca);
      if(_TargetRankObstacleInvolved(a) || _TargetRankObstacleInvolved(b)){
         if(MathAbs(a.rr - b.rr) > _RREps()) return (a.rr > b.rr);
      }
      return (a.order < b.order);
   }

   int _PickBestRankedTarget(const TargetRankCandidate &list[]) const {
      int best = -1;
      for(int i=0; i<ArraySize(list); i++){
         if(!_TargetRankEligible(list[i])) continue;
         if(best < 0 || _TargetRankBetter(list[i], list[best])) best = i;
      }
      return best;
   }

   bool _RankedListHasCleanRoute(const TargetRankCandidate &list[]) const {
      for(int i=0; i<ArraySize(list); i++){
         if(_TargetRankEligible(list[i]) && !_TargetRankCrossesMajor(list[i])) return true;
      }
      return false;
   }

   // Best eligible route that passes THROUGH an obstacle.  Only consulted once every
   // clean route has been ruled out and the alternative is a synthetic that crosses the
   // same obstacle: at that point the crossing risk is already being taken, so the
   // decision is purely which crossing route pays more.  A killer-severity crossing is
   // never a candidate -- that is a "do not trade" signal, not a target choice.
   int _PickBestCrossingRoute(const TargetRankCandidate &list[]) const {
      int best = -1;
      for(int i=0; i<ArraySize(list); i++){
         if(!_TargetRankEligible(list[i])) continue;
         if(!list[i].crosses_obstacle) continue;
         if(list[i].obstacle_severity >= InpBlockerKillSeverity) continue;
         if(best < 0 || list[i].rr > list[best].rr) best = i;
      }
      return best;
   }

   void _LogTargetRank(const TradePlan &p, const TargetRankCandidate &list[], const int chosen_index) const {
      // One line per route per candidate: the single largest producer in the journal
      // at 51.7% of its bytes.  Route detail, so level 2.
      if(!_JournalDetailEnabled(2)) return;
      for(int i=0; i<ArraySize(list); i++){
         _Journal("[target_rank] symbol=" + p.symbol
                  + " kind=" + list[i].kind
                  + " tp=" + _FmtPrice(p.symbol, list[i].price)
                  + " rr=" + DoubleToString(list[i].rr, 4)
                  + " partial_tp=" + (list[i].partial_tp > 0.0 ? _FmtPrice(p.symbol, list[i].partial_tp) : "none")
                  + " partial_rr=" + DoubleToString(list[i].partial_rr, 4)
                  + " partial_meets_floor=" + (list[i].partial_tp > 0.0
                                               ? (list[i].partial_meets_floor ? "true" : "false")
                                               : "n/a")
                  + " crosses_obstacle=" + (list[i].crosses_obstacle ? "true" : "false")
                  + " truncated=" + (list[i].truncated_by_obstacle ? "true" : "false")
                  + " obstacle=" + (StringLen(list[i].obstacle_kind) > 0 ? list[i].obstacle_kind : "none")
                  + " severity=" + DoubleToString(list[i].obstacle_severity, 2)
                  + " meets_rr_floor=" + (list[i].meets_rr_floor ? "true" : "false")
                  + " meets_min_distance=" + (list[i].meets_min_distance ? "true" : "false")
                  + " eligible=" + (_TargetRankEligible(list[i]) ? "true" : "false")
                  + " reject=" + (StringLen(list[i].reject_reason) > 0 ? list[i].reject_reason : "none")
                  + " chosen=" + (i == chosen_index ? "true" : "false"));
      }
   }

   bool _ApplyRankedTarget(TradePlan &p, const TargetRankCandidate &c, const double stop_dist) {
      p.tp2            = c.price;
      p.tp_model       = c.kind;
      p.target_source  = c.kind;
      p.target_model   = c.kind;
      // Publish the obstacle with its severity, not just its name.  A route selected here
      // used to leave obstacle_strength_features at whatever an earlier stage happened to
      // set -- empty whenever no liquidity target existed to trigger the only writer.
      _PublishObstacleEvidence(p, c.obstacle_kind, c.obstacle_price, stop_dist, c.crosses_obstacle);
      p.obstacle_r     = c.rr;
      p.effective_rr2  = c.rr;
      if(c.partial_tp > 0.0){
         // Partial-then-liquidity banks a first leg in front of the obstacle and runs
         // the remainder to the structural objective.  Publish the partial level so
         // downstream partial management and the AI arbitration payload agree on the
         // same two legs.
         p.capped_before_obstacle_tp = c.partial_tp;
         p.capped_before_obstacle_rr = (stop_dist > 0.0
                                        ? _RewardToTarget(p.is_buy, p.entry_est, c.partial_tp) / stop_dist
                                        : 0.0);
         if(StringLen(p.capped_before_obstacle_source) == 0)
            p.capped_before_obstacle_source = "capped_before_" + c.obstacle_kind;
         // Publishing the level was never enough: _BuildPlanPrices rebuilt tp1 from
         // tp1_r_multiple afterwards and its 0.65R floor pushed the first leg past the
         // obstacle.  The route owns its first leg -- say so, and the builder honours it.
         p.tp1                  = c.partial_tp;
         p.tp1_from_target_model = true;
      } else {
         p.tp1_from_target_model = false;
      }
      _MaybeRequireTargetArbitration(p, p.tp2, p.target_source);
      return true;
   }

   bool _SelectObstacleAwareTarget(TradePlan &p, const double stop_dist, const double min_target_dist,
                                   const double max_target_dist, string &reason) {
      reason = "ok";
      p.tp_model = "";
      p.target_source = "";
      p.obstacle_kind = "";
      p.obstacle_price = 0.0;
      p.obstacle_r = 0.0;
      p.effective_rr2 = 0.0;
      // Re-selection re-decides who owns the first leg.  Clearing it here means a
      // route that no longer wins cannot leave a stale tp1 claim behind.
      p.tp1_from_target_model = false;
      // Publish the first-leg floor before any route is scored, so the ranking, the
      // price builder and the AI target-candidate payload all read one number.
      p.min_tp1_reward = _MinTp1Reward(p, stop_dist);
      _ResetTargetArbitrationFields(p);

      PriceLevelCandidate targets[];
      PriceLevelCandidate obstacles[];
      _CollectTargetLevels(p, p.entry_est, targets, obstacles);
      _SeedTargetArbitrationCandidates(p, stop_dist, obstacles);

      bool saw_too_near = false;
      bool saw_liquidity_too_near = false;
      bool saw_valid_target = false;
      // PLAN B: a structural target clamped back to an obstacle is a *different*
      // failure from one that is genuinely too close.  Collapsing both into
      // saw_too_near destroyed the diagnosis and handed control to the synthetic.
      bool saw_truncated_by_obstacle = false;
      bool structural_target_exists = false;
      double min_rr = MathMax(0.0, _FamilyMinRR(p));

      TargetRankCandidate ranked[];
      ArrayResize(ranked, 0);

      for(int i=0; i<ArraySize(targets); i++){
         double candidate_target = targets[i].price;
         if(!_IsRewardSideLevel(p.is_buy, p.entry_est, candidate_target)) continue;
         structural_target_exists = true;

         string obstacle_kind = "";
         double obstacle_price = 0.0;
         double effective_target = candidate_target;
         bool truncated = _NearestObstacleBeforeTarget(p, p.entry_est, candidate_target, obstacles,
                                                       obstacle_kind, obstacle_price, effective_target);

         double reward = _RewardToTarget(p.is_buy, p.entry_est, effective_target);
         if(reward <= 0) continue;
         saw_valid_target = true;

         double rr = (stop_dist > 0 ? reward / stop_dist : 0.0);
         double obstacle_r = ((obstacle_price > 0.0 && stop_dist > 0)
                              ? MathAbs(obstacle_price - p.entry_est) / stop_dist : 0.0);
         double severity = (StringLen(obstacle_kind) > 0 ? _ObstacleSeverity(obstacle_kind, obstacle_r) : 0.0);
         bool ok_dist = (min_target_dist <= 0 || reward >= min_target_dist);
         bool ok_rr = _RRMeetsFloor(rr, min_rr);

         string reject = "";
         if(!ok_dist || !ok_rr){
            saw_too_near = true;
            if(targets[i].priority <= 1) saw_liquidity_too_near = true;
            if(truncated){
               saw_truncated_by_obstacle = true;
               reject = "truncated_by_obstacle_below_floor";
            } else {
               reject = (ok_dist ? "below_rr_floor" : "below_min_target_distance");
            }
         }
         if(InpRejectAgainstHtfImbalance && obstacle_kind == "htf_opposing_imbalance" &&
            rr < InpObstacleMinStopMult)
            reject = "htf_opposing_imbalance_too_near";

         // The clamped route stops in front of the obstacle, so it never crosses.
         _RankAddTarget(ranked, (truncated ? "capped_before_" + obstacle_kind : targets[i].kind),
                        effective_target, 0.0, 0.0, true, rr, false, severity, obstacle_kind, obstacle_price,
                        ok_rr, ok_dist, truncated, reject);

         // PLAN A step 4 / PLAN B step 2: when the obstacle is the only reason the
         // structural objective failed, offer partial-then-liquidity as a first-class
         // route rather than dropping to a synthetic that ignores that same obstacle.
         if(truncated){
            double full_reward = _RewardToTarget(p.is_buy, p.entry_est, candidate_target);
            double full_rr = (stop_dist > 0 ? full_reward / stop_dist : 0.0);
            if(full_reward > 0.0){
               bool p_ok_rr = _RRMeetsFloor(full_rr, min_rr);
               bool p_ok_dist = (min_target_dist <= 0 || full_reward >= min_target_dist);
               // Both flags above describe the RUNNER.  The first leg -- the whole point
               // of this route -- has its own economics and must clear the same minimum
               // TP1 reward the execution layer enforces, otherwise the route is asking
               // for a partial nobody can place.  A 0.0108R leg used to pass a gate
               // literally named after it because only the 45.67R runner was measured.
               double partial_reward = _RewardToTarget(p.is_buy, p.entry_est, effective_target);
               double partial_rr = (stop_dist > 0 ? partial_reward / stop_dist : 0.0);
               bool p_ok_leg = (partial_reward > 0.0 && partial_reward >= p.min_tp1_reward &&
                                partial_reward < full_reward);
               // A newly promoted route must obey the same distance ceiling every other
               // route obeys; otherwise partial-then-liquidity becomes a way to smuggle
               // a target past InpMaxTargetAdrFrac / InpMaxTargetAtrMult.
               string p_reject = "";
               if(max_target_dist > 0.0 && full_reward > max_target_dist)
                  p_reject = "partial_runner_exceeds_max_target_distance";
               else if(!p_ok_leg)
                  p_reject = "partial_leg_below_tp1_floor";
               else if(!p_ok_rr || !p_ok_dist)
                  p_reject = "partial_runner_below_floor";
               if(!p_ok_leg && _JournalDetailEnabled(2))
                  _Journal("[partial_leg_gate] symbol=" + p.symbol
                           + " entry=" + _FmtPrice(p.symbol, p.entry_est)
                           + " partial_tp=" + _FmtPrice(p.symbol, effective_target)
                           + " partial_reward=" + _FmtPrice(p.symbol, partial_reward)
                           + " partial_rr=" + DoubleToString(partial_rr, 4)
                           + " min_tp1_reward=" + _FmtPrice(p.symbol, p.min_tp1_reward)
                           + " runner_tp=" + _FmtPrice(p.symbol, candidate_target)
                           + " runner_rr=" + DoubleToString(full_rr, 4)
                           + " obstacle=" + obstacle_kind
                           + " severity=" + DoubleToString(severity, 2)
                           + " action=route_ineligible");
               _RankAddTarget(ranked, "partial_before_obstacle_then_liquidity", candidate_target,
                              effective_target, partial_rr, p_ok_leg, full_rr, true, severity,
                              obstacle_kind, obstacle_price,
                              p_ok_rr, p_ok_dist, true, p_reject);

               // The obstacle must bind the same way for EVERY route.  The synthetic
               // fallback below is measured straight through this obstacle and therefore
               // clears the RR floor, while this structural target is only ever scored on
               // its clamped reward and discarded.  That asymmetry is what made
               // "cross it for 1.05R" beat "cross it for 4.75R" and produced
               // ai_veto_target_arbitration_incoherent: "the feasible liquidity target was
               // not selected".  Offer the same target taken in full, explicitly marked as
               // crossing, so the two are comparable when no clean route survives.
               // It is a crossing route, so _TargetRankBetter still ranks it below every
               // non-crossing option and the clean-route path is untouched.
               string through_reject = "";
               if(max_target_dist > 0.0 && full_reward > max_target_dist)
                  through_reject = "through_obstacle_exceeds_max_target_distance";
               else if(!p_ok_rr || !p_ok_dist)
                  through_reject = "through_obstacle_below_floor";
               _RankAddTarget(ranked, targets[i].kind, candidate_target,
                              0.0, 0.0, true, full_rr, true, severity,
                              obstacle_kind, obstacle_price,
                              p_ok_rr, p_ok_dist, false, through_reject);
            }
         }
      }

      double swing_range = MathAbs(p.po3.swing_high - p.po3.swing_low);
      if(InpTPUseFibExtension && swing_range > 0){
         double fib_target = (p.is_buy ? p.po3.swing_high + swing_range * (InpTPFibExtension - 1.0)
                                       : p.po3.swing_low  - swing_range * (InpTPFibExtension - 1.0));
         if(_IsRewardSideLevel(p.is_buy, p.entry_est, fib_target)){
            string obstacle_kind = "";
            double obstacle_price = 0.0;
            double effective_target = fib_target;
            _NearestObstacleBeforeTarget(p, p.entry_est, fib_target, obstacles,
                                         obstacle_kind, obstacle_price, effective_target);
            double reward = _RewardToTarget(p.is_buy, p.entry_est, effective_target);
            if(reward > 0){
               saw_valid_target = true;
               structural_target_exists = true;
               double fib_rr = (stop_dist > 0 ? reward / stop_dist : 0.0);
               double fib_obstacle_r = ((obstacle_price > 0.0 && stop_dist > 0)
                                        ? MathAbs(obstacle_price - p.entry_est) / stop_dist : 0.0);
               double fib_severity = (StringLen(obstacle_kind) > 0
                                      ? _ObstacleSeverity(obstacle_kind, fib_obstacle_r) : 0.0);
               bool fib_ok_dist = (min_target_dist <= 0 || reward >= min_target_dist);
               bool fib_ok_rr = _RRMeetsFloor(fib_rr, min_rr);
               if(!fib_ok_dist || !fib_ok_rr) saw_too_near = true;
               _RankAddTarget(ranked, "fib_extension", effective_target, 0.0, 0.0, true, fib_rr, false,
                              fib_severity, obstacle_kind, obstacle_price,
                              fib_ok_rr, fib_ok_dist, false,
                              ((fib_ok_rr && fib_ok_dist) ? "" : "below_rr_floor"));
            }
         }
      }

      // The synthetic fallback is the safety net that every LATER stage re-reads:
      // _ApplyFeasibleTargetSanitizer, the AI target-arbitration payload and the live
      // plan rebuild all consult p.fallback_tp.  Plan A only requires that a crossing
      // synthetic never OUTRANK a clean structural route -- not that it go uncomputed.
      // Publishing it here, before the structural early return, keeps both guarantees:
      // the structural route still wins selection, and the net still exists downstream.
      // (Leaving it unset made the sanitizer report fallback_reason=missing_tp with
      //  reward == entry price, surfacing as no_feasible_target.)
      double fallback_rr = MathMax(_EffectiveFallbackRR(), min_rr);
      if(InpMaxPlanRR2 > 0) fallback_rr = MathMin(fallback_rr, InpMaxPlanRR2);
      _JournalDetail(2, "[fallback_target] configured_fallback_rr=" + DoubleToString(InpFallbackRR2, 6)
               + " min_live_rr=" + DoubleToString(InpMinLiveRR2, 6)
               + " buffer=" + DoubleToString(InpFallbackRRBufferR, 6)
               + " effective_fallback_rr=" + DoubleToString(fallback_rr, 6));
      double synthetic_reward = stop_dist * fallback_rr;
      double synthetic_target = 0.0;
      bool synthetic_available = (InpAllowSyntheticRRTarget && synthetic_reward > 0
                                  && (saw_valid_target || saw_too_near));
      if(synthetic_available){
         synthetic_target = (p.is_buy ? p.entry_est + synthetic_reward : p.entry_est - synthetic_reward);
         p.fallback_tp = synthetic_target;
         p.fallback_rr = fallback_rr;
         p.fallback_source = "synthetic_rr_fallback";
         _RefreshTargetFeasibility(p, "pre_ai");
      }

      // PLAN A step 3 / PLAN B step 5: a structural route that merely got clamped is
      // still a real route.  Take the best ranked one now, so a crossing fallback can
      // never outrank a clean target -- the fallback published above stays a fallback.
      int structural_best = _PickBestRankedTarget(ranked);
      if(structural_best >= 0 && !_TargetRankCrossesMajor(ranked[structural_best])){
         _LogTargetRank(p, ranked, structural_best);
         _Journal("[rr_floor_decision] symbol=" + p.symbol
                  + " chosen=" + ranked[structural_best].kind
                  + " rr=" + DoubleToString(ranked[structural_best].rr, 4)
                  + " floor=" + DoubleToString(min_rr, 4)
                  + " truncated=" + (ranked[structural_best].truncated_by_obstacle ? "true" : "false")
                  + " fallback_tp=" + _FmtPrice(p.symbol, p.fallback_tp)
                  + " binding_constraint=structural_target_selected");
         return _ApplyRankedTarget(p, ranked[structural_best], stop_dist);
      }

      if(synthetic_available){
         string obstacle_kind = "";
         double obstacle_price = 0.0;
         bool obstacle_before = _HasOpposingObstacleBeforeTarget(p, p.entry_est, synthetic_target, obstacles,
                                                                 obstacle_kind, obstacle_price);
         if(obstacle_before){
            double buffer = _ObstacleBufferPrice(p.symbol);
            double capped_target = (p.is_buy ? obstacle_price - buffer : obstacle_price + buffer);
            double capped_reward = _RewardToTarget(p.is_buy, p.entry_est, capped_target);
            double capped_rr = (stop_dist > 0 ? capped_reward / stop_dist : 0.0);
            if(capped_reward > 0.0){
               p.capped_before_obstacle_tp = capped_target;
               p.capped_before_obstacle_rr = capped_rr;
               p.capped_before_obstacle_source = "capped_before_" + obstacle_kind;
            }
            // This branch used to set the kind but never the severity, which is exactly
            // the case that shipped obstacle_strength_features empty to the model.
            _PublishObstacleEvidence(p, obstacle_kind, obstacle_price, stop_dist, true);
            p.obstacle_r = p.obstacle_distance_r;
            _RefreshTargetFeasibility(p, "pre_ai_obstacle");
            if(capped_reward > 0 &&
               (min_target_dist <= 0 || capped_reward >= min_target_dist) &&
               _RRMeetsFloor(capped_rr, min_rr)){
               p.tp2 = capped_target;
               p.tp_model = "capped_before_" + obstacle_kind;
               p.target_source = "capped_before_" + obstacle_kind;
               p.target_model = "capped_before_" + obstacle_kind;
               p.obstacle_price = obstacle_price;
               p.obstacle_r = capped_rr;
               p.effective_rr2 = capped_rr;
               _MaybeRequireTargetArbitration(p, p.tp2, p.target_source);
               return true;
            }
            if(_SyntheticTargetBlockedByObstacle(obstacle_kind) && !_CanAskAiForTargetArbitration(p)){
               reason = "synthetic_fallback_crossed_obstacle_blocked";
               return false;
            }
            // PLAN B step 3: the floor must bind the same way for every route.  A
            // synthetic that crosses the obstacle only "clears" min_rr by measuring
            // reward *through* it; measured to the obstacle -- the only distance it can
            // actually bank before meeting opposing flow -- it does not clear at all.
            // PLAN A step 3: never let that route become tp2 while a clean route exists.
            //
            // The condition must be "a clean route is ELIGIBLE", not "a route was seen".
            // `saw_valid_target` only records that some target was evaluated; gating on it
            // sent plans with zero eligible routes into this branch, where
            // _PickBestRankedTarget returned -1 and the plan was killed as
            // synthetic_fallback_crossed_obstacle_blocked -- a reason asserting that a
            // clean route existed when none did.  With no clean route the synthetic is
            // the only remaining option, so fall through to the normal capped/max-distance
            // handling below instead of rejecting.
            if(_RankedListHasCleanRoute(ranked)){
               p.why_not_synthetic_fallback =
                  "Crosses " + obstacle_kind + " at " + DoubleToString(p.obstacle_distance_r, 2)
                  + "R; a non-crossing structural route was available.";
               _Journal("[rr_floor_decision] symbol=" + p.symbol
                        + " rejected=synthetic_rr_fallback"
                        + " raw_rr=" + DoubleToString(fallback_rr, 4)
                        + " truncated_rr=" + DoubleToString(capped_rr, 4)
                        + " floor=" + DoubleToString(min_rr, 4)
                        + " binding_constraint=crosses_obstacle_clean_route_exists");
               int clean_best = _PickBestRankedTarget(ranked);
               _LogTargetRank(p, ranked, clean_best);
               // _RankedListHasCleanRoute and _PickBestRankedTarget share _TargetRankEligible,
               // so this is always >= 0 here; the guard stays defensive rather than fatal.
               if(clean_best >= 0) return _ApplyRankedTarget(p, ranked[clean_best], stop_dist);
            }

            // No clean route survives, so the only remaining options all pass through this
            // obstacle.  Two questions follow, in this order.
            double synthetic_severity = _ObstacleSeverity(obstacle_kind, p.obstacle_distance_r);

            // 1. Is crossing acceptable at all?  A killer-severity obstacle immediately in
            //    front of entry is not a target-selection problem, it is a "do not trade"
            //    signal.  Shipping the synthetic anyway produced
            //    ai_veto_target_arbitration_incoherent every time; reject it here, with a
            //    reason that names the real constraint, instead of paying for that veto.
            //    Judged on the synthetic's own obstacle -- the one it would actually cross.
            if(synthetic_severity >= InpBlockerKillSeverity){
               // _ObstacleSeverity adds a flat +1.5 for ANY obstacle inside
               // InpObstacleRejectR, so a level 0.0003R from entry scores exactly the
               // same 8.50 as one at 0.69R.  Below InpObstacleMinStopMult the level is
               // not a route blocker at all -- it sits inside the entry's own structure,
               // and _DeterministicExecutionGate already refuses the whole plan for it
               // as obstacle_too_close.  Both paths reject, so behaviour is unchanged;
               // only the published reason differed, and this one runs first, which
               // filed 80% of this bucket under a name asserting a route comparison
               // that was never the binding constraint.  Name the real constraint.
               bool degenerate_obstacle = (p.obstacle_distance_r > 0.0 &&
                                           p.obstacle_distance_r < InpObstacleMinStopMult);
               p.why_not_synthetic_fallback = (degenerate_obstacle
                  ? "Nearest " + obstacle_kind + " sits " + DoubleToString(p.obstacle_distance_r, 4)
                    + "R from entry, inside InpObstacleMinStopMult ("
                    + DoubleToString(InpObstacleMinStopMult, 2)
                    + "R): the entry has no room in front of it, so no target route exists."
                  : "Crosses " + obstacle_kind + " at " + DoubleToString(p.obstacle_distance_r, 2)
                    + "R with severity " + DoubleToString(synthetic_severity, 2)
                    + "; no route reaches a target without crossing a killer obstacle.");
               _Journal("[obstacle_crossing_gate] symbol=" + p.symbol
                        + " obstacle=" + obstacle_kind
                        + " obstacle_distance_r=" + DoubleToString(p.obstacle_distance_r, 4)
                        + " severity=" + DoubleToString(synthetic_severity, 2)
                        + " class=" + _ObstacleSeverityClass(synthetic_severity)
                        + " kill_threshold=" + DoubleToString(InpBlockerKillSeverity, 2)
                        + " min_stop_mult=" + DoubleToString(InpObstacleMinStopMult, 4)
                        + " degenerate=" + (degenerate_obstacle ? "true" : "false")
                        + " synthetic_rr=" + DoubleToString(fallback_rr, 4)
                        + " action=" + (degenerate_obstacle
                                        ? "reject_obstacle_inside_entry_structure"
                                        : "reject_no_route_without_killer_crossing"));
               _LogTargetRank(p, ranked, -1);
               reason = (degenerate_obstacle ? "obstacle_inside_entry_structure"
                                             : "all_routes_cross_killer_obstacle");
               return false;
            }

            // 2. Crossing is permitted, so it is now purely a question of which crossing
            //    route pays more.  The synthetic was the only route allowed to measure its
            //    reward THROUGH the obstacle; the structural route through the same
            //    obstacle is now ranked on the same basis, so prefer whichever is larger.
            int crossing_best = _PickBestCrossingRoute(ranked);
            if(crossing_best >= 0 && ranked[crossing_best].rr > fallback_rr + _RREps()){
               p.why_not_synthetic_fallback =
                  "A structural route through the same " + obstacle_kind + " pays "
                  + DoubleToString(ranked[crossing_best].rr, 2) + "R versus "
                  + DoubleToString(fallback_rr, 2) + "R for the synthetic.";
               _Journal("[obstacle_crossing_gate] symbol=" + p.symbol
                        + " obstacle=" + obstacle_kind
                        + " severity=" + DoubleToString(synthetic_severity, 2)
                        + " class=" + _ObstacleSeverityClass(synthetic_severity)
                        + " chosen=" + ranked[crossing_best].kind
                        + " structural_rr=" + DoubleToString(ranked[crossing_best].rr, 4)
                        + " synthetic_rr=" + DoubleToString(fallback_rr, 4)
                        + " action=prefer_structural_through_same_obstacle");
               _LogTargetRank(p, ranked, crossing_best);
               return _ApplyRankedTarget(p, ranked[crossing_best], stop_dist);
            }
         }
         double reward = _RewardToTarget(p.is_buy, p.entry_est, synthetic_target);
         if(reward > 0){
            if(obstacle_before && InpRejectSyntheticFallbackAfterCrossedObstacle && !_CanAskAiForTargetArbitration(p)){
               reason = "synthetic_fallback_crossed_obstacle_blocked";
               return false;
            }
            if(!p.fallback_feasible_for_tp2){
               if(p.fallback_infeasible_reason == "exceeds_max_target_distance" &&
                  p.synthetic_capped_to_max_distance_feasible){
                  p.tp2 = p.synthetic_capped_to_max_distance_tp;
                  p.tp_model = "synthetic_rr_capped_to_max_distance";
                  p.target_source = "synthetic_rr_capped_to_max_distance";
                  p.target_model = "synthetic_rr_capped_to_max_distance";
                  p.effective_rr2 = p.synthetic_capped_to_max_distance_rr;
                  _MaybeRequireTargetArbitration(p, p.tp2, p.target_source);
                  return true;
               }
               reason = (StringLen(p.fallback_infeasible_reason) > 0 ? "synthetic_fallback_" + p.fallback_infeasible_reason : "target_too_close_for_swing_duration");
               return false;
            }
            p.tp2 = synthetic_target;
            p.tp_model = "synthetic_rr_fallback";
            p.target_source = "synthetic_rr_fallback";
            p.target_model = "synthetic_rr_fallback";
            p.obstacle_kind = (obstacle_before ? _CrossedObstacleKind(obstacle_kind) : "");
            p.obstacle_price = (obstacle_before ? obstacle_price : 0.0);
            p.obstacle_r = 0.0;
            p.effective_rr2 = (stop_dist > 0 ? reward / stop_dist : 0.0);
            if(_RRMeetsFloor(p.effective_rr2, min_rr) &&
               (min_target_dist <= 0 || reward >= min_target_dist)){
               // PLAN B step 5: the synthetic is now reachable only when no structural
               // route survived.  Record which of the two it was so every remaining
               // synthetic in the journal carries its own justification.
               _Journal("[rr_floor_decision] symbol=" + p.symbol
                        + " chosen=synthetic_rr_fallback"
                        + " rr=" + DoubleToString(p.effective_rr2, 4)
                        + " floor=" + DoubleToString(min_rr, 4)
                        + " structural_routes_seen=" + (saw_valid_target ? "true" : "false")
                        + " truncated_by_obstacle=" + (saw_truncated_by_obstacle ? "true" : "false")
                        + " binding_constraint="
                        + (saw_valid_target ? "structural_routes_below_floor"
                                            : "no_structural_target_existed"));
               _LogTargetRank(p, ranked, -1);
               _MaybeRequireTargetArbitration(p, p.tp2, p.target_source);
               return true;
            }
            reason = "target_too_close_for_swing_duration";
            return false;
         }
      }

      // Last resort: a route that is merely below the floor still beats no plan at all
      // only when it does not cross a major obstacle.  Anything crossing stays rejected.
      int fallback_best = _PickBestRankedTarget(ranked);
      if(fallback_best >= 0 && !_TargetRankCrossesMajor(ranked[fallback_best])){
         _LogTargetRank(p, ranked, fallback_best);
         return _ApplyRankedTarget(p, ranked[fallback_best], stop_dist);
      }
      _LogTargetRank(p, ranked, -1);

      // PLAN B step 1: report truncation distinctly so the funnel can tell a target that
      // was blocked by structure from one that was simply too close to be worth taking.
      if(saw_truncated_by_obstacle) reason = "liquidity_target_truncated_by_obstacle";
      else if(saw_liquidity_too_near) reason = "liquidity_target_too_near";
      else if(saw_too_near) reason = "target_too_close_for_swing_duration";
      else reason = "liquidity_target_too_near";
      return false;
   }

   bool _SelectObstacleAwareTarget(TradePlan &p, const double stop_dist) {
      string reason = "";
      return _SelectObstacleAwareTarget(p, stop_dist, 0.0, 0.0, reason);
   }

   bool _StopsDistanceOk(const string symbol, const bool is_buy, const double ref_price, const double sl, const double tp) {
      double min_dist = _BrokerBufferPrice(symbol);
      if(min_dist <= 0) return true;
      if(sl > 0 && MathAbs(ref_price - sl) < min_dist) return false;
      if(tp > 0 && MathAbs(tp - ref_price) < min_dist) return false;
      if(is_buy && tp > 0 && tp <= ref_price) return false;
      if(!is_buy && tp > 0 && tp >= ref_price) return false;
      if(is_buy && sl > 0 && sl >= ref_price) return false;
      if(!is_buy && sl > 0 && sl <= ref_price) return false;
      return true;
   }

   double _MinPlanStopDistance(const TradePlan &p, const double entry_price) {
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double floor = point * MathMax(1, InpMinStopTicks);
      double broker = _BrokerBufferPrice(p.symbol);
      if(broker > 0) floor = MathMax(floor, broker);
      double spread = _CurrentSpreadPrice(p.symbol);
      if(spread > 0 && InpMinStopSpreadMult > 0) floor = MathMax(floor, spread * InpMinStopSpreadMult);
      double atr_abs = _ApproxAtrAbs(p, entry_price);
      if(atr_abs > 0 && InpMinStopAtrFrac > 0) floor = MathMax(floor, atr_abs * InpMinStopAtrFrac);
      double entry_atr_abs = _ApproxEntryAtrAbs(p, entry_price);
      if(entry_atr_abs > 0 && InpMinStopEntryAtrFrac > 0) floor = MathMax(floor, entry_atr_abs * InpMinStopEntryAtrFrac);
      return floor;
   }

   double _MinSwingTargetDistance(const TradePlan &p, const double entry_price) {
      double floor = 0.0;
      double entry_atr_abs = _ApproxEntryAtrAbs(p, entry_price);
      if(entry_atr_abs > 0 && InpMinTargetAtrMult > 0) floor = MathMax(floor, entry_atr_abs * InpMinTargetAtrMult);
      double adr_abs = _ApproxAdrAbs(p, entry_price);
      if(adr_abs > 0 && InpMinTargetAdrFrac > 0 && (!_IsMicroFamily(p) || InpUseAdrTargetFloorForMicro))
         floor = MathMax(floor, adr_abs * InpMinTargetAdrFrac);
      return floor;
   }

   double _MaxPlanTargetDistance(const TradePlan &p, const double entry_price, const double stop_dist) {
      double cap = 0.0;
      if(InpMaxPlanRR2 > 0 && stop_dist > 0) cap = stop_dist * InpMaxPlanRR2;

      double adr_abs = _ApproxAdrAbs(p, entry_price);
      if(adr_abs > 0 && InpMaxTargetAdrFrac > 0){
         double adr_cap = adr_abs * InpMaxTargetAdrFrac;
         cap = (cap > 0 ? MathMin(cap, adr_cap) : adr_cap);
      }

      double atr_abs = _ApproxAtrAbs(p, entry_price);
      if(atr_abs > 0 && InpMaxTargetAtrMult > 0){
         double atr_cap = atr_abs * InpMaxTargetAtrMult;
         cap = (cap > 0 ? MathMin(cap, atr_cap) : atr_cap);
      }

      return cap;
   }

   double _RewardToTarget(const bool is_buy, const double entry_price, const double target_price) {
      return (is_buy ? target_price - entry_price : entry_price - target_price);
   }

   bool _PriceAlreadyReachedTarget(const TradePlan &p, const double target_price, const double risk) const {
      if(target_price <= 0.0) return true;
      double bid = SymbolInfoDouble(p.symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(p.symbol, SYMBOL_ASK);
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0.0) point = 0.00001;
      double eps = MathMax(point * 2.0, risk * 0.01);
      double px = (p.is_buy ? bid : ask);
      if(px <= 0.0) return false;
      return (p.is_buy ? px >= target_price - eps : px <= target_price + eps);
   }

   bool _TargetCrossesKnownMajorObstacle(const TradePlan &p, const double target_price) {
      if(target_price <= 0.0 || p.entry_est <= 0.0 || p.obstacle_price <= 0.0 ||
         StringLen(p.obstacle_kind) == 0)
         return false;
      if(!_IsRewardSideLevel(p.is_buy, p.entry_est, p.obstacle_price) ||
         !_IsRewardSideLevel(p.is_buy, p.entry_est, target_price))
         return false;
      double obstacle_reward = _RewardToTarget(p.is_buy, p.entry_est, p.obstacle_price);
      double target_reward = _RewardToTarget(p.is_buy, p.entry_est, target_price);
      if(obstacle_reward <= 0.0 || target_reward <= obstacle_reward + _RREps())
         return false;
      double risk = MathAbs(p.entry_est - p.sl);
      double obstacle_r = (risk > 0.0 ? obstacle_reward / risk : p.obstacle_distance_r);
      return (_ObstacleSeverity(p.obstacle_kind, obstacle_r) >= InpBlockerMajorSeverity);
   }

   void _RefreshTargetFeasibility(TradePlan &p, const string stage) {
      double risk = MathAbs(p.entry_est - p.sl);
      double min_rr = MathMax(0.0, InpMinLiveRR2);
      double max_dist = _MaxPlanTargetDistance(p, p.entry_est, risk);
      double fallback_tp = p.fallback_tp;
      double fallback_reward = _RewardToTarget(p.is_buy, p.entry_est, fallback_tp);
      double fallback_rr = (risk > 0.0 && fallback_reward > 0.0 ? fallback_reward / risk : 0.0);
      if(p.fallback_rr > 0.0) fallback_rr = p.fallback_rr;

      p.fallback_feasible_for_tp2 = false;
      p.fallback_feasible_for_tp1_only = false;
      p.fallback_infeasible_reason = "";
      p.fallback_reward_distance = MathMax(0.0, fallback_reward);
      p.fallback_max_allowed_distance = max_dist;
      p.synthetic_capped_to_max_distance_tp = 0.0;
      p.synthetic_capped_to_max_distance_rr = 0.0;
      p.synthetic_capped_to_max_distance_feasible = false;
      p.synthetic_capped_to_max_distance_reason = "";

      bool direction_valid = (fallback_tp > 0.0 && _IsRewardSideLevel(p.is_buy, p.entry_est, fallback_tp));
      bool target_reached = _PriceAlreadyReachedTarget(p, fallback_tp, risk);
      bool exceeds_max = (max_dist > 0.0 && fallback_reward > max_dist + _RREps());
      bool rr_pass = _RRMeetsFloor(fallback_rr, min_rr);

      if(fallback_tp <= 0.0) p.fallback_infeasible_reason = "missing_tp";
      else if(risk <= 0.0) p.fallback_infeasible_reason = "no_valid_stop";
      else if(!direction_valid) p.fallback_infeasible_reason = "invalid_direction";
      else if(target_reached) p.fallback_infeasible_reason = "target_already_reached";
      else if(exceeds_max) p.fallback_infeasible_reason = "exceeds_max_target_distance";
      else if(!rr_pass) p.fallback_infeasible_reason = "rr_below_min";
      else p.fallback_feasible_for_tp2 = true;

      if(exceeds_max && risk > 0.0 && max_dist > 0.0){
         double capped_rr = max_dist / risk;
         double capped_tp = _TpFromReward(p, max_dist);
         if(capped_tp > 0.0 && _RRMeetsFloor(capped_rr, min_rr) && !_PriceAlreadyReachedTarget(p, capped_tp, risk)){
            if(_TargetCrossesKnownMajorObstacle(p, capped_tp)){
               p.synthetic_capped_to_max_distance_reason = "crosses_known_major_obstacle";
            } else {
               p.synthetic_capped_to_max_distance_tp = capped_tp;
               p.synthetic_capped_to_max_distance_rr = capped_rr;
               p.synthetic_capped_to_max_distance_feasible = true;
               p.synthetic_capped_to_max_distance_reason = "fallback_capped_by_max_distance";
               m_total_target_feasibility_synthetic_capped_to_max_distance++;
            }
         }
         m_total_target_feasibility_synthetic_infeasible_max_distance++;
      }

      if(StringLen(p.fallback_infeasible_reason) == 0 && !p.fallback_feasible_for_tp2)
         p.fallback_infeasible_reason = "unknown";

      if(_JournalDetailEnabled(2) &&
         (StringLen(p.fallback_infeasible_reason) > 0 || p.fallback_tp > 0.0)){
         _Journal("[target_feasibility] stage=" + stage
                  + " model=synthetic_rr_fallback"
                  + " configured_rr=" + DoubleToString(InpFallbackRR2, 2)
                  + " rr=" + DoubleToString(fallback_rr, 4)
                  + " feasible=" + (p.fallback_feasible_for_tp2 ? "true" : "false")
                  + " reason=" + (p.fallback_feasible_for_tp2 ? "ok" : p.fallback_infeasible_reason)
                  + " reward=" + _FmtPrice(p.symbol, p.fallback_reward_distance)
                  + " max_allowed=" + _FmtPrice(p.symbol, max_dist)
                  + " target_reached=" + (target_reached ? "true" : "false")
                  + " direction_valid=" + (direction_valid ? "true" : "false"));
      }
      if(!_JournalDetailEnabled(2)) return;
      if(p.synthetic_capped_to_max_distance_feasible){
         _Journal("[target_feasibility] stage=" + stage
                  + " model=synthetic_rr_capped_to_max_distance"
                  + " tp=" + _FmtPrice(p.symbol, p.synthetic_capped_to_max_distance_tp)
                  + " rr=" + DoubleToString(p.synthetic_capped_to_max_distance_rr, 4)
                  + " feasible=true"
                  + " reason=fallback_capped_by_max_distance");
      } else if(p.synthetic_capped_to_max_distance_reason == "crosses_known_major_obstacle"){
         _Journal("[target_feasibility] stage=" + stage
                  + " model=synthetic_rr_capped_to_max_distance"
                  + " feasible=false reason=crosses_known_major_obstacle"
                  + " obstacle_kind=" + p.obstacle_kind
                  + " obstacle_price=" + _FmtPrice(p.symbol, p.obstacle_price));
      }
   }

   bool _ApplySyntheticCappedToMaxDistance(TradePlan &p, const string reason) {
      _RefreshTargetFeasibility(p, reason);
      if(!p.synthetic_capped_to_max_distance_feasible) return false;
      double old_tp = p.tp2;
      double old_rr = _ExecutionRR2(p);
      p.tp2 = p.synthetic_capped_to_max_distance_tp;
      p.fallback_tp = p.tp2;
      p.fallback_rr = p.synthetic_capped_to_max_distance_rr;
      p.fallback_source = "synthetic_rr_capped_to_max_distance";
      p.target_source = "ai_selected_synthetic_rr_capped_to_max_distance";
      p.target_model = "synthetic_rr_capped_to_max_distance";
      p.tp_model = "synthetic_rr_capped_to_max_distance";
      p.effective_rr2 = p.synthetic_capped_to_max_distance_rr;
      p.ai_chosen_target_model = "synthetic_rr_capped_to_max_distance";
      p.ai_chosen_tp2 = p.tp2;
      p.ai_chosen_rr2 = p.effective_rr2;
      _Journal("[target_rebuild_downgrade] from=synthetic_rr_fallback"
               + " to=synthetic_rr_capped_to_max_distance"
               + " reason=max_distance_after_entry_change"
               + " old_tp=" + _FmtPrice(p.symbol, old_tp)
               + " new_tp=" + _FmtPrice(p.symbol, p.tp2)
               + " old_rr=" + DoubleToString(old_rr, 4)
               + " new_rr=" + DoubleToString(p.effective_rr2, 4));
      return true;
   }

   double _PlanTickSize(const string symbol) const {
      // One definition, shared with AIGateBridge so the payload measures the
      // same ticks the sanitizer does.  See PlanTickSize in
      // ExecutionAdjustmentContract.mqh.
      return PlanTickSize(symbol);
   }

   //+---------------------------------------------------------------+
   //| THE canonical target feasibility evaluation.                   |
   //|                                                                |
   //| Every stage that asks "is this target usable?" -- initial plan  |
   //| construction, AI candidate construction, the sanitizer, the     |
   //| watchlist precheck, the live execution rebuild, and final       |
   //| validation -- calls exactly this function and reports exactly   |
   //| this result.  Previously each recomputed the cap for itself,    |
   //| which is how one stage logged                                   |
   //|   "synthetic_rr_capped_to_max_distance feasible=true            |
   //|    reward=30.01 max_allowed=30.01"                              |
   //| while another rejected the same plan as                         |
   //|   "ai_chosen_target_exceeds_max_distance".                      |
   //|                                                                 |
   //| The max-distance decision is made in whole ticks so a target    |
   //| capped exactly to the maximum passes deterministically.         |
   //+---------------------------------------------------------------+
   void _EvaluateTargetFeasibility(const TradePlan &p,
                                   const double target_price,
                                   const string model,
                                   const string authority,
                                   const bool require_min_rr,
                                   TargetFeasibilityResult &out) {
      out.Reset();
      out.authority    = authority;
      out.model        = (StringLen(model) > 0 ? model : "unknown");
      out.target_price = target_price;

      if(target_price <= 0.0 || p.entry_est <= 0.0 || p.sl <= 0.0){
         out.reason = "ai_chosen_target_missing_tp";
         return;
      }
      double risk = MathAbs(p.entry_est - p.sl);
      out.risk = risk;
      if(risk <= 0.0){
         out.reason = "no_valid_stop";
         return;
      }

      out.direction_valid = _IsRewardSideLevel(p.is_buy, p.entry_est, target_price);
      if(!out.direction_valid){
         out.reason = "ai_chosen_target_invalid_direction";
         return;
      }

      out.reward = _RewardToTarget(p.is_buy, p.entry_est, target_price);
      if(out.reward <= 0.0){
         out.reason = "liquidity_target_too_near";
         return;
      }
      out.rr = out.reward / risk;

      out.target_reached = _PriceAlreadyReachedTarget(p, target_price, risk);
      if(out.target_reached){
         out.reason = "ai_chosen_target_already_reached";
         return;
      }

      double tick = _PlanTickSize(p.symbol);
      out.max_allowed_distance  = _MaxPlanTargetDistance(p, p.entry_est, risk);
      out.min_required_distance = _MinSwingTargetDistance(p, p.entry_est);
      out.min_required_rr       = (require_min_rr ? MathMax(0.0, InpMinLiveRR2) : 0.0);
      out.reward_ticks          = PriceDistanceToTicks(out.reward, tick);
      out.max_allowed_ticks     = PriceDistanceToTicks(out.max_allowed_distance, tick);

      // All three gates are measured before any of them decides the reason.
      // They used to short-circuit, which left the unreached flags at their
      // Reset() default of false -- so the EURCHF rejection printed
      // "rr=5.72928 min_rr=0.90000 rr_floor_pass=false" and read as three
      // simultaneous failures when exactly one check had been run.  A
      // diagnostic that names checks it never performed is how a correct
      // decision becomes undiagnosable.
      out.max_distance_pass = RewardWithinMaxDistance(out.reward, out.max_allowed_distance, tick);
      out.min_distance_pass = (out.min_required_distance <= 0.0 ||
                               out.reward_ticks >= PriceDistanceToTicks(out.min_required_distance, tick));
      out.rr_floor_pass     = (!require_min_rr || _RRMeetsFloor(out.rr, out.min_required_rr));

      // Precedence is unchanged: max distance, then min distance, then RR floor.
      if(!out.max_distance_pass){
         out.reason = "ai_chosen_target_exceeds_max_distance";
         return;
      }
      if(!out.min_distance_pass){
         out.reason = "target_too_close_for_swing_duration";
         return;
      }
      if(!out.rr_floor_pass){
         out.reason = "rr_below_live_floor";
         return;
      }

      out.feasible = true;
      out.reason   = "ok";
   }

   void _LogTargetFeasibilityResult(const string stage, const TargetFeasibilityResult &r) {
      _Journal("[target_feasibility] stage=" + stage + " " + r.Describe());
   }

   bool _TargetPriceFeasibleForTp(TradePlan &p, const double target_price, const bool require_min_rr) {
      // Thin wrapper preserved so the many existing call sites keep working;
      // the decision itself now has exactly one implementation.
      TargetFeasibilityResult r;
      _EvaluateTargetFeasibility(p, target_price, _EffectiveTargetModel(p),
                                 "canonical_target_feasibility", require_min_rr, r);
      return r.feasible;
   }

   bool _CurrentTargetFeasibleForTp2(TradePlan &p) {
      if(!_HasKnownTargetModel(p)) return false;
      return _TargetPriceFeasibleForTp(p, p.tp2, true);
   }

   bool _PlanBlockerIsKiller(const TradePlan &p) {
      string cls = _NormToken(p.ai_blocker_class);
      double severity = p.ai_blocker_severity;
      if(severity <= 0.0 && StringLen(p.obstacle_kind) > 0)
         severity = _ObstacleSeverity(p.obstacle_kind, p.obstacle_distance_r);
      return (p.ai_blocker_is_trade_killer || cls == "killer" || cls == "kill" || severity >= InpBlockerKillSeverity);
   }

   void _LogTargetSanitizerRewrite(const string from_model, const string to_model, const string reason, const TradePlan &p) {
      _Journal("[target_sanitizer] from=" + from_model
               + " to=" + to_model
               + " reason=" + reason
               + " tp1=" + _FmtPrice(p.symbol, p.tp1)
               + " tp2=" + _FmtPrice(p.symbol, p.tp2)
               + " rr2=" + DoubleToString(_ExecutionRR2(p), 6));
   }

   bool _ApplyFeasibleTargetSanitizer(TradePlan &p, string &reason) {
      reason = "ok";
      bool ai_context = (StringLen(p.ai_chosen_target_model) > 0 ||
                         StringLen(p.ai.chosen_target_model) > 0 ||
                         StringLen(p.ai_decision_id) > 0 ||
                         StringLen(p.target_decision_reason) > 0 ||
                         p.target_arbitration_required);
      if(!ai_context) return true;

      _NormalizeTargetLabels(p);
      _RefreshTargetFeasibility(p, "target_sanitizer");
      if(_CurrentTargetFeasibleForTp2(p))
         return true;

      string from_model = _EffectiveTargetModel(p);

      if(p.assessed_plan_locked){
         // Authority rules 3 and 4: an approved next_liquidity_session_range
         // must not silently become synthetic_rr_fallback, and an approved
         // ai_selected_liquidity_target must not silently become a synthetic
         // source.  Substituting here is exactly what produced a materially
         // different trade that the fingerprint check then had to reject.
         TargetFeasibilityResult locked;
         _EvaluateTargetFeasibility(p, p.tp2, p.target_model,
                                    "target_sanitizer_locked_plan", true, locked);
         _LogTargetFeasibilityResult("target_sanitizer_locked_plan", locked);
         reason = (StringLen(locked.reason) > 0 && locked.reason != "ok"
                   ? locked.reason
                   : "assessed_target_no_longer_feasible");
         p.execution_failure_class = EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE;
         _Journal("[target_sanitizer] substitution_refused=true reason=assessed_plan_locked"
                  + " approved_model=" + p.assessed_target_model
                  + " approved_source=" + p.assessed_target_source
                  + " approved_tp2=" + _FmtPrice(p.symbol, p.assessed_selected_target_price)
                  + " failure_class=" + EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE
                  + " action=" + EXEC_ACTION_TERMINAL_INVALIDATE
                  + " detail=" + locked.Describe());
         return false;
      }

      if(InpAllowPartialBeforeObstacle &&
         p.capped_before_obstacle_tp > 0.0 &&
         p.liquidity_target_preserved > 0.0 &&
         _TargetPriceFeasibleForTp(p, p.capped_before_obstacle_tp, false) &&
         _TargetPriceFeasibleForTp(p, p.liquidity_target_preserved, true)){
         p.tp1 = p.capped_before_obstacle_tp;
         p.tp2 = p.liquidity_target_preserved;
         // This substitution exists precisely to bank a leg in front of the obstacle,
         // so the leg is the route's, not the generic builder's.
         p.tp1_from_target_model = true;
         p.target_source = "ai_selected_partial_then_liquidity";
         p.target_model = "partial_before_obstacle_then_liquidity";
         p.tp_model = "partial_then_liquidity";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_target_model = "partial_before_obstacle_then_liquidity";
         p.ai_chosen_tp1 = p.tp1;
         p.ai_chosen_tp2 = p.tp2;
         p.ai.chosen_rr1 = _TargetRR(p, p.tp1);
         p.ai_chosen_rr2 = p.effective_rr2;
         _LogTargetSanitizerRewrite(from_model, "partial_before_obstacle_then_liquidity", "feasible_priority_target", p);
         return true;
      }

      if(!_PlanBlockerIsKiller(p) &&
         p.liquidity_target_preserved > 0.0 &&
         _TargetPriceFeasibleForTp(p, p.liquidity_target_preserved, true)){
         p.tp2 = p.liquidity_target_preserved;
         p.target_source = "ai_selected_liquidity_target";
         p.target_model = (StringLen(p.liquidity_target_model) > 0 ? p.liquidity_target_model : "liquidity_target");
         p.tp_model = "liquidity_target";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_target_model = "liquidity_target";
         p.ai_chosen_tp2 = p.tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
         _LogTargetSanitizerRewrite(from_model, "liquidity_target", "feasible_priority_target", p);
         return true;
      }

      if(p.capped_before_obstacle_tp > 0.0 &&
         _TargetPriceFeasibleForTp(p, p.capped_before_obstacle_tp, true)){
         p.tp2 = p.capped_before_obstacle_tp;
         p.target_source = "ai_selected_capped_before_obstacle";
         p.target_model = (StringLen(p.capped_before_obstacle_source) > 0 ? p.capped_before_obstacle_source : "capped_before_obstacle");
         p.tp_model = "capped_before_obstacle";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_target_model = "capped_before_obstacle";
         p.ai_chosen_tp2 = p.tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
         _LogTargetSanitizerRewrite(from_model, "capped_before_obstacle", "feasible_priority_target", p);
         return true;
      }

      if(p.synthetic_capped_to_max_distance_feasible &&
         _TargetPriceFeasibleForTp(p, p.synthetic_capped_to_max_distance_tp, true)){
         if(_ApplySyntheticCappedToMaxDistance(p, "target_sanitizer")){
            _LogTargetSanitizerRewrite(from_model, "synthetic_rr_capped_to_max_distance", "feasible_priority_target", p);
            return true;
         }
      }

      if(p.fallback_feasible_for_tp2 &&
         p.fallback_tp > 0.0 &&
         _TargetPriceFeasibleForTp(p, p.fallback_tp, true)){
         p.tp2 = p.fallback_tp;
         p.target_source = "ai_selected_synthetic_rr_fallback";
         p.target_model = "synthetic_rr_fallback";
         p.tp_model = "synthetic_rr_fallback";
         p.effective_rr2 = _TargetRR(p, p.tp2);
         p.ai_chosen_target_model = "synthetic_rr_fallback";
         p.ai_chosen_tp2 = p.tp2;
         p.ai_chosen_rr2 = p.effective_rr2;
         _LogTargetSanitizerRewrite(from_model, "synthetic_rr_fallback", "feasible_priority_target", p);
         return true;
      }

      reason = "no_feasible_target";
      _Journal("[target_sanitizer] reject=true reason=no_feasible_target"
               + " from=" + from_model
               + " fallback_reason=" + p.fallback_infeasible_reason
               + " capped_feasible=" + (p.synthetic_capped_to_max_distance_feasible ? "true" : "false"));
      return false;
   }

   bool _ResolveInitialStop(const TradePlan &p, const double buffer, double &out_sl, string &reason) {
      reason = "ok";
      out_sl = 0.0;

      if(p.fvg.lower <= 0 || p.fvg.upper <= 0 || p.fvg.upper <= p.fvg.lower){
         reason = "fvg_too_small";
         return false;
      }

      if(InpStopModel == STOP_FVG_EDGE){
         out_sl = (p.is_buy ? p.fvg.lower - buffer : p.fvg.upper + buffer);
         if(out_sl <= 0) reason = "structural_stop_invalid";
         return (out_sl > 0);
      }

      if(InpStopModel == STOP_STRUCTURAL_SWEEP){
         double source_manip_low = (p.source_manip_low > 0 ? p.source_manip_low : p.po3.manip_low);
         double source_manip_high = (p.source_manip_high > 0 ? p.source_manip_high : p.po3.manip_high);
         if(p.is_buy){
            if(source_manip_low <= 0){
               reason = "structural_stop_invalid";
               return false;
            }
            out_sl = MathMin(p.fvg.lower, source_manip_low) - buffer;
         } else {
            if(source_manip_high <= 0){
               reason = "structural_stop_invalid";
               return false;
            }
            out_sl = MathMax(p.fvg.upper, source_manip_high) + buffer;
         }
         if(out_sl <= 0) reason = "structural_stop_invalid";
         return (out_sl > 0);
      }

      double source_manip_low = (p.source_manip_low > 0 ? p.source_manip_low : p.po3.manip_low);
      double source_manip_high = (p.source_manip_high > 0 ? p.source_manip_high : p.po3.manip_high);
      if(p.is_buy){
         if(source_manip_low <= 0 || p.po3.swing_low <= 0){
            reason = "structural_stop_invalid";
            return false;
         }
         out_sl = MathMin(MathMin(p.fvg.lower, source_manip_low), p.po3.swing_low) - buffer;
      } else {
         if(source_manip_high <= 0 || p.po3.swing_high <= 0){
            reason = "structural_stop_invalid";
            return false;
         }
         out_sl = MathMax(MathMax(p.fvg.upper, source_manip_high), p.po3.swing_high) + buffer;
      }
      if(out_sl <= 0) reason = "structural_stop_invalid";
      return (out_sl > 0);
   }

   bool _BuildPlanPrices(TradePlan &p, const double entry_price, string &reject_reason) {
      reject_reason = "ok";
      p.entry_est = entry_price;
      if(p.entry_est <= 0){
         reject_reason = "structural_stop_invalid";
         return _BuildPlanReject(p, reject_reason, entry_price, p.sl, p.tp2);
      }

      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double fvg_width = MathAbs(p.fvg.upper - p.fvg.lower);
      if(fvg_width < point * MathMax(1, PO3EffectiveMinFvgWidthTicks())){
         reject_reason = "fvg_too_small";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      if(p.stop_buffer_points <= 0) _ApplySetupManagementProfile(p);
      double spread_price = _CurrentSpreadPrice(p.symbol);
      double htf_atr_abs = _ApproxAtrAbs(p, p.entry_est);
      double buf = MathMax(p.stop_buffer_points * point, InpSLBufferPts * point);
      if(htf_atr_abs > 0 && InpSLBufferAtrFrac > 0) buf = MathMax(buf, htf_atr_abs * InpSLBufferAtrFrac);
      if(spread_price > 0 && InpMinStopSpreadMult > 0) buf = MathMax(buf, spread_price * InpMinStopSpreadMult);

      string stop_reason = "";
      if(!_ResolveInitialStop(p, buf, p.sl, stop_reason)){
         reject_reason = stop_reason;
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      double initial_sl = p.sl;

      double stop_dist = MathAbs(p.entry_est - p.sl);
      if(stop_dist <= 0 || (p.is_buy && p.sl >= p.entry_est) || (!p.is_buy && p.sl <= p.entry_est)){
         reject_reason = "structural_stop_invalid";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }
      double min_stop_dist = _MinPlanStopDistance(p, p.entry_est);
      if(min_stop_dist > 0 && stop_dist < min_stop_dist){
         if(p.is_buy) p.sl = p.entry_est - min_stop_dist;
         else         p.sl = p.entry_est + min_stop_dist;
         stop_dist = MathAbs(p.entry_est - p.sl);
      }
      if(stop_dist <= 0 || p.sl <= 0){
         reject_reason = "stop_too_tight";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }
      if(stop_dist > p.entry_est * InpStopMaxFracOfPrice){
         // The stop here is structurally *valid* -- it is simply wider than the
         // configured fraction-of-price risk cap.  Reporting it as
         // "structural_stop_invalid" merged this risk-cap rejection into the
         // genuine geometry failures above and made the single largest bucket in
         // the funnel impossible to diagnose.  Name the real constraint, and log
         // the numbers needed to judge whether the cap or the universe is wrong.
         reject_reason = "stop_distance_exceeds_max_frac_of_price";
         _Journal("[stop_distance_cap] symbol=" + p.symbol
                  + " entry=" + _FmtPrice(p.symbol, p.entry_est)
                  + " sl=" + _FmtPrice(p.symbol, p.sl)
                  + " stop_dist=" + _FmtPrice(p.symbol, stop_dist)
                  + " stop_frac_of_price=" + DoubleToString(stop_dist / p.entry_est, 6)
                  + " max_frac_of_price=" + DoubleToString(InpStopMaxFracOfPrice, 6)
                  + " stop_model=" + _StopModelName());
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      double target_cap = _MaxPlanTargetDistance(p, p.entry_est, stop_dist);
      double min_target_dist = _MinSwingTargetDistance(p, p.entry_est);
      string target_reason = "";
      if(!_SelectObstacleAwareTarget(p, stop_dist, min_target_dist, target_cap, target_reason)){
         reject_reason = (StringLen(target_reason) > 0 ? target_reason : "target_too_close_for_swing_duration");
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }
      if(p.assessed_plan_locked){
         // The AI already chose a target and Python approved it.  The landscape
         // scan above stays -- it revalidates obstacles against current
         // structure -- but its *choice* must not override the approved one.
         // Re-deriving here is what turned an approved 4070.51 liquidity target
         // into a 4220.90 one, blew the distance cap, let the sanitizer
         // substitute synthetic_rr_fallback, and then failed the assessment
         // fingerprint on target_source,target_model,obstacle_kind,tp1,tp2.
         ExecutionAdjustmentContract contract;
         _BuildExecutionAdjustmentContract(p, contract);
         string contract_reason = "";
         string contract_failure = "";
         if(!_ApplyAssessedTargetUnderContract(p, contract, contract_reason, contract_failure)){
            p.execution_failure_class = contract_failure;
            // This runs on the live COPY inside _PlaceMarket, so the line above is
            // discarded with that copy.  Publish the detector's verdict for the
            // current attempt as well, or _ClassifyExecutionFailure falls back to
            // matching the reason string and can only approximate it.
            m_last_execution_failure_class = contract_failure;
            reject_reason = contract_reason;
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
      } else if(_HasStoredTargetArbitration(p)){
         string stored_target_reason = "";
         if(!_ApplyStoredTargetArbitrationAfterRebuild(p, stored_target_reason)){
            reject_reason = stored_target_reason;
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
      } else if(StringLen(p.ai.chosen_target_model) > 0 || StringLen(p.ai.decision_id) > 0 || StringLen(p.ai_decision_id) > 0){
         string ai_target_reason = "";
         if(!_ApplyAiTargetArbitration(p, p.ai, ai_target_reason)){
            reject_reason = ai_target_reason;
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
         if(_PlanUsesSyntheticFallback(p) && !_RecomputeSyntheticFallbackTarget(p, "plan_rebuild_after_ai_target_arbitration")){
            reject_reason = "ai_selected_synthetic_fallback_invalid";
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
      }
      double reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp2);
      if(target_cap > 0 && reward > target_cap){
         bool ai_target_applied = (StringLen(p.ai_chosen_target_model) > 0);
         if(p.assessed_plan_locked){
            // An approved target that no longer fits the live cap is a real,
            // classified reason to stop -- not something to "keep for
            // validation" and then quietly replace with a different target.
            // The canonical evaluator decides in whole ticks, so a target
            // capped exactly to the maximum still passes.
            TargetFeasibilityResult cap_check;
            _EvaluateTargetFeasibility(p, p.tp2, p.target_model,
                                       "plan_rebuild_max_distance", true, cap_check);
            _LogTargetFeasibilityResult("plan_rebuild_max_distance", cap_check);
            if(!cap_check.feasible){
               p.execution_failure_class = EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE;
               reject_reason = cap_check.reason;
               return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
            }
         } else if(ai_target_applied){
            _Journal("[target_validation] ai_chosen_target_exceeds_cap kept_for_validation"
                     + " chosen=" + p.target_model
                     + " reward=" + _FmtPrice(p.symbol, reward)
                     + " cap=" + _FmtPrice(p.symbol, target_cap));
         } else {
            reward = target_cap;
            if(p.is_buy) p.tp2 = p.entry_est + reward;
            else         p.tp2 = p.entry_est - reward;
            p.effective_rr2 = (stop_dist > 0 ? reward / stop_dist : 0.0);
            if(StringFind(p.tp_model, "_capped") < 0) p.tp_model += "_capped";
            if(StringLen(p.target_model) == 0 || StringFind(p.target_model, "_capped") < 0) p.target_model = p.tp_model;
         }
      }

      double tp2_reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp2);
      if(tp2_reward <= 0){
         reject_reason = "liquidity_target_too_near";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }
      if(min_target_dist > 0 && tp2_reward < min_target_dist){
         reject_reason = "target_too_close_for_swing_duration";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      double min_tp1_reward = _MinTp1Reward(p, stop_dist);
      if(p.tp1_from_target_model && p.tp1 > 0.0){
         // A target model that defines its own first leg is the authority on it.  This
         // builder used to overwrite that leg unconditionally: for
         // partial_before_obstacle_then_liquidity the 0.65R floor moved a partial sitting
         // 0.0108R in front of a major obstacle out to 1.0R -- *past* the obstacle -- so
         // the shipped plan contradicted both its own tp_model and its own
         // target_candidates table, and the AI vetoed it as
         // ai_veto_target_arbitration_incoherent.  Validate the route's leg and fail
         // closed if it is unusable; never silently relocate it.
         double route_tp1_reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp1);
         if(route_tp1_reward <= 0.0){
            reject_reason = "target_model_tp1_wrong_side_of_entry";
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
         if(route_tp1_reward >= tp2_reward){
            reject_reason = "target_model_tp1_beyond_target";
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
         // The two halves of the floor answer different questions, so each is
         // compared against the quantity it is actually about.  Measuring both
         // against the drifted live leg is what killed three of the eleven
         // 2026-09-05 approvals: the geometry floor rose with the widened stop
         // while the frozen leg's reward fell with the drifted entry, so a plan
         // needed TP1 at 1.31R at approval to survive its own execution.
         double geometry_floor      = _MinTp1GeometryReward(p, stop_dist);
         double geometry_leg_reward = _Tp1GeometryLegReward(p, route_tp1_reward);
         double spread_floor        = _MinTp1SpreadReward(p);
         bool geometry_fail = (geometry_floor > 0.0 && geometry_leg_reward < geometry_floor);
         bool spread_fail   = (spread_floor > 0.0 && route_tp1_reward < spread_floor);
         if(geometry_fail || spread_fail){
            reject_reason = "target_model_tp1_below_min_reward";
            _Journal("[tp1_authority] symbol=" + p.symbol
                     + " owner=target_model model=" + p.target_model
                     + " tp1=" + _FmtPrice(p.symbol, p.tp1)
                     + " tp1_reward=" + _FmtPrice(p.symbol, route_tp1_reward)
                     + " min_tp1_reward=" + _FmtPrice(p.symbol, min_tp1_reward)
                     + " geometry_leg_reward=" + _FmtPrice(p.symbol, geometry_leg_reward)
                     + " geometry_floor=" + _FmtPrice(p.symbol, geometry_floor)
                     + " spread_floor=" + _FmtPrice(p.symbol, spread_floor)
                     + " live_stop_dist=" + _FmtPrice(p.symbol, stop_dist)
                     + " floor_stop_dist=" + _FmtPrice(p.symbol, _Tp1FloorStopDistance(p, stop_dist))
                     + " floor_basis=" + (p.assessed_plan_locked && p.assessed_stop_distance > 0.0
                                          ? "assessed_plan" : "live_rebuild")
                     + " failed_half=" + (geometry_fail ? (spread_fail ? "geometry_and_spread" : "geometry")
                                                        : "spread")
                     + " action=reject_fail_closed");
            return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
         }
         if(InpVerboseJournal)
            _Journal("[tp1_authority] symbol=" + p.symbol
                     + " owner=target_model model=" + p.target_model
                     + " tp1=" + _FmtPrice(p.symbol, p.tp1)
                     + " tp1_rr=" + DoubleToString(route_tp1_reward / stop_dist, 4)
                     + " tp2=" + _FmtPrice(p.symbol, p.tp2)
                     + " obstacle=" + (StringLen(p.obstacle_kind) > 0 ? p.obstacle_kind : "none")
                     + " action=preserved");
      } else {
         double tp1_reward = stop_dist * MathMax(0.40, p.tp1_r_multiple);
         if(InpTP1UseFibExtension){
            double tp1_swing_range = MathAbs(p.po3.swing_high - p.po3.swing_low);
            if(tp1_swing_range > 0.0 && InpTP1FibExtension > 1.0){
               double fib_tp1 = (p.is_buy
                                 ? p.po3.swing_high + tp1_swing_range * (InpTP1FibExtension - 1.0)
                                 : p.po3.swing_low - tp1_swing_range * (InpTP1FibExtension - 1.0));
               double fib_tp1_reward = _RewardToTarget(p.is_buy, p.entry_est, fib_tp1);
               if(fib_tp1_reward > 0.0 && fib_tp1_reward < tp2_reward)
                  tp1_reward = fib_tp1_reward;
            }
         }
         if(min_tp1_reward > 0) tp1_reward = MathMax(tp1_reward, min_tp1_reward);
         if(tp2_reward > 0) tp1_reward = MathMin(tp1_reward, tp2_reward * 0.70);
         if(tp1_reward <= 0) tp1_reward = MathMin(tp2_reward, stop_dist);
         if(p.is_buy) p.tp1 = p.entry_est + tp1_reward;
         else         p.tp1 = p.entry_est - tp1_reward;
      }

      if(!_StopsDistanceOk(p.symbol, p.is_buy, p.entry_est, p.sl, p.tp2)){
         reject_reason = "broker_distance_invalid";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      _ApplyStopAudit(p, initial_sl, p.entry_est);
      _EstimateExecutionCosts(p);
      _FinalizePlanEconomics(p, "plan_prices_finalized", true);
      p.ote_distance_frac = _OteDistanceFrac(p);
      p.ote_state = _OteState(p);
      _NormalizeTargetLabels(p);
      string ai_target_validation_reason = "";
      string ai_target_validation_detail = "";
      if(!ValidateAiChosenTargetBeforeWatchlist(p, ai_target_validation_reason, ai_target_validation_detail)){
         reject_reason = ai_target_validation_reason;
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }
      if(!_FinalizeFirstLeg(p, true, reject_reason))
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      _LogBuildPlanAccepted(p, min_target_dist);

      return true;
   }

   bool _BuildPlanPrices(TradePlan &p, const double entry_price) {
      string reject_reason = "";
      return _BuildPlanPrices(p, entry_price, reject_reason);
   }

   double _LiquidityRR(const TradePlan &p) {
      double risk = MathAbs(p.entry_est - p.sl);
      if(risk <= 0 || p.po3.liquidity_target <= 0) return 0.0;
      double reward = (p.is_buy ? p.po3.liquidity_target - p.entry_est : p.entry_est - p.po3.liquidity_target);
      if(reward <= 0) return 0.0;
      return reward / risk;
   }

   double _DeterministicSetupScoreFloor(const TradePlan &p) const {
      if(_IsFvgEntryNonTradable(p.fvg_execution_class)) return DBL_MAX;
      double floor = _FamilySetupFloor(p);
      if(floor <= 0.0) floor = InpSetupFloorRange;
      if(p.fvg_execution_class == "stale_fvg") floor = MathMax(InpSetupFloorRange, InpSetupFloorReversal) + 4.0;

      if(!InpRequireConfirmedPO3ForExecution){
         if(p.po3.context_tier == "B") floor += InpSetupFloorTierBExtra;
         else if(p.po3.context_tier == "C") floor += InpSetupFloorTierCExtra;
      }
      return floor;
   }

   bool _RunnerFiltersPass(const TradePlan &p, string &reason) const {
      reason = "ok";
      double seq_floor = (m_active_policy.runner_sequence_floor > 0.0 ? m_active_policy.runner_sequence_floor : InpRunnerSequenceQualityFloor);
      double liq_floor = (m_active_policy.runner_liquidity_rr_floor > 0.0 ? m_active_policy.runner_liquidity_rr_floor : InpRunnerLiquidityRRFloor);
      double cost_ceiling = (m_active_policy.runner_cost_r_ceiling > 0.0 ? m_active_policy.runner_cost_r_ceiling : InpRunnerCostRCeiling);
      double align_floor = (m_active_policy.runner_alignment_floor > 0.0 ? m_active_policy.runner_alignment_floor : InpRunnerHtfAlignmentFloor);
      double adverse_ceiling = (m_active_policy.runner_adverse_ceiling > 0.0 ? m_active_policy.runner_adverse_ceiling : InpRunnerAdverseContextCeiling);
      double ev_floor = m_active_policy.runner_ev_floor;

      if(p.sequence_quality < seq_floor){ reason = "runner_sequence_quality"; return false; }
      if(p.liquidity_rr < liq_floor){ reason = "runner_liquidity_rr"; return false; }
      if((p.execution_cost_r + p.slippage_r + p.commission_r) > cost_ceiling){ reason = "runner_cost_burden"; return false; }
      if(p.htf_alignment_score < align_floor){ reason = "runner_htf_alignment"; return false; }
      if(p.adverse_context_score > adverse_ceiling){ reason = "runner_adverse_context"; return false; }
      if(ev_floor > 0.0 && p.net_reward_after_cost_r < ev_floor){ reason = "runner_net_reward_floor"; return false; }
      return true;
   }

   bool _DowngradeRunnerToStandard(TradePlan &p, const string downgrade_reason) {
      if(!InpAllowRunnerDowngrade) return false;
      double risk = MathAbs(p.entry_est - p.sl);
      if(risk <= 0.0) return false;
      p.runner_downgraded = true;
      p.runner_downgrade_reason = downgrade_reason;
      p.original_runner_target = p.tp2;
      p.runner_trade = false;
      p.management_profile = "standard_micro";

      double rr = _ExecutionRR2(p);
      double min_rr = MathMax(_FamilyMinRR(p), InpStandardTradeLiquidityRRFloor);
      double total_cost_r = p.execution_cost_r + p.slippage_r + p.commission_r;
      if(!_RRMeetsFloor(rr, min_rr) || total_cost_r > InpStandardTradeCostRCeiling){
         double fallback_rr = MathMax(min_rr, _EffectiveFallbackRR());
         if(InpMaxPlanRR2 > 0.0) fallback_rr = MathMin(fallback_rr, InpMaxPlanRR2);
         double reward = risk * fallback_rr;
         if(reward <= 0.0) return false;
         p.tp2 = (p.is_buy ? p.entry_est + reward : p.entry_est - reward);
         p.tp_model = "runner_downgrade_synthetic_rr";
         p.target_source = "runner_downgrade_synthetic_rr";
         p.effective_rr2 = fallback_rr;
      }
      p.standard_target_after_downgrade = p.tp2;
      _EstimateExecutionCosts(p);
      p.heuristic_quality_estimate_gross = _HeuristicQualityEstimateGrossDiagnostic(p);
      p.heuristic_quality_estimate = _HeuristicQualityEstimateDiagnostic(p);
      _FinalizePlanEconomics(p, "runner_downgrade", false);
      p.gross_expected_r = p.heuristic_quality_estimate_gross;
      p.net_expected_r = p.heuristic_quality_estimate;
      p.expected_value_r = p.heuristic_quality_estimate;
      return (_RRMeetsFloor(_ExecutionRR2(p), min_rr) &&
              (p.execution_cost_r + p.slippage_r + p.commission_r) <= InpStandardTradeCostRCeiling);
   }

   bool _DeterministicExecutionGate(TradePlan &p, string &reason, const bool execution_stage=true) {
      reason = "ok";
      PO3State state = (p.po3.state != PO3_IDLE ? p.po3.state : PO3StateFromString(p.po3.po3_state));
      bool requires_full_po3 = _FamilyRequiresFullPO3Sequence(p);
      bool tier_b_execution_allowed = (!InpRequireConfirmedPO3ForExecution &&
                                       p.po3.context_tier == "B" &&
                                       p.po3.has_sweep &&
                                       p.po3.has_displacement &&
                                       !p.po3.sweep_running &&
                                       (state == PO3_DISPLACEMENT_CONFIRMED ||
                                        state == PO3_DEVELOPING ||
                                        state == PO3_FVG_CONFIRMED ||
                                        state == PO3_ENTRY_WAITING));
      string hard_reason = "";
      if(!_ObjectiveHardPreTradeGate(p, execution_stage ? "pre_order_hard_gate" : "pre_ai_hard_gate", hard_reason)){
         reason = hard_reason;
         return false;
      }
      if(PO3IsTerminalBadState(p.po3)){
         reason = "po3_state_" + p.po3.po3_state;
         return false;
      }
      if(execution_stage && requires_full_po3 && (!p.po3.has_sweep || p.po3.sweep_running || p.po3.t_sweep <= 0)){
         reason = "closed_sweep_missing";
         return false;
      }
      if(execution_stage && requires_full_po3 && (!p.po3.has_displacement || p.po3.t_disp <= p.po3.t_sweep)){
         reason = "displacement_not_after_sweep";
         return false;
      }
      if(execution_stage && requires_full_po3 && (!p.po3.has_bos || p.po3.t_bos <= p.po3.t_disp) && !tier_b_execution_allowed){
         reason = "structure_not_after_displacement";
         return false;
      }
      // PRE-AI BOS CONTRACT GATE.
      // The decision contract marks htf_bos_required for full-PO3 families, so a
      // candidate carrying setup_family=full_po3_* with has_bos=false is a *guaranteed*
      // provider rejection (observed live as ai_veto_missing_mandatory_evidence on 7 of
      // 10 decisions).  Sending it burns a paid provider call for a foregone outcome.
      //
      // The tier-B waiver above deliberately lets a developing context reach EXECUTION
      // once something has approved it; it cannot waive the AI's own evidence
      // requirement, because the AI is the thing doing the approving.  So this check is
      // intentionally not subject to tier_b_execution_allowed.
      if(!execution_stage && InpUseAI && requires_full_po3 &&
         (!p.po3.has_bos || p.po3.t_bos <= p.po3.t_disp)){
         reason = "full_po3_family_without_confirmed_bos";
         _Journal("[bos_contract_gate] symbol=" + p.symbol
                  + " family=" + (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p))
                  + " has_bos=" + (p.po3.has_bos ? "true" : "false")
                  + " htf_bos=" + (p.po3.htf_bos ? "true" : "false")
                  + " ltf_bos=" + (p.po3.ltf_bos ? "true" : "false")
                  + " developing_bos=" + (p.po3.developing_bos ? "true" : "false")
                  + " t_bos=" + IntegerToString((long)p.po3.t_bos)
                  + " t_disp=" + IntegerToString((long)p.po3.t_disp)
                  + " context_tier=" + p.po3.context_tier
                  + " po3_state=" + p.po3.po3_state
                  + " action=reject_before_provider_call");
         return false;
      }
      if(execution_stage && InpRequireFvgAfterDisp && p.fvg.t_form <= p.po3.t_disp){
         reason = "fvg_not_after_impulse";
         return false;
      }
      if(execution_stage && InpRequireConfirmedPO3ForExecution && state != PO3_CONFIRMED){
         reason = "po3_not_confirmed_" + (StringLen(p.po3.po3_state) > 0 ? p.po3.po3_state : "missing");
         return false;
      }
      if(execution_stage && !p.armed){
         reason = "entry_retrace_not_confirmed";
         return false;
      }
      if(execution_stage && InpUseAI){
         bool full_structured = (p.ai.decision_quality_tier == "FULL_STRUCTURED" ||
                                 p.ai.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
         if(!full_structured){
            reason = "degraded_ai_response_non_trading";
            return false;
         }
         if(!p.ai.mandatory_fields_complete){
            reason = "ai_quality_schema_incomplete";
            return false;
         }
         if(p.ai.decision_state == "ABSTAIN"){
            reason = "ai_abstain";
            return false;
         }
         if(p.ai.decision_state != "APPROVE" || !p.ai.allow || !p.ai.raw_allow){
            reason = "ai_raw_allow_false";
            return false;
         }
         if(p.ai.repeatability_schema_version != REPEATABILITY_SCHEMA_VERSION ||
            p.ai.hierarchical_prior_schema_version != HIERARCHICAL_PRIOR_SCHEMA_VERSION){
            reason = "ai_quality_schema_incomplete";
            return false;
         }
         if(p.ai.repeatability_required_live &&
            (p.ai.repeatability_status != "REPEATABLE" || !p.ai.repeatability_trading_eligible)){
            reason = (StringLen(p.ai.repeatability_rejection_code) > 0
                      ? p.ai.repeatability_rejection_code
                      : "repeatability_unavailable");
            return false;
         }
         string quality_threshold_source = "";
         double quality_threshold = EffectiveLlmQualityScoreThreshold(p, quality_threshold_source);
         // Uncalibrated LLM quality thresholds remain observable for cohort
         // analysis but have no direct positive or negative trade authority.
         if(p.ai.suggested_risk_multiplier <= 0.0){
            reason = "resolved_risk_multiplier_zero";
            return false;
         }
      }
      if(_IsFvgEntryNonTradable(p.fvg_execution_class)){
         reason = "fvg_invalidated_or_fully_filled";
         return false;
      }
      if(p.subtype_policy_action == "suppress"){
         reason = "subtype_suppressed";
         return false;
      }
      if(p.ote_state == "lost"){
         reason = "ote_softness_exceeded";
         return false;
      }
      double rr = _ExecutionRR2(p);
      double min_rr = MathMax(0.0, _FamilyMinRR(p));
      if(!_RRMeetsFloor(rr, min_rr)){
         reason = "effective_rr2=" + DoubleToString(rr, 2);
         return false;
      }
      if(p.obstacle_r > 0 && p.obstacle_r < InpObstacleMinStopMult){
         reason = (_FamilyGroup(p) == "continuation" ? "continuation_obstacle_too_close" : "obstacle_too_close");
         return false;
      }
      double total_cost_r = _TotalExecutionCostR(p);
      if(total_cost_r > _FamilyCostCeiling(p)){
         reason = (_FamilyGroup(p) == "continuation" ? "continuation_cost_too_high" : "execution_cost_too_high");
         return false;
      }
      _FinalizePlanEconomics(p, "deterministic_execution_gate", false);
      if(p.net_reward_after_cost_r < InpMinNetExpectedR){
         reason = "net_reward_after_cost_r_too_low";
         return false;
      }
      double floor = _DeterministicSetupScoreFloor(p);
      if(!execution_stage && p.setup_floor_action == "soft_penalty" && !InpAiStrict){
         floor = MathMax(18.0, p.setup_floor_score - 6.0);
      }
      if(p.setup_score < floor){
         reason = "setup_score=" + DoubleToString(p.setup_score, 2) + "<" + DoubleToString(floor, 2);
         return false;
      }
      if(p.runner_trade){
         string runner_reason = "";
         if(!_RunnerFiltersPass(p, runner_reason)){
            if(!_DowngradeRunnerToStandard(p, runner_reason)){
               reason = runner_reason;
               return false;
            }
         }
      }
      return true;
   }

   string _AiVetoReason(const AiDecision &dec) const {
      if(!dec.ok) return "";
      if(InpAiVetoEnable && dec.veto_fields_present){
         if(dec.veto_enabled){
            string evidence = dec.veto_evidence_fields_json;
            StringReplace(evidence, " ", "");
            if(StringLen(dec.veto_code) == 0 || evidence == "[]" || StringLen(dec.veto_reason) == 0)
               return "ai_quality_schema_incomplete";
            return dec.veto_code + ":" + dec.veto_reason;
         }
      }
      return "";
   }

   bool _AiHardVeto(const AiDecision &dec) const {
      return (StringLen(_AiVetoReason(dec)) > 0);
   }

   double _RequiredAiScore(const TradePlan &p) const {
      string source = "";
      return MathMax(0.0, EffectiveLlmQualityScoreThreshold(p, source));
   }

   double _SetupScore(const TradePlan &p) {
      double score = p.fvg.score;
      if(p.fvg_execution_class == "fresh_fvg" || p.fvg_execution_class == "virgin_fvg") score += 4.5;
      else if(p.fvg_execution_class == "touched_fvg") score += 2.6;
      else if(p.fvg_execution_class == "mid_mitigated_fvg" || p.fvg_execution_class == "mid_mitigated_reentry") score += 1.8;
      else if(p.fvg_execution_class == "stale_fvg") score -= 3.5;
      else if(p.fvg_execution_class == "continuation_reentry") score += 3.6;
      else if(_IsFvgEntryNonTradable(p.fvg_execution_class)) score -= 18.0;

      string family_group = _FamilyGroup(p);
      if(family_group == "reversal") score += 4.0;
      else if(family_group == "continuation") score += 3.2;
      else if(family_group == "edge") score += 3.4;
      else if(family_group == "failed_breakout") score += 3.0;
      else if(family_group == "range") score += 2.6;

      if(p.entry_branch == "breaker_retest") score += 3.0;
      else if(p.entry_branch == "ote_inside_fvg") score += 2.4;
      else if(p.entry_branch == "nested_htf_ltf_fvg" || p.entry_branch == "nested_fvg_edge") score += 2.8;
      else if(p.entry_branch == "session_reentry") score += 2.0;
      else if(p.entry_branch == "range_reentry") score += 2.2;
      else if(p.entry_branch == "continuation_reentry") score += 2.5;
      score += p.trend_strength * 20.0;
      score += MathMin(12.0, p.po3.sweep_strength * 10.0);
      if(p.po3.in_killzone) score += 6.0;
      if(p.po3.session_name == "OFF_HOURS") score -= 4.0;
      if(p.po3.htf_mss) score += 5.0;
      if(p.po3.htf_choch) score += 4.0;
      if(p.po3.ltf_bos) score += 4.0;
      if(p.po3.ltf_mss) score += 3.0;
      if(p.po3.ltf_choch) score += 2.0;
      if(p.adx_value >= InpAdxMin) score += 4.0;
      if(p.session_vol_ratio >= InpSessionVolMinAdrFrac) score += MathMin(6.0, p.session_vol_ratio * 6.0);
      if(p.vwap_dist_atr > 0 && p.vwap_dist_atr <= InpVwapMaxDistAtr) score += 3.0;
      if(p.expansion_score >= InpExpansionRatioMin && p.expansion_score <= InpExpansionRatioMax) score += 4.0;
      score -= MathMin(6.0, p.news_risk * 6.0);
      double rr2 = _ExecutionRR2(p);
      if(rr2 > 0){
         if(rr2 < 1.2) score += rr2 * 4.0;
         else if(rr2 <= 4.5) score += 10.0 + MathMin(5.0, (rr2 - 1.2) * 1.4);
         else if(rr2 <= InpMaxPlanRR2) score += MathMax(4.0, 12.0 - (rr2 - 4.5) * 1.5);
         else score -= MathMin(12.0, (rr2 - InpMaxPlanRR2) * 2.0);
      }
      double planned_risk = MathAbs(p.entry_est - p.sl);
      double stop_floor = _MinPlanStopDistance(p, p.entry_est);
      if(stop_floor > 0 && planned_risk < stop_floor * 1.01) score -= 2.0;
      if(p.tp_model == "liquidity_target") score += 4.0;
      if(StringFind(p.tp_model, "htf_opposing_imbalance") >= 0) score += 2.5;
      if(StringFind(p.tp_model, "opposing_imbalance") >= 0) score += 1.8;
      if(p.po3.liquidity_kind == "equal_high_cluster" || p.po3.liquidity_kind == "equal_low_cluster") score += 4.0;
      if(p.po3.liquidity_kind == "prev_week_high" || p.po3.liquidity_kind == "prev_week_low") score += 4.0;
      if(p.po3.context_tier == "B") score -= InpDevelopingContextExecutionPenalty;
      else if(p.po3.context_tier == "C") score -= InpDevelopingContextExecutionPenalty * 1.5;
      score += p.fvg.origin_score;
      score += p.fvg.cleanliness_score * 0.6;
      score += p.fvg.nesting_score * 0.8;
      score += p.fvg.htf_overlap_score * 0.8;
      score += p.fvg.retest_score * 0.6;
      if(StringFind(p.obstacle_kind, "htf_opposing_imbalance") >= 0) score -= 4.0;
      else if(StringLen(p.obstacle_kind) > 0) score -= 1.5;
      return score;
   }

   bool _TryBuildCandidateFromBranch(const TradePlan &base, const FVGZone &zone, const string branch,
                                     TradePlan &out_plan, string &reason) {
      reason = "ok";
      out_plan = base;   // work directly on the caller's plan so every exit publishes
      out_plan.fvg = zone;
      out_plan.entry_branch = branch;
      out_plan.entry_model = branch;
      out_plan.setup_taxonomy = UNKNOWN_UNCLASSIFIED;
      out_plan.setup_taxonomy_version = "";
      out_plan.setup_taxonomy_enum = "";
      out_plan.taxonomy_mapping_source = "";
      out_plan.taxonomy_mapping_failure_reason = "";
      out_plan.setup_type = "";
      out_plan.setup_subtype = "";
      out_plan.setup_story_scope = "";
      if(!_BranchEnabledByConfig(branch, reason)) return false;
      if(InpRequireFvgAfterDisp && out_plan.po3.has_displacement && zone.t_form <= out_plan.po3.t_disp){
         reason = "fvg_not_after_impulse";
         return false;
      }
      if(out_plan.po3.has_sweep && out_plan.po3.has_displacement && out_plan.po3.has_bos){
         PO3SetState(out_plan.po3, PO3_FVG_CONFIRMED, "valid_fvg_after_impulse");
      } else if(!out_plan.po3.has_bos){
         if(out_plan.po3.has_displacement)
            PO3SetState(out_plan.po3, PO3_DISPLACEMENT_CONFIRMED, "missing_structure_confirmation");
         else if(out_plan.po3.has_sweep)
            PO3SetState(out_plan.po3, PO3_SWEEP_CONFIRMED, "missing_displacement_confirmation");
         else
            PO3SetState(out_plan.po3, PO3_DEVELOPING, "missing_po3_sequence");
      }
      if(branch == "fvg_edge") out_plan.entry_model = (out_plan.is_buy ? "fvg_upper" : "fvg_lower");
      out_plan.fvg_execution_class = _FvgExecutionClass(out_plan);
      string taxonomy_reason = "";
      if(!_ResolveSetupTaxonomy(out_plan, taxonomy_reason)){
         reason = "unknown_setup_taxonomy";
         _LogUnknownSetupTaxonomy(out_plan, "candidate_generation");
         return false;
      }
      out_plan.setup_family = _DeriveSetupFamily(out_plan);
      string family_reason = "";
      if(!_FamilyAllowedByStrategy(out_plan, family_reason)){
         reason = family_reason;
         return false;
      }
      if(out_plan.setup_family == "micro_continuation_fvg" && !out_plan.po3.has_displacement){
         reason = "continuation_no_impulse";
         return false;
      }
      if(out_plan.setup_family == "micro_continuation_fvg" && out_plan.po3.t_disp > 0 && zone.t_form < out_plan.po3.t_disp){
         reason = "continuation_fvg_missing";
         return false;
      }
      if(out_plan.setup_family == "micro_failed_breakout_reclaim" && out_plan.po3.t_sweep > 0 && zone.t_form <= out_plan.po3.t_sweep){
         reason = "failed_breakout_fvg_missing";
         return false;
      }
      out_plan.setup_class = _DeriveSetupClass(out_plan);
      _ApplySetupManagementProfile(out_plan);
      _InitializeNarrativeFields(out_plan);
      if(InpOnlyBreakerRetestVirginStrongOrigin && InpExclusiveModelFilterBeforeAI){
         if(!_ApplyExclusiveModelFilter(out_plan, "exclusive_model_filter", true)){
            reason = "exclusive_breaker_retest_virgin_strong_origin_only";
            return false;
         }
      }

      double entry_price = 0.0;
      if(!_ResolveBranchEntryPrice(out_plan, branch, entry_price, reason)) return false;
      string price_reason = "";
      if(!_BuildPlanPrices(out_plan, entry_price, price_reason)){
         reason = (StringLen(price_reason) > 0 ? price_reason : "invalid_plan_prices");
         return false;
      }
      m_funnel_plan_prices_valid++;
      m_total_plans_valid++;
      if(out_plan.po3.state == PO3_FVG_CONFIRMED)
         PO3SetState(out_plan.po3, PO3_ENTRY_WAITING, "valid_entry_zone_waiting");
      if(!m_po3.CheckOTE(out_plan)){
         reason = "ote_failed";
         return false;
      }
      string live_reason = "";
      if(!_WatchlistStillValidEx(out_plan, live_reason)){
         reason = live_reason;
         return false;
      }

      _LoadActivePolicyIfNeeded();
      _PopulateDerivedPlanFields(out_plan);
      out_plan.setup_score = _SetupScore(out_plan);
      string subtype_reason = "";
      if(!_ApplySubtypeEvidence(out_plan, subtype_reason)){
         reason = subtype_reason;
         return false;
      }
      string context_reason = "";
      if(!_ApplyContextPolicy(out_plan, context_reason)){
         reason = context_reason;
         return false;
      }
      string session_weekday_reason = "";
      if(!_ApplySessionWeekdayPolicy(out_plan, session_weekday_reason)){
         reason = session_weekday_reason;
         return false;
      }
      _PopulateDerivedPlanFields(out_plan);
      string floor_reason = "";
      if(!_ApplyPreAiSetupFloor(out_plan, floor_reason)){
         reason = floor_reason;
         return false;
      }
      _PopulateDerivedPlanFields(out_plan);
      string rule_reason = "";
      if(!_DeterministicExecutionGate(out_plan, rule_reason, false)){
         reason = rule_reason;
         return false;
      }
      // out_plan already holds the finished plan -- see the assignment above.
      return true;
   }

   bool _CandidateRanksAhead(const TradePlan &a, const TradePlan &b) const {
      if(a.setup_score > b.setup_score) return true;
      if(a.setup_score < b.setup_score) return false;
      if(a.net_reward_after_cost_r > b.net_reward_after_cost_r) return true;
      if(a.net_reward_after_cost_r < b.net_reward_after_cost_r) return false;
      return (a.trend_strength > b.trend_strength);
   }

   void _SortCandidateGroup(TradePlan &cands[]) const {
      int n = ArraySize(cands);
      for(int i=0; i<n-1; i++){
         int best = i;
         for(int j=i+1; j<n; j++){
            if(_CandidateRanksAhead(cands[j], cands[best])) best = j;
         }
         if(best != i){
            TradePlan tmp = cands[i];
            cands[i] = cands[best];
            cands[best] = tmp;
         }
      }
   }

   void _QueueScanCandidate(const TradePlan &p) {
      int per_symbol_limit = MathMax(1, InpMaxFvgCandidatesPerSymbol);
      int symbol_count = 0;
      int worst_idx = -1;
      for(int i=0; i<ArraySize(m_scan_candidates); i++){
         if(m_scan_candidates[i].symbol != p.symbol) continue;
         symbol_count++;
         if(worst_idx < 0 || _CandidateRanksAhead(m_scan_candidates[worst_idx], m_scan_candidates[i]))
            worst_idx = i;
      }

      if(symbol_count < per_symbol_limit){
         int n = ArraySize(m_scan_candidates);
         ArrayResize(m_scan_candidates, n + 1);
         m_scan_candidates[n] = p;
         return;
      }
      if(worst_idx >= 0 && _CandidateRanksAhead(p, m_scan_candidates[worst_idx]))
         m_scan_candidates[worst_idx] = p;
   }

   bool _AddToWatchlist(const TradePlan &p) {
      TradePlan staged = p;
      _InitializeNarrativeFields(staged);
      bool tester_bootstrap = _IsTesterBootstrapPlan(staged);
      bool full_structured = (staged.ai.decision_quality_tier == "FULL_STRUCTURED" || staged.ai.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
      if(!tester_bootstrap && (!full_structured || !staged.ai.mandatory_fields_complete || staged.ai.decision_state != "APPROVE" ||
         !staged.ai.allow || !staged.ai.raw_allow)){
         string response_reason = (!full_structured ? "degraded_ai_response_non_trading" : "ai_quality_schema_incomplete");
         if(staged.ai.decision_state == "ABSTAIN") response_reason = "ai_abstain";
         _LogSetupReject(staged.symbol, "decision_integrity", response_reason,
                         "decision_quality_tier=" + staged.ai.decision_quality_tier
                         + " decision_state=" + staged.ai.decision_state);
         return false;
      }
      if(staged.ai.suggested_risk_multiplier <= 0.0){
         _LogSetupReject(staged.symbol, "decision_integrity", "resolved_risk_multiplier_zero", "stage=watchlist");
         return false;
      }
      if(staged.candidate_hash != staged.ai_selected_candidate_hash ||
         staged.ai.selected_candidate_hash != staged.candidate_hash ||
         staged.request_execution_fingerprint != staged.ai.request_execution_fingerprint ||
         _AssessedFingerprintFromDecision(staged, staged.ai) != staged.assessed_execution_fingerprint){
         _LogSetupReject(staged.symbol, "decision_integrity", "candidate_hash_mismatch",
                         "ai_selected_candidate_hash=" + staged.ai.selected_candidate_hash
                         + " executed_candidate_hash=" + staged.candidate_hash);
         return false;
      }
      _FreezeSourcePO3Story(staged);
      _ApplySetupManagementProfile(staged);
      string outcome_policy_reason = "";
      bool was_mpc = _PlanIsMPC(staged);
      if(!_ApplyOutcomePolicy(staged, "watchlist", outcome_policy_reason)){
         if(was_mpc) m_total_mpc_watchlist_blocks++;
         return false;
      }
      if(_PlanUsesSyntheticFallback(staged) && !_RecomputeSyntheticFallbackTarget(staged, "watchlist_precheck_after_ai_or_rebuild")){
         _LogSetupReject(p.symbol, "ai_target_validation", "ai_selected_synthetic_fallback_invalid", "stage=watchlist_precheck");
         _Journal("[watchlist_precheck] pass=false reason=ai_selected_synthetic_fallback_invalid action=not_added");
         return false;
      }
      if(InpOnlyBreakerRetestVirginStrongOrigin){
         bool count_filter = !InpExclusiveModelFilterBeforeAI;
         if(!_ApplyExclusiveModelFilter(staged, "exclusive_model_filter", count_filter))
            return false;
      }
      if(_HasSymbolInPlans(m_watchlist, staged.symbol)){
         _LogSetupReject(p.symbol, "watchlist", "already_busy", "watchlist_size=" + IntegerToString(ArraySize(m_watchlist)));
         _Journal(p.symbol + " watchlist add skipped: already busy");
         return false;
      }
      if(_ShouldSkipForGlobalOpenPositions()){
         _LogSetupReject(p.symbol, "watchlist", "global_position_busy", "open_positions=" + IntegerToString(_CountOpenPositions()));
         _Journal(p.symbol + " watchlist add skipped: another managed position is already open");
         return false;
      }
      if(InpSkipIfSymbolOpen && _SymbolHasOpenPosition(staged.symbol)){
         _LogSetupReject(p.symbol, "watchlist", "symbol_position_active", "");
         _Journal(p.symbol + " watchlist add skipped: symbol position is already active");
         return false;
      }
      if(_HasBlockingPendingOrderForPlan(staged)){
         _LogSetupReject(p.symbol, "watchlist", "symbol_exposure_active", "");
         _Journal(p.symbol + " watchlist add skipped: symbol exposure is already active");
         return false;
      }
      string sweep_reason = "";
      if(_SweepTradeCapReached(staged, true, sweep_reason)){
         _LogSetupReject(p.symbol, "watchlist", sweep_reason, "source_story=" + _PO3StoryId(staged));
         _Journal(p.symbol + " watchlist add skipped: " + sweep_reason);
         return false;
      }
      string target_validation_reason = "";
      string target_validation_detail = "";
      if(!ValidateAiChosenTargetBeforeWatchlist(staged, target_validation_reason, target_validation_detail)){
         staged.narrative_state = "invalidated";
         staged.invalidation_cause = target_validation_reason;
         _WriteTradeMeta(staged);
         _LogSetupReject(p.symbol, "ai_target_validation", target_validation_reason,
                         target_validation_detail
                         + " chosen=" + staged.target_model
                         + " rr2=" + DoubleToString(_ExecutionRR2(staged), 4)
                         + " min_rr=" + DoubleToString(InpMinLiveRR2, 4)
                         + " obstacle_kind=" + staged.obstacle_kind);
         _Journal(p.symbol + " watchlist add skipped: " + target_validation_reason
                  + " detail=" + target_validation_detail);
         return false;
      }
      string precheck_reason = "";
      if(!_WatchlistStillValidEx(staged, precheck_reason)){
         staged.narrative_state = "invalidated";
         staged.invalidation_cause = precheck_reason;
         _WriteTradeMeta(staged);
         m_funnel_watchlist_precheck_rejects++;
         _TrackNamedCounter(m_funnel_watchlist_precheck_reasons, m_funnel_watchlist_precheck_counts, precheck_reason);
         _TrackNamedCounter(m_total_watchlist_precheck_reasons, m_total_watchlist_precheck_counts, precheck_reason);
         _LogSetupReject(p.symbol, "watchlist_precheck", precheck_reason,
                         "entry=" + _FmtPrice(staged.symbol, staged.entry_est)
                         + " tp2=" + _FmtPrice(staged.symbol, staged.tp2)
                         + " rr2=" + DoubleToString(_ExecutionRR2(staged), 6));
         _Journal("[watchlist_precheck] pass=false reason=" + precheck_reason + " action=not_added");
         return false;
      }
      string fingerprint_changes = "";
      if(!_ExecutionFingerprintWithinTolerance(staged, fingerprint_changes)){
         _LogSetupReject(staged.symbol, "decision_integrity", "execution_fingerprint_mismatch",
                         "stage=watchlist changed_components=" + fingerprint_changes
                         + " assessed_execution_fingerprint=" + staged.assessed_execution_fingerprint
                         + " final_execution_fingerprint=" + staged.final_execution_fingerprint);
         return false;
      }
      _Journal("[watchlist_precheck] pass=true reason=" + precheck_reason + " action=added");
      int n = ArraySize(m_watchlist);
      ArrayResize(m_watchlist, n+1);
      m_watchlist[n] = staged;
      if(m_watchlist[n].arm_max_bars <= 0) m_watchlist[n].arm_max_bars = _EffectiveWatchlistMaxBars();
      // Start watchlist aging when the setup actually enters the watchlist,
      // not when the earlier scan or AI request was created.
      m_watchlist[n].created_at = TimeLocal();
      m_watchlist[n].last_score_refresh = m_watchlist[n].created_at;
      m_watchlist[n].last_confirm_bar_time = _LastClosedBarTime(m_watchlist[n].symbol, m_watchlist[n].confirm_tf);
      m_watchlist[n].bars_waited = 0;
      m_watchlist[n].armed = false;
      m_watchlist[n].armed_at = 0;
      if(StringLen(m_watchlist[n].ai_decision_source) == 0) m_watchlist[n].ai_decision_source = "llm_full_structured";
      m_watchlist[n].narrative_state = "staged";
      m_funnel_watchlist_added++;
      m_total_watchlist_added++;
      _Journal(p.symbol + " added to watchlist entry=" + DoubleToString(m_watchlist[n].entry_est, 5)
               + " rr2=" + DoubleToString(_ExecutionRR2(m_watchlist[n]), 2)
               + " setup_class=" + m_watchlist[n].setup_class
               + " po3_scope=" + m_watchlist[n].po3.po3_scope
               + " source_story=" + _PO3StoryId(m_watchlist[n])
               + " attempt_number_for_sweep=" + IntegerToString(m_watchlist[n].attempt_number_for_sweep));
      return true;
   }

   void _PrepareTesterBootstrapApproval(TradePlan &p) {
      // Canonicalize the deterministic target exactly as the normal approved
      // target path does before the assessed plan is locked.  Candidate/request
      // identity remains the immutable pre-decision identity; execution identity
      // records the selected canonical target.
      _NormalizeTargetLabels(p);
      p.ai_chosen_target_model = _EffectiveTargetModel(p);
      _NormalizeTargetLabels(p);

      AiDecision dec;
      ZeroMemory(dec);
      dec.ok = true;
      // There is deliberately no model/Python authority in bootstrap mode.
      // The explicit tester-only MQL authority is recorded separately.
      dec.allow = false;
      dec.raw_allow = false;
      dec.model_raw_allow = false;
      dec.python_final_allow = false;
      dec.mql_final_allow = false;
      dec.decision_state = "APPROVE";
      dec.decision_quality_tier = "BOOTSTRAP_RULE_ONLY";
      dec.response_quality_alias = "BOOTSTRAP_RULE_ONLY";
      dec.mandatory_fields_complete = true;
      dec.decision_schema_version = "20260809_tester_bootstrap_rule_only_v1";
      dec.provider_contract_version = "tester_bootstrap_rule_only_v1";
      dec.provider_mode = "TESTER_BOOTSTRAP_RULE_ONLY";
      dec.provider_id = "mql_deterministic_engine";
      dec.endpoint_class = "not_applicable";
      dec.endpoint_identity_hash = "tester_bootstrap_not_applicable";
      dec.configured_models_hash = "tester_bootstrap_not_applicable";
      dec.actual_model_id = "tester_bootstrap_rule_only";
      dec.fallback_model = "not_applicable";
      dec.model_fingerprint = "tester_bootstrap_rule_only_v1";
      dec.evidence_envelope_version = "tester_bootstrap_evidence_v1";
      dec.family_profile_version = "20260809_family_context_v2";
      dec.memory_schema_version = "tester_bootstrap_memory_seed_v1";
      dec.retrieval_policy_version = "tester_bootstrap_no_retrieval_v1";
      dec.role_contract_version = "tester_bootstrap_no_roles_v1";
      dec.consensus_resolver_version = "tester_bootstrap_deterministic_selection_v1";
      dec.generation_settings_hash = "tester_bootstrap_not_applicable";
      dec.input_fingerprint = p.request_execution_fingerprint;
      dec.historical_evidence_state = "INSUFFICIENT_SAMPLE";
      dec.final_resolver_reason = "deterministic_tester_bootstrap";
      dec.provider_health_state = "NOT_APPLICABLE";
      dec.role_latencies_json = "{}";
      dec.provider_retry_counts_json = "{}";
      dec.provider_usage_json = "{}";
      dec.unsupported_generation_parameters_json = "[]";
      dec.analyst_output_json = "{}";
      dec.critic_output_json = "{}";
      dec.adjudicator_output_json = "{}";
      dec.chosen_index = p.candidate_index;
      dec.selected_candidate_id = p.candidate_id;
      dec.selected_candidate_hash = p.candidate_hash;
      dec.request_execution_fingerprint = p.request_execution_fingerprint;
      dec.selected_target_identity = _EffectiveTargetModel(p);
      dec.selected_target_price = p.tp2;
      dec.assessed_entry = p.entry_est;
      dec.assessed_sl = p.sl;
      dec.assessed_tp1 = p.tp1;
      dec.assessed_tp2 = p.tp2;
      dec.rule_score = p.setup_score;
      dec.suggested_risk_multiplier = MathMax(0.01, MathMin(1.0, InpTesterBootstrapRiskMultiplier));
      dec.model_version = "tester_bootstrap_rule_only_v1";
      dec.reasons_json = "tester_only_deterministic_cold_start_seed";
      dec.decision_source = "bootstrap_rule_only";
      dec.decision_id = "bootstrap_" + p.candidate_hash;
      dec.rejection_codes_json = "[]";
      dec.narrative_state = "bootstrap_staged";
      dec.invalidation_risks_json = "[]";
      dec.missing_confirmations_json = "[]";
      dec.veto_fields_present = true;
      dec.veto_enabled = false;
      dec.veto_evidence_fields_json = "[]";
      dec.llm_numeric_diagnostics_authority = "not_applicable_tester_bootstrap";
      dec.chosen_target_model = _EffectiveTargetModel(p);
      dec.chosen_tp1 = p.tp1;
      dec.chosen_tp2 = p.tp2;
      dec.chosen_rr2 = _ExecutionRR2(p);
      dec.target_decision_reason = "deterministic_plan_target";
      dec.target_arbitration_schema_version = "tester_bootstrap_target_v1";
      dec.prompt_contract_version = "tester_bootstrap_no_prompt_v1";
      dec.workload_mode = "TESTER_AI_BOOTSTRAP_RULE_ONLY";
      dec.reasoning_configuration = "deterministic_rule_only";
      dec.bucket_prior_hash = "tester_bootstrap_no_prior";
      dec.calibration_artifact_id = "tester_bootstrap_not_applicable";
      dec.hierarchical_prior_artifact_hash = "tester_bootstrap_no_prior";
      dec.hierarchical_prior_schema_version = "tester_bootstrap_no_prior_v1";
      dec.repeatability_schema_version = "tester_bootstrap_not_applicable_v1";
      dec.repeatability_status = "NOT_APPLICABLE";
      dec.repeatability_required_live = false;
      dec.repeatability_artifact_state = "not_applicable_tester_bootstrap";
      dec.repeatability_score_threshold_authority = false;
      dec.repeatability_trading_eligible = false;
      dec.repeatability_group_key = "tester_bootstrap";
      dec.repeatability_authority_hash = "tester_bootstrap_not_applicable";

      dec.assessed_execution_fingerprint = _AssessedFingerprintFromDecision(p, dec);
      p.ai = dec;
      p.ai_decision_id = dec.decision_id;
      p.ai_decision_source = dec.decision_source;
      p.model_raw_allow = false;
      p.python_final_allow = false;
      p.mql_final_allow = false;
      p.decision_field_authority_json = "{\"bootstrap_rule_only\":{\"owner\":\"mql_tester\",\"authority\":\"tester_only\"}}";
      p.python_decision_reasons = "not_applicable_tester_bootstrap";
      p.mql_decision_reasons = "pending_final_mql_execution_gates";
      p.reasoning_configuration = dec.reasoning_configuration;
      p.prompt_contract_version = dec.prompt_contract_version;
      p.bucket_prior_hash = "tester_bootstrap_no_prior";
      p.calibration_artifact_id = dec.calibration_artifact_id;
      p.repeatability_artifact_id = dec.repeatability_authority_hash;
      p.hierarchical_prior_artifact_hash = dec.hierarchical_prior_artifact_hash;
      p.hierarchical_prior_schema_version = dec.hierarchical_prior_schema_version;
      p.repeatability_status = dec.repeatability_status;
      p.repeatability_required_live = false;
      p.repeatability_artifact_state = dec.repeatability_artifact_state;
      p.repeatability_rejection_code = "";
      p.repeatability_score_threshold_authority = false;
      p.repeatability_trading_eligible = false;
      p.repeatability_group_key = dec.repeatability_group_key;
      p.repeatability_authority_hash = dec.repeatability_authority_hash;
      p.ai_selected_candidate_hash = p.candidate_hash;
      p.candidate_hash_match = true;
      p.assessed_execution_fingerprint = dec.assessed_execution_fingerprint;
      _LockAssessedPlan(p, dec);
      _PopulateCohortMetadata(p);
   }

   bool _QueueTesterBootstrapCandidate(TradePlan &cands[]) {
      for(int i=0; i<ArraySize(cands); i++){
         TradePlan selected = cands[i];
         string deterministic_reason = "";
         if(!_DeterministicExecutionGate(selected, deterministic_reason, false)){
            _LogSetupReject(selected.symbol, "bootstrap_precheck", deterministic_reason,
                            "candidate=" + IntegerToString(selected.candidate_index));
            continue;
         }
         _PrepareTesterBootstrapApproval(selected);
         _Journal("[tester_bootstrap] selected=true symbol=" + selected.symbol
                  + " candidate_id=" + selected.candidate_id
                  + " candidate_hash=" + selected.candidate_hash
                  + " risk_multiplier=" + DoubleToString(selected.ai.suggested_risk_multiplier, 4)
                  + " wall_clock_wait=false live_ai_calls=false");
         _WriteShadowDecisionUpdate(selected, selected.ai, "bootstrap_rule_only_selected", "", false);
         if(_AddToWatchlist(selected)) return true;
      }
      return false;
   }

   bool _QueueCandidateGroup(TradePlan &cands[]) {
      int count = ArraySize(cands);
      if(count <= 0) return false;

      int write = 0;
      for(int i=0; i<count; i++){
         if(!_NormalizePlanPO3State(cands[i], "candidate")) continue;
         if(write != i) cands[write] = cands[i];
         write++;
      }
      if(write <= 0){
         if(count > 0) _LogSetupReject(cands[0].symbol, "po3", "candidate_group_po3_normalization_failed",
                                       "candidate_count=" + IntegerToString(count));
         _Journal("candidate group dropped: no valid plans remained after PO3 normalization");
         ArrayResize(cands, 0);
         return false;
      }
      if(write != count){
         ArrayResize(cands, write);
         count = write;
      }

      datetime group_sim_request_time = TimeCurrent();
      for(int i=0; i<count; i++){
         cands[i].candidate_index = i;
         cands[i].candidate_count = count;
         if(cands[i].setup_snapshot_time <= 0) cands[i].setup_snapshot_time = group_sim_request_time;
         cands[i].ai_request_time = group_sim_request_time;
         cands[i].ai_advisory_time = 0;
         cands[i].ai_result_age_sim_minutes = 0;
         cands[i].tester_ai_result_stale = false;
      }
      if(!_FilterOutcomePolicyBeforeAI(cands)){
         _Journal("candidate group dropped: outcome policy removed all candidates before AI");
         return false;
      }
      count = ArraySize(cands);
      for(int i=0; i<count; i++){
         cands[i].candidate_index = i;
         cands[i].candidate_count = count;
         _PrepareDecisionIdentity(cands[i]);
      }
      if(_TesterBootstrapMode())
         return _QueueTesterBootstrapCandidate(cands);
      string group_signature = _GroupSignature(cands);
      string tester_cache_signature = _TesterAiCacheSignature(cands);
      AiDecision cached_decision;
      // The frozen identity is what keeps this lookup addressing the same cohort
      // for the whole run.  Report any disagreement between the frozen value and
      // a fresh computation: it cannot change the key any more, but it is the
      // only way the condition becomes visible instead of looking like a setup
      // that simply was not recorded.
      string identity_drift_detail = "";
      if(m_ai.CheckIdentityDrift(identity_drift_detail) && m_ai.IdentityDriftEvents() <= 20){
         _Journal("[decision_identity_drift] symbol=" + cands[0].symbol + " " + identity_drift_detail);
      }
      if(InpUseAI && _TryLoadTesterAiDecision(tester_cache_signature, cached_decision)){
         if(_QueueTesterCachedDecision(cands, tester_cache_signature, cached_decision)) return true;
         _Journal(cands[0].symbol + " tester AI cache response staging failed; using normal AI path");
      }
      if(InpUseAI && MQLInfoInteger(MQL_TESTER) && _EffectiveTesterAiMode() == TESTER_AI_CACHE_ONLY){
         m_total_reject_tester_ai_cache_miss++;
         m_total_ai_cache_misses++;
         // decision_input_hash is component 4 of the signature and is shared by
         // every request in a cohort, so printing it beside the cohort value the
         // artifacts on disk were recorded with distinguishes "this setup was
         // never recorded" from "this whole run is addressing the wrong cohort".
         string miss_identity = " decision_input_hash=" + m_ai.DecisionHash()
                                + " cache_cohort_decision_hash=" + _TesterCacheCohortDecisionHash()
                                + " cohort_match=" + (_TesterCacheCohortMatches() ? "true" : "false")
                                + " cache_key=" + _TesterAiCacheKey(tester_cache_signature);
         _LogSetupReject(cands[0].symbol, "ai", "tester_ai_cache_miss",
                         "signature=" + tester_cache_signature
                         + " tester_ai_cache=" + (InpTesterAiCache ? "true" : "false")
                         + " tester_ai_mode=cache_only"
                         + miss_identity);
         _Journal("[tester_ai_mode] mode=cache_only cache_miss=true live_ai_calls=false action=reject tester_ai_cache_miss"
                  + " symbol=" + cands[0].symbol
                  + miss_identity
                  + " signature=" + tester_cache_signature);
         return false;
      }
      int cooldown_remaining = 0;
      if(InpUseAI && _IsAiCooldownActive(cands[0].symbol, group_signature, cooldown_remaining)){
         _Journal(cands[0].symbol + " AI request skipped: unchanged setup cooling down for "
                  + IntegerToString(cooldown_remaining) + "s");
         if(InpAiStrict) return false;
         _Journal(cands[0].symbol + " AI cooldown decision_quality_tier=RULE_ONLY_NON_TRADING action=reject");
         _ApplyRuleOnlyNonTradingDiagnostic(cands[0], "cooldown");
         return false;
      }
      if(!_CaptureSnapshotsForPlans(cands)){
         _RememberAiCooldown(cands[0].symbol, group_signature, "snapshot_missing");
         _LogSetupReject(cands[0].symbol, "ai", "snapshot_missing",
                         "candidate_count=" + IntegerToString(count));
         _Journal(cands[0].symbol + " AI request blocked: required snapshots unavailable");
         return false;
      }

      if(InpUseAI){
         string req_id;
         string tester_cache_key = _TesterAiCacheKey(tester_cache_signature);
         if(MQLInfoInteger(MQL_TESTER) && _EffectiveTesterAiMode() == TESTER_AI_RECORD_ONLY){
            if(_TesterRecordSignatureSeen(tester_cache_signature)){
               m_total_record_only_duplicate_signatures_skipped++;
               _Journal("[tester_ai_mode] mode=record_only duplicate_cache_signature=true"
                        + " action=skip_duplicate_export"
                        + " tester_cache_key=" + tester_cache_key
                        + " tester_cache_signature=" + tester_cache_signature);
               return false;
            }
            if(m_ai.SendRequestCandidates(cands, req_id, tester_cache_signature, tester_cache_key)){
               _RememberTesterRecordSignature(tester_cache_signature);
               m_funnel_ai_requests++;
               m_total_ai_requests_queued++;
               m_total_record_only_requests_exported++;
               _LogSetupReject(cands[0].symbol, "ai", "tester_ai_record_only",
                               "req_id=" + req_id
                               + " candidates=" + IntegerToString(count)
                               + " tester_cache_key=" + tester_cache_key
                               + " tester_cache_signature=" + tester_cache_signature);
               _Journal("[tester_ai_mode] mode=record_only request_recorded=true req_id=" + req_id
                        + " action=reject_no_trade"
                        + " tester_cache_key=" + tester_cache_key
                        + " tester_cache_signature=" + tester_cache_signature);
            } else {
               _LogSetupReject(cands[0].symbol, "ai", "ai_transport_error",
                               "record_only=true candidate_count=" + IntegerToString(count));
            }
            return false;
         }
         if(MQLInfoInteger(MQL_TESTER) && _EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG)
            _Journal("[tester_ai_mode] mode=live_wait_debug"
                     + " warning=tester_live_ai_wait_not_backtest_safe"
                     + " allow_trading=" + (InpTesterAllowLiveWaitDebugTrading ? "true" : "false")
                     + " default_action=" + (InpTesterAllowLiveWaitDebugTrading ? "trade_only_if_sim_age_safe" : "record_response_do_not_trade"));
         if(m_ai.SendRequestCandidates(cands, req_id, tester_cache_signature, tester_cache_key)){
            m_funnel_ai_requests++;
            m_total_ai_requests_queued++;
            _Journal(cands[0].symbol + " AI request queued candidates=" + IntegerToString(count)
                     + " req_id=" + req_id
                     + (MQLInfoInteger(MQL_TESTER) ? " tester_cache_key=" + tester_cache_key : ""));
            int base = ArraySize(m_pending_ai);
            ArrayResize(m_pending_ai, base + count);
            datetime sim_now = TimeCurrent();
            for(int i=0; i<count; i++){
               cands[i].req_id = req_id;
               if(cands[i].setup_snapshot_time <= 0) cands[i].setup_snapshot_time = sim_now;
               cands[i].ai_request_time = sim_now;
               cands[i].ai_advisory_time = 0;
               cands[i].ai_result_age_sim_minutes = 0;
               cands[i].tester_ai_result_stale = false;
               cands[i].ai_requested_at = TimeLocal();
               cands[i].ai_requested_wall_ms = _WallClockMs();
               m_pending_ai[base + i] = cands[i];
            }
            return true;
         }
         string send_fail_reason = (MQLInfoInteger(MQL_TESTER) ? "ai_transport_error" : "send_failed");
         _RememberAiCooldown(cands[0].symbol, group_signature, send_fail_reason);
         _LogSetupReject(cands[0].symbol, "ai", send_fail_reason,
                         "candidate_count=" + IntegerToString(count));
         _Journal(cands[0].symbol + " AI request failed to send");
         m_ai.NotifyFailureBackoff();
         if(InpAiStrict) return false;
         _Journal(cands[0].symbol + " decision_quality_tier=RULE_ONLY_NON_TRADING action=reject reason=send_failed");
      }

      _ApplyRuleOnlyNonTradingDiagnostic(cands[0], (InpUseAI ? "send_failed" : "ai_disabled"));
      return false;
   }

   void _RemovePendingGroup(const string req_id) {
      _ForgetAiWaitLog(req_id);
      for(int i=ArraySize(m_pending_ai)-1; i>=0; i--){
         if(m_pending_ai[i].req_id != req_id) continue;
         int last = ArraySize(m_pending_ai)-1;
         m_pending_ai[i] = m_pending_ai[last];
         ArrayResize(m_pending_ai, last);
      }
   }

   bool _RejectPendingGroupAsNonTrading(const string req_id, const string reason) {
      string group_symbol = "";
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(m_pending_ai[i].req_id != req_id) continue;
         if(StringLen(group_symbol) == 0) group_symbol = m_pending_ai[i].symbol;
         _ApplyRuleOnlyNonTradingDiagnostic(m_pending_ai[i], reason);
      }
      _Journal(group_symbol + " AI " + reason
               + " decision_quality_tier=RULE_ONLY_NON_TRADING action=reject_all_candidates");
      _RemovePendingGroup(req_id);
      return false;
   }

   void _UpdateNarrativeFromLive(TradePlan &p, const PO3Context &live_po3) {
      if(_SamePO3Story(p, live_po3)){
         if(StringLen(p.superseded_by) > 0 && p.narrative_state == "legacy_active")
            p.narrative_state = (p.armed ? "armed" : "staged");
         return;
      }
      string live_id = _PO3ContextId(p.symbol, live_po3);
      p.superseded_by = live_id;
      p.narrative_state = "legacy_active";
      p.invalidation_cause = "";
   }

   //+---------------------------------------------------------------+
   //| Structural break diagnostics.                                  |
   //|                                                                |
   //| The check itself is unchanged and still fails closed.  What is  |
   //| new is being able to tell WHY it fired.  Two valid AI approvals |
   //| in the eleventh run were rejected by price_broke_fvg_high after |
   //| simulated market time advanced 13 minutes while the tester      |
   //| blocked waiting for the AI.  That is an artefact of live-wait   |
   //| debug mode, not evidence about the strategy, and counting it as |
   //| strategy quality is how a healthy setup gets blamed for a       |
   //| harness limitation.  Deterministic cache replay does not have   |
   //| this problem, which is why it -- not live-wait -- is the        |
   //| authoritative historical workflow.                              |
   //+---------------------------------------------------------------+
   void _LogStructuralBreak(const TradePlan &p, const string reason, const double px) {
      datetime now = _NowServerOrLocal();
      long sim_age_sec = (p.ai_requested_at > 0 ? (long)(now - p.ai_requested_at) : -1);
      bool live_wait = (MQLInfoInteger(MQL_TESTER) && InpTesterAiMode == TESTER_AI_LIVE_WAIT_DEBUG);
      // A break that appears only after the simulated clock jumped forward
      // during a blocking AI wait is attributable to the wait, not the market.
      bool time_jump_suspect = (live_wait && sim_age_sec > 0);
      _Journal("[watchlist_structural_break] symbol=" + p.symbol
               + " reason=" + reason
               + " request_simulated_time=" + (p.ai_requested_at > 0 ? TimeToString(p.ai_requested_at, TIME_DATE|TIME_MINUTES|TIME_SECONDS) : "unset")
               + " response_simulated_time=" + TimeToString(now, TIME_DATE|TIME_MINUTES|TIME_SECONDS)
               + " simulated_age_sec=" + IntegerToString((int)sim_age_sec)
               + " assessed_entry=" + _FmtPrice(p.symbol, p.assessed_entry)
               + " live_price=" + _FmtPrice(p.symbol, px)
               + " fvg_lower=" + _FmtPrice(p.symbol, p.fvg.lower)
               + " fvg_upper=" + _FmtPrice(p.symbol, p.fvg.upper)
               + " direction=" + (p.is_buy ? "BUY" : "SELL")
               + " tester_mode=" + IntegerToString((int)InpTesterAiMode)
               + " live_wait_debug=" + (live_wait ? "true" : "false")
               + " invalidation_class=" + (time_jump_suspect
                                           ? "tester_time_jump_suspect_not_strategy_evidence"
                                           : "genuine_live_invalidation")
               + " fail_closed=true");
   }

   bool _WatchlistStillValidEx(const TradePlan &p, string &reason) {
      if(!_SymbolEligible(p.symbol)){
         reason = "symbol_not_eligible";
         return false;
      }

      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double eps = MathMax(point * 2.0, (p.fvg.upper - p.fvg.lower) * 0.20);
      double px = SymbolInfoDouble(p.symbol, p.is_buy ? SYMBOL_BID : SYMBOL_ASK);
      if(px <= 0){
         reason = "missing_live_quote";
         return false;
      }

      double target = (p.tp2 > 0 ? p.tp2 : p.po3.liquidity_target);
      double structural_low = 0.0;
      double structural_high = 0.0;
      double source_manip_low = (p.source_manip_low > 0 ? p.source_manip_low : p.po3.manip_low);
      double source_manip_high = (p.source_manip_high > 0 ? p.source_manip_high : p.po3.manip_high);
      if(p.is_buy){
         if(source_manip_low > 0 && p.po3.swing_low > 0) structural_low = MathMin(source_manip_low, p.po3.swing_low);
         else if(source_manip_low > 0) structural_low = source_manip_low;
         else structural_low = p.po3.swing_low;
      } else {
         if(source_manip_high > 0 && p.po3.swing_high > 0) structural_high = MathMax(source_manip_high, p.po3.swing_high);
         else if(source_manip_high > 0) structural_high = source_manip_high;
         else structural_high = p.po3.swing_high;
      }
      if(p.is_buy){
         if(structural_low > 0 && px < (structural_low - eps)){
            reason = "structural_invalidation_low";
            _LogStructuralBreak(p, reason, px);
            return false;
         }
         if(p.fvg.lower > 0 && px < (p.fvg.lower - eps)){
            reason = "price_broke_fvg_low";
            _LogStructuralBreak(p, reason, px);
            return false;
         }
         if(target > 0 && px >= (target - eps)){
            reason = "target_already_reached";
            _LogStructuralBreak(p, reason, px);
            return false;
         }
      } else {
         if(structural_high > 0 && px > (structural_high + eps)){
            reason = "structural_invalidation_high";
            _LogStructuralBreak(p, reason, px);
            return false;
         }
         if(p.fvg.upper > 0 && px > (p.fvg.upper + eps)){
            reason = "price_broke_fvg_high";
            _LogStructuralBreak(p, reason, px);
            return false;
         }
         if(target > 0 && px <= (target + eps)){
            reason = "target_already_reached";
            _LogStructuralBreak(p, reason, px);
            return false;
         }
      }

      PO3Context live_po3;
      if(!m_po3.Build(p.symbol, p.htf, live_po3)){
         reason = "live_po3_unavailable_preserving_frozen_story";
         _Journal(p.symbol + " " + reason + " source_story=" + _PO3StoryId(p));
         return true;
      }
      if(_OppositeConfirmedPO3(p, live_po3)){
         reason = "opposite_confirmed_po3";
         return false;
      }
      if(!_SamePO3Story(p, live_po3)){
         reason = "superseded_sweep";
         return true;
      }
      reason = "ok";
      return true;
   }

   bool _WatchlistStillValid(const TradePlan &p) {
      string reason = "";
      return _WatchlistStillValidEx(p, reason);
   }

   bool _PlanTargetAlreadyReached(const TradePlan &p, string &reason) const {
      reason = "ok";
      double target = (p.tp2 > 0 ? p.tp2 : p.po3.liquidity_target);
      if(target <= 0.0) return false;
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double risk = MathAbs(p.entry_est - p.sl);
      double eps = MathMax(point * 2.0, risk * 0.01);
      double px = SymbolInfoDouble(p.symbol, p.is_buy ? SYMBOL_BID : SYMBOL_ASK);
      if(px <= 0.0) return false;
      if(p.is_buy && px >= (target - eps)){
         reason = "target_already_reached";
         return true;
      }
      if(!p.is_buy && px <= (target + eps)){
         reason = "target_already_reached";
         return true;
      }
      return false;
   }

   bool _OppositeConfirmedPO3(const TradePlan &p, const PO3Context &live_po3) const {
      if(live_po3.bias_long == p.is_buy) return false;
      if(live_po3.sweep_running) return false;
      if(!(live_po3.has_sweep && live_po3.has_displacement && live_po3.has_bos)) return false;
      PO3State live_state = (live_po3.state != PO3_IDLE ? live_po3.state : PO3StateFromString(live_po3.po3_state));
      return (live_state == PO3_STRUCTURE_CONFIRMED ||
              live_state == PO3_FVG_CONFIRMED ||
              live_state == PO3_ENTRY_WAITING ||
              live_state == PO3_CONFIRMED);
   }

   bool _PendingBrokerStateOk(string &reason) const {
      reason = "ok";
      ENUM_ORDER_STATE state = (ENUM_ORDER_STATE)OrderGetInteger(ORDER_STATE);
      if(state == ORDER_STATE_CANCELED || state == ORDER_STATE_REJECTED || state == ORDER_STATE_EXPIRED){
         reason = "broker_order_state_" + IntegerToString((int)state);
         return false;
      }
      return true;
   }

   bool _PendingOrderStillValidEx(const TradePlan &p, string &reason) {
      reason = "ok";
      if(!_SymbolEligible(p.symbol)){
         reason = "symbol_not_eligible";
         return false;
      }

      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double eps = MathMax(point * 2.0, MathAbs(p.entry_est - p.sl) * 0.02);
      double px = SymbolInfoDouble(p.symbol, p.is_buy ? SYMBOL_BID : SYMBOL_ASK);
      if(px <= 0){
         reason = "missing_live_quote";
         return false;
      }

      if(p.is_buy){
         double structural_low = 0.0;
         double source_manip_low = (p.source_manip_low > 0 ? p.source_manip_low : p.po3.manip_low);
         if(source_manip_low > 0 && p.po3.swing_low > 0) structural_low = MathMin(source_manip_low, p.po3.swing_low);
         else if(source_manip_low > 0) structural_low = source_manip_low;
         else structural_low = p.po3.swing_low;
         if(structural_low > 0 && px < (structural_low - eps)){
            reason = "structural_invalidation_low";
            return false;
         }
         double target = (p.tp2 > 0 ? p.tp2 : p.po3.liquidity_target);
         if(target > 0 && px >= (target - eps)){
            reason = "target_already_reached";
            return false;
         }
      } else {
         double structural_high = 0.0;
         double source_manip_high = (p.source_manip_high > 0 ? p.source_manip_high : p.po3.manip_high);
         if(source_manip_high > 0 && p.po3.swing_high > 0) structural_high = MathMax(source_manip_high, p.po3.swing_high);
         else if(source_manip_high > 0) structural_high = source_manip_high;
         else structural_high = p.po3.swing_high;
         if(structural_high > 0 && px > (structural_high + eps)){
            reason = "structural_invalidation_high";
            return false;
         }
         double target = (p.tp2 > 0 ? p.tp2 : p.po3.liquidity_target);
         if(target > 0 && px <= (target + eps)){
            reason = "target_already_reached";
            return false;
         }
      }

      PO3Context live_po3;
      if(m_po3.Build(p.symbol, p.htf, live_po3)){
         if(_OppositeConfirmedPO3(p, live_po3)){
            reason = "opposite_confirmed_po3";
            return false;
         }
      }

      return true;
   }

   void _UpdatePendingWaitBars(TradePlan &p) {
      datetime last_bar_time = _LastClosedBarTime(p.symbol, p.confirm_tf);
      if(last_bar_time <= 0) return;
      if(p.last_confirm_bar_time <= 0){
         p.last_confirm_bar_time = last_bar_time;
         return;
      }
      if(last_bar_time == p.last_confirm_bar_time) return;
      p.last_confirm_bar_time = last_bar_time;
      p.bars_waited++;
   }

   bool _TryPendingEntryZoneMarket(TradePlan &meta, const ulong ticket) {
      double live_entry = 0.0, tolerance = 0.0;
      if(!_EntryZoneTouched(meta, live_entry, tolerance)) return false;

      TradePlan live = meta;
      live.armed = true;
      if(live.setup_score <= 0.0) live.setup_score = _SetupScore(live);
      _PopulateDerivedPlanFields(live);
      string rule_reason = "";
      if(!_DeterministicExecutionGate(live, rule_reason)){
         _Journal(meta.symbol + " pending entry-zone market held ticket=" + IntegerToString((int)ticket)
                  + " reason=" + rule_reason
                  + " planned=" + _FmtPrice(meta.symbol, meta.entry_est)
                  + " live=" + _FmtPrice(meta.symbol, live_entry)
                  + " tolerance=" + _FmtPrice(meta.symbol, tolerance));
         return false;
      }

      _Journal(meta.symbol + " pending entry-zone market conversion ticket=" + IntegerToString((int)ticket)
               + " planned=" + _FmtPrice(meta.symbol, meta.entry_est)
               + " live=" + _FmtPrice(meta.symbol, live_entry)
               + " tolerance=" + _FmtPrice(meta.symbol, tolerance));
      if(!_PlaceMarket(live, true, true)) return false;

      if(m_trade.OrderDelete(ticket)){
         _TrackPendingOrderDelete("converted_to_market");
      } else {
         _Journal(meta.symbol + " pending order delete after market conversion failed ticket="
                  + IntegerToString((int)ticket) + " retcode=" + _TradeRetcodeText());
      }
      return true;
   }

   bool _PendingRelaxedEntryPrice(const TradePlan &p, const double old_entry,
                                  double &relaxed_entry, string &reason) const {
      relaxed_entry = old_entry;
      reason = "ok";
      if(InpPendingEntryRelaxAfterBars <= 0){
         reason = "pending_relax_disabled";
         return false;
      }
      if(p.fvg.upper <= p.fvg.lower){
         reason = "invalid_fvg";
         return false;
      }
      double edge = (p.is_buy ? p.fvg.upper : p.fvg.lower);
      if(edge <= 0){
         reason = "invalid_fvg_edge";
         return false;
      }
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      relaxed_entry = edge;
      if(p.is_buy && relaxed_entry <= old_entry + point){
         reason = "already_at_fvg_edge";
         return false;
      }
      if(!p.is_buy && relaxed_entry >= old_entry - point){
         reason = "already_at_fvg_edge";
         return false;
      }
      return true;
   }

   bool _TryRelaxPendingEntry(TradePlan &meta, const ulong ticket, const datetime expiry) {
      if(InpPendingEntryRelaxAfterBars <= 0) return false;
      if(meta.bars_waited < InpPendingEntryRelaxAfterBars) return false;

      double old_entry = OrderGetDouble(ORDER_PRICE_OPEN);
      if(old_entry <= 0) old_entry = (meta.planned_entry > 0 ? meta.planned_entry : meta.entry_est);
      double relaxed_entry = old_entry;
      string relax_reason = "";
      if(!_PendingRelaxedEntryPrice(meta, old_entry, relaxed_entry, relax_reason)) return false;

      double market_ref = _LiveEntryPrice(meta);
      double broker_buffer = _BrokerBufferPrice(meta.symbol);
      if(market_ref <= 0){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=missing_live_quote");
         return false;
      }
      if(MathAbs(market_ref - relaxed_entry) < broker_buffer){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=broker_buffer");
         return false;
      }

      bool using_stored_target = _HasStoredTargetArbitration(meta);
      bool should_have_stored_target = (StringLen(meta.ai_chosen_target_model) > 0 ||
                                        StringLen(meta.ai.chosen_target_model) > 0 ||
                                        StringLen(meta.ai_decision_id) > 0);
      if(should_have_stored_target && !using_stored_target){
         string terminal_reason = "missing_stored_target_arbitration_for_pending_relax";
         meta.narrative_state = "invalidated";
         meta.invalidation_cause = terminal_reason;
         _WriteTradeMeta(meta);
         _Journal("[pending_relax] terminal_invalid=true reason=" + terminal_reason
                  + " ticket=" + IntegerToString((int)ticket)
                  + " old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry));
         if(m_trade.OrderDelete(ticket))
            _TrackPendingOrderDelete(terminal_reason, false);
         return false;
      }

      TradePlan relaxed = meta;
      relaxed.entry_est = relaxed_entry;
      double old_tp = meta.tp2;
      string price_reason = "";
      if(!_BuildPlanPrices(relaxed, relaxed_entry, price_reason)){
         if(price_reason == "invalid_ai_target_arbitration_response")
            m_funnel_pending_relax_invalid_ai_target_arbitration_response++;
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=" + price_reason);
         if(IsStructuralPlanRebuildFailure(price_reason)){
            meta.narrative_state = "invalidated";
            meta.invalidation_cause = price_reason;
            _WriteTradeMeta(meta);
            _Journal("[pending_relax] terminal_invalid=true reason=" + price_reason
                     + " ticket=" + IntegerToString((int)ticket)
                     + " old_entry=" + _FmtPrice(meta.symbol, old_entry)
                     + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry));
            if(m_trade.OrderDelete(ticket))
               _TrackPendingOrderDelete(price_reason, false);
         }
         return false;
      }
      if(using_stored_target){
         m_funnel_pending_relax_using_stored_target_arbitration++;
         _Journal("[pending_relax] using_stored_target_arbitration=true"
                  + " chosen=" + relaxed.target_model
                  + " old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " new_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " old_tp=" + _FmtPrice(meta.symbol, old_tp)
                  + " new_tp=" + _FmtPrice(meta.symbol, relaxed.tp2)
                  + " rr2=" + DoubleToString(_ExecutionRR2(relaxed), 6));
      }
      _PopulateDerivedPlanFields(relaxed);
      relaxed.setup_score = _SetupScore(relaxed);
      string rule_reason = "";
      if(!_DeterministicExecutionGate(relaxed, rule_reason)){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=" + rule_reason);
         return false;
      }
      string target_validation_reason = "";
      string target_validation_detail = "";
      if(!ValidateAiChosenTargetBeforeWatchlist(relaxed, target_validation_reason, target_validation_detail)){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=" + target_validation_reason
                  + " detail=" + target_validation_detail);
         return false;
      }
      if(!_StopsDistanceOk(meta.symbol, meta.is_buy, relaxed_entry, relaxed.sl, relaxed.tp2)){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=broker_distance_invalid");
         return false;
      }
      string hard_safety_reason = "";
      if(!CanPlaceOrderHardSafety(relaxed, hard_safety_reason)){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=final_hard_safety_" + hard_safety_reason);
         return false;
      }
      string relax_session_reason = "";
      if(!_ApplyBrokerSessionEntryGate(relaxed, relax_session_reason)){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=broker_session_gate_" + relax_session_reason);
         return false;
      }
      double pending_volume = OrderGetDouble(ORDER_VOLUME_CURRENT);
      double old_pending_risk = _RiskMoneyForPosition(meta.symbol, meta.is_buy, pending_volume,
                                                      old_entry, OrderGetDouble(ORDER_SL));
      double new_pending_risk = _RiskMoneyForPosition(meta.symbol, meta.is_buy, pending_volume,
                                                      relaxed_entry, relaxed.sl);
      if(new_pending_risk <= 0.0){
         _Journal(meta.symbol + " pending entry relax skipped reason=modified_original_risk_unavailable");
         return false;
      }
      double incremental_risk = MathMax(0.0, new_pending_risk - MathMax(0.0, old_pending_risk));
      if(incremental_risk > 0.0000001){
         string relax_portfolio_reason = "";
         if(!_ApplyFinalPortfolioRiskGovernance(relaxed, incremental_risk, relax_portfolio_reason)){
            _Journal(meta.symbol + " pending entry relax skipped reason=portfolio_initial_risk_gate_" + relax_portfolio_reason);
            return false;
         }
      }
      relaxed.executed_candidate_hash = relaxed.candidate_hash;
      string relax_fingerprint_changes = "";
      if(!_ExecutionFingerprintWithinTolerance(relaxed, relax_fingerprint_changes)){
         _Journal("[execution_fingerprint] match=false stage=pending_relax changed_components=" + relax_fingerprint_changes
                  + " assessed=" + relaxed.assessed_execution_fingerprint
                  + " final=" + relaxed.final_execution_fingerprint);
         return false;
      }

      ENUM_ORDER_TYPE_TIME type_time = (expiry > 0 ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC);
      if(!m_trade.OrderModify(ticket, relaxed_entry, relaxed.sl, relaxed.tp2, type_time, expiry)){
         _Journal(meta.symbol + " pending entry relax modify failed old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=" + _TradeRetcodeText());
         return false;
      }

      relaxed.planned_entry = relaxed_entry;
      relaxed.planned_sl = relaxed.sl;
      relaxed.planned_tp1 = relaxed.tp1;
      relaxed.planned_tp2 = relaxed.tp2;
      _CaptureEntryRiskContext(relaxed, pending_volume, relaxed_entry, relaxed.sl);
      relaxed.narrative_state = "pending_order";
      relaxed.bars_waited = meta.bars_waited;
      relaxed.last_confirm_bar_time = meta.last_confirm_bar_time;
      meta = relaxed;
      _WriteTradeMeta(meta, ticket);
      _Journal(meta.symbol + " pending entry relaxed old_entry=" + _FmtPrice(meta.symbol, old_entry)
               + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
               + " reason=pending_waited_" + IntegerToString(meta.bars_waited) + "_bars");
      return true;
   }

   string _PendingExpiryMissClassification(const TradePlan &meta) const {
      double entry = (meta.planned_entry > 0.0 ? meta.planned_entry : meta.entry_est);
      double sl = (meta.planned_sl > 0.0 ? meta.planned_sl : meta.sl);
      double tp = (meta.planned_tp2 > 0.0 ? meta.planned_tp2 : meta.tp2);
      double risk = MathAbs(entry - sl);
      if(entry <= 0.0 || risk <= 0.0 || tp <= 0.0) return "pending_expired_unclassified";

      double ref = SymbolInfoDouble(meta.symbol, meta.is_buy ? SYMBOL_BID : SYMBOL_ASK);
      if(ref <= 0.0) return "pending_expired_unclassified";

      double toward_target = (meta.is_buy ? (ref - entry) : (entry - ref));
      double toward_stop = (meta.is_buy ? (entry - ref) : (ref - entry));
      double distance_to_entry = MathAbs(ref - entry);

      if(toward_target >= risk * 0.35) return "entry_too_deep";
      if(toward_stop >= risk * 0.35) return "weak_context_or_entry_too_shallow";
      if(distance_to_entry >= risk * 0.55) return "no_retrace";
      return "expired_near_entry";
   }

   bool _ParseTradeMetaJson(const string txt, TradePlan &p) {
      ZeroMemory(p);
      return m_state.ParseTradePlanJson(txt, p);
   }

   //--- Trade-meta memo -------------------------------------------------------
   // One entry per bus path, holding the exact text last written to or read from
   // that path.  Two pure-function memos ride on it:
   //
   //   write : serializing the same plan twice yields the same characters, and
   //           writing the same characters over a file that already holds them is
   //           a no-op on disk.  Skipping it is invisible to every consumer.
   //   parse : ParseTradePlanJson() is deterministic, so a parse of bytes we have
   //           already parsed must produce the same plan.  Returning the stored
   //           plan is the same value, not a cheaper approximation of it.
   //
   // Neither memo may decide anything.  m_tm_parsed is cleared on every real write
   // so a plan is only ever served from a genuine parse of the bytes now on disk,
   // and the write path re-checks that the file still exists, so an external delete
   // is repaired by the next write rather than papered over.
   string    m_tm_path[];
   string    m_tm_json[];
   TradePlan m_tm_plan[];
   bool      m_tm_parsed[];
   datetime  m_tm_written_at[];
   int       m_tm_next;
   long      m_tm_writes;
   long      m_tm_writes_skipped_identical;
   long      m_tm_parses;
   long      m_tm_parses_skipped_identical;
   long      m_tm_write_us;
   long      m_tm_parse_us;
   long      m_tm_serialize_us;
   long      m_tm_serializes;
   long      m_mp_calls;
   long      m_mp_us;
   long      m_mp_load_us;
   long      m_mp_meta_us;
   long      m_mp_penalty_us;
   long      m_mp_tail_us;
   long      m_tm_miss_content;
   long      m_tm_miss_watermark;
   long      m_tm_miss_absent;
   long      m_tm_miss_new_path;
   int       m_tm_diff_reports;

   int _TradeMetaMemoFind(const string path) {
      for(int i=0; i<ArraySize(m_tm_path); i++)
         if(m_tm_path[i] == path) return i;
      return -1;
   }

   // Replace a top-level numeric value with 0 in place.  One StringFind and one
   // splice -- no parse, no allocation per field.
   void _BlankJsonNumber(string &j, const string key) const {
      string needle = "\"" + key + "\":";
      int at = StringFind(j, needle);
      if(at < 0) return;
      int start = at + StringLen(needle);
      int len = (int)StringLen(j);
      int end = start;
      while(end < len){
         ushort c = (ushort)StringGetCharacter(j, end);
         if((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E'){ end++; continue; }
         break;
      }
      if(end <= start) return;
      j = StringSubstr(j, 0, start) + "0" + StringSubstr(j, end);
   }

   // The document with the tick watermark blanked.  PenaltyWatcher stamps
   // latest_observed_tick_time / _msc on EVERY tick and _ApplyPenaltyStateToMeta
   // copies them into the trade meta, so the serialized document is different on
   // every simulated second even when nothing about the trade has changed.  Comparing
   // on the blanked form is what lets the memo coalesce; the watermark still reaches
   // disk, both on the next material change and on the interval below.
   string _TradeMetaComparisonKey(const string j) const {
      string key = j;
      _BlankJsonNumber(key, "latest_observed_tick_time");
      _BlankJsonNumber(key, "latest_observed_tick_msc");
      return key;
   }

   // The watermark's source of truth is PenaltyState, which _PersistPenaltyStates()
   // already writes every 15 simulated seconds.  Bounding the trade-meta copy to the
   // same window keeps the mirror no staler than the field it mirrors, and turns
   // ~10 alias writes per simulated second into ~10 per interval.
   bool _TradeMetaWatermarkDue(const int idx) const {
      if(InpTradeMetaWatermarkSeconds <= 0) return true;
      datetime now = _NowServerOrLocal();
      datetime written = m_tm_written_at[idx];
      if(written <= 0 || now < written) return true;
      return ((now - written) >= (datetime)InpTradeMetaWatermarkSeconds);
   }

   // Existing slot, else a fresh one, else the round-robin victim.  Eviction only
   // costs a rewrite or a reparse; it can never produce a wrong answer.
   int _TradeMetaMemoSlot(const string path) {
      int idx = _TradeMetaMemoFind(path);
      if(idx >= 0) return idx;
      int size = ArraySize(m_tm_path);
      if(size < InpTradeMetaMemoEntries){
         if(ArrayResize(m_tm_path, size+1) != size+1) return -1;
         ArrayResize(m_tm_json, size+1);
         ArrayResize(m_tm_plan, size+1);
         ArrayResize(m_tm_parsed, size+1);
         ArrayResize(m_tm_written_at, size+1);
         return size;
      }
      if(size <= 0) return -1;
      int victim = m_tm_next % size;
      m_tm_next = (victim + 1) % size;
      return victim;
   }

   void _TradeMetaMemoStore(const string path, const string comparison_key) {
      int slot = _TradeMetaMemoSlot(path);
      if(slot < 0) return;
      m_tm_path[slot]       = path;
      m_tm_json[slot]       = comparison_key;
      m_tm_parsed[slot]     = false;
      m_tm_written_at[slot] = _NowServerOrLocal();
   }

   // First character at which two documents differ, or -1 when they are identical.
   int _FirstDifferenceAt(const string a, const string b) const {
      int la = (int)StringLen(a);
      int lb = (int)StringLen(b);
      int n = (la < lb ? la : lb);
      for(int i=0; i<n; i++)
         if(StringGetCharacter(a, i) != StringGetCharacter(b, i)) return i;
      return (la == lb ? -1 : n);
   }

   // Why a write could not be skipped.  "The memo stopped working" is not a
   // diagnosis, and a hit-rate that silently collapses turns a fix into a
   // regression that nothing reports.  The excerpt names the field that moved,
   // bounded so a permanently unstable document cannot flood the journal.
   void _ReportTradeMetaContentMiss(const string path, const string old_key, const string new_key) {
      m_tm_miss_content++;
      // Early samples catch the opening transitions, which are legitimate and change
      // several fields at once; the periodic samples are the ones that show whether the
      // document is still moving once the position has settled.  Without the second
      // kind the first five reports are spent on the fill and say nothing about steady
      // state -- which is exactly how the first attempt at this measurement was wasted.
      bool due = (m_tm_miss_content <= 5 || (m_tm_miss_content % 997) == 0);
      if(!due || m_tm_diff_reports >= 30) return;
      m_tm_diff_reports++;
      int at = _FirstDifferenceAt(old_key, new_key);
      int from = (at > 80 ? at - 80 : 0);
      _Journal("[trade_meta_diff] path=" + path
               + " first_diff_at=" + IntegerToString(at)
               + " old_len=" + IntegerToString((int)StringLen(old_key))
               + " new_len=" + IntegerToString((int)StringLen(new_key))
               + " old=[" + StringSubstr(old_key, from, 200) + "]"
               + " new=[" + StringSubstr(new_key, from, 200) + "]");
   }

   // comparison_key is _TradeMetaComparisonKey(json).  It is computed once by the
   // caller and handed down because every alias receives the identical document, and
   // deriving it here rebuilt the same ~76,000 character string ten times per pass for
   // no possible difference in the answer.
   void _WriteTradeMetaFile(const string path, const string json, const string comparison_key) {
      if(InpTradeMetaMemoEntries > 0){
         // comparison_key is used directly rather than copied into a local: it is the
         // blanked ~76,000 character document, and a local copy per alias is the same
         // waste the caller-side derivation just removed.
         int idx = _TradeMetaMemoFind(path);
         if(idx < 0) m_tm_miss_new_path++;
         else if(m_tm_json[idx] != comparison_key)
            _ReportTradeMetaContentMiss(path, m_tm_json[idx], comparison_key);
         else if(_TradeMetaWatermarkDue(idx)) m_tm_miss_watermark++;
         else if(!m_bus.Exists(path)) m_tm_miss_absent++;
         if(idx >= 0 && m_tm_json[idx] == comparison_key &&
            !_TradeMetaWatermarkDue(idx) && m_bus.Exists(path)){
            m_tm_writes_skipped_identical++;
            return;
         }
         ulong t0 = GetMicrosecondCount();
         m_bus.WriteText(path, json);
         m_tm_write_us += (long)(GetMicrosecondCount() - t0);
         m_tm_writes++;
         _TradeMetaMemoStore(path, comparison_key);
         return;
      }
      ulong t1 = GetMicrosecondCount();
      m_bus.WriteText(path, json);
      m_tm_write_us += (long)(GetMicrosecondCount() - t1);
      m_tm_writes++;
   }

   bool _ReadTradeMetaPath(const string path, TradePlan &p){
      string txt;
      if(!m_bus.ReadText(path, txt)) return false;
      string key = "";
      if(InpTradeMetaMemoEntries > 0){
         key = _TradeMetaComparisonKey(txt);
         int idx = _TradeMetaMemoFind(path);
         if(idx >= 0 && m_tm_parsed[idx] && m_tm_json[idx] == key){
            p = m_tm_plan[idx];
            // The stored plan was parsed from a document whose watermark may be an
            // older tick, so read the two blanked fields back from the bytes actually
            // on disk.  What the caller receives is then exactly what a full reparse
            // would have produced -- the memo returns the same value, not a cheaper
            // approximation of it.
            p.latest_observed_tick_time = (datetime)JsonGetNumber(txt, "latest_observed_tick_time", 0);
            p.latest_observed_tick_msc  = (long)JsonGetNumber(txt, "latest_observed_tick_msc", 0);
            m_tm_parses_skipped_identical++;
            return true;
         }
      }
      ulong t0 = GetMicrosecondCount();
      bool ok = _ParseTradeMetaJson(txt, p);
      m_tm_parse_us += (long)(GetMicrosecondCount() - t0);
      m_tm_parses++;
      if(ok && InpTradeMetaMemoEntries > 0){
         int slot = _TradeMetaMemoSlot(path);
         if(slot >= 0){
            m_tm_path[slot]   = path;
            m_tm_json[slot]   = key;
            m_tm_plan[slot]   = p;
            m_tm_parsed[slot] = true;
            // A read does not make the file any fresher than the last real write did.
            if(m_tm_written_at[slot] <= 0) m_tm_written_at[slot] = _NowServerOrLocal();
         }
      }
      return ok;
   }

   void _WriteTradeMeta(const TradePlan &p, const ulong ticket=0) {
      TradePlan meta = p;
      _LoadActivePolicyIfNeeded();
      _InitializeNarrativeFields(meta);
      _ApplySetupManagementProfile(meta);
      _PopulateDerivedPlanFields(meta);
      if(StringLen(meta.policy_snapshot_id) == 0 && StringLen(m_active_policy.policy_id) > 0)
         meta.policy_snapshot_id = m_active_policy.policy_id;
      meta.trade_key = _MakeTradeKey(meta);
      if(meta.created_at <= 0) meta.created_at = _NowServerOrLocal();
      if(meta.planned_entry <= 0) meta.planned_entry = meta.entry_est;
      if(meta.planned_sl <= 0) meta.planned_sl = meta.sl;
      if(meta.planned_tp1 <= 0) meta.planned_tp1 = meta.tp1;
      if(meta.planned_tp2 <= 0) meta.planned_tp2 = meta.tp2;
      if(meta.planned_at <= 0) meta.planned_at = (meta.created_at > 0 ? meta.created_at : _NowServerOrLocal());
      if(StringLen(meta.ai_decision_source) == 0) meta.ai_decision_source = "unavailable_non_trading";
      // Timed separately from the write: the memo can remove the write but the
      // document still has to be built to know what the write would have said, so
      // serialization is the floor cost of calling this function at all.
      ulong ser0 = GetMicrosecondCount();
      string j = m_state.TradePlanToJson(meta);
      m_tm_serialize_us += (long)(GetMicrosecondCount() - ser0);
      m_tm_serializes++;
      // Every alias below receives the same characters, so each one is memoized
      // independently: the alias set is unchanged, only the redundant rewrites go.
      // The comparison key is derived once here for the same reason -- it is a pure
      // function of j, and every alias would otherwise recompute it identically.
      string comparison_key = (InpTradeMetaMemoEntries > 0 ? _TradeMetaComparisonKey(j) : "");
      _WriteTradeMetaFile(_TradeKeyPath(meta.trade_key), j, comparison_key);
      if(StringLen(meta.broker_comment) > 0 && meta.broker_comment != meta.trade_key)
         _WriteTradeMetaFile(_TradeKeyPath(meta.broker_comment), j, comparison_key);
      if(ticket > 0) _WriteTradeMetaFile(_TradeTicketPath(ticket), j, comparison_key);
      if(meta.result_order_ticket > 0){
         _WriteTradeMetaFile(_TradeOrderPath(meta.result_order_ticket), j, comparison_key);
         _WriteTradeMetaFile(_TradeTicketPath(meta.result_order_ticket), j, comparison_key);
      }
      if(meta.result_deal_ticket > 0)
         _WriteTradeMetaFile(_TradeDealPath(meta.result_deal_ticket), j, comparison_key);
      if(meta.broker_position_ticket > 0){
         _WriteTradeMetaFile(_TradePositionPath(meta.broker_position_ticket), j, comparison_key);
         _WriteTradeMetaFile(_TradeTicketPath(meta.broker_position_ticket), j, comparison_key);
      }
      if(meta.broker_position_identifier > 0)
         _WriteTradeMetaFile(_TradePositionIdentifierPath(meta.broker_position_identifier), j, comparison_key);
      if(meta.position_id > 0)
         _WriteTradeMetaFile(_TradePositionIdentifierPath(meta.position_id), j, comparison_key);
   }

   bool _TradeMetaIdentityMatches(const TradePlan &p,
                                  const ulong ticket,
                                  const string symbol,
                                  const string comment) const {
      if(StringLen(symbol) > 0 && p.symbol != symbol) return false;
      if(StringLen(comment) > 0 && p.broker_comment != comment && p.trade_key != comment) return false;
      if(ticket <= 0) return false;
      return (p.result_order_ticket == ticket ||
              p.result_deal_ticket == ticket ||
              p.broker_position_ticket == ticket ||
              p.broker_position_identifier == (long)ticket ||
              p.position_id == (long)ticket);
   }

   bool _LoadTradeMeta(const ulong ticket, const string symbol, const string comment, TradePlan &p){
      if(ticket > 0 && _ReadTradeMetaPath(_TradePositionPath(ticket), p) &&
         _TradeMetaIdentityMatches(p, ticket, symbol, comment)) return true;
      if(ticket > 0 && _ReadTradeMetaPath(_TradeOrderPath(ticket), p) &&
         _TradeMetaIdentityMatches(p, ticket, symbol, comment)) return true;
      if(ticket > 0 && _ReadTradeMetaPath(_TradeDealPath(ticket), p) &&
         _TradeMetaIdentityMatches(p, ticket, symbol, comment)) return true;
      if(ticket > 0 && _ReadTradeMetaPath(_TradePositionIdentifierPath((long)ticket), p) &&
         _TradeMetaIdentityMatches(p, ticket, symbol, comment)) return true;
      if(ticket > 0 && _ReadTradeMetaPath(_TradeTicketPath(ticket), p) &&
         _TradeMetaIdentityMatches(p, ticket, symbol, comment)) return true;
      if(StringLen(comment) > 0 && _ReadTradeMetaPath(_TradeKeyPath(comment), p)){
         if(!_TradeMetaIdentityMatches(p, ticket, symbol, comment)) return false;
         _WriteTradeMeta(p, ticket);
         return true;
      }
      return false;
   }

   ulong _FindPositionTicketByIdentifier(const long position_identifier) {
      if(position_identifier <= 0) return 0;
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0 || !PositionMatchesMagic(ticket)) continue;
         if((long)PositionGetInteger(POSITION_IDENTIFIER) == position_identifier) return ticket;
      }
      return 0;
   }

   void _QuarantineExecutionIdentity(TradePlan &meta,
                                     const string reason,
                                     const ulong order_ticket,
                                     const ulong deal_ticket) {
      meta.result_order_ticket = order_ticket;
      meta.result_deal_ticket = deal_ticket;
      meta.execution_identity_verified = false;
      meta.execution_identity_quarantined = true;
      meta.execution_identity_reason = reason;
      meta.account_position_mode = m_account_position_mode;
      meta.attribution_status = "QUARANTINED";
      meta.attribution_error = reason;
      meta.learning_eligible = false;
      meta.optimization_eligible = false;
      meta.suppression_eligible = false;
      meta.ledger_integrity_status = "QUARANTINED";
      meta.ledger_integrity_reasons = "[\"" + JsonEscape(reason) + "\"]";
      meta.ledger_schema_version = TRADE_LEDGER_SCHEMA_VERSION;
      FolderCreate(_ExecutionIdentityQuarantineDir(), FILE_COMMON);
      string leaf = "identity_" + IntegerToString((long)(order_ticket > 0 ? order_ticket : deal_ticket))
                    + "_" + IntegerToString((long)_NowServerOrLocal()) + ".json";
      string row = "{";
      row += JsonKVInt("created_at", (int)_NowServerOrLocal()) + ",";
      row += JsonKVStr("reason", reason) + ",";
      row += JsonKVStr("account_position_mode", m_account_position_mode) + ",";
      row += JsonKVStr("symbol", meta.symbol) + ",";
      row += JsonKVStr("trade_key", meta.trade_key) + ",";
      row += JsonKVStr("broker_comment", meta.broker_comment) + ",";
      row += JsonKVStr("candidate_id", meta.candidate_id) + ",";
      row += JsonKVStr("candidate_hash", meta.candidate_hash) + ",";
      row += JsonKVStr("execution_fingerprint", meta.final_execution_fingerprint) + ",";
      row += JsonKVNum("order_ticket", (double)order_ticket, 0) + ",";
      row += JsonKVNum("deal_ticket", (double)deal_ticket, 0);
      row += "}";
      m_bus.WriteText(_ExecutionIdentityQuarantineDir() + "\\" + leaf, row);
      _WriteTradeMeta(meta, order_ticket);
      _Journal("[execution_identity_quarantine] reason=" + reason
               + " order_ticket=" + IntegerToString((long)order_ticket)
               + " deal_ticket=" + IntegerToString((long)deal_ticket)
               + " candidate_hash=" + meta.candidate_hash);
   }

   //+---------------------------------------------------------------+
   //| Which identity failures can only mean "the terminal has not     |
   //| finished registering this execution yet".                       |
   //|                                                                 |
   //| _ResolveExactExecutionIdentity is called twice for a market     |
   //| order: once the instant OrderSend returns, and again when the   |
   //| entry deal reaches OnTradeTransaction.  On 2026-09-06 BOTH of   |
   //| the run's two trades failed the first call on the position's    |
   //| open time and passed the second 163 ms and 255 ms later --      |
   //| #Japan225 order 2 and GBPJPY order 7, each ending                |
   //| POSITION_FILLED_IDENTITY_VERIFIED.  The first call had          |
   //| nevertheless written a quarantine artifact and driven the trade |
   //| lineage through broker_accepted_identity_quarantined, so the    |
   //| audit trail recorded a contradiction for two executions that    |
   //| were, in the end, exactly what they claimed to be.              |
   //|                                                                 |
   //| These reasons are transient by construction: they say a record  |
   //| is absent or not yet mutually consistent.  Everything else -- a |
   //| magic, symbol, comment, direction or volume that does not match |
   //| -- is a real contradiction and is still quarantined at once.     |
   //| Deferring is not accepting: the trade stays unverified, stays   |
   //| non-attributable and stays out of learning until the            |
   //| authoritative fill-time call verifies it or quarantines it.      |
   //+---------------------------------------------------------------+
   bool _ExecutionIdentityFailureIsSettlementPending(const string reason) const {
      if(StringLen(reason) == 0) return false;
      if(StringFind(reason, "result_deal_not_in_history") == 0) return true;
      if(StringFind(reason, "result_order_not_in_history") == 0) return true;
      if(StringFind(reason, "missing_deal_position_id") == 0) return true;
      if(StringFind(reason, "exact_position_not_found_by_deal_position_id") == 0) return true;
      if(StringFind(reason, "position_open_time_unavailable") == 0) return true;
      if(StringFind(reason, "position_open_time_mismatch") == 0) return true;
      return false;
   }

   bool _ResolveExactExecutionIdentity(TradePlan &meta,
                                       const ulong order_ticket,
                                       const ulong deal_ticket,
                                       const double requested_volume,
                                       string &reason,
                                       const double accepted_deal_volume=0.0) {
      reason = "";
      meta.result_order_ticket = order_ticket;
      meta.result_deal_ticket = deal_ticket;
      meta.account_position_mode = m_account_position_mode;
      if(order_ticket == 0){ reason = "missing_result_order_ticket"; return false; }
      if(deal_ticket == 0){ reason = "missing_result_deal_ticket"; return false; }
      if(StringLen(meta.trade_key) == 0 || StringLen(meta.candidate_id) == 0 ||
         StringLen(meta.candidate_hash) == 0 || StringLen(meta.final_execution_fingerprint) == 0){
         reason = "missing_trade_candidate_or_fingerprint_identity";
         return false;
      }

      if(!HistoryDealSelect(deal_ticket)){
         datetime now = _NowServerOrLocal();
         HistorySelect(now - 86400, now + 60);
      }
      if(!HistoryDealSelect(deal_ticket)){ reason = "result_deal_not_in_history"; return false; }
      if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_ORDER) != order_ticket){
         reason = "deal_order_ticket_mismatch";
         return false;
      }
      if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber){
         reason = "deal_magic_mismatch";
         return false;
      }
      if(HistoryDealGetString(deal_ticket, DEAL_SYMBOL) != meta.symbol){
         reason = "deal_symbol_mismatch";
         return false;
      }
      ENUM_DEAL_ENTRY deal_entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
      if(deal_entry != DEAL_ENTRY_IN && deal_entry != DEAL_ENTRY_INOUT){
         reason = "deal_is_not_entry";
         return false;
      }
      ENUM_DEAL_TYPE deal_type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
      if((meta.is_buy && deal_type != DEAL_TYPE_BUY) || (!meta.is_buy && deal_type != DEAL_TYPE_SELL)){
         reason = "deal_direction_mismatch";
         return false;
      }
      double volume_step = SymbolInfoDouble(meta.symbol, SYMBOL_VOLUME_STEP);
      double volume_tolerance = MathMax(InpLedgerVolumeReconciliationTolerance, volume_step * 0.51);
      double deal_volume = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
      double expected_deal_volume = (accepted_deal_volume > 0.0 ? accepted_deal_volume : requested_volume);
      if(requested_volume <= 0.0 || expected_deal_volume <= 0.0 ||
         MathAbs(deal_volume - expected_deal_volume) > volume_tolerance){
         reason = "deal_volume_mismatch";
         return false;
      }

      if(!HistoryOrderSelect(order_ticket)){ reason = "result_order_not_in_history"; return false; }
      if((ulong)HistoryOrderGetInteger(order_ticket, ORDER_MAGIC) != InpMagicNumber){
         reason = "order_magic_mismatch";
         return false;
      }
      if(HistoryOrderGetString(order_ticket, ORDER_SYMBOL) != meta.symbol){
         reason = "order_symbol_mismatch";
         return false;
      }
      string order_comment = HistoryOrderGetString(order_ticket, ORDER_COMMENT);
      if(StringLen(meta.broker_comment) == 0 || order_comment != meta.broker_comment){
         reason = "order_comment_mismatch";
         return false;
      }
      double order_volume = HistoryOrderGetDouble(order_ticket, ORDER_VOLUME_INITIAL);
      if(MathAbs(order_volume - requested_volume) > volume_tolerance){
         reason = "order_volume_mismatch";
         return false;
      }

      long position_identifier = (long)HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
      if(position_identifier <= 0){ reason = "missing_deal_position_id"; return false; }
      ulong position_ticket = _FindPositionTicketByIdentifier(position_identifier);
      if(position_ticket == 0 || !PositionSelectByTicket(position_ticket)){
         reason = "exact_position_not_found_by_deal_position_id";
         return false;
      }
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber){
         reason = "position_magic_mismatch";
         return false;
      }
      if(PositionGetString(POSITION_SYMBOL) != meta.symbol){
         reason = "position_symbol_mismatch";
         return false;
      }
      ENUM_POSITION_TYPE position_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if((meta.is_buy && position_type != POSITION_TYPE_BUY) || (!meta.is_buy && position_type != POSITION_TYPE_SELL)){
         reason = "position_direction_mismatch";
         return false;
      }
      if(PositionGetString(POSITION_COMMENT) != meta.broker_comment){
         reason = "position_comment_mismatch";
         return false;
      }
      double position_volume = PositionGetDouble(POSITION_VOLUME);
      double aggregate_entry_volume = 0.0;
      for(int deal_index=0; deal_index<HistoryDealsTotal(); deal_index++){
         ulong related_deal = HistoryDealGetTicket(deal_index);
         if(related_deal == 0) continue;
         if((ulong)HistoryDealGetInteger(related_deal, DEAL_ORDER) != order_ticket) continue;
         if((long)HistoryDealGetInteger(related_deal, DEAL_POSITION_ID) != position_identifier) continue;
         ENUM_DEAL_ENTRY related_entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(related_deal, DEAL_ENTRY);
         if(related_entry != DEAL_ENTRY_IN && related_entry != DEAL_ENTRY_INOUT) continue;
         aggregate_entry_volume += HistoryDealGetDouble(related_deal, DEAL_VOLUME);
      }
      if(aggregate_entry_volume <= 0.0) aggregate_entry_volume = expected_deal_volume;
      if(position_volume <= 0.0 || MathAbs(position_volume - aggregate_entry_volume) > volume_tolerance){
         reason = "position_volume_mismatch";
         return false;
      }
      datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
      datetime position_time = (datetime)PositionGetInteger(POSITION_TIME);
      // A missing timestamp and two timestamps that disagree are different
      // conditions and used to share one reason string.  The #Japan225 entry on
      // 2026.08.03 quarantined as "position_open_time_mismatch" and then verified
      // on a later transaction event for the same deal, which is the shape of an
      // unsettled read rather than a real disagreement -- but the reason string
      // could not tell the two apart, so the ledger recorded a mismatch that may
      // never have been one.  Both still fail closed; they are only nameable now.
      if(deal_time <= 0 || position_time <= 0){
         reason = "position_open_time_unavailable:deal_time=" + IntegerToString((long)deal_time)
                  + ":position_time=" + IntegerToString((long)position_time);
         return false;
      }
      if(MathAbs((double)(position_time - deal_time)) > (double)MathMax(0, InpLedgerTimestampToleranceSec)){
         reason = "position_open_time_mismatch:deal_time=" + IntegerToString((long)deal_time)
                  + ":position_time=" + IntegerToString((long)position_time)
                  + ":delta_sec=" + IntegerToString((long)(position_time - deal_time))
                  + ":tolerance_sec=" + IntegerToString(MathMax(0, InpLedgerTimestampToleranceSec));
         return false;
      }

      meta.position_id = position_identifier;
      meta.broker_position_identifier = position_identifier;
      meta.broker_position_ticket = position_ticket;
      meta.filled_entry = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
      meta.filled_at = deal_time;
      meta.execution_identity_verified = true;
      meta.execution_identity_quarantined = false;
      meta.execution_identity_reason = "verified_order_deal_position_chain";
      meta.attribution_status = "VERIFIED";
      meta.attribution_error = "";
      meta.learning_eligible = false;
      meta.optimization_eligible = false;
      meta.suppression_eligible = false;
      meta.ledger_integrity_status = "UNATTRIBUTED";
      meta.ledger_integrity_reasons = "[\"outcome_not_closed_or_audited\"]";
      meta.ledger_schema_version = TRADE_LEDGER_SCHEMA_VERSION;
      _Journal("[execution_identity] order_ticket=" + IntegerToString((long)order_ticket)
               + " deal_ticket=" + IntegerToString((long)deal_ticket)
               + " position_id=" + IntegerToString(position_identifier)
               + " position_ticket=" + IntegerToString((long)position_ticket)
               + " trade_key=" + meta.trade_key
               + " candidate_hash=" + meta.candidate_hash
               + " execution_fingerprint=" + meta.final_execution_fingerprint
               + " verified=true account_mode=" + m_account_position_mode);
      return true;
   }

   ulong _FindEntryDealForOrder(const ulong order_ticket, const long expected_position_identifier=0) {
      if(order_ticket == 0) return 0;
      datetime now = _NowServerOrLocal();
      if(!HistorySelect(now - MathMax(1, InpAnalyticsHistoryDays) * 86400, now + 60)) return 0;
      for(int i=HistoryDealsTotal()-1; i>=0; i--){
         ulong deal_ticket = HistoryDealGetTicket(i);
         if(deal_ticket == 0) continue;
         if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_ORDER) != order_ticket) continue;
         if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber) continue;
         ENUM_DEAL_ENTRY entry_kind = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
         if(entry_kind != DEAL_ENTRY_IN && entry_kind != DEAL_ENTRY_INOUT) continue;
         long position_identifier = (long)HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
         if(expected_position_identifier > 0 && position_identifier != expected_position_identifier) continue;
         return deal_ticket;
      }
      return 0;
   }

   bool _LoadAndResolvePositionMeta(const ulong position_ticket,
                                    const string symbol,
                                    const string comment,
                                    TradePlan &meta,
                                    string &reason) {
      reason = "";
      if(_LoadTradeMeta(position_ticket, symbol, comment, meta)){
         if(meta.execution_identity_verified && !meta.execution_identity_quarantined) return true;
      }
      if(!PositionSelectByTicket(position_ticket)){ reason = "position_not_selectable"; return false; }
      long position_identifier = (long)PositionGetInteger(POSITION_IDENTIFIER);
      TradePlan pending_meta;
      bool have_pending_meta = false;
      if(StringLen(comment) > 0)
         have_pending_meta = _ReadTradeMetaPath(_TradeKeyPath(comment), pending_meta);
      if(!have_pending_meta || pending_meta.result_order_ticket == 0){
         reason = "missing_exact_order_metadata_for_position";
         return false;
      }
      if(pending_meta.symbol != symbol || pending_meta.broker_comment != comment){
         reason = "pending_metadata_symbol_or_comment_mismatch";
         return false;
      }
      ulong entry_deal = _FindEntryDealForOrder(pending_meta.result_order_ticket, position_identifier);
      if(entry_deal == 0){ reason = "entry_deal_not_found_for_order_and_position"; return false; }
      if(!_ResolveExactExecutionIdentity(pending_meta,
                                         pending_meta.result_order_ticket,
                                         entry_deal,
                                         pending_meta.initial_volume,
                                         reason)) return false;
      if(pending_meta.broker_position_ticket != position_ticket){
         reason = "resolved_position_ticket_mismatch";
         return false;
      }
      meta = pending_meta;
      _WriteTradeMeta(meta, position_ticket);
      return true;
   }

   void _NormalizeRecoveredExposureMeta(TradePlan &meta,
                                        const string symbol,
                                        const bool is_buy,
                                        const double entry,
                                        const double sl,
                                        const double tp,
                                        const double volume,
                                        const datetime opened_at,
                                        const string comment,
                                        const long position_id,
                                        const bool pending_order) {
      datetime now = _NowServerOrLocal();
      if(StringLen(meta.symbol) == 0) meta.symbol = symbol;
      meta.is_buy = is_buy;
      if((int)meta.htf <= 0) meta.htf = PO3EffectiveHTF();
      if((int)meta.ltf <= 0) meta.ltf = PO3EffectiveEntryTF();
      if((int)meta.confirm_tf <= 0) meta.confirm_tf = PO3EffectiveConfirmTF();

      if(StringLen(meta.trade_key) == 0){
         if(StringLen(comment) > 0) meta.trade_key = comment;
         else meta.trade_key = symbol + "_" + IntegerToString((int)(position_id > 0 ? position_id : (long)now));
      }
      if(StringLen(meta.entry_model) == 0) meta.entry_model = "recovered";
      if(StringLen(meta.entry_branch) == 0) meta.entry_branch = (pending_order ? "recovered_pending_order" : "recovered_live_position");
      if(StringLen(meta.setup_family) == 0) meta.setup_family = "recovered";
      if(StringLen(meta.setup_class) == 0) meta.setup_class = "recovered";
      if(StringLen(meta.target_source) == 0) meta.target_source = "recovered_broker_tp";
      if(StringLen(meta.ai_decision_source) == 0) meta.ai_decision_source = "restart_recovery";

      if(meta.entry_est <= 0.0) meta.entry_est = entry;
      if(meta.sl <= 0.0) meta.sl = sl;
      if(meta.tp2 <= 0.0) meta.tp2 = tp;
      if(meta.planned_entry <= 0.0) meta.planned_entry = entry;
      if(meta.planned_sl <= 0.0) meta.planned_sl = sl;
      if(meta.planned_tp2 <= 0.0) meta.planned_tp2 = tp;
      if(meta.created_at <= 0) meta.created_at = (opened_at > 0 ? opened_at : now);
      if(meta.planned_at <= 0) meta.planned_at = meta.created_at;
      if(meta.initial_volume <= 0.0) meta.initial_volume = volume;

      if(!pending_order){
         if(meta.filled_entry <= 0.0) meta.filled_entry = entry;
         if(meta.filled_at <= 0) meta.filled_at = (opened_at > 0 ? opened_at : now);
         if(position_id > 0) meta.position_id = position_id;
         if(StringLen(meta.narrative_state) == 0) meta.narrative_state = "executed_recovered";
         if(meta.mfe_price <= 0.0) meta.mfe_price = entry;
         if(meta.mae_price <= 0.0) meta.mae_price = entry;
      } else {
         if(StringLen(meta.narrative_state) == 0) meta.narrative_state = "pending_order";
         meta.last_confirm_bar_time = _LastClosedBarTime(symbol, meta.confirm_tf);
      }
   }

   void _RecoverManagedExposureOnInit() {
      int recovered_positions = 0;
      int repaired_positions = 0;
      int recovered_orders = 0;
      int repaired_orders = 0;

      for(int i=PositionsTotal()-1; i>=0; i--){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;

         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         bool is_buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double entry = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double vol = PositionGetDouble(POSITION_VOLUME);
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
         long position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);

         TradePlan meta;
         string identity_reason = "";
         bool had_meta = _LoadAndResolvePositionMeta(ticket, sym, comment, meta, identity_reason);
         if(!had_meta){
            ZeroMemory(meta);
            meta.symbol = sym;
            meta.broker_comment = comment;
            meta.trade_key = comment;
            meta.position_id = position_id;
            meta.broker_position_identifier = position_id;
            meta.broker_position_ticket = ticket;
         }
         _NormalizeRecoveredExposureMeta(meta, sym, is_buy, entry, sl, tp, vol, opened, comment, position_id, false);
         if(!had_meta)
            _QuarantineExecutionIdentity(meta, "restart_position_attribution_unavailable:" + identity_reason, 0, 0);
         double live_px = (is_buy ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK));
         if(live_px <= 0.0) live_px = entry;
         if(meta.execution_identity_verified && !meta.execution_identity_quarantined)
            _UpdateAnalyticsSnapshot(meta, ticket, live_px, vol);
         _WriteTradeMeta(meta, ticket);
         if(meta.execution_identity_verified && !meta.execution_identity_quarantined)
            _RememberConsumedSweep(meta);
         if(had_meta) repaired_positions++;
         else recovered_positions++;
         _Journal(sym + " managed position " + (had_meta ? "reattached" : "recovered")
                  + " after restart ticket=" + IntegerToString((int)ticket)
                  + " key=" + meta.trade_key
                  + " state=" + meta.narrative_state);
      }

      for(int i=OrdersTotal()-1; i>=0; i--){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;
         ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         bool is_buy_limit = (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY_STOP_LIMIT);
         bool is_sell_limit = (type == ORDER_TYPE_SELL_LIMIT || type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL_STOP_LIMIT);
         if(!is_buy_limit && !is_sell_limit) continue;

         string sym = OrderGetString(ORDER_SYMBOL);
         string comment = OrderGetString(ORDER_COMMENT);
         double entry = OrderGetDouble(ORDER_PRICE_OPEN);
         double sl = OrderGetDouble(ORDER_SL);
         double tp = OrderGetDouble(ORDER_TP);
         double vol = OrderGetDouble(ORDER_VOLUME_CURRENT);
         if(vol <= 0.0) vol = OrderGetDouble(ORDER_VOLUME_INITIAL);
         datetime setup_time = (datetime)OrderGetInteger(ORDER_TIME_SETUP);

         TradePlan meta;
         bool had_meta = _LoadTradeMeta(ticket, sym, comment, meta);
         if(!had_meta){
            ZeroMemory(meta);
            meta.symbol = sym;
            meta.broker_comment = comment;
            meta.trade_key = comment;
            meta.result_order_ticket = ticket;
         }
         _NormalizeRecoveredExposureMeta(meta, sym, is_buy_limit, entry, sl, tp, vol, setup_time, comment, (long)ticket, true);
         if(!had_meta){
            _QuarantineExecutionIdentity(meta, "restart_pending_order_attribution_unavailable", ticket, 0);
            _Journal(sym + " deleting unattributed pending order during restart ticket=" + IntegerToString((long)ticket));
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete("unattributed_pending_order");
            continue;
         }
         if(InpOnlyBreakerRetestVirginStrongOrigin && !_ExclusiveModelPreExecutionOk(meta)){
            _Journal(sym + " managed pending order deleted during restart recovery reason=exclusive_model_integrity_failed ticket="
                     + IntegerToString((int)ticket));
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete("exclusive_model_integrity_failed");
            continue;
         }
         _WriteTradeMeta(meta, ticket);
         if(had_meta) repaired_orders++;
         else recovered_orders++;
         _Journal(sym + " managed pending order " + (had_meta ? "reattached" : "recovered")
                  + " after restart ticket=" + IntegerToString((int)ticket)
                  + " key=" + meta.trade_key
                  + " state=" + meta.narrative_state);
      }

      if(recovered_positions + repaired_positions + recovered_orders + repaired_orders > 0){
         _Journal("restart exposure recovery summary"
                  + " positions_recovered=" + IntegerToString(recovered_positions)
                  + " positions_reattached=" + IntegerToString(repaired_positions)
                  + " orders_recovered=" + IntegerToString(recovered_orders)
                  + " orders_reattached=" + IntegerToString(repaired_orders));
      }
   }

   void _QueueAnalyticsRefreshJob(const TradePlan &meta) {
      if(!InpAnalyticsEnable || !InpAutoAnalyticsRefresh) return;
      if(!meta.learning_eligible || !meta.optimization_eligible || meta.ledger_integrity_status != "CLEAN"){
         _Journal("[ledger_integrity_gate] analytics_refresh=false trade_key=" + meta.trade_key
                  + " status=" + meta.ledger_integrity_status
                  + " reasons=" + meta.ledger_integrity_reasons);
         return;
      }
      string job = "{";
      job += JsonKVStr("job_type", "refresh_suite") + ",";
      job += JsonKVStr("trade_key", meta.trade_key) + ",";
      job += JsonKVStr("symbol", meta.symbol) + ",";
      job += JsonKVInt("closed_at", (int)meta.closed_at) + ",";
      job += JsonKVStr("policy_snapshot_id", meta.policy_snapshot_id) + ",";
      job += JsonKVBool("auto_activate", InpAnalyticsAutoActivate) + ",";
      job += JsonKVBool("policy_shadow_mode", InpPolicyShadowMode) + ",";
      job += JsonKVInt("policy_min_total_closed_trades", InpPolicyMinTotalClosedTrades) + ",";
      job += JsonKVInt("policy_min_bucket_trades", InpPolicyMinBucketTrades) + ",";
      job += JsonKVNum("policy_min_profit_factor_after_costs", InpPolicyMinProfitFactorAfterCosts, 4) + ",";
      job += JsonKVNum("policy_max_train_test_gap_r", InpPolicyMaxTrainTestGapR, 4) + ",";
      job += JsonKVNum("policy_min_positive_fold_rate", InpPolicyMinPositiveFoldRate, 4) + ",";
      job += JsonKVNum("policy_max_drawdown_r", InpPolicyMaxDrawdownR, 4) + ",";
      job += JsonKVStr("po3_subtype", _Po3Subtype(meta)) + ",";
      job += JsonKVStr("fvg_subtype", _FvgSubtype(meta)) + ",";
      job += JsonKVStr("setup_class", meta.setup_class) + ",";
      job += JsonKVBool("exclusive_model_mode", meta.exclusive_model_mode) + ",";
      job += JsonKVStr("exclusive_model_name", meta.exclusive_model_name) + ",";
      job += JsonKVBool("exclusive_model_passed", meta.exclusive_model_passed) + ",";
      job += JsonKVStr("origin_quality", meta.origin_quality) + ",";
      job += JsonKVStr("regime_bucket", _RegimeBucket(meta));
      job += "}";
      string job_name = _AnalyticsJobsDir() + "\\refresh_" + meta.trade_key + ".json";
      m_bus.WriteText(job_name, job);
   }

   void _AppendLedgerIntegrityReason(string &json, int &count, const string reason) const {
      if(count > 0) json += ",";
      json += "\"" + JsonEscape(reason) + "\"";
      count++;
   }

   void _AppendCompletedAiTradeLedger(const TradePlan &meta,
                                      const string partials_json,
                                      const string deals_json,
                                      const double full_close_pct,
                                      const long final_reason) {
      if(!InpAnalyticsEnable) return;
      string setup_code = _SetupCodeForPlan(meta);
      string row = "{";
      row += JsonKVStr("trade_key", meta.trade_key) + ",";
      row += JsonKVStr("position_ticket", IntegerToString((long)meta.broker_position_ticket)) + ",";
      row += JsonKVStr("position_identifier", IntegerToString(meta.broker_position_identifier)) + ",";
      row += JsonKVStr("order_ticket", IntegerToString((long)meta.result_order_ticket)) + ",";
      row += JsonKVStr("deal_ticket", IntegerToString((long)meta.result_deal_ticket)) + ",";
      row += JsonKVStr("intended_order_type", meta.intended_order_type) + ",";
      row += JsonKVStr("execution_authority_state", meta.execution_authority_state) + ",";
      row += JsonKVBool("broker_submission_attempted", meta.broker_submission_attempted) + ",";
      row += JsonKVBool("broker_request_accepted", meta.broker_request_accepted) + ",";
      row += JsonKVNum("broker_retcode", (double)meta.broker_retcode, 0) + ",";
      row += JsonKVStr("broker_retcode_description", meta.broker_retcode_description) + ",";
      row += JsonKVBool("broker_partial_fill", meta.broker_partial_fill) + ",";
      row += JsonKVBool("final_execution_success", meta.final_execution_success) + ",";
      row += JsonKVBool("execution_identity_verified", meta.execution_identity_verified) + ",";
      row += JsonKVStr("attribution_status", meta.attribution_status) + ",";
      row += JsonKVStr("ledger_schema_version", meta.ledger_schema_version) + ",";
      row += JsonKVStr("ledger_integrity_status", meta.ledger_integrity_status) + ",";
      row += "\"ledger_integrity_reasons\":" + (StringLen(meta.ledger_integrity_reasons) > 0 ? meta.ledger_integrity_reasons : "[]") + ",";
      row += JsonKVBool("learning_eligible", meta.learning_eligible) + ",";
      row += JsonKVBool("optimization_eligible", meta.optimization_eligible) + ",";
      row += JsonKVBool("suppression_eligible", meta.suppression_eligible) + ",";
      row += JsonKVStr("candidate_id", meta.candidate_id) + ",";
      row += JsonKVStr("candidate_hash", meta.candidate_hash) + ",";
      row += JsonKVStr("ai_selected_candidate_hash", meta.ai_selected_candidate_hash) + ",";
      row += JsonKVStr("executed_candidate_hash", meta.executed_candidate_hash) + ",";
      row += JsonKVBool("candidate_hash_match", meta.candidate_hash_match) + ",";
      row += JsonKVStr("assessed_execution_fingerprint", meta.assessed_execution_fingerprint) + ",";
      row += JsonKVStr("final_execution_fingerprint", meta.final_execution_fingerprint) + ",";
      row += JsonKVBool("execution_fingerprint_match", meta.execution_fingerprint_match) + ",";
      row += JsonKVStr("symbol", meta.symbol) + ",";
      row += JsonKVStr("direction", meta.is_buy ? "buy" : "sell") + ",";
      row += JsonKVStr("setup_code", setup_code) + ",";
      row += JsonKVStr("setup_family", meta.setup_family) + ",";
      row += JsonKVStr("setup_class", meta.setup_class) + ",";
      row += JsonKVStr("setup_taxonomy_version", meta.setup_taxonomy_version) + ",";
      row += JsonKVStr("setup_taxonomy_enum", meta.setup_taxonomy_enum) + ",";
      row += JsonKVStr("taxonomy_mapping_source", meta.taxonomy_mapping_source) + ",";
      row += JsonKVStr("session", meta.session_code) + ",";
      row += JsonKVStr("killzone", meta.killzone_code) + ",";
      row += JsonKVStr("decision_schema_version", meta.ai.decision_schema_version) + ",";
      row += JsonKVStr("decision_quality_tier", meta.ai.decision_quality_tier) + ",";
      row += JsonKVStr("response_quality", meta.ai.response_quality_alias) + ",";
      row += JsonKVStr("decision_source", meta.ai.decision_source) + ",";
      row += JsonKVStr("workload_mode", meta.ai.workload_mode) + ",";
      row += JsonKVStr("provider_contract_version", meta.ai.provider_contract_version) + ",";
      row += JsonKVStr("provider_mode", meta.ai.provider_mode) + ",";
      row += JsonKVStr("provider_id", meta.ai.provider_id) + ",";
      row += JsonKVStr("endpoint_class", meta.ai.endpoint_class) + ",";
      row += JsonKVStr("endpoint_identity_hash", meta.ai.endpoint_identity_hash) + ",";
      row += JsonKVStr("configured_models_hash", meta.ai.configured_models_hash) + ",";
      row += JsonKVStr("actual_model_id", meta.ai.actual_model_id) + ",";
      row += JsonKVStr("fallback_model", meta.ai.fallback_model) + ",";
      row += JsonKVStr("model_fingerprint", meta.ai.model_fingerprint) + ",";
      row += JsonKVStr("evidence_envelope_version", meta.ai.evidence_envelope_version) + ",";
      row += JsonKVStr("family_profile_version", meta.ai.family_profile_version) + ",";
      row += JsonKVStr("memory_schema_version", meta.ai.memory_schema_version) + ",";
      row += JsonKVStr("retrieval_policy_version", meta.ai.retrieval_policy_version) + ",";
      row += JsonKVStr("role_contract_version", meta.ai.role_contract_version) + ",";
      row += JsonKVStr("consensus_resolver_version", meta.ai.consensus_resolver_version) + ",";
      row += JsonKVStr("generation_settings_hash", meta.ai.generation_settings_hash) + ",";
      row += JsonKVStr("input_fingerprint", meta.ai.input_fingerprint) + ",";
      row += "\"retrieved_analogue_ids\":" + (StringLen(meta.ai.retrieved_analogue_ids_json) > 0 ? meta.ai.retrieved_analogue_ids_json : "[]") + ",";
      row += JsonKVStr("historical_evidence_state", meta.ai.historical_evidence_state) + ",";
      row += JsonKVStr("analyst_response_fingerprint", meta.ai.analyst_response_fingerprint) + ",";
      row += JsonKVStr("critic_response_fingerprint", meta.ai.critic_response_fingerprint) + ",";
      row += JsonKVStr("adjudicator_response_fingerprint", meta.ai.adjudicator_response_fingerprint) + ",";
      row += JsonKVStr("final_resolver_reason", meta.ai.final_resolver_reason) + ",";
      row += JsonKVStr("provider_health_state", meta.ai.provider_health_state) + ",";
      row += "\"role_latencies\":" + (StringLen(meta.ai.role_latencies_json) > 0 ? meta.ai.role_latencies_json : "{}") + ",";
      row += "\"provider_retry_counts\":" + (StringLen(meta.ai.provider_retry_counts_json) > 0 ? meta.ai.provider_retry_counts_json : "{}") + ",";
      row += "\"provider_usage\":" + (StringLen(meta.ai.provider_usage_json) > 0 ? meta.ai.provider_usage_json : "{}") + ",";
      row += "\"estimated_context_tokens\":" + (meta.ai.estimated_context_tokens_available ? DoubleToString(meta.ai.estimated_context_tokens, 0) : "null") + ",";
      row += "\"unsupported_generation_parameters\":" + (StringLen(meta.ai.unsupported_generation_parameters_json) > 0 ? meta.ai.unsupported_generation_parameters_json : "[]") + ",";
      row += "\"analyst_output\":" + (StringLen(meta.ai.analyst_output_json) > 0 ? meta.ai.analyst_output_json : "{}") + ",";
      row += "\"critic_output\":" + (StringLen(meta.ai.critic_output_json) > 0 ? meta.ai.critic_output_json : "{}") + ",";
      row += "\"adjudicator_output\":" + (StringLen(meta.ai.adjudicator_output_json) > 0 ? meta.ai.adjudicator_output_json : "{}") + ",";
      row += JsonKVStr("decision_state", meta.ai.decision_state) + ",";
      row += JsonKVBool("model_raw_allow", meta.model_raw_allow) + ",";
      row += JsonKVBool("python_final_allow", meta.python_final_allow) + ",";
      row += JsonKVBool("mql_final_allow", meta.mql_final_allow) + ",";
      row += "\"decision_field_authority\":" + (StringLen(meta.decision_field_authority_json) > 0 ? meta.decision_field_authority_json : "{}") + ",";
      row += JsonKVStr("python_decision_reasons", meta.python_decision_reasons) + ",";
      row += JsonKVStr("mql_decision_reasons", meta.mql_decision_reasons) + ",";
      row += JsonKVStr("engine_version", meta.engine_version) + ",";
      row += JsonKVStr("git_commit", meta.git_commit) + ",";
      row += JsonKVStr("dirty_tree_status", meta.dirty_tree_status) + ",";
      row += JsonKVStr("set_file_hash", meta.set_file_hash) + ",";
      row += JsonKVStr("runtime_input_hash", meta.runtime_input_hash) + ",";
      row += JsonKVStr("prompt_contract_version", meta.prompt_contract_version) + ",";
      row += JsonKVStr("model_version", meta.ai.model_version) + ",";
      row += JsonKVStr("reasoning_configuration", meta.reasoning_configuration) + ",";
      row += JsonKVStr("target_schema_version", meta.target_arbitration_schema_version) + ",";
      row += JsonKVStr("policy_id", meta.policy_snapshot_id) + ",";
      row += JsonKVStr("policy_hash", meta.policy_hash) + ",";
      row += JsonKVStr("bucket_prior_hash", meta.bucket_prior_hash) + ",";
      row += JsonKVStr("taxonomy_version", meta.setup_taxonomy_version) + ",";
      row += JsonKVStr("feature_version", meta.feature_version) + ",";
      row += JsonKVStr("calibration_artifact_id", meta.calibration_artifact_id) + ",";
      row += JsonKVStr("repeatability_artifact_id", meta.repeatability_artifact_id) + ",";
      row += JsonKVStr("cohort_id", meta.cohort_id) + ",";
      row += JsonKVBool("cohort_complete", meta.cohort_complete) + ",";
      row += JsonKVNum("rule_score", meta.ai.rule_score, 6) + ",";
      row += JsonKVNum("llm_quality_score", meta.ai.llm_quality_score, 6) + ",";
      row += JsonKVNum("blended_legacy_score_diagnostic", meta.ai.blended_legacy_score, 6) + ",";
      row += JsonKVNum("legacy_agreement_confidence_diagnostic", meta.ai.legacy_agreement_confidence, 6) + ",";
      row += JsonKVNum("llm_self_reported_confidence", meta.ai.llm_self_reported_confidence, 6) + ",";
      row += JsonKVBool("calibration_available", meta.ai.calibration_available) + ",";
      row += "\"calibrated_win_probability\":null,";
      row += "\"expected_net_r\":null,";
      row += JsonKVNum("structure_quality_score", meta.ai.structure_quality_score, 6) + ",";
      row += JsonKVNum("entry_timing_score", meta.ai.entry_timing_score, 6) + ",";
      row += JsonKVNum("follow_through_probability", meta.ai.follow_through_probability, 6) + ",";
      row += JsonKVNum("invalidation_risk", meta.ai.invalidation_risk, 6) + ",";
      row += JsonKVNum("chop_risk", meta.ai.chop_risk, 6) + ",";
      row += JsonKVNum("post_entry_failure_risk", meta.ai.post_entry_failure_risk, 6) + ",";
      row += JsonKVNum("final_trade_expectancy_score", meta.ai.final_trade_expectancy_score, 6) + ",";
      row += JsonKVStr("llm_numeric_diagnostics_authority", meta.ai.llm_numeric_diagnostics_authority) + ",";
      row += JsonKVBool("ai_veto_enabled", meta.ai.veto_enabled) + ",";
      row += JsonKVStr("ai_veto_code", meta.ai.veto_code) + ",";
      row += "\"ai_veto_evidence_fields\":" + (StringLen(meta.ai.veto_evidence_fields_json) > 0 ? meta.ai.veto_evidence_fields_json : "[]") + ",";
      row += JsonKVStr("ai_veto_reason", meta.ai.veto_reason) + ",";
      row += JsonKVStr("bucket_prior_override_justification", meta.ai.bucket_prior_override_justification) + ",";
      row += JsonKVNum("entry_price", meta.filled_entry, 8) + ",";
      row += JsonKVNum("sl", (meta.planned_sl > 0 ? meta.planned_sl : meta.sl), 8) + ",";
      row += JsonKVNum("tp1", (meta.planned_tp1 > 0 ? meta.planned_tp1 : meta.tp1), 8) + ",";
      row += JsonKVNum("tp2", (meta.planned_tp2 > 0 ? meta.planned_tp2 : meta.tp2), 8) + ",";
      row += JsonKVNum("rr2", _PlanRR2(meta), 6) + ",";
      row += JsonKVNum("volume", meta.initial_volume, 4) + ",";
      row += "\"partials\":" + (StringLen(partials_json) > 0 ? partials_json : "[]") + ",";
      row += "\"deals\":" + (StringLen(deals_json) > 0 ? deals_json : "[]") + ",";
      row += JsonKVNum("full_close_pnl", meta.realized_pnl, 2) + ",";
      row += JsonKVNum("full_close_pct", full_close_pct, 6) + ",";
      row += JsonKVNum("full_close_r", meta.realized_r, 6) + ",";
      row += JsonKVNum("gross_price_pnl", meta.gross_price_pnl, 2) + ",";
      row += JsonKVNum("total_commission", meta.total_commission, 2) + ",";
      row += JsonKVNum("total_swap", meta.total_swap, 2) + ",";
      row += JsonKVNum("total_fees", meta.total_fees, 2) + ",";
      row += JsonKVNum("predicted_round_turn_cost", meta.estimated_cost_stressed_per_lot * MathMax(0.0, meta.initial_volume), 4) + ",";
      row += JsonKVNum("actual_realized_cost", meta.actual_realized_cost, 4) + ",";
      row += JsonKVNum("cost_prediction_error", meta.cost_prediction_error, 4) + ",";
      row += JsonKVStr("cost_source", meta.estimated_cost_source) + ",";
      row += JsonKVInt("cost_sample_size", meta.estimated_cost_sample_size) + ",";
      row += JsonKVStr("commission_model_version", meta.commission_model_version) + ",";
      row += JsonKVNum("broker_net_pnl", meta.broker_net_pnl, 2) + ",";
      row += JsonKVNum("internal_net_pnl", meta.internal_net_pnl, 2) + ",";
      row += JsonKVNum("pnl_reconciliation_difference", meta.pnl_reconciliation_difference, 6) + ",";
      row += JsonKVNum("result_pct_fixed_initial_balance", meta.result_pct_fixed_initial_balance, 6) + ",";
      row += JsonKVNum("result_pct_equity_at_entry", meta.result_pct_equity_at_entry, 6) + ",";
      row += JsonKVNum("result_r_initial_risk", meta.result_r_initial_risk, 6) + ",";
      row += JsonKVStr("outcome_direction_broker", meta.outcome_direction_broker) + ",";
      row += JsonKVStr("outcome_direction_r", meta.outcome_direction_r) + ",";
      row += JsonKVStr("outcome_direction_equity_pct", meta.outcome_direction_equity_pct) + ",";
      row += JsonKVBool("outcome_direction_match", meta.outcome_direction_match) + ",";
      row += JsonKVStr("outcome_reconciliation_status", meta.outcome_reconciliation_status) + ",";
      row += JsonKVStr("full_close_reason", _DealReasonLabel(final_reason)) + ",";
      row += JsonKVStr("target_source", meta.target_source) + ",";
      row += JsonKVStr("target_model", meta.target_model) + ",";
      row += JsonKVStr("hierarchical_prior_artifact_hash", meta.hierarchical_prior_artifact_hash) + ",";
      row += JsonKVStr("hierarchical_prior_schema_version", meta.hierarchical_prior_schema_version) + ",";
      row += JsonKVStr("repeatability_status", meta.repeatability_status) + ",";
      row += JsonKVStr("repeatability_group_key", meta.repeatability_group_key) + ",";
      row += JsonKVStr("repeatability_authority_hash", meta.repeatability_authority_hash) + ",";
      row += JsonKVBool("repeatability_required_live", meta.ai.repeatability_required_live) + ",";
      row += JsonKVStr("repeatability_artifact_state", meta.ai.repeatability_artifact_state) + ",";
      row += JsonKVStr("repeatability_rejection_code", meta.ai.repeatability_rejection_code) + ",";
      row += JsonKVStr("risk_factor_schema_version", meta.risk_factor_schema_version) + ",";
      row += "\"risk_factor_contributions\":" + (StringLen(meta.risk_factor_contributions_json) > 0 ? meta.risk_factor_contributions_json : "{}") + ",";
      row += JsonKVNum("original_initial_risk_money", meta.original_initial_risk_money, 4) + ",";
      row += JsonKVStr("management_version", meta.management_version) + ",";
      row += JsonKVStr("management_state", meta.management_state) + ",";
      row += JsonKVStr("management_action_id", meta.management_action_id) + ",";
      row += JsonKVStr("management_action_lifecycle_state", meta.management_action_lifecycle_state) + ",";
      row += JsonKVStr("management_requested_action", meta.management_requested_action) + ",";
      row += JsonKVNum("management_requested_volume", meta.management_requested_volume, 8) + ",";
      row += JsonKVNum("management_normalized_volume", meta.management_normalized_volume, 8) + ",";
      row += JsonKVNum("management_position_volume_before", meta.management_position_volume_before, 8) + ",";
      row += JsonKVNum("management_requested_cut_fraction", meta.management_requested_cut_fraction, 8) + ",";
      row += JsonKVInt("management_action_retry_count", meta.management_action_retry_count) + ",";
      row += JsonKVInt("management_next_retry_at", (int)meta.management_next_retry_at) + ",";
      row += JsonKVNum("management_last_retcode", (double)meta.management_last_retcode, 0) + ",";
      row += JsonKVStr("management_last_retcode_description", meta.management_last_retcode_description) + ",";
      row += JsonKVStr("management_action_terminal_reason", meta.management_action_terminal_reason) + ",";
      row += JsonKVStr("management_policy", meta.management_policy) + ",";
      row += JsonKVInt("management_decision_at", (int)meta.management_decision_at) + ",";
      row += JsonKVBool("management_features_time_safe", meta.management_features_time_safe) + ",";
      row += JsonKVStr("management_snapshot_action", meta.management_snapshot_action) + ",";
      row += JsonKVNum("management_snapshot_mfe_r", meta.management_snapshot_mfe_r, 6) + ",";
      row += JsonKVNum("management_snapshot_mae_r", meta.management_snapshot_mae_r, 6) + ",";
      row += JsonKVInt("management_snapshot_minutes_open", meta.management_snapshot_minutes_open) + ",";
      row += JsonKVNum("management_snapshot_distance_to_sl_r", meta.management_snapshot_distance_to_sl_r, 6) + ",";
      row += JsonKVNum("management_snapshot_distance_to_tp_r", meta.management_snapshot_distance_to_tp_r, 6) + ",";
      row += JsonKVNum("management_snapshot_spread_r", meta.management_snapshot_spread_r, 6) + ",";
      row += JsonKVNum("management_snapshot_execution_cost_r", meta.management_snapshot_execution_cost_r, 6) + ",";
      row += JsonKVBool("management_snapshot_structure_valid", meta.management_snapshot_structure_valid) + ",";
      row += JsonKVNum("actual_managed_result", meta.actual_managed_result, 4) + ",";
      row += JsonKVNum("actual_managed_result_r", meta.actual_managed_result_r, 6) + ",";
      if(!meta.counterfactual_pending && !meta.counterfactual_ambiguous){
         row += JsonKVNum("counterfactual_original_sl_tp_result", meta.counterfactual_original_sl_tp_result, 4) + ",";
         row += JsonKVNum("counterfactual_original_sl_tp_result_r", meta.counterfactual_original_sl_tp_result_r, 6) + ",";
      } else {
         row += "\"counterfactual_original_sl_tp_result\":null,\"counterfactual_original_sl_tp_result_r\":null,";
      }
      if(meta.counterfactual_target_before_stop_available)
         row += JsonKVBool("target_before_stop", meta.counterfactual_target_before_stop) + ",";
      else
         row += "\"target_before_stop\":null,";
      if(meta.counterfactual_target_before_stop_available)
         row += JsonKVBool("unmanaged_original_plan_target_before_stop", meta.counterfactual_target_before_stop) + ",";
      else
         row += "\"unmanaged_original_plan_target_before_stop\":null,";
      if(!meta.counterfactual_pending && !meta.counterfactual_ambiguous)
         row += JsonKVNum("unmanaged_original_plan_net_r", meta.counterfactual_original_sl_tp_result_r, 6) + ",";
      else
         row += "\"unmanaged_original_plan_net_r\":null,";
      row += JsonKVNum("observed_mfe_r", meta.mfe_r, 6) + ",";
      row += JsonKVNum("observed_mae_r", MathAbs(meta.mae_r), 6) + ",";
      if(meta.management_policy_selection_eligible)
         row += JsonKVNum("management_alpha", meta.management_alpha, 6) + ",";
      else
         row += "\"management_alpha\":null,";
      row += JsonKVBool("counterfactual_ambiguous", meta.counterfactual_ambiguous) + ",";
      row += JsonKVBool("management_policy_selection_eligible", meta.management_policy_selection_eligible) + ",";
      row += JsonKVNum("mfe_r", meta.mfe_r, 6) + ",";
      row += JsonKVNum("mae_r", meta.mae_r, 6) + ",";
      row += JsonKVInt("first_0_25r_time", (int)meta.first_0_25r_time) + ",";
      row += JsonKVInt("first_0_50r_time", (int)meta.first_0_50r_time) + ",";
      row += JsonKVInt("first_adverse_threshold_time", (int)meta.first_adverse_threshold_time) + ",";
      row += JsonKVInt("latest_observed_tick_time", (int)meta.latest_observed_tick_time) + ",";
      row += JsonKVNum("latest_observed_tick_msc", (double)meta.latest_observed_tick_msc, 0) + ",";
      row += JsonKVStr("path_completeness_status", meta.path_completeness_status) + ",";
      row += JsonKVStr("path_observation_source", meta.path_observation_source) + ",";
      row += JsonKVBool("path_data_gap", meta.path_data_gap) + ",";
      row += JsonKVBool("path_order_ambiguous", meta.path_order_ambiguous) + ",";
      row += JsonKVInt("minutes_to_0_25r_mfe", meta.minutes_to_0_25r_mfe) + ",";
      row += JsonKVInt("minutes_to_0_50r_mfe", meta.minutes_to_0_50r_mfe) + ",";
      row += JsonKVBool("stuck_no_mfe_triggered", meta.stuck_no_mfe_triggered) + ",";
      row += JsonKVBool("dr_and_structural_invalid_triggered", meta.dr_and_structural_invalid_triggered) + ",";
      row += JsonKVInt("penalty_reductions_count", meta.penalty_reductions_count) + ",";
      row += JsonKVInt("opened_at", (int)meta.filled_at) + ",";
      row += JsonKVInt("closed_at", (int)meta.closed_at);
      row += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\completed_ai_trades.jsonl", row + "\n");
      _Journal("[trade_completed] ticket=" + IntegerToString((long)meta.broker_position_ticket)
               + " key=" + meta.trade_key
               + " symbol=" + meta.symbol
               + " setup_code=" + setup_code
               + " full_close_pnl=" + DoubleToString(meta.realized_pnl, 2)
               + " full_close_pct=" + DoubleToString(full_close_pct, 6)
               + " llm_quality_score=" + DoubleToString(meta.ai.llm_quality_score, 2)
               + " llm_self_reported_confidence=" + DoubleToString(meta.ai.llm_self_reported_confidence, 3));
   }

   bool _WriteClosedTradeOutcome(const string key_hint, const long position_id, const string symbol_hint) {
      if(!InpAnalyticsEnable) return false;

      string key = key_hint;
      if(StringLen(key) == 0 && position_id > 0) key = symbol_hint + "_" + IntegerToString(position_id);
      if(StringLen(key) == 0) return false;
      // An OUT deal can be a partial risk-management exit.  The deal comment is
      // often "expert_exit", not the original trade key, so comment-based open
      // checks cannot prove that the position is fully closed.  The immutable
      // DEAL_POSITION_ID/POSITION_IDENTIFIER relationship is authoritative.
      if(position_id > 0 && _FindPositionTicketByIdentifier(position_id) > 0){
         _Journal("[trade_completion_deferred] reason=position_identifier_still_open"
                  + " position_id=" + IntegerToString(position_id)
                  + " key_hint=" + key
                  + " partial_exit_not_final=true");
         return false;
      }
      if(position_id > 0 && _PathExists(_CompletedPositionMarkerPath(position_id))) return false;
      if(_PathExists(_TradeResultPath(key))) return false;
      if(_HasOpenPositionWithKey(key)) return false;

      TradePlan meta;
      bool have_meta = false;
      if(StringLen(key_hint) > 0) have_meta = _ReadTradeMetaPath(_TradeKeyPath(key_hint), meta);
      if(!have_meta && position_id > 0) have_meta = _ReadTradeMetaPath(_TradePositionIdentifierPath(position_id), meta);
      if(have_meta && StringLen(meta.trade_key) > 0) key = meta.trade_key;
      // Re-check after resolving the canonical trade key. Exit-deal comments
      // such as "expert_exit" or "sl ..." are not stable dedupe identities.
      if(have_meta && _PathExists(_TradeResultPath(key))) return false;
      if(!have_meta){
         _Journal("[execution_identity_quarantine] reason=closed_trade_metadata_missing"
                  + " position_id=" + IntegerToString(position_id)
                  + " symbol=" + symbol_hint
                  + " analytics_excluded=true");
         return false;
      }
      if(!meta.execution_identity_verified || meta.execution_identity_quarantined ||
         position_id <= 0 || meta.broker_position_identifier != position_id){
         _Journal("[execution_identity_quarantine] reason=closed_trade_identity_not_verified"
                  + " position_id=" + IntegerToString(position_id)
                  + " stored_position_id=" + IntegerToString(meta.broker_position_identifier)
                  + " candidate_hash=" + meta.candidate_hash
                  + " analytics_excluded=true");
         return false;
      }

      datetime now = _NowServerOrLocal();
      datetime from = now - MathMax(1, InpAnalyticsHistoryDays) * 86400;
      if(!HistorySelect(from, now)) return false;

      double in_vol = 0.0, in_value = 0.0;
      double out_vol = 0.0, out_value = 0.0;
      double broker_gross_price_pnl = 0.0;
      double total_commission = 0.0;
      double total_swap = 0.0;
      double total_fees = 0.0;
      double broker_net_pnl = 0.0;
      double internal_gross_price_pnl = 0.0;
      double exit_prices[];
      double exit_volumes[];
      datetime exit_times[];
      ArrayResize(exit_prices, 0);
      ArrayResize(exit_volumes, 0);
      ArrayResize(exit_times, 0);
      bool deal_magic_mismatch = false;
      bool deal_symbol_mismatch = false;
      bool inout_ambiguous = false;
      int out_count = 0;
      datetime first_in_time = 0;
      datetime last_out_time = 0;
      long final_reason = -1;
      string final_symbol = symbol_hint;
      string partials_json = "[";
      int partial_count = 0;
      string all_deals_json = "[";
      int all_deal_count = 0;

      int deals = HistoryDealsTotal();
      for(int i=0; i<deals; i++){
         ulong deal_ticket = HistoryDealGetTicket(i);
         if(deal_ticket == 0) continue;
         long deal_pos_id = (long)HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
         if(position_id > 0){
            if(deal_pos_id != position_id) continue;
         } else if(StringLen(key_hint) > 0){
            continue;
         } else {
            continue;
         }

         if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber)
            deal_magic_mismatch = true;
         string deal_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
         if(StringLen(meta.symbol) > 0 && deal_symbol != meta.symbol)
            deal_symbol_mismatch = true;

         ENUM_DEAL_ENTRY entry_kind = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
         double vol = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
         double price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
         datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
         final_symbol = deal_symbol;
         double deal_profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
         double deal_commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
         double deal_swap = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
         double deal_fee = HistoryDealGetDouble(deal_ticket, DEAL_FEE);
         long deal_reason_all = HistoryDealGetInteger(deal_ticket, DEAL_REASON);
         broker_gross_price_pnl += deal_profit;
         total_commission += deal_commission;
         total_swap += deal_swap;
         total_fees += deal_fee;
         broker_net_pnl += deal_profit + deal_commission + deal_swap + deal_fee;
         if(all_deal_count > 0) all_deals_json += ",";
         all_deals_json += "{";
         all_deals_json += JsonKVStr("deal_ticket", IntegerToString((long)deal_ticket)) + ",";
         all_deals_json += JsonKVInt("time", (int)deal_time) + ",";
         all_deals_json += JsonKVStr("entry_kind", _DealEntryLabel(entry_kind)) + ",";
         all_deals_json += JsonKVStr("symbol", deal_symbol) + ",";
         all_deals_json += JsonKVNum("price", price, 8) + ",";
         all_deals_json += JsonKVNum("volume", vol, 4) + ",";
         all_deals_json += JsonKVNum("profit", deal_profit, 2) + ",";
         all_deals_json += JsonKVNum("commission", deal_commission, 2) + ",";
         all_deals_json += JsonKVNum("swap", deal_swap, 2) + ",";
         all_deals_json += JsonKVNum("fee", deal_fee, 2) + ",";
         all_deals_json += JsonKVStr("reason", _DealReasonLabel(deal_reason_all));
         all_deals_json += "}";
         all_deal_count++;

         if(entry_kind == DEAL_ENTRY_IN || entry_kind == DEAL_ENTRY_INOUT){
            in_vol += vol;
            in_value += price * vol;
            if(first_in_time == 0 || deal_time < first_in_time) first_in_time = deal_time;
            if(entry_kind == DEAL_ENTRY_INOUT) inout_ambiguous = true;
         }
         if(entry_kind == DEAL_ENTRY_OUT || entry_kind == DEAL_ENTRY_OUT_BY || entry_kind == DEAL_ENTRY_INOUT){
            long deal_reason = HistoryDealGetInteger(deal_ticket, DEAL_REASON);
            out_vol += vol;
            out_value += price * vol;
            int exit_idx = ArraySize(exit_prices);
            ArrayResize(exit_prices, exit_idx + 1);
            ArrayResize(exit_volumes, exit_idx + 1);
            ArrayResize(exit_times, exit_idx + 1);
            exit_prices[exit_idx] = price;
            exit_volumes[exit_idx] = vol;
            exit_times[exit_idx] = deal_time;
            if(partial_count > 0) partials_json += ",";
            partials_json += "{";
            partials_json += JsonKVStr("deal_ticket", IntegerToString((long)deal_ticket)) + ",";
            partials_json += JsonKVInt("time", (int)deal_time) + ",";
            partials_json += JsonKVStr("entry_kind", _DealEntryLabel(entry_kind)) + ",";
            partials_json += JsonKVNum("price", price, 8) + ",";
            partials_json += JsonKVNum("volume", vol, 4) + ",";
            partials_json += JsonKVNum("profit", deal_profit, 2) + ",";
            partials_json += JsonKVNum("commission", deal_commission, 2) + ",";
            partials_json += JsonKVNum("swap", deal_swap, 2) + ",";
            partials_json += JsonKVNum("fee", deal_fee, 2) + ",";
            partials_json += JsonKVNum("pnl", deal_profit + deal_commission + deal_swap + deal_fee, 2) + ",";
            partials_json += JsonKVStr("reason", _DealReasonLabel(deal_reason));
            partials_json += "}";
            partial_count++;
            out_count++;
            if(deal_time >= last_out_time){
               last_out_time = deal_time;
               final_reason = deal_reason;
            }
         }
      }
      partials_json += "]";
      all_deals_json += "]";

      if(out_count <= 0 || last_out_time <= 0) return false;
      double average_entry = (in_vol > 0.0 ? in_value / in_vol : meta.filled_entry);
      double ledger_tick_size = SymbolInfoDouble(meta.symbol, SYMBOL_TRADE_TICK_SIZE);
      if(ledger_tick_size <= 0.0) ledger_tick_size = SymbolInfoDouble(meta.symbol, SYMBOL_POINT);
      if(ledger_tick_size <= 0.0) ledger_tick_size = 0.00001;
      double ledger_entry_tolerance = MathMax(0.0, InpLedgerPriceTickTolerance) * ledger_tick_size;
      bool entry_price_reconciliation_mismatch = (meta.filled_entry > 0.0 && average_entry > 0.0 &&
                                                  MathAbs(meta.filled_entry - average_entry) > ledger_entry_tolerance);
      ENUM_ORDER_TYPE profit_type = (meta.is_buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      for(int e=0; e<ArraySize(exit_prices); e++){
         double calculated_profit = 0.0;
         if(OrderCalcProfit(profit_type, meta.symbol, exit_volumes[e], average_entry, exit_prices[e], calculated_profit))
            internal_gross_price_pnl += calculated_profit;
      }
      double internal_net_pnl = internal_gross_price_pnl + total_commission + total_swap + total_fees;
      double pnl_reconciliation_difference = broker_net_pnl - internal_net_pnl;
      double realized_pnl = broker_net_pnl;
      datetime reset_after = _AnalyticsResetAfter();
      if(reset_after > 0 && last_out_time <= reset_after) return false;

      if(StringLen(meta.symbol) == 0) meta.symbol = final_symbol;
      if(meta.planned_entry <= 0) meta.planned_entry = meta.entry_est;
      if(meta.planned_sl <= 0) meta.planned_sl = meta.sl;
      if(meta.planned_tp1 <= 0) meta.planned_tp1 = meta.tp1;
      if(meta.planned_tp2 <= 0) meta.planned_tp2 = meta.tp2;
      if(meta.planned_at <= 0) meta.planned_at = (meta.created_at > 0 ? meta.created_at : first_in_time);

      if(meta.filled_entry <= 0 && in_vol > 0) meta.filled_entry = in_value / in_vol;
      if(meta.filled_at <= 0) meta.filled_at = first_in_time;
      if(meta.initial_volume <= 0 && in_vol > 0) meta.initial_volume = in_vol;
      if(position_id > 0) meta.position_id = position_id;
      if(StringLen(meta.ai_decision_source) == 0){
         meta.ai_decision_source = (StringLen(meta.ai.decision_source) > 0
                                    ? meta.ai.decision_source
                                    : "legacy_decision_source_unknown_non_trading");
      }
      _PopulateDerivedPlanFields(meta);

      PenaltyState retained_penalty_state;
      bool retained_penalty_state_found = m_penalty.GetState(position_id,
                                                              meta.symbol,
                                                              retained_penalty_state);
      if(retained_penalty_state_found)
         _ApplyPenaltyStateToMeta(meta, retained_penalty_state);

      double risk_dist = _RiskDistanceForMeta(meta);
      if(risk_dist > 0 && meta.planned_entry > 0){
         meta.fill_slippage = meta.filled_entry - meta.planned_entry;
         double adverse = (meta.is_buy ? (meta.filled_entry - meta.planned_entry) : (meta.planned_entry - meta.filled_entry));
         meta.fill_slippage_r = adverse / risk_dist;
      }
      if(risk_dist > 0.0 && meta.filled_entry > 0.0){
         if(meta.mfe_price <= 0.0) meta.mfe_price = meta.filled_entry;
         if(meta.mae_price <= 0.0) meta.mae_price = meta.filled_entry;
         for(int exit_i=0; exit_i<ArraySize(exit_prices); exit_i++){
            double observed_exit = exit_prices[exit_i];
            datetime observed_at = exit_times[exit_i];
            if(meta.is_buy){
               meta.mfe_price = MathMax(meta.mfe_price, observed_exit);
               meta.mae_price = MathMin(meta.mae_price, observed_exit);
            } else {
               meta.mfe_price = MathMin(meta.mfe_price, observed_exit);
               meta.mae_price = MathMax(meta.mae_price, observed_exit);
            }
            double exit_mfe_r = (meta.is_buy
                                 ? (meta.mfe_price - meta.filled_entry)
                                 : (meta.filled_entry - meta.mfe_price)) / risk_dist;
            double exit_mae_r = (meta.is_buy
                                 ? (meta.filled_entry - meta.mae_price)
                                 : (meta.mae_price - meta.filled_entry)) / risk_dist;
            meta.mfe_r = MathMax(meta.mfe_r, MathMax(0.0, exit_mfe_r));
            meta.mae_r = MathMax(meta.mae_r, MathMax(0.0, exit_mae_r));
            if(meta.first_0_25r_time <= 0 && meta.mfe_r >= 0.25)
               meta.first_0_25r_time = observed_at;
            if(meta.first_0_50r_time <= 0 && meta.mfe_r >= 0.50)
               meta.first_0_50r_time = observed_at;
            if(meta.first_adverse_threshold_time <= 0 &&
               meta.mae_r >= MathMax(0.01, MathAbs(InpPenaltyMaeTriggerR)))
               meta.first_adverse_threshold_time = observed_at;
            if(meta.first_0_25r_time == observed_at &&
               meta.first_adverse_threshold_time == observed_at)
               meta.path_order_ambiguous = true;
            if(meta.first_0_50r_time == observed_at &&
               meta.first_adverse_threshold_time == observed_at)
               meta.path_order_ambiguous = true;
            meta.latest_observed_tick_time = MathMax(meta.latest_observed_tick_time, observed_at);
         }
         if(StringLen(meta.path_completeness_status) == 0){
            meta.path_completeness_status = "UNKNOWN";
            meta.path_observation_source = "DEAL_HISTORY_ONLY";
            meta.path_data_gap = true;
         }
         // Third publisher of the same derivation.  It used filled_at alone, without the
         // planned_at fallback the other two apply, so an unfilled-but-planned meta got a
         // different answer here than it did one function away.
         datetime ledger_opened_at = _MetaOpenedAt(meta);
         int ledger_minutes_25 = _MinutesFromOpenToStamp(ledger_opened_at, meta.first_0_25r_time);
         if(ledger_minutes_25 >= 0) meta.minutes_to_0_25r_mfe = ledger_minutes_25;
         int ledger_minutes_50 = _MinutesFromOpenToStamp(ledger_opened_at, meta.first_0_50r_time);
         if(ledger_minutes_50 >= 0) meta.minutes_to_0_50r_mfe = ledger_minutes_50;
      }

      double exit_price = (out_vol > 0 ? out_value / out_vol : 0.0);
      meta.closed_at = last_out_time;
      meta.realized_pnl = realized_pnl;
      meta.gross_price_pnl = broker_gross_price_pnl;
      meta.total_commission = total_commission;
      meta.total_swap = total_swap;
      meta.total_fees = total_fees;
      meta.actual_realized_cost = -(total_commission + total_swap + total_fees);
      double predicted_round_turn_cost = meta.estimated_cost_stressed_per_lot * MathMax(0.0, meta.initial_volume);
      meta.cost_prediction_error = meta.actual_realized_cost - predicted_round_turn_cost;
      meta.broker_net_pnl = broker_net_pnl;
      meta.internal_net_pnl = internal_net_pnl;
      meta.pnl_reconciliation_difference = pnl_reconciliation_difference;
      if(risk_dist > 0 && meta.initial_volume > 0){
         if(meta.initial_risk_money <= 0.0)
            meta.initial_risk_money = _RiskMoneyForPosition(meta.symbol, meta.is_buy, meta.initial_volume,
                                                            (meta.filled_entry > 0 ? meta.filled_entry : meta.planned_entry),
                                                            (meta.planned_sl > 0 ? meta.planned_sl : meta.sl));
         if(meta.initial_risk_money > 0) meta.realized_r = broker_net_pnl / meta.initial_risk_money;
      }
      meta.result_r_initial_risk = meta.realized_r;
      meta.result_pct_fixed_initial_balance = (InpAnalyticsVirtualBalance > 0.0
                                               ? broker_net_pnl / InpAnalyticsVirtualBalance * 100.0 : 0.0);
      meta.result_pct_equity_at_entry = (meta.account_equity_at_entry > 0.0
                                         ? broker_net_pnl / meta.account_equity_at_entry * 100.0 : 0.0);
      meta.outcome_direction_broker = (broker_net_pnl > 0.00000001 ? "positive" : (broker_net_pnl < -0.00000001 ? "negative" : "flat"));
      meta.outcome_direction_r = (meta.result_r_initial_risk > 0.00000001 ? "positive" : (meta.result_r_initial_risk < -0.00000001 ? "negative" : "flat"));
      meta.outcome_direction_equity_pct = (meta.result_pct_equity_at_entry > 0.00000001 ? "positive" : (meta.result_pct_equity_at_entry < -0.00000001 ? "negative" : "flat"));
      meta.outcome_direction_match = (meta.outcome_direction_broker == meta.outcome_direction_r &&
                                      meta.outcome_direction_broker == meta.outcome_direction_equity_pct);
      meta.outcome_reconciliation_status = (meta.outcome_direction_match ? "clean" : "quarantined");
      _Journal("[broker_pnl_reconciliation] position_id=" + IntegerToString(position_id)
               + " broker_net_pnl=" + DoubleToString(broker_net_pnl, 2)
               + " internal_net_pnl=" + DoubleToString(internal_net_pnl, 2)
               + " difference=" + DoubleToString(pnl_reconciliation_difference, 6)
               + " status=" + (MathAbs(pnl_reconciliation_difference) <= InpLedgerMoneyReconciliationTolerance ? "clean" : "quarantined")
               + " contributing_deals=" + all_deals_json);
      meta.exit_path = _ExitPathLabel(meta, exit_price, out_count, final_reason);
      double plan_rr_for_eff = _PlanRR2(meta);
      if(plan_rr_for_eff > 0.0) meta.target_efficiency = _ClampRange(meta.mfe_r / plan_rr_for_eff, 0.0, 2.0);
      meta.tp2_realistic_before_reversal = (meta.tp2_hit_at > 0 || (plan_rr_for_eff > 0.0 && meta.mfe_r >= plan_rr_for_eff * 0.90));
      meta.be_move_helped = (meta.tp1_done && StringFind(meta.exit_path, "stop") >= 0 && meta.realized_r >= 0.0);
      meta.trailing_stop_improved = false;
      meta.analytics_logged = true;
      string counterfactual_reason = "";
      bool counterfactual_ok = _ComputeOriginalPolicyCounterfactual(meta, counterfactual_reason);
      _Journal("[management_counterfactual] position_id=" + IntegerToString(position_id)
               + " actual_r=" + DoubleToString(meta.actual_managed_result_r, 6)
               + " counterfactual_r=" + DoubleToString(meta.counterfactual_original_sl_tp_result_r, 6)
               + " management_alpha=" + DoubleToString(meta.management_alpha, 6)
               + " ambiguous=" + (meta.counterfactual_ambiguous ? "true" : "false")
               + " status=" + (counterfactual_ok ? "evaluated" : (meta.counterfactual_pending ? "pending" : "excluded"))
               + " reason=" + counterfactual_reason);
      _Journal("[broker_cost_calibration] symbol=" + meta.symbol
               + " asset_class=" + meta.asset_class
               + " source=" + meta.estimated_cost_source
               + " sample_size=" + IntegerToString(meta.estimated_cost_sample_size)
               + " predicted_round_turn_cost=" + DoubleToString(predicted_round_turn_cost, 4)
               + " actual_realized_cost=" + DoubleToString(meta.actual_realized_cost, 4)
               + " prediction_error=" + DoubleToString(meta.cost_prediction_error, 4)
               + " model_version=" + meta.commission_model_version);

      string po3_subtype = _Po3Subtype(meta);
      string fvg_subtype = _FvgSubtype(meta);
      string regime_bucket = _RegimeBucket(meta);
      if(StringLen(meta.origin_quality) == 0) meta.origin_quality = _OriginQuality(meta, meta.fvg);
      if(StringLen(meta.exclusive_model_name) == 0) meta.exclusive_model_name = _ExclusiveModelName();
      double rr2 = _PlanRR2(meta);
      double effective_rr2 = _ExecutionRR2(meta);
      double liquidity_rr = _LiquidityRR(meta);
      double virtual_balance_base = MathMax(1.0, InpAnalyticsVirtualBalance);
      double result_pct_virtual = meta.result_pct_fixed_initial_balance;
      string data_integrity_status = "CLEAN";
      string data_integrity_reasons = "[";
      int integrity_count = 0;
      bool critical_integrity_failure = false;
      bool tester_bootstrap_trade = _IsTesterBootstrapPlan(meta);
      if(!meta.execution_identity_verified || meta.execution_identity_quarantined){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "execution_identity_not_verified");
         critical_integrity_failure = true;
      }
      if(meta.position_id <= 0 || meta.broker_position_identifier <= 0 || meta.position_id != meta.broker_position_identifier){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "position_identifier_invalid_or_mismatch");
         critical_integrity_failure = true;
      }
      if(StringLen(meta.trade_key) == 0){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "missing_trade_key");
         critical_integrity_failure = true;
      }
      if(!meta.candidate_hash_match || meta.candidate_hash != meta.ai_selected_candidate_hash || meta.candidate_hash != meta.executed_candidate_hash){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "candidate_hash_mismatch");
         critical_integrity_failure = true;
      }
      if(!meta.execution_fingerprint_match || StringLen(meta.final_execution_fingerprint) == 0){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "execution_fingerprint_mismatch");
         critical_integrity_failure = true;
      }
      if(meta.setup_taxonomy == UNKNOWN_UNCLASSIFIED || meta.setup_taxonomy_version != SETUP_TAXONOMY_VERSION){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "unknown_setup_taxonomy");
         critical_integrity_failure = true;
      }
      if(!tester_bootstrap_trade && meta.ai.decision_quality_tier != "FULL_STRUCTURED" && meta.ai.decision_quality_tier != "CACHE_OF_FULL_STRUCTURED"){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "degraded_ai_response_non_trading");
         critical_integrity_failure = true;
      }
      if(deal_magic_mismatch){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "deal_magic_mismatch");
         critical_integrity_failure = true;
      }
      if(deal_symbol_mismatch){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "deal_symbol_mismatch");
         critical_integrity_failure = true;
      }
      if(inout_ambiguous){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "deal_inout_accounting_ambiguous");
         critical_integrity_failure = true;
      }
      if(MathAbs(in_vol - out_vol) > MathMax(InpLedgerVolumeReconciliationTolerance,
                                             SymbolInfoDouble(meta.symbol, SYMBOL_VOLUME_STEP) * 0.51)){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "entry_exit_volume_mismatch");
         critical_integrity_failure = true;
      }
      if(MathAbs(pnl_reconciliation_difference) > InpLedgerMoneyReconciliationTolerance){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "broker_internal_pnl_mismatch");
         critical_integrity_failure = true;
      }
      if(entry_price_reconciliation_mismatch){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "entry_price_reconciliation_mismatch");
         critical_integrity_failure = true;
         _Journal("[ledger_integrity] reason=entry_price_reconciliation_mismatch"
                  + " stored=" + _FmtPrice(meta.symbol, meta.filled_entry)
                  + " deal_average=" + _FmtPrice(meta.symbol, average_entry)
                  + " tolerance_ticks=" + DoubleToString(InpLedgerPriceTickTolerance, 2));
      }
      if(meta.initial_risk_money <= 0.0 || !MathIsValidNumber(meta.result_r_initial_risk)){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "initial_risk_money_invalid");
         critical_integrity_failure = true;
      }
      if(!meta.outcome_direction_match){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "outcome_direction_mismatch");
         critical_integrity_failure = true;
      }
      if(meta.filled_at <= 0 || meta.closed_at < meta.filled_at){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "timestamp_order_invalid");
         critical_integrity_failure = true;
      }
      if(!MathIsValidNumber(meta.mfe_r) || !MathIsValidNumber(meta.mae_r) ||
         meta.mfe_r < -InpLedgerRReconciliationTolerance || meta.mae_r < -InpLedgerRReconciliationTolerance ||
         meta.mfe_r > InpLedgerMaxMfeR || meta.mae_r > InpLedgerMaxAbsMaeR)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "mfe_mae_invariant_failed");
      if(meta.account_equity_at_entry <= 0.0)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "equity_at_entry_missing");
      if(MathAbs(meta.realized_pnl) <= 0.0000001)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "zero_realized_pnl");
      if(StringLen(meta.management_version) == 0)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "management_version_missing");
      if(meta.path_completeness_status != "TICK_COMPLETE" || meta.path_data_gap)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "path_evidence_not_tick_complete");
      if(meta.path_order_ambiguous)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "path_event_order_ambiguous");
      if(meta.counterfactual_ambiguous && !meta.counterfactual_pending)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "counterfactual_path_ambiguous_or_unavailable");
      if(!tester_bootstrap_trade && (!meta.model_raw_allow || !meta.python_final_allow || !meta.mql_final_allow)){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "three_stage_decision_authority_incomplete");
         critical_integrity_failure = true;
      }
      if(tester_bootstrap_trade && !meta.mql_final_allow){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "bootstrap_mql_authority_incomplete");
         critical_integrity_failure = true;
      }
      if(!meta.broker_submission_attempted || !meta.broker_request_accepted ||
         !meta.final_execution_success){
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "broker_execution_lifecycle_incomplete");
         critical_integrity_failure = true;
      }
      if(!meta.cohort_complete)
         _AppendLedgerIntegrityReason(data_integrity_reasons, integrity_count, "cohort_metadata_incomplete");
      data_integrity_reasons += "]";
      if(critical_integrity_failure) data_integrity_status = "QUARANTINED";
      else if(integrity_count > 0) data_integrity_status = "SUSPICIOUS";
      meta.ledger_integrity_status = data_integrity_status;
      meta.ledger_integrity_reasons = data_integrity_reasons;
      meta.ledger_schema_version = TRADE_LEDGER_SCHEMA_VERSION;
      meta.attribution_status = (meta.execution_identity_verified ? "VERIFIED" : "QUARANTINED");
      meta.attribution_error = (critical_integrity_failure ? data_integrity_reasons : "");
      bool clean_eligible = (data_integrity_status == "CLEAN" && meta.execution_identity_verified &&
                             meta.candidate_hash_match && meta.execution_fingerprint_match &&
                             meta.setup_taxonomy != UNKNOWN_UNCLASSIFIED && meta.cohort_complete &&
                             ((tester_bootstrap_trade && meta.mql_final_allow) ||
                              (!tester_bootstrap_trade && meta.model_raw_allow && meta.python_final_allow && meta.mql_final_allow)) &&
                             meta.broker_submission_attempted && meta.broker_request_accepted &&
                             meta.final_execution_success);
      meta.learning_eligible = clean_eligible;
      meta.optimization_eligible = clean_eligible;
      meta.suppression_eligible = clean_eligible;
      meta.management_policy_selection_eligible = (clean_eligible && !meta.counterfactual_ambiguous &&
                                                   !meta.counterfactual_pending &&
                                                   meta.management_version == MANAGEMENT_SCHEMA_VERSION);
      if(meta.counterfactual_pending) _QueueCounterfactualEvaluation(meta);
      string j = "{";
      j += JsonKVInt("analytics_schema_version", 3) + ",";
      j += JsonKVStr("trade_key", key) + ",";
      j += JsonKVStr("broker_comment", meta.broker_comment) + ",";
      j += JsonKVStr("source_deal_comment", key_hint) + ",";
      j += JsonKVStr("decision_schema_version", meta.ai.decision_schema_version) + ",";
      j += JsonKVStr("decision_quality_tier", meta.ai.decision_quality_tier) + ",";
      j += JsonKVStr("response_quality", meta.ai.response_quality_alias) + ",";
      j += JsonKVStr("provider_contract_version", meta.ai.provider_contract_version) + ",";
      j += JsonKVStr("provider_mode", meta.ai.provider_mode) + ",";
      j += JsonKVStr("provider_id", meta.ai.provider_id) + ",";
      j += JsonKVStr("endpoint_class", meta.ai.endpoint_class) + ",";
      j += JsonKVStr("endpoint_identity_hash", meta.ai.endpoint_identity_hash) + ",";
      j += JsonKVStr("configured_models_hash", meta.ai.configured_models_hash) + ",";
      j += JsonKVStr("actual_model_id", meta.ai.actual_model_id) + ",";
      j += JsonKVStr("fallback_model", meta.ai.fallback_model) + ",";
      j += JsonKVStr("model_fingerprint", meta.ai.model_fingerprint) + ",";
      j += JsonKVStr("evidence_envelope_version", meta.ai.evidence_envelope_version) + ",";
      j += JsonKVStr("family_profile_version", meta.ai.family_profile_version) + ",";
      j += JsonKVStr("memory_schema_version", meta.ai.memory_schema_version) + ",";
      j += JsonKVStr("retrieval_policy_version", meta.ai.retrieval_policy_version) + ",";
      j += JsonKVStr("role_contract_version", meta.ai.role_contract_version) + ",";
      j += JsonKVStr("consensus_resolver_version", meta.ai.consensus_resolver_version) + ",";
      j += JsonKVStr("generation_settings_hash", meta.ai.generation_settings_hash) + ",";
      j += JsonKVStr("input_fingerprint", meta.ai.input_fingerprint) + ",";
      j += JsonKVStr("analyst_response_fingerprint", meta.ai.analyst_response_fingerprint) + ",";
      j += JsonKVStr("critic_response_fingerprint", meta.ai.critic_response_fingerprint) + ",";
      j += JsonKVStr("adjudicator_response_fingerprint", meta.ai.adjudicator_response_fingerprint) + ",";
      j += JsonKVStr("final_resolver_reason", meta.ai.final_resolver_reason) + ",";
      j += JsonKVStr("decision_state", meta.ai.decision_state) + ",";
      j += JsonKVBool("model_raw_allow", meta.model_raw_allow) + ",";
      j += JsonKVBool("python_final_allow", meta.python_final_allow) + ",";
      j += JsonKVBool("mql_final_allow", meta.mql_final_allow) + ",";
      j += JsonKVStr("candidate_id", meta.candidate_id) + ",";
      j += JsonKVStr("candidate_hash", meta.candidate_hash) + ",";
      j += JsonKVStr("ai_selected_candidate_hash", meta.ai_selected_candidate_hash) + ",";
      j += JsonKVStr("executed_candidate_hash", meta.executed_candidate_hash) + ",";
      j += JsonKVBool("candidate_hash_match", meta.candidate_hash_match) + ",";
      j += JsonKVStr("assessed_execution_fingerprint", meta.assessed_execution_fingerprint) + ",";
      j += JsonKVStr("final_execution_fingerprint", meta.final_execution_fingerprint) + ",";
      j += JsonKVBool("execution_fingerprint_match", meta.execution_fingerprint_match) + ",";
      j += JsonKVNum("result_order_ticket", (double)meta.result_order_ticket, 0) + ",";
      j += JsonKVNum("result_deal_ticket", (double)meta.result_deal_ticket, 0) + ",";
      j += JsonKVNum("broker_position_ticket", (double)meta.broker_position_ticket, 0) + ",";
      j += JsonKVNum("broker_position_identifier", (double)meta.broker_position_identifier, 0) + ",";
      j += JsonKVStr("intended_order_type", meta.intended_order_type) + ",";
      j += JsonKVStr("execution_authority_state", meta.execution_authority_state) + ",";
      j += JsonKVBool("broker_submission_attempted", meta.broker_submission_attempted) + ",";
      j += JsonKVBool("broker_request_accepted", meta.broker_request_accepted) + ",";
      j += JsonKVNum("broker_retcode", (double)meta.broker_retcode, 0) + ",";
      j += JsonKVStr("broker_retcode_description", meta.broker_retcode_description) + ",";
      j += JsonKVBool("broker_partial_fill", meta.broker_partial_fill) + ",";
      j += JsonKVBool("final_execution_success", meta.final_execution_success) + ",";
      j += JsonKVBool("execution_identity_verified", meta.execution_identity_verified) + ",";
      j += JsonKVBool("execution_identity_quarantined", meta.execution_identity_quarantined) + ",";
      j += JsonKVStr("account_position_mode", meta.account_position_mode) + ",";
      j += JsonKVStr("attribution_status", meta.attribution_status) + ",";
      j += JsonKVStr("attribution_error", meta.attribution_error) + ",";
      j += JsonKVStr("ledger_schema_version", meta.ledger_schema_version) + ",";
      j += JsonKVStr("ledger_integrity_status", meta.ledger_integrity_status) + ",";
      j += "\"ledger_integrity_reasons\":" + meta.ledger_integrity_reasons + ",";
      j += JsonKVBool("learning_eligible", meta.learning_eligible) + ",";
      j += JsonKVBool("optimization_eligible", meta.optimization_eligible) + ",";
      j += JsonKVBool("suppression_eligible", meta.suppression_eligible) + ",";
      j += JsonKVStr("data_integrity_status", data_integrity_status) + ",";
      j += "\"data_integrity_reasons\":" + data_integrity_reasons + ",";
      j += JsonKVStr("symbol", meta.symbol) + ",";
      j += JsonKVStr("direction", meta.is_buy ? "buy" : "sell") + ",";
      j += JsonKVBool("is_buy", meta.is_buy) + ",";
      j += JsonKVInt("planned_at", (int)meta.planned_at) + ",";
      j += JsonKVInt("filled_at", (int)meta.filled_at) + ",";
      j += JsonKVInt("closed_at", (int)meta.closed_at) + ",";
      j += JsonKVNum("planned_entry", meta.planned_entry, 8) + ",";
      j += JsonKVNum("planned_sl", meta.planned_sl, 8) + ",";
      j += JsonKVNum("planned_tp1", meta.planned_tp1, 8) + ",";
      j += JsonKVNum("planned_tp2", meta.planned_tp2, 8) + ",";
      j += JsonKVNum("filled_entry", meta.filled_entry, 8) + ",";
      j += JsonKVNum("exit_price", exit_price, 8) + ",";
      j += JsonKVNum("fill_slippage", meta.fill_slippage, 8) + ",";
      j += JsonKVNum("fill_slippage_r", meta.fill_slippage_r, 6) + ",";
      j += JsonKVNum("initial_volume", meta.initial_volume, 4) + ",";
      j += JsonKVNum("lot_size", meta.initial_volume, 4) + ",";
      j += JsonKVNum("position_id", (double)meta.position_id, 0) + ",";
      j += JsonKVNum("realized_pnl", meta.realized_pnl, 2) + ",";
      // Which run produced this trade.  <bus>\logs\trade_results is shared by every
      // run against the same bus, and the behavioural circuit-breaker reads the whole
      // directory, so without this a Strategy Tester replay inherits the previous
      // replay's losing streak.  See PO3SetBehaviorRuntimeScope in Risk.mqh.
      j += JsonKVStr("runtime_scope", m_state.RuntimeScope()) + ",";
      j += JsonKVNum("realized_r", meta.realized_r, 6) + ",";
      j += JsonKVNum("gross_price_pnl", meta.gross_price_pnl, 2) + ",";
      j += JsonKVNum("total_commission", meta.total_commission, 2) + ",";
      j += JsonKVNum("total_swap", meta.total_swap, 2) + ",";
      j += JsonKVNum("total_fees", meta.total_fees, 2) + ",";
      j += JsonKVNum("predicted_round_turn_cost", meta.estimated_cost_stressed_per_lot * MathMax(0.0, meta.initial_volume), 4) + ",";
      j += JsonKVNum("actual_realized_cost", meta.actual_realized_cost, 4) + ",";
      j += JsonKVNum("cost_prediction_error", meta.cost_prediction_error, 4) + ",";
      j += JsonKVStr("cost_source", meta.estimated_cost_source) + ",";
      j += JsonKVInt("cost_sample_size", meta.estimated_cost_sample_size) + ",";
      j += JsonKVStr("commission_model_version", meta.commission_model_version) + ",";
      j += JsonKVNum("broker_net_pnl", meta.broker_net_pnl, 2) + ",";
      j += JsonKVNum("internal_net_pnl", meta.internal_net_pnl, 2) + ",";
      j += JsonKVNum("pnl_reconciliation_difference", meta.pnl_reconciliation_difference, 6) + ",";
      j += JsonKVNum("account_equity_at_entry", meta.account_equity_at_entry, 2) + ",";
      j += JsonKVNum("account_balance_at_entry", meta.account_balance_at_entry, 2) + ",";
      j += JsonKVNum("initial_risk_money", meta.initial_risk_money, 2) + ",";
      j += JsonKVNum("initial_risk_pct_equity", meta.initial_risk_pct_equity, 6) + ",";
      j += JsonKVNum("result_pct_fixed_initial_balance", meta.result_pct_fixed_initial_balance, 6) + ",";
      j += JsonKVNum("result_pct_equity_at_entry", meta.result_pct_equity_at_entry, 6) + ",";
      j += JsonKVNum("result_r_initial_risk", meta.result_r_initial_risk, 6) + ",";
      j += JsonKVStr("outcome_direction_broker", meta.outcome_direction_broker) + ",";
      j += JsonKVStr("outcome_direction_r", meta.outcome_direction_r) + ",";
      j += JsonKVStr("outcome_direction_equity_pct", meta.outcome_direction_equity_pct) + ",";
      j += JsonKVBool("outcome_direction_match", meta.outcome_direction_match) + ",";
      j += JsonKVStr("outcome_reconciliation_status", meta.outcome_reconciliation_status) + ",";
      j += JsonKVNum("virtual_balance_base", virtual_balance_base, 2) + ",";
      j += JsonKVNum("result_pct_of_virtual_start", result_pct_virtual, 6) + ",";
      j += JsonKVNum("result_pct_of_virtual_balance", result_pct_virtual, 6) + ",";
      j += "\"partials\":" + partials_json + ",";
      j += "\"deals\":" + all_deals_json + ",";
      j += JsonKVNum("mfe_price", meta.mfe_price, 8) + ",";
      j += JsonKVNum("mae_price", meta.mae_price, 8) + ",";
      j += JsonKVNum("mfe_r", meta.mfe_r, 6) + ",";
      j += JsonKVNum("mae_r", meta.mae_r, 6) + ",";
      j += JsonKVInt("first_0_25r_time", (int)meta.first_0_25r_time) + ",";
      j += JsonKVInt("first_0_50r_time", (int)meta.first_0_50r_time) + ",";
      j += JsonKVInt("first_adverse_threshold_time", (int)meta.first_adverse_threshold_time) + ",";
      j += JsonKVInt("latest_observed_tick_time", (int)meta.latest_observed_tick_time) + ",";
      j += JsonKVNum("latest_observed_tick_msc", (double)meta.latest_observed_tick_msc, 0) + ",";
      j += JsonKVStr("path_completeness_status", meta.path_completeness_status) + ",";
      j += JsonKVStr("path_observation_source", meta.path_observation_source) + ",";
      j += JsonKVBool("path_data_gap", meta.path_data_gap) + ",";
      j += JsonKVBool("path_order_ambiguous", meta.path_order_ambiguous) + ",";
      j += JsonKVInt("time_to_tp1_sec", (meta.tp1_hit_at > 0 && meta.filled_at > 0 ? (int)(meta.tp1_hit_at - meta.filled_at) : -1)) + ",";
      j += JsonKVInt("time_to_tp2_sec", (meta.tp2_hit_at > 0 && meta.filled_at > 0 ? (int)(meta.tp2_hit_at - meta.filled_at) : -1)) + ",";
      j += JsonKVInt("time_to_sl_sec", (meta.sl_hit_at > 0 && meta.filled_at > 0 ? (int)(meta.sl_hit_at - meta.filled_at) : -1)) + ",";
      j += JsonKVBool("tp2_realistic_before_reversal", meta.tp2_realistic_before_reversal) + ",";
      j += JsonKVBool("be_move_helped", meta.be_move_helped) + ",";
      j += JsonKVBool("trailing_stop_improved", meta.trailing_stop_improved) + ",";
      j += JsonKVNum("target_efficiency", meta.target_efficiency, 6) + ",";
      j += JsonKVStr("exit_path", meta.exit_path) + ",";
      j += JsonKVStr("deal_reason", _DealReasonLabel(final_reason)) + ",";
      j += JsonKVStr("session_name", meta.po3.session_name) + ",";
      j += JsonKVStr("session_code", meta.session_code) + ",";
      j += JsonKVStr("killzone_name", meta.po3.killzone_name) + ",";
      j += JsonKVStr("killzone_code", meta.killzone_code) + ",";
      j += JsonKVBool("in_killzone", meta.po3.in_killzone) + ",";
      j += JsonKVStr("po3_scope", meta.po3.po3_scope) + ",";
      j += JsonKVStr("po3_subtype", po3_subtype) + ",";
      j += JsonKVStr("fvg_subtype", fvg_subtype) + ",";
      j += JsonKVStr("regime_bucket", regime_bucket) + ",";
      j += JsonKVStr("setup_family", meta.setup_family) + ",";
      j += JsonKVStr("setup_class", meta.setup_class) + ",";
      j += JsonKVStr("setup_taxonomy_version", meta.setup_taxonomy_version) + ",";
      j += JsonKVStr("setup_taxonomy_enum", meta.setup_taxonomy_enum) + ",";
      j += JsonKVStr("taxonomy_mapping_source", meta.taxonomy_mapping_source) + ",";
      j += JsonKVStr("setup_type", meta.setup_type) + ",";
      j += JsonKVStr("setup_subtype", meta.setup_subtype) + ",";
      j += JsonKVStr("setup_story_scope", meta.setup_story_scope) + ",";
      j += JsonKVStr("entry_branch", meta.entry_branch) + ",";
      j += JsonKVStr("fvg_execution_class", meta.fvg_execution_class) + ",";
      j += JsonKVStr("model_code", meta.model_code) + ",";
      j += JsonKVBool("exclusive_model_mode", meta.exclusive_model_mode) + ",";
      j += JsonKVStr("exclusive_model_name", meta.exclusive_model_name) + ",";
      j += JsonKVBool("exclusive_model_passed", meta.exclusive_model_passed) + ",";
      j += JsonKVStr("exclusive_fail_reason", meta.exclusive_fail_reason) + ",";
      j += JsonKVStr("origin_quality", meta.origin_quality) + ",";
      j += JsonKVStr("target_source", meta.target_source) + ",";
      j += JsonKVStr("volatility_profile", meta.volatility_profile) + ",";
      j += JsonKVStr("policy_bucket", meta.policy_bucket) + ",";
      j += JsonKVStr("policy_snapshot_id", meta.policy_snapshot_id) + ",";
      j += JsonKVBool("runner_trade", meta.runner_trade) + ",";
      j += JsonKVBool("runner_downgraded", meta.runner_downgraded) + ",";
      j += JsonKVStr("runner_downgrade_reason", meta.runner_downgrade_reason) + ",";
      j += JsonKVNum("original_runner_target", meta.original_runner_target, 8) + ",";
      j += JsonKVNum("standard_target_after_downgrade", meta.standard_target_after_downgrade, 8) + ",";
      j += JsonKVStr("target_model", meta.target_model) + ",";
      j += JsonKVStr("management_profile", meta.management_profile) + ",";
      j += JsonKVStr("analytics_key", meta.analytics_key) + ",";
      j += JsonKVStr("symbol_policy_action", meta.symbol_policy_action) + ",";
      j += JsonKVStr("symbol_policy_reason", meta.symbol_policy_reason) + ",";
      j += JsonKVStr("family_policy_action", meta.family_policy_action) + ",";
      j += JsonKVStr("family_policy_reason", meta.family_policy_reason) + ",";
      j += JsonKVStr("entry_miss_classification", meta.entry_miss_classification) + ",";
      j += JsonKVStr("portfolio_cluster", meta.portfolio_cluster) + ",";
      j += JsonKVStr("usd_exposure_key", meta.usd_exposure_key) + ",";
      j += JsonKVNum("portfolio_score", meta.portfolio_score, 6) + ",";
      j += JsonKVInt("portfolio_rank", meta.portfolio_rank) + ",";
      j += JsonKVStr("scheduler_action", meta.scheduler_action) + ",";
      j += JsonKVStr("scheduler_reason", meta.scheduler_reason) + ",";
      j += JsonKVNum("session_concentration", meta.session_concentration, 6) + ",";
      j += JsonKVNum("usd_concentration", meta.usd_concentration, 6) + ",";
      j += JsonKVNum("cluster_concentration", meta.cluster_concentration, 6) + ",";
      j += JsonKVNum("diversity_bonus", meta.diversity_bonus, 6) + ",";
      j += JsonKVStr("obstacle_kind", meta.obstacle_kind) + ",";
      j += JsonKVNum("obstacle_price", meta.obstacle_price, 8) + ",";
      j += JsonKVNum("obstacle_r", meta.obstacle_r, 6) + ",";
      j += JsonKVStr("ai_decision_source", meta.ai_decision_source) + ",";
      j += JsonKVStr("setup_id", meta.setup_id) + ",";
      j += JsonKVStr("fvg_id", meta.fvg_id) + ",";
      j += JsonKVStr("candidate_id", meta.candidate_id) + ",";
      j += JsonKVStr("ai_decision_id", meta.ai_decision_id) + ",";
      j += JsonKVStr("policy_version", meta.policy_version) + ",";
      j += JsonKVStr("risk_version", meta.risk_version) + ",";
      j += JsonKVStr("management_version", meta.management_version) + ",";
      j += JsonKVStr("management_state", meta.management_state) + ",";
      j += JsonKVStr("management_previous_state", meta.management_previous_state) + ",";
      j += JsonKVStr("management_transition_reason", meta.management_transition_reason) + ",";
      j += JsonKVStr("management_action_executed", meta.management_action_executed) + ",";
      j += JsonKVStr("management_action_id", meta.management_action_id) + ",";
      j += JsonKVStr("management_action_lifecycle_state", meta.management_action_lifecycle_state) + ",";
      j += JsonKVStr("management_requested_action", meta.management_requested_action) + ",";
      j += JsonKVNum("management_requested_volume", meta.management_requested_volume, 8) + ",";
      j += JsonKVNum("management_normalized_volume", meta.management_normalized_volume, 8) + ",";
      j += JsonKVNum("management_position_volume_before", meta.management_position_volume_before, 8) + ",";
      j += JsonKVNum("management_requested_cut_fraction", meta.management_requested_cut_fraction, 8) + ",";
      j += JsonKVInt("management_action_retry_count", meta.management_action_retry_count) + ",";
      j += JsonKVInt("management_next_retry_at", (int)meta.management_next_retry_at) + ",";
      j += JsonKVNum("management_last_retcode", (double)meta.management_last_retcode, 0) + ",";
      j += JsonKVStr("management_last_retcode_description", meta.management_last_retcode_description) + ",";
      j += JsonKVStr("management_action_terminal_reason", meta.management_action_terminal_reason) + ",";
      j += JsonKVStr("management_policy", meta.management_policy) + ",";
      j += JsonKVStr("invalidation_confirmation_mode", meta.invalidation_confirmation_mode) + ",";
      j += JsonKVNum("actual_managed_result", meta.actual_managed_result, 4) + ",";
      j += JsonKVNum("actual_managed_result_r", meta.actual_managed_result_r, 6) + ",";
      j += JsonKVNum("counterfactual_original_sl_tp_result", meta.counterfactual_original_sl_tp_result, 4) + ",";
      j += JsonKVNum("counterfactual_original_sl_tp_result_r", meta.counterfactual_original_sl_tp_result_r, 6) + ",";
      j += JsonKVNum("management_alpha", meta.management_alpha, 6) + ",";
      j += JsonKVBool("counterfactual_ambiguous", meta.counterfactual_ambiguous) + ",";
      j += JsonKVBool("counterfactual_pending", meta.counterfactual_pending) + ",";
      j += JsonKVStr("counterfactual_status", meta.counterfactual_status) + ",";
      j += JsonKVStr("counterfactual_resolution_reason", meta.counterfactual_resolution_reason) + ",";
      j += JsonKVInt("counterfactual_horizon_at", (int)meta.counterfactual_horizon_at) + ",";
      j += JsonKVInt("counterfactual_evaluated_at", (int)meta.counterfactual_evaluated_at) + ",";
      j += JsonKVBool("management_policy_selection_eligible", meta.management_policy_selection_eligible) + ",";
      j += JsonKVStr("lineage_root_id", meta.lineage_root_id) + ",";
      j += JsonKVStr("parent_setup_id", meta.parent_setup_id) + ",";
      j += JsonKVInt("lineage_version", meta.lineage_version) + ",";
      j += JsonKVInt("attempt_number_for_sweep", meta.attempt_number_for_sweep) + ",";
      j += JsonKVInt("source_t_sweep", (int)meta.source_t_sweep) + ",";
      j += JsonKVInt("source_t_disp", (int)meta.source_t_disp) + ",";
      j += JsonKVInt("source_t_bos", (int)meta.source_t_bos) + ",";
      j += JsonKVStr("source_context_tier", meta.source_context_tier) + ",";
      j += JsonKVStr("source_sweep_side", meta.source_sweep_side) + ",";
      j += JsonKVNum("source_manip_low", meta.source_manip_low, 8) + ",";
      j += JsonKVNum("source_manip_high", meta.source_manip_high, 8) + ",";
      j += JsonKVNum("source_dr_high", meta.source_dr_high, 8) + ",";
      j += JsonKVNum("source_dr_low", meta.source_dr_low, 8) + ",";
      j += JsonKVNum("source_liquidity_target", meta.source_liquidity_target, 8) + ",";
      j += JsonKVStr("source_liquidity_kind", meta.source_liquidity_kind) + ",";
      j += JsonKVStr("narrative_state", meta.narrative_state) + ",";
      j += JsonKVStr("superseded_by", meta.superseded_by) + ",";
      j += JsonKVStr("invalidation_cause", meta.invalidation_cause) + ",";
      j += JsonKVStr("subtype_policy_action", meta.subtype_policy_action) + ",";
      j += JsonKVNum("subtype_policy_penalty", meta.subtype_policy_penalty, 6) + ",";
      j += JsonKVNum("subtype_shrunk_win_rate", meta.subtype_shrunk_win_rate, 6) + ",";
      j += JsonKVNum("subtype_avg_r", meta.subtype_avg_r, 6) + ",";
      j += JsonKVNum("subtype_risk_multiplier", meta.subtype_risk_multiplier, 6) + ",";
      j += JsonKVNum("setup_floor_score", meta.setup_floor_score, 6) + ",";
      j += JsonKVNum("setup_floor_penalty", meta.setup_floor_penalty, 6) + ",";
      j += JsonKVStr("setup_floor_action", meta.setup_floor_action) + ",";
      j += JsonKVStr("session_weekday_policy_action", meta.session_weekday_policy_action) + ",";
      j += JsonKVNum("session_weekday_risk_multiplier", meta.session_weekday_risk_multiplier, 6) + ",";
      j += JsonKVNum("session_weekday_rr_delta", meta.session_weekday_rr_delta, 6) + ",";
      j += JsonKVNum("session_weekday_score_bias", meta.session_weekday_score_bias, 6) + ",";
      j += JsonKVStr("ote_state", meta.ote_state) + ",";
      j += JsonKVNum("ote_distance_frac", meta.ote_distance_frac, 6) + ",";
      j += JsonKVNum("ote_softness_frac", meta.ote_softness_frac, 6) + ",";
      j += JsonKVNum("setup_score", meta.setup_score, 6) + ",";
      j += JsonKVNum("rr2", rr2, 6) + ",";
      j += JsonKVNum("effective_rr2", effective_rr2, 6) + ",";
      j += JsonKVNum("liquidity_rr", liquidity_rr, 6) + ",";
      j += JsonKVNum("sequence_quality", meta.sequence_quality, 6) + ",";
      j += JsonKVNum("htf_alignment_score", meta.htf_alignment_score, 6) + ",";
      j += JsonKVNum("adverse_context_score", meta.adverse_context_score, 6) + ",";
      j += JsonKVNum("heuristic_quality_estimate_gross", meta.heuristic_quality_estimate_gross, 6) + ",";
      j += JsonKVNum("heuristic_quality_estimate", meta.heuristic_quality_estimate, 6) + ",";
      j += JsonKVNum("net_reward_after_cost_r", meta.net_reward_after_cost_r, 6) + ",";
      j += JsonKVBool("heuristic_quality_trade_authority", false) + ",";
      j += JsonKVNum("rule_score", meta.ai.rule_score, 4) + ",";
      j += JsonKVNum("llm_quality_score", meta.ai.llm_quality_score, 4) + ",";
      j += JsonKVNum("blended_legacy_score_diagnostic", meta.ai.blended_legacy_score, 4) + ",";
      j += JsonKVNum("legacy_agreement_confidence_diagnostic", meta.ai.legacy_agreement_confidence, 4) + ",";
      j += JsonKVNum("llm_self_reported_confidence", meta.ai.llm_self_reported_confidence, 4) + ",";
      j += JsonKVBool("calibration_available", meta.ai.calibration_available) + ",";
      j += "\"calibrated_win_probability\":null,";
      j += "\"expected_net_r\":null,";
      j += JsonKVStr("llm_numeric_diagnostics_authority", meta.ai.llm_numeric_diagnostics_authority) + ",";
      j += JsonKVBool("ai_veto_enabled", meta.ai.veto_enabled) + ",";
      j += JsonKVStr("ai_veto_code", meta.ai.veto_code) + ",";
      j += "\"ai_veto_evidence_fields\":" + (StringLen(meta.ai.veto_evidence_fields_json) > 0 ? meta.ai.veto_evidence_fields_json : "[]") + ",";
      j += JsonKVStr("ai_veto_reason", meta.ai.veto_reason) + ",";
      j += JsonKVBool("repeatability_required_live", meta.ai.repeatability_required_live) + ",";
      j += JsonKVStr("repeatability_status", meta.ai.repeatability_status) + ",";
      j += JsonKVStr("repeatability_group_key", meta.ai.repeatability_group_key) + ",";
      j += JsonKVStr("repeatability_authority_hash", meta.ai.repeatability_authority_hash) + ",";
      j += JsonKVStr("repeatability_artifact_state", meta.ai.repeatability_artifact_state) + ",";
      j += JsonKVStr("repeatability_rejection_code", meta.ai.repeatability_rejection_code) + ",";
      j += JsonKVNum("llm_quality_score_threshold", meta.ai.llm_quality_score_threshold, 4) + ",";
      j += JsonKVStr("llm_quality_threshold_source", meta.ai.llm_quality_threshold_source) + ",";
      j += JsonKVBool("llm_quality_threshold_passed", meta.ai.llm_quality_threshold_passed) + ",";
      j += JsonKVStr("llm_quality_reject_reason", meta.ai.llm_quality_reject_reason) + ",";
      j += JsonKVBool("global_llm_quality_as_hard_floor", meta.ai.global_llm_quality_as_hard_floor) + ",";
      j += JsonKVStr("ai_rejection_codes_json", meta.ai.rejection_codes_json) + ",";
      j += JsonKVStr("ai_narrative_state", meta.ai.narrative_state) + ",";
      j += JsonKVStr("ai_invalidation_risks_json", meta.ai.invalidation_risks_json) + ",";
      j += JsonKVStr("ai_missing_confirmations_json", meta.ai.missing_confirmations_json) + ",";
      j += JsonKVNum("ai_suggested_risk_multiplier", meta.ai.suggested_risk_multiplier, 6) + ",";
      j += JsonKVStr("ai_model_version", meta.ai.model_version) + ",";
      j += JsonKVNum("fvg_score", meta.fvg.score, 4) + ",";
      j += JsonKVNum("origin_score", meta.fvg.origin_score, 4) + ",";
      j += JsonKVNum("cleanliness_score", meta.fvg.cleanliness_score, 4) + ",";
      j += JsonKVNum("age_score", meta.fvg.age_score, 4) + ",";
      j += JsonKVNum("nesting_score", meta.fvg.nesting_score, 4) + ",";
      j += JsonKVNum("htf_overlap_score", meta.fvg.htf_overlap_score, 4) + ",";
      j += JsonKVNum("retest_score", meta.fvg.retest_score, 4) + ",";
      j += JsonKVStr("fvg_mitigation_state", meta.fvg.mitigation_state) + ",";
      j += JsonKVStr("fvg_invalidation_reason", meta.fvg.invalidation_reason) + ",";
      j += JsonKVBool("fvg_entry_invalid", meta.fvg.entry_invalid) + ",";
      j += JsonKVBool("fvg_structure_invalidated", meta.fvg.structure_invalidated) + ",";
      j += JsonKVNum("fvg_displacement_candle_score", meta.fvg.displacement_candle_score, 4) + ",";
      j += JsonKVNum("fvg_middle_candle_body_score", meta.fvg.middle_candle_body_score, 4) + ",";
      j += JsonKVNum("fvg_volume_impulse_score", meta.fvg.volume_impulse_score, 4) + ",";
      j += JsonKVNum("fvg_gap_width_atr_score", meta.fvg.gap_width_atr_score, 4) + ",";
      j += JsonKVNum("fvg_manipulation_distance_score", meta.fvg.manipulation_distance_score, 4) + ",";
      j += JsonKVNum("fvg_premium_discount_score", meta.fvg.premium_discount_score, 4) + ",";
      j += JsonKVNum("fvg_htf_nesting_score", meta.fvg.htf_nesting_score, 4) + ",";
      j += JsonKVNum("fvg_freshness_score", meta.fvg.freshness_score, 4) + ",";
      j += JsonKVNum("fvg_retest_quality_score", meta.fvg.retest_quality_score, 4) + ",";
      j += JsonKVNum("fvg_opposing_obstruction_score", meta.fvg.opposing_obstruction_score, 4) + ",";
      j += JsonKVNum("atr_pct", meta.atr_pct, 6) + ",";
      j += JsonKVNum("trend_strength", meta.trend_strength, 6) + ",";
      j += JsonKVNum("trend_slope_pct", meta.trend_slope_pct, 8) + ",";
      j += JsonKVNum("adx_value", meta.adx_value, 4) + ",";
      j += JsonKVNum("adr_pct", meta.adr_pct, 6) + ",";
      j += JsonKVNum("session_vol_ratio", meta.session_vol_ratio, 6) + ",";
      j += JsonKVNum("vwap_dist_atr", meta.vwap_dist_atr, 6) + ",";
      j += JsonKVNum("compression_score", meta.compression_score, 6) + ",";
      j += JsonKVNum("expansion_score", meta.expansion_score, 6) + ",";
      j += JsonKVNum("news_risk", meta.news_risk, 6) + ",";
      j += JsonKVNum("estimated_cost_price", meta.estimated_cost_price, 8) + ",";
      j += JsonKVNum("estimated_slippage_price", meta.estimated_slippage_price, 8) + ",";
      j += JsonKVNum("estimated_commission_money", meta.estimated_commission_money, 4) + ",";
      j += JsonKVNum("execution_cost_r", meta.execution_cost_r, 6) + ",";
      j += JsonKVNum("slippage_r", meta.slippage_r, 6) + ",";
      j += JsonKVNum("commission_r", meta.commission_r, 6) + ",";
      j += JsonKVBool("execution_cost_risk_reduced", meta.execution_cost_risk_reduced) + ",";
      j += JsonKVNum("execution_cost_risk_multiplier", meta.execution_cost_risk_multiplier, 6) + ",";
      j += JsonKVStr("stop_floor_reason", meta.stop_floor_reason) + ",";
      j += JsonKVNum("stop_floor_distance", meta.stop_floor_distance, 8) + ",";
      j += JsonKVNum("stop_noise_band", meta.stop_noise_band, 8) + ",";
      j += JsonKVNum("broker_min_stop_distance", meta.broker_min_stop_distance, 8) + ",";
      j += JsonKVBool("stop_microstructure_distortion", meta.stop_microstructure_distortion) + ",";
      j += JsonKVNum("stop_quality_score", meta.stop_quality_score, 6) + ",";
      j += JsonKVNum("realized_stop_quality", meta.realized_stop_quality, 6) + ",";
      j += JsonKVNum("realized_stop_buffer_r", meta.realized_stop_buffer_r, 6) + ",";
      j += JsonKVStr("snapshot_htf_path", meta.snapshot_htf_path) + ",";
      j += JsonKVStr("snapshot_ltf_path", meta.snapshot_ltf_path) + ",";
      j += JsonKVStr("po3_state", meta.po3.po3_state) + ",";
      j += JsonKVInt("po3_state_id", (int)meta.po3.state) + ",";
      j += JsonKVStr("po3_state_reason", meta.po3.po3_state_reason) + ",";
      j += JsonKVStr("sweep_side", meta.po3.sweep_side) + ",";
      j += JsonKVStr("structure_type", meta.po3.structure_type) + ",";
      j += JsonKVStr("htf_structure_type", meta.po3.htf_structure_type) + ",";
      j += JsonKVStr("ltf_structure_type", meta.po3.ltf_structure_type) + ",";
      j += JsonKVStr("final_setup_class", meta.po3.final_setup_class) + ",";
      j += JsonKVInt("t_sweep", (int)meta.po3.t_sweep) + ",";
      j += JsonKVInt("t_disp", (int)meta.po3.t_disp) + ",";
      j += JsonKVInt("t_bos", (int)meta.po3.t_bos) + ",";
      j += JsonKVStr("liquidity_kind", meta.po3.liquidity_kind) + ",";
      j += JsonKVInt("liquidity_cluster_count", meta.po3.liquidity_cluster_count) + ",";
      j += JsonKVBool("htf_mss", meta.po3.htf_mss) + ",";
      j += JsonKVBool("htf_choch", meta.po3.htf_choch) + ",";
      j += JsonKVBool("ltf_bos", meta.po3.ltf_bos) + ",";
      j += JsonKVBool("ltf_mss", meta.po3.ltf_mss) + ",";
      j += JsonKVBool("ltf_choch", meta.po3.ltf_choch);
      j += "}";
      m_bus.WriteText(_TradeResultPath(key), j);
      if(position_id > 0){
         string marker = "{";
         marker += JsonKVNum("position_id", (double)position_id, 0) + ",";
         marker += JsonKVStr("trade_key", key) + ",";
         marker += JsonKVStr("run_session_id", m_ai.SessionId()) + ",";
         marker += JsonKVStr("runtime_state_scope", m_state.RuntimeScope()) + ",";
         marker += JsonKVStr("ledger_schema_version", TRADE_LEDGER_SCHEMA_VERSION) + ",";
         marker += JsonKVInt("closed_at", (int)meta.closed_at);
         marker += "}";
         m_bus.WriteText(_CompletedPositionMarkerPath(position_id), marker);
      }
      _AppendCompletedAiTradeLedger(meta, partials_json, all_deals_json, result_pct_virtual, final_reason);
      _WriteTradeMeta(meta);
      _QueueAnalyticsRefreshJob(meta);
      if(retained_penalty_state_found)
         m_penalty.ForgetState(position_id, meta.symbol);
      return true;
   }

   void _FinalizeClosedTrades() {
      if(!InpAnalyticsEnable) return;
      datetime now = _NowServerOrLocal();
      datetime reset_after = _AnalyticsResetAfter();
      datetime from = now - MathMax(1, InpAnalyticsHistoryDays) * 86400;
      if(!HistorySelect(from, now)) return;

      string processed_keys[];
      ArrayResize(processed_keys, 0);

      int deals = HistoryDealsTotal();
      for(int i=0; i<deals; i++){
         ulong deal_ticket = HistoryDealGetTicket(i);
         if(deal_ticket == 0) continue;
         if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber) continue;

         ENUM_DEAL_ENTRY entry_kind = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
         if(entry_kind != DEAL_ENTRY_OUT && entry_kind != DEAL_ENTRY_OUT_BY && entry_kind != DEAL_ENTRY_INOUT) continue;
         datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
         if(reset_after > 0 && deal_time <= reset_after) continue;

         string key = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
         string symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
         long position_id = (long)HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
         if(StringLen(key) == 0 && position_id <= 0) continue;
         string dedupe = (StringLen(key) > 0 ? key : (symbol + "_" + IntegerToString(position_id)));
         if(_HasStringValue(processed_keys, dedupe)) continue;
         if(_HasOpenPositionWithKey(key)) continue;
         if(_WriteClosedTradeOutcome(key, position_id, symbol)){
            int n = ArraySize(processed_keys);
            ArrayResize(processed_keys, n+1);
            processed_keys[n] = dedupe;
         }
      }
   }

bool _PlaceMarket(const TradePlan &p, const bool ignore_symbol_pending=false, const bool force_market_only=false) {
      m_last_execution_reject_reason = "";
      m_last_execution_failure_class = "";
      m_last_execution_spread = 0.0;
      m_last_order_construction_attempted = false;
      bool tester_bootstrap = _IsTesterBootstrapPlan(p);
      bool full_structured = (p.ai.decision_quality_tier == "FULL_STRUCTURED" || p.ai.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
      if(!tester_bootstrap){
         if(!full_structured || !p.ai.mandatory_fields_complete || p.ai.decision_state != "APPROVE" ||
            !p.ai.python_final_allow || !p.ai.model_raw_allow)
            return _RejectPlacement(p, (!full_structured ? "degraded_ai_response_non_trading" : "ai_quality_schema_incomplete"));
         if(p.ai.decision_state == "ABSTAIN") return _RejectPlacement(p, "ai_abstain");
         if(p.ai.repeatability_schema_version != REPEATABILITY_SCHEMA_VERSION ||
            p.ai.hierarchical_prior_schema_version != HIERARCHICAL_PRIOR_SCHEMA_VERSION)
            return _RejectPlacement(p, "ai_quality_schema_incomplete");
         if(p.ai.repeatability_required_live &&
            (p.ai.repeatability_status != "REPEATABLE" || !p.ai.repeatability_trading_eligible))
            return _RejectPlacement(p, StringLen(p.ai.repeatability_rejection_code) > 0
                                    ? p.ai.repeatability_rejection_code
                                    : "repeatability_unavailable");
      }
      if(p.candidate_hash != p.ai_selected_candidate_hash || p.candidate_hash != p.ai.selected_candidate_hash)
         return _RejectPlacement(p, "candidate_hash_mismatch");
      if(p.request_execution_fingerprint != p.ai.request_execution_fingerprint ||
         _AssessedFingerprintFromDecision(p, p.ai) != p.assessed_execution_fingerprint)
         return _RejectPlacement(p, "execution_fingerprint_mismatch");
      string rollover_reason = "";
      if(PO3EntryBlockedByRollover(_NowServerOrLocal(), rollover_reason))
         return _RejectPlacement(p, rollover_reason);
      if(!_ExclusiveModelPreExecutionOk(p)) return false;
      if(_ShouldSkipForGlobalOpenPositions()) return _RejectPlacement(p, "another managed position is already open");
      if(m_force_one_managed_trade_per_symbol && _SymbolHasOpenPosition(p.symbol))
         return _RejectPlacement(p, "netting_mode_one_managed_trade_per_symbol");
      if(m_force_one_managed_trade_per_symbol && !ignore_symbol_pending && _SymbolHasPendingOrder(p.symbol))
         return _RejectPlacement(p, "netting_mode_one_managed_trade_per_symbol");
      if(InpSkipIfSymbolOpen && _SymbolHasOpenPosition(p.symbol)) return _RejectPlacement(p, "symbol already has an open managed position");
      if(!ignore_symbol_pending && _HasBlockingPendingOrderForPlan(p)) return _RejectPlacement(p, "symbol already has a pending managed order");
      if(_CountOpenPositions() >= InpMaxOpenPositions) return _RejectPlacement(p, "max open positions reached");
      PO3State place_state = (p.po3.state != PO3_IDLE ? p.po3.state : PO3StateFromString(p.po3.po3_state));
      if(InpRequireConfirmedPO3ForExecution && place_state != PO3_CONFIRMED)
         return _RejectPlacement(p, "po3_not_confirmed_" + (StringLen(p.po3.po3_state) > 0 ? p.po3.po3_state : "missing"));
      string sweep_reason = "";
      if(_SweepTradeCapReached(p, false, sweep_reason)) return _RejectPlacement(p, sweep_reason);


      double bid = SymbolInfoDouble(p.symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(p.symbol, SYMBOL_ASK);
      if(bid <= 0 || ask <= 0) return _RejectPlacement(p, "missing live bid/ask quote");
      double entry_px = p.is_buy ? ask : bid;
      double risk_money = CalcDesiredRiskMoney();
      if(risk_money <= 0) return _RejectPlacement(p, "risk budget resolved to zero");

      double planned_risk = MathAbs(p.entry_est - p.sl);
      if(planned_risk <= 0) return _RejectPlacement(p, "planned risk distance is invalid");

      string spread_reject_reason = "";
      if(!_SpreadWithinLimits(p, bid, ask, planned_risk, spread_reject_reason)){
         m_last_execution_spread = ask - bid;
         m_last_execution_failure_class = _ClassifySpreadFailure(p, m_last_execution_spread);
         return _RejectPlacement(p, spread_reject_reason);
      }

      double adverse_drift = (p.is_buy ? entry_px - p.entry_est : p.entry_est - entry_px);
      double point = SymbolInfoDouble(p.symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double max_drift_r = (_IsMicroFamily(p) ? MathMax(InpMaxEntryDriftR, InpMicroMarketEntryToleranceR) : InpMaxEntryDriftR);
      double max_adverse_drift = MathMax(planned_risk * max_drift_r, point * PO3EffectiveMinFvgWidthTicks());
      bool hard_drift_exceeded = (adverse_drift > max_adverse_drift);

      double target_risk = risk_money;
      double rem = 0.0;
      if(InpMaxTotalRiskEnable){
         HasCapacityForNewTrade(0.0, rem);
         if(rem <= 0.0){
            _Journal("[portfolio_risk_block] reason=max_total_risk_reached open_risk_pct=unknown cap=" + DoubleToString(InpMaxTotalRiskMoney, 2));
            return _RejectPlacement(p, "portfolio risk capacity exhausted");
         }
         target_risk = MathMin(target_risk, rem);
      }
      if(p.subtype_risk_multiplier <= 0.0 || p.active_policy_risk_multiplier <= 0.0 || p.bucket_policy_risk_multiplier <= 0.0 ||
         p.ai.suggested_risk_multiplier <= 0.0 ||
         (p.execution_cost_risk_reduced && p.execution_cost_risk_multiplier <= 0.0))
         return _RejectPlacement(p, "resolved_risk_multiplier_zero");
      if(p.subtype_risk_multiplier > 1.0 || p.active_policy_risk_multiplier > 1.0 || p.bucket_policy_risk_multiplier > 1.0 ||
         p.ai.suggested_risk_multiplier > 1.0 || p.execution_cost_risk_multiplier > 1.0)
         return _RejectPlacement(p, "resolved_risk_multiplier_invalid");
      double risk_mult = p.subtype_risk_multiplier * p.active_policy_risk_multiplier * p.bucket_policy_risk_multiplier * p.ai.suggested_risk_multiplier;
      if(p.execution_cost_risk_reduced) risk_mult *= p.execution_cost_risk_multiplier;
      if(risk_mult <= 0.0) return _RejectPlacement(p, "resolved_risk_multiplier_zero");
      target_risk *= risk_mult;

      string trade_key = _MakeTradeKey(p);
      double market_tol_r = MathMax(InpMarketEntryToleranceR, InpEntryZoneToleranceR);
      if(_IsMicroFamily(p)) market_tol_r = MathMax(market_tol_r, InpMicroMarketEntryToleranceR);
      double market_tol = planned_risk * market_tol_r;
      bool market_ok = (adverse_drift <= market_tol);
      if(market_ok){
          TradePlan live = p;
          live.trade_key = trade_key;
         string live_session_reason = "";
         if(!_ApplyBrokerSessionEntryGate(live, live_session_reason))
            return _RejectPlacement(p, "broker_session_gate:" + live_session_reason);
         string live_price_reason = "";
         if(!_BuildPlanPrices(live, entry_px, live_price_reason))
            return _RejectPlacement(p, "failed_to_rebuild_live_plan_prices:" + live_price_reason);
         _PopulateDerivedPlanFields(live);
         live.setup_score = _SetupScore(live);
         string subtype_reason = "";
         if(!_ApplySubtypeEvidence(live, subtype_reason))
            return _RejectPlacement(p, "live subtype policy " + subtype_reason);
         string context_reason = "";
         if(!_ApplyContextPolicy(live, context_reason))
            return _RejectPlacement(p, "live context policy " + context_reason);
         string session_weekday_reason = "";
         if(!_ApplySessionWeekdayPolicy(live, session_weekday_reason))
            return _RejectPlacement(p, "live session-weekday policy " + session_weekday_reason);
         _PopulateDerivedPlanFields(live);
         string floor_reason = "";
         if(!_ApplyPreAiSetupFloor(live, floor_reason))
            return _RejectPlacement(p, "live pre-ai floor " + floor_reason);
         _PopulateDerivedPlanFields(live);
         string ote_reason = "";
         if(!_OteSoftGate(live, ote_reason))
            return _RejectPlacement(p, "live entry OTE " + ote_reason);
         string live_rule_reason = "";
         if(!_DeterministicExecutionGate(live, live_rule_reason))
            return _RejectPlacement(p, "live deterministic gate failed " + live_rule_reason);
         string live_target_validation_reason = "";
         string live_target_validation_detail = "";
         if(!ValidateAiChosenTargetBeforeWatchlist(live, live_target_validation_reason, live_target_validation_detail))
            return _RejectPlacement(p, "ai_target_validation:" + live_target_validation_reason + " " + live_target_validation_detail);
         double live_rr2 = _ExecutionRR2(live);
         if(!_StopsDistanceOk(p.symbol, p.is_buy, entry_px, live.sl, live.tp2))
            return _RejectPlacement(p, "broker stop/freeze distance invalid for market execution");

         double vol = CalcVolumeForRisk(p.symbol, p.is_buy, entry_px, live.sl, target_risk);
         if(vol <= 0) return _RejectPlacement(p, "volume for risk resolved to zero");
         double new_risk = _RiskMoneyForPosition(p.symbol, p.is_buy, vol, entry_px, live.sl);
         if(new_risk <= 0) return _RejectPlacement(p, "could not evaluate live risk for order");
         double governance_proposed_risk = new_risk;
         if(ignore_symbol_pending)
            governance_proposed_risk = MathMax(0.0, new_risk - _MatchingPendingRiskForReplacement(live));
         string live_portfolio_reason = "";
         if(!_ApplyFinalPortfolioRiskGovernance(live, governance_proposed_risk, live_portfolio_reason))
            return _RejectPlacement(p, "portfolio_initial_risk_gate:" + live_portfolio_reason);
         string corr_reason = "";
         if(!_CorrelatedExposureOk(live, new_risk, corr_reason))
            return _RejectPlacement(p, corr_reason);
         string live_hard_safety_reason = "";
         if(!CanPlaceOrderHardSafety(live, live_hard_safety_reason))
            return _RejectPlacement(p, "final hard safety " + live_hard_safety_reason);
         live.executed_candidate_hash = live.candidate_hash;
         string live_fingerprint_changes = "";
         if(!_ExecutionFingerprintWithinTolerance(live, live_fingerprint_changes)){
            _Journal("[execution_fingerprint] match=false stage=market changed_components=" + live_fingerprint_changes
                     + " assessed=" + live.assessed_execution_fingerprint
                     + " final=" + live.final_execution_fingerprint);
            return _RejectPlacement(p, "execution_fingerprint_mismatch:" + live_fingerprint_changes);
         }

         datetime market_entry_time = _NowServerOrLocal();
         _ApplyEntryTimeCommentIdentity(live, market_entry_time);
         string trade_comment = live.broker_comment;

         live.planned_entry = p.entry_est;
         live.planned_sl = live.sl;
         live.planned_tp1 = live.tp1;
         live.planned_tp2 = live.tp2;
         live.planned_at = market_entry_time;
         live.initial_volume = vol;
         _CaptureEntryRiskContext(live, vol, entry_px, live.sl);
         live.narrative_state = "pre_submission_eligible";
         live.model_raw_allow = live.ai.model_raw_allow;
         live.python_final_allow = live.ai.python_final_allow;
         live.intended_order_type = (p.is_buy ? "MARKET_BUY" : "MARKET_SELL");
         live.broker_submission_attempted = false;
         live.broker_request_accepted = false;
         live.broker_retcode = 0;
         live.broker_retcode_description = "";
         live.broker_partial_fill = false;
         live.final_execution_success = false;
         live.execution_identity_verified = false;
         live.execution_identity_quarantined = false;
         _SetExecutionAuthority(live, "MQL_PRE_SUBMISSION_ELIGIBLE", false,
                                "all_final_market_execution_gates_passed_broker_not_yet_called");
         live.tp1_done = false;
         live.account_position_mode = m_account_position_mode;
         _WriteTradeMeta(live);
         _Journal("[decision_authority] model_raw_allow=" + (live.model_raw_allow ? "true" : "false")
                  + " python_final_allow=" + (live.python_final_allow ? "true" : "false")
                  + " mql_final_allow=false authority_state=MQL_PRE_SUBMISSION_ELIGIBLE python_reasons=" + live.python_decision_reasons
                  + " mql_reasons=" + live.mql_decision_reasons);
         _WriteShadowDecisionUpdate(live, live.ai, "mql_pre_submission_eligible",
                                    live.mql_decision_reasons, false);

         bool ok = false;
         m_funnel_market_entries_attempted++;
         m_last_order_construction_attempted = true;
         live.broker_submission_attempted = true;
         live.narrative_state = "broker_submission_attempted";
         _SetExecutionAuthority(live, "BROKER_SUBMISSION_ATTEMPTED", false,
                                "market_order_method_invoked");
         if(p.is_buy) ok = m_trade.Buy(vol, p.symbol, 0.0, live.sl, live.tp2, trade_comment);
         else         ok = m_trade.Sell(vol, p.symbol, 0.0, live.sl, live.tp2, trade_comment);

         uint market_retcode = m_trade.ResultRetcode();
         live.broker_retcode = (long)market_retcode;
         live.broker_retcode_description = m_trade.ResultRetcodeDescription();
         live.result_order_ticket = m_trade.ResultOrder();
         live.result_deal_ticket = m_trade.ResultDeal();
         live.broker_partial_fill = (market_retcode == TRADE_RETCODE_DONE_PARTIAL);
         live.broker_request_accepted = (ok && _BrokerRetcodeAccepted(market_retcode));
         if(!live.broker_request_accepted){
            live.narrative_state = "broker_request_rejected";
            _SetExecutionAuthority(live, "BROKER_REQUEST_REJECTED", false,
                                   "market_order_rejected:" + _TradeRetcodeText());
            _WriteTradeMeta(live, live.result_order_ticket);
            return _RejectPlacement(live, "market order rejected: " + _TradeRetcodeText());
         }
         live.narrative_state = "broker_request_accepted";
         _SetExecutionAuthority(live, "BROKER_REQUEST_ACCEPTED", true,
                                "market_order_accepted_by_broker");
         _WriteTradeMeta(live, live.result_order_ticket);
         _WriteShadowDecisionUpdate(live, live.ai, "mql_market_broker_accepted",
                                    live.mql_decision_reasons, true);
         _Journal("[decision_authority] model_raw_allow=" + (live.model_raw_allow ? "true" : "false")
                  + " python_final_allow=" + (live.python_final_allow ? "true" : "false")
                  + " mql_final_allow=true authority_state=BROKER_REQUEST_ACCEPTED"
                  + " python_reasons=" + live.python_decision_reasons
                  + " mql_reasons=" + live.mql_decision_reasons);
         m_funnel_trades_opened++;
         m_total_trades_opened++;
         m_total_orders_placed++;
         _RememberConsumedSweep(live);

         ulong result_order = live.result_order_ticket;
         ulong result_deal = live.result_deal_ticket;
         double accepted_volume = (live.broker_partial_fill && m_trade.ResultVolume() > 0.0
                                   ? m_trade.ResultVolume() : vol);
         string identity_reason = "";
         bool identity_ok = _ResolveExactExecutionIdentity(live, result_order, result_deal, vol,
                                                            identity_reason, accepted_volume);
         if(!identity_ok && _ExecutionIdentityFailureIsSettlementPending(identity_reason)){
            // The broker has accepted, but the terminal has not finished registering
            // the execution.  Wait for the entry deal instead of recording a
            // contradiction that does not exist yet; OnTradeTransaction runs the same
            // resolution and is the authority on the answer.
            live.filled_entry = (m_trade.ResultPrice() > 0.0 ? m_trade.ResultPrice() : entry_px);
            live.filled_at = _NowServerOrLocal();
            live.final_execution_success = false;
            live.execution_identity_verified = false;
            live.execution_identity_quarantined = false;
            live.execution_identity_reason = "settlement_pending:" + identity_reason;
            live.attribution_status = "PENDING_SETTLEMENT";
            live.learning_eligible = false;
            live.optimization_eligible = false;
            live.suppression_eligible = false;
            live.execution_authority_state = "BROKER_ACCEPTED_IDENTITY_PENDING";
            live.narrative_state = "broker_accepted_identity_pending";
            _WriteTradeMeta(live, result_order);
            _Journal("[execution_identity_deferred] order_ticket=" + IntegerToString((long)result_order)
                     + " deal_ticket=" + IntegerToString((long)result_deal)
                     + " candidate_hash=" + live.candidate_hash
                     + " reason=" + identity_reason
                     + " resolver=on_trade_transaction_entry_deal"
                     + " action=await_settlement_not_quarantined");
            _SetExecutionAuthority(live, "BROKER_ACCEPTED_IDENTITY_PENDING", true,
                                   "broker_accepted_identity_not_yet_settled:" + identity_reason);
         } else if(!identity_ok){
            live.filled_entry = (m_trade.ResultPrice() > 0.0 ? m_trade.ResultPrice() : entry_px);
            live.filled_at = _NowServerOrLocal();
            live.final_execution_success = false;
            live.execution_authority_state = "BROKER_ACCEPTED_IDENTITY_QUARANTINED";
            live.narrative_state = "broker_accepted_identity_quarantined";
            _QuarantineExecutionIdentity(live, identity_reason, result_order, result_deal);
            _SetExecutionAuthority(live, "BROKER_ACCEPTED_IDENTITY_QUARANTINED", true,
                                   "broker_accepted_but_exact_identity_failed:" + identity_reason);
         } else {
            live.final_execution_success = true;
            live.narrative_state = (live.broker_partial_fill ? "executed_partial_fill" : "executed");
            _SetExecutionAuthority(live,
                                   live.broker_partial_fill ? "POSITION_PARTIALLY_FILLED_IDENTITY_VERIFIED"
                                                            : "EXECUTION_IDENTITY_VERIFIED",
                                   true,
                                   live.broker_partial_fill ? "partial_fill_exact_identity_verified"
                                                            : "market_fill_exact_identity_verified");
         }
         ulong pos_ticket = (identity_ok ? live.broker_position_ticket : 0);
         if(identity_ok)
            _UpdateAnalyticsSnapshot(live, pos_ticket, (p.is_buy ? bid : ask), vol);
         _WriteTradeMeta(live, (identity_ok ? live.broker_position_ticket : result_order));
          _Journal(p.symbol + " market entry placed key=" + trade_key
                   + " comment=" + trade_comment
                   + " entry_session=" + live.session_code
                   + " entry_killzone=" + live.killzone_code
                   + " entry_time=" + TimeToString(market_entry_time, TIME_DATE|TIME_MINUTES)
                   + " vol=" + DoubleToString(vol, 2)
                   + " planned=" + _FmtPrice(p.symbol, p.entry_est)
                   + " filled=" + _FmtPrice(p.symbol, live.filled_entry)
                   + " sl=" + _FmtPrice(p.symbol, live.sl)
                   + " tp2=" + _FmtPrice(p.symbol, live.tp2)
                   + " rr2=" + DoubleToString(live_rr2, 2));
          return true;
       }

      if(hard_drift_exceeded){
         _Journal(p.symbol + " market entry bypassed: drift above hard limit planned="
                  + _FmtPrice(p.symbol, p.entry_est)
                  + " live=" + _FmtPrice(p.symbol, entry_px)
                  + " -> trying pending entry");
      }

      if(force_market_only)
         return _RejectPlacement(p, "entry_zone_market_not_available");

      double pending_entry = p.entry_est;
      TradePlan pending = p;
      pending.trade_key = trade_key;
      string pending_session_reason = "";
      if(!_ApplyBrokerSessionEntryGate(pending, pending_session_reason))
         return _RejectPlacement(p, "broker_session_gate:" + pending_session_reason);
      string pending_price_reason = "";
      if(!_BuildPlanPrices(pending, pending_entry, pending_price_reason))
         return _RejectPlacement(p, "failed_to_rebuild_pending_entry_plan_prices:" + pending_price_reason);
      _PopulateDerivedPlanFields(pending);
      pending.setup_score = _SetupScore(pending);
      string pending_subtype_reason = "";
      if(!_ApplySubtypeEvidence(pending, pending_subtype_reason))
         return _RejectPlacement(p, "pending subtype policy " + pending_subtype_reason);
      string pending_context_reason = "";
      if(!_ApplyContextPolicy(pending, pending_context_reason))
         return _RejectPlacement(p, "pending context policy " + pending_context_reason);
      string pending_session_weekday_reason = "";
      if(!_ApplySessionWeekdayPolicy(pending, pending_session_weekday_reason))
         return _RejectPlacement(p, "pending session-weekday policy " + pending_session_weekday_reason);
      _PopulateDerivedPlanFields(pending);
      string pending_floor_reason = "";
      if(!_ApplyPreAiSetupFloor(pending, pending_floor_reason))
         return _RejectPlacement(p, "pending pre-ai floor " + pending_floor_reason);
      _PopulateDerivedPlanFields(pending);
      string pending_ote_reason = "";
      if(!_OteSoftGate(pending, pending_ote_reason))
         return _RejectPlacement(p, "pending OTE " + pending_ote_reason);
      string pending_rule_reason = "";
      if(!_DeterministicExecutionGate(pending, pending_rule_reason))
         return _RejectPlacement(p, "pending deterministic gate failed " + pending_rule_reason);
      string pending_target_validation_reason = "";
      string pending_target_validation_detail = "";
      if(!ValidateAiChosenTargetBeforeWatchlist(pending, pending_target_validation_reason, pending_target_validation_detail))
         return _RejectPlacement(p, "ai_target_validation:" + pending_target_validation_reason + " " + pending_target_validation_detail);
      double pending_rr2 = _ExecutionRR2(pending);
      if(!_StopsDistanceOk(p.symbol, p.is_buy, pending_entry, pending.sl, pending.tp2))
         return _RejectPlacement(p, "broker stop/freeze distance invalid for pending limit");
      string pending_target_reason = "";
      if(_PlanTargetAlreadyReached(pending, pending_target_reason))
         return _RejectPlacement(pending, "pending " + pending_target_reason + " before order send");

      double market_ref = (p.is_buy ? ask : bid);
      if(MathAbs(market_ref - pending_entry) < _BrokerBufferPrice(p.symbol))
         return _RejectPlacement(p, "live price too close to pending entry for broker buffer");

      double vol = CalcVolumeForRisk(p.symbol, p.is_buy, pending_entry, pending.sl, target_risk);
      if(vol <= 0) return _RejectPlacement(p, "pending volume for risk resolved to zero");
      double new_risk = _RiskMoneyForPosition(p.symbol, p.is_buy, vol, pending_entry, pending.sl);
      if(new_risk <= 0) return _RejectPlacement(p, "could not evaluate pending risk for order");
      string pending_portfolio_reason = "";
      if(!_ApplyFinalPortfolioRiskGovernance(pending, new_risk, pending_portfolio_reason))
         return _RejectPlacement(p, "portfolio_initial_risk_gate:" + pending_portfolio_reason);
      string pending_corr_reason = "";
      if(!_CorrelatedExposureOk(pending, new_risk, pending_corr_reason))
         return _RejectPlacement(p, pending_corr_reason);
      string pending_hard_safety_reason = "";
      if(!CanPlaceOrderHardSafety(pending, pending_hard_safety_reason))
         return _RejectPlacement(p, "final hard safety " + pending_hard_safety_reason);
      pending.executed_candidate_hash = pending.candidate_hash;
      string pending_fingerprint_changes = "";
      if(!_ExecutionFingerprintWithinTolerance(pending, pending_fingerprint_changes)){
         _Journal("[execution_fingerprint] match=false stage=pending changed_components=" + pending_fingerprint_changes
                  + " assessed=" + pending.assessed_execution_fingerprint
                  + " final=" + pending.final_execution_fingerprint);
         return _RejectPlacement(p, "execution_fingerprint_mismatch:" + pending_fingerprint_changes);
      }

      datetime pending_entry_time = _NowServerOrLocal();
      _ApplyEntryTimeCommentIdentity(pending, pending_entry_time);
      string trade_comment = pending.broker_comment;

      datetime expiry = TimeTradeServer();
      if(expiry <= 0) expiry = TimeLocal();
      int expiry_minutes = _PendingExpiryMinutes(pending.htf);
      expiry += expiry_minutes * 60;

      bool ok = false;
      pending.model_raw_allow = pending.ai.model_raw_allow;
      pending.python_final_allow = pending.ai.python_final_allow;
      pending.intended_order_type = (p.is_buy ? "BUY_LIMIT" : "SELL_LIMIT");
      pending.broker_submission_attempted = false;
      pending.broker_request_accepted = false;
      pending.broker_retcode = 0;
      pending.broker_retcode_description = "";
      pending.broker_partial_fill = false;
      pending.final_execution_success = false;
      pending.execution_identity_verified = false;
      pending.execution_identity_quarantined = false;
      pending.narrative_state = "pre_submission_eligible";
      _SetExecutionAuthority(pending, "MQL_PRE_SUBMISSION_ELIGIBLE", false,
                             "all_final_pending_execution_gates_passed_broker_not_yet_called");
      _Journal("[decision_authority] model_raw_allow=" + (pending.model_raw_allow ? "true" : "false")
               + " python_final_allow=" + (pending.python_final_allow ? "true" : "false")
               + " mql_final_allow=false authority_state=MQL_PRE_SUBMISSION_ELIGIBLE python_reasons=" + pending.python_decision_reasons
               + " mql_reasons=" + pending.mql_decision_reasons);
      _WriteTradeMeta(pending);
      _WriteShadowDecisionUpdate(pending, pending.ai, "mql_pre_submission_eligible",
                                 pending.mql_decision_reasons, false);
      m_funnel_pending_entries_attempted++;
      pending.broker_submission_attempted = true;
      pending.narrative_state = "broker_submission_attempted";
      _SetExecutionAuthority(pending, "BROKER_SUBMISSION_ATTEMPTED", false,
                             "pending_order_method_invoked");
      if(p.is_buy) ok = m_trade.BuyLimit(vol, pending_entry, p.symbol, pending.sl, pending.tp2, ORDER_TIME_SPECIFIED, expiry, trade_comment);
      else         ok = m_trade.SellLimit(vol, pending_entry, p.symbol, pending.sl, pending.tp2, ORDER_TIME_SPECIFIED, expiry, trade_comment);

      uint pending_retcode = m_trade.ResultRetcode();
      pending.broker_retcode = (long)pending_retcode;
      pending.broker_retcode_description = m_trade.ResultRetcodeDescription();
      pending.result_order_ticket = m_trade.ResultOrder();
      pending.result_deal_ticket = m_trade.ResultDeal();
      pending.broker_partial_fill = (pending_retcode == TRADE_RETCODE_DONE_PARTIAL);
      pending.broker_request_accepted = (ok && _BrokerRetcodeAccepted(pending_retcode));
      if(!pending.broker_request_accepted){
         pending.narrative_state = "broker_request_rejected";
         _SetExecutionAuthority(pending, "BROKER_REQUEST_REJECTED", false,
                                "pending_order_rejected:" + _TradeRetcodeText());
         _WriteTradeMeta(pending, pending.result_order_ticket);
         return _RejectPlacement(pending, "pending order rejected: " + _TradeRetcodeText());
      }
      m_funnel_pending_orders_placed++;
      m_total_orders_placed++;

      pending.planned_entry = pending_entry;
      pending.planned_sl = pending.sl;
      pending.planned_tp1 = pending.tp1;
      pending.planned_tp2 = pending.tp2;
      pending.planned_at = _NowServerOrLocal();
      pending.initial_volume = vol;
      _CaptureEntryRiskContext(pending, vol, pending_entry, pending.sl);
      pending.narrative_state = "pending_order";
      pending.tp1_done = false;
      pending.bars_waited = 0;
      pending.last_confirm_bar_time = _LastClosedBarTime(pending.symbol, pending.confirm_tf);
      ulong order_ticket = pending.result_order_ticket;
      pending.result_order_ticket = order_ticket;
      pending.account_position_mode = m_account_position_mode;
      pending.execution_identity_verified = false;
      pending.execution_identity_quarantined = false;
      pending.execution_identity_reason = "pending_order_awaiting_fill";
      pending.final_execution_success = false;
      _SetExecutionAuthority(pending, "ORDER_ACCEPTED_PENDING", true,
                             "pending_order_accepted_awaiting_fill");
      if(order_ticket == 0){
         pending.narrative_state = "pending_order_identity_unavailable";
         pending.execution_authority_state = "ORDER_ACCEPTED_IDENTITY_QUARANTINED";
         _QuarantineExecutionIdentity(pending, "missing_result_order_ticket_after_pending_submit", 0, 0);
         _SetExecutionAuthority(pending, "ORDER_ACCEPTED_IDENTITY_QUARANTINED", true,
                                "pending_order_accepted_missing_order_identity");
      }
      _WriteTradeMeta(pending, order_ticket);
      _WriteShadowDecisionUpdate(pending, pending.ai, "mql_pending_order_accepted",
                                 pending.mql_decision_reasons, true);
      _Journal("[decision_authority] model_raw_allow=" + (pending.model_raw_allow ? "true" : "false")
               + " python_final_allow=" + (pending.python_final_allow ? "true" : "false")
               + " mql_final_allow=true authority_state=" + pending.execution_authority_state
               + " python_reasons=" + pending.python_decision_reasons
               + " mql_reasons=" + pending.mql_decision_reasons);
      _Journal(p.symbol + " pending limit placed key=" + trade_key
               + " comment=" + trade_comment
               + " entry_session=" + pending.session_code
               + " entry_killzone=" + pending.killzone_code
               + " entry_time=" + TimeToString(pending_entry_time, TIME_DATE|TIME_MINUTES)
               + " vol=" + DoubleToString(vol, 2)
               + " entry=" + _FmtPrice(p.symbol, pending_entry)
               + " sl=" + _FmtPrice(p.symbol, pending.sl)
               + " tp2=" + _FmtPrice(p.symbol, pending.tp2)
               + " rr2=" + DoubleToString(pending_rr2, 2)
               + " expiry_minutes=" + IntegerToString(expiry_minutes)
               + " expiry=" + TimeToString(expiry, TIME_DATE|TIME_MINUTES));
      return true;
   }

   bool _CandleConfirmOk(const string symbol, const TradePlan &p) {
      if(!InpWaitCandleConfirm) return true;
      MqlRates r[];
      ArraySetAsSeries(r, true);
      int got = CopyRates(symbol, p.confirm_tf, 0, 3, r);
      if(got < 3) return false;
      MqlRates c = r[1]; // last closed
      MqlRates prev = r[2];
      double body_frac = CandleBodyFrac(c);
      double min_body_frac = InpConfirmBodyFracMin;
      double close_near_ext = InpConfirmCloseNearExt;
      if(_FamilyGroup(p) == "continuation"){
         min_body_frac = MathMax(0.45, InpConfirmBodyFracMin - 0.10);
         close_near_ext = MathMin(0.55, InpConfirmCloseNearExt + 0.10);
      } else if(_FamilyGroup(p) == "edge"){
         min_body_frac = MathMax(0.50, InpConfirmBodyFracMin - 0.05);
      } else if(p.fvg_execution_class == "mid_mitigated_fvg" || p.fvg_execution_class == "mid_mitigated_reentry"){
         min_body_frac = MathMin(0.80, InpConfirmBodyFracMin + 0.08);
         close_near_ext = MathMax(0.25, InpConfirmCloseNearExt - 0.08);
      }
      if(body_frac < min_body_frac) return false;

      // close near extreme: use fraction distance from extreme (lower is better)
      bool want_close_near_high = p.is_buy;
      double near = CloseNearExtremeFrac(c, want_close_near_high);
      if(near > close_near_ext) return false;

      // direction match
      if(p.is_buy && c.close <= c.open) return false;
      if(!p.is_buy && c.close >= c.open) return false;
      bool break_prev = (p.is_buy ? c.close > prev.high : c.close < prev.low);
      bool engulf_prev = (p.is_buy ? (c.close > prev.open && c.open <= prev.close)
                                   : (c.close < prev.open && c.open >= prev.close));
      if(!break_prev && !engulf_prev) return false;
      return true;
   }

   bool _B50Ok(const string symbol, const TradePlan &p) {
      if(!InpWaitB50OnM1) return true;
      string group = _FamilyGroup(p);
      if(group == "continuation" || group == "edge" || group == "range" || group == "failed_breakout") return true;
      // Approximate "B50" retracement using PO3 swing range midpoint
      double a = (p.is_buy ? p.po3.manip_low : p.po3.bos_level);
      double b = (p.is_buy ? p.po3.bos_level : p.po3.manip_high);
      if(a<=0 || b<=0) return true; // if unknown, don't block
      double b50 = (a+b)/2.0;
      double px = SymbolInfoDouble(symbol, p.is_buy ? SYMBOL_BID : SYMBOL_ASK);

      // Long wants price <= B50 before entry; short wants price >= B50
      if(p.is_buy) return (px <= b50);
      else return (px >= b50);
   }

   bool _MidMitigationOk(const string symbol, const TradePlan &p) {
      if(!InpRequireFvgMidMitigation) return true;
      if(p.fvg_execution_class == "continuation_reentry" || _FamilyGroup(p) == "edge" ||
         p.entry_branch == "nested_htf_ltf_fvg" || p.entry_branch == "session_reentry")
         return true;
      return m_fvg.MidTouched(symbol, p.ltf, p.fvg);
   }

   bool _SameFvgZone(const FVGZone &a, const FVGZone &b, const string symbol) const {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      return (a.t_form == b.t_form &&
              MathAbs(a.lower - b.lower) <= point * 2.0 &&
              MathAbs(a.upper - b.upper) <= point * 2.0);
   }

   bool _TrySameStoryNewEntry(TradePlan &p, const string invalid_reason) {
      if(!InpAllowSameStoryNewEntry) return false;
      bool has_bound_ai_assessment = (StringLen(p.ai_decision_source) > 0 ||
                                      StringLen(p.ai_decision_id) > 0 ||
                                      StringLen(p.ai_selected_candidate_hash) > 0 ||
                                      StringLen(p.ai.selected_candidate_hash) > 0 ||
                                      StringLen(p.assessed_execution_fingerprint) > 0);
      if(has_bound_ai_assessment){
         _LogSetupReject(p.symbol, "decision_integrity",
                         "alternative_candidate_requires_independent_ai_assessment",
                         "candidate_id=" + p.candidate_id
                         + " candidate_hash=" + p.candidate_hash
                         + " ai_selected_candidate_hash=" + p.ai_selected_candidate_hash
                         + " invalid_reason=" + invalid_reason
                         + " action=reject_original_no_ai_assessment_transfer");
         _Journal(p.symbol + " same-story branch replacement blocked: assessed candidate cannot transfer AI authority"
                  + " candidate_hash=" + p.candidate_hash
                  + " invalid_reason=" + invalid_reason);
         return false;
      }
      int attempts = MathMax(1, p.attempt_number_for_sweep);
      if(attempts >= MathMax(1, InpMaxSameStoryEntryAttempts)){
         _Journal(p.symbol + " same-story branch replacement blocked max_attempts="
                  + IntegerToString(InpMaxSameStoryEntryAttempts)
                  + " invalid_reason=" + invalid_reason);
         return false;
      }

      TradePlan branch_base = p;
      branch_base.fvg.entry_invalid = true;
      branch_base.fvg.invalidated = (invalid_reason == "fvg_invalidated_or_fully_filled" ||
                                     invalid_reason == "fvg_structure_invalidated");
      branch_base.fvg.invalidation_reason = invalid_reason;
      _FreezeSourcePO3Story(branch_base);

      FVGZone cands[];
      if(!m_fvg.FindCandidates(branch_base.symbol, branch_base.ltf, branch_base.po3,
                               MathMax(1, InpMaxFvgCandidatesPerSymbol), cands)){
         _Journal(branch_base.symbol + " same-story branch replacement unavailable: no alternate FVGs"
                  + " invalid_reason=" + invalid_reason
                  + " source_story=" + _PO3StoryId(branch_base));
         return false;
      }

      string branches[];
      ArrayResize(branches, 5);
      branches[0] = "fvg_edge";
      branches[1] = "fvg_mid";
      branches[2] = "breaker_retest";
      branches[3] = "range_reentry";
      branches[4] = "continuation_reentry";

      TradePlan best;
      bool have_best = false;
      string last_reason = "";
      for(int i=0; i<ArraySize(cands); i++){
         if(_SameFvgZone(cands[i], p.fvg, p.symbol)) continue;
         for(int b=0; b<ArraySize(branches); b++){
            TradePlan candidate;
            ZeroMemory(candidate);
            string reason = "";
            if(!_TryBuildCandidateFromBranch(branch_base, cands[i], branches[b], candidate, reason)){
               last_reason = reason;
               continue;
            }
            if(!have_best || _CandidateRanksAhead(candidate, best)){
               best = candidate;
               have_best = true;
            }
         }
      }

      if(!have_best){
         _Journal(branch_base.symbol + " same-story branch replacement failed"
                  + " invalid_reason=" + invalid_reason
                  + " last_reject=" + last_reason
                  + " source_story=" + _PO3StoryId(branch_base));
         return false;
      }

      _CarryNarrativeForward(best, p, true);
      best.snapshot_htf_path = p.snapshot_htf_path;
      best.snapshot_ltf_path = p.snapshot_ltf_path;
      best.created_at = p.created_at;
      best.last_confirm_bar_time = p.last_confirm_bar_time;
      best.bars_waited = p.bars_waited;
      best.arm_max_bars = p.arm_max_bars;
      best.max_watch_minutes = p.max_watch_minutes;
      best.armed = false;
      best.armed_at = 0;
      best.mid_touched = !InpRequireFvgMidMitigation;
      best.b50_touched = !InpWaitB50OnM1;
      if(_MidMitigationOk(best.symbol, best)) best.mid_touched = true;
      if(_B50Ok(best.symbol, best)) best.b50_touched = true;
      best.attempt_number_for_sweep = attempts + 1;
      best.narrative_state = "staged";
      best.invalidation_cause = "same_story_branch_replacement_from_" + invalid_reason;
      best.last_score_refresh = TimeLocal();
      p = best;
      _Journal(p.symbol + " same-story new entry branch selected"
               + " branch=" + p.entry_branch
               + " attempt_number_for_sweep=" + IntegerToString(p.attempt_number_for_sweep)
               + " source_story=" + _PO3StoryId(p)
               + " setup_score=" + DoubleToString(p.setup_score, 2)
               + " rr2=" + DoubleToString(_ExecutionRR2(p), 2)
               + " ai_binding=none");
      return true;
   }

   bool _RefreshWatchlistPlan(TradePlan &p) {
      if(p.last_score_refresh > 0 && (TimeLocal() - p.last_score_refresh) < 30) return true;

      if(_MidMitigationOk(p.symbol, p)) p.mid_touched = true;
      if(_B50Ok(p.symbol, p)) p.b50_touched = true;

      FVGZone cands[];
      if(!m_fvg.FindCandidates(p.symbol, p.ltf, p.po3, MathMax(1, InpMaxFvgCandidatesPerSymbol), cands)){
         p.last_score_refresh = TimeLocal();
         return true;
      }

      int best_idx = -1;
      double best_match = DBL_MAX;
      double old_width = MathMax(p.fvg.upper - p.fvg.lower, SymbolInfoDouble(p.symbol, SYMBOL_POINT) * 2.0);
      int tf_secs = PeriodSeconds(p.ltf);
      if(tf_secs <= 0) tf_secs = 60;
      for(int i=0; i<ArraySize(cands); i++){
         if(cands[i].bullish != p.fvg.bullish) continue;
         double mid_diff = MathAbs(cands[i].mid - p.fvg.mid) / old_width;
         double time_diff = (double)MathAbs((int)(cands[i].t_form - p.fvg.t_form)) / (double)tf_secs;
         double match = mid_diff + time_diff * 0.15;
         if(match < best_match){
            best_match = match;
            best_idx = i;
         }
      }
      if(best_idx < 0 || best_match > 4.0){
         p.last_score_refresh = TimeLocal();
         return true;
      }

      TradePlan refreshed;
      ZeroMemory(refreshed);
      string rebuild_reason = "";
      if(!_TryBuildCandidateFromBranch(p, cands[best_idx], p.entry_branch, refreshed, rebuild_reason)){
         if(_IsEntryZoneOnlyInvalidation(rebuild_reason) && _TrySameStoryNewEntry(p, rebuild_reason))
            return true;
         p.last_score_refresh = TimeLocal();
         return true;
      }
      bool refreshed_candidate_identity_changed = !_SameFvgZone(refreshed.fvg, p.fvg, p.symbol);
      bool has_bound_ai_assessment = (StringLen(p.ai_decision_source) > 0 ||
                                      StringLen(p.ai_decision_id) > 0 ||
                                      StringLen(p.ai_selected_candidate_hash) > 0 ||
                                      StringLen(p.ai.selected_candidate_hash) > 0 ||
                                      StringLen(p.assessed_execution_fingerprint) > 0);
      if(refreshed_candidate_identity_changed && has_bound_ai_assessment){
         _LogSetupReject(p.symbol, "decision_integrity",
                         "alternative_candidate_requires_independent_ai_assessment",
                         "candidate_id=" + p.candidate_id
                         + " candidate_hash=" + p.candidate_hash
                         + " old_fvg_time=" + IntegerToString((int)p.fvg.t_form)
                         + " new_fvg_time=" + IntegerToString((int)refreshed.fvg.t_form)
                         + " action=drop_watchlist_no_ai_assessment_transfer");
         return false;
      }
      bool material_refresh = (refreshed.fvg.t_form != p.fvg.t_form ||
                               MathAbs(refreshed.entry_est - p.entry_est) > SymbolInfoDouble(p.symbol, SYMBOL_POINT) * 2.0 ||
                               refreshed.tp_model != p.tp_model);
      _CarryNarrativeForward(refreshed, p, material_refresh);
      refreshed.armed = p.armed;
      refreshed.mid_touched = p.mid_touched;
      refreshed.b50_touched = p.b50_touched;
      refreshed.bars_waited = p.bars_waited;
      refreshed.arm_max_bars = p.arm_max_bars;
      refreshed.max_watch_minutes = p.max_watch_minutes;
      refreshed.last_confirm_bar_time = p.last_confirm_bar_time;
      refreshed.armed_at = p.armed_at;
      refreshed.ai = p.ai;
      refreshed.ai_decision_source = p.ai_decision_source;
      refreshed.snapshot_htf_path = p.snapshot_htf_path;
      refreshed.snapshot_ltf_path = p.snapshot_ltf_path;
      refreshed.created_at = p.created_at;
      refreshed.portfolio_rank = p.portfolio_rank;
      refreshed.portfolio_score = p.portfolio_score;
      refreshed.scheduler_action = p.scheduler_action;
      refreshed.scheduler_reason = p.scheduler_reason;
      refreshed.last_score_refresh = TimeLocal();
      p = refreshed;
      return true;
   }

public:
#ifdef PO3_TEST_ORDER_ADAPTER
   // Harness-only surface. Absent from production builds entirely, so there is
   // no runtime switch a live run could trip into.
   void HarnessBindOrderAdapter(const int mode){ m_trade.BindHarness(m_bus, mode); }
   void HarnessForceRetcode(const uint retcode){ m_trade.ForceRetcode(retcode); }
   void HarnessClearForcedRetcode(){ m_trade.ClearForcedRetcode(); }
   bool HarnessOrderAttempted() const { return m_trade.OrderAttempted(); }
   int  HarnessOrderAttempts() const { return m_trade.Attempts(); }
   int  HarnessOrdersAccepted() const { return m_trade.Accepted(); }
   int  HarnessOrdersRejected() const { return m_trade.Rejected(); }
   string HarnessOrderCountersJson() const { return m_trade.CountersJson(); }
   int  HarnessWatchlistAdded() const { return m_total_watchlist_added; }
   int  HarnessWatchlistSize() const { return ArraySize(m_watchlist); }
   int  HarnessPendingAiSize() const { return ArraySize(m_pending_ai); }
#endif

   CTradeEngine(): m_ai(m_bus), m_state(m_bus), m_penalty(m_bus) {
      m_last_positions_tick = 0;
      m_last_penalty_persist = 0;
      m_last_rollover_log = 0;
      m_last_execution_reject_reason = "";
      m_last_execution_failure_class = "";
      m_last_execution_spread = 0.0;
      m_policy_loaded_at = 0;
      m_account_position_mode = "unknown";
      m_internal_account_position_mode = UNSUPPORTED_ACCOUNT_MODE;
      m_force_one_managed_trade_per_symbol = false;
      m_source_git_commit = "UNAVAILABLE";
      m_source_dirty_tree_status = "UNKNOWN";
      m_set_file_hash = "UNAVAILABLE";
      m_deployment_manifest_hash = "UNAVAILABLE";
      m_deployment_manifest_valid = false;
      ZeroMemory(m_active_policy);
      ArrayResize(m_subtype_policy, 0);
      ArrayResize(m_context_policy, 0);
      _ResetSetupFunnel();
      _ResetFinalCounters();
   }

   string RuntimeInputHash() {
      return m_ai.RuntimeHash();
   }

   bool Init() {
      MathSrand((int)TimeLocal());
      m_bus = CFileBus(InpBusRoot);
      m_bus.Ensure();
      string replay_identity_reason = "";
      if(!m_ai.ValidateReplayIdentityMode(replay_identity_reason)){
         _Journal("[startup_reject] reason=" + replay_identity_reason);
         return false;
      }
      // Freeze the replay identity before anything can consume it.  Every later
      // reader -- the startup policy manifest, request payloads, the plan cohort
      // stamp and the tester cache signature -- must see one value for the whole
      // process, otherwise a recorded cohort becomes unreachable part way through
      // a replay and the run silently reports cache misses instead of decisions.
      // See CAIGateBridge::FreezeReplayIdentity.
      _Journal("[decision_identity] " + m_ai.FreezeReplayIdentity());
      if(MQLInfoInteger(MQL_TESTER) && InpTesterAiCache){
         // One startup verdict instead of thousands of indistinguishable misses.
         bool cohort_match = _TesterCacheCohortMatches();
         _Journal("[tester_ai_cache_cohort] decision_input_hash=" + m_ai.DecisionHash()
                  + " recorded_cohorts=" + _TesterCacheCohortSummary()
                  + " dominant=" + _TesterCacheCohortDecisionHash()
                  + " artifacts_total=" + IntegerToString(_TesterCacheCohortTotal())
                  + " artifacts_sampled=" + IntegerToString(_TesterCacheCohortSampled())
                  + " match=" + (cohort_match ? "true" : "false")
                  + " verdict=" + (cohort_match ? "replayable"
                                                : "no_recorded_artifact_addressable_by_this_build_or_inputs"));
         // CACHE_ONLY exists to replay a recorded cohort.  When not one artifact
         // on disk carries this build's decision identity, every lookup is a
         // guaranteed miss and the run cannot reach a single AI decision -- it
         // burns a full pass to report zero approvals, which reads exactly like
         // a strategy failure and is not one.  Reject at startup instead, and
         // say which identity was expected against which was found.
         //
         // Only CACHE_ONLY is affected.  RECORD_ONLY legitimately starts against
         // a foreign or empty cohort -- recording one is its whole purpose.
         if(!cohort_match && _EffectiveTesterAiMode() == TESTER_AI_CACHE_ONLY){
            Print("[PO3_AIGate] [startup_reject] reason=tester_cache_cohort_unaddressable",
                  " mode=cache_only",
                  " decision_input_hash=", m_ai.DecisionHash(),
                  " recorded_cohorts=", _TesterCacheCohortSummary(),
                  " artifacts_total=", IntegerToString(_TesterCacheCohortTotal()),
                  " addressable=0",
                  " next_step=rerecord_cohort_with_TESTER_AI_RECORD_ONLY_then_export_then_CACHE_ONLY");
            return false;
         }
      }
      string runtime_state_scope = "";
      if(MQLInfoInteger(MQL_TESTER)){
         runtime_state_scope = "tester_session_" + m_ai.SessionId()
                               + "_magic_" + IntegerToString((long)InpMagicNumber);
      } else {
         // Keep live recovery state across terminal restarts while isolating it
         // from every tester run and from other accounts/strategies.
         runtime_state_scope = "live_account_"
                               + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))
                               + "_magic_" + IntegerToString((long)InpMagicNumber);
      }
      if(!m_state.SetRuntimeScope(runtime_state_scope)){
         Print("[PO3_AIGate] [startup_reject] reason=runtime_state_scope_unavailable scope=",
               runtime_state_scope);
         return false;
      }
      Print("[PO3_AIGate] [runtime_state_scope] scope=", m_state.RuntimeScope(),
            " tester=", (MQLInfoInteger(MQL_TESTER) ? "true" : "false"),
            " legacy_global_state_loaded=false");
      // The behavioural circuit-breaker reads a bus-wide trade_results directory.
      // Give it this run's identity so it cannot inherit another run's losing
      // streak.  See PO3SetBehaviorRuntimeScope in Risk.mqh.
      PO3SetBehaviorRuntimeScope(m_state.RuntimeScope());
#ifdef PO3_TEST_ORDER_ADAPTER
      // Bind here rather than from the EA so the harness EA can reuse the
      // production EA verbatim instead of maintaining a forked copy of the
      // scheduling loop that would drift away from it.
      m_trade.BindHarness(m_bus, InpHarnessOrderAdapterMode);
#endif
      _LoadDeploymentManifest();
      m_internal_account_position_mode = _ResolveAccountPositionMode();
      m_account_position_mode = _AccountPositionModeLabel(m_internal_account_position_mode);
      m_force_one_managed_trade_per_symbol = (m_internal_account_position_mode == NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK);
      FolderCreate(_ExecutionIdentityQuarantineDir(), FILE_COMMON);
      Print("[PO3_AIGate] [account_position_mode] broker_margin_mode=",
            IntegerToString((int)AccountInfoInteger(ACCOUNT_MARGIN_MODE)),
            " internal_mode=", m_account_position_mode,
            " netting_policy=", (InpNettingPositionPolicy == NETTING_FORCE_ONE_MANAGED_POSITION_PER_SYMBOL
                                   ? "FORCE_ONE_MANAGED_POSITION_PER_SYMBOL" : "REJECT_STARTUP"),
            " virtual_subposition_ledger_available=false",
            " startup_allowed=", (m_internal_account_position_mode == UNSUPPORTED_ACCOUNT_MODE ? "false" : "true"),
            " exact_order_deal_position_attribution=true",
            " force_one_managed_trade_per_symbol=", (m_force_one_managed_trade_per_symbol ? "true" : "false"));
      if(m_internal_account_position_mode == UNSUPPORTED_ACCOUNT_MODE){
         Print("[PO3_AIGate] [startup_reject] reason=netting_virtual_subposition_ledger_unavailable broker_margin_mode=",
               IntegerToString((int)AccountInfoInteger(ACCOUNT_MARGIN_MODE)),
               " virtual_subposition_ledger_available=false netting_policy=REJECT_STARTUP action=abort");
         return false;
      }
      if(m_force_one_managed_trade_per_symbol)
         Print("[PO3_AIGate] [account_position_mode] warning=explicit_netting_fallback_selected action=force_one_managed_trade_per_symbol analytics_independent_subpositions=false");
      if(!MQLInfoInteger(MQL_TESTER)){
         Print("[PO3_AIGate] [live_forward_mode] account_trade_mode=", m_account_position_mode,
               " workload_mode=LIVE_FORWARD behavior_contract_hash=", m_ai.BehaviorContractHash(),
               " contract_version=", LIVE_FORWARD_CONTRACT_VERSION,
               " demo_real_equivalent=true");
      }
      if(MQLInfoInteger(MQL_TESTER) && (InpUseAI || _TesterBootstrapMode())){
         Print("[PO3_AIGate] [tester_ai_mode] raw_value=", IntegerToString((int)InpTesterAiMode),
               " raw_name=", _TesterAiModeName(InpTesterAiMode),
               " effective_name=", _TesterAiModeName(_EffectiveTesterAiMode()),
               " tester_ai_cache=", (InpTesterAiCache ? "true" : "false"),
               " allow_live_wait_debug_trading=", (InpTesterAllowLiveWaitDebugTrading ? "true" : "false"));
         if(_InvalidTesterBootstrapConfig()){
            Print("[PO3_AIGate] [fatal_config] bootstrap_rule_only requires InpUseAI=false, InpAiWaitInTester=false, InpTesterAllowLiveWaitDebugTrading=false, and risk multiplier in (0,1]. Aborting test.");
            return false;
         }
         if(_EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG && InpTesterAllowLiveWaitDebugTrading){
            // This remains a diagnostic, non-authoritative historical mode: a
            // browser wait can advance tester time.  Permit it only behind the
            // explicit acknowledgement and the strongest available snapshot,
            // stale-result and blocking-wait controls.  The assessed plan is
            // immutable and the current market state is revalidated before an
            // order can be submitted.
            if(!InpAiWaitInTester ||
               !InpTesterFreezeAiExecutionSnapshot ||
               !InpTesterRejectStaleAiResults ||
               InpTesterMaxAiResultAgeSimMinutes <= 0){
               Print("[PO3_AIGate] [fatal_config] live_wait_debug trading requires InpAiWaitInTester=true, InpTesterFreezeAiExecutionSnapshot=true, InpTesterRejectStaleAiResults=true, and a positive simulated-age limit. Aborting test.");
               return false;
            }
            Print("[PO3_AIGate] [tester_ai_mode] live_wait_debug_trading_explicitly_acknowledged=true authoritative_historical_backtest=false immutable_execution_snapshot=true current_market_revalidation=true simulated_time_may_advance=true");
         }
         if(_InvalidTesterCacheOnlyConfig()){
            Print("[PO3_AIGate] [fatal_config] tester_ai_mode=cache_only requires InpTesterAiCache=true. No AI decisions can be produced. Aborting test.");
            Print("[PO3_AIGate] [tester_ai_workflow] clean_backtest_requires_cache_replay=true");
            Print("[PO3_AIGate] [tester_ai_workflow] Step 1: run RECORD_ONLY to export requests.");
            Print("[PO3_AIGate] [tester_ai_workflow] Step 2: run python ai_gate.py / batch processor to fill cache.");
            Print("[PO3_AIGate] [tester_ai_workflow] Step 3: rerun with CACHE_ONLY and InpTesterAiCache=true.");
            return false;
         }
         if(_EffectiveTesterAiMode() == TESTER_AI_BOOTSTRAP_RULE_ONLY){
            Print("[PO3_AIGate] [tester_ai_mode] bootstrap_rule_only_active=true live_ai_calls=false wall_clock_wait=false simulated_trading=true tester_only=true risk_multiplier=",
                  DoubleToString(InpTesterBootstrapRiskMultiplier, 4));
         } else if(_EffectiveTesterAiMode() == TESTER_AI_CACHE_ONLY){
            Print("[PO3_AIGate] [tester_ai_workflow] clean_backtest_requires_cache_replay=true");
            if(!_TesterAiCacheHasFiles()){
               Print("[PO3_AIGate] [fatal_config] CACHE_ONLY has no tester cache files. Run RECORD_ONLY + Python cache exporter first.");
               Print("[PO3_AIGate] [tester_ai_workflow] Step 1: run RECORD_ONLY to export requests.");
               Print("[PO3_AIGate] [tester_ai_workflow] Step 2: run python ai_gate.py / batch processor to fill cache.");
               Print("[PO3_AIGate] [tester_ai_workflow] Step 3: rerun with CACHE_ONLY and InpTesterAiCache=true.");
               return false;
            }
            Print("[PO3_AIGate] [tester_ai_mode] cache_only_valid=true cache_enabled=true cache_dir=", _TesterAiCacheDir());
         } else if(_EffectiveTesterAiMode() == TESTER_AI_RECORD_ONLY){
            Print("[PO3_AIGate] [tester_ai_mode] record_only_active=true cache_lookup=false cache_export=true live_ai_calls=false trading=false backtest_safe=true");
         } else if(_EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG){
            Print("[PO3_AIGate] [tester_ai_mode] live_wait_debug_active=true trading_enabled=",
                  (InpTesterAllowLiveWaitDebugTrading ? "true" : "false"),
                  " warning=tester_live_ai_wait_not_backtest_safe");
         }
      }
      _EnsureSnapshotDirs();
      if(InpAnalyticsEnable) FolderCreate(_TradeResultsDir(), FILE_COMMON);
      FolderCreate(_PolicyDir(), FILE_COMMON);
      FolderCreate(_AnalyticsDir(), FILE_COMMON);
      FolderCreate(_AnalyticsJobsDir(), FILE_COMMON);
      m_trade.SetExpertMagicNumber(InpMagicNumber);
      m_trade.SetDeviationInPoints(InpMaxSlippagePts);

      string startup_ledger_status = _CurrentLedgerIntegrityStatus();
      bool active_analytics_authority_requested = (InpAnalyticsAutoActivate && !InpPolicyShadowMode);
      bool active_data_policy_authority_requested = (InpRiskFactorGateEnable ||
                                                      InpInvalidationAssetClassPolicyEnable ||
                                                      InpNormalizedFvgMode == NORMALIZED_FVG_ENFORCE);
      _Journal("[policy_authority_startup] ledger_integrity_status=" + startup_ledger_status
               + " analytics_active_requested=" + (active_analytics_authority_requested ? "true" : "false")
               + " data_policy_active_requested=" + (active_data_policy_authority_requested ? "true" : "false"));
      if(startup_ledger_status != "CLEAN" && active_analytics_authority_requested){
         _Journal("[startup_policy_manifest] policy_type=active status=blocked authority=blocked reason=ledger_not_clean");
      }
      if(startup_ledger_status != "CLEAN" && active_data_policy_authority_requested){
         Print("[PO3_AIGate] [startup_reject] reason=active_policy_authority_requires_clean_ledger ledger_status=",
               startup_ledger_status,
               " risk_factor_enabled=", (InpRiskFactorGateEnable ? "true" : "false"),
               " invalidation_enabled=", (InpInvalidationAssetClassPolicyEnable ? "true" : "false"),
               " normalized_fvg_enforce=", (InpNormalizedFvgMode == NORMALIZED_FVG_ENFORCE ? "true" : "false"));
         return false;
      }

      double aggregate_cap_money = 0.0;
      double aggregate_pct_cap_money = 0.0;
      string aggregate_cap_source = "none";
      string aggregate_cap_reason = "";
      bool aggregate_cap_valid = EffectiveAggregateRiskCap(aggregate_cap_money,
                                                            aggregate_pct_cap_money,
                                                            aggregate_cap_source,
                                                            aggregate_cap_reason);
      double risk_base_money = _AggregateRiskBaseMoney();
      _Journal("[aggregate_risk_startup] enabled=" + (InpMaxTotalRiskEnable ? "true" : "false")
               + " base_money=" + DoubleToString(risk_base_money, 2)
               + " pct_cap=" + DoubleToString(InpMaxTotalRiskPct, 4)
               + " pct_cap_money=" + DoubleToString(aggregate_pct_cap_money, 2)
               + " configured_money_cap=" + DoubleToString(InpMaxTotalRiskMoney, 2)
               + " effective_cap_money=" + DoubleToString(aggregate_cap_money, 2)
               + " effective_source=" + aggregate_cap_source
               + " valid=" + (aggregate_cap_valid ? "true" : "false")
               + (StringLen(aggregate_cap_reason) > 0 ? " reason=" + aggregate_cap_reason : ""));
      if(!MQLInfoInteger(MQL_TESTER) && InpRequireAggregateRiskCapLive && !aggregate_cap_valid){
         Print("[PO3_AIGate] [startup_reject] reason=live_aggregate_risk_cap_required_but_invalid detail=", aggregate_cap_reason);
         return false;
      }

      string risk_factor_policy_json = "";
      bool risk_factor_policy_loaded = _ReadCommonText(InpRiskFactorPolicyFile, risk_factor_policy_json);
      bool risk_factor_policy_valid = (risk_factor_policy_loaded &&
                                       JsonGetString(risk_factor_policy_json, "schema_version", "") == RISK_FACTOR_SCHEMA_VERSION);
      _Journal("[risk_factor_startup] enabled=" + (InpRiskFactorGateEnable ? "true" : "false")
               + " required_live=" + (InpRiskFactorPolicyRequiredLive ? "true" : "false")
               + " file=" + InpRiskFactorPolicyFile
               + " exists=" + (risk_factor_policy_loaded ? "true" : "false")
               + " schema_valid=" + (risk_factor_policy_valid ? "true" : "false")
               + " required_schema=" + RISK_FACTOR_SCHEMA_VERSION);
      if(InpRiskFactorGateEnable && !risk_factor_policy_valid){
         Print("[PO3_AIGate] [startup_reject] reason=risk_factor_policy_missing_or_incompatible file=", InpRiskFactorPolicyFile);
         return false;
      }
      if(!MQLInfoInteger(MQL_TESTER) && InpRiskFactorPolicyRequiredLive && !risk_factor_policy_valid){
         Print("[PO3_AIGate] [startup_reject] reason=mandatory_live_risk_factor_policy_unavailable file=", InpRiskFactorPolicyFile);
         return false;
      }

      string startup_cost_source = "";
      int startup_cost_samples = 0;
      double startup_cost_median = 0.0;
      datetime startup_cost_from = 0;
      datetime startup_cost_to = 0;
      double startup_stressed_cost = BrokerCostEstimatePerLot(_Symbol,
                                                               startup_cost_source,
                                                               startup_cost_samples,
                                                               startup_cost_median,
                                                               startup_cost_from,
                                                               startup_cost_to);
      _Journal("[broker_cost_startup] symbol=" + _Symbol
               + " source=" + startup_cost_source
               + " sample_size=" + IntegerToString(startup_cost_samples)
               + " median_per_lot=" + DoubleToString(startup_cost_median, 6)
               + " stressed_per_lot=" + DoubleToString(startup_stressed_cost, 6)
               + " model_version=" + COMMISSION_MODEL_SCHEMA_VERSION);
      string normalized_fvg_policy_json = "";
      bool normalized_fvg_policy_loaded = _ReadCommonText(InpNormalizedFvgAssetClassPolicyFile,
                                                           normalized_fvg_policy_json);
      bool normalized_fvg_policy_schema_valid = (normalized_fvg_policy_loaded &&
                                                  JsonGetString(normalized_fvg_policy_json, "schema_version", "") == NORMALIZED_FVG_SCHEMA_VERSION &&
                                                  StringLen(JsonGetObject(normalized_fvg_policy_json, "asset_classes", "")) > 0);
      bool normalized_fvg_policy_authority_compatible = (normalized_fvg_policy_schema_valid &&
                                                          JsonGetString(normalized_fvg_policy_json, "policy_scope", "asset_class") == "asset_class" &&
                                                          JsonGetString(normalized_fvg_policy_json, "ledger_integrity_status", "") == "CLEAN" &&
                                                          JsonGetString(normalized_fvg_policy_json, "taxonomy_version", "") == SETUP_TAXONOMY_VERSION);
      _Journal("[normalized_fvg_startup] mode=" + IntegerToString((int)InpNormalizedFvgMode)
               + " schema_version=" + NORMALIZED_FVG_SCHEMA_VERSION
               + " policy_scope=asset_class shadow_default="
               + (InpNormalizedFvgMode == NORMALIZED_FVG_SHADOW ? "true" : "false")
               + " policy_file=" + InpNormalizedFvgAssetClassPolicyFile
               + " policy_loaded=" + (normalized_fvg_policy_loaded ? "true" : "false")
               + " policy_schema_valid=" + (normalized_fvg_policy_schema_valid ? "true" : "false")
               + " policy_authority_compatible=" + (normalized_fvg_policy_authority_compatible ? "true" : "false")
               + " minimum_clean_samples=" + IntegerToString(InpNormalizedFvgMinAssetClassSamples));
      if(InpNormalizedFvgMode == NORMALIZED_FVG_ENFORCE && !normalized_fvg_policy_authority_compatible){
         Print("[PO3_AIGate] [startup_reject] reason=normalized_fvg_enforce_policy_missing_incompatible_or_unclean file=",
               InpNormalizedFvgAssetClassPolicyFile);
         return false;
      }
      string invalidation_policy_json = "";
      bool invalidation_policy_loaded = _ReadCommonText(InpInvalidationAssetClassPolicyFile,
                                                         invalidation_policy_json);
      bool invalidation_policy_valid = (invalidation_policy_loaded &&
                                         JsonGetString(invalidation_policy_json, "schema_version", "") == INVALIDATION_POLICY_SCHEMA_VERSION &&
                                         StringLen(JsonGetObject(invalidation_policy_json, "asset_classes", "")) > 0);
      _Journal("[invalidation_policy_startup] enabled=" + (InpInvalidationAssetClassPolicyEnable ? "true" : "false")
               + " file=" + InpInvalidationAssetClassPolicyFile
               + " loaded=" + (invalidation_policy_loaded ? "true" : "false")
               + " schema_valid=" + (invalidation_policy_valid ? "true" : "false")
               + " required_schema=" + INVALIDATION_POLICY_SCHEMA_VERSION);
      if(InpInvalidationAssetClassPolicyEnable && !invalidation_policy_valid){
         Print("[PO3_AIGate] [startup_reject] reason=invalidation_asset_class_policy_missing_or_incompatible file=",
               InpInvalidationAssetClassPolicyFile);
         return false;
      }
      _LoadActivePolicyFiles();
      _WriteStartupPolicyManifest();

      TradePlan tmp[];
      if(m_state.LoadPlans(m_state.WatchlistPath(), tmp)) {
         _CopyPlans(m_watchlist, tmp);
         _PrunePlanArray(m_watchlist, false);
      }
      if(m_state.LoadPlans(m_state.PendingAiPath(), tmp)) {
         _CopyPlans(m_pending_ai, tmp);
         if(MQLInfoInteger(MQL_TESTER) && ArraySize(m_pending_ai) > 0){
            string req_ids[];
            ArrayResize(req_ids, 0);
            for(int i=0; i<ArraySize(m_pending_ai); i++){
               string req_id = m_pending_ai[i].req_id;
               if(StringLen(req_id) == 0 || _HasStringValue(req_ids, req_id)) continue;
               int n = ArraySize(req_ids);
               ArrayResize(req_ids, n+1);
               req_ids[n] = req_id;
               _ArchivePendingArtifacts(req_id, "shutdown");
            }
            _Journal("tester init clearing restored pending_ai requests=" + IntegerToString(ArraySize(req_ids))
                     + " candidates=" + IntegerToString(ArraySize(m_pending_ai)));
            ArrayResize(m_pending_ai, 0);
         }
         _PrunePlanArray(m_pending_ai, true);
      }
      if(m_state.LoadPlans(m_state.CounterfactualPendingPath(), tmp)){
         _CopyPlans(m_counterfactual_pending, tmp);
         for(int i=ArraySize(m_counterfactual_pending)-1; i>=0; i--){
            if(m_counterfactual_pending[i].management_version == MANAGEMENT_SCHEMA_VERSION &&
               m_counterfactual_pending[i].counterfactual_pending &&
               StringLen(m_counterfactual_pending[i].trade_key) > 0) continue;
            int last = ArraySize(m_counterfactual_pending) - 1;
            if(i != last) m_counterfactual_pending[i] = m_counterfactual_pending[last];
            ArrayResize(m_counterfactual_pending, last);
         }
      }
      if(m_state.LoadPlans(m_state.ShadowPendingPath(), tmp)){
         _CopyPlans(m_shadow_pending, tmp);
         for(int i=ArraySize(m_shadow_pending)-1; i>=0; i--){
            if(m_shadow_pending[i].shadow_candidate_schema_version == SHADOW_CANDIDATE_SCHEMA_VERSION &&
               m_shadow_pending[i].shadow_outcome_status == "PENDING" &&
               StringLen(m_shadow_pending[i].shadow_candidate_record_hash) > 0) continue;
            int last = ArraySize(m_shadow_pending) - 1;
            if(i != last) m_shadow_pending[i] = m_shadow_pending[last];
            ArrayResize(m_shadow_pending, last);
         }
      }
      PenaltyState penalty_tmp[];
      if(m_state.LoadPenaltyStates(m_state.PenaltyPath(), penalty_tmp)) { m_penalty.RestoreStates(penalty_tmp); }
      _RecoverManagedExposureOnInit();
      _Journal("ENGINE_VERSION=" + ENGINE_VERSION
               + " input_schema=" + ENGINE_INPUT_SCHEMA
               + " htf=" + IntegerToString((int)PO3EffectiveHTF())
               + " entry_tf=" + IntegerToString((int)PO3EffectiveEntryTF())
               + " confirm_tf=" + IntegerToString((int)PO3EffectiveConfirmTF())
               + " preset=" + InpStrategyPreset);
      _Journal("[runtime_inputs] InpEnableMPCTrading=" + (InpEnableMPCTrading ? "true" : "false")
               + " InpEnableBucketRiskPolicy=" + (InpEnableBucketRiskPolicy ? "true" : "false")
               + " InpBucketRiskPolicyFile=" + InpBucketRiskPolicyFile
               + " InpAiVetoEnable=" + (InpAiVetoEnable ? "true" : "false")
               + " InpAiMinFollowThroughProb=" + DoubleToString(InpAiMinFollowThroughProb, 4)
               + " InpAiMaxInvalidationRisk=" + DoubleToString(InpAiMaxInvalidationRisk, 4)
               + " InpAiMaxChopRisk=" + DoubleToString(InpAiMaxChopRisk, 4)
               + " InpAiMaxPostEntryFailureRisk=" + DoubleToString(InpAiMaxPostEntryFailureRisk, 4)
               + " InpAiMinFinalExpectancyScore=" + DoubleToString(InpAiMinFinalExpectancyScore, 4));
      _Journal("[risk_controls] InpMaxTotalRiskEnable=" + (InpMaxTotalRiskEnable ? "true" : "false")
               + " InpMaxTotalRiskMoney=" + DoubleToString(InpMaxTotalRiskMoney, 2)
               + " InpDailyLossCapPct=" + DoubleToString(InpDailyLossCapPct, 4)
               + " InpMaxOpenPositions=" + IntegerToString(InpMaxOpenPositions)
               + " InpMaxTradesPerScan=" + IntegerToString(InpMaxTradesPerScan));
       if(_AllEntryFamiliesDisabled()){
         _Journal("configuration_error: all entry families disabled " + _EntryFamilyConfigSummary());
         return false;
      }
       _Journal("entry_family_config " + _EntryFamilyConfigSummary());
       _Journal("frequency_objective trades_per_day_min=" + IntegerToString(InpTargetTradesPerDayMin)
                + " trades_per_day_max=" + IntegerToString(InpTargetTradesPerDayMax)
                + " note=objective_only_valid_setups_are_never_forced");
      _Journal("engine init watchlist=" + IntegerToString(ArraySize(m_watchlist))
               + " pending_ai=" + IntegerToString(ArraySize(m_pending_ai))
               + " counterfactual_pending=" + IntegerToString(ArraySize(m_counterfactual_pending))
               + " shadow_outcomes_pending=" + IntegerToString(ArraySize(m_shadow_pending))
               + " ai=" + (InpUseAI ? "on" : "off")
               + " scan_all=" + (InpScanAllMarketWatch ? "true" : "false")
               + " bus=" + InpBusRoot);
      int effective_bars = _EffectiveWatchlistMaxBars();
      if(effective_bars != InpWatchlistMaxBars){
         _Journal("watchlist max bars auto-adjusted input=" + IntegerToString(InpWatchlistMaxBars)
                  + " effective=" + IntegerToString(effective_bars)
                  + " reason=" + (InpWaitB50OnM1 && InpWaitCandleConfirm ? "wait_b50_and_candle_confirm" : "m1_multi_confirmation"));
      }
      return true;
   }

   void Deinit() {
      // A decision pending at shutdown cannot be resumed against a later
      // market state. Fail it closed and archive any remaining bus artifact.
      string shutdown_req_ids[];
      ArrayResize(shutdown_req_ids, 0);
      AiDecision shutdown_decision;
      ZeroMemory(shutdown_decision);
      shutdown_decision.decision_state = "REJECT";
      shutdown_decision.decision_quality_tier = "DEGRADED_NON_TRADING";
      shutdown_decision.decision_schema_version = AI_DECISION_SCHEMA_VERSION;
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         _WriteShadowDecisionUpdate(m_pending_ai[i], shutdown_decision,
                                    "mql_shutdown_reject", "shutdown_pending_ai", false);
         string req_id = m_pending_ai[i].req_id;
         if(StringLen(req_id) == 0 || _HasStringValue(shutdown_req_ids, req_id)) continue;
         int n = ArraySize(shutdown_req_ids);
         ArrayResize(shutdown_req_ids, n + 1);
         shutdown_req_ids[n] = req_id;
         _ArchivePendingArtifacts(req_id, "shutdown");
      }
      if(ArraySize(shutdown_req_ids) > 0)
         _Journal("[file_bus_recovery] shutdown_pending_requests=" + IntegerToString(ArraySize(shutdown_req_ids))
                  + " action=archived_shutdown_and_failed_closed");
      ArrayResize(m_pending_ai, 0);

      // persist watchlist/research state
      _FinalizeClosedTrades();
      _MaintainCounterfactualEvaluations();
      _MaintainShadowCandidateOutcomes();
      _LogFinalSummary();
      _PrunePlanArray(m_watchlist, false);
      _PrunePlanArray(m_pending_ai, true);
      m_state.SavePlans(m_state.WatchlistPath(), m_watchlist);
      m_state.SavePlans(m_state.PendingAiPath(), m_pending_ai);
      _PersistResearchQueues();
      _PersistPenaltyStates();
   }

   void HandleTradeTransaction(const MqlTradeTransaction &trans) {
      if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0) return;
      // Any added deal changes the population the broker-cost estimate is computed
      // from: entry deals carry commission, exit deals carry commission, swap and
      // fee.  Drop the memo here, BEFORE the magic and entry-kind filters below --
      // those exist to decide whether this deal opens one of our positions, which is
      // a different question from whether the cost history moved.  Over-invalidating
      // costs one recomputation; under-invalidating would serve a stale cost.
      BrokerCostHistoryInvalidate();
      if(!HistoryDealSelect(trans.deal)) return;
      if((ulong)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagicNumber) return;
      ENUM_DEAL_ENTRY entry_kind = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
      if(entry_kind != DEAL_ENTRY_IN && entry_kind != DEAL_ENTRY_INOUT) return;

      ulong order_ticket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_ORDER);
      long position_identifier = (long)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
      string symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
      string comment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
      if(HistoryOrderSelect(order_ticket)){
         string order_comment = HistoryOrderGetString(order_ticket, ORDER_COMMENT);
         if(StringLen(order_comment) > 0) comment = order_comment;
      }

      TradePlan meta;
      bool have_meta = _ReadTradeMetaPath(_TradeOrderPath(order_ticket), meta);
      if(!have_meta && StringLen(comment) > 0)
         have_meta = _ReadTradeMetaPath(_TradeKeyPath(comment), meta);
      if(!have_meta){
         ZeroMemory(meta);
         meta.symbol = symbol;
         meta.trade_key = comment;
         meta.broker_comment = comment;
         meta.result_order_ticket = order_ticket;
         meta.result_deal_ticket = trans.deal;
         meta.position_id = position_identifier;
         meta.broker_position_identifier = position_identifier;
         meta.broker_position_ticket = _FindPositionTicketByIdentifier(position_identifier);
         meta.broker_submission_attempted = true;
         meta.broker_request_accepted = true;
         meta.mql_final_allow = true;
         meta.ai.mql_final_allow = true;
         meta.final_execution_success = false;
         meta.execution_authority_state = "BROKER_ACCEPTED_IDENTITY_QUARANTINED";
         _QuarantineExecutionIdentity(meta, "missing_execution_metadata_for_entry_deal", order_ticket, trans.deal);
         _SetExecutionAuthority(meta, "BROKER_ACCEPTED_IDENTITY_QUARANTINED", true,
                                "entry_deal_arrived_without_exact_execution_metadata");
         return;
      }

      bool pending_fill = (meta.narrative_state == "pending_order" ||
                           meta.narrative_state == "pending_order_recovered");
      double deal_volume = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
      double volume_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
      double volume_tolerance = MathMax(InpLedgerVolumeReconciliationTolerance, volume_step * 0.51);
      meta.broker_submission_attempted = true;
      meta.broker_request_accepted = true;
      meta.mql_final_allow = true;
      meta.ai.mql_final_allow = true;
      meta.result_order_ticket = order_ticket;
      meta.result_deal_ticket = trans.deal;
      meta.broker_partial_fill = (meta.initial_volume > 0.0 &&
                                  deal_volume + volume_tolerance < meta.initial_volume);
      string identity_reason = "";
      if(!_ResolveExactExecutionIdentity(meta, order_ticket, trans.deal, meta.initial_volume,
                                         identity_reason, deal_volume)){
         meta.position_id = position_identifier;
         meta.broker_position_identifier = position_identifier;
         meta.broker_position_ticket = _FindPositionTicketByIdentifier(position_identifier);
         meta.final_execution_success = false;
         meta.execution_authority_state = "BROKER_ACCEPTED_IDENTITY_QUARANTINED";
         _QuarantineExecutionIdentity(meta, identity_reason, order_ticket, trans.deal);
         _SetExecutionAuthority(meta, "BROKER_ACCEPTED_IDENTITY_QUARANTINED", true,
                                "entry_fill_exact_identity_failed:" + identity_reason);
         return;
      }
      meta.final_execution_success = true;
      meta.narrative_state = (meta.broker_partial_fill ? "executed_partial_fill" : "executed");
      _SetExecutionAuthority(meta,
                             meta.broker_partial_fill ? "POSITION_PARTIALLY_FILLED_IDENTITY_VERIFIED"
                                                      : "POSITION_FILLED_IDENTITY_VERIFIED",
                             true,
                             meta.broker_partial_fill ? "entry_partial_fill_exact_identity_verified"
                                                      : "entry_fill_exact_identity_verified");
      _CaptureEntryRiskContext(meta,
                               deal_volume,
                               HistoryDealGetDouble(trans.deal, DEAL_PRICE),
                               (meta.planned_sl > 0.0 ? meta.planned_sl : meta.sl));
      _UpdateAnalyticsSnapshot(meta,
                               meta.broker_position_ticket,
                               SymbolInfoDouble(symbol, meta.is_buy ? SYMBOL_BID : SYMBOL_ASK),
                               PositionSelectByTicket(meta.broker_position_ticket) ? PositionGetDouble(POSITION_VOLUME) : meta.initial_volume);
      _WriteTradeMeta(meta, meta.broker_position_ticket);
      _WriteShadowDecisionUpdate(meta, meta.ai,
                                 meta.broker_partial_fill ? "mql_position_partially_filled"
                                                          : "mql_position_filled",
                                 meta.mql_decision_reasons, true);
      if(pending_fill){
         m_funnel_orders_filled++;
         m_funnel_trades_opened++;
         m_total_trades_opened++;
         _RememberConsumedSweep(meta);
         _Journal(symbol + " pending order filled with exact identity"
                  + " order_ticket=" + IntegerToString((long)order_ticket)
                  + " deal_ticket=" + IntegerToString((long)trans.deal)
                  + " position_id=" + IntegerToString(position_identifier)
                  + " candidate_hash=" + meta.candidate_hash);
      }
   }

   bool EntryFreezeActive(string &reason) {
      return PO3EntryBlockedByRollover(_NowServerOrLocal(), reason);
   }

   void MaintainRolloverProtection() {
      _MaintainBrokerSymbolSessionProtection();
      datetime now = _NowServerOrLocal();
      string reason = "";
      if(PO3PreCloseFlattenActive(now, reason)){
         bool acted = _FlattenManagedExposureWithReason(m_trade, reason);
         if(acted){
            _Journal("rollover protection flattened managed exposure reason=" + reason);
            m_last_rollover_log = now;
         } else if(m_last_rollover_log <= 0 || (now - m_last_rollover_log) >= 60){
            _Journal("rollover protection active reason=" + reason + " exposure=none");
            m_last_rollover_log = now;
         }
         return;
      }

      if(PO3TradingFreezeActive(now, reason)){
         bool acted = _DeleteManagedPendingOrders(m_trade, reason);
         if(acted){
            _Journal("rollover protection deleted pending orders reason=" + reason);
            m_last_rollover_log = now;
         } else if(m_last_rollover_log <= 0 || (now - m_last_rollover_log) >= 60){
            _Journal("rollover trading freeze active reason=" + reason);
            m_last_rollover_log = now;
         }
      }
   }

   void AbortScan(const string reason) {
      if(ArraySize(m_scan_candidates) > 0)
         _Journal("scan aborted reason=" + reason + " discarded_candidates=" + IntegerToString(ArraySize(m_scan_candidates)));
      ArrayResize(m_scan_candidates, 0);
      _ResetSetupFunnel();
   }

   void BeginScan() {
      ArrayResize(m_scan_candidates, 0);
      _ResetSetupFunnel();
   }

   void FinalizeScan() {
      if(ArraySize(m_scan_candidates) <= 0){
         _Journal("snapshots not attempted: no candidates created");
         _LogSetupFunnel();
         return;
      }
      _Journal("finalizing scan candidates=" + IntegerToString(ArraySize(m_scan_candidates))
               + " per_symbol_cap=" + IntegerToString(MathMax(1, InpMaxFvgCandidatesPerSymbol)));
      int symbol_limit = InpMaxTradesPerScan;
      if(symbol_limit <= 0) symbol_limit = ArraySize(m_scan_candidates);

      string symbols[];
      int rep_indexes[];
      ArrayResize(symbols, 0);
      ArrayResize(rep_indexes, 0);
      for(int i=0; i<ArraySize(m_scan_candidates); i++){
         int idx = -1;
         for(int j=0; j<ArraySize(symbols); j++){
            if(symbols[j] == m_scan_candidates[i].symbol){
               idx = j;
               break;
            }
         }
         if(idx < 0){
            idx = ArraySize(symbols);
            ArrayResize(symbols, idx + 1);
            ArrayResize(rep_indexes, idx + 1);
            symbols[idx] = m_scan_candidates[i].symbol;
            rep_indexes[idx] = i;
            continue;
         }
         if(_CandidateRanksAhead(m_scan_candidates[i], m_scan_candidates[rep_indexes[idx]])){
            rep_indexes[idx] = i;
         }
      }

      string selected_symbols[];
      string selected_clusters[];
      string selected_sessions[];
      string selected_usd[];
      string selected_setups[];
      double selected_scores[];
      double selected_session_conc[];
      double selected_usd_conc[];
      double selected_cluster_conc[];
      double selected_diversity[];
      ArrayResize(selected_symbols, 0);
      ArrayResize(selected_clusters, 0);
      ArrayResize(selected_sessions, 0);
      ArrayResize(selected_usd, 0);
      ArrayResize(selected_setups, 0);
      ArrayResize(selected_scores, 0);
      ArrayResize(selected_session_conc, 0);
      ArrayResize(selected_usd_conc, 0);
      ArrayResize(selected_cluster_conc, 0);
      ArrayResize(selected_diversity, 0);

      while(ArraySize(selected_symbols) < symbol_limit && ArraySize(selected_symbols) < ArraySize(symbols)){
         int best_idx = -1;
         double best_score = -DBL_MAX;
         double best_session_conc = 0.0;
         double best_usd_conc = 0.0;
         double best_cluster_conc = 0.0;
         double best_diversity = 0.0;

         for(int i=0; i<ArraySize(symbols); i++){
            string sym = symbols[i];
            if(_HasStringValue(selected_symbols, sym)) continue;

            TradePlan rep = m_scan_candidates[rep_indexes[i]];
            int cluster_hits = _ExistingClusterCount(rep.portfolio_cluster) + _CountStringValue(selected_clusters, rep.portfolio_cluster);
            int session_hits = _ExistingSessionCount(rep.po3.session_name) + _CountStringValue(selected_sessions, rep.po3.session_name);
            int usd_hits = _ExistingUsdCount(rep.usd_exposure_key) + _CountStringValue(selected_usd, rep.usd_exposure_key);
            int setup_hits = _CountStringValue(selected_setups, rep.setup_class);

            double diversity_bonus = (setup_hits == 0 ? InpPortfolioSchedulerDiversityBonus : 0.0);
            double score = rep.net_reward_after_cost_r * InpPortfolioSchedulerEvWeight
                           + rep.setup_score * 0.02
                           - cluster_hits * InpPortfolioSchedulerCorrPenalty
                           - session_hits * InpPortfolioSchedulerSessionPenalty
                           - usd_hits * InpPortfolioSchedulerUsdPenalty
                           + diversity_bonus;

            if(score > best_score){
               best_idx = i;
               best_score = score;
               best_session_conc = (double)session_hits;
               best_usd_conc = (double)usd_hits;
               best_cluster_conc = (double)cluster_hits;
               best_diversity = diversity_bonus;
            }
         }

         if(best_idx < 0) break;

         int n = ArraySize(selected_symbols);
         TradePlan rep = m_scan_candidates[rep_indexes[best_idx]];
         ArrayResize(selected_symbols, n + 1);
         ArrayResize(selected_clusters, n + 1);
         ArrayResize(selected_sessions, n + 1);
         ArrayResize(selected_usd, n + 1);
         ArrayResize(selected_setups, n + 1);
         ArrayResize(selected_scores, n + 1);
         ArrayResize(selected_session_conc, n + 1);
         ArrayResize(selected_usd_conc, n + 1);
         ArrayResize(selected_cluster_conc, n + 1);
         ArrayResize(selected_diversity, n + 1);
         selected_symbols[n] = symbols[best_idx];
         selected_clusters[n] = rep.portfolio_cluster;
         selected_sessions[n] = rep.po3.session_name;
         selected_usd[n] = rep.usd_exposure_key;
         selected_setups[n] = rep.setup_class;
         selected_scores[n] = best_score;
         selected_session_conc[n] = best_session_conc;
         selected_usd_conc[n] = best_usd_conc;
         selected_cluster_conc[n] = best_cluster_conc;
         selected_diversity[n] = best_diversity;
      }

      for(int i=0; i<ArraySize(m_scan_candidates); i++){
         int sel_idx = -1;
         for(int j=0; j<ArraySize(selected_symbols); j++){
            if(selected_symbols[j] == m_scan_candidates[i].symbol){
               sel_idx = j;
               break;
            }
         }
         if(sel_idx >= 0){
            m_scan_candidates[i].portfolio_rank = sel_idx + 1;
            m_scan_candidates[i].portfolio_score = selected_scores[sel_idx];
            m_scan_candidates[i].session_concentration = selected_session_conc[sel_idx];
            m_scan_candidates[i].usd_concentration = selected_usd_conc[sel_idx];
            m_scan_candidates[i].cluster_concentration = selected_cluster_conc[sel_idx];
            m_scan_candidates[i].diversity_bonus = selected_diversity[sel_idx];
            m_scan_candidates[i].scheduler_action = "selected";
            m_scan_candidates[i].scheduler_reason = "portfolio_scheduler";
         } else {
            m_scan_candidates[i].portfolio_rank = 0;
            m_scan_candidates[i].portfolio_score = m_scan_candidates[i].net_reward_after_cost_r;
            m_scan_candidates[i].session_concentration = (double)_ExistingSessionCount(m_scan_candidates[i].po3.session_name);
            m_scan_candidates[i].usd_concentration = (double)_ExistingUsdCount(m_scan_candidates[i].usd_exposure_key);
            m_scan_candidates[i].cluster_concentration = (double)_ExistingClusterCount(m_scan_candidates[i].portfolio_cluster);
            m_scan_candidates[i].diversity_bonus = 0.0;
            m_scan_candidates[i].scheduler_action = "deferred";
            m_scan_candidates[i].scheduler_reason = "scan_limit";
         }
      }

      for(int s=0; s<ArraySize(selected_symbols); s++){
         TradePlan group[];
         ArrayResize(group, 0);
         for(int j=0; j<ArraySize(m_scan_candidates); j++){
            if(m_scan_candidates[j].symbol != selected_symbols[s]) continue;
            int n = ArraySize(group);
            ArrayResize(group, n + 1);
            group[n] = m_scan_candidates[j];
         }
         if(ArraySize(group) == 0) continue;
         _SortCandidateGroup(group);
         if(ArraySize(group) > MathMax(1, InpMaxFvgCandidatesPerSymbol))
            ArrayResize(group, MathMax(1, InpMaxFvgCandidatesPerSymbol));
         _QueueCandidateGroup(group);
      }
      ArrayResize(m_scan_candidates, 0);
      _LogSetupFunnel();
   }

   void _FillSyntheticContextLevels(const string symbol, PO3Context &ctx, const MqlRates &rates[], const int got,
                                    const int start_idx, const int end_idx) {
      double hi = 0.0, lo = 0.0;
      int to = MathMin(end_idx, got - 1);
      for(int i=MathMax(1, start_idx); i<=to; i++){
         if(rates[i].high <= 0 || rates[i].low <= 0) continue;
         if(hi <= 0 || rates[i].high > hi) hi = rates[i].high;
         if(lo <= 0 || rates[i].low < lo) lo = rates[i].low;
      }
      ctx.dr_high = hi;
      ctx.dr_low = lo;
      ctx.dr_mid = (hi > lo && lo > 0 ? (hi + lo) * 0.5 : 0.0);
      ctx.swing_high = hi;
      ctx.swing_low = lo;
      ctx.prev_day_high = iHigh(symbol, PERIOD_D1, 1);
      ctx.prev_day_low = iLow(symbol, PERIOD_D1, 1);
      ctx.prev_week_high = iHigh(symbol, PERIOD_W1, 1);
      ctx.prev_week_low = iLow(symbol, PERIOD_W1, 1);
      ctx.liquidity_target = (ctx.bias_long ? hi : lo);
      ctx.liquidity_target_high = ctx.bias_long;
      ctx.liquidity_kind = (ctx.bias_long ? "micro_range_high" : "micro_range_low");
      ctx.liquidity_cluster_count = 1;
      m_po3.PopulateSessionContext(symbol, rates[1].time, ctx);
   }

   void _InitBaseFromSyntheticContext(TradePlan &base, const string symbol, const PO3Context &ctx) {
      ZeroMemory(base);
      base.symbol = symbol;
      base.is_buy = ctx.bias_long;
      base.htf = PO3EffectiveHTF();
      base.ltf = PO3EffectiveEntryTF();
      base.confirm_tf = PO3EffectiveConfirmTF();
      base.po3 = ctx;
      base.arm_max_bars = _EffectiveWatchlistMaxBars();
      base.armed = false;
      base.mid_touched = !InpRequireFvgMidMitigation;
      base.b50_touched = !InpWaitB50OnM1;
      base.bars_waited = 0;
      base.last_confirm_bar_time = _LastClosedBarTime(symbol, base.confirm_tf);
      base.created_at = TimeLocal();
      base.last_score_refresh = base.created_at;
   }

   bool _BuildMicroContinuationBase(const string symbol, TradePlan &base, string &reason) {
      reason = "ok";
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, PO3EffectiveEntryTF(), 0, MathMax(140, InpMinLtfBars + 40), rates);
      if(got < MathMax(40, InpMinLtfBars)){ reason = "continuation_no_impulse"; return false; }
      double atr = ATRFromRates(rates, got, 14);
      if(atr <= 0.0){ reason = "continuation_no_impulse"; return false; }
      double slope = TrendSlopePct(rates, got, 34);
      double trend_strength = TrendStrength(rates, got, 34);
      int best_idx = -1;
      double best_score = -1.0;
      for(int i=1; i<MathMin(got-2, 16); i++){
         double range = rates[i].high - rates[i].low;
         double body = MathAbs(rates[i].close - rates[i].open);
         if(range <= 0.0) continue;
         int dir = (rates[i].close >= rates[i].open ? 1 : -1);
         if((dir > 0 && slope < -0.00010) || (dir < 0 && slope > 0.00010)) continue;
         double body_frac = body / range;
         double range_atr = range / atr;
         if(body_frac < MathMax(0.12, InpDispBodyFracMin * 0.70)) continue;
         if(range_atr < MathMax(0.18, InpDispRangeATRMin * 0.65)) continue;
         double score = body_frac * 4.0 + range_atr * 2.0 + trend_strength * 8.0;
         if(score > best_score){ best_score = score; best_idx = i; }
      }
      if(best_idx < 0){ reason = "continuation_no_impulse"; return false; }

      bool is_buy = (rates[best_idx].close >= rates[best_idx].open);
      PO3Context ctx;
      ZeroMemory(ctx);
      ctx.valid = true;
      ctx.bias_long = is_buy;
      ctx.bias_short = !is_buy;
      ctx.has_sweep = false;
      ctx.has_displacement = true;
      ctx.has_bos = false;
      ctx.t_sweep = 0;
      ctx.t_disp = rates[best_idx].time;
      ctx.sweep_side = "none_continuation";
      ctx.structure_type = "micro_continuation";
      ctx.htf_structure_type = "micro_synthetic";
      ctx.ltf_structure_type = "impulse_continuation";
      ctx.final_setup_class = "micro_continuation_fvg";
      ctx.po3_scope = "micro_po3";
      ctx.context_tier = "B";
      ctx.context_score = MathMin(10.0, best_score);
      ctx.context_age_bars = best_idx;
      ctx.htf_pretrend_dir = (is_buy ? 1 : -1);
      ctx.ltf_pretrend_dir = (is_buy ? 1 : -1);
      ctx.displacement_score = MathMin(10.0, best_score);
      ctx.displacement_body_frac = CandleBodyFrac(rates[best_idx]);
      ctx.displacement_range_atr = (rates[best_idx].high - rates[best_idx].low) / atr;
      ctx.displacement_volume_ratio = 1.0;
      ctx.manip_low = rates[best_idx].low;
      ctx.manip_high = rates[best_idx].high;
      _FillSyntheticContextLevels(symbol, ctx, rates, got, 1, 80);
      if(is_buy) ctx.manip_low = MathMin(ctx.manip_low, rates[best_idx+1].low);
      else       ctx.manip_high = MathMax(ctx.manip_high, rates[best_idx+1].high);
      PO3SetState(ctx, PO3_DISPLACEMENT_CONFIRMED, "micro_continuation_impulse");
      _InitBaseFromSyntheticContext(base, symbol, ctx);
      return true;
   }

   bool _BuildFailedBreakoutBase(const string symbol, TradePlan &base, string &reason) {
      reason = "ok";
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, PO3EffectiveEntryTF(), 0, MathMax(140, InpMinLtfBars + 50), rates);
      if(got < MathMax(60, InpMinLtfBars)){ reason = "failed_breakout_no_reclaim"; return false; }
      double range_high = 0.0, range_low = 0.0;
      for(int i=12; i<MathMin(got, 90); i++){
         if(range_high <= 0.0 || rates[i].high > range_high) range_high = rates[i].high;
         if(range_low <= 0.0 || rates[i].low < range_low) range_low = rates[i].low;
      }
      if(range_high <= range_low || range_low <= 0.0){ reason = "failed_breakout_no_reclaim"; return false; }
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      int reclaim_idx = -1;
      bool is_buy = true;
      double best_score = -1.0;
      for(int i=1; i<MathMin(got-2, 12); i++){
         double body = MathAbs(rates[i].close - rates[i].open);
         double range = rates[i].high - rates[i].low;
         double body_frac = (range > 0.0 ? body / range : 0.0);
         bool bull_reclaim = (rates[i].low < range_low - point && rates[i].close > range_low && rates[i].close > rates[i].open);
         bool bear_reclaim = (rates[i].high > range_high + point && rates[i].close < range_high && rates[i].close < rates[i].open);
         if(!bull_reclaim && !bear_reclaim) continue;
         double excursion = (bull_reclaim ? (range_low - rates[i].low) : (rates[i].high - range_high));
         double score = body_frac * 5.0 + MathMax(0.0, excursion / point) * 0.05;
         if(score > best_score){ best_score = score; reclaim_idx = i; is_buy = bull_reclaim; }
      }
      if(reclaim_idx < 0){ reason = "failed_breakout_no_reclaim"; return false; }

      PO3Context ctx;
      ZeroMemory(ctx);
      ctx.valid = true;
      ctx.bias_long = is_buy;
      ctx.bias_short = !is_buy;
      ctx.has_sweep = false;
      ctx.has_displacement = true;
      ctx.has_bos = false;
      ctx.t_sweep = rates[reclaim_idx].time;
      ctx.t_disp = rates[reclaim_idx].time;
      ctx.sweep_side = (is_buy ? "failed_breakout_below_range_reclaim" : "failed_breakout_above_range_reclaim");
      ctx.structure_type = "micro_failed_breakout_reclaim";
      ctx.htf_structure_type = "micro_synthetic";
      ctx.ltf_structure_type = "failed_breakout_reclaim";
      ctx.final_setup_class = "micro_failed_breakout_reclaim";
      ctx.po3_scope = "micro_po3";
      ctx.context_tier = "B";
      ctx.context_score = MathMin(10.0, MathMax(3.0, best_score));
      ctx.context_age_bars = reclaim_idx;
      ctx.htf_pretrend_dir = (is_buy ? 1 : -1);
      ctx.ltf_pretrend_dir = (is_buy ? 1 : -1);
      ctx.displacement_score = MathMin(10.0, MathMax(3.0, best_score));
      ctx.manip_low = (is_buy ? rates[reclaim_idx].low : range_low);
      ctx.manip_high = (is_buy ? range_high : rates[reclaim_idx].high);
      _FillSyntheticContextLevels(symbol, ctx, rates, got, 12, 90);
      ctx.dr_high = range_high;
      ctx.dr_low = range_low;
      ctx.dr_mid = (range_high + range_low) * 0.5;
      ctx.liquidity_target = ctx.dr_mid;
      ctx.liquidity_target_high = is_buy;
      ctx.liquidity_kind = "failed_breakout_range_mid";
      PO3SetState(ctx, PO3_DISPLACEMENT_CONFIRMED, "micro_failed_breakout_reclaim");
      _InitBaseFromSyntheticContext(base, symbol, ctx);
      return true;
   }

   bool _PopulateBaseRegime(TradePlan &base, string &reason) {
      return _RegimeGateOkEx(base.symbol, base.htf, base.is_buy, base.po3,
                             base.atr_pct, base.trend_strength, base.trend_slope_pct,
                             base.adx_value, base.adr_pct, base.session_vol_ratio,
                             base.vwap_dist_atr, base.compression_score, base.expansion_score,
                             base.news_risk, reason);
   }

   bool _AppendCandidatesFromBase(TradePlan &base, const string &branches[], const string flow_name) {
      string regime_reason = "";
      if(!_PopulateBaseRegime(base, regime_reason)){
         _LogSetupReject(base.symbol, "regime", regime_reason, "flow=" + flow_name);
         return false;
      }
      FVGZone fvg_cands[];
      FVGDiagnostics fvg_diag;
      FVGDiagReset(fvg_diag);
      if(!m_fvg.FindCandidates(base.symbol, base.ltf, base.po3, MathMax(1, InpMaxFvgCandidatesPerSymbol), fvg_cands, fvg_diag)){
         m_funnel_raw_fvgs += fvg_diag.raw_fvg_found;
         m_funnel_accepted_fvgs += fvg_diag.accepted;
         string miss_reason = (flow_name == "micro_continuation_fvg" ? "continuation_fvg_missing" : "failed_breakout_fvg_missing");
         _LogSetupReject(base.symbol, "fvg", miss_reason, FVGDiagSummary(fvg_diag) + " flow=" + flow_name);
         return false;
      }
      m_funnel_raw_fvgs += fvg_diag.raw_fvg_found;
      m_funnel_accepted_fvgs += fvg_diag.accepted;
      m_funnel_fvg_candidates_created += ArraySize(fvg_cands);
      m_total_fvg_candidates_created += ArraySize(fvg_cands);

      bool added = false;
      for(int i=0; i<ArraySize(fvg_cands); i++){
         for(int b=0; b<ArraySize(branches); b++){
            TradePlan p;
            ZeroMemory(p);
            string reject_reason = "";
            if(!_TryBuildCandidateFromBranch(base, fvg_cands[i], branches[b], p, reject_reason)){
               _WriteRejectedShadowCandidate(base, fvg_cands[i], branches[b], reject_reason);
               if(reject_reason == "exclusive_breaker_retest_virgin_strong_origin_only")
                  continue;
               string reject_stage = "branch";
               if(StringFind(reject_reason, "target_") >= 0 || StringFind(reject_reason, "liquidity_target") >= 0 ||
                  StringFind(reject_reason, "synthetic_target") >= 0 || StringFind(reject_reason, "structural_stop") >= 0 ||
                  StringFind(reject_reason, "fvg_too_small") >= 0 || StringFind(reject_reason, "broker_distance") >= 0)
                  reject_stage = "plan_price";
               else if(StringFind(reject_reason, "cost") >= 0 || StringFind(reject_reason, "net_reward") >= 0)
                  reject_stage = "edge";
               _LogSetupReject(base.symbol, reject_stage, reject_reason,
                               "flow=" + flow_name
                               + " candidate=" + IntegerToString(i)
                               + " branch=" + branches[b]);
               continue;
            }
            p.trade_key = _MakeTradeKey(p);
            _WriteShadowCandidateRecord(p, "pre_ai_accept", "");
            _QueueScanCandidate(p);
            m_funnel_branch_candidates++;
            added = true;
            _Journal(base.symbol + " " + flow_name + " accepted"
                     + " setup_family=" + p.setup_family
                     + " branch=" + p.entry_branch
                     + " setup_score=" + DoubleToString(p.setup_score, 2)
                     + " rr2=" + DoubleToString(_ExecutionRR2(p), 2)
                     + " heuristic_quality_estimate=" + DoubleToString(p.heuristic_quality_estimate, 3)
                     + " net_reward_after_cost_r=" + DoubleToString(p.net_reward_after_cost_r, 3)
                     + " execution_cost_r=" + DoubleToString(p.execution_cost_r, 3)
                     + " management_profile=" + p.management_profile);
         }
      }
      return added;
   }

   bool _EvaluateScalpFamilies(const string symbol) {
      if(!StrategyAllowsScalpContinuation()) return false;
      bool added = false;
      TradePlan cont_base;
      string cont_reason = "";
      if(_BuildMicroContinuationBase(symbol, cont_base, cont_reason)){
         string cont_branches[];
         ArrayResize(cont_branches, 3);
         cont_branches[0] = "fvg_edge";
         cont_branches[1] = "fvg_mid";
         cont_branches[2] = "continuation_reentry";
         added = (_AppendCandidatesFromBase(cont_base, cont_branches, "micro_continuation_fvg") || added);
      } else {
         _LogSetupReject(symbol, "continuation", cont_reason, "flow=micro_continuation_fvg");
      }
      TradePlan fail_base;
      string fail_reason = "";
      if(_BuildFailedBreakoutBase(symbol, fail_base, fail_reason)){
         string fail_branches[];
         ArrayResize(fail_branches, 3);
         fail_branches[0] = "fvg_edge";
         fail_branches[1] = "fvg_mid";
         fail_branches[2] = "range_reentry";
         added = (_AppendCandidatesFromBase(fail_base, fail_branches, "micro_failed_breakout_reclaim") || added);
      } else {
         _LogSetupReject(symbol, "failed_breakout", fail_reason, "flow=micro_failed_breakout_reclaim");
      }
      return added;
   }

   // Build plan for a symbol; returns true if plan created (queued either to AI, watchlist, or direct entry)
   bool EvaluateSymbol(const string symbol) {
      m_funnel_scans++;
      m_total_scans++;
      if(!_SymbolEligible(symbol)){
         _LogSetupReject(symbol, "scan", "symbol_not_eligible", "");
         _Journal(symbol + " skipped: symbol not eligible");
         return false;
      }
      if(_SymbolBusy(symbol)){
         _LogSetupReject(symbol, "scan", "symbol_busy", "");
         _Journal(symbol + " skipped: symbol busy");
         return false;
      }

      PO3Context po3;
      PO3BuildStats po3_stats;
      ENUM_TIMEFRAMES effective_htf = PO3EffectiveHTF();
      ENUM_TIMEFRAMES effective_entry_tf = PO3EffectiveEntryTF();
      ENUM_TIMEFRAMES effective_confirm_tf = PO3EffectiveConfirmTF();
      bool po3_ok = m_po3.Build(symbol, effective_htf, po3, po3_stats);
      m_funnel_closed_sweeps_found += po3_stats.sweep_found;
      m_funnel_displacement_passed += po3_stats.displacement_passed;
      m_funnel_tier_a_contexts += po3_stats.tier_a_contexts;
      m_funnel_tier_b_contexts += po3_stats.tier_b_contexts;
      if(!po3_ok){
         if(StrategyAllowsScalpContinuation()){
            bool scalp_added = _EvaluateScalpFamilies(symbol);
            if(scalp_added) return true;
         }
         _LogSetupReject(symbol, "po3", po3_stats.last_reject_reason,
                         "dr_checked=" + IntegerToString(po3_stats.dr_checked)
                         + " sweep_found=" + IntegerToString(po3_stats.sweep_found)
                         + " displacement_passed=" + IntegerToString(po3_stats.displacement_passed)
                         + " disp_missing=" + IntegerToString(po3_stats.displacement_missing)
                         + " bos_missing=" + IntegerToString(po3_stats.bos_missing)
                         + " disp_quality_failed=" + IntegerToString(po3_stats.displacement_quality_failed)
                         + " dr_width_rejected=" + IntegerToString(po3_stats.dr_width_rejected));
         _Journal(symbol + " skipped: no valid PO3 context reason=" + po3_stats.last_reject_reason
                  + " dr_checked=" + IntegerToString(po3_stats.dr_checked)
                  + " sweep_found=" + IntegerToString(po3_stats.sweep_found)
                  + " displacement_passed=" + IntegerToString(po3_stats.displacement_passed)
                  + " disp_missing=" + IntegerToString(po3_stats.displacement_missing)
                  + " bos_missing=" + IntegerToString(po3_stats.bos_missing)
                  + " disp_quality_failed=" + IntegerToString(po3_stats.displacement_quality_failed)
                  + " dr_width_rejected=" + IntegerToString(po3_stats.dr_width_rejected));
         return false;
      }
      if(!StrategyAllowsFullPO3()){
         return _EvaluateScalpFamilies(symbol);
      }
      m_funnel_po3_context_created++;
      m_total_po3_context_created++;
      _Journal(symbol + " PO3 context created tier=" + po3.context_tier
               + " state=" + po3.po3_state
               + " structure=" + po3.structure_type
               + " scope=" + po3.po3_scope
               + " score=" + DoubleToString(po3.context_score, 2)
               + " sweep=" + TimeToString(po3.t_sweep, TIME_DATE|TIME_MINUTES)
               + " disp=" + TimeToString(po3.t_disp, TIME_DATE|TIME_MINUTES)
               + " bos=" + TimeToString(po3.t_bos, TIME_DATE|TIME_MINUTES)
               + " stats_dr_checked=" + IntegerToString(po3_stats.dr_checked)
               + " stats_sweep_found=" + IntegerToString(po3_stats.sweep_found)
               + " stats_displacement_passed=" + IntegerToString(po3_stats.displacement_passed)
               + " stats_bos_missing=" + IntegerToString(po3_stats.bos_missing));

      TradePlan base;
      ZeroMemory(base);
      base.symbol = symbol;
      base.is_buy = po3.bias_long;
      base.htf = effective_htf;
      base.ltf = effective_entry_tf;
      base.confirm_tf = effective_confirm_tf;
      base.po3 = po3;
      base.arm_max_bars = _EffectiveWatchlistMaxBars();
      base.armed = false;
      base.mid_touched = !InpRequireFvgMidMitigation;
      base.b50_touched = !InpWaitB50OnM1;
      base.bars_waited = 0;
      base.last_confirm_bar_time = _LastClosedBarTime(symbol, base.confirm_tf);
      base.armed_at = 0;
      base.tp1_done = false;
      base.created_at = TimeLocal();
      base.ai_requested_at = 0;
      base.last_score_refresh = base.created_at;

      if(InpRequireLtfStructure && !(po3.ltf_bos || po3.ltf_mss || po3.ltf_choch)){
         _LogSetupReject(symbol, "po3", "missing_required_ltf_structure",
                         "ltf_bos=" + IntegerToString(po3.ltf_bos ? 1 : 0)
                         + " ltf_mss=" + IntegerToString(po3.ltf_mss ? 1 : 0)
                         + " ltf_choch=" + IntegerToString(po3.ltf_choch ? 1 : 0));
         _Journal(symbol + " skipped: missing required LTF structure");
         return false;
      }

      // Regime gate on HTF
      string regime_reason = "";
      if(!_RegimeGateOkEx(symbol, effective_htf, base.is_buy, po3,
                          base.atr_pct, base.trend_strength, base.trend_slope_pct,
                          base.adx_value, base.adr_pct, base.session_vol_ratio,
                          base.vwap_dist_atr, base.compression_score, base.expansion_score,
                           base.news_risk, regime_reason)){
         _LogSetupReject(symbol, "regime", regime_reason,
                         "adx=" + DoubleToString(base.adx_value, 1)
                         + " trend=" + DoubleToString(base.trend_strength, 2)
                         + " slope=" + DoubleToString(base.trend_slope_pct, 5)
                         + " session_vol=" + DoubleToString(base.session_vol_ratio, 2)
                         + " expansion=" + DoubleToString(base.expansion_score, 2)
                         + " vwap_atr=" + DoubleToString(base.vwap_dist_atr, 2)
                         + " news_risk=" + DoubleToString(base.news_risk, 2));
         _Journal(symbol + " skipped by regime gate reason=" + regime_reason
                  + " session=" + po3.session_name
                  + " killzone=" + (po3.in_killzone ? "true" : "false")
                  + " adx=" + DoubleToString(base.adx_value, 1)
                  + " trend=" + DoubleToString(base.trend_strength, 2)
                  + " slope=" + DoubleToString(base.trend_slope_pct, 5)
                  + " session_vol=" + DoubleToString(base.session_vol_ratio, 2)
                  + " expansion=" + DoubleToString(base.expansion_score, 2)
                  + " vwap_atr=" + DoubleToString(base.vwap_dist_atr, 2)
                  + " news_risk=" + DoubleToString(base.news_risk, 2));
         return false;
      }

      FVGZone fvg_cands[];
      FVGDiagnostics fvg_diag;
      FVGDiagReset(fvg_diag);
      if(!m_fvg.FindCandidates(symbol, effective_entry_tf, po3, MathMax(1, InpMaxFvgCandidatesPerSymbol), fvg_cands, fvg_diag)){
         m_funnel_raw_fvgs += fvg_diag.raw_fvg_found;
         m_funnel_accepted_fvgs += fvg_diag.accepted;
         int all_disabled = (_AllEntryFamiliesDisabled() ? 1 : 0);
         int no_enabled_family = (_EnabledEntryFamilyCount() <= 0 ? 1 : 0);
         fvg_diag.no_enabled_entry_family = no_enabled_family;
         if(fvg_diag.raw_fvg_found > 0 && fvg_diag.accepted <= 0) fvg_diag.all_candidates_rejected = 1;
         _LogSetupReject(symbol, "fvg", "no_fvg_candidates",
                         FVGDiagSummary(fvg_diag)
                         + " all_entry_families_disabled=" + IntegerToString(all_disabled)
                         + " family_config=" + _EntryFamilyConfigSummary());
         _Journal(symbol + " skipped: no FVG candidates "
                  + FVGDiagSummary(fvg_diag)
                  + " all_entry_families_disabled=" + IntegerToString(all_disabled)
                  + " family_suppressed_by_policy=0"
                  + " family_config=" + _EntryFamilyConfigSummary());
         return false;
      }
      m_funnel_raw_fvgs += fvg_diag.raw_fvg_found;
      m_funnel_accepted_fvgs += fvg_diag.accepted;
      m_funnel_fvg_candidates_created += ArraySize(fvg_cands);
      m_total_fvg_candidates_created += ArraySize(fvg_cands);
      _Journal(symbol + " FVG candidates created count=" + IntegerToString(ArraySize(fvg_cands)));

      bool added = false;
      int branch_disabled_count = 0;
      int family_suppressed_by_policy = 0;
      int no_enabled_execution_family = (_EnabledEntryFamilyCount() <= 0 ? 1 : 0);
      for(int i=0; i<ArraySize(fvg_cands); i++){
         string branches[];
         ArrayResize(branches, 0);
         int bn = 0;
         ArrayResize(branches, ++bn); branches[bn-1] = "fvg_mid";
         ArrayResize(branches, ++bn); branches[bn-1] = "fvg_edge";
         ArrayResize(branches, ++bn); branches[bn-1] = "breaker_retest";
         ArrayResize(branches, ++bn); branches[bn-1] = "ote_inside_fvg";
         ArrayResize(branches, ++bn); branches[bn-1] = "nested_htf_ltf_fvg";
         ArrayResize(branches, ++bn); branches[bn-1] = "session_reentry";
         ArrayResize(branches, ++bn); branches[bn-1] = "range_reentry";
         ArrayResize(branches, ++bn); branches[bn-1] = "continuation_reentry";

         for(int b=0; b<ArraySize(branches); b++){
            TradePlan p;
            ZeroMemory(p);
            string reject_reason = "";
             if(!_TryBuildCandidateFromBranch(base, fvg_cands[i], branches[b], p, reject_reason)){
                _WriteRejectedShadowCandidate(base, fvg_cands[i], branches[b], reject_reason);
                if(reject_reason == "exclusive_breaker_retest_virgin_strong_origin_only")
                   continue;
                if(StringFind(reject_reason, "branch_disabled_") == 0) branch_disabled_count++;
                if(StringFind(reject_reason, "suppressed") >= 0) family_suppressed_by_policy++;
                string reject_stage = "branch";
                if(StringFind(reject_reason, "target_") >= 0 || StringFind(reject_reason, "liquidity_target") >= 0 ||
                   StringFind(reject_reason, "synthetic_target") >= 0 || StringFind(reject_reason, "structural_stop") >= 0 ||
                   StringFind(reject_reason, "fvg_too_small") >= 0 || StringFind(reject_reason, "broker_distance") >= 0)
                   reject_stage = "plan_price";
                else if(StringFind(reject_reason, "pre_ai_floor") >= 0 || StringFind(reject_reason, "setup_score") >= 0)
                   reject_stage = "setup_floor";
                else if(StringFind(reject_reason, "live_po3") >= 0 || StringFind(reject_reason, "po3") >= 0 ||
                        StringFind(reject_reason, "structure_not") >= 0 || StringFind(reject_reason, "displacement") >= 0)
                   reject_stage = "po3";
                _LogSetupReject(symbol, reject_stage, reject_reason,
                                "candidate=" + IntegerToString(i)
                                + " branch=" + branches[b]
                                + " fvg_score=" + DoubleToString(fvg_cands[i].score, 2)
                                + " fvg_width=" + DoubleToString(fvg_cands[i].upper - fvg_cands[i].lower, 5)
                                + " setup_score=" + DoubleToString(p.setup_score, 2)
                                + " setup_floor=" + DoubleToString(p.setup_floor_score, 2)
                                + " rr2=" + DoubleToString(_ExecutionRR2(p), 2));
                _Journal(symbol + " candidate " + IntegerToString(i) + " branch=" + branches[b]
                         + " rejected: " + reject_reason);
               continue;
            }

            p.trade_key = _MakeTradeKey(p);
            _WriteShadowCandidateRecord(p, "pre_ai_accept", "");
            _Journal(symbol + " candidate " + IntegerToString(i)
                     + " branch=" + p.entry_branch
                     + " setup_class=" + p.setup_class
                     + " accepted setup_score=" + DoubleToString(p.setup_score, 2)
                     + " rr2=" + DoubleToString(_ExecutionRR2(p), 2)
                     + " tp=" + p.tp_model);

            _QueueScanCandidate(p);
            m_funnel_branch_candidates++;
            added = true;
         }
      }

      if(StrategyAllowsScalpContinuation()){
         bool scalp_added = _EvaluateScalpFamilies(symbol);
         added = (scalp_added || added);
      }

      if(!added){
         int all_candidates_rejected = (ArraySize(fvg_cands) > 0 ? 1 : 0);
         _Journal(symbol + " produced no executable candidates after filters"
                  + " branch_disabled=" + IntegerToString(branch_disabled_count)
                  + " family_suppressed_by_policy=" + IntegerToString(family_suppressed_by_policy)
                  + " no_enabled_execution_family=" + IntegerToString(no_enabled_execution_family)
                  + " no_enabled_entry_family=" + IntegerToString(no_enabled_execution_family)
                  + " all_candidates_rejected=" + IntegerToString(all_candidates_rejected)
                  + " all_entry_families_disabled=" + IntegerToString(_AllEntryFamiliesDisabled() ? 1 : 0)
                  + " family_config=" + _EntryFamilyConfigSummary());
      }
      return added;
   }

   void ProcessPendingAI() {
      _PruneAiCooldowns();
      for(int i=ArraySize(m_pending_ai)-1; i>=0; i--){
         bool stale = false;
         string reason = "";
         if(m_pending_ai[i].created_at <= 0){
            stale = true;
            reason = "missing_created_at";
         } else if(StringLen(m_pending_ai[i].req_id) == 0 || m_pending_ai[i].ai_requested_at <= 0) {
            stale = true;
            reason = "missing_ai_request_state";
         }
         if(!stale) continue;
         _Journal(m_pending_ai[i].symbol + " pending AI dropped: " + reason);
         int last = ArraySize(m_pending_ai) - 1;
         m_pending_ai[i] = m_pending_ai[last];
         ArrayResize(m_pending_ai, last);
      }

      string req_ids[];
      ArrayResize(req_ids, 0);
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         string req_id = m_pending_ai[i].req_id;
         if(StringLen(req_id) == 0 || _HasStringValue(req_ids, req_id)) continue;
         int n = ArraySize(req_ids);
         ArrayResize(req_ids, n+1);
         req_ids[n] = req_id;
         }

         datetime now = TimeLocal();
         for(int r=0; r<ArraySize(req_ids); r++){
            datetime oldest_request = 0;
            datetime oldest_sim_request = 0;
            ulong oldest_wall_request = 0;
            string group_symbol = "";
            int candidate_count = 1;
            for(int i=0; i<ArraySize(m_pending_ai); i++){
               if(m_pending_ai[i].req_id != req_ids[r]) continue;
               if(StringLen(group_symbol) == 0) group_symbol = m_pending_ai[i].symbol;
               candidate_count = MathMax(candidate_count, m_pending_ai[i].candidate_count);
               if(oldest_request <= 0 || m_pending_ai[i].ai_requested_at < oldest_request)
                  oldest_request = m_pending_ai[i].ai_requested_at;
               datetime sim_request = (m_pending_ai[i].ai_request_time > 0 ? m_pending_ai[i].ai_request_time : m_pending_ai[i].setup_snapshot_time);
               if(sim_request > 0 && (oldest_sim_request <= 0 || sim_request < oldest_sim_request))
                  oldest_sim_request = sim_request;
               if(m_pending_ai[i].ai_requested_wall_ms > 0 && (oldest_wall_request == 0 || m_pending_ai[i].ai_requested_wall_ms < oldest_wall_request))
                  oldest_wall_request = m_pending_ai[i].ai_requested_wall_ms;
            }
            string req_rel = _ReqPath(req_ids[r]);
            string resp_rel = _RespPath(req_ids[r]);
            string group_signature = _PendingGroupSignature(req_ids[r], group_symbol);

            AiDecision dec;
            bool has_response = _PathExists(resp_rel);
            if(has_response){
               int timeout_min = _PendingAiTimeoutMinutes();
               ulong timeout_ms = (ulong)MathMax(1, timeout_min) * 60 * 1000;
               if(oldest_wall_request > 0 &&
                  _WallElapsedMs(oldest_wall_request) >= timeout_ms){
                  _Journal("[stale_response_rejected] request_id=" + req_ids[r]
                           + " reason=ai_response_late_wall_deadline"
                           + " elapsed_wall_ms=" + IntegerToString((long)_WallElapsedMs(oldest_wall_request))
                           + " configured_timeout_ms=" + IntegerToString((long)timeout_ms));
                  _LogSetupReject(group_symbol, "ai_wait_timeout", "ai_wait_timeout_real_time",
                                  "req_id=" + req_ids[r]
                                  + " response=late_after_deadline");
                  m_total_tester_ai_wait_timeout++;
                  _ArchivePendingArtifacts(req_ids[r], "stale");
                  _RemovePendingGroup(req_ids[r]);
                  continue;
               }
               if(!m_ai.TryReadDecision(req_ids[r], dec)){
                  if(oldest_wall_request > 0 && _WallElapsedMs(oldest_wall_request) < 5000){
                     ulong now_ms = _WallClockMs();
                     if(_ShouldLogAiWait(req_ids[r], now_ms))
                        _Journal(group_symbol + " AI response unreadable req_id=" + req_ids[r] + " -> waiting for stable file");
                     continue;
                  }
                  _RememberAiCooldown(group_symbol, group_signature, "response_unreadable");
                  if(!InpAiStrict && InpAllowRuleOnlyFallback){
                     _Journal(group_symbol + " AI response unreadable req_id=" + req_ids[r] + " -> governed fallback");
                     _RejectPendingGroupAsNonTrading(req_ids[r], "invalid_json");
                  } else {
                     _Journal(group_symbol + " AI response unreadable req_id=" + req_ids[r]
                              + " -> dropped "
                              + (InpAllowRuleOnlyFallback ? "strict mode" : "rule-only fallback disabled"));
                     _ArchivePendingArtifacts(req_ids[r], "quarantined");
                     _RemovePendingGroup(req_ids[r]);
                  }
                  continue;
               }
               datetime advisory_sim = TimeCurrent();
               int age_sim_min = 0;
               if(oldest_sim_request > 0 && advisory_sim >= oldest_sim_request)
                  age_sim_min = (int)((advisory_sim - oldest_sim_request) / 60);
               int wall_age_sec = 0;
               if(oldest_wall_request > 0)
                  wall_age_sec = (int)(_WallElapsedMs(oldest_wall_request) / 1000);
               int max_age = MathMax(0, InpTesterMaxAiResultAgeSimMinutes);
               bool tester_runtime = _IsTesterRuntime();
               bool tester_blocking_wait = _TesterLiveAiBlockingWaitMode();
               bool tester_live_wait_debug_mode = _TesterLiveWaitDebugMode();
               bool tester_live_wait_debug_trading_disabled = (tester_live_wait_debug_mode &&
                                                               !InpTesterAllowLiveWaitDebugTrading);
               bool tester_live_wait_sim_time_jump = (tester_live_wait_debug_mode &&
                                                      oldest_sim_request > 0 &&
                                                      age_sim_min > max_age);
               // During the explicit blocking tester wait, simulated age is a
               // diagnostic only. The immutable wall deadline and full request
               // identity govern freshness. Debug trading still requires its
               // separate explicit acknowledgement.
               bool tester_live_wait_non_tradeable = tester_live_wait_debug_trading_disabled;
               string tester_live_wait_non_tradeable_reason = "none";
               if(tester_live_wait_debug_trading_disabled)
                  tester_live_wait_non_tradeable_reason = "live_wait_debug_trading_disabled";
               bool tester_stale_ai = (tester_runtime &&
                                       !tester_blocking_wait &&
                                       InpTesterRejectStaleAiResults &&
                                       oldest_sim_request > 0 &&
                                       age_sim_min > max_age);
               string freshness_action = "";
               if(tester_live_wait_non_tradeable) freshness_action = "record_response_do_not_trade";
               else if(tester_runtime && tester_blocking_wait) freshness_action = "accept_after_blocking_wait";
               else freshness_action = (tester_stale_ai ? "reject" : (tester_runtime && InpTesterFreezeAiExecutionSnapshot ? "snapshot_validate" : "accept"));
               _Journal("[ai_freshness] tester=" + (tester_runtime ? "true" : "false")
                        + " pause_scan=" + (tester_blocking_wait ? "true" : "false")
                        + " request_time=" + (oldest_sim_request > 0 ? TimeToString(oldest_sim_request, TIME_DATE|TIME_MINUTES) : "0")
                        + " advisory_time=" + TimeToString(advisory_sim, TIME_DATE|TIME_MINUTES)
                        + " age_sim_min=" + IntegerToString(age_sim_min)
                        + " wall_age_sec=" + IntegerToString(wall_age_sec)
                        + " max=" + IntegerToString(max_age)
                        + " action=" + freshness_action
                        + " sim_age_diagnostic_only=" + (tester_blocking_wait ? "true" : "false")
                        + " live_mode_sim_age_ignored=" + (!tester_runtime ? "true" : "false"));
               if(tester_runtime && tester_blocking_wait && !tester_live_wait_non_tradeable)
                  m_total_ai_results_accepted_after_blocking_wait++;
               if(tester_live_wait_non_tradeable){
                  m_funnel_ai_advisories++;
                  m_total_ai_advisories++;
                  if(tester_live_wait_sim_time_jump) m_total_tester_live_wait_non_tradeable_sim_jump++;
                  if(tester_live_wait_debug_trading_disabled) m_total_tester_live_wait_debug_trading_disabled++;
                  TradePlan record_group[];
                  int record_count = _CollectPendingGroupPlans(req_ids[r], record_group);
                  for(int si=0; si<record_count; si++){
                     record_group[si].ai_advisory_time = advisory_sim;
                     record_group[si].ai_result_age_sim_minutes = age_sim_min;
                     record_group[si].tester_ai_result_stale = true;
                  }
                  string cache_signature = (record_count > 0 ? _TesterAiCacheSignature(record_group) : group_signature);
                  bool stored = false;
                  _Journal("[tester_ai_mode] mode=live_wait_debug"
                           + " sim_age_min=" + IntegerToString(age_sim_min)
                           + " wall_age_sec=" + IntegerToString(wall_age_sec)
                           + " action=record_response_do_not_trade"
                           + " reason=" + tester_live_wait_non_tradeable_reason
                           + " allow_trading=" + (InpTesterAllowLiveWaitDebugTrading ? "true" : "false")
                           + " sim_time_jump=" + (tester_live_wait_sim_time_jump ? "true" : "false"));
                  _Journal("[ai_cache] stored_from_live_wait_debug=" + (stored ? "true" : "false")
                           + " req_id=" + req_ids[r]
                           + " signature=" + cache_signature);
                  _Journal("[tester_ai_mode] live_wait_debug_response_recorded=true tradable=false");
                  if(InpTesterFreezeAiExecutionSnapshot && record_count > 0){
                     int diag_idx = dec.chosen_index;
                     if(diag_idx < 0 || diag_idx >= record_count) diag_idx = 0;
                     string current_precheck_reason = "";
                     bool current_precheck = _WatchlistStillValidEx(record_group[diag_idx], current_precheck_reason);
                     _Journal("[snapshot_diagnostic] target_validation_at_request_time=recorded_only"
                              + " current_state_precheck=" + (current_precheck ? "pass" : "fail")
                              + " reason=" + current_precheck_reason
                              + " trading=false");
                  }
                  string tester_live_wait_reject_reason = (tester_live_wait_debug_trading_disabled ?
                                                          "tester_live_wait_debug_trading_disabled" :
                                                          "tester_live_wait_result_not_tradeable_due_to_sim_time_jump");
                  _LogSetupReject(group_symbol, "tester_ai_workflow", tester_live_wait_reject_reason,
                                  "req_id=" + req_ids[r]
                                  + " age_sim_min=" + IntegerToString(age_sim_min)
                                  + " wall_age_sec=" + IntegerToString(wall_age_sec)
                                  + " max=" + IntegerToString(max_age)
                                  + " allow_trading=" + (InpTesterAllowLiveWaitDebugTrading ? "true" : "false")
                                  + " cached=" + (stored ? "true" : "false"));
                  _ArchivePendingArtifacts(req_ids[r], "completed");
                  _RemovePendingGroup(req_ids[r]);
                  continue;
               }
               if(tester_stale_ai){
                  m_funnel_ai_result_stale_in_tester++;
                  m_total_ai_results_rejected_stale++;
                  for(int si=0; si<ArraySize(m_pending_ai); si++){
                     if(m_pending_ai[si].req_id != req_ids[r]) continue;
                     m_pending_ai[si].ai_advisory_time = advisory_sim;
                     m_pending_ai[si].ai_result_age_sim_minutes = age_sim_min;
                     m_pending_ai[si].tester_ai_result_stale = true;
                  }
                  _RememberAiCooldown(group_symbol, group_signature, "ai_result_stale_in_tester");
                  _LogSetupReject(group_symbol, "ai_result_freshness", "ai_result_stale_in_tester",
                                  "req_id=" + req_ids[r]
                                  + " age_sim_min=" + IntegerToString(age_sim_min)
                                  + " max=" + IntegerToString(max_age));
                  _ArchivePendingArtifacts(req_ids[r], "stale");
                  _RemovePendingGroup(req_ids[r]);
                  continue;
               }
            } else {
               bool request_still_pending = _PathExists(req_rel);
               bool timed_out = false;
               for(int i=0; i<ArraySize(m_pending_ai); i++){
                  if(m_pending_ai[i].req_id != req_ids[r]) continue;
                  if(_PendingAiTimedOut(m_pending_ai[i])){
                     timed_out = true;
                     break;
                  }
               }
               if(timed_out){
                  string timeout_reason = (MQLInfoInteger(MQL_TESTER) ? "ai_wait_timeout_real_time" : "timeout");
                  bool allow_fallback = (!InpAiStrict && InpAllowRuleOnlyFallback);
                  if(MQLInfoInteger(MQL_TESTER) && InpLiveFailClosedOnAIFailure)
                     allow_fallback = false;
                  _RememberAiCooldown(group_symbol, group_signature, timeout_reason);
                  if(allow_fallback) _RejectPendingGroupAsNonTrading(req_ids[r], "timeout");
                  else {
                     if(MQLInfoInteger(MQL_TESTER))
                        m_total_tester_ai_wait_timeout++;
                     _LogSetupReject(group_symbol, "ai_wait_timeout", timeout_reason,
                                     "req_id=" + req_ids[r]
                                     + " elapsed_wall_sec=" + IntegerToString(oldest_wall_request > 0 ? (int)(_WallElapsedMs(oldest_wall_request) / 1000) : 0)
                                     + " timeout_min=" + IntegerToString(_PendingAiTimeoutMinutes()));
                     _Journal(group_symbol + " AI timeout req_id=" + req_ids[r]
                              + " response=missing request=" + (request_still_pending ? "present" : "missing")
                              + " reason=" + timeout_reason
                              + " -> dropped "
                              + (InpAllowRuleOnlyFallback ? "strict mode" : "rule-only fallback disabled"));
                     _ArchivePendingArtifacts(req_ids[r], "timed_out");
                     _RemovePendingGroup(req_ids[r]);
                  }
                  continue;
               }
               continue;
            }

         m_funnel_ai_advisories++;
         m_total_ai_advisories++;
         TradePlan decision_group[];
         ArrayResize(decision_group, 0);
         for(int i=0; i<ArraySize(m_pending_ai); i++){
            if(m_pending_ai[i].req_id != req_ids[r]) continue;
            m_pending_ai[i].ai_advisory_time = TimeCurrent();
            if(m_pending_ai[i].ai_request_time > 0 && m_pending_ai[i].ai_advisory_time >= m_pending_ai[i].ai_request_time)
               m_pending_ai[i].ai_result_age_sim_minutes = (int)((m_pending_ai[i].ai_advisory_time - m_pending_ai[i].ai_request_time) / 60);
            int n = ArraySize(decision_group);
            ArrayResize(decision_group, n + 1);
            decision_group[n] = m_pending_ai[i];
         }
         if(ArraySize(decision_group) > 0){
            string decision_cache_signature = _TesterAiCacheSignature(decision_group);
            bool live_wait_debug_mode = (MQLInfoInteger(MQL_TESTER) && _EffectiveTesterAiMode() == TESTER_AI_LIVE_WAIT_DEBUG);
            bool stored_from_live_wait = (live_wait_debug_mode ? false :
                                          _RememberTesterAiDecision(decision_cache_signature, dec));
            if(live_wait_debug_mode){
               _Journal("[ai_cache] stored_from_live_wait_debug=false"
                        + " req_id=" + req_ids[r]
                        + " signature=" + decision_cache_signature
                        + " reason=live_wait_debug_not_replay_authoritative");
               _Journal("[tester_ai_mode] live_wait_debug_response_recorded=true tradable=true");
            }
         }

         string assessment_integrity_reason = "";
         bool assessment_group_ok = _DecisionAssessmentsMatchGroup(dec, decision_group, assessment_integrity_reason);
         bool strict_response_quality = (dec.decision_quality_tier == "FULL_STRUCTURED" || dec.decision_quality_tier == "CACHE_OF_FULL_STRUCTURED");
         bool strict_schema_ok = (dec.mandatory_fields_complete &&
                                  dec.decision_schema_version == AI_DECISION_SCHEMA_VERSION &&
                                  dec.hierarchical_prior_schema_version == HIERARCHICAL_PRIOR_SCHEMA_VERSION &&
                                  dec.repeatability_schema_version == REPEATABILITY_SCHEMA_VERSION &&
                                  strict_response_quality && assessment_group_ok);

         TradePlan selected;
         bool have_selected = false;
         int selected_matches = 0;
         // Identity is candidate_index + candidate_id + candidate_hash.
         // request_execution_fingerprint used to be a fourth condition here.  It is a
         // cost snapshot: every input of _ExecutionFingerprint except spread_r,
         // slippage_r, execution_cost_r and net_reward_after_cost_r is already an
         // input of _CandidateHash, and those four are live broker measurements the
         // execution contract authorises to drift by max_cost_deterioration_r.  Using
         // them to decide WHICH candidate the AI selected could only ever turn a cost
         // wobble into "no candidate was selected".  Presence is still required and
         // the values are revalidated against the contract by
         // _RestoreTesterRequestProvenance before the group is ever queued.
         for(int i=0; i<ArraySize(decision_group); i++){
            if(decision_group[i].candidate_index != dec.chosen_index) continue;
            if(decision_group[i].candidate_id != dec.selected_candidate_id) continue;
            if(decision_group[i].candidate_hash != dec.selected_candidate_hash) continue;
            selected = decision_group[i];
            selected_matches++;
         }
         have_selected = (selected_matches == 1);
         if(!have_selected){
            // The group holds a handful of candidates and this path is a hard reject,
            // so printing the whole group is what makes the failure diagnosable
            // instead of leaving a bare "candidate_hash_mismatch" with no data.
            string group_dump = "";
            for(int i=0; i<ArraySize(decision_group); i++){
               if(StringLen(group_dump) > 0) group_dump += ";";
               group_dump += IntegerToString(decision_group[i].candidate_index)
                           + ":" + decision_group[i].candidate_hash
                           + ":" + decision_group[i].candidate_id;
            }
            _Journal("[selected_candidate_unresolved] req_id=" + req_ids[r]
                     + " matches=" + IntegerToString(selected_matches)
                     + " group_plan_count=" + IntegerToString(ArraySize(decision_group))
                     + " decision_chosen_index=" + IntegerToString(dec.chosen_index)
                     + " decision_candidate_hash=" + dec.selected_candidate_hash
                     + " decision_candidate_id=" + dec.selected_candidate_id
                     + " group=" + group_dump);
            // "ok" is what _DecisionAssessmentsMatchGroup writes on success, so the
            // old StringLen()==0 guard could never fire and the reason was lost.
            if(StringLen(assessment_integrity_reason) == 0 || assessment_integrity_reason == "ok")
               assessment_integrity_reason = "selected_candidate_not_resolved_in_group";
         }

         string deterministic_reason = "candidate_not_selected";
         bool deterministic_pass = false;
         if(have_selected)
            deterministic_pass = _DeterministicExecutionGate(selected, deterministic_reason, false);

         string ai_veto_reason = _AiVetoReason(dec);
         bool hard_veto = (StringLen(ai_veto_reason) > 0);
         if(hard_veto)
            _Journal("[ai_veto] req_id=" + req_ids[r]
                     + " reason=" + ai_veto_reason
                     + " follow_through=" + DoubleToString(dec.follow_through_probability, 4)
                     + " invalidation_risk=" + DoubleToString(dec.invalidation_risk, 4)
                     + " chop_risk=" + DoubleToString(dec.chop_risk, 4)
                     + " cost_risk=" + DoubleToString(dec.cost_risk, 4)
                     + " post_entry_failure_risk=" + DoubleToString(dec.post_entry_failure_risk, 4)
                     + " final_expectancy=" + DoubleToString(dec.final_trade_expectancy_score, 4));

         string ai_threshold_source = "InpMinAiScoreTrend";
         double required_llm_quality_score = (have_selected ? EffectiveLlmQualityScoreThreshold(selected, ai_threshold_source) : InpMinAiScoreTrend);
         // The configured value remains a diagnostic cohort feature. It is an
         // uncalibrated LLM number and has no direct trade authority.
         bool llm_quality_floor_ok = (dec.llm_quality_score >= required_llm_quality_score);
         bool state_approve = (dec.decision_state == "APPROVE");
         bool full_ai_approval = (dec.python_final_allow && dec.model_raw_allow && state_approve);
         bool risk_multiplier_ok = (dec.suggested_risk_multiplier > 0.0 && dec.suggested_risk_multiplier <= 1.0);
         string reject_reason = "ok";
         if(!strict_schema_ok)
            reject_reason = (strict_response_quality ? "ai_quality_schema_incomplete" : "degraded_ai_response_non_trading");
         else if(!have_selected) reject_reason = "candidate_hash_mismatch";
         else if(dec.repeatability_required_live &&
                 (dec.repeatability_status != "REPEATABLE" || !dec.repeatability_trading_eligible))
            reject_reason = (StringLen(dec.repeatability_rejection_code) > 0
                             ? dec.repeatability_rejection_code
                             : "repeatability_unavailable");
         else if(dec.decision_state == "ABSTAIN") reject_reason = "ai_abstain";
         else if(!state_approve || !dec.model_raw_allow || !dec.python_final_allow) reject_reason = "ai_raw_allow_false";
         else if(!risk_multiplier_ok) reject_reason = "resolved_risk_multiplier_zero";
         else if(hard_veto) reject_reason = "ai_veto";
         else if(!deterministic_pass) reject_reason = deterministic_reason;

         dec.llm_quality_score_threshold = required_llm_quality_score;
         dec.llm_quality_threshold_source = ai_threshold_source;
         dec.llm_quality_threshold_passed = llm_quality_floor_ok;
         dec.llm_quality_reject_reason = (reject_reason == "ok" ? "" : reject_reason);
         dec.global_llm_quality_as_hard_floor = InpGlobalAiScoreAsHardFloor;
         bool allow = (reject_reason == "ok" && full_ai_approval);
         dec.mql_final_allow = false;

         _Journal("[decision_authority] model_raw_allow=" + (dec.model_raw_allow ? "true" : "false")
                  + " python_final_allow=" + (dec.python_final_allow ? "true" : "false")
                  + " mql_final_allow=false"
                  + " python_reasons=" + dec.reasons_json
                  + " mql_reasons=" + (reject_reason == "ok" ? "pending_final_mql_execution_gates" : reject_reason));

         _Journal("[decision_scores] req_id=" + req_ids[r]
                  + " candidate_id=" + dec.selected_candidate_id
                  + " candidate_hash=" + dec.selected_candidate_hash
                  + " rule_score=" + DoubleToString(dec.rule_score, 4)
                  + " llm_quality_score=" + DoubleToString(dec.llm_quality_score, 4)
                  + " blended_legacy_score=" + DoubleToString(dec.blended_legacy_score, 4)
                  + " calibrated_win_probability=unavailable"
                  + " expected_net_r=unavailable"
                  + " llm_self_reported_confidence=" + DoubleToString(dec.llm_self_reported_confidence, 4)
                  + " legacy_agreement_confidence=" + DoubleToString(dec.legacy_agreement_confidence, 4)
                  + " legacy_fields_trade_authority=false"
                  + " llm_numeric_authority=diagnostic_only_no_direct_trade_authority"
                  + " family_threshold_pass_diagnostic=" + (llm_quality_floor_ok ? "true" : "false"));
         _Journal("[repeatability_authority] req_id=" + req_ids[r]
                  + " schema=" + dec.repeatability_schema_version
                  + " status=" + dec.repeatability_status
                  + " required_live=" + (dec.repeatability_required_live ? "true" : "false")
                  + " artifact_state=" + dec.repeatability_artifact_state
                  + " rejection_code=" + dec.repeatability_rejection_code
                  + " score_threshold_authority=" + (dec.repeatability_score_threshold_authority ? "true" : "false")
                  + " trading_eligible=" + (dec.repeatability_trading_eligible ? "true" : "false")
                  + " group_key=" + dec.repeatability_group_key);
         if(dec.decision_state == "ABSTAIN")
            _Journal("[ai_abstain] candidate_id=" + dec.selected_candidate_id
                     + " candidate_hash=" + dec.selected_candidate_hash
                     + " reason=" + dec.reasons_json);
         _Journal(group_symbol + " AI advisory req_id=" + req_ids[r]
                  + " raw_allow=" + (dec.raw_allow ? "true" : "false")
                  + " decision_state=" + dec.decision_state
                  + " decision_quality_tier=" + dec.decision_quality_tier
                  + " hard_veto=" + (hard_veto ? "true" : "false")
                  + " threshold_reason=" + reject_reason
                  + " final_allow=" + (allow ? "true" : "false")
                  + " llm_quality_score=" + DoubleToString(dec.llm_quality_score, 2)
                  + " required_llm_quality_score=" + DoubleToString(required_llm_quality_score, 2)
                  + " llm_self_reported_confidence_diagnostic=" + DoubleToString(dec.llm_self_reported_confidence, 2)
                  + " confidence_trade_authority=false"
                  + " chosen=" + IntegerToString(dec.chosen_index)
                  + " candidate_hash_match=" + (have_selected ? "true" : "false")
                  + " assessment_group_ok=" + (assessment_group_ok ? "true" : "false")
                  + " decision_source=" + dec.decision_source);

         for(int shadow_idx=0; shadow_idx<ArraySize(decision_group); shadow_idx++){
            bool shadow_selected = (have_selected &&
                                    decision_group[shadow_idx].candidate_hash == dec.selected_candidate_hash);
            string shadow_stage = (shadow_selected
                                   ? (allow ? "python_approved_mql_pending" : "python_rejected")
                                   : "python_not_selected");
            string shadow_reason = (shadow_selected ? reject_reason : "candidate_not_selected_by_python");
            _WriteShadowDecisionUpdate(decision_group[shadow_idx], dec, shadow_stage,
                                       shadow_reason == "ok" ? "" : shadow_reason, false);
         }

         if(allow){
            m_funnel_ai_final_allow++;
            m_total_ai_final_allow_true++;
            // The plan is no longer pending, but the immutable AI request id is
            // still part of its audit lineage and must survive through entry,
            // fill attribution, close logging, and completed-memory ingestion.
            selected.req_id = req_ids[r];
            selected.ai_requested_at = 0;
            selected.ai_requested_wall_ms = 0;
            selected.last_confirm_bar_time = _LastClosedBarTime(selected.symbol, selected.confirm_tf);
            selected.ai_decision_id = dec.decision_id;
            selected.ai_decision_source = dec.decision_source;
            selected.ai = dec;
            selected.model_raw_allow = dec.model_raw_allow;
            selected.python_final_allow = dec.python_final_allow;
            selected.mql_final_allow = false;
            selected.decision_field_authority_json = dec.decision_field_authority_json;
            selected.python_decision_reasons = dec.reasons_json;
            selected.mql_decision_reasons = "pending_final_mql_execution_gates";
            selected.reasoning_configuration = dec.reasoning_configuration;
            selected.bucket_prior_hash = dec.bucket_prior_hash;
            selected.calibration_artifact_id = dec.calibration_artifact_id;
            selected.repeatability_artifact_id = dec.repeatability_authority_hash;
            selected.request_fingerprint = dec.request_fingerprint;
            selected.response_fingerprint = dec.response_fingerprint;
            selected.hierarchical_prior_artifact_hash = dec.hierarchical_prior_artifact_hash;
            selected.hierarchical_prior_schema_version = dec.hierarchical_prior_schema_version;
            selected.repeatability_status = dec.repeatability_status;
            selected.repeatability_required_live = dec.repeatability_required_live;
            selected.repeatability_artifact_state = dec.repeatability_artifact_state;
            selected.repeatability_rejection_code = dec.repeatability_rejection_code;
            selected.ai.repeatability_required_live = dec.repeatability_required_live;
            selected.ai.repeatability_artifact_state = dec.repeatability_artifact_state;
            selected.ai.repeatability_rejection_code = dec.repeatability_rejection_code;
            selected.repeatability_score_threshold_authority = dec.repeatability_score_threshold_authority;
            selected.repeatability_trading_eligible = dec.repeatability_trading_eligible;
            selected.repeatability_group_key = dec.repeatability_group_key;
            selected.repeatability_authority_hash = dec.repeatability_authority_hash;
            selected.ai_selected_candidate_hash = dec.selected_candidate_hash;
            selected.candidate_hash_match = (selected.candidate_hash == selected.ai_selected_candidate_hash);
            string expected_assessed_fingerprint = _AssessedFingerprintFromDecision(selected, dec);
            if(!selected.candidate_hash_match || expected_assessed_fingerprint != dec.assessed_execution_fingerprint){
               _WriteShadowDecisionUpdate(selected, dec, "mql_rejected_decision_integrity",
                                          (!selected.candidate_hash_match ? "candidate_hash_mismatch" : "execution_fingerprint_mismatch"), false);
               _LogSetupReject(group_symbol, "decision_integrity",
                               (!selected.candidate_hash_match ? "candidate_hash_mismatch" : "execution_fingerprint_mismatch"),
                               "ai_selected_candidate_hash=" + dec.selected_candidate_hash
                               + " executed_candidate_hash=" + selected.candidate_hash
                               + " request_execution_fingerprint=" + selected.request_execution_fingerprint
                               + " expected_assessed_execution_fingerprint=" + expected_assessed_fingerprint
                               + " received_assessed_execution_fingerprint=" + dec.assessed_execution_fingerprint);
               _RemovePendingGroup(req_ids[r]);
               continue;
            }
            string ai_target_reason = "";
            if(!_ApplyAiTargetArbitration(selected, dec, ai_target_reason)){
               _WriteShadowDecisionUpdate(selected, dec, "mql_rejected_target_arbitration",
                                          ai_target_reason, false);
               if(ai_target_reason == "invalid_ai_target_arbitration_response")
                  m_funnel_invalid_ai_target_arbitration_response++;
               _RememberAiCooldown(group_symbol, group_signature, ai_target_reason);
               _LogSetupReject(group_symbol, "ai_target_arbitration", ai_target_reason,
                               "chosen_target_model=" + dec.chosen_target_model
                               + " chosen_tp2=" + DoubleToString(dec.chosen_tp2, 8)
                               + " blocker_class=" + dec.target_blocker_class
                               + " blocker_severity=" + DoubleToString(dec.target_blocker_severity, 2));
               _Journal(group_symbol + " AI target arbitration rejected req_id=" + req_ids[r]
                        + " reason=" + ai_target_reason
                        + " chosen_target_model=" + dec.chosen_target_model
                        + " target_reason=" + dec.target_decision_reason);
               _RemovePendingGroup(req_ids[r]);
               continue;
            }
            double tick_size = SymbolInfoDouble(selected.symbol, SYMBOL_TRADE_TICK_SIZE);
            if(tick_size <= 0.0) tick_size = SymbolInfoDouble(selected.symbol, SYMBOL_POINT);
            if(tick_size <= 0.0) tick_size = 0.00001;
            double assessed_price_tolerance = 2.0 * tick_size;
            if(MathAbs(selected.entry_est - dec.assessed_entry) > assessed_price_tolerance ||
               MathAbs(selected.sl - dec.assessed_sl) > assessed_price_tolerance ||
               MathAbs(selected.tp1 - dec.assessed_tp1) > assessed_price_tolerance ||
               MathAbs(selected.tp2 - dec.assessed_tp2) > assessed_price_tolerance ||
               MathAbs(selected.tp2 - dec.selected_target_price) > assessed_price_tolerance){
               string changed = "";
               if(MathAbs(selected.entry_est - dec.assessed_entry) > assessed_price_tolerance) _AppendChangedComponent(changed, "entry");
               if(MathAbs(selected.sl - dec.assessed_sl) > assessed_price_tolerance) _AppendChangedComponent(changed, "sl");
               if(MathAbs(selected.tp1 - dec.assessed_tp1) > assessed_price_tolerance) _AppendChangedComponent(changed, "tp1");
               if(MathAbs(selected.tp2 - dec.assessed_tp2) > assessed_price_tolerance) _AppendChangedComponent(changed, "tp2");
               _WriteShadowDecisionUpdate(selected, dec, "mql_rejected_execution_fingerprint",
                                          "execution_fingerprint_mismatch:" + changed, false);
               _LogSetupReject(group_symbol, "decision_integrity", "execution_fingerprint_mismatch",
                               "changed_components=" + changed
                               + " assessed_execution_fingerprint=" + dec.assessed_execution_fingerprint);
               _RemovePendingGroup(req_ids[r]);
               continue;
            }
            selected.assessed_execution_fingerprint = dec.assessed_execution_fingerprint;
            selected.assessed_entry = dec.assessed_entry;
            selected.assessed_sl = dec.assessed_sl;
            selected.assessed_tp1 = dec.assessed_tp1;
            selected.assessed_tp2 = dec.assessed_tp2;
            double assessed_risk = MathAbs(dec.assessed_entry - dec.assessed_sl);
            selected.assessed_net_rr = (assessed_risk > 0.0 ? _RewardToTarget(selected.is_buy, dec.assessed_entry, dec.assessed_tp2) / assessed_risk : 0.0);
            if(!(MQLInfoInteger(MQL_TESTER) && _EffectiveTesterAiMode() == TESTER_AI_CACHE_ONLY)){
               selected.assessed_spread_r = selected.spread_r;
               selected.assessed_slippage_r = selected.slippage_r;
               selected.assessed_execution_cost_r = selected.execution_cost_r;
            }
            selected.assessed_symbol = selected.symbol;
            selected.assessed_is_buy = selected.is_buy;
            selected.assessed_setup_code = selected.model_code;
            selected.assessed_setup_family = selected.setup_family;
            selected.assessed_entry_branch = selected.entry_branch;
            selected.assessed_source_t_sweep = selected.source_t_sweep;
            selected.assessed_source_t_disp = selected.source_t_disp;
            selected.assessed_source_t_bos = selected.source_t_bos;
            selected.assessed_target_source = selected.target_source;
            selected.assessed_target_model = selected.target_model;
            selected.assessed_obstacle_kind = selected.obstacle_kind;
            selected.assessed_obstacle_tf = selected.obstacle_tf;
            selected.assessed_obstacle_price = selected.obstacle_price;
            selected.assessed_decision_input_hash = m_ai.DecisionHash();
            selected.assessed_strategy_schema_version = ENGINE_INPUT_SCHEMA;
            // Freeze the AssessedTradePlan *after* the approved target has been
            // applied.  Baselining before arbitration is what made the assessed
            // fingerprint describe a trade nobody approved.
            _LockAssessedPlan(selected, dec);
            _PopulateCohortMetadata(selected);
            _WriteShadowDecisionUpdate(selected, dec, "python_approved_target_applied", "", false);
            _Journal(selected.symbol + " deterministic gate approved candidate=" + IntegerToString(selected.candidate_index)
                     + " setup_score=" + DoubleToString(selected.setup_score, 2)
                     + " source=" + selected.ai_decision_source
                     + " target_source=" + selected.target_source
                     + " tp2=" + _FmtPrice(selected.symbol, selected.tp2)
                     + " target_reason=" + selected.target_decision_reason);
            _AddToWatchlist(selected);
         } else {
            _RememberAiCooldown(group_symbol, group_signature, reject_reason);
            string reject_stage = ((reject_reason == "candidate_hash_mismatch" ||
                                    reject_reason == "ai_quality_schema_incomplete" ||
                                    reject_reason == "degraded_ai_response_non_trading") ? "decision_integrity" : "ai");
            _LogSetupReject(group_symbol, reject_stage, reject_reason,
                            "llm_quality_score=" + DoubleToString(dec.llm_quality_score, 2)
                            + " required_llm_quality_score=" + DoubleToString(required_llm_quality_score, 2)
                            + " source=" + ai_threshold_source
                            + " family=" + (have_selected ? selected.setup_family : "")
                            + " class=" + (have_selected ? selected.setup_class : "")
                            + " branch=" + (have_selected ? selected.entry_branch : "")
                            + " llm_self_reported_confidence_diagnostic=" + DoubleToString(dec.llm_self_reported_confidence, 2)
                            + " hard_veto=" + IntegerToString(hard_veto ? 1 : 0)
                            + " reason_detail=" + ai_veto_reason
                            + " assessment_integrity_reason=" + assessment_integrity_reason
                            + " ai_selected_candidate_hash=" + dec.selected_candidate_hash);
            _Journal(group_symbol + " AI/rule reject req_id=" + req_ids[r] + " reasons=" + dec.reasons_json
                     + " deterministic_reason=" + deterministic_reason + " reject=" + reject_reason);
         }

         _RemovePendingGroup(req_ids[r]);
      }
   }

   void MaintainWatchlist() {
      _PrunePlanArray(m_watchlist, false);
      for(int i=ArraySize(m_watchlist)-1; i>=0; i--){
         TradePlan p = m_watchlist[i];
         _InitializeNarrativeFields(p);
         if(InpOnlyBreakerRetestVirginStrongOrigin && !_ApplyExclusiveModelFilter(p, "exclusive_model_filter", false)){
            p.narrative_state = "invalidated";
            p.invalidation_cause = "exclusive_model_integrity_failed";
            _WriteTradeMeta(p);
            _Journal(p.symbol + " watchlist dropped: exclusive model mode rejected existing staged plan");
            int last = ArraySize(m_watchlist)-1;
            m_watchlist[i] = m_watchlist[last];
            ArrayResize(m_watchlist, last);
            continue;
         }
         if(p.arm_max_bars <= 0) p.arm_max_bars = _EffectiveWatchlistMaxBars();

         int max_watch_minutes = _EffectiveWatchlistMaxMinutes(p.htf);
         if(_PlanTooOld(p, max_watch_minutes)){
            p.narrative_state = "expired";
            p.invalidation_cause = "max_age_minutes";
            PO3SetState(p.po3, PO3_EXPIRED, "max_age_minutes");
            _WriteTradeMeta(p);
            _LogSetupReject(p.symbol, "watchlist", "max_age_minutes",
                            "age_limit_minutes=" + IntegerToString(max_watch_minutes)
                            + " bars_waited=" + IntegerToString(p.bars_waited));
            _Journal(p.symbol + " watchlist expired by age minutes=" + IntegerToString(max_watch_minutes));
            int last = ArraySize(m_watchlist)-1;
            m_watchlist[i]=m_watchlist[last];
            ArrayResize(m_watchlist, last);
            continue;
         }

         datetime last_bar_time = _LastClosedBarTime(p.symbol, p.confirm_tf);
         bool needs_refresh = false;
         bool new_confirm_bar = false;
         if(last_bar_time > 0 && last_bar_time != p.last_confirm_bar_time){
            p.last_confirm_bar_time = last_bar_time;
            p.bars_waited++;
            needs_refresh = true;
            new_confirm_bar = true;
         }
         if(!needs_refresh && (p.last_score_refresh <= 0 || (TimeLocal() - p.last_score_refresh) >= 60)){
            needs_refresh = true;
         }
         if(needs_refresh && !_RefreshWatchlistPlan(p)){
            _Journal(p.symbol + " watchlist dropped: refresh/re-score failed");
            int last = ArraySize(m_watchlist)-1;
            m_watchlist[i]=m_watchlist[last];
            ArrayResize(m_watchlist, last);
            continue;
         }

         PO3Context live_ote_po3;
         if(m_po3.Build(p.symbol, p.htf, live_ote_po3)){
            TradePlan ote_live = p;
            ote_live.po3 = live_ote_po3;
             string ote_reason = "";
             if(!_OteSoftGate(ote_live, ote_reason)){
                if(_TrySameStoryNewEntry(p, ote_reason)){
                   m_watchlist[i] = p;
                   continue;
                }
                p.narrative_state = "invalidated";
                p.invalidation_cause = ote_reason;
                PO3SetState(p.po3, PO3_INVALIDATED, ote_reason);
                _WriteTradeMeta(p);
                _LogSetupReject(p.symbol, "watchlist", ote_reason,
                                "ote_distance_frac=" + DoubleToString(ote_live.ote_distance_frac, 4)
                                + " ote_softness_frac=" + DoubleToString(p.ote_softness_frac, 4));
                _Journal(p.symbol + " watchlist invalidated: " + ote_reason);
               int last = ArraySize(m_watchlist)-1;
               m_watchlist[i]=m_watchlist[last];
               ArrayResize(m_watchlist, last);
               continue;
            }
            p.ote_distance_frac = ote_live.ote_distance_frac;
            p.ote_state = ote_live.ote_state;
         }

         if(p.bars_waited > p.arm_max_bars){
            p.narrative_state = "expired";
            p.invalidation_cause = "confirm_timeout";
            PO3SetState(p.po3, PO3_EXPIRED, "confirm_timeout");
            _WriteTradeMeta(p);
            _LogSetupReject(p.symbol, "watchlist", "confirm_timeout",
                            "bars_waited=" + IntegerToString(p.bars_waited)
                            + " arm_max_bars=" + IntegerToString(p.arm_max_bars));
            _Journal(p.symbol + " watchlist expired by confirm bars waited=" + IntegerToString(p.bars_waited));
            int last = ArraySize(m_watchlist)-1;
            m_watchlist[i]=m_watchlist[last];
            ArrayResize(m_watchlist, last);
            continue;
         }

         string invalid_reason = "";
          bool watchlist_valid = _WatchlistStillValidEx(p, invalid_reason);
          if(watchlist_valid && invalid_reason == "superseded_sweep"){
             PO3Context live_po3;
             if(m_po3.Build(p.symbol, p.htf, live_po3)){
                _UpdateNarrativeFromLive(p, live_po3);
                _Journal(p.symbol + " watchlist legacy_active: frozen story preserved superseded_by="
                         + p.superseded_by
                         + " source_story=" + _PO3StoryId(p));
             }
          }
          if(!watchlist_valid){
             if(_IsEntryZoneOnlyInvalidation(invalid_reason) && _TrySameStoryNewEntry(p, invalid_reason)){
                m_watchlist[i] = p;
                continue;
             }
             if(p.bars_waited <= 0) m_funnel_watchlist_instant_invalidations_bars0++;
             p.narrative_state = "invalidated";
             p.invalidation_cause = invalid_reason;
            if(_IsEntryZoneOnlyInvalidation(invalid_reason))
               p.po3.po3_state_reason = invalid_reason;
            else
               PO3SetState(p.po3, PO3_INVALIDATED, invalid_reason);
             _WriteTradeMeta(p);
             _LogSetupReject(p.symbol, "watchlist", invalid_reason,
                             "bars_waited=" + IntegerToString(p.bars_waited)
                             + " entry=" + _FmtPrice(p.symbol, p.entry_est)
                             + " fvg_low=" + _FmtPrice(p.symbol, p.fvg.lower)
                             + " fvg_high=" + _FmtPrice(p.symbol, p.fvg.upper));
             _Journal(p.symbol + " watchlist invalidated: " + invalid_reason);
            int last = ArraySize(m_watchlist)-1;
            m_watchlist[i]=m_watchlist[last];
            ArrayResize(m_watchlist, last);
            continue;
         }

         string sweep_reason = "";
         if(_SweepTradeCapReached(p, false, sweep_reason)){
            p.narrative_state = "superseded";
            p.invalidation_cause = sweep_reason;
            _WriteTradeMeta(p);
            _Journal(p.symbol + " watchlist dropped: " + sweep_reason);
            int last = ArraySize(m_watchlist)-1;
            m_watchlist[i]=m_watchlist[last];
            ArrayResize(m_watchlist, last);
            continue;
         }

         if(_MidMitigationOk(p.symbol, p)) p.mid_touched = true;
         if(_B50Ok(p.symbol, p)) p.b50_touched = true;
         double entry_zone_px = 0.0, entry_zone_tol = 0.0;
         bool entry_zone_ready = _EntryZoneTouched(p, entry_zone_px, entry_zone_tol);

         if(!p.armed){
            bool zone_ready = (!InpRequireFvgMidMitigation || p.mid_touched || entry_zone_ready);
            bool b50_ready = (!InpWaitB50OnM1 || p.b50_touched);
            if(zone_ready && b50_ready){
               p.armed = true;
               p.armed_at = (last_bar_time > 0 ? last_bar_time : TimeLocal());
               p.narrative_state = "armed";
               _Journal(p.symbol + " watchlist armed bars_waited=" + IntegerToString(p.bars_waited)
                        + " entry=" + _FmtPrice(p.symbol, p.entry_est));
            } else if(new_confirm_bar){
               _LogWatchlistWaiting(p, zone_ready, b50_ready);
            }
         }

         bool candle_ok = _CandleConfirmOk(p.symbol, p);
         bool ok = (p.armed && (candle_ok || entry_zone_ready));
         if(p.armed && entry_zone_ready && !candle_ok){
            _Journal(p.symbol + " entry-zone trigger planned=" + _FmtPrice(p.symbol, p.entry_est)
                     + " live=" + _FmtPrice(p.symbol, entry_zone_px)
                     + " tolerance=" + _FmtPrice(p.symbol, entry_zone_tol));
         }
         if(p.armed && !ok && new_confirm_bar && InpWaitCandleConfirm){
            _LogWatchlistConfirmWaiting(p);
         }

         PO3State watch_state = (p.po3.state != PO3_IDLE ? p.po3.state : PO3StateFromString(p.po3.po3_state));
         // Which sequence this setup is DEFINED by.  _DeterministicExecutionGate has
         // guarded its sweep/displacement/BOS checks with this predicate since §4o;
         // this gate never consulted it, so it demanded a closed BOS from families
         // that have none by definition.  #Germany40 (micro_continuation_fvg, story
         // "...|none_continuation|...") was approved, armed, and then touched its
         // entry zone 3,340 times with ZERO execution attempts: tier_b_execution_allowed
         // requires has_sweep, a micro continuation has no sweep, so the waiver could
         // never apply and the gate waited forever for an event the family excludes.
         // Any micro continuation setup was structurally unexecutable.
         bool watch_requires_full_po3 = _FamilyRequiresFullPO3Sequence(p);
         bool needs_live_sequence = (!p.po3.has_bos || p.po3.developing_bos || p.po3.sweep_running ||
                                     watch_state == PO3_DEVELOPING || watch_state == PO3_SWEEP_CONFIRMED ||
                                     watch_state == PO3_DISPLACEMENT_CONFIRMED);
         bool tier_b_execution_allowed = (!InpRequireConfirmedPO3ForExecution &&
                                          p.po3.context_tier == "B" &&
                                          p.po3.has_sweep &&
                                          p.po3.has_displacement &&
                                          !p.po3.sweep_running);
         // A family outside the full-PO3 contract is held on the evidence its own
         // contract names -- a confirmed displacement -- not on a BOS it never claims.
         // InpRequireConfirmedPO3ForExecution still overrides everything, and
         // _DeterministicExecutionGate inside _PlaceMarket still applies every
         // family-appropriate check, so nothing is waived, only re-aimed.
         bool family_sequence_hold = (watch_requires_full_po3
                                      ? (needs_live_sequence && !tier_b_execution_allowed)
                                      : (!p.po3.has_displacement || p.po3.sweep_running));
         if(ok && !watch_requires_full_po3 && !family_sequence_hold &&
            needs_live_sequence && watch_state != PO3_CONFIRMED && new_confirm_bar)
            _Journal("[execution_sequence_gate] symbol=" + p.symbol
                     + " family=" + (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p))
                     + " requires_full_po3=false"
                     + " has_sweep=" + (p.po3.has_sweep ? "true" : "false")
                     + " has_displacement=" + (p.po3.has_displacement ? "true" : "false")
                     + " has_bos=" + (p.po3.has_bos ? "true" : "false")
                     + " po3_state=" + p.po3.po3_state
                     + " action=allow_family_contract_satisfied");
         if(ok && (InpRequireConfirmedPO3ForExecution || family_sequence_hold) && watch_state != PO3_CONFIRMED){
            PO3Context live_confirm_po3;
            bool confirmed_now = false;
            if(m_po3.Build(p.symbol, p.htf, live_confirm_po3)){
               PO3State live_state = (live_confirm_po3.state != PO3_IDLE ? live_confirm_po3.state : PO3StateFromString(live_confirm_po3.po3_state));
               bool live_state_ok = (live_state == PO3_STRUCTURE_CONFIRMED || live_state == PO3_FVG_CONFIRMED ||
                                     live_state == PO3_ENTRY_WAITING || live_state == PO3_CONFIRMED);
               bool fvg_after_disp_ok = (!InpRequireFvgAfterDisp || p.fvg.t_form > live_confirm_po3.t_disp);
               if(watch_requires_full_po3 || InpRequireConfirmedPO3ForExecution){
                  confirmed_now = (live_state_ok &&
                                   live_confirm_po3.t_sweep == p.po3.t_sweep &&
                                   live_confirm_po3.has_displacement &&
                                   live_confirm_po3.t_disp > live_confirm_po3.t_sweep &&
                                   live_confirm_po3.has_bos &&
                                   live_confirm_po3.t_bos > live_confirm_po3.t_disp &&
                                   fvg_after_disp_ok);
               } else {
                  // The promotion criterion has to be reachable by the family being
                  // promoted.  Demanding a closed sweep->displacement->BOS chain from a
                  // micro continuation -- which is defined without a sweep and without a
                  // BOS -- made this branch unsatisfiable, so a held plan could never be
                  // released even when its own contract was fully satisfied.
                  confirmed_now = (live_state_ok &&
                                   live_confirm_po3.has_displacement &&
                                   !live_confirm_po3.sweep_running &&
                                   fvg_after_disp_ok);
               }
               if(confirmed_now){
                  p.po3 = live_confirm_po3;
                  PO3SetState(p.po3, PO3_CONFIRMED, "entry_retrace_confirmed");
                  p.narrative_state = "armed_confirmed";
                  _Journal(p.symbol + " watchlist PO3 promoted to CONFIRMED before execution");
               }
            }
            if(!confirmed_now){
               ok = false;
               if(new_confirm_bar){
                  _Journal(p.symbol + " execution held: PO3 state=" + p.po3.po3_state + " awaiting closed BOS/displacement sequence");
                  _Journal("[execution_sequence_gate] symbol=" + p.symbol
                           + " family=" + (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p))
                           + " requires_full_po3=" + (watch_requires_full_po3 ? "true" : "false")
                           + " require_confirmed_input=" + (InpRequireConfirmedPO3ForExecution ? "true" : "false")
                           + " has_sweep=" + (p.po3.has_sweep ? "true" : "false")
                           + " has_displacement=" + (p.po3.has_displacement ? "true" : "false")
                           + " has_bos=" + (p.po3.has_bos ? "true" : "false")
                           + " sweep_running=" + (p.po3.sweep_running ? "true" : "false")
                           + " tier=" + p.po3.context_tier
                           + " tier_b_waiver=" + (tier_b_execution_allowed ? "true" : "false")
                           + " po3_state=" + p.po3.po3_state
                           + " action=hold_awaiting_family_sequence");
               }
            }
         }

         if(ok){
            string suppress_reason = "";
            if(_SuppressExecutionRetry(p, suppress_reason)){
               // Nothing that could change the outcome has changed.  Skipping
               // is what keeps one impossible plan from producing 91 identical
               // rejection blocks.
               m_watchlist[i] = p;
               continue;
            }
            if(p.po3.state != PO3_CONFIRMED && p.po3.has_sweep && p.po3.has_displacement && p.po3.has_bos && !p.po3.sweep_running)
               PO3SetState(p.po3, PO3_CONFIRMED, "entry_retrace_confirmed");
            _Journal(p.symbol + " confirmation complete, attempting execution");
            p.narrative_state = "executing";
            if(_PlaceMarket(p)){
               int last = ArraySize(m_watchlist)-1;
               m_watchlist[i]=m_watchlist[last];
               ArrayResize(m_watchlist, last);
               continue;
            }
            if(m_last_order_construction_attempted) p.execution_order_construction_attempts++;
            _RecordExecutionFailure(p, m_last_execution_reject_reason);
            if(ExecFailureIsTerminal(p.execution_failure_class)){
               // A semantic change means the approved trade no longer exists.
               // Retrying it is meaningless; the setup must be reassessed.
               //
               // "Reassessed" is what the action name promised and what the code
               // never did: PO3_INVALIDATED buried the whole sequence, so the
               // setup could not come back even though only the obstacle/target
               // picture had moved.  A live NZDCHF approval died here five
               // milliseconds after arming, on obstacle_kind/obstacle_tf alone,
               // with unauthorized_fields_changed=none.
               //
               // Reassessment is only offered when the change was confined to
               // fields the contract already authorises movement in.  Anything
               // unauthorized is corruption, not market movement, and keeps the
               // original hard invalidation.  The approved plan still leaves the
               // watchlist either way, so no stale approval can ever execute;
               // the only difference is whether the next scan may rebuild the
               // setup and ask the AI again against current evidence.
               bool reassess = (InpRequeueAiOnSemanticPlanChange &&
                                p.execution_failure_class == EXEC_FAIL_SEMANTIC_PLAN_CHANGED &&
                                StringLen(p.semantic_unauthorized_fields_changed) == 0);
               p.narrative_state = (reassess ? "awaiting_reassessment" : "invalidated");
               p.invalidation_cause = p.execution_failure_class;
               if(!reassess)
                  PO3SetState(p.po3, PO3_INVALIDATED, p.execution_failure_class);
               _WriteTradeMeta(p);
               _LogSetupReject(p.symbol, "watchlist_execution_rebuild", p.execution_failure_class,
                               "watchlist_action=" + (reassess ? "requeue_ai_next_scan"
                                                              : ExecFailureAction(p.execution_failure_class))
                               + " duplicate_execution_attempts=" + IntegerToString(p.execution_attempts_suppressed)
                               + " raw_reason=" + m_last_execution_reject_reason);
               _Journal("[execution_rebuild] failure_class=" + p.execution_failure_class
                        + " action=" + (reassess ? "requeue_ai_next_scan"
                                                 : ExecFailureAction(p.execution_failure_class))
                        + " po3_preserved=" + (reassess ? "true" : "false")
                        + " immutable_changed=" + p.semantic_immutable_fields_changed
                        + " unauthorized_changed=" + (StringLen(p.semantic_unauthorized_fields_changed) > 0
                                                      ? p.semantic_unauthorized_fields_changed : "none")
                        + " semantic_plan_match=" + (p.semantic_plan_match ? "true" : "false")
                        + " duplicate_execution_attempts=" + IntegerToString(p.execution_attempts_suppressed)
                        + " reason=" + m_last_execution_reject_reason);
               int last_terminal = ArraySize(m_watchlist)-1;
               m_watchlist[i]=m_watchlist[last_terminal];
               ArrayResize(m_watchlist, last_terminal);
               continue;
            }
            string structural_reason = _StructuralPlanRebuildReason(m_last_execution_reject_reason);
            if(StringLen(structural_reason) > 0){
               p.narrative_state = "invalidated";
               p.invalidation_cause = structural_reason;
               PO3SetState(p.po3, PO3_INVALIDATED, structural_reason);
               _WriteTradeMeta(p);
               _LogSetupReject(p.symbol, "watchlist_execution_rebuild", structural_reason,
                               "watchlist_action=invalidated_terminal"
                               + " previous_retries=" + IntegerToString(p.bars_waited)
                               + " raw_reason=" + m_last_execution_reject_reason);
               _Journal("[execution_rebuild] structural_failure=true reason=" + structural_reason
                        + " action=terminal_watchlist_invalidation");
               _Journal("[watchlist] invalidated terminal=true reason=" + structural_reason
                        + " previous_retries=" + IntegerToString(p.bars_waited));
               int last = ArraySize(m_watchlist)-1;
               m_watchlist[i]=m_watchlist[last];
               ArrayResize(m_watchlist, last);
               continue;
            }
         }

         m_watchlist[i] = p;
      }
   }

   void MaintainOrders() {
      for(int i=OrdersTotal()-1; i>=0; i--){
         ulong ticket = OrderGetTicket(i);
         if(!OrderMatchesMagic(ticket)) continue;

         ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         bool is_buy_pending = (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY_STOP_LIMIT);
         bool is_sell_pending = (type == ORDER_TYPE_SELL_LIMIT || type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL_STOP_LIMIT);
         if(!is_buy_pending && !is_sell_pending) continue;

         string sym = OrderGetString(ORDER_SYMBOL);
         string comment = OrderGetString(ORDER_COMMENT);
         TradePlan meta;
         if(!_LoadTradeMeta(ticket, sym, comment, meta)){
            _Journal(sym + " pending order missing trade metadata ticket=" + IntegerToString((int)ticket));
            if(InpOnlyBreakerRetestVirginStrongOrigin){
               _LogSetupReject(sym, "pre_execution_integrity", "exclusive_model_integrity_failed",
                               "exclusive_fail_reason=missing_trade_metadata ticket=" + IntegerToString((int)ticket));
               if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete("exclusive_model_missing_metadata");
            }
            continue;
         }
         if(InpOnlyBreakerRetestVirginStrongOrigin && !_ExclusiveModelPreExecutionOk(meta)){
            _Journal(sym + " deleting pending order ticket=" + IntegerToString((int)ticket)
                     + " reason=exclusive_model_integrity_failed");
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete("exclusive_model_integrity_failed");
            continue;
         }

         datetime expiry = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
         datetime now = TimeTradeServer();
         if(now <= 0) now = TimeLocal();

         string rollover_reason = "";
         if(PO3EntryBlockedByRollover(now, rollover_reason)){
            _Journal(sym + " deleting pending order ticket=" + IntegerToString((int)ticket)
                     + " reason=" + rollover_reason);
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete("rollover_entry_freeze");
            continue;
         }

         string broker_reason = "";
         if(!_PendingBrokerStateOk(broker_reason)){
            _Journal(sym + " deleting pending order ticket=" + IntegerToString((int)ticket) + " reason=" + broker_reason);
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete(broker_reason);
            continue;
         }

         if(expiry > 0 && now >= expiry){
            string miss_classification = _PendingExpiryMissClassification(meta);
            meta.entry_miss_classification = miss_classification;
            _WriteTradeMeta(meta, ticket);
            _Journal(sym + " deleting expired pending order ticket=" + IntegerToString((int)ticket)
                     + " entry_miss_classification=" + miss_classification);
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete("expiry_reached_" + miss_classification, true);
            continue;
         }

         _UpdatePendingWaitBars(meta);

         string invalid_reason = "";
         if(!_PendingOrderStillValidEx(meta, invalid_reason)){
            _Journal(sym + " deleting pending order ticket=" + IntegerToString((int)ticket) + " reason=" + invalid_reason);
            if(m_trade.OrderDelete(ticket)) _TrackPendingOrderDelete(invalid_reason);
            continue;
         }

         if(_TryPendingEntryZoneMarket(meta, ticket))
            continue;

         _TryRelaxPendingEntry(meta, ticket, expiry);
         _WriteTradeMeta(meta, ticket);
      }
   }

   void ObserveChartTick(const string chart_symbol) {
      m_penalty.ObserveChartTick(chart_symbol);
   }

   // Timed at the entry point.  This is the whole per-simulated-second cost of
   // holding a position, and it is the stage that collapsed replay throughput from
   // 359x to 0.333x real time the moment the first position opened.  The clock is
   // permanent so the next regression here is reported as a number rather than
   // inferred from gaps between journal lines.
   void MaintainPositions() {
      if(m_last_positions_tick == TimeLocal()) return;
      m_last_positions_tick = TimeLocal();
      ulong t0 = GetMicrosecondCount();
      _MaintainPositionsBody();
      m_mp_us += (long)(GetMicrosecondCount() - t0);
      m_mp_calls++;
   }

   void _MaintainPositionsBody() {
      // TP1 partial + BE; penalty watcher tick.
      // TP1 partial logic (basic; extend with per-position state to avoid repeat)
      for(int i=PositionsTotal()-1; i>=0; i--){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;

         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         bool is_buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double entry = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double vol = PositionGetDouble(POSITION_VOLUME);
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

         TradePlan meta;
         if(!_SymbolEligible(sym)) continue;
         string position_identity_reason = "";
         // Segment clocks.  The serialize/write/parse clocks account for part of this
         // stage; without the segments the rest is a single unattributed number and the
         // next optimisation is a guess.  A recovery path inside the loader can itself
         // write a meta, so a rare load is charged for that write too -- the common
         // path, where the position file resolves first, cannot reach it.
         ulong load0 = GetMicrosecondCount();
         bool meta_resolved = _LoadAndResolvePositionMeta(ticket, sym, comment, meta, position_identity_reason);
         m_mp_load_us += (long)(GetMicrosecondCount() - load0);
         if(!meta_resolved){
            TradePlan quarantined;
            ZeroMemory(quarantined);
            long live_position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
            _NormalizeRecoveredExposureMeta(quarantined, sym, is_buy, entry, sl, tp, vol, opened, comment,
                                            live_position_id, false);
            quarantined.broker_position_ticket = ticket;
            quarantined.broker_position_identifier = live_position_id;
            _QuarantineExecutionIdentity(quarantined,
                                         "live_position_attribution_unavailable:" + position_identity_reason,
                                         0,
                                         0);
            continue;
         }
         if(meta.execution_identity_quarantined || !meta.execution_identity_verified) continue;

          bool pending_fill_detected = (meta.narrative_state == "pending_order" || meta.narrative_state == "pending_order_recovered");
          double px = SymbolInfoDouble(sym, is_buy ? SYMBOL_BID : SYMBOL_ASK);
          _UpdateAnalyticsSnapshot(meta, ticket, px, vol);
          if(pending_fill_detected){
             if(meta.filled_entry <= 0) meta.filled_entry = entry;
             if(meta.filled_at <= 0) meta.filled_at = opened;
             meta.narrative_state = "executed";
             m_funnel_orders_filled++;
             m_funnel_trades_opened++;
             m_total_trades_opened++;
             _RememberConsumedSweep(meta);
             _Journal(sym + " pending order filled ticket=" + IntegerToString((int)ticket)
                      + " entry=" + _FmtPrice(sym, meta.filled_entry)
                      + " sl=" + _FmtPrice(sym, sl)
                      + " tp=" + _FmtPrice(sym, tp));
          }

          double risk_dist = _RiskDistanceForMeta(meta);
          double tp1_price = meta.tp1;
          if(tp1_price <= 0 && risk_dist > 0){
             double tp1_r = MathMax(0.40, meta.tp1_r_multiple);
             tp1_price = (is_buy ? entry + risk_dist * tp1_r : entry - risk_dist * tp1_r);
          }

          // TP1 reach check
          bool tp1_hit = (tp1_price > 0 ? (is_buy ? (px >= tp1_price) : (px <= tp1_price)) : false);
          if(tp1_hit && !meta.tp1_done){
             bool partial_ok = true;
             double partial_pct = (meta.tp1_partial_pct > 0 ? meta.tp1_partial_pct : InpTP1PartialPct);
             if(partial_pct > 0 && partial_pct < 0.99){
                VolumeNormalizationResult close_norm = NormalizeClosingVolume(sym,
                                                                               vol * partial_pct,
                                                                               vol,
                                                                               InpClosingVolumeAllowCloseAllBelowMinimum);
                if(close_norm.action == "close_all")
                   partial_ok = m_trade.PositionClose(ticket);
                else if(close_norm.normalized_volume > 0.0)
                   partial_ok = m_trade.PositionClosePartial(ticket, close_norm.normalized_volume);
                else {
                   partial_ok = false;
                   _Journal("[closing_volume] stage=tp1 ticket=" + IntegerToString((long)ticket)
                            + " requested=" + DoubleToString(close_norm.requested_volume, 8)
                            + " normalized=" + DoubleToString(close_norm.normalized_volume, 8)
                            + " residual=" + DoubleToString(close_norm.residual_volume, 8)
                            + " action=" + close_norm.action
                            + " reason=" + close_norm.reason);
                }
             }
             if(partial_ok){
                _CaptureManagementDecisionSnapshot(meta, "tp1_partial_or_close", px, sl, tp);
                meta.tp1_done = true;
                _Journal(sym + " TP1 handled ticket=" + IntegerToString((int)ticket)
                         + " partial=" + (partial_ok ? "true" : "false"));
             } else {
                _Journal(sym + " TP1 action failed ticket=" + IntegerToString((int)ticket)
                         + " partial=" + (partial_ok ? "true" : "false")
                         + " retcode=" + _TradeRetcodeText());
             }
          }

          if(InpBEEnable && meta.be_rule != "off"){
             datetime now = TimeTradeServer();
             if(now <= 0) now = TimeLocal();
             int minutes_open = (opened > 0 ? (int)((now - opened) / 60) : 0);
             double be_trigger_r = MathMax(InpBETriggerR, (meta.be_trigger_r > 0 ? meta.be_trigger_r : InpBETriggerR));
             if(minutes_open >= InpMinMinutesBeforeBE && meta.mfe_r >= be_trigger_r){
                double point = SymbolInfoDouble(sym, SYMBOL_POINT);
                if(point <= 0) point = 0.00001;
                double new_sl = entry;
                if(meta.be_rule == "structure" && meta.fvg.mid > 0){
                   new_sl = (is_buy ? MathMax(entry, meta.fvg.mid) : MathMin(entry, meta.fvg.mid));
                } else if(meta.be_rule == "bos_level" && meta.po3.bos_level > 0){
                   new_sl = (is_buy ? MathMax(entry, meta.po3.bos_level) : MathMin(entry, meta.po3.bos_level));
                } else if(meta.be_rule == "session_level"){
                   double session_ref = (is_buy ? meta.po3.session_low : meta.po3.session_high);
                   if(session_ref > 0)
                      new_sl = (is_buy ? MathMax(entry, session_ref) : MathMin(entry, session_ref));
                }
                double offset = meta.be_offset_points * point;
                new_sl += (is_buy ? offset : -offset);
                if((is_buy && new_sl > sl) || (!is_buy && new_sl < sl)){
                   bool be_ok = m_trade.PositionModify(ticket, new_sl, tp);
                   if(be_ok)
                      _CaptureManagementDecisionSnapshot(meta, "breakeven_stop_modify", px, sl, tp);
                   _Journal(sym + " breakeven time-gated ticket=" + IntegerToString((int)ticket)
                            + " minutes_open=" + IntegerToString(minutes_open)
                            + " mfe_r=" + DoubleToString(meta.mfe_r, 2)
                            + " trigger_r=" + DoubleToString(be_trigger_r, 2)
                            + " ok=" + (be_ok ? "true" : "false"));
                }
             }
          }
          ulong meta0 = GetMicrosecondCount();
          _WriteTradeMeta(meta, ticket);
          m_mp_meta_us += (long)(GetMicrosecondCount() - meta0);
       }

      // Penalties
      ulong pen0 = GetMicrosecondCount();
      m_penalty.Tick(m_trade);
      m_mp_penalty_us += (long)(GetMicrosecondCount() - pen0);
      for(int state_i=PositionsTotal()-1; state_i>=0; state_i--){
         ulong state_ticket = PositionGetTicket(state_i);
         if(!PositionMatchesMagic(state_ticket)) continue;
         string state_symbol = PositionGetString(POSITION_SYMBOL);
         long state_position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
         PenaltyState penalty_state;
         if(!m_penalty.GetState(state_position_id, state_symbol, penalty_state)) continue;
         TradePlan state_meta;
         string state_meta_reason = "";
         ulong state_load0 = GetMicrosecondCount();
         bool state_resolved = _LoadAndResolvePositionMeta(state_ticket,
                                                          state_symbol,
                                                          PositionGetString(POSITION_COMMENT),
                                                          state_meta,
                                                          state_meta_reason);
         m_mp_load_us += (long)(GetMicrosecondCount() - state_load0);
         if(!state_resolved) continue;
         _ApplyPenaltyStateToMeta(state_meta, penalty_state);
         if(StringLen(penalty_state.requested_action) > 0 &&
            penalty_state.requested_action != "NO_BROKER_ACTION"){
            double state_px = SymbolInfoDouble(state_symbol,
                                               PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? SYMBOL_BID : SYMBOL_ASK);
            _CaptureManagementDecisionSnapshot(state_meta,
                                               penalty_state.requested_action,
                                               state_px,
                                               (state_meta.planned_sl > 0.0 ? state_meta.planned_sl : state_meta.sl),
                                               (state_meta.planned_tp2 > 0.0 ? state_meta.planned_tp2 : state_meta.tp2));
         }
         ulong state_meta0 = GetMicrosecondCount();
         _WriteTradeMeta(state_meta, state_ticket);
         m_mp_meta_us += (long)(GetMicrosecondCount() - state_meta0);
      }
      ulong tail0 = GetMicrosecondCount();
      if(m_last_penalty_persist == 0 || (TimeLocal() - m_last_penalty_persist) >= 15){
         _PersistPenaltyStates();
      }
      _FinalizeClosedTrades();
      _MaintainCounterfactualEvaluations();
      _MaintainShadowCandidateOutcomes();
      m_mp_tail_us += (long)(GetMicrosecondCount() - tail0);
   }

   void Persist() {
      _PrunePlanArray(m_watchlist, false);
      _PrunePlanArray(m_pending_ai, true);
      m_state.SavePlans(m_state.WatchlistPath(), m_watchlist);
      m_state.SavePlans(m_state.PendingAiPath(), m_pending_ai);
      _PersistResearchQueues();
      _PersistPenaltyStates();
   }

   bool HasPendingAI() const {
      return (ArraySize(m_pending_ai) > 0);
   }

   int PendingAICount() const {
      return ArraySize(m_pending_ai);
   }

   int PendingAIRequestCount() const {
      return _PendingAIRequestCountInternal();
   }

   string PendingAIRequestIds() const {
      string req_ids[];
      ArrayResize(req_ids, 0);
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         string req_id = m_pending_ai[i].req_id;
         if(StringLen(req_id) == 0 || _HasStringValue(req_ids, req_id)) continue;
         int n = ArraySize(req_ids);
         ArrayResize(req_ids, n + 1);
         req_ids[n] = req_id;
      }
      if(ArraySize(req_ids) <= 0) return "none";
      string out = "";
      for(int i=0; i<ArraySize(req_ids); i++){
         if(i > 0) out += ",";
         out += req_ids[i];
      }
      return out;
   }

   bool PendingAISnapshotSaved() const {
      if(ArraySize(m_pending_ai) <= 0) return false;
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(m_pending_ai[i].setup_snapshot_time <= 0) return false;
      }
      return true;
   }

   ulong PendingAIOldestWallStartMs() const {
      ulong oldest = 0;
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         ulong started = m_pending_ai[i].ai_requested_wall_ms;
         if(started == 0) continue;
         if(oldest == 0 || started < oldest) oldest = started;
      }
      return oldest;
   }

   bool PendingAIResponsePresent() {
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(StringLen(m_pending_ai[i].req_id) == 0) continue;
         if(_PathExists(_RespPath(m_pending_ai[i].req_id))) return true;
      }
      return false;
   }

   void NoteTesterAiWaitStarted() {
      m_total_tester_ai_wait_started++;
   }

   void NoteTesterAiWaitCompleted() {
      m_total_tester_ai_wait_completed++;
   }

   void NoteTesterAiWaitTimeout() {
      m_total_tester_ai_wait_timeout++;
   }

   int TesterAiWaitTimeoutTotal() const {
      return m_total_tester_ai_wait_timeout;
   }

   bool HasActiveSymbolState(const string symbol) {
      return _SymbolBusy(symbol);
   }
};

#endif
