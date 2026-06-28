//+------------------------------------------------------------------+
//| TradeEngine.mqh - build plans, manage watchlist, execute trades    |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_TRADEENGINE_MQH__
#define __PO3_AIGATE_TRADEENGINE_MQH__
#include <Trade/Trade.mqh>
#include "Config.mqh"
#include "Types.mqh"
#include "Indicators.mqh"
#include "PO3.mqh"
#include "FVG.mqh"
#include "Risk.mqh"
#include "AIGateBridge.mqh"
#include "StateStore.mqh"
#include "PenaltyWatcher.mqh"
#include "JsonLite.mqh"

class CTradeEngine {
private:
   CTrade m_trade;
   CFileBus m_bus;
   CAIGateBridge m_ai;
   CStateStore m_state;

   CPO3 m_po3;
   CFVG m_fvg;
   CPenaltyWatcher m_penalty;

   TradePlan m_watchlist[];
   TradePlan m_pending_ai[];
   TradePlan m_scan_candidates[];
   string m_ai_cooldown_symbols[];
   string m_ai_cooldown_signatures[];
   datetime m_ai_cooldown_until[];
   string m_tester_ai_cache_signatures[];
   AiDecision m_tester_ai_cache_decisions[];
   string m_tester_snapshot_cache_keys[];
   string m_tester_snapshot_cache_paths[];
   string m_consumed_sweep_keys[];
   ActivePolicySnapshot m_active_policy;
   SubtypePolicyEntry m_subtype_policy[];
   ContextPolicyEntry m_context_policy[];
   SessionWeekdayPolicyEntry m_session_weekday_policy[];
   datetime m_policy_loaded_at;
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
   string m_funnel_pending_delete_reasons[];
   int m_funnel_pending_delete_counts[];
   string m_funnel_reject_stages[];
   string m_funnel_reject_reasons[];
   int m_funnel_reject_counts[];

   datetime m_last_positions_tick;
   datetime m_last_penalty_persist;
   datetime m_last_rollover_log;
   string m_ai_wait_log_req_ids[];
   ulong m_ai_wait_log_ms[];

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
      return m_bus.LogDir() + "\\trade_ticket_" + IntegerToString((int)ticket) + ".json";
   }

   string _LegacyTradeSymbolPath(const string symbol) {
      return m_bus.LogDir() + "\\trade_" + symbol + ".json";
   }

   string _TradeResultsDir() {
      return m_bus.LogDir() + "\\trade_results";
   }

   string _TradeResultPath(const string key) {
      return _TradeResultsDir() + "\\trade_result_" + key + ".json";
   }

   datetime _AnalyticsResetAfter() {
      if(MQLInfoInteger(MQL_TESTER)) return 0;
      string txt;
      if(!_ReadText(m_bus.LogDir() + "\\analytics_reset.json", txt)) return 0;
      return (datetime)(int)JsonGetNumber(txt, "reset_after", 0.0);
   }

   datetime _NowServerOrLocal() {
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
      return (ulong)GetTickCount();
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

   bool _ParseSubtypePolicyLine(const string line, SubtypePolicyEntry &entry) const {
      entry.subtype_key = JsonGetString(line, "subtype_key", "");
      if(StringLen(entry.subtype_key) == 0) return false;
      entry.action = JsonGetString(line, "action", "");
      entry.score_penalty = JsonGetNumber(line, "score_penalty", 0.0);
      entry.risk_multiplier = JsonGetNumber(line, "risk_multiplier", 1.0);
      entry.shrunk_win_rate = JsonGetNumber(line, "shrunk_win_rate", 0.0);
      entry.avg_r = JsonGetNumber(line, "avg_r", 0.0);
      entry.evidence_score = JsonGetNumber(line, "evidence_score", 0.0);
      entry.sample_count = (int)JsonGetNumber(line, "sample_count", 0.0);
      entry.policy_id = JsonGetString(line, "policy_id", "");
      if(entry.risk_multiplier <= 0.0) entry.risk_multiplier = 1.0;
      return true;
   }

   bool _ParseContextPolicyLine(const string line, ContextPolicyEntry &entry) const {
      entry.policy_bucket = JsonGetString(line, "policy_bucket", "");
      if(StringLen(entry.policy_bucket) == 0) return false;
      entry.action = JsonGetString(line, "action", "");
      entry.score_bias = JsonGetNumber(line, "score_bias", 0.0);
      entry.risk_multiplier = JsonGetNumber(line, "risk_multiplier", 1.0);
      entry.expected_value_bias = JsonGetNumber(line, "expected_value_bias", 0.0);
      entry.sample_count = (int)JsonGetNumber(line, "sample_count", 0.0);
      entry.policy_id = JsonGetString(line, "policy_id", "");
      if(entry.risk_multiplier <= 0.0) entry.risk_multiplier = 1.0;
      return true;
   }

   bool _ParseSessionWeekdayPolicyLine(const string line, SessionWeekdayPolicyEntry &entry) const {
      entry.session_name = JsonGetString(line, "session_name", "");
      entry.weekday = JsonGetString(line, "weekday", "");
      if(StringLen(entry.session_name) == 0 || StringLen(entry.weekday) == 0) return false;
      entry.action = JsonGetString(line, "action", "monitor");
      entry.risk_multiplier = JsonGetNumber(line, "risk_multiplier", 1.0);
      entry.rr_floor_delta = JsonGetNumber(line, "rr_floor_delta", 0.0);
      entry.score_bias = JsonGetNumber(line, "score_bias", 0.0);
      entry.sample_count = (int)JsonGetNumber(line, "sample_count", 0.0);
      entry.policy_id = JsonGetString(line, "policy_id", "");
      if(entry.risk_multiplier <= 0.0) entry.risk_multiplier = 1.0;
      return true;
   }

   bool _LoadActivePolicyFiles() {
      ZeroMemory(m_active_policy);
      ArrayResize(m_subtype_policy, 0);
      ArrayResize(m_context_policy, 0);
      ArrayResize(m_session_weekday_policy, 0);

      string txt;
      if(_ReadText(_ActivePolicyPath(), txt)){
         m_active_policy.policy_id = JsonGetString(txt, "policy_id", "");
         m_active_policy.version = (int)JsonGetNumber(txt, "version", 0.0);
         m_active_policy.activated_at = (datetime)(int)JsonGetNumber(txt, "activated_at", 0.0);
         m_active_policy.evidence_score = JsonGetNumber(txt, "evidence_score", 0.0);
         m_active_policy.evidence_passed = JsonGetBool(txt, "evidence_passed", false);
         m_active_policy.walk_forward_passed = JsonGetBool(txt, "walk_forward_passed", false);
         m_active_policy.change_rate_passed = JsonGetBool(txt, "change_rate_passed", false);
         m_active_policy.soft_setup_floor = JsonGetNumber(txt, "soft_setup_floor", 0.0);
         m_active_policy.hard_setup_floor = JsonGetNumber(txt, "hard_setup_floor", 0.0);
         m_active_policy.setup_floor_penalty_mult = JsonGetNumber(txt, "setup_floor_penalty_mult", 1.0);
         m_active_policy.ote_softness_frac = JsonGetNumber(txt, "ote_softness_frac", InpOteSoftnessFrac);
         m_active_policy.default_risk_multiplier = JsonGetNumber(txt, "default_risk_multiplier", 1.0);
         m_active_policy.runner_sequence_floor = JsonGetNumber(txt, "runner_sequence_floor", InpRunnerSequenceQualityFloor);
         m_active_policy.runner_liquidity_rr_floor = JsonGetNumber(txt, "runner_liquidity_rr_floor", InpRunnerLiquidityRRFloor);
         m_active_policy.runner_cost_r_ceiling = JsonGetNumber(txt, "runner_cost_r_ceiling", InpRunnerCostRCeiling);
         m_active_policy.runner_alignment_floor = JsonGetNumber(txt, "runner_alignment_floor", InpRunnerHtfAlignmentFloor);
         m_active_policy.runner_adverse_ceiling = JsonGetNumber(txt, "runner_adverse_ceiling", InpRunnerAdverseContextCeiling);
         m_active_policy.runner_ev_floor = JsonGetNumber(txt, "runner_ev_floor", 0.0);
         bool shadow_policy = JsonGetBool(txt, "shadow_mode", false);
         string activation_state = JsonGetString(txt, "activation_state", "");
         m_active_policy.valid = (StringLen(m_active_policy.policy_id) > 0 &&
                                  m_active_policy.evidence_passed &&
                                  m_active_policy.walk_forward_passed &&
                                  m_active_policy.change_rate_passed &&
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
               if(!_ParseSubtypePolicyLine(line, entry)) continue;
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
               if(!_ParseContextPolicyLine(line, entry)) continue;
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
               if(!_ParseSessionWeekdayPolicyLine(line, entry)) continue;
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

   bool _HardSuppressionGate(const TradePlan &p, string &reason) const {
      reason = "";
      string family_group = _FamilyGroup(p);
      string family = _NormToken(p.setup_family);
      string branch = _NormToken(p.entry_branch);
      bool stale = _IsStaleFvgPlan(p);
      bool touched = _IsTouchedFvgPlan(p);

      if(InpTradeOnlyKillzones && !p.po3.in_killzone){
         reason = "trade_only_killzone_block";
         return false;
      }
      if(InpSuppressMicroBisiSibiEdge && (family == "micro_bisi_sibi" || family == "micro_bisi_sibi_edge" || branch == "fvg_edge")){
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
      if(InpRejectSyntheticFallbackAfterCrossedObstacle &&
         p.target_source == "synthetic_rr_fallback" &&
         StringFind(p.obstacle_kind, "crossed") >= 0 &&
         (StringFind(p.obstacle_kind, "opposing") >= 0 || StringFind(p.obstacle_kind, "imbalance") >= 0)){
         reason = "synthetic_fallback_crossed_obstacle_blocked";
         return false;
      }
      return true;
   }

   double _TotalExecutionCostR(const TradePlan &p) const {
      return MathMax(0.0, p.execution_cost_r) + MathMax(0.0, p.slippage_r) + MathMax(0.0, p.commission_r);
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
      if(!_HardSuppressionGate(p, reason)){
         _LogSetupReject(p.symbol, stage, reason, "branch=" + p.entry_branch + " family=" + p.setup_family
                         + " session=" + p.po3.session_name + " killzone=" + (p.po3.in_killzone ? "true" : "false"));
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
      return _ObjectiveHardPreTradeGate(check, "final_order_hard_safety", reason);
   }

   bool _IsRealAccount() const {
      if(MQLInfoInteger(MQL_TESTER)) return false;
      return (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL);
   }

   bool _IsDemoLikeAccount() const {
      if(MQLInfoInteger(MQL_TESTER)) return false;
      long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
      return (mode == ACCOUNT_TRADE_MODE_DEMO || mode == ACCOUNT_TRADE_MODE_CONTEST);
   }

   bool _RuleFallbackAllowed(const string reason, string &mode_reason) const {
      if(!InpAllowRuleOnlyFallback){
         mode_reason = "rule_only_fallback_disabled_" + reason;
         return false;
      }
      if(MQLInfoInteger(MQL_TESTER)){
         mode_reason = "rule_only_fallback_tester_" + reason;
         return true;
      }
      if(_IsRealAccount()){
         if(InpLiveFailClosedOnAIFailure && !InpAllowRuleOnlyLive){
            mode_reason = "ai_failure_live_fail_closed_" + reason;
            return false;
         }
         if(!InpAllowRuleOnlyLive){
            mode_reason = "rule_only_live_disabled_" + reason;
            return false;
         }
         mode_reason = "rule_only_fallback_live_reduced_" + reason;
         return true;
      }
      if(_IsDemoLikeAccount()){
         mode_reason = "rule_only_fallback_demo_" + reason;
         return true;
      }
      mode_reason = "rule_only_fallback_unknown_" + reason;
      return !InpLiveFailClosedOnAIFailure;
   }

   bool _ApplyRuleFallback(TradePlan &p, const string reason) {
      string mode_reason = "";
      if(!_RuleFallbackAllowed(reason, mode_reason)){
         p.reject_code = mode_reason;
         _LogSetupReject(p.symbol, "ai", mode_reason, "fallback_reason=" + reason);
         _Journal(p.symbol + " rules fallback blocked reason=" + mode_reason);
         return false;
      }
      if(p.subtype_risk_multiplier <= 0.0) p.subtype_risk_multiplier = 1.0;
      if(!MQLInfoInteger(MQL_TESTER)){
         double mult = _ClampRange(InpFallbackRiskMultiplier, 0.0, 1.0);
         if(mult <= 0.0){
            p.reject_code = "fallback_risk_multiplier_zero";
            _LogSetupReject(p.symbol, "ai", "fallback_risk_multiplier_zero",
                            "fallback_risk_multiplier=" + DoubleToString(InpFallbackRiskMultiplier, 2));
            _Journal(p.symbol + " rules fallback blocked: fallback risk multiplier is zero");
            return false;
         }
         p.subtype_risk_multiplier *= mult;
      }
      p.ai.ok = false;
      p.ai.allow = true;
      p.ai.score = p.setup_score;
      p.ai.chosen_index = p.candidate_index;
      p.ai.confidence = 0.0;
      p.ai.reasons_json = reason;
      p.ai.decision_source = mode_reason;
      p.ai.rejection_codes_json = "[\"ai_" + reason + "\"]";
      p.ai.narrative_state = "rule_only_fallback";
      p.ai.suggested_risk_multiplier = (!MQLInfoInteger(MQL_TESTER) ? _ClampRange(InpFallbackRiskMultiplier, 0.0, 1.0) : 1.0);
      p.ai.model_version = "rule_only_fallback";
      p.ai_decision_source = mode_reason;
      p.reject_code = "";
      return true;
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
      ArrayResize(m_funnel_pending_delete_reasons, 0);
      ArrayResize(m_funnel_pending_delete_counts, 0);
      ArrayResize(m_funnel_reject_stages, 0);
      ArrayResize(m_funnel_reject_reasons, 0);
      ArrayResize(m_funnel_reject_counts, 0);
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

   string _DeriveSetupFamily(const TradePlan &p) const {
      string branch = p.entry_branch;
      if(StringLen(branch) == 0) branch = p.entry_model;
      bool full_scope = (p.po3.po3_scope == "institutional_po3" || InpStrategyMode == STRATEGY_FULL_PO3);
      bool scalp_context = (p.po3.structure_type == "micro_continuation" ||
                            p.po3.structure_type == "micro_failed_breakout_reclaim" ||
                            p.po3.htf_structure_type == "micro_synthetic");
      if(p.po3.structure_type == "micro_failed_breakout_reclaim") return "micro_failed_breakout_reclaim";
      if(branch == "range_reentry" || branch == "session_reentry") return "micro_range_reentry";
      if(branch == "fvg_edge" && !full_scope) return "micro_bisi_sibi_edge";
      if(branch == "breaker_retest" && !full_scope) return "micro_bisi_sibi_edge";
      if(branch == "nested_htf_ltf_fvg" || branch == "nested_fvg_edge" || branch == "continuation_reentry" || scalp_context)
         return (full_scope ? "full_po3_continuation" : "micro_continuation_fvg");
      if(p.fvg.continuation || _FvgExecutionClass(p) == "continuation_reentry")
         return (full_scope ? "full_po3_continuation" : "micro_continuation_fvg");
      if(p.fvg.reversal || p.po3.htf_mss || p.po3.htf_choch || p.po3.ltf_mss || p.po3.ltf_choch)
         return (full_scope ? "full_po3_reversal" : "micro_po3_reversal");
      return (full_scope ? "full_po3_continuation" : "micro_continuation_fvg");
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

   double _FamilyAiScoreFloor(const TradePlan &p) const {
      string group = _FamilyGroup(p);
      string family = (StringLen(p.setup_family) > 0 ? p.setup_family : _DeriveSetupFamily(p));
      if(family == "full_po3_reversal" || family == "full_po3_continuation") return InpAiScoreFullPO3;
      if(group == "failed_breakout") return InpAiScoreFailedBreakout;
      if(group == "continuation" || group == "edge") return InpAiScoreContinuation;
      if(group == "range") return InpAiScoreRange;
      return InpAiScoreMicroPO3;
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

   string _BrokerCommentForPlan(const TradePlan &p) const {
      string model = (StringLen(p.model_code) > 0 ? p.model_code : _ModelCodeForPlan(p));
      string session = (StringLen(p.session_code) > 0 ? p.session_code : _SessionCodeForPlan(p));
      string kz = (StringLen(p.killzone_code) > 0 ? p.killzone_code : (p.po3.in_killzone ? "K" : "NK"));
      string comment = model + "-" + session + "-" + kz + "-" + _CompactSymbolCode(p.symbol) + "-" + _ShortPlanId(p);
      if(StringLen(comment) > 31)
         comment = model + "-" + session + "-" + kz + "-" + StringSubstr(_CompactSymbolCode(p.symbol), 0, 4) + "-" + _ShortPlanId(p);
      return comment;
   }

   void _InitializeNarrativeFields(TradePlan &p) {
      if(StringLen(p.entry_branch) == 0) p.entry_branch = p.entry_model;
      if(StringLen(p.fvg_execution_class) == 0) p.fvg_execution_class = _FvgExecutionClass(p);
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
      p.estimated_commission_money = InpCommissionPerLotRoundTurn;
      p.estimated_cost_price = spread + p.estimated_slippage_price;
      double risk = MathAbs(p.entry_est - p.sl);
      p.execution_cost_r = (risk > 0.0 ? spread / risk : 0.0);
      p.slippage_r = (risk > 0.0 ? p.estimated_slippage_price / risk : 0.0);
      p.commission_r = 0.0;
      if(risk > 0.0 && InpCommissionPerLotRoundTurn > 0.0){
         double tick_size = SymbolInfoDouble(p.symbol, SYMBOL_TRADE_TICK_SIZE);
         double tick_value = SymbolInfoDouble(p.symbol, SYMBOL_TRADE_TICK_VALUE);
         if(tick_size > 0.0 && tick_value > 0.0){
            double risk_money_per_lot = (risk / tick_size) * tick_value;
            if(risk_money_per_lot > 0.0) p.commission_r = InpCommissionPerLotRoundTurn / risk_money_per_lot;
         }
      }
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

   double _ExpectedWinRate(const TradePlan &p) const {
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

   double _ExpectedWinRateGross(const TradePlan &p) const {
      TradePlan tmp = p;
      tmp.execution_cost_r = 0.0;
      return _ExpectedWinRate(tmp);
   }

   double _ExpectedValueR(const TradePlan &p) const {
      double win_rate = _ExpectedWinRate(p);
      double rr = MathMax(0.0, _ExecutionRR2(p));
      double base = win_rate * rr - (1.0 - win_rate);
      if(p.subtype_avg_r != 0.0) base = 0.65 * base + 0.35 * p.subtype_avg_r;
      return base;
   }

   double _ExpectedValueRGross(const TradePlan &p) const {
      double win_rate = _ExpectedWinRateGross(p);
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
      if(p.subtype_risk_multiplier <= 0.0) p.subtype_risk_multiplier = 1.0;
      if(p.ote_softness_frac <= 0.0){
         p.ote_softness_frac = (m_active_policy.ote_softness_frac > 0.0 ? m_active_policy.ote_softness_frac : InpOteSoftnessFrac);
      }
      p.ote_distance_frac = _OteDistanceFrac(p);
      p.ote_state = _OteState(p);
      _EstimateExecutionCosts(p);
      p.gross_expected_r = _ExpectedValueRGross(p);
      p.net_expected_r = p.gross_expected_r - p.execution_cost_r - p.slippage_r - p.commission_r;
      p.expected_value_r = p.net_expected_r;
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
      p.subtype_risk_multiplier = (entry.risk_multiplier > 0.0 ? entry.risk_multiplier : 1.0);
      if(StringLen(entry.policy_id) > 0) p.policy_snapshot_id = entry.policy_id;

      if(p.subtype_policy_action == "suppress"){
         reason = "subtype_suppressed";
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
      int idx = _FindContextPolicy(p.policy_bucket);
      if(idx < 0) return true;

      ContextPolicyEntry entry = m_context_policy[idx];
      if(StringLen(entry.policy_id) > 0) p.policy_snapshot_id = entry.policy_id;
      if(entry.action == "suppress"){
         reason = "context_suppressed";
         return false;
      }
      p.setup_score += entry.score_bias;
      p.expected_value_r += entry.expected_value_bias;
      p.subtype_risk_multiplier *= entry.risk_multiplier;
      return true;
   }

   bool _ApplySessionWeekdayPolicy(TradePlan &p, string &reason) {
      reason = "";
      _LoadActivePolicyIfNeeded();

      if(p.subtype_risk_multiplier <= 0.0) p.subtype_risk_multiplier = 1.0;
      if(p.session_weekday_risk_multiplier > 0.0 && MathAbs(p.session_weekday_risk_multiplier - 1.0) > 0.0001){
         p.subtype_risk_multiplier /= p.session_weekday_risk_multiplier;
         if(p.subtype_risk_multiplier <= 0.0) p.subtype_risk_multiplier = 1.0;
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
      p.session_weekday_risk_multiplier = _ClampRange(entry.risk_multiplier, 0.05, 1.50);
      p.session_weekday_rr_delta = _ClampRange(entry.rr_floor_delta, -0.25, 0.50);
      p.session_weekday_score_bias = _ClampRange(entry.score_bias, -12.0, 8.0);
      if(StringLen(entry.policy_id) > 0) p.policy_snapshot_id = entry.policy_id;

      if(action == "suppress"){
         reason = "session_weekday_suppressed";
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
         reason = "pre_ai_floor_hard";
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
         p.po3.has_displacement = false;
         p.po3.t_disp = 0;
         changed = true;
      }

      if(p.po3.has_bos && p.po3.t_bos <= 0){
         p.po3.has_bos = false;
         p.po3.t_bos = 0;
         p.po3.htf_mss = false;
         p.po3.htf_choch = false;
         changed = true;
      }

      if(p.po3.has_displacement && p.po3.t_sweep > 0 && p.po3.t_disp <= p.po3.t_sweep){
         p.po3.has_displacement = false;
         p.po3.t_disp = 0;
         changed = true;
      }

      if(p.po3.has_bos && p.po3.t_disp > 0 && p.po3.t_bos > 0 && p.po3.t_bos <= p.po3.t_disp){
         p.po3.has_bos = false;
         p.po3.t_bos = 0;
         p.po3.htf_mss = false;
         p.po3.htf_choch = false;
         changed = true;
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
      string sig = ENGINE_VERSION + "|" + ENGINE_INPUT_SCHEMA + "|" + _GroupSignature(plans);
      for(int i=0; i<ArraySize(plans); i++){
         sig += "|" + plans[i].setup_family;
         sig += "|" + plans[i].setup_class;
         sig += "|" + plans[i].entry_branch;
         sig += "|" + DoubleToString(plans[i].setup_score, 4);
         sig += "|" + DoubleToString(plans[i].expected_value_r, 4);
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

   string _TesterAiCachePath(const string signature) const {
      return _TesterAiCacheDir() + "\\" + _TesterAiCacheKey(signature) + ".json";
   }

   string _AiDecisionJson(const string req_id, const string signature, const AiDecision &dec) const {
      string rejection_codes = dec.rejection_codes_json;
      if(StringLen(rejection_codes) == 0) rejection_codes = "[]";
      string invalidation_risks = dec.invalidation_risks_json;
      if(StringLen(invalidation_risks) == 0) invalidation_risks = "[]";
      string missing_confirmations = dec.missing_confirmations_json;
      if(StringLen(missing_confirmations) == 0) missing_confirmations = "[]";
      string j = "{";
      j += JsonKVStr("id", req_id) + ",";
      j += JsonKVStr("cache_signature", signature) + ",";
      j += JsonKVBool("allow", dec.allow) + ",";
      j += JsonKVNum("score", dec.score, 6) + ",";
      j += JsonKVInt("chosen_index", dec.chosen_index) + ",";
      j += JsonKVNum("confidence", dec.confidence, 6) + ",";
      j += JsonKVStr("decision_source", dec.decision_source) + ",";
      j += JsonKVStr("reasons", dec.reasons_json) + ",";
      j += JsonKVStr("decision_id", dec.decision_id) + ",";
      j += "\"rejection_codes\":" + rejection_codes + ",";
      j += JsonKVStr("narrative_state", dec.narrative_state) + ",";
      j += "\"invalidation_risks\":" + invalidation_risks + ",";
      j += "\"missing_confirmations\":" + missing_confirmations + ",";
      j += JsonKVNum("suggested_risk_multiplier", dec.suggested_risk_multiplier, 6) + ",";
      j += JsonKVStr("model_version", dec.model_version);
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

   bool _TryLoadTesterAiDecision(const string signature, AiDecision &out) {
      if(!MQLInfoInteger(MQL_TESTER) || !InpTesterAiCache || StringLen(signature) == 0) return false;
      int idx = _FindTesterAiCache(signature);
      if(idx >= 0){
         out = m_tester_ai_cache_decisions[idx];
         return out.ok;
      }

      string txt;
      if(!m_bus.ReadText(_TesterAiCachePath(signature), txt)) return false;
      if(JsonGetString(txt, "cache_signature", "") != signature) return false;
      double score = JsonGetNumber(txt, "score", -1.0e100);
      double confidence = JsonGetNumber(txt, "confidence", -1.0e100);
      double chosen = JsonGetNumber(txt, "chosen_index", -1.0e100);
      if(score <= -1.0e90 || confidence <= -1.0e90 || chosen <= -1.0e90) return false;

      ZeroMemory(out);
      out.ok = true;
      out.allow = JsonGetBool(txt, "allow", false);
      out.score = score;
      out.chosen_index = (int)chosen;
      out.confidence = confidence;
      out.decision_source = JsonGetString(txt, "decision_source", "");
      out.reasons_json = JsonGetString(txt, "reasons", "");
      out.decision_id = JsonGetString(txt, "decision_id", "");
      out.rejection_codes_json = JsonGetArray(txt, "rejection_codes", "[]");
      out.narrative_state = JsonGetString(txt, "narrative_state", "");
      out.invalidation_risks_json = JsonGetArray(txt, "invalidation_risks", "[]");
      out.missing_confirmations_json = JsonGetArray(txt, "missing_confirmations", "[]");
      out.suggested_risk_multiplier = JsonGetNumber(txt, "suggested_risk_multiplier", 1.0);
      out.model_version = JsonGetString(txt, "model_version", "");
      _PutTesterAiCacheMemory(signature, out);
      return true;
   }

   void _RememberTesterAiDecision(const string signature, const AiDecision &dec) {
      if(!MQLInfoInteger(MQL_TESTER) || !InpTesterAiCache || !dec.ok || StringLen(signature) == 0) return;
      string source = dec.decision_source;
      StringToLower(source);
      if(StringFind(source, "error") >= 0 || StringFind(source, "fallback") >= 0) return;
      _PutTesterAiCacheMemory(signature, dec);
      FolderCreate(_TesterAiCacheDir(), FILE_COMMON);
      m_bus.WriteText(_TesterAiCachePath(signature), _AiDecisionJson("cached", signature, dec));
   }

   bool _QueueTesterCachedDecision(TradePlan &plans[], const string signature, const AiDecision &dec) {
      if(ArraySize(plans) <= 0) return false;
      string req_id = "cache_" + _TesterAiCacheKey(signature) + "_" + IntegerToString((int)TimeLocal())
                      + "_" + IntegerToString((int)MathRand());
      string response_path = m_bus.RespDir() + "\\" + req_id + ".json";
      if(!m_bus.WriteText(response_path, _AiDecisionJson(req_id, signature, dec))) return false;

      int base = ArraySize(m_pending_ai);
      ArrayResize(m_pending_ai, base + ArraySize(plans));
      for(int i=0; i<ArraySize(plans); i++){
         plans[i].req_id = req_id;
         plans[i].ai_requested_at = TimeLocal();
         plans[i].ai_requested_wall_ms = _WallClockMs();
         m_pending_ai[base + i] = plans[i];
      }
      _Journal(plans[0].symbol + " tester AI cache hit candidates=" + IntegerToString(ArraySize(plans))
               + " key=" + _TesterAiCacheKey(signature));
      return true;
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

   void _ArchiveBusArtifact(const string rel_path) {
      if(StringLen(rel_path) == 0) return;
      if(!_PathExists(rel_path)) return;
      m_bus.CopyToStale(rel_path);
   }

   void _ArchivePendingArtifacts(const string req_id) {
      if(StringLen(req_id) == 0) return;
      _ArchiveBusArtifact(m_bus.ReqDir() + "\\" + req_id + ".json");
      _ArchiveBusArtifact(m_bus.RespDir() + "\\" + req_id + ".json");
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
         return (_WallElapsedMs(p.ai_requested_wall_ms) > (ulong)(timeout_min * 60 * 1000));
      }
      if(p.ai_requested_at <= 0) return false;
      return ((TimeLocal() - p.ai_requested_at) > (timeout_min * 60));
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
         meta.mfe_r = (meta.is_buy ? (meta.mfe_price - meta.filled_entry) : (meta.filled_entry - meta.mfe_price)) / risk_dist;
         meta.mae_r = (meta.is_buy ? (meta.mae_price - meta.filled_entry) : (meta.filled_entry - meta.mae_price)) / risk_dist;
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
         if(meta.mae_r <= -1.0 && meta.mfe_r >= 0.5) realized_quality -= 1.0;
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
      if(!InpSkipIfSymbolOpen) return false;
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
      if(!InpSkipIfSymbolOpen) return false;
      return (_SymbolHasOpenPosition(symbol) || _SymbolHasPendingOrder(symbol));
   }

   bool _SymbolBusy(const string symbol) {
      if(_ShouldSkipForGlobalOpenPositions()) return true;
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

   ulong _FindPositionTicket(const string symbol) {
      for(int i=0; i<PositionsTotal(); i++){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL) == symbol) return ticket;
      }
      return 0;
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
               + " net_expected_r=" + DoubleToString(p.net_expected_r, 3)
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

   bool _SyntheticTargetBlockedByObstacle(const string obstacle_kind) const {
      if(InpRejectSyntheticFallbackAfterCrossedObstacle &&
         (StringFind(obstacle_kind, "opposing") >= 0 || StringFind(obstacle_kind, "imbalance") >= 0))
         return true;
      if(InpBlockSyntheticTargetThroughOpposingImbalance) return true;
      if(InpRejectAgainstHtfImbalance && obstacle_kind == "htf_opposing_imbalance") return true;
      return false;
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

      PriceLevelCandidate targets[];
      PriceLevelCandidate obstacles[];
      _CollectTargetLevels(p, p.entry_est, targets, obstacles);

      bool saw_too_near = false;
      bool saw_liquidity_too_near = false;
      bool saw_valid_target = false;
      double min_rr = MathMax(0.0, _FamilyMinRR(p));

      for(int i=0; i<ArraySize(targets); i++){
         double candidate_target = targets[i].price;
         if(!_IsRewardSideLevel(p.is_buy, p.entry_est, candidate_target)) continue;

         string obstacle_kind = "";
         double obstacle_price = 0.0;
         double effective_target = candidate_target;
         _NearestObstacleBeforeTarget(p, p.entry_est, candidate_target, obstacles,
                                      obstacle_kind, obstacle_price, effective_target);

         double reward = _RewardToTarget(p.is_buy, p.entry_est, effective_target);
         if(reward <= 0) continue;
         saw_valid_target = true;
         if(min_target_dist > 0 && reward < min_target_dist){
            saw_too_near = true;
            if(targets[i].priority <= 1) saw_liquidity_too_near = true;
            continue;
         }
         double rr = (stop_dist > 0 ? reward / stop_dist : 0.0);
         if(rr < min_rr){
            saw_too_near = true;
            if(targets[i].priority <= 1) saw_liquidity_too_near = true;
            continue;
         }

         p.tp2 = effective_target;
         p.tp_model = targets[i].kind;
         p.target_source = targets[i].kind;
         p.obstacle_kind = obstacle_kind;
         p.obstacle_price = obstacle_price;
         p.obstacle_r = rr;
         p.effective_rr2 = rr;
         if(InpRejectAgainstHtfImbalance && obstacle_kind == "htf_opposing_imbalance" && rr < InpObstacleMinStopMult){
            reason = "liquidity_target_too_near";
            return false;
         }
         return true;
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
               if(min_target_dist > 0 && reward < min_target_dist){
                  saw_too_near = true;
               } else {
                  p.tp2 = effective_target;
                  p.tp_model = "fib_extension";
                  p.target_source = "fib_extension";
                  p.obstacle_kind = obstacle_kind;
                  p.obstacle_price = obstacle_price;
                  p.obstacle_r = (stop_dist > 0 ? reward / stop_dist : 0.0);
                  p.effective_rr2 = p.obstacle_r;
                  if(p.effective_rr2 >= min_rr) return true;
               }
            }
         }
      }

      double fallback_rr = MathMax(InpFallbackRR2, min_rr);
      if(InpMaxPlanRR2 > 0) fallback_rr = MathMin(fallback_rr, InpMaxPlanRR2);
      double synthetic_reward = stop_dist * fallback_rr;
      if(max_target_dist > 0.0 && synthetic_reward > max_target_dist)
         synthetic_reward = max_target_dist;
      if(InpAllowSyntheticRRTarget && synthetic_reward > 0 && (saw_valid_target || saw_too_near)){
         double synthetic_target = (p.is_buy ? p.entry_est + synthetic_reward : p.entry_est - synthetic_reward);
         string obstacle_kind = "";
         double obstacle_price = 0.0;
         bool obstacle_before = _HasOpposingObstacleBeforeTarget(p, p.entry_est, synthetic_target, obstacles,
                                                                 obstacle_kind, obstacle_price);
         if(obstacle_before){
            double buffer = _ObstacleBufferPrice(p.symbol);
            double capped_target = (p.is_buy ? obstacle_price - buffer : obstacle_price + buffer);
            double capped_reward = _RewardToTarget(p.is_buy, p.entry_est, capped_target);
            double capped_rr = (stop_dist > 0 ? capped_reward / stop_dist : 0.0);
            if(capped_reward > 0 &&
               (min_target_dist <= 0 || capped_reward >= min_target_dist) &&
               capped_rr >= min_rr){
               p.tp2 = capped_target;
               p.tp_model = "capped_before_" + obstacle_kind;
               p.target_source = "capped_before_" + obstacle_kind;
               p.obstacle_kind = obstacle_kind;
               p.obstacle_price = obstacle_price;
               p.obstacle_r = capped_rr;
               p.effective_rr2 = capped_rr;
               return true;
            }
            if(_SyntheticTargetBlockedByObstacle(obstacle_kind)){
               reason = "synthetic_fallback_crossed_obstacle_blocked";
               return false;
            }
         }
         double reward = _RewardToTarget(p.is_buy, p.entry_est, synthetic_target);
         if(reward > 0){
            if(obstacle_before && InpRejectSyntheticFallbackAfterCrossedObstacle){
               reason = "synthetic_fallback_crossed_obstacle_blocked";
               return false;
            }
            p.tp2 = synthetic_target;
            p.tp_model = "synthetic_rr_fallback";
            p.target_source = "synthetic_rr_fallback";
            p.obstacle_kind = (obstacle_before ? "crossed_" + obstacle_kind : "");
            p.obstacle_price = (obstacle_before ? obstacle_price : 0.0);
            p.obstacle_r = 0.0;
            p.effective_rr2 = (stop_dist > 0 ? reward / stop_dist : 0.0);
            if(p.effective_rr2 >= min_rr &&
               (min_target_dist <= 0 || reward >= min_target_dist))
               return true;
            reason = "target_too_close_for_swing_duration";
            return false;
         }
      }

      if(saw_liquidity_too_near) reason = "liquidity_target_too_near";
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
         reject_reason = "structural_stop_invalid";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      double target_cap = _MaxPlanTargetDistance(p, p.entry_est, stop_dist);
      double min_target_dist = _MinSwingTargetDistance(p, p.entry_est);
      string target_reason = "";
      if(!_SelectObstacleAwareTarget(p, stop_dist, min_target_dist, target_cap, target_reason)){
         reject_reason = (StringLen(target_reason) > 0 ? target_reason : "target_too_close_for_swing_duration");
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }
      double reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp2);
      if(target_cap > 0 && reward > target_cap){
         reward = target_cap;
         if(p.is_buy) p.tp2 = p.entry_est + reward;
         else         p.tp2 = p.entry_est - reward;
         p.effective_rr2 = (stop_dist > 0 ? reward / stop_dist : 0.0);
         if(StringFind(p.tp_model, "_capped") < 0) p.tp_model += "_capped";
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

      double tp1_reward = stop_dist * MathMax(0.40, p.tp1_r_multiple);
      double min_tp1_reward = MathMax(stop_dist * 0.65, spread_price * InpMinTP1SpreadMult);
      if(min_tp1_reward > 0) tp1_reward = MathMax(tp1_reward, min_tp1_reward);
      if(tp2_reward > 0) tp1_reward = MathMin(tp1_reward, tp2_reward * 0.70);
      if(tp1_reward <= 0) tp1_reward = MathMin(tp2_reward, stop_dist);
      if(p.is_buy) p.tp1 = p.entry_est + tp1_reward;
      else         p.tp1 = p.entry_est - tp1_reward;

      if(!_StopsDistanceOk(p.symbol, p.is_buy, p.entry_est, p.sl, p.tp2)){
         reject_reason = "broker_distance_invalid";
         return _BuildPlanReject(p, reject_reason, p.entry_est, p.sl, p.tp2);
      }

      _ApplyStopAudit(p, initial_sl, p.entry_est);
      _EstimateExecutionCosts(p);
      p.ote_distance_frac = _OteDistanceFrac(p);
      p.ote_state = _OteState(p);
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
      if(ev_floor > 0.0 && p.expected_value_r < ev_floor){ reason = "runner_ev_floor"; return false; }
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
      if(rr < min_rr || total_cost_r > InpStandardTradeCostRCeiling){
         double fallback_rr = MathMax(min_rr, InpFallbackRR2);
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
      p.gross_expected_r = _ExpectedValueRGross(p);
      p.net_expected_r = p.gross_expected_r - p.execution_cost_r - p.slippage_r - p.commission_r;
      p.expected_value_r = p.net_expected_r;
      return (_ExecutionRR2(p) >= min_rr &&
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
      if(execution_stage && _IsRealAccount() && InpUseAI && InpLiveFailClosedOnAIFailure && !InpAllowRuleOnlyLive){
         if(StringFind(p.ai_decision_source, "rule_only") >= 0 || StringFind(p.ai_decision_source, "fallback") >= 0){
            reason = "rule_only_live_blocked";
            return false;
         }
         if(p.ai.confidence < InpMinAiConfidence){
            reason = "ai_confidence_below_threshold";
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
      if(rr < min_rr){
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
      p.net_expected_r = p.gross_expected_r - p.execution_cost_r - p.slippage_r - p.commission_r;
      p.expected_value_r = p.net_expected_r;
      if(p.net_expected_r < InpMinNetExpectedR){
         reason = "net_expected_r_too_low";
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

   bool _AiHardVeto(const AiDecision &dec) const {
      if(!dec.ok) return false;
      if(dec.allow) return false;
      if(dec.confidence < 0.72) return false;
      return (dec.score <= 5.20);
   }

   double _RequiredAiScore(const TradePlan &p) const {
      double score = MathMax(0.0, _FamilyAiScoreFloor(p));
      if(!InpRequireConfirmedPO3ForExecution){
         if(p.po3.context_tier == "B") score += 0.35;
         else if(p.po3.context_tier == "C") score += 0.75;
      }
      return score;
   }

   double _RequiredAiConfidence(const TradePlan &p) const {
      double confidence = InpMinAiConfidence;
      if(!InpRequireConfirmedPO3ForExecution){
         if(p.po3.context_tier == "B") confidence += 0.03;
         else if(p.po3.context_tier == "C") confidence += 0.06;
      }
      return MathMin(0.95, confidence);
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
      TradePlan p = base;
      p.fvg = zone;
      p.entry_branch = branch;
      p.entry_model = branch;
      if(!_BranchEnabledByConfig(branch, reason)) return false;
      if(InpRequireFvgAfterDisp && p.po3.has_displacement && zone.t_form <= p.po3.t_disp){
         reason = "fvg_not_after_impulse";
         return false;
      }
      if(p.po3.has_sweep && p.po3.has_displacement && p.po3.has_bos){
         PO3SetState(p.po3, PO3_FVG_CONFIRMED, "valid_fvg_after_impulse");
      } else if(!p.po3.has_bos){
         if(p.po3.has_displacement)
            PO3SetState(p.po3, PO3_DISPLACEMENT_CONFIRMED, "missing_structure_confirmation");
         else if(p.po3.has_sweep)
            PO3SetState(p.po3, PO3_SWEEP_CONFIRMED, "missing_displacement_confirmation");
         else
            PO3SetState(p.po3, PO3_DEVELOPING, "missing_po3_sequence");
      }
      if(branch == "fvg_edge") p.entry_model = (p.is_buy ? "fvg_upper" : "fvg_lower");
      p.fvg_execution_class = _FvgExecutionClass(p);
      p.setup_family = _DeriveSetupFamily(p);
      string family_reason = "";
      if(!_FamilyAllowedByStrategy(p, family_reason)){
         reason = family_reason;
         return false;
      }
      if(p.setup_family == "micro_continuation_fvg" && !p.po3.has_displacement){
         reason = "continuation_no_impulse";
         return false;
      }
      if(p.setup_family == "micro_continuation_fvg" && p.po3.t_disp > 0 && zone.t_form < p.po3.t_disp){
         reason = "continuation_fvg_missing";
         return false;
      }
      if(p.setup_family == "micro_failed_breakout_reclaim" && p.po3.t_sweep > 0 && zone.t_form <= p.po3.t_sweep){
         reason = "failed_breakout_fvg_missing";
         return false;
      }
      p.setup_class = _DeriveSetupClass(p);
      _ApplySetupManagementProfile(p);
      _InitializeNarrativeFields(p);
      if(InpOnlyBreakerRetestVirginStrongOrigin && InpExclusiveModelFilterBeforeAI){
         if(!_ApplyExclusiveModelFilter(p, "exclusive_model_filter", true)){
            reason = "exclusive_breaker_retest_virgin_strong_origin_only";
            return false;
         }
      }

      double entry_price = 0.0;
      if(!_ResolveBranchEntryPrice(p, branch, entry_price, reason)) return false;
      string price_reason = "";
      if(!_BuildPlanPrices(p, entry_price, price_reason)){
         reason = (StringLen(price_reason) > 0 ? price_reason : "invalid_plan_prices");
         return false;
      }
      m_funnel_plan_prices_valid++;
      if(p.po3.state == PO3_FVG_CONFIRMED)
         PO3SetState(p.po3, PO3_ENTRY_WAITING, "valid_entry_zone_waiting");
      if(!m_po3.CheckOTE(p)){
         reason = "ote_failed";
         return false;
      }
      string live_reason = "";
      if(!_WatchlistStillValidEx(p, live_reason)){
         reason = live_reason;
         return false;
      }

      _LoadActivePolicyIfNeeded();
      _PopulateDerivedPlanFields(p);
      p.setup_score = _SetupScore(p);
      string subtype_reason = "";
      if(!_ApplySubtypeEvidence(p, subtype_reason)){
         reason = subtype_reason;
         return false;
      }
      string context_reason = "";
      if(!_ApplyContextPolicy(p, context_reason)){
         reason = context_reason;
         return false;
      }
      string session_weekday_reason = "";
      if(!_ApplySessionWeekdayPolicy(p, session_weekday_reason)){
         reason = session_weekday_reason;
         return false;
      }
      _PopulateDerivedPlanFields(p);
      string floor_reason = "";
      if(!_ApplyPreAiSetupFloor(p, floor_reason)){
         reason = floor_reason;
         return false;
      }
      _PopulateDerivedPlanFields(p);
      string rule_reason = "";
      if(!_DeterministicExecutionGate(p, rule_reason, false)){
         reason = rule_reason;
         return false;
      }
      out_plan = p;
      return true;
   }

   bool _CandidateRanksAhead(const TradePlan &a, const TradePlan &b) const {
      if(a.expected_value_r > b.expected_value_r) return true;
      if(a.expected_value_r < b.expected_value_r) return false;
      if(a.setup_score > b.setup_score) return true;
      if(a.setup_score < b.setup_score) return false;
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
      _FreezeSourcePO3Story(staged);
      _ApplySetupManagementProfile(staged);
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
      if(StringLen(m_watchlist[n].ai_decision_source) == 0) m_watchlist[n].ai_decision_source = "rule_only_fallback";
      m_watchlist[n].narrative_state = "staged";
      m_funnel_watchlist_added++;
      _Journal(p.symbol + " added to watchlist entry=" + DoubleToString(m_watchlist[n].entry_est, 5)
               + " rr2=" + DoubleToString(_ExecutionRR2(m_watchlist[n]), 2)
               + " setup_class=" + m_watchlist[n].setup_class
               + " po3_scope=" + m_watchlist[n].po3.po3_scope
               + " source_story=" + _PO3StoryId(m_watchlist[n])
               + " attempt_number_for_sweep=" + IntegerToString(m_watchlist[n].attempt_number_for_sweep));
      return true;
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

      for(int i=0; i<count; i++){
         cands[i].candidate_index = i;
         cands[i].candidate_count = count;
      }
      string group_signature = _GroupSignature(cands);
      string tester_cache_signature = _TesterAiCacheSignature(cands);
      AiDecision cached_decision;
      if(InpUseAI && _TryLoadTesterAiDecision(tester_cache_signature, cached_decision)){
         if(_QueueTesterCachedDecision(cands, tester_cache_signature, cached_decision)) return true;
         _Journal(cands[0].symbol + " tester AI cache response staging failed; using normal AI path");
      }
      int cooldown_remaining = 0;
      if(InpUseAI && _IsAiCooldownActive(cands[0].symbol, group_signature, cooldown_remaining)){
         _Journal(cands[0].symbol + " AI request skipped: unchanged setup cooling down for "
                  + IntegerToString(cooldown_remaining) + "s");
         if(InpAiStrict) return false;
         _Journal(cands[0].symbol + " AI cooldown bypassed via rules-only watchlist because strict AI is off");
         if(!_ApplyRuleFallback(cands[0], "cooldown")) return false;
         return _AddToWatchlist(cands[0]);
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
         if(m_ai.SendRequestCandidates(cands, req_id)){
            m_funnel_ai_requests++;
            _Journal(cands[0].symbol + " AI request queued candidates=" + IntegerToString(count) + " req_id=" + req_id);
            int base = ArraySize(m_pending_ai);
            ArrayResize(m_pending_ai, base + count);
            for(int i=0; i<count; i++){
               cands[i].req_id = req_id;
               cands[i].ai_requested_at = TimeLocal();
               cands[i].ai_requested_wall_ms = _WallClockMs();
               m_pending_ai[base + i] = cands[i];
            }
            return true;
         }
         _RememberAiCooldown(cands[0].symbol, group_signature, "send_failed");
         _LogSetupReject(cands[0].symbol, "ai", "send_failed",
                         "candidate_count=" + IntegerToString(count));
         _Journal(cands[0].symbol + " AI request failed to send");
         m_ai.NotifyFailureBackoff();
         if(InpAiStrict) return false;
         _Journal(cands[0].symbol + " falling back to rules-only watchlist mode");
      }

      if(!_ApplyRuleFallback(cands[0], (InpUseAI ? "send_failed" : "ai_disabled"))) return false;
      return _AddToWatchlist(cands[0]);
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

   bool _FallbackPendingGroup(const string req_id, const string reason) {
      TradePlan selected;
      bool have_selected = false;
      string group_symbol = "";
      for(int i=0; i<ArraySize(m_pending_ai); i++){
         if(m_pending_ai[i].req_id != req_id) continue;
         if(StringLen(group_symbol) == 0) group_symbol = m_pending_ai[i].symbol;
         if(!have_selected || m_pending_ai[i].expected_value_r > selected.expected_value_r ||
            (m_pending_ai[i].expected_value_r == selected.expected_value_r &&
             m_pending_ai[i].setup_score > selected.setup_score)){
            selected = m_pending_ai[i];
            have_selected = true;
         }
      }
      if(!have_selected){
         _RemovePendingGroup(req_id);
         return false;
      }

      selected.req_id = "";
      selected.ai_requested_at = 0;
      selected.ai_requested_wall_ms = 0;
      if(!_ApplyRuleFallback(selected, reason)){
         _RemovePendingGroup(req_id);
         return false;
      }
      selected.last_confirm_bar_time = _LastClosedBarTime(selected.symbol, selected.confirm_tf);
      _Journal(group_symbol + " AI " + reason + " -> rules fallback candidate="
               + IntegerToString(selected.candidate_index)
               + " setup_score=" + DoubleToString(selected.setup_score, 2));
      bool added = _AddToWatchlist(selected);
      _RemovePendingGroup(req_id);
      return added;
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
            return false;
         }
         if(p.fvg.lower > 0 && px < (p.fvg.lower - eps)){
            reason = "price_broke_fvg_low";
            return false;
         }
         if(target > 0 && px >= (target - eps)){
            reason = "target_already_reached";
            return false;
         }
      } else {
         if(structural_high > 0 && px > (structural_high + eps)){
            reason = "structural_invalidation_high";
            return false;
         }
         if(p.fvg.upper > 0 && px > (p.fvg.upper + eps)){
            reason = "price_broke_fvg_high";
            return false;
         }
         if(target > 0 && px <= (target + eps)){
            reason = "target_already_reached";
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

      TradePlan relaxed = meta;
      relaxed.entry_est = relaxed_entry;
      string price_reason = "";
      if(!_BuildPlanPrices(relaxed, relaxed_entry, price_reason)){
         _Journal(meta.symbol + " pending entry relax skipped old_entry=" + _FmtPrice(meta.symbol, old_entry)
                  + " relaxed_entry=" + _FmtPrice(meta.symbol, relaxed_entry)
                  + " reason=" + price_reason);
         return false;
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

   bool _ReadTradeMetaPath(const string path, TradePlan &p){
      string txt;
      if(!m_bus.ReadText(path, txt)) return false;
      return _ParseTradeMetaJson(txt, p);
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
      if(StringLen(meta.ai_decision_source) == 0) meta.ai_decision_source = "rule_only_fallback";
      string j = m_state.TradePlanToJson(meta);
      m_bus.WriteText(_TradeKeyPath(meta.trade_key), j);
      if(StringLen(meta.broker_comment) > 0 && meta.broker_comment != meta.trade_key)
         m_bus.WriteText(_TradeKeyPath(meta.broker_comment), j);
      if(ticket > 0) m_bus.WriteText(_TradeTicketPath(ticket), j);
      if(meta.position_id > 0) m_bus.WriteText(_TradeTicketPath((ulong)meta.position_id), j);
   }

   bool _LoadTradeMeta(const ulong ticket, const string symbol, const string comment, TradePlan &p){
      if(ticket > 0 && _ReadTradeMetaPath(_TradeTicketPath(ticket), p)) return true;
      if(StringLen(comment) > 0 && _ReadTradeMetaPath(_TradeKeyPath(comment), p)){
         if(ticket > 0) _WriteTradeMeta(p, ticket);
         return true;
      }
      return _ReadTradeMetaPath(_LegacyTradeSymbolPath(symbol), p);
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
         bool had_meta = _LoadTradeMeta(ticket, sym, comment, meta);
         if(!had_meta) ZeroMemory(meta);
         _NormalizeRecoveredExposureMeta(meta, sym, is_buy, entry, sl, tp, vol, opened, comment, position_id, false);
         double live_px = (is_buy ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK));
         if(live_px <= 0.0) live_px = entry;
         _UpdateAnalyticsSnapshot(meta, ticket, live_px, vol);
         _WriteTradeMeta(meta, ticket);
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
         if(!had_meta) ZeroMemory(meta);
         _NormalizeRecoveredExposureMeta(meta, sym, is_buy_limit, entry, sl, tp, vol, setup_time, comment, (long)ticket, true);
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

   bool _WriteClosedTradeOutcome(const string key_hint, const long position_id, const string symbol_hint) {
      if(!InpAnalyticsEnable) return false;

      string key = key_hint;
      if(StringLen(key) == 0 && position_id > 0) key = symbol_hint + "_" + IntegerToString((int)position_id);
      if(StringLen(key) == 0) return false;
      if(_PathExists(_TradeResultPath(key))) return false;
      if(_HasOpenPositionWithKey(key)) return false;

      TradePlan meta;
      bool have_meta = false;
      if(StringLen(key_hint) > 0) have_meta = _ReadTradeMetaPath(_TradeKeyPath(key_hint), meta);
      if(!have_meta && position_id > 0) have_meta = _ReadTradeMetaPath(_TradeTicketPath((ulong)position_id), meta);
      if(!have_meta && StringLen(symbol_hint) > 0) have_meta = _ReadTradeMetaPath(_LegacyTradeSymbolPath(symbol_hint), meta);
      if(have_meta && StringLen(meta.trade_key) > 0) key = meta.trade_key;
      if(!have_meta){
         ZeroMemory(meta);
         meta.trade_key = key;
         meta.symbol = symbol_hint;
      }

      datetime now = _NowServerOrLocal();
      datetime from = now - MathMax(1, InpAnalyticsHistoryDays) * 86400;
      if(!HistorySelect(from, now)) return false;

      double in_vol = 0.0, in_value = 0.0;
      double out_vol = 0.0, out_value = 0.0;
      double realized_pnl = 0.0;
      int out_count = 0;
      datetime first_in_time = 0;
      datetime last_out_time = 0;
      long final_reason = -1;
      string final_symbol = symbol_hint;
      string partials_json = "[";
      int partial_count = 0;

      int deals = HistoryDealsTotal();
      for(int i=0; i<deals; i++){
         ulong deal_ticket = HistoryDealGetTicket(i);
         if(deal_ticket == 0) continue;
         if((ulong)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) != InpMagicNumber) continue;

         long deal_pos_id = (long)HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
         string deal_comment = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
         if(position_id > 0){
            if(deal_pos_id != position_id && deal_comment != key_hint) continue;
         } else if(StringLen(key_hint) > 0){
            if(deal_comment != key_hint) continue;
         } else {
            continue;
         }

         ENUM_DEAL_ENTRY entry_kind = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
         double vol = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
         double price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
         datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
         final_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);

         if(entry_kind == DEAL_ENTRY_IN){
            in_vol += vol;
            in_value += price * vol;
            if(first_in_time == 0 || deal_time < first_in_time) first_in_time = deal_time;
         }
         if(entry_kind == DEAL_ENTRY_OUT || entry_kind == DEAL_ENTRY_OUT_BY || entry_kind == DEAL_ENTRY_INOUT){
            double deal_profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
            double deal_commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
            double deal_swap = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
            long deal_reason = HistoryDealGetInteger(deal_ticket, DEAL_REASON);
            out_vol += vol;
            out_value += price * vol;
            realized_pnl += deal_profit + deal_commission + deal_swap;
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
            partials_json += JsonKVNum("pnl", deal_profit + deal_commission + deal_swap, 2) + ",";
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

      if(out_count <= 0 || last_out_time <= 0) return false;
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
         meta.ai_decision_source = (StringLen(meta.ai.decision_source) > 0 ? meta.ai.decision_source : "rule_only_fallback");
      }
      _PopulateDerivedPlanFields(meta);

      double risk_dist = _RiskDistanceForMeta(meta);
      if(risk_dist > 0 && meta.planned_entry > 0){
         meta.fill_slippage = meta.filled_entry - meta.planned_entry;
         double adverse = (meta.is_buy ? (meta.filled_entry - meta.planned_entry) : (meta.planned_entry - meta.filled_entry));
         meta.fill_slippage_r = adverse / risk_dist;
      }

      double exit_price = (out_vol > 0 ? out_value / out_vol : 0.0);
      meta.closed_at = last_out_time;
      meta.realized_pnl = realized_pnl;
      if(risk_dist > 0 && meta.initial_volume > 0){
         double initial_risk_money = _RiskMoneyForPosition(meta.symbol, meta.is_buy, meta.initial_volume,
                                                           (meta.filled_entry > 0 ? meta.filled_entry : meta.planned_entry),
                                                           (meta.planned_sl > 0 ? meta.planned_sl : meta.sl));
         if(initial_risk_money > 0) meta.realized_r = realized_pnl / initial_risk_money;
      }
      meta.exit_path = _ExitPathLabel(meta, exit_price, out_count, final_reason);
      double plan_rr_for_eff = _PlanRR2(meta);
      if(plan_rr_for_eff > 0.0) meta.target_efficiency = _ClampRange(meta.mfe_r / plan_rr_for_eff, 0.0, 2.0);
      meta.tp2_realistic_before_reversal = (meta.tp2_hit_at > 0 || (plan_rr_for_eff > 0.0 && meta.mfe_r >= plan_rr_for_eff * 0.90));
      meta.be_move_helped = (meta.tp1_done && StringFind(meta.exit_path, "stop") >= 0 && meta.realized_r >= 0.0);
      meta.trailing_stop_improved = false;
      meta.analytics_logged = true;

      string po3_subtype = _Po3Subtype(meta);
      string fvg_subtype = _FvgSubtype(meta);
      string regime_bucket = _RegimeBucket(meta);
      if(StringLen(meta.origin_quality) == 0) meta.origin_quality = _OriginQuality(meta, meta.fvg);
      if(StringLen(meta.exclusive_model_name) == 0) meta.exclusive_model_name = _ExclusiveModelName();
      double rr2 = _PlanRR2(meta);
      double effective_rr2 = _ExecutionRR2(meta);
      double liquidity_rr = _LiquidityRR(meta);
      double virtual_balance_base = MathMax(1.0, InpAnalyticsVirtualBalance);
      double result_pct_virtual = (meta.realized_pnl / virtual_balance_base) * 100.0;
      string data_integrity_status = "clean";
      string data_integrity_reasons = "[";
      int integrity_count = 0;
      if(meta.position_id <= 0){
         data_integrity_status = "suspicious";
         data_integrity_reasons += "\"missing_position_id\"";
         integrity_count++;
      }
      if(MathAbs(meta.realized_pnl) <= 0.0000001){
         if(integrity_count > 0) data_integrity_reasons += ",";
         data_integrity_status = "suspicious";
         data_integrity_reasons += "\"zero_realized_pnl\"";
         integrity_count++;
      }
      if(StringLen(meta.trade_key) == 0){
         if(integrity_count > 0) data_integrity_reasons += ",";
         data_integrity_status = "suspicious";
         data_integrity_reasons += "\"missing_trade_key\"";
         integrity_count++;
      }
      data_integrity_reasons += "]";
      string j = "{";
      j += JsonKVInt("analytics_schema_version", 2) + ",";
      j += JsonKVStr("trade_key", key) + ",";
      j += JsonKVStr("broker_comment", meta.broker_comment) + ",";
      j += JsonKVStr("source_deal_comment", key_hint) + ",";
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
      j += JsonKVNum("realized_r", meta.realized_r, 6) + ",";
      j += JsonKVNum("virtual_balance_base", virtual_balance_base, 2) + ",";
      j += JsonKVNum("result_pct_of_virtual_start", result_pct_virtual, 6) + ",";
      j += JsonKVNum("result_pct_of_virtual_balance", result_pct_virtual, 6) + ",";
      j += "\"partials\":" + partials_json + ",";
      j += JsonKVNum("mfe_price", meta.mfe_price, 8) + ",";
      j += JsonKVNum("mae_price", meta.mae_price, 8) + ",";
      j += JsonKVNum("mfe_r", meta.mfe_r, 6) + ",";
      j += JsonKVNum("mae_r", meta.mae_r, 6) + ",";
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
      j += JsonKVNum("gross_expected_r", meta.gross_expected_r, 6) + ",";
      j += JsonKVNum("net_expected_r", meta.net_expected_r, 6) + ",";
      j += JsonKVNum("expected_value_r", meta.expected_value_r, 6) + ",";
      j += JsonKVNum("ai_score", meta.ai.score, 4) + ",";
      j += JsonKVNum("ai_confidence", meta.ai.confidence, 4) + ",";
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
      _WriteTradeMeta(meta);
      _QueueAnalyticsRefreshJob(meta);
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
         string dedupe = (StringLen(key) > 0 ? key : (symbol + "_" + IntegerToString((int)position_id)));
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
      string rollover_reason = "";
      if(PO3EntryBlockedByRollover(_NowServerOrLocal(), rollover_reason))
         return _RejectPlacement(p, rollover_reason);
      if(!_ExclusiveModelPreExecutionOk(p)) return false;
      if(_ShouldSkipForGlobalOpenPositions()) return _RejectPlacement(p, "another managed position is already open");
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

      double spread_ticks = _CurrentSpreadTicks(p.symbol);
      if(InpMaxSpreadTicks > 0 && spread_ticks > InpMaxSpreadTicks)
         return _RejectPlacement(p, "spread too wide ticks=" + DoubleToString(spread_ticks, 1));
      if(InpMaxSpreadRiskFrac > 0 && (ask - bid) > planned_risk * InpMaxSpreadRiskFrac)
         return _RejectPlacement(p, "spread too large relative to stop distance");

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
         if(rem <= 0.0) return _RejectPlacement(p, "portfolio risk capacity exhausted");
         target_risk = MathMin(target_risk, rem);
      }
      double risk_mult = (p.subtype_risk_multiplier > 0.0 ? p.subtype_risk_multiplier :
                          (m_active_policy.default_risk_multiplier > 0.0 ? m_active_policy.default_risk_multiplier : 1.0));
      if(p.ai.suggested_risk_multiplier > 0.0)
         risk_mult *= MathMin(1.0, p.ai.suggested_risk_multiplier);
      if(p.execution_cost_risk_reduced && p.execution_cost_risk_multiplier > 0.0)
         risk_mult *= MathMin(1.0, p.execution_cost_risk_multiplier);
      double clamped_risk_mult = _ClampRange(risk_mult, 0.0, 1.50);
      if(clamped_risk_mult <= 0.0) return _RejectPlacement(p, "risk_multiplier_resolved_to_zero");
      target_risk *= clamped_risk_mult;

      string trade_key = _MakeTradeKey(p);
      TradePlan identity = p;
      identity.trade_key = trade_key;
      _InitializeNarrativeFields(identity);
      string trade_comment = identity.broker_comment;
      double market_tol_r = MathMax(InpMarketEntryToleranceR, InpEntryZoneToleranceR);
      if(_IsMicroFamily(p)) market_tol_r = MathMax(market_tol_r, InpMicroMarketEntryToleranceR);
      double market_tol = planned_risk * market_tol_r;
      bool market_ok = (adverse_drift <= market_tol);
      if(market_ok){
          TradePlan live = p;
          live.trade_key = trade_key;
          live.broker_comment = trade_comment;
         if(!_BuildPlanPrices(live, entry_px)) return _RejectPlacement(p, "failed to rebuild live plan prices");
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
         double live_rr2 = _ExecutionRR2(live);
         if(!_StopsDistanceOk(p.symbol, p.is_buy, entry_px, live.sl, live.tp2))
            return _RejectPlacement(p, "broker stop/freeze distance invalid for market execution");

         double vol = CalcVolumeForRisk(p.symbol, p.is_buy, entry_px, live.sl, target_risk);
         if(vol <= 0) return _RejectPlacement(p, "volume for risk resolved to zero");
         double new_risk = _RiskMoneyForPosition(p.symbol, p.is_buy, vol, entry_px, live.sl);
         if(new_risk <= 0) return _RejectPlacement(p, "could not evaluate live risk for order");
         string corr_reason = "";
         if(!_CorrelatedExposureOk(live, new_risk, corr_reason))
            return _RejectPlacement(p, corr_reason);
         if(InpMaxTotalRiskEnable && !HasCapacityForNewTrade(new_risk, rem))
            return _RejectPlacement(p, "portfolio risk cap would be exceeded by market order");
         string live_hard_safety_reason = "";
         if(!CanPlaceOrderHardSafety(live, live_hard_safety_reason))
            return _RejectPlacement(p, "final hard safety " + live_hard_safety_reason);

         bool ok = false;
         m_funnel_market_entries_attempted++;
         if(p.is_buy) ok = m_trade.Buy(vol, p.symbol, 0.0, live.sl, live.tp2, trade_comment);
         else         ok = m_trade.Sell(vol, p.symbol, 0.0, live.sl, live.tp2, trade_comment);

         if(!ok) return _RejectPlacement(p, "market order rejected: " + _TradeRetcodeText());
         m_funnel_trades_opened++;
         _RememberConsumedSweep(live);

         live.planned_entry = p.entry_est;
         live.planned_sl = live.sl;
         live.planned_tp1 = live.tp1;
         live.planned_tp2 = live.tp2;
         live.planned_at = _NowServerOrLocal();
         live.initial_volume = vol;
         live.narrative_state = "executed";
         live.tp1_done = false;
         ulong pos_ticket = _FindPositionTicket(p.symbol);
         if(pos_ticket > 0 && PositionSelectByTicket(pos_ticket)){
            live.position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
            live.filled_entry = PositionGetDouble(POSITION_PRICE_OPEN);
            live.filled_at = (datetime)PositionGetInteger(POSITION_TIME);
         } else {
            live.filled_entry = entry_px;
            live.filled_at = _NowServerOrLocal();
         }
         _UpdateAnalyticsSnapshot(live, pos_ticket, (p.is_buy ? bid : ask), vol);
         _WriteTradeMeta(live, pos_ticket);
          _Journal(p.symbol + " market entry placed key=" + trade_key
                   + " comment=" + trade_comment
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
      pending.broker_comment = trade_comment;
      if(!_BuildPlanPrices(pending, pending_entry)) return _RejectPlacement(p, "failed to rebuild pending entry plan prices");
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
      string pending_corr_reason = "";
      if(!_CorrelatedExposureOk(pending, new_risk, pending_corr_reason))
         return _RejectPlacement(p, pending_corr_reason);
      if(InpMaxTotalRiskEnable && !HasCapacityForNewTrade(new_risk, rem))
         return _RejectPlacement(p, "portfolio risk cap would be exceeded by pending order");
      string pending_hard_safety_reason = "";
      if(!CanPlaceOrderHardSafety(pending, pending_hard_safety_reason))
         return _RejectPlacement(p, "final hard safety " + pending_hard_safety_reason);

      datetime expiry = TimeTradeServer();
      if(expiry <= 0) expiry = TimeLocal();
      int expiry_minutes = _PendingExpiryMinutes(pending.htf);
      expiry += expiry_minutes * 60;

      bool ok = false;
      m_funnel_pending_entries_attempted++;
      if(p.is_buy) ok = m_trade.BuyLimit(vol, pending_entry, p.symbol, pending.sl, pending.tp2, ORDER_TIME_SPECIFIED, expiry, trade_comment);
      else         ok = m_trade.SellLimit(vol, pending_entry, p.symbol, pending.sl, pending.tp2, ORDER_TIME_SPECIFIED, expiry, trade_comment);

      if(!ok) return _RejectPlacement(p, "pending order rejected: " + _TradeRetcodeText());
      m_funnel_pending_orders_placed++;

      pending.planned_entry = pending_entry;
      pending.planned_sl = pending.sl;
      pending.planned_tp1 = pending.tp1;
      pending.planned_tp2 = pending.tp2;
      pending.planned_at = _NowServerOrLocal();
      pending.initial_volume = vol;
      pending.narrative_state = "pending_order";
      pending.tp1_done = false;
      pending.bars_waited = 0;
      pending.last_confirm_bar_time = _LastClosedBarTime(pending.symbol, pending.confirm_tf);
      ulong order_ticket = m_trade.ResultOrder();
      _WriteTradeMeta(pending, order_ticket);
      _Journal(p.symbol + " pending limit placed key=" + trade_key
               + " comment=" + trade_comment
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
            if(!have_best || candidate.expected_value_r > best.expected_value_r ||
               (candidate.expected_value_r == best.expected_value_r && candidate.setup_score > best.setup_score)){
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
      best.ai = p.ai;
      best.ai_decision_id = p.ai_decision_id;
      best.ai_decision_source = p.ai_decision_source;
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
               + " ai_reused=" + (StringLen(p.ai_decision_source) > 0 ? p.ai_decision_source : "none"));
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
   CTradeEngine(): m_ai(m_bus), m_state(m_bus), m_penalty(m_bus) {
      m_last_positions_tick = 0;
      m_last_penalty_persist = 0;
      m_last_rollover_log = 0;
      m_policy_loaded_at = 0;
      ZeroMemory(m_active_policy);
      ArrayResize(m_subtype_policy, 0);
      ArrayResize(m_context_policy, 0);
      _ResetSetupFunnel();
   }

   bool Init() {
      MathSrand((int)TimeLocal());
      m_bus = CFileBus(InpBusRoot);
      m_bus.Ensure();
      _EnsureSnapshotDirs();
      if(InpAnalyticsEnable) FolderCreate(_TradeResultsDir(), FILE_COMMON);
      FolderCreate(_PolicyDir(), FILE_COMMON);
      FolderCreate(_AnalyticsDir(), FILE_COMMON);
      FolderCreate(_AnalyticsJobsDir(), FILE_COMMON);
      m_trade.SetExpertMagicNumber(InpMagicNumber);
      m_trade.SetDeviationInPoints(InpMaxSlippagePts);
      _LoadActivePolicyFiles();

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
               _ArchivePendingArtifacts(req_id);
            }
            _Journal("tester init clearing restored pending_ai requests=" + IntegerToString(ArraySize(req_ids))
                     + " candidates=" + IntegerToString(ArraySize(m_pending_ai)));
            ArrayResize(m_pending_ai, 0);
         }
         _PrunePlanArray(m_pending_ai, true);
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
      // persist watchlist/pending
      _FinalizeClosedTrades();
      _PrunePlanArray(m_watchlist, false);
      _PrunePlanArray(m_pending_ai, true);
      m_state.SavePlans(m_state.WatchlistPath(), m_watchlist);
      m_state.SavePlans(m_state.PendingAiPath(), m_pending_ai);
      _PersistPenaltyStates();
   }

   bool EntryFreezeActive(string &reason) {
      return PO3EntryBlockedByRollover(_NowServerOrLocal(), reason);
   }

   void MaintainRolloverProtection() {
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
         if(m_scan_candidates[i].expected_value_r > m_scan_candidates[rep_indexes[idx]].expected_value_r ||
            (m_scan_candidates[i].expected_value_r == m_scan_candidates[rep_indexes[idx]].expected_value_r &&
             m_scan_candidates[i].setup_score > m_scan_candidates[rep_indexes[idx]].setup_score)){
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
            double score = rep.expected_value_r * InpPortfolioSchedulerEvWeight
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
            m_scan_candidates[i].portfolio_score = m_scan_candidates[i].expected_value_r;
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

      bool added = false;
      for(int i=0; i<ArraySize(fvg_cands); i++){
         for(int b=0; b<ArraySize(branches); b++){
            TradePlan p;
            ZeroMemory(p);
            string reject_reason = "";
            if(!_TryBuildCandidateFromBranch(base, fvg_cands[i], branches[b], p, reject_reason)){
               if(reject_reason == "exclusive_breaker_retest_virgin_strong_origin_only")
                  continue;
               string reject_stage = "branch";
               if(StringFind(reject_reason, "target_") >= 0 || StringFind(reject_reason, "liquidity_target") >= 0 ||
                  StringFind(reject_reason, "synthetic_target") >= 0 || StringFind(reject_reason, "structural_stop") >= 0 ||
                  StringFind(reject_reason, "fvg_too_small") >= 0 || StringFind(reject_reason, "broker_distance") >= 0)
                  reject_stage = "plan_price";
               else if(StringFind(reject_reason, "cost") >= 0 || StringFind(reject_reason, "net_expected") >= 0)
                  reject_stage = "edge";
               _LogSetupReject(base.symbol, reject_stage, reject_reason,
                               "flow=" + flow_name
                               + " candidate=" + IntegerToString(i)
                               + " branch=" + branches[b]);
               continue;
            }
            p.trade_key = _MakeTradeKey(p);
            _QueueScanCandidate(p);
            m_funnel_branch_candidates++;
            added = true;
            _Journal(base.symbol + " " + flow_name + " accepted"
                     + " setup_family=" + p.setup_family
                     + " branch=" + p.entry_branch
                     + " setup_score=" + DoubleToString(p.setup_score, 2)
                     + " rr2=" + DoubleToString(_ExecutionRR2(p), 2)
                     + " net_expected_r=" + DoubleToString(p.net_expected_r, 3)
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
            ulong oldest_wall_request = 0;
            string group_symbol = "";
            int candidate_count = 1;
            for(int i=0; i<ArraySize(m_pending_ai); i++){
               if(m_pending_ai[i].req_id != req_ids[r]) continue;
               if(StringLen(group_symbol) == 0) group_symbol = m_pending_ai[i].symbol;
               candidate_count = MathMax(candidate_count, m_pending_ai[i].candidate_count);
               if(oldest_request <= 0 || m_pending_ai[i].ai_requested_at < oldest_request)
                  oldest_request = m_pending_ai[i].ai_requested_at;
               if(m_pending_ai[i].ai_requested_wall_ms > 0 && (oldest_wall_request == 0 || m_pending_ai[i].ai_requested_wall_ms < oldest_wall_request))
                  oldest_wall_request = m_pending_ai[i].ai_requested_wall_ms;
            }
            string req_rel = _ReqPath(req_ids[r]);
            string resp_rel = _RespPath(req_ids[r]);
            string group_signature = _PendingGroupSignature(req_ids[r], group_symbol);

            AiDecision dec;
            bool has_response = _PathExists(resp_rel);
            if(has_response){
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
                     _FallbackPendingGroup(req_ids[r], "invalid_json");
                  } else {
                     _Journal(group_symbol + " AI response unreadable req_id=" + req_ids[r]
                              + " -> dropped "
                              + (InpAllowRuleOnlyFallback ? "strict mode" : "rule-only fallback disabled"));
                     _ArchivePendingArtifacts(req_ids[r]);
                     _RemovePendingGroup(req_ids[r]);
                  }
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
               if(MQLInfoInteger(MQL_TESTER) && !request_still_pending && oldest_wall_request > 0 && _WallElapsedMs(oldest_wall_request) > 15000){
                  _RememberAiCooldown(group_symbol, group_signature, "request_missing");
                  _Journal(group_symbol + " AI request missing req_id=" + req_ids[r] + " -> dropped");
                  _ArchivePendingArtifacts(req_ids[r]);
                  _RemovePendingGroup(req_ids[r]);
                  continue;
               }
               if(timed_out){
                  bool allow_fallback = (!InpAiStrict && InpAllowRuleOnlyFallback);
                  _RememberAiCooldown(group_symbol, group_signature, "timeout");
                  if(allow_fallback) _FallbackPendingGroup(req_ids[r], "timeout");
                  else {
                     _Journal(group_symbol + " AI timeout req_id=" + req_ids[r]
                              + " response=missing request=" + (request_still_pending ? "present" : "missing")
                              + " -> dropped "
                              + (InpAllowRuleOnlyFallback ? "strict mode" : "rule-only fallback disabled"));
                     _ArchivePendingArtifacts(req_ids[r]);
                     _RemovePendingGroup(req_ids[r]);
                  }
                  continue;
               }
               continue;
            }

         TradePlan decision_group[];
         ArrayResize(decision_group, 0);
         for(int i=0; i<ArraySize(m_pending_ai); i++){
            if(m_pending_ai[i].req_id != req_ids[r]) continue;
            int n = ArraySize(decision_group);
            ArrayResize(decision_group, n + 1);
            decision_group[n] = m_pending_ai[i];
         }
         if(ArraySize(decision_group) > 0)
            _RememberTesterAiDecision(_TesterAiCacheSignature(decision_group), dec);

         int chosen = dec.chosen_index;
         if(chosen < 0 || chosen >= candidate_count) chosen = 0;
         bool hard_veto = _AiHardVeto(dec);

         TradePlan rule_best;
         bool have_rule_best = false;
         TradePlan ai_selected;
         bool have_ai_selected = false;
         string selected_reason = "";
         for(int i=0; i<ArraySize(m_pending_ai); i++){
            if(m_pending_ai[i].req_id != req_ids[r]) continue;
            m_pending_ai[i].ai = dec;
            if(StringLen(m_pending_ai[i].ai_decision_source) == 0)
               m_pending_ai[i].ai_decision_source = (StringLen(dec.decision_source) > 0 ? dec.decision_source : "llm_blended");

            string rule_reason = "";
            bool rule_pass = _DeterministicExecutionGate(m_pending_ai[i], rule_reason, false);
            if(rule_pass && (!have_rule_best ||
               m_pending_ai[i].expected_value_r > rule_best.expected_value_r ||
               (m_pending_ai[i].expected_value_r == rule_best.expected_value_r &&
                m_pending_ai[i].setup_score > rule_best.setup_score))){
               rule_best = m_pending_ai[i];
               have_rule_best = true;
               selected_reason = rule_reason;
            }
            if(m_pending_ai[i].candidate_index == chosen){
               ai_selected = m_pending_ai[i];
               have_ai_selected = rule_pass;
            }
         }

         TradePlan selected;
         bool have_selected = false;
         if(have_ai_selected){
            selected = ai_selected;
            have_selected = true;
         } else if(have_rule_best){
            selected = rule_best;
            have_selected = true;
         }

         double required_ai_score = (have_selected ? _RequiredAiScore(selected) : InpMinAiScoreTrend);
         double required_ai_confidence = (have_selected ? _RequiredAiConfidence(selected) : InpMinAiConfidence);
         bool ai_conf_ok = (dec.confidence >= required_ai_confidence);
         bool ai_score_ok = (dec.score >= required_ai_score);
         bool raw_ai_ok = (dec.allow || (!InpAiRequireRawAllow &&
                           dec.score >= MathMax(InpAiOverrideScore, required_ai_score + 0.25) &&
                           dec.confidence >= MathMax(InpAiOverrideConfidence, required_ai_confidence - 0.10)));
         string ai_threshold_reason = "ok";
         if(!ai_conf_ok) ai_threshold_reason = "ai_confidence_below_threshold";
         else if(!ai_score_ok) ai_threshold_reason = "ai_score_below_threshold";
         else if(!raw_ai_ok) ai_threshold_reason = "ai_raw_allow_false";

         bool allow = (have_selected && !hard_veto && ai_conf_ok && ai_score_ok && raw_ai_ok);
         if(have_selected && !hard_veto && !allow && !InpAiStrict && InpAllowRuleOnlyFallback){
            TradePlan fallback_selected = selected;
            if(_ApplyRuleFallback(fallback_selected, ai_threshold_reason)){
               selected = fallback_selected;
               allow = true;
            }
         }
         _Journal(group_symbol + " AI advisory req_id=" + req_ids[r]
                  + " raw_allow=" + (dec.allow ? "true" : "false")
                  + " hard_veto=" + (hard_veto ? "true" : "false")
                  + " threshold_reason=" + ai_threshold_reason
                  + " final_allow=" + (allow ? "true" : "false")
                  + " score=" + DoubleToString(dec.score, 2)
                  + " required_score=" + DoubleToString(required_ai_score, 2)
                  + " conf=" + DoubleToString(dec.confidence, 2)
                  + " required_conf=" + DoubleToString(required_ai_confidence, 2)
                  + " chosen=" + IntegerToString(chosen)
                  + " decision_source=" + dec.decision_source);

         if(allow){
            selected.req_id = "";
            selected.ai_requested_at = 0;
            selected.ai_requested_wall_ms = 0;
            selected.last_confirm_bar_time = _LastClosedBarTime(selected.symbol, selected.confirm_tf);
            selected.ai_decision_id = dec.decision_id;
            if(StringLen(selected.ai_decision_source) == 0){
               if(StringLen(dec.decision_source) > 0) selected.ai_decision_source = dec.decision_source;
               else selected.ai_decision_source = "llm_blended";
            }
            if(!dec.allow && selected.ai_decision_source == "llm_blended") selected.ai_decision_source = "score_override";
            _Journal(selected.symbol + " deterministic gate approved candidate=" + IntegerToString(selected.candidate_index)
                     + " setup_score=" + DoubleToString(selected.setup_score, 2)
                     + " source=" + selected.ai_decision_source);
            _AddToWatchlist(selected);
         } else {
            string reject_reason = (hard_veto ? "llm_veto" : "no_rule_candidate");
            _RememberAiCooldown(group_symbol, group_signature, reject_reason);
            _LogSetupReject(group_symbol, "ai", reject_reason,
                            "score=" + DoubleToString(dec.score, 2)
                            + " required_score=" + DoubleToString(required_ai_score, 2)
                            + " confidence=" + DoubleToString(dec.confidence, 2)
                            + " required_confidence=" + DoubleToString(required_ai_confidence, 2)
                            + " hard_veto=" + IntegerToString(hard_veto ? 1 : 0));
            _Journal(group_symbol + " AI/rule reject req_id=" + req_ids[r] + " reasons=" + dec.reasons_json
                     + " rule_reason=" + selected_reason + " reject=" + reject_reason);
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
         bool needs_live_sequence = (!p.po3.has_bos || p.po3.developing_bos || p.po3.sweep_running ||
                                     watch_state == PO3_DEVELOPING || watch_state == PO3_SWEEP_CONFIRMED ||
                                     watch_state == PO3_DISPLACEMENT_CONFIRMED);
         bool tier_b_execution_allowed = (!InpRequireConfirmedPO3ForExecution &&
                                          p.po3.context_tier == "B" &&
                                          p.po3.has_sweep &&
                                          p.po3.has_displacement &&
                                          !p.po3.sweep_running);
         if(ok && (InpRequireConfirmedPO3ForExecution || (needs_live_sequence && !tier_b_execution_allowed)) && watch_state != PO3_CONFIRMED){
            PO3Context live_confirm_po3;
            bool confirmed_now = false;
            if(m_po3.Build(p.symbol, p.htf, live_confirm_po3)){
               PO3State live_state = (live_confirm_po3.state != PO3_IDLE ? live_confirm_po3.state : PO3StateFromString(live_confirm_po3.po3_state));
               confirmed_now = ((live_state == PO3_STRUCTURE_CONFIRMED || live_state == PO3_FVG_CONFIRMED ||
                                 live_state == PO3_ENTRY_WAITING || live_state == PO3_CONFIRMED) &&
                                live_confirm_po3.t_sweep == p.po3.t_sweep &&
                                live_confirm_po3.has_displacement &&
                                live_confirm_po3.t_disp > live_confirm_po3.t_sweep &&
                                live_confirm_po3.has_bos &&
                                live_confirm_po3.t_bos > live_confirm_po3.t_disp &&
                                (!InpRequireFvgAfterDisp || p.fvg.t_form > live_confirm_po3.t_disp));
               if(confirmed_now){
                  p.po3 = live_confirm_po3;
                  PO3SetState(p.po3, PO3_CONFIRMED, "entry_retrace_confirmed");
                  p.narrative_state = "armed_confirmed";
                  _Journal(p.symbol + " watchlist PO3 promoted to CONFIRMED before execution");
               }
            }
            if(!confirmed_now){
               ok = false;
               if(new_confirm_bar)
                  _Journal(p.symbol + " execution held: PO3 state=" + p.po3.po3_state + " awaiting closed BOS/displacement sequence");
            }
         }

         if(ok){
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

   void MaintainPositions() {
      // TP1 partial + BE; penalty watcher tick.
      if(m_last_positions_tick == TimeLocal()) return;
      m_last_positions_tick = TimeLocal();

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
         if(!_SymbolEligible(sym) || !_LoadTradeMeta(ticket, sym, comment, meta)) continue;

          bool pending_fill_detected = (meta.narrative_state == "pending_order" || meta.narrative_state == "pending_order_recovered");
          double px = SymbolInfoDouble(sym, is_buy ? SYMBOL_BID : SYMBOL_ASK);
          _UpdateAnalyticsSnapshot(meta, ticket, px, vol);
          if(pending_fill_detected){
             if(meta.filled_entry <= 0) meta.filled_entry = entry;
             if(meta.filled_at <= 0) meta.filled_at = opened;
             meta.narrative_state = "executed";
             m_funnel_orders_filled++;
             m_funnel_trades_opened++;
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
                double vol_cut = _ClampVolToStep(sym, vol * partial_pct);
                double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
                if(vol_cut >= vmin && vol - vol_cut >= vmin){
                   partial_ok = m_trade.PositionClosePartial(ticket, vol_cut);
                }
             }
             if(partial_ok){
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
                   _Journal(sym + " breakeven time-gated ticket=" + IntegerToString((int)ticket)
                            + " minutes_open=" + IntegerToString(minutes_open)
                            + " mfe_r=" + DoubleToString(meta.mfe_r, 2)
                            + " trigger_r=" + DoubleToString(be_trigger_r, 2)
                            + " ok=" + (be_ok ? "true" : "false"));
                }
             }
          }
          _WriteTradeMeta(meta, ticket);
       }

      // Penalties
      m_penalty.Tick(m_trade);
      if(m_last_penalty_persist == 0 || (TimeLocal() - m_last_penalty_persist) >= 15){
         _PersistPenaltyStates();
      }
      _FinalizeClosedTrades();
   }

   void Persist() {
      _PrunePlanArray(m_watchlist, false);
      _PrunePlanArray(m_pending_ai, true);
      m_state.SavePlans(m_state.WatchlistPath(), m_watchlist);
      m_state.SavePlans(m_state.PendingAiPath(), m_pending_ai);
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

   bool HasActiveSymbolState(const string symbol) {
      return _SymbolBusy(symbol);
   }
};

#endif
