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

class CPenaltyWatcher {
private:
   CFileBus *m_bus;
   PenaltyState m_states[];
   string m_invalidation_policy_json;
   datetime m_invalidation_policy_loaded_at;

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
      m_bus.AppendText("logs\\management_experiments.jsonl", experiment + "\n");
   }

public:
   CPenaltyWatcher(CFileBus &bus){
      m_bus=&bus;
      ArrayResize(m_states, 0);
   }

   void RestoreStates(const PenaltyState &states[]){
      int n = ArraySize(states);
      ArrayResize(m_states, n);
      for(int i=0; i<n; i++) m_states[i] = states[i];
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
         double vol = PositionGetDouble(POSITION_VOLUME);
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

         if(sl <= 0 || vol <= 0) continue;
         double risk_dist = MathAbs(entry - sl);
         if(risk_dist <= 0) continue;

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
         }

         st.symbol = sym;
         st.position_identifier = position_identifier;
         st.position_ticket = ticket;
         st.opened_at = opened;
         st.sl = sl;
         st.is_buy = is_buy;
         if(st.risk_dist <= 0) st.risk_dist = risk_dist;
         if(StringLen(st.current_state) == 0) st.current_state = "HEALTHY";
         if(StringLen(st.management_version) == 0) st.management_version = MANAGEMENT_SCHEMA_VERSION;

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
         double mfeR = is_buy ? (st.mfe_price - st.entry)/st.risk_dist : (st.entry - st.mfe_price)/st.risk_dist;

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

         if(desired_state != st.current_state){
            string prior_state = st.current_state;
            datetime evidence_time = (thesis_confirmed && st.confirmed_time > 0 ? st.confirmed_time : now);
            string transition_reason = (desired_state == "HEALTHY" ? "trigger_cleared" : trigger_reason);
            string action_id = _TransitionActionId(st, desired_state, transition_reason, evidence_time);
            st.previous_state = prior_state;
            st.current_state = desired_state;
            st.transition_time = now;
            st.transition_reason = transition_reason;
            st.evidence_snapshot_json = "{" + JsonKVNum("price", px, 8) + ","
                                        + JsonKVNum("cur_r", curR, 6) + ","
                                        + JsonKVNum("mfe_r", mfeR, 6) + ","
                                        + JsonKVBool("raw_thesis_breach", thesis_raw) + ","
                                        + JsonKVBool("confirmed", thesis_confirmed) + ","
                                        + JsonKVStr("confirmation_mode", st.confirmation_mode) + "}";
            st.action_id = action_id;
            st.management_version = MANAGEMENT_SCHEMA_VERSION;
            st.action_executed = "none";

            if(_ActionAlreadyExecuted(st, action_id)){
               st.action_executed = "duplicate_action_skipped";
               _RiskLog("[management_action] position_id=" + IntegerToString(position_identifier)
                        + " action_id=" + action_id + " status=skipped reason=idempotent_duplicate");
            } else if(desired_state == "THESIS_INVALID" &&
                      InpThesisInvalidationPolicy == THESIS_INVALIDATION_ORIGINAL_SL_TP_ONLY){
               st.action_executed = "shadow_original_sl_tp_only";
               _RememberExecutedAction(st, action_id);
            } else if((desired_state == "WARNING" || desired_state == "THESIS_INVALID") &&
                      !_InCooldown(st)){
               st.strikes++;
               st.last_reduction_at = now;
               bool force_full_close = (desired_state == "THESIS_INVALID" &&
                                        InpThesisInvalidationPolicy == THESIS_INVALIDATION_FULL_EXIT);
               if(st.strikes >= close_strikes) force_full_close = true;
               bool action_ok = false;
               if(force_full_close){
                  st.action_executed = "full_close";
                  action_ok = trade.PositionClose(ticket);
               } else {
                  double eff_cut_pct = _ScaledCutPct(MathMax(0.01, cut_pct), st.strikes);
                  VolumeNormalizationResult close_norm = NormalizeClosingVolume(sym,
                                                                                 vol * eff_cut_pct,
                                                                                 vol,
                                                                                 InpClosingVolumeAllowCloseAllBelowMinimum);
                  if(close_norm.action == "close_all"){
                     st.action_executed = "close_all_small_remainder";
                     action_ok = trade.PositionClose(ticket);
                  } else if(close_norm.normalized_volume > 0.0){
                     st.action_executed = "partial_exit";
                     action_ok = trade.PositionClosePartial(ticket, close_norm.normalized_volume);
                  } else {
                     st.action_executed = "closing_volume_skipped";
                     action_ok = false;
                     _RiskLog("[closing_volume] stage=management position_id=" + IntegerToString(position_identifier)
                              + " requested=" + DoubleToString(close_norm.requested_volume, 8)
                              + " normalized=" + DoubleToString(close_norm.normalized_volume, 8)
                              + " residual=" + DoubleToString(close_norm.residual_volume, 8)
                              + " action=" + close_norm.action
                              + " reason=" + close_norm.reason);
                  }
               }
               _RememberExecutedAction(st, action_id);
               _RiskLog("[management_action] position_id=" + IntegerToString(position_identifier)
                        + " action_id=" + action_id
                        + " action=" + st.action_executed
                        + " status=" + (action_ok ? "executed" : "failed_no_retry_same_transition"));
               if(action_ok && (st.action_executed == "full_close" || st.action_executed == "close_all_small_remainder")){
                  st.previous_state = desired_state;
                  st.current_state = "EXITED";
                  st.transition_time = now;
                  st.transition_reason = "management_close_submitted";
               }
            } else if(desired_state == "WARNING" || desired_state == "THESIS_INVALID"){
               st.action_executed = "cooldown_skip_no_retry_same_transition";
               _RememberExecutedAction(st, action_id);
            } else {
               st.action_executed = "none";
               _RememberExecutedAction(st, action_id);
            }
            _LogTransition(st);
         }

         _SaveState(st);
      }

      for(int i=ArraySize(m_states)-1; i>=0; i--){
         if(!_PositionIdentifierStillOpen(m_states[i].position_identifier,
                                          live_position_identifiers)){
            _RemoveStateAt(i);
         }
      }
   }
};

#endif
