//+------------------------------------------------------------------+
//| PenaltyWatcher.mqh - degradation cuts and closes                   |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_PENALTYWATCHER_MQH__
#define __PO3_AIGATE_PENALTYWATCHER_MQH__
#include "Config.mqh"
#include "Types.mqh"
#include "FileBus.mqh"
#include "JsonLite.mqh"
#include "Risk.mqh"
#include "Indicators.mqh"

//--- Pure management-AI review timing.  All instants are absolute server/tester
//--- seconds, so a state restored after a restart keeps exactly the remaining
//--- cooldown.  The duration is the EA input InpPenaltyCooldownMin -- never the
//--- model's diagnostic recommended_wait_minutes.
long ManagementAiCooldownUntil(const long decided_at, const int cooldown_minutes) {
   int minutes = cooldown_minutes;
   if(minutes < 1) minutes = 1;
   return decided_at + minutes * 60;
}

bool ManagementAiCooldownActive(const long now, const long cooldown_until) {
   if(cooldown_until <= 0) return false;
   return (now < cooldown_until);
}

bool ManagementAiReviewTimedOut(const long now, const long requested_at, const int timeout_seconds) {
   int timeout = timeout_seconds;
   if(timeout < 10) timeout = 10;
   if(requested_at <= 0) return true;
   return (now - requested_at > timeout);
}

class CPenaltyWatcher {
private:
   CFileBus *m_bus;
   PenaltyState m_states[];
   string m_invalidation_policy_json;
   datetime m_invalidation_policy_loaded_at;
   bool m_immediate_persist_requested;

   bool _ParseInvalidationMode(const string value,
                               ENUM_INVALIDATION_CONFIRMATION_MODE &mode) const {
      string normalized = value;
      StringToUpper(normalized);
      if(normalized == "CLOSED_M1_BAR") mode = INVALIDATION_CLOSED_M1_BAR;
      else if(normalized == "CLOSED_ENTRY_TF_BAR") mode = INVALIDATION_CLOSED_ENTRY_TF_BAR;
      else if(normalized == "N_SECOND_PERSISTENCE") mode = INVALIDATION_N_SECOND_PERSISTENCE;
      else if(normalized == "PRICE_SPREAD_BUFFER") mode = INVALIDATION_PRICE_SPREAD_BUFFER;
      else if(normalized == "TICK") mode = INVALIDATION_TICK;
      else return false;
      return true;
   }

   bool _ResolveInvalidationPolicy(const TradePlan &meta,
                                   ENUM_INVALIDATION_CONFIRMATION_MODE &mode,
                                   int &persistence_seconds,
                                   double &spread_buffer_multiple,
                                   string &policy_source,
                                   string &policy_version) {
      mode = InpInvalidationConfirmationMode;
      persistence_seconds = MathMax(1, InpInvalidationPersistenceSeconds);
      spread_buffer_multiple = MathMax(0.0, InpInvalidationSpreadBufferMult);
      policy_source = "configured_global_inputs";
      policy_version = "global_inputs";
      if(!InpInvalidationAssetClassPolicyEnable) return true;

      datetime now = TimeLocal();
      if(StringLen(m_invalidation_policy_json) == 0 || m_invalidation_policy_loaded_at <= 0 ||
         now - m_invalidation_policy_loaded_at >= 60){
         int h = FileOpen(InpInvalidationAssetClassPolicyFile,
                          FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
         m_invalidation_policy_json = "";
         if(h != INVALID_HANDLE){
            while(!FileIsEnding(h)) m_invalidation_policy_json += FileReadString(h);
            FileClose(h);
         }
         m_invalidation_policy_loaded_at = now;
      }
      if(StringLen(m_invalidation_policy_json) == 0 ||
         JsonGetString(m_invalidation_policy_json, "schema_version", "") != INVALIDATION_POLICY_SCHEMA_VERSION)
         return false;
      string asset_class = meta.asset_class;
      if(StringLen(asset_class) == 0) asset_class = _RiskAssetClassForSymbol(meta.symbol);
      StringToLower(asset_class);
      string classes = JsonGetObject(m_invalidation_policy_json, "asset_classes", "");
      string selected = JsonGetObject(classes, asset_class, "");
      if(StringLen(selected) == 0) return false;
      ENUM_INVALIDATION_CONFIRMATION_MODE resolved_mode;
      if(!_ParseInvalidationMode(JsonGetString(selected, "mode", ""), resolved_mode)) return false;
      int resolved_persistence = (int)JsonGetNumber(selected, "persistence_seconds", persistence_seconds);
      double resolved_buffer = JsonGetNumber(selected, "spread_buffer_multiple", spread_buffer_multiple);
      if(resolved_persistence < 1 || !MathIsValidNumber(resolved_buffer) || resolved_buffer < 0.0) return false;
      mode = resolved_mode;
      persistence_seconds = resolved_persistence;
      spread_buffer_multiple = resolved_buffer;
      policy_source = "versioned_asset_class_policy:" + asset_class;
      policy_version = JsonGetString(m_invalidation_policy_json, "policy_version", INVALIDATION_POLICY_SCHEMA_VERSION);
      return true;
   }

   datetime _NowServerOrTester() const {
      datetime now = 0;
      if(MQLInfoInteger(MQL_TESTER)) now = TimeCurrent();
      if(now <= 0) now = TimeTradeServer();
      if(now <= 0) now = TimeCurrent();
      if(now <= 0) now = TimeLocal();
      return now;
   }

   bool _InCooldown(const PenaltyState &st){
      if(st.last_reduction_at == 0) return false;
      return (_NowServerOrTester() < (st.last_reduction_at + InpPenaltyCooldownMin*60));
   }

   //--- Management AI review: second approval layer for a frozen deterministic
   //--- broker action.  The watcher stays the first authority; the review can
   //--- only say APPROVE (execute that exact action) or DENY (execute nothing).
   bool _ManagementAiReviewRequired() const {
      if(!InpPenaltyAiReviewEnable) return false;
      if(MQLInfoInteger(MQL_TESTER) != 0 && !InpPenaltyAiReviewInTester) return false;
      return true;
   }

   string _ManagementAiRequestPath(const string request_id) const {
      return m_bus.ManagementReviewRequestDir() + "\\" + request_id + ".json";
   }

   string _ManagementAiResponsePath(const string request_id) const {
      return m_bus.ManagementReviewResponseDir() + "\\" + request_id + ".json";
   }

   void _LogManagementAiReview(const PenaltyState &st, const string stage, const string detail) const {
      _RiskLog("[management_ai_review] stage=" + stage
               + " position_id=" + IntegerToString(st.position_identifier)
               + " symbol=" + st.symbol
               + " action_id=" + st.action_id
               + " requested_action=" + st.requested_action
               + " requested_cut_fraction=" + DoubleToString(st.requested_cut_fraction, 4)
               + " strikes=" + IntegerToString(st.strikes)
               + " request_id=" + st.management_ai_request_id
               + " review_seq=" + IntegerToString(st.management_ai_review_seq)
               + " verdict=" + st.management_ai_verdict
               + " cooldown_until=" + TimeToString(st.management_ai_cooldown_until, TIME_DATE|TIME_SECONDS)
               + " " + detail);
   }

   bool _ManagementAiRequestBound(const PenaltyState &st) const {
      return (StringLen(st.management_ai_request_id) > 0 &&
              StringLen(st.action_id) > 0 &&
              st.management_ai_action_id == st.action_id &&
              st.management_ai_requested_action == st.requested_action &&
              MathAbs(st.management_ai_requested_cut_fraction - st.requested_cut_fraction) <= 0.00000001);
   }

   bool _ManagementAiApprovalBound(const PenaltyState &st) const {
      return (_ManagementAiRequestBound(st) && st.management_ai_verdict == "APPROVE");
   }

   void _ClearManagementAiBinding(PenaltyState &st) {
      st.management_ai_request_id = "";
      st.management_ai_action_id = "";
      st.management_ai_request_fingerprint = "";
      st.management_ai_requested_action = "";
      st.management_ai_requested_cut_fraction = 0.0;
      st.management_ai_verdict = "";
      st.management_ai_reason_codes = "";
      st.management_ai_reason = "";
      st.management_ai_status_reason = "";
      st.management_ai_requested_at = 0;
      st.management_ai_resolved_at = 0;
      st.management_ai_provider = "";
      st.management_ai_model = "";
      st.management_ai_response_fingerprint = "";
   }

   // A proposal that stops being pending before its verdict arrives withdraws
   // its request if the gate has not claimed it yet, so no call is spent on it.
   void _AbandonManagementAiRequest(const PenaltyState &st, const string reason) {
      if(StringLen(st.management_ai_request_id) == 0) return;
      string request_path = _ManagementAiRequestPath(st.management_ai_request_id);
      bool removed = (m_bus.Exists(request_path) && m_bus.Delete(request_path));
      string response_path = _ManagementAiResponsePath(st.management_ai_request_id);
      if(m_bus.Exists(response_path)) m_bus.Delete(response_path);
      _LogManagementAiReview(st, "request_abandoned",
                             "reason=" + reason + " unclaimed_request_removed=" + (removed ? "true" : "false"));
   }

   double _CurPrice(const string symbol, const bool is_buy){
      double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
      return is_buy ? bid : ask;
   }

   int _FindState(const long position_identifier, const string symbol){
      for(int i=0; i<ArraySize(m_states); i++){
         if(m_states[i].position_identifier == position_identifier &&
            m_states[i].symbol == symbol) return i;
      }
      return -1;
   }

   void _SaveState(const PenaltyState &st){
      if(st.position_identifier <= 0) return;
      int idx = _FindState(st.position_identifier, st.symbol);
      if(idx < 0){
         idx = ArraySize(m_states);
         ArrayResize(m_states, idx+1);
      }
      m_states[idx] = st;
   }

   void _RemoveStateAt(const int idx){
      int last = ArraySize(m_states) - 1;
      if(idx < 0 || idx > last) return;
      if(idx != last) m_states[idx] = m_states[last];
      ArrayResize(m_states, last);
   }

   double _ScaledCutPct(const double base_cut_pct, const int strikes) const {
      double out = base_cut_pct;
      if(InpPenaltyCutStrikes > 0 && strikes >= InpPenaltyCutStrikes){
         double mult = 1.0 + 0.50 * (strikes - InpPenaltyCutStrikes + 1);
         out = MathMin(1.0, base_cut_pct * mult);
      }
      return out;
   }

   bool _PositionIdentifierStillOpen(const long position_identifier,
                                     const long &live_position_identifiers[]){
      for(int i=0; i<ArraySize(live_position_identifiers); i++){
         if(live_position_identifiers[i] == position_identifier) return true;
      }
      return false;
   }

   double _Clamp(const double v, const double lo, const double hi) const {
      return MathMax(lo, MathMin(hi, v));
   }

   double _RegimeScale(const string symbol) const {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, PO3EffectiveEntryTF(), 0, 100, rates);
      if(got < 30) return 1.0;
      double atr = ATRFromRates(rates, got, 14);
      double close_px = rates[1].close;
      if(atr <= 0 || close_px <= 0) return 1.0;
      double atr_pct = atr / close_px;
      double baseline = MathMax(InpAtrMinPct * 2.5, 0.0015);
      double scale = atr_pct / baseline;
      double spread = SymbolInfoDouble(symbol, SYMBOL_ASK) - SymbolInfoDouble(symbol, SYMBOL_BID);
      if(spread > 0 && atr > 0){
         double spread_ratio = spread / atr;
         if(spread_ratio > 0.20) scale *= 0.90;
         else if(spread_ratio < 0.08) scale *= 1.05;
      }
      return _Clamp(scale, 0.75, 1.50);
   }

   double _MaeTriggerR(const string symbol) const {
      double scale = _RegimeScale(symbol);
      return InpPenaltyMaeTriggerR * (0.85 + 0.30 * scale);
   }

   int _StuckMinutes(const string symbol) const {
      double scale = _RegimeScale(symbol);
      double adjusted = (double)InpPenaltyStuckMinutes * (1.25 - 0.20 * scale);
      return MathMax(10, (int)MathRound(adjusted));
   }

   double _StuckMinMfeR(const string symbol) const {
      double scale = _RegimeScale(symbol);
      return InpPenaltyStuckMinMfeR * (0.80 + 0.20 * scale);
   }

   double _GivebackTrigMfeR(const string symbol) const {
      double scale = _RegimeScale(symbol);
      return InpPenaltyGivebackTrigMfeR * (0.85 + 0.20 * scale);
   }

   double _GivebackFloorR(const string symbol) const {
      double scale = _RegimeScale(symbol);
      return InpPenaltyGivebackFloorR * (1.15 - 0.15 * scale);
   }

   string _TradeKeyPath(const string key){
      return m_bus.LogDir() + "\\trade_key_" + key + ".json";
   }

   string _TradeTicketPath(const ulong ticket){
      return m_bus.LogDir() + "\\trade_ticket_" + IntegerToString((int)ticket) + ".json";
   }

   bool _ParseTradeMeta(const string txt, TradePlan &p){
      ZeroMemory(p);
      p.trade_key = JsonGetString(txt, "trade_key", "");
      p.symbol = JsonGetString(txt, "symbol", "");
      if(StringLen(p.symbol) == 0) return false;
      p.is_buy = JsonGetBool(txt, "is_buy", true);
      p.sl = JsonGetNumber(txt, "sl", 0);
      p.entry_est = JsonGetNumber(txt, "entry", 0);
      p.tp1 = JsonGetNumber(txt, "tp1", 0);
      p.tp2 = JsonGetNumber(txt, "tp2", 0);
      p.fvg.lower = JsonGetNumber(txt, "fvg_lower", 0);
      p.fvg.upper = JsonGetNumber(txt, "fvg_upper", 0);
      p.fvg.mid = JsonGetNumber(txt, "fvg_mid", 0);
      p.fvg.entry_invalid = JsonGetBool(txt, "fvg_entry_invalid", false);
      p.fvg.structure_invalidated = JsonGetBool(txt, "fvg_structure_invalidated", false);
      p.fvg.mitigation_state = JsonGetString(txt, "fvg_mitigation_state", "");
      p.fvg.invalidation_reason = JsonGetString(txt, "fvg_invalidation_reason", "");
      p.po3.po3_state = JsonGetString(txt, "po3_state", "");
      p.po3.state = (PO3State)(int)JsonGetNumber(txt, "po3_state_id", (double)(int)PO3StateFromString(p.po3.po3_state));
      p.po3.po3_state = PO3StateToString(p.po3.state);
      p.po3.structure_type = JsonGetString(txt, "structure_type", "");
      p.po3.dr_low = JsonGetNumber(txt, "dr_low", 0);
      p.po3.dr_high = JsonGetNumber(txt, "dr_high", 0);
      p.po3.manip_low = JsonGetNumber(txt, "manip_low", 0);
      p.po3.manip_high = JsonGetNumber(txt, "manip_high", 0);
      p.po3.swing_low = JsonGetNumber(txt, "swing_low", 0);
      p.po3.swing_high = JsonGetNumber(txt, "swing_high", 0);
      p.penalty_mae_trigger_r = JsonGetNumber(txt, "penalty_mae_trigger_r", 0);
      p.penalty_giveback_trigger_r = JsonGetNumber(txt, "penalty_giveback_trigger_r", 0);
      p.penalty_giveback_floor_r = JsonGetNumber(txt, "penalty_giveback_floor_r", 0);
      p.penalty_stuck_minutes = (int)JsonGetNumber(txt, "penalty_stuck_minutes", 0);
      p.penalty_stuck_min_mfe_r = JsonGetNumber(txt, "penalty_stuck_min_mfe_r", 0);
      p.penalty_dr_invalid_cut_pct = JsonGetNumber(txt, "penalty_dr_invalid_cut_pct", 0);
      p.penalty_fvg_invalid_cut_pct = JsonGetNumber(txt, "penalty_fvg_invalid_cut_pct", 0);
      p.penalty_close_strikes = (int)JsonGetNumber(txt, "penalty_close_strikes", 0);
      p.broker_comment = JsonGetString(txt, "broker_comment", "");
      p.result_order_ticket = (ulong)JsonGetNumber(txt, "result_order_ticket", 0);
      p.result_deal_ticket = (ulong)JsonGetNumber(txt, "result_deal_ticket", 0);
      p.broker_position_ticket = (ulong)JsonGetNumber(txt, "broker_position_ticket", 0);
      p.broker_position_identifier = (long)JsonGetNumber(txt, "broker_position_identifier", 0);
      p.position_id = (long)JsonGetNumber(txt, "position_id", 0);
      p.execution_identity_verified = JsonGetBool(txt, "execution_identity_verified", false);
      p.execution_identity_quarantined = JsonGetBool(txt, "execution_identity_quarantined", false);
      return true;
   }

   bool _MetaMatchesPosition(const TradePlan &p,
                             const ulong ticket,
                             const string symbol,
                             const string comment) const {
      if(!p.execution_identity_verified || p.execution_identity_quarantined) return false;
      if(p.symbol != symbol || p.broker_position_ticket != ticket) return false;
      if(StringLen(comment) > 0 && p.broker_comment != comment && p.trade_key != comment) return false;
      return (p.broker_position_identifier > 0 && p.position_id == p.broker_position_identifier);
   }

   bool _LoadTradeMeta(const ulong ticket, const string symbol, const string comment, TradePlan &p){
      string txt;
      if(ticket > 0 && m_bus.ReadText(_TradeTicketPath(ticket), txt) &&
         _ParseTradeMeta(txt, p) && _MetaMatchesPosition(p, ticket, symbol, comment)) return true;
      if(StringLen(comment) > 0 && m_bus.ReadText(_TradeKeyPath(comment), txt) &&
         _ParseTradeMeta(txt, p) && _MetaMatchesPosition(p, ticket, symbol, comment)) return true;
      return false;
    }

   string _ConfirmationModeName(const ENUM_INVALIDATION_CONFIRMATION_MODE mode) const {
      if(mode == INVALIDATION_CLOSED_M1_BAR) return "closed_m1_bar";
      if(mode == INVALIDATION_CLOSED_ENTRY_TF_BAR) return "closed_entry_tf_bar";
      if(mode == INVALIDATION_N_SECOND_PERSISTENCE) return "n_second_persistence";
      if(mode == INVALIDATION_PRICE_SPREAD_BUFFER) return "price_spread_buffer";
      return "tick";
   }

   bool _ActionAlreadyExecuted(const PenaltyState &st, const string action_id) const {
      if(StringLen(action_id) == 0 || StringLen(st.executed_action_ids) == 0) return false;
      return (StringFind("|" + st.executed_action_ids + "|", "|" + action_id + "|") >= 0);
   }

   void _RememberExecutedAction(PenaltyState &st, const string action_id) {
      if(StringLen(action_id) == 0 || _ActionAlreadyExecuted(st, action_id)) return;
      if(StringLen(st.executed_action_ids) > 0) st.executed_action_ids += "|";
      st.executed_action_ids += action_id;
   }

   bool _ActionLifecyclePending(const PenaltyState &st) const {
      return (st.action_lifecycle_state == "DETECTED" ||
              st.action_lifecycle_state == "AI_REVIEW_PENDING" ||
              st.action_lifecycle_state == "AI_APPROVED" ||
              st.action_lifecycle_state == "ELIGIBLE" ||
              st.action_lifecycle_state == "DEFERRED_COOLDOWN" ||
              st.action_lifecycle_state == "SUBMISSION_ATTEMPTED" ||
              st.action_lifecycle_state == "FAILED_RETRYABLE");
   }

   int _ActionStrength(const string action) const {
      if(action == "FULL_CLOSE") return 2;
      if(action == "PARTIAL_CLOSE") return 1;
      return 0;
   }

   bool _ManagementRetcodeAccepted(const uint retcode) const {
      return (retcode == TRADE_RETCODE_DONE ||
              retcode == TRADE_RETCODE_DONE_PARTIAL ||
              retcode == TRADE_RETCODE_PLACED);
   }

   bool _ManagementFailureTerminal(const uint retcode) const {
      return (retcode == TRADE_RETCODE_INVALID ||
              retcode == TRADE_RETCODE_INVALID_ORDER ||
              retcode == TRADE_RETCODE_ONLY_REAL);
   }

   void _SetActionLifecycle(PenaltyState &st,
                            const string next_state,
                            const string reason) {
      string previous = st.action_lifecycle_state;
      if(previous == "AI_REVIEW_PENDING" && (next_state == "SUPERSEDED" || next_state == "FAILED_TERMINAL"))
         _AbandonManagementAiRequest(st, reason);
      st.action_lifecycle_state = next_state;
      if(next_state == "FAILED_TERMINAL" || next_state == "SUPERSEDED")
         st.action_terminal_reason = reason;
      _RiskLog("[management_action_lifecycle] action_id=" + st.action_id
               + " position_id=" + IntegerToString(st.position_identifier)
               + " position_ticket=" + IntegerToString((long)st.position_ticket)
               + " requested_action=" + st.requested_action
               + " requested_volume=" + DoubleToString(st.requested_volume, 8)
               + " normalized_volume=" + DoubleToString(st.normalized_volume, 8)
               + " previous_state=" + previous
               + " next_state=" + next_state
               + " broker_retcode=" + IntegerToString((int)st.action_last_retcode)
               + " broker_retcode_description=" + st.action_last_retcode_description
               + " retry_count=" + IntegerToString(st.action_retry_count)
               + " next_eligible_retry_time=" + TimeToString(st.next_eligible_retry_time, TIME_DATE|TIME_SECONDS)
               + " reason=" + reason);
   }

   void _QueueManagementAction(PenaltyState &st,
                               const string action_id,
                               const string requested_action,
                               const double cut_fraction,
                               const string reason) {
      if(_ActionLifecyclePending(st) && st.action_id == action_id &&
         st.requested_action == requested_action) return;
      if(_ActionLifecyclePending(st))
         _SetActionLifecycle(st, "SUPERSEDED", "stronger_or_newer_management_action:" + reason);
      st.action_id = action_id;
      st.requested_action = requested_action;
      st.requested_cut_fraction = _Clamp(cut_fraction, 0.0, 1.0);
      st.requested_volume = 0.0;
      st.normalized_volume = 0.0;
      st.action_position_volume_before = 0.0;
      st.action_retry_count = 0;
      st.next_eligible_retry_time = 0;
      st.action_last_attempt_at = 0;
      st.action_last_retcode = 0;
      st.action_last_retcode_description = "";
      st.action_terminal_reason = "";
      st.action_executed = "none";
      _SetActionLifecycle(st, "DETECTED", reason);
      if(_ManagementAiReviewRequired() && requested_action != "NO_BROKER_ACTION"){
         // The proposal is frozen here: action, cut fraction and identity can no
         // longer change for this action_id.  Exactly one review decides it.
         _ClearManagementAiBinding(st);
         _SetActionLifecycle(st, "AI_REVIEW_PENDING", "deterministic_proposal_frozen_awaiting_single_ai_review:" + reason);
         return;
      }
      _SetActionLifecycle(st, "ELIGIBLE", reason);
   }

   bool _PriorActionEffectVisible(PenaltyState &st,
                                  const double current_volume,
                                  const double volume_tolerance) {
      if(!_ActionLifecyclePending(st) || st.action_position_volume_before <= 0.0) return false;
      if(st.requested_action == "PARTIAL_CLOSE" &&
         current_volume + volume_tolerance < st.action_position_volume_before){
         st.action_executed = "partial_exit";
         st.last_reduction_at = _NowServerOrTester();
         st.strikes++;
         _RememberExecutedAction(st, st.action_id);
         _SetActionLifecycle(st, "SUCCEEDED", "position_volume_reduction_verified_after_deferred_broker_result");
         return true;
      }
      return false;
   }

   void _ProcessPendingAction(CTrade &trade,
                              PenaltyState &st,
                              const ulong ticket,
                              const double current_volume,
                              const datetime now) {
      if(!_ActionLifecyclePending(st) || _ActionAlreadyExecuted(st, st.action_id)) return;
      double volume_step = SymbolInfoDouble(st.symbol, SYMBOL_VOLUME_STEP);
      double volume_tolerance = MathMax(1.0e-8, volume_step * 0.51);
      if(_PriorActionEffectVisible(st, current_volume, volume_tolerance)) return;
      if(_ManagementAiReviewRequired() && !_ManagementAiApprovalBound(st)){
         // Nothing reaches the broker without a bound APPROVE for this exact
         // proposal.  A pre-upgrade state restored as ELIGIBLE is routed to review.
         _ClearManagementAiBinding(st);
         _SetActionLifecycle(st, "AI_REVIEW_PENDING", "execution_requires_bound_management_ai_approval");
         return;
      }

      datetime policy_cooldown_until = (st.last_reduction_at > 0
                                        ? st.last_reduction_at + InpPenaltyCooldownMin * 60 : 0);
      datetime retry_at = MathMax(policy_cooldown_until, st.next_eligible_retry_time);
      if(retry_at > now){
         st.next_eligible_retry_time = retry_at;
         _SetActionLifecycle(st, "DEFERRED_COOLDOWN", "management_retry_cooldown_active");
         return;
      }
      if(st.action_retry_count >= MathMax(1, InpManagementActionMaxRetries)){
         _SetActionLifecycle(st, "FAILED_TERMINAL", "management_retry_limit_exhausted");
         return;
      }

      st.requested_volume = (st.requested_action == "FULL_CLOSE"
                             ? current_volume
                             : current_volume * st.requested_cut_fraction);
      st.action_position_volume_before = current_volume;
      bool submit_full_close = (st.requested_action == "FULL_CLOSE");
      if(submit_full_close){
         st.normalized_volume = current_volume;
      } else {
         VolumeNormalizationResult close_norm = NormalizeClosingVolume(st.symbol,
                                                                        st.requested_volume,
                                                                        current_volume,
                                                                        InpClosingVolumeAllowCloseAllBelowMinimum);
         st.normalized_volume = close_norm.normalized_volume;
         if(close_norm.action == "close_all"){
            submit_full_close = true;
            st.normalized_volume = current_volume;
            st.action_executed = "close_all_small_remainder";
         } else if(st.normalized_volume <= 0.0){
            st.action_retry_count++;
            st.action_last_attempt_at = now;
            st.next_eligible_retry_time = now + MathMax(1, InpManagementActionRetryCooldownSec);
            st.action_last_retcode = 0;
            st.action_last_retcode_description = "closing_volume_normalization_unavailable:" + close_norm.reason;
            _RiskLog("[closing_volume] stage=management position_id=" + IntegerToString(st.position_identifier)
                     + " requested=" + DoubleToString(close_norm.requested_volume, 8)
                     + " normalized=" + DoubleToString(close_norm.normalized_volume, 8)
                     + " residual=" + DoubleToString(close_norm.residual_volume, 8)
                     + " action=" + close_norm.action
                     + " reason=" + close_norm.reason);
            _SetActionLifecycle(st, "FAILED_RETRYABLE", "normalized_close_volume_invalid_recompute_later");
            return;
         }
      }

      st.action_retry_count++;
      st.action_last_attempt_at = now;
      st.next_eligible_retry_time = now + MathMax(1, InpManagementActionRetryCooldownSec);
      _SetActionLifecycle(st, "SUBMISSION_ATTEMPTED", "management_close_submitted_to_broker");
      bool submitted = (submit_full_close
                        ? trade.PositionClose(ticket)
                        : trade.PositionClosePartial(ticket, st.normalized_volume));
      uint retcode = trade.ResultRetcode();
      st.action_last_retcode = (long)retcode;
      st.action_last_retcode_description = trade.ResultRetcodeDescription();

      bool still_open = PositionSelectByTicket(ticket);
      double remaining_volume = (still_open ? PositionGetDouble(POSITION_VOLUME) : 0.0);
      bool effect_verified = (submit_full_close
                              ? !still_open
                              : (!still_open || remaining_volume + volume_tolerance < current_volume));
      if(submitted && _ManagementRetcodeAccepted(retcode) && effect_verified){
         st.action_executed = (submit_full_close
                               ? (st.action_executed == "close_all_small_remainder"
                                  ? "close_all_small_remainder" : "full_close")
                               : "partial_exit");
         st.last_reduction_at = now;
         st.strikes++;
         _RememberExecutedAction(st, st.action_id);
         _SetActionLifecycle(st, "SUCCEEDED", "broker_result_and_position_volume_verified");
         if(!still_open){
            st.previous_state = st.current_state;
            st.current_state = "EXITED";
            st.transition_time = now;
            st.transition_reason = "management_close_verified";
         }
         return;
      }

      if(retcode == TRADE_RETCODE_POSITION_CLOSED || !still_open){
         _SetActionLifecycle(st, "SUPERSEDED", "position_no_longer_open");
         return;
      }
      if(_ManagementFailureTerminal(retcode)){
         _SetActionLifecycle(st, "FAILED_TERMINAL", "terminal_broker_rejection");
         return;
      }
      if(st.action_retry_count >= MathMax(1, InpManagementActionMaxRetries)){
         _SetActionLifecycle(st, "FAILED_TERMINAL", "management_retry_limit_exhausted");
         return;
      }
      _SetActionLifecycle(st, "FAILED_RETRYABLE",
                          submitted && _ManagementRetcodeAccepted(retcode)
                          ? "broker_accepted_effect_not_yet_verified"
                          : "retryable_broker_or_runtime_failure");
   }

   string _ManagementAiRequestJson(const PenaltyState &st,
                                   const ulong ticket,
                                   const string comment,
                                   const TradePlan &meta,
                                   const bool has_meta,
                                   const double px,
                                   const double cur_r,
                                   const int mins_open,
                                   const double volume,
                                   const double tp,
                                   const bool structural_invalid,
                                   const bool severe_structural_invalid,
                                   const bool fvg_invalid,
                                   const bool dr_invalid,
                                   const bool thesis_raw,
                                   const bool thesis_confirmed,
                                   const string desired_state,
                                   const string trigger_reason,
                                   const int close_strikes) {
      // Immutable entry thesis fields the per-tick meta parse does not carry.
      // Read once per proposal, never per tick.
      string meta_txt = "";
      if(has_meta){
         if(!(ticket > 0 && m_bus.ReadText(_TradeTicketPath(ticket), meta_txt)) && StringLen(comment) > 0)
            m_bus.ReadText(_TradeKeyPath(comment), meta_txt);
      }
      string j = "{";
      j += JsonKVStr("schema_version", MANAGEMENT_AI_REVIEW_SCHEMA_VERSION) + ",";
      j += JsonKVStr("request_kind", "management_review") + ",";
      j += JsonKVStr("request_id", st.management_ai_request_id) + ",";
      j += JsonKVStr("request_fingerprint", st.management_ai_request_fingerprint) + ",";
      j += JsonKVInt("review_seq", st.management_ai_review_seq) + ",";
      j += JsonKVInt("requested_at", (int)st.management_ai_requested_at) + ",";
      j += JsonKVStr("position_identifier", IntegerToString(st.position_identifier)) + ",";
      j += JsonKVNum("ticket", (double)ticket, 0) + ",";
      j += JsonKVStr("symbol", st.symbol) + ",";
      j += JsonKVStr("action_id", st.action_id) + ",";
      j += "\"proposal\":{" + JsonKVStr("requested_action", st.requested_action) + ","
           + JsonKVNum("requested_cut_fraction", st.requested_cut_fraction, 8) + ","
           + JsonKVStr("trigger_reason", trigger_reason) + ","
           + JsonKVStr("desired_state", desired_state) + ","
           + JsonKVInt("strikes", st.strikes) + ","
           + JsonKVInt("close_strikes", close_strikes) + ","
           + JsonKVStr("authority", "deterministic_penalty_watcher") + "},";
      j += "\"position\":{" + JsonKVBool("is_buy", st.is_buy) + ","
           + JsonKVNum("entry", st.entry, 8) + ","
           + JsonKVNum("sl", st.sl, 8) + ","
           + JsonKVNum("tp", tp, 8) + ","
           + JsonKVNum("planned_tp1", (has_meta ? meta.tp1 : 0.0), 8) + ","
           + JsonKVNum("planned_tp2", (has_meta ? meta.tp2 : 0.0), 8) + ","
           + JsonKVNum("current_price", px, 8) + ","
           + JsonKVNum("volume", volume, 8) + ","
           + JsonKVNum("risk_distance", st.risk_dist, 8) + ","
           + JsonKVNum("current_r", cur_r, 4) + ","
           + JsonKVNum("mfe_r", st.mfe_r, 4) + ","
           + JsonKVNum("mae_r", st.mae_r, 4) + ","
           + JsonKVInt("minutes_open", mins_open) + "},";
      j += "\"invalidation\":{" + JsonKVBool("structural_invalid", structural_invalid) + ","
           + JsonKVBool("severe_structural_invalid", severe_structural_invalid) + ","
           + JsonKVBool("fvg_invalid", fvg_invalid) + ","
           + JsonKVBool("dealing_range_invalid", dr_invalid) + ","
           + JsonKVBool("thesis_raw_breach", thesis_raw) + ","
           + JsonKVBool("thesis_confirmed", thesis_confirmed) + ","
           + JsonKVStr("confirmation_mode", st.confirmation_mode) + ","
           + JsonKVNum("confirmation_trigger_level", st.confirmation_trigger_level, 8) + ","
           + JsonKVInt("confirmed_time", (int)st.confirmed_time) + "},";
      j += "\"thesis\":{" + JsonKVBool("trade_meta_available", has_meta) + ","
           + JsonKVStr("setup_family", JsonGetString(meta_txt, "setup_family", "")) + ","
           + JsonKVStr("setup_class", JsonGetString(meta_txt, "setup_class", "")) + ","
           + JsonKVStr("entry_branch", JsonGetString(meta_txt, "entry_branch", "")) + ","
           + JsonKVStr("target_source", JsonGetString(meta_txt, "target_source", "")) + ","
           + JsonKVStr("po3_state", (has_meta ? meta.po3.po3_state : "")) + ","
           + JsonKVStr("structure_type", (has_meta ? meta.po3.structure_type : "")) + ","
           + JsonKVNum("fvg_lower", (has_meta ? meta.fvg.lower : 0.0), 8) + ","
           + JsonKVNum("fvg_upper", (has_meta ? meta.fvg.upper : 0.0), 8) + ","
           + JsonKVStr("fvg_mitigation_state", (has_meta ? meta.fvg.mitigation_state : "")) + ","
           + JsonKVBool("fvg_structure_invalidated", (has_meta && meta.fvg.structure_invalidated)) + ","
           + JsonKVNum("dealing_range_low", (has_meta ? meta.po3.dr_low : 0.0), 8) + ","
           + JsonKVNum("dealing_range_high", (has_meta ? meta.po3.dr_high : 0.0), 8) + ","
           + JsonKVNum("manipulation_low", (has_meta ? meta.po3.manip_low : 0.0), 8) + ","
           + JsonKVNum("manipulation_high", (has_meta ? meta.po3.manip_high : 0.0), 8) + ","
           + JsonKVNum("swing_low", (has_meta ? meta.po3.swing_low : 0.0), 8) + ","
           + JsonKVNum("swing_high", (has_meta ? meta.po3.swing_high : 0.0), 8) + "},";
      j += "\"state\":{" + JsonKVStr("current_state", st.current_state) + ","
           + JsonKVStr("previous_state", st.previous_state) + ","
           + JsonKVStr("transition_reason", st.transition_reason) + ","
           + JsonKVInt("last_reduction_at", (int)st.last_reduction_at) + "}";
      j += "}";
      return j;
   }

   // Freeze the review binding and write the ONE request for this proposal.
   void _StartManagementAiReview(PenaltyState &st,
                                 const ulong ticket,
                                 const string comment,
                                 const TradePlan &meta,
                                 const bool has_meta,
                                 const double px,
                                 const double cur_r,
                                 const int mins_open,
                                 const double volume,
                                 const double tp,
                                 const bool structural_invalid,
                                 const bool severe_structural_invalid,
                                 const bool fvg_invalid,
                                 const bool dr_invalid,
                                 const bool thesis_raw,
                                 const bool thesis_confirmed,
                                 const string desired_state,
                                 const string transition_reason,
                                 const int close_strikes,
                                 const datetime now) {
      if(_ActionAlreadyExecuted(st, st.action_id)){
         _SetActionLifecycle(st, "SUPERSEDED", "management_action_already_executed");
         return;
      }
      // While the post-reduction cooldown holds, the deterministic system could
      // not execute anyway: asking the model now would spend a call on a verdict
      // that must wait.
      datetime reduction_cooldown_until = (st.last_reduction_at > 0
                                           ? st.last_reduction_at + InpPenaltyCooldownMin * 60 : 0);
      if(reduction_cooldown_until > now){
         if(st.next_eligible_retry_time != reduction_cooldown_until){
            st.next_eligible_retry_time = reduction_cooldown_until;
            _LogManagementAiReview(st, "request_deferred", "reason=reduction_cooldown_active provider_call=false");
         }
         return;
      }
      if(st.next_eligible_retry_time > now) return;

      string trigger = (StringLen(transition_reason) > 0 ? transition_reason : desired_state);
      _ClearManagementAiBinding(st);
      st.management_ai_review_seq++;
      st.management_ai_schema_version = MANAGEMENT_AI_REVIEW_SCHEMA_VERSION;
      st.management_ai_action_id = st.action_id;
      st.management_ai_requested_action = st.requested_action;
      st.management_ai_requested_cut_fraction = st.requested_cut_fraction;
      st.management_ai_requested_at = now;
      string action_hash = IntegerToString((long)PO3ContractFnv1a(st.action_id + "|" + st.requested_action + "|"
                                                                  + DoubleToString(st.requested_cut_fraction, 8)));
      st.management_ai_request_id = "mgmt_" + IntegerToString(st.position_identifier) + "_"
                                    + IntegerToString(st.management_ai_review_seq) + "_" + action_hash;
      st.management_ai_request_fingerprint = IntegerToString((long)PO3ContractFnv1a(
         st.management_ai_request_id + "|" + IntegerToString(st.position_identifier) + "|" + st.action_id + "|"
         + st.requested_action + "|" + DoubleToString(st.requested_cut_fraction, 8) + "|"
         + IntegerToString(st.strikes) + "|" + IntegerToString((long)now) + "|" + trigger));
      st.management_ai_verdict = "PENDING";
      m_immediate_persist_requested = true;

      string response_path = _ManagementAiResponsePath(st.management_ai_request_id);
      if(m_bus.Exists(response_path)) m_bus.Delete(response_path);
      string body = _ManagementAiRequestJson(st, ticket, comment, meta, has_meta, px, cur_r, mins_open, volume, tp,
                                             structural_invalid, severe_structural_invalid, fvg_invalid, dr_invalid,
                                             thesis_raw, thesis_confirmed, desired_state, trigger, close_strikes);
      if(!m_bus.WriteText(_ManagementAiRequestPath(st.management_ai_request_id), body)){
         // Nothing was published, so no provider call can exist for this id.
         st.management_ai_request_id = "";
         st.management_ai_verdict = "";
         st.next_eligible_retry_time = now + MathMax(1, InpManagementActionRetryCooldownSec);
         _LogManagementAiReview(st, "request_write_failed", "provider_call=false broker_action=none");
         return;
      }
      _LogManagementAiReview(st, "request_written",
                             "single_provider_call_for_proposal=true timeout_sec=" + IntegerToString(InpPenaltyAiReviewTimeoutSec)
                             + " trigger=" + trigger
                             + " current_r=" + DoubleToString(cur_r, 4)
                             + " mfe_r=" + DoubleToString(st.mfe_r, 4)
                             + " mae_r=" + DoubleToString(st.mae_r, 4)
                             + " minutes_open=" + IntegerToString(mins_open));
   }

   void _ResolveManagementAiUnresolved(PenaltyState &st, const string reason, const datetime now) {
      string request_path = _ManagementAiRequestPath(st.management_ai_request_id);
      bool unclaimed_removed = (m_bus.Exists(request_path) && m_bus.Delete(request_path));
      st.management_ai_verdict = "UNRESOLVED";
      st.management_ai_status_reason = reason;
      st.management_ai_resolved_at = now;
      st.management_ai_cooldown_until = (datetime)ManagementAiCooldownUntil((long)now, InpPenaltyCooldownMin);
      m_immediate_persist_requested = true;
      _SetActionLifecycle(st, "AI_REVIEW_UNRESOLVED", "not_approved:" + reason);
      _LogManagementAiReview(st, "unresolved_not_approved",
                             "reason=" + reason + " broker_action=none strikes_unchanged=true"
                             + " unclaimed_request_removed=" + (unclaimed_removed ? "true" : "false"));
   }

   // Only a bound, fresh, schema-valid RESOLVED envelope can yield a verdict.
   bool _ValidateManagementAiResponse(PenaltyState &st,
                                      const string txt,
                                      const datetime now,
                                      string &verdict,
                                      string &reason) {
      verdict = "";
      reason = "";
      string doc_reason = "";
      if(!JsonValidateDocumentStrict(txt, doc_reason)){ reason = "response_malformed_json:" + doc_reason; return false; }
      string value = "";
      if(!JsonGetStringStrict(txt, "schema_version", value) || value != MANAGEMENT_AI_REVIEW_SCHEMA_VERSION){
         reason = "response_schema_version_mismatch"; return false;
      }
      if(!JsonGetStringStrict(txt, "response_kind", value) || value != "management_review"){
         reason = "response_kind_mismatch"; return false;
      }
      if(!JsonGetStringStrict(txt, "request_id", value) || value != st.management_ai_request_id){
         reason = "response_request_id_mismatch"; return false;
      }
      string status = "";
      if(!JsonGetStringStrict(txt, "status", status)){ reason = "response_status_missing"; return false; }
      if(status == "ERROR"){
         string category = "";
         JsonGetStringStrict(txt, "error_category", category, true);
         reason = "response_error_envelope:" + category;
         return false;
      }
      if(status != "RESOLVED"){ reason = "response_status_invalid"; return false; }
      if(!JsonGetStringStrict(txt, "request_fingerprint", value) || value != st.management_ai_request_fingerprint){
         reason = "response_request_fingerprint_mismatch"; return false;
      }
      if(!JsonGetStringStrict(txt, "position_identifier", value) || value != IntegerToString(st.position_identifier)){
         reason = "response_position_mismatch"; return false;
      }
      if(!JsonGetStringStrict(txt, "action_id", value) || value != st.action_id || st.management_ai_action_id != st.action_id){
         reason = "response_action_id_mismatch"; return false;
      }
      if(!JsonGetStringStrict(txt, "requested_action", value) || value != st.requested_action){
         reason = "response_requested_action_altered"; return false;
      }
      double number = 0.0;
      if(!JsonGetNumberStrict(txt, "requested_cut_fraction", number) ||
         MathAbs(number - st.requested_cut_fraction) > 0.000001){
         reason = "response_cut_fraction_altered"; return false;
      }
      if(!JsonGetNumberStrict(txt, "requested_at", number) ||
         (long)MathRound(number) != (long)st.management_ai_requested_at){
         reason = "response_requested_at_mismatch"; return false;
      }
      if(ManagementAiReviewTimedOut((long)now, (long)st.management_ai_requested_at, InpPenaltyAiReviewTimeoutSec)){
         reason = "response_stale"; return false;
      }
      if(!JsonGetStringStrict(txt, "verdict", value) || (value != "APPROVE" && value != "DENY")){
         reason = "response_verdict_invalid"; return false;
      }
      string codes = "";
      if(!JsonGetArrayStrict(txt, "reason_codes", codes) || StringLen(codes) < 4){
         reason = "response_reason_codes_missing"; return false;
      }
      verdict = value;
      st.management_ai_reason_codes = codes;
      string text = "";
      if(JsonGetStringStrict(txt, "reason", text, true)) st.management_ai_reason = text;
      if(JsonGetStringStrict(txt, "provider_id", text, true)) st.management_ai_provider = text;
      if(JsonGetStringStrict(txt, "model", text, true)) st.management_ai_model = text;
      if(JsonGetStringStrict(txt, "response_fingerprint", text, true)) st.management_ai_response_fingerprint = text;
      return true;
   }

   // Returns true only when a bound APPROVE made the frozen action ELIGIBLE.
   bool _AwaitManagementAiReview(PenaltyState &st, const datetime now) {
      string response_path = _ManagementAiResponsePath(st.management_ai_request_id);
      if(!m_bus.Exists(response_path)){
         if(ManagementAiReviewTimedOut((long)now, (long)st.management_ai_requested_at, InpPenaltyAiReviewTimeoutSec))
            _ResolveManagementAiUnresolved(st, "management_ai_review_timeout", now);
         return false;
      }
      string txt = "";
      if(!m_bus.ReadText(response_path, txt)){
         if(ManagementAiReviewTimedOut((long)now, (long)st.management_ai_requested_at, InpPenaltyAiReviewTimeoutSec))
            _ResolveManagementAiUnresolved(st, "management_ai_response_unreadable", now);
         return false;
      }
      m_bus.Delete(response_path);
      string verdict = "";
      string invalid_reason = "";
      if(!_ValidateManagementAiResponse(st, txt, now, verdict, invalid_reason)){
         _ResolveManagementAiUnresolved(st, invalid_reason, now);
         return false;
      }
      st.management_ai_resolved_at = now;
      m_immediate_persist_requested = true;
      if(verdict == "APPROVE"){
         st.management_ai_verdict = "APPROVE";
         st.management_ai_status_reason = "bound_fresh_schema_valid_approve";
         st.next_eligible_retry_time = 0;
         _SetActionLifecycle(st, "AI_APPROVED", "management_ai_approved_exact_deterministic_action");
         _LogManagementAiReview(st, "approved", "execute_original_action_now=true reason_codes=" + st.management_ai_reason_codes
                                + " provider=" + st.management_ai_provider + " model=" + st.management_ai_model);
         _SetActionLifecycle(st, "ELIGIBLE", "management_ai_approval_bound_to_action");
         return true;
      }
      st.management_ai_verdict = "DENY";
      st.management_ai_status_reason = "bound_fresh_schema_valid_deny";
      st.management_ai_denied_at = now;
      st.management_ai_cooldown_until = (datetime)ManagementAiCooldownUntil((long)now, InpPenaltyCooldownMin);
      _SetActionLifecycle(st, "AI_DENIED", "management_ai_denied_no_broker_action");
      _SetActionLifecycle(st, "AI_DENIAL_COOLDOWN", "no_penalty_execution_and_no_ai_call_until_cooldown_expiry");
      _LogManagementAiReview(st, "denied", "broker_action=none strikes_unchanged=true reason_codes="
                             + st.management_ai_reason_codes + " cooldown_minutes=" + IntegerToString(InpPenaltyCooldownMin));
      return false;
   }

   string _TransitionActionId(const PenaltyState &st,
                              const string next_state,
                              const string reason,
                              const datetime evidence_time) const {
      return IntegerToString(st.position_identifier) + ":" + MANAGEMENT_SCHEMA_VERSION + ":"
             + next_state + ":" + reason + ":" + IntegerToString((long)evidence_time);
   }

   bool _TransitionAllowed(const string current_state, const string next_state) const {
      if(current_state == next_state) return true;
      if(current_state == "HEALTHY")
         return (next_state == "WARNING" || next_state == "THESIS_INVALID" || next_state == "EXITED");
      if(current_state == "WARNING")
         return (next_state == "HEALTHY" || next_state == "THESIS_INVALID" || next_state == "EXITED");
      if(current_state == "THESIS_INVALID") return (next_state == "EXITED");
      return false;
   }

   bool _ClosedBarBeyond(const string symbol,
                         const ENUM_TIMEFRAMES timeframe,
                         const bool is_buy,
                         const double trigger_level,
                         const double buffer,
                         datetime &confirming_bar) const {
      confirming_bar = 0;
      if(trigger_level <= 0.0) return false;
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      if(CopyRates(symbol, timeframe, 0, 2, rates) < 2) return false;
      confirming_bar = rates[1].time;
      return (is_buy ? rates[1].close < trigger_level - buffer
                     : rates[1].close > trigger_level + buffer);
   }

   bool _ConfirmThesisInvalidation(PenaltyState &st,
                                   const TradePlan &meta,
                                   const bool raw_breach,
                                   const double trigger_level,
                                   const double px,
                                   const datetime now) {
      ENUM_INVALIDATION_CONFIRMATION_MODE confirmation_mode;
      int persistence_seconds = 0;
      double spread_buffer_multiple = 0.0;
      string policy_source = "";
      string policy_version = "";
      if(!_ResolveInvalidationPolicy(meta,
                                    confirmation_mode,
                                    persistence_seconds,
                                    spread_buffer_multiple,
                                    policy_source,
                                    policy_version)){
         st.confirmation_mode = "ASSET_CLASS_POLICY_INVALID_FAIL_CLOSED";
         st.confirmed_time = 0;
         _RiskLog("[invalidation_confirmation] symbol=" + st.symbol
                  + " status=blocked reason=asset_class_policy_missing_or_incompatible"
                  + " policy_file=" + InpInvalidationAssetClassPolicyFile);
         return false;
      }
      double bid = SymbolInfoDouble(st.symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(st.symbol, SYMBOL_ASK);
      double spread = MathMax(0.0, ask - bid);
      double buffer = spread * spread_buffer_multiple;
      st.confirmation_mode = _ConfirmationModeName(confirmation_mode) + ":" + policy_source + ":" + policy_version;
      st.confirmation_trigger_level = trigger_level;
      st.confirmation_spread = spread;
      st.confirmation_buffer = buffer;
      st.confirmation_timeframe = (int)(confirmation_mode == INVALIDATION_CLOSED_ENTRY_TF_BAR
                                        ? meta.ltf : PERIOD_M1);
      if(!raw_breach){
         st.first_breach_time = 0;
         st.confirmed_time = 0;
         st.confirming_bar = 0;
         return false;
      }
      if(st.first_breach_time <= 0) st.first_breach_time = now;

      bool confirmed = false;
      if(confirmation_mode == INVALIDATION_CLOSED_M1_BAR){
         confirmed = _ClosedBarBeyond(st.symbol, PERIOD_M1, st.is_buy, trigger_level, buffer, st.confirming_bar);
      } else if(confirmation_mode == INVALIDATION_CLOSED_ENTRY_TF_BAR){
         ENUM_TIMEFRAMES tf = (meta.ltf > PERIOD_CURRENT ? meta.ltf : PO3EffectiveEntryTF());
         st.confirmation_timeframe = (int)tf;
         confirmed = _ClosedBarBeyond(st.symbol, tf, st.is_buy, trigger_level, buffer, st.confirming_bar);
      } else if(confirmation_mode == INVALIDATION_N_SECOND_PERSISTENCE){
         confirmed = (now - st.first_breach_time >= persistence_seconds);
      } else if(confirmation_mode == INVALIDATION_PRICE_SPREAD_BUFFER){
         confirmed = (trigger_level > 0.0 &&
                      (st.is_buy ? px < trigger_level - buffer : px > trigger_level + buffer));
      } else {
         confirmed = true;
      }
      if(confirmed && st.confirmed_time <= 0) st.confirmed_time = now;
      return confirmed;
   }

   void _LogTransition(const PenaltyState &st) const {
      _RiskLog("[management_transition] position_id=" + IntegerToString(st.position_identifier)
               + " from=" + st.previous_state
               + " to=" + st.current_state
               + " reason=" + st.transition_reason
               + " action=" + st.action_executed
               + " action_id=" + st.action_id);
      string experiment = "{";
      experiment += JsonKVStr("schema_version", MANAGEMENT_EXPERIMENT_SCHEMA_VERSION) + ",";
      experiment += JsonKVNum("position_id", (double)st.position_identifier, 0) + ",";
      experiment += JsonKVStr("symbol", st.symbol) + ",";
      experiment += JsonKVInt("transition_time", (int)st.transition_time) + ",";
      experiment += JsonKVStr("transition_reason", st.transition_reason) + ",";
      experiment += JsonKVStr("active_policy", InpThesisInvalidationPolicy == THESIS_INVALIDATION_FULL_EXIT
                              ? "full_exit_on_confirmed_thesis_invalidation"
                              : (InpThesisInvalidationPolicy == THESIS_INVALIDATION_ORIGINAL_SL_TP_ONLY
                                 ? "original_sl_tp_only"
                                 : "partial_exit_on_confirmed_thesis_invalidation")) + ",";
      experiment += "\"shadow_policies\":[\"full_exit_on_confirmed_thesis_invalidation\",\"partial_exit_on_confirmed_thesis_invalidation\",\"original_sl_tp_only\"],";
      experiment += JsonKVBool("trading_authority", false) + ",";
      experiment += JsonKVBool("outcome_pending", true) + ",";
      experiment += JsonKVBool("policy_selection_eligible", false) + ",";
      experiment += JsonKVStr("eligibility_reason", "clean_oos_management_alpha_not_yet_available");
      experiment += "}";
      m_bus.AppendText(m_bus.LogDir() + "\\management_experiments.jsonl", experiment + "\n");
   }

   void _ObserveSelectedPositionPath(const ulong ticket,
                                     const string observed_symbol,
                                     const MqlTick &tick,
                                     const string observation_source,
                                     const bool tick_complete_source) {
      if(ticket == 0 || !PositionSelectByTicket(ticket) || !PositionMatchesMagic(ticket)) return;
      string symbol = PositionGetString(POSITION_SYMBOL);
      long position_identifier = (long)PositionGetInteger(POSITION_IDENTIFIER);
      if(position_identifier <= 0 || symbol != observed_symbol) return;
      bool is_buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double risk_dist = MathAbs(entry - sl);
      double executable_price = (is_buy ? tick.bid : tick.ask);
      if(entry <= 0.0 || sl <= 0.0 || risk_dist <= 0.0 || executable_price <= 0.0) return;

      int idx = _FindState(position_identifier, symbol);
      PenaltyState st;
      if(idx >= 0){
         st = m_states[idx];
      } else {
         ZeroMemory(st);
         st.symbol = symbol;
         st.position_identifier = position_identifier;
         st.position_ticket = ticket;
         st.opened_at = (datetime)PositionGetInteger(POSITION_TIME);
         st.entry = entry;
         st.sl = sl;
         st.risk_dist = risk_dist;
         st.is_buy = is_buy;
         st.mfe_price = entry;
         st.mae_price = entry;
         st.current_state = "HEALTHY";
         st.transition_time = st.opened_at;
         st.transition_reason = "position_tracking_started";
         st.management_version = MANAGEMENT_SCHEMA_VERSION;
         st.path_data_gap = (tick_complete_source &&
                             (st.opened_at <= 0 || tick.time > st.opened_at + 2));
      }

      if(st.entry <= 0.0) st.entry = entry;
      if(st.sl <= 0.0) st.sl = sl;
      if(st.risk_dist <= 0.0) st.risk_dist = risk_dist;
      st.position_ticket = ticket;
      st.is_buy = is_buy;
      st.position_closed_observed_at = 0;
      if(st.mfe_price <= 0.0) st.mfe_price = st.entry;
      if(st.mae_price <= 0.0) st.mae_price = st.entry;
      if(is_buy){
         st.mfe_price = MathMax(st.mfe_price, executable_price);
         st.mae_price = MathMin(st.mae_price, executable_price);
      } else {
         st.mfe_price = MathMin(st.mfe_price, executable_price);
         st.mae_price = MathMax(st.mae_price, executable_price);
      }
      double favorable_r = (is_buy ? st.mfe_price - st.entry : st.entry - st.mfe_price) / st.risk_dist;
      double adverse_r = (is_buy ? st.entry - st.mae_price : st.mae_price - st.entry) / st.risk_dist;
      st.mfe_r = MathMax(st.mfe_r, MathMax(0.0, favorable_r));
      st.mae_r = MathMax(st.mae_r, MathMax(0.0, adverse_r));
      datetime observed_time = (tick.time > 0 ? tick.time : _NowServerOrTester());
      if(st.first_0_25r_time <= 0 && st.mfe_r >= 0.25) st.first_0_25r_time = observed_time;
      if(st.first_0_50r_time <= 0 && st.mfe_r >= 0.50) st.first_0_50r_time = observed_time;
      double adverse_threshold = MathMax(0.01, MathAbs(InpPenaltyMaeTriggerR));
      if(st.first_adverse_threshold_time <= 0 && st.mae_r >= adverse_threshold)
         st.first_adverse_threshold_time = observed_time;
      if(st.first_0_25r_time == observed_time && st.first_adverse_threshold_time == observed_time)
         st.path_order_ambiguous = true;
      if(st.first_0_50r_time == observed_time && st.first_adverse_threshold_time == observed_time)
         st.path_order_ambiguous = true;
      st.latest_observed_tick_time = observed_time;
      st.latest_observed_tick_msc = (long)tick.time_msc;
      st.path_observation_source = observation_source;
      if(tick_complete_source && !st.path_data_gap)
         st.path_completeness_status = "TICK_COMPLETE";
      else if(st.path_data_gap)
         st.path_completeness_status = "DATA_GAP";
      else if(st.path_completeness_status != "TICK_COMPLETE")
         st.path_completeness_status = "TIMER_SAMPLED";
      _SaveState(st);
   }

public:
   CPenaltyWatcher(CFileBus &bus){
      m_bus=&bus;
      m_immediate_persist_requested = false;
      ArrayResize(m_states, 0);
   }

   // Review bindings, verdicts and cooldowns must reach disk before the next
   // periodic snapshot, or a restart could lose which proposal was already sent.
   bool ConsumeImmediatePersistRequest(){
      bool requested = m_immediate_persist_requested;
      m_immediate_persist_requested = false;
      return requested;
   }

   void RestoreStates(const PenaltyState &states[]){
      int n = ArraySize(states);
      ArrayResize(m_states, n);
      for(int i=0; i<n; i++){
         m_states[i] = states[i];
         m_states[i].path_data_gap = true;
         m_states[i].path_completeness_status = "DATA_GAP";
         m_states[i].path_observation_source = "RESTORED_AFTER_OFFLINE_INTERVAL";
         if(m_states[i].action_lifecycle_state == "SUBMISSION_ATTEMPTED"){
            m_states[i].action_lifecycle_state = "FAILED_RETRYABLE";
            m_states[i].next_eligible_retry_time = _NowServerOrTester()
                                                     + MathMax(1, InpManagementActionRetryCooldownSec);
            m_states[i].action_last_retcode_description = "restart_requires_effect_verification_before_retry";
         }
      }
   }

   void SnapshotStates(PenaltyState &out_states[]){
      int n = ArraySize(m_states);
      ArrayResize(out_states, n);
      for(int i=0; i<n; i++) out_states[i] = m_states[i];
   }

   bool GetState(const long position_identifier, const string symbol, PenaltyState &out_state){
      int idx = _FindState(position_identifier, symbol);
      if(idx < 0) return false;
      out_state = m_states[idx];
      return true;
   }

   bool ForgetState(const long position_identifier, const string symbol){
      int idx = _FindState(position_identifier, symbol);
      if(idx < 0) return false;
      _RemoveStateAt(idx);
      return true;
   }

   void ObserveChartTick(const string chart_symbol){
      MqlTick tick;
      if(!SymbolInfoTick(chart_symbol, tick)) return;
      for(int i=PositionsTotal()-1; i>=0; i--){
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != chart_symbol) continue;
         _ObserveSelectedPositionPath(ticket, chart_symbol, tick, "ON_TICK_CHART_SYMBOL", true);
      }
   }

   void Tick(CTrade &trade){
      datetime now = _NowServerOrTester();
      long live_position_identifiers[];
      ArrayResize(live_position_identifiers, 0);

      // Iterate open positions and apply rules (giveback, MAE, stuck, invalidations).
      for(int i=PositionsTotal()-1; i>=0; i--){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;

         long position_identifier = (long)PositionGetInteger(POSITION_IDENTIFIER);
         if(position_identifier <= 0){
            _RiskLog(PositionGetString(POSITION_SYMBOL)
                     + " penalty state skipped reason=missing_position_identifier"
                     + " ticket=" + IntegerToString((long)ticket));
            continue;
         }

         int n_live = ArraySize(live_position_identifiers);
         ArrayResize(live_position_identifiers, n_live+1);
         live_position_identifiers[n_live] = position_identifier;

         string sym = PositionGetString(POSITION_SYMBOL);
         string comment = PositionGetString(POSITION_COMMENT);
         bool is_buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double entry = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double vol = PositionGetDouble(POSITION_VOLUME);
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

         if(sl <= 0 || vol <= 0) continue;
         double risk_dist = MathAbs(entry - sl);
         if(risk_dist <= 0) continue;

         MqlTick sampled_tick;
         if(SymbolInfoTick(sym, sampled_tick))
            _ObserveSelectedPositionPath(ticket, sym, sampled_tick,
                                         "TIMER_SAMPLED_MARKET_WATCH", false);

         int st_idx = _FindState(position_identifier, sym);
         PenaltyState st;
         if(st_idx >= 0){
            st = m_states[st_idx];
         } else {
            ZeroMemory(st);
            st.symbol = sym;
            st.position_identifier = position_identifier;
            st.position_ticket = ticket;
            st.opened_at = opened;
            st.entry = entry;
            st.sl = sl;
            st.risk_dist = risk_dist;
            st.is_buy = is_buy;
            st.mfe_price = entry;
            st.mae_price = entry;
            st.strikes = 0;
            st.last_reduction_at = 0;
            st.current_state = "HEALTHY";
            st.previous_state = "";
            st.transition_time = opened;
            st.transition_reason = "position_tracking_started";
            st.management_version = MANAGEMENT_SCHEMA_VERSION;
            st.executed_action_ids = "";
            st.action_lifecycle_state = "";
            st.path_completeness_status = "UNKNOWN";
            st.path_observation_source = "NO_QUOTE_OBSERVED";
            st.path_data_gap = true;
         }

         st.symbol = sym;
         st.position_identifier = position_identifier;
         st.position_ticket = ticket;
         st.opened_at = opened;
         st.position_closed_observed_at = 0;
         if(st.entry <= 0.0) st.entry = entry;
         if(st.sl <= 0.0) st.sl = sl;
         st.is_buy = is_buy;
         if(st.risk_dist <= 0) st.risk_dist = risk_dist;
         if(StringLen(st.current_state) == 0) st.current_state = "HEALTHY";
         if(StringLen(st.management_version) == 0) st.management_version = MANAGEMENT_SCHEMA_VERSION;

         // AI denial / unresolved-review cooldown.  At expiry the denied proposal
         // is discarded; this same pass then evaluates the position afresh.
         if(st.management_ai_cooldown_until > 0 &&
            !ManagementAiCooldownActive((long)now, (long)st.management_ai_cooldown_until)){
            string stale_action_id = st.action_id;
            if(st.action_lifecycle_state == "AI_DENIAL_COOLDOWN" ||
               st.action_lifecycle_state == "AI_REVIEW_UNRESOLVED"){
               _LogManagementAiReview(st, "cooldown_expired",
                                      "stale_proposal_discarded=true fresh_deterministic_evaluation=true");
               st.action_id = "";
               st.requested_action = "";
               st.requested_cut_fraction = 0.0;
               _SetActionLifecycle(st, "AI_COOLDOWN_EXPIRED", "denied_or_unresolved_proposal_discarded:" + stale_action_id);
            }
            st.management_ai_cooldown_until = 0;
            m_immediate_persist_requested = true;
         }
         bool management_ai_cooldown_active = ManagementAiCooldownActive((long)now,
                                                                         (long)st.management_ai_cooldown_until);

         double px = _CurPrice(sym, is_buy);
         if(is_buy){
            st.mfe_price = MathMax(st.mfe_price, px);
            st.mae_price = MathMin(st.mae_price, px);
         } else {
            st.mfe_price = MathMin(st.mfe_price, px);
            st.mae_price = MathMax(st.mae_price, px);
         }

         // compute current R and MFE R using persisted state
         double curR = is_buy ? (px - st.entry)/st.risk_dist : (st.entry - px)/st.risk_dist;
         double observed_mfe_r = is_buy ? (st.mfe_price - st.entry)/st.risk_dist : (st.entry - st.mfe_price)/st.risk_dist;
         double observed_mae_r = is_buy ? (st.entry - st.mae_price)/st.risk_dist : (st.mae_price - st.entry)/st.risk_dist;
         st.mfe_r = MathMax(st.mfe_r, MathMax(0.0, observed_mfe_r));
         st.mae_r = MathMax(st.mae_r, MathMax(0.0, observed_mae_r));
         double mfeR = st.mfe_r;

          // ---- Invalidations ----
          TradePlan meta;
          bool has_meta = _LoadTradeMeta(ticket, sym, comment, meta);

         bool dr_invalid=false, fvg_invalid=false, structural_invalid=false, severe_structural_invalid=false;
         double thesis_trigger_level = 0.0;
         int mins_open = (int)MathMax(0, (now - opened) / 60);
         if(has_meta){
            if(is_buy && meta.po3.dr_low>0 && px < meta.po3.dr_low) dr_invalid=true;
            if(!is_buy && meta.po3.dr_high>0 && px > meta.po3.dr_high) dr_invalid=true;

            double eps = (double)InpPenaltyInvalidEpsBps/10000.0 * px;
            if(is_buy && meta.fvg.lower>0 && px < (meta.fvg.lower - eps)) fvg_invalid=true;
            if(!is_buy && meta.fvg.upper>0 && px > (meta.fvg.upper + eps)) fvg_invalid=true;
            if(meta.fvg.structure_invalidated) fvg_invalid = true;

            double structural_level = 0.0;
            if(is_buy){
               if(meta.po3.manip_low > 0) structural_level = meta.po3.manip_low;
               if(meta.po3.swing_low > 0)
                  structural_level = (structural_level > 0 ? MathMin(structural_level, meta.po3.swing_low) : meta.po3.swing_low);
               if(structural_level > 0 && px < (structural_level - eps)) structural_invalid = true;
            } else {
               if(meta.po3.manip_high > 0) structural_level = meta.po3.manip_high;
               if(meta.po3.swing_high > 0)
                  structural_level = (structural_level > 0 ? MathMax(structural_level, meta.po3.swing_high) : meta.po3.swing_high);
               if(structural_level > 0 && px > (structural_level + eps)) structural_invalid = true;
            }
            if(structural_invalid){
               thesis_trigger_level = structural_level;
               double severe_gap = MathMax(eps, st.risk_dist * 0.25);
               if(is_buy) severe_structural_invalid = (structural_level > 0 && px < (structural_level - severe_gap));
               else       severe_structural_invalid = (structural_level > 0 && px > (structural_level + severe_gap));
               if(curR <= -1.05) severe_structural_invalid = true;
            }
         }

         // ---- Penalty triggers ----
         bool trigger_cut=false;
         bool warning_trigger=false;
         double cut_pct=0.0;
         string trigger_reason = "";
         double mae_trigger_r = (has_meta && meta.penalty_mae_trigger_r != 0.0 ? meta.penalty_mae_trigger_r : _MaeTriggerR(sym));
         int stuck_minutes = (has_meta && meta.penalty_stuck_minutes > 0 ? meta.penalty_stuck_minutes : _StuckMinutes(sym));
         double stuck_min_mfe_r = (has_meta && meta.penalty_stuck_min_mfe_r > 0.0 ? meta.penalty_stuck_min_mfe_r : _StuckMinMfeR(sym));
         double giveback_trigger_r = (has_meta && meta.penalty_giveback_trigger_r > 0.0 ? meta.penalty_giveback_trigger_r : _GivebackTrigMfeR(sym));
         double giveback_floor_r = (has_meta && meta.penalty_giveback_floor_r > 0.0 ? meta.penalty_giveback_floor_r : _GivebackFloorR(sym));
         double dr_invalid_cut_pct = (has_meta && meta.penalty_dr_invalid_cut_pct > 0.0 ? meta.penalty_dr_invalid_cut_pct : InpPenaltyDrInvalidCutPct);
         double fvg_invalid_cut_pct = (has_meta && meta.penalty_fvg_invalid_cut_pct > 0.0 ? meta.penalty_fvg_invalid_cut_pct : InpPenaltyFvgInvalidCutPct);
         int close_strikes = (has_meta && meta.penalty_close_strikes > 0 ? meta.penalty_close_strikes : InpPenaltyCloseStrikes);
         bool penalty_time_ok = (mins_open >= InpMinMinutesBeforePenaltyCuts || severe_structural_invalid);

         if(structural_invalid && penalty_time_ok){
            trigger_cut=true;
            cut_pct = dr_invalid_cut_pct;
            trigger_reason = "structural_invalid";
         }

         if(fvg_invalid && structural_invalid && penalty_time_ok){
            trigger_cut=true;
            cut_pct = MathMax(cut_pct, fvg_invalid_cut_pct);
            trigger_reason = "fvg_and_structural_invalid";
         }
         if(dr_invalid && structural_invalid && penalty_time_ok){
            trigger_cut=true;
            cut_pct = MathMax(cut_pct, dr_invalid_cut_pct);
            trigger_reason = "dr_and_structural_invalid";
         }

         // MAE trigger
         if(penalty_time_ok && curR <= mae_trigger_r){
            trigger_cut=true;
            warning_trigger=true;
            cut_pct = MathMax(cut_pct, 0.25);
            if(StringLen(trigger_reason) == 0) trigger_reason = "mae_trigger";
         }

         // Stuck trigger
         if(penalty_time_ok && mins_open >= stuck_minutes && mfeR < stuck_min_mfe_r){
            trigger_cut=true;
            warning_trigger=true;
            cut_pct = MathMax(cut_pct, 0.25);
            if(StringLen(trigger_reason) == 0) trigger_reason = "stuck_no_mfe";
         }

         // Giveback trigger (approx using current R as both, unless you persist MFE)
         if(penalty_time_ok && mfeR >= giveback_trigger_r && curR <= giveback_floor_r){
            trigger_cut=true;
            warning_trigger=true;
            cut_pct = MathMax(cut_pct, 0.25);
            if(StringLen(trigger_reason) == 0) trigger_reason = "giveback";
         }

         bool thesis_raw = (structural_invalid && penalty_time_ok);
         bool thesis_confirmed = (has_meta && thesis_raw
                                  ? _ConfirmThesisInvalidation(st, meta, true, thesis_trigger_level, px, now)
                                  : false);
         if(!thesis_raw && has_meta)
            _ConfirmThesisInvalidation(st, meta, false, thesis_trigger_level, px, now);

         string desired_state = "HEALTHY";
         if(thesis_confirmed) desired_state = "THESIS_INVALID";
         else if(warning_trigger) desired_state = "WARNING";

         if(desired_state != st.current_state && !_TransitionAllowed(st.current_state, desired_state)){
            _RiskLog("[management_transition_blocked] position_id=" + IntegerToString(position_identifier)
                     + " from=" + st.current_state
                     + " to=" + desired_state
                     + " reason=invalid_state_regression");
            desired_state = st.current_state;
         }

         bool state_changed = (desired_state != st.current_state);
         datetime evidence_time = (thesis_confirmed && st.confirmed_time > 0
                                   ? st.confirmed_time
                                   : (state_changed || st.transition_time <= 0 ? now : st.transition_time));
         string transition_reason = (desired_state == "HEALTHY" ? "trigger_cleared" : trigger_reason);
         if(state_changed){
            string prior_state = st.current_state;
            st.previous_state = prior_state;
            st.current_state = desired_state;
            st.transition_time = now;
            st.transition_reason = transition_reason;
            st.evidence_snapshot_json = "{" + JsonKVNum("price", px, 8) + ","
                                        + JsonKVNum("cur_r", curR, 6) + ","
                                        + JsonKVNum("mfe_r", st.mfe_r, 6) + ","
                                        + JsonKVNum("mae_r", st.mae_r, 6) + ","
                                        + JsonKVBool("raw_thesis_breach", thesis_raw) + ","
                                        + JsonKVBool("confirmed", thesis_confirmed) + ","
                                        + JsonKVStr("confirmation_mode", st.confirmation_mode) + "}";
            st.management_version = MANAGEMENT_SCHEMA_VERSION;
            if(desired_state == "HEALTHY" && _ActionLifecyclePending(st))
               _SetActionLifecycle(st, "SUPERSEDED", "management_trigger_cleared_before_execution");
         }

         bool management_action_required = ((desired_state == "WARNING" || desired_state == "THESIS_INVALID") &&
                                            (trigger_cut || thesis_confirmed ||
                                             (desired_state == "THESIS_INVALID" && _ActionLifecyclePending(st))));
         if(management_action_required && management_ai_cooldown_active){
            // Passive observation only: no punitive execution, no new proposal,
            // no AI call.  Excursion, invalidation and state tracking above
            // continue and are persisted below.
         } else if(management_action_required){
            string action_id = (_ActionLifecyclePending(st) && !state_changed
                                ? st.action_id
                                : _TransitionActionId(st, desired_state, transition_reason, evidence_time));
            if(InpThesisInvalidationPolicy == THESIS_INVALIDATION_ORIGINAL_SL_TP_ONLY &&
               desired_state == "THESIS_INVALID"){
               if(!_ActionAlreadyExecuted(st, action_id)){
                  _QueueManagementAction(st, action_id, "NO_BROKER_ACTION", 0.0,
                                         "original_sl_tp_only_policy");
                  st.action_executed = "shadow_original_sl_tp_only";
                  _RememberExecutedAction(st, action_id);
                  _SetActionLifecycle(st, "SUCCEEDED", "policy_requires_no_broker_intervention");
               }
            } else {
               bool force_full_close = (desired_state == "THESIS_INVALID" &&
                                        InpThesisInvalidationPolicy == THESIS_INVALIDATION_FULL_EXIT);
               if(st.strikes + 1 >= close_strikes) force_full_close = true;
               string requested_action = (force_full_close ? "FULL_CLOSE" : "PARTIAL_CLOSE");
               double cut_fraction = (force_full_close
                                      ? 1.0
                                      : _ScaledCutPct(MathMax(0.01, cut_pct), st.strikes + 1));
               bool need_new_action = (StringLen(st.action_id) == 0 ||
                                       (st.action_id != action_id && !_ActionAlreadyExecuted(st, action_id)) ||
                                       (_ActionLifecyclePending(st) &&
                                        _ActionStrength(requested_action) > _ActionStrength(st.requested_action)) ||
                                       (state_changed && st.action_id != action_id));
               if(need_new_action)
                  _QueueManagementAction(st, action_id, requested_action, cut_fraction, transition_reason);
               if(st.action_lifecycle_state == "AI_REVIEW_PENDING"){
                  if(!_ManagementAiRequestBound(st))
                     _StartManagementAiReview(st, ticket, comment, meta, has_meta, px, curR, mins_open, vol, tp,
                                              structural_invalid, severe_structural_invalid, fvg_invalid, dr_invalid,
                                              thesis_raw, thesis_confirmed, desired_state, transition_reason,
                                              close_strikes, now);
                  else
                     _AwaitManagementAiReview(st, now);
               }
               // An APPROVE turns the frozen proposal ELIGIBLE above; it is
               // submitted in this same pass with the original action and cut.
               if(_ActionLifecyclePending(st) && st.action_lifecycle_state != "AI_REVIEW_PENDING")
                  _ProcessPendingAction(trade, st, ticket, vol, now);
            }
         } else if(_ActionLifecyclePending(st) && desired_state == st.current_state){
            _SetActionLifecycle(st, "SUPERSEDED", "management_action_no_longer_applicable");
         }

         if(state_changed) _LogTransition(st);

         _SaveState(st);
      }

      for(int i=ArraySize(m_states)-1; i>=0; i--){
         if(!_PositionIdentifierStillOpen(m_states[i].position_identifier,
                                          live_position_identifiers)){
            if(_ActionLifecyclePending(m_states[i]) &&
               m_states[i].requested_action == "FULL_CLOSE" &&
               _ManagementRetcodeAccepted((uint)m_states[i].action_last_retcode)){
               m_states[i].action_executed = "full_close";
               m_states[i].last_reduction_at = now;
               m_states[i].strikes++;
               _RememberExecutedAction(m_states[i], m_states[i].action_id);
               _SetActionLifecycle(m_states[i], "SUCCEEDED",
                                   "position_absence_verified_after_deferred_full_close_result");
               m_states[i].previous_state = m_states[i].current_state;
               m_states[i].current_state = "EXITED";
               m_states[i].transition_time = now;
               m_states[i].transition_reason = "management_close_verified_after_deferred_result";
            } else if(_ActionLifecyclePending(m_states[i])){
               _SetActionLifecycle(m_states[i], "SUPERSEDED",
                                   "position_closed_by_other_authority_before_retry");
            }
            if(m_states[i].position_closed_observed_at <= 0){
               m_states[i].position_closed_observed_at = now;
               _RiskLog("[management_state_retained] position_id="
                        + IntegerToString(m_states[i].position_identifier)
                        + " symbol=" + m_states[i].symbol
                        + " reason=awaiting_completed_trade_ledger");
            } else if(now - m_states[i].position_closed_observed_at > 86400){
               _RiskLog("[management_state_expired] position_id="
                        + IntegerToString(m_states[i].position_identifier)
                        + " symbol=" + m_states[i].symbol
                        + " reason=completed_trade_ledger_not_consumed_within_24h");
               _RemoveStateAt(i);
            }
         }
      }
   }
};

#endif
