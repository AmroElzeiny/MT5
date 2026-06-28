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

   int _FindState(const long ticket, const string symbol){
      for(int i=0; i<ArraySize(m_states); i++){
         if(m_states[i].position_ticket == ticket && m_states[i].symbol == symbol) return i;
      }
      return -1;
   }

   void _SaveState(const PenaltyState &st){
      int idx = _FindState(st.position_ticket, st.symbol);
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

   bool _TicketStillOpen(const long ticket, const long &live_tickets[]){
      for(int i=0; i<ArraySize(live_tickets); i++){
         if(live_tickets[i] == ticket) return true;
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

   string _LegacyTradeSymbolPath(const string symbol){
      return m_bus.LogDir() + "\\trade_" + symbol + ".json";
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
      return true;
   }

   bool _LoadTradeMeta(const ulong ticket, const string symbol, const string comment, TradePlan &p){
      string txt;
      if(ticket > 0 && m_bus.ReadText(_TradeTicketPath(ticket), txt)) return _ParseTradeMeta(txt, p);
      if(StringLen(comment) > 0 && m_bus.ReadText(_TradeKeyPath(comment), txt)) return _ParseTradeMeta(txt, p);
      if(m_bus.ReadText(_LegacyTradeSymbolPath(symbol), txt)) return _ParseTradeMeta(txt, p);
      return false;
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

   void Tick(CTrade &trade){
      datetime now = _NowServerOrTester();
      long live_tickets[];
      ArrayResize(live_tickets, 0);

      // Iterate open positions and apply rules (giveback, MAE, stuck, invalidations).
      for(int i=PositionsTotal()-1; i>=0; i--){
         ulong ticket = PositionGetTicket(i);
         if(!PositionMatchesMagic(ticket)) continue;

         int n_live = ArraySize(live_tickets);
         ArrayResize(live_tickets, n_live+1);
         live_tickets[n_live] = (long)ticket;

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

         int st_idx = _FindState((long)ticket, sym);
         PenaltyState st;
         if(st_idx >= 0){
            st = m_states[st_idx];
         } else {
            ZeroMemory(st);
            st.symbol = sym;
            st.position_ticket = (long)ticket;
            st.opened_at = opened;
            st.entry = entry;
            st.sl = sl;
            st.risk_dist = risk_dist;
            st.is_buy = is_buy;
            st.mfe_price = entry;
            st.mae_price = entry;
            st.strikes = 0;
            st.last_reduction_at = 0;
         }

         st.symbol = sym;
         st.position_ticket = (long)ticket;
         st.opened_at = opened;
         st.sl = sl;
         st.is_buy = is_buy;
         if(st.risk_dist <= 0) st.risk_dist = risk_dist;

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
               double severe_gap = MathMax(eps, st.risk_dist * 0.25);
               if(is_buy) severe_structural_invalid = (structural_level > 0 && px < (structural_level - severe_gap));
               else       severe_structural_invalid = (structural_level > 0 && px > (structural_level + severe_gap));
               if(curR <= -1.05) severe_structural_invalid = true;
            }
         }

         // ---- Penalty triggers ----
         bool trigger_cut=false;
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
            cut_pct = MathMax(cut_pct, 0.25);
            if(StringLen(trigger_reason) == 0) trigger_reason = "mae_trigger";
         }

         // Stuck trigger
         if(penalty_time_ok && mins_open >= stuck_minutes && mfeR < stuck_min_mfe_r){
            trigger_cut=true;
            cut_pct = MathMax(cut_pct, 0.25);
            if(StringLen(trigger_reason) == 0) trigger_reason = "stuck_no_mfe";
         }

         // Giveback trigger (approx using current R as both, unless you persist MFE)
         if(penalty_time_ok && mfeR >= giveback_trigger_r && curR <= giveback_floor_r){
            trigger_cut=true;
            cut_pct = MathMax(cut_pct, 0.25);
            if(StringLen(trigger_reason) == 0) trigger_reason = "giveback";
         }

         if(trigger_cut && !_InCooldown(st)){
            st.strikes++;
            st.last_reduction_at = now;

            if(st.strikes >= close_strikes){
               _SaveState(st);
               bool close_ok = trade.PositionClose(ticket);
               _RiskLog(sym + " penalty close"
                        + " ticket=" + IntegerToString((int)ticket)
                        + " reason=" + trigger_reason
                        + " strikes=" + IntegerToString(st.strikes)
                        + " mins_open=" + IntegerToString(mins_open)
                        + " curR=" + DoubleToString(curR, 2)
                        + " mfeR=" + DoubleToString(mfeR, 2)
                        + " ok=" + (close_ok ? "true" : "false"));
               continue;
            }

            // partial reduction
            double eff_cut_pct = _ScaledCutPct(cut_pct, st.strikes);
            double vol_cut = _ClampVolToStep(sym, vol * eff_cut_pct);
            double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
             bool action_ok = false;
             string action = "partial";
             if(vol_cut < vmin || vol - vol_cut < vmin){
                // if too small after cut, just close
                action = "close_small_remainder";
                action_ok = trade.PositionClose(ticket);
             } else {
                action_ok = trade.PositionClosePartial(ticket, vol_cut);
             }
             _RiskLog(sym + " penalty reduction"
                      + " ticket=" + IntegerToString((int)ticket)
                      + " action=" + action
                      + " reason=" + trigger_reason
                      + " strikes=" + IntegerToString(st.strikes)
                      + " mins_open=" + IntegerToString(mins_open)
                      + " cut_pct=" + DoubleToString(eff_cut_pct, 2)
                      + " curR=" + DoubleToString(curR, 2)
                      + " mfeR=" + DoubleToString(mfeR, 2)
                      + " ok=" + (action_ok ? "true" : "false"));
           }

         _SaveState(st);
      }

      for(int i=ArraySize(m_states)-1; i>=0; i--){
         if(!_TicketStillOpen(m_states[i].position_ticket, live_tickets)){
            _RemoveStateAt(i);
         }
      }
   }
};

#endif
