//+------------------------------------------------------------------+
//| Risk.mqh - sizing, exposure caps, kill-switch                      |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_RISK_MQH__
#define __PO3_AIGATE_RISK_MQH__
#include "Config.mqh"
#include "JsonLite.mqh"

void _RiskLog(const string msg) {
   if(!InpVerboseJournal) return;
   if(InpJournalTesterOnly && !MQLInfoInteger(MQL_TESTER)) return;
   Print("[PO3_AIGate] ", msg);
}

struct VolumeNormalizationResult {
   double requested_volume;
   double normalized_volume;
   double residual_volume;
   double broker_min;
   double broker_step;
   double broker_max;
   string action;
   string reason;
};

double _VolumeFloorToStep(const double volume, const double step) {
   if(step <= 0.0 || volume <= 0.0) return 0.0;
   return MathFloor((volume + step * 1e-10) / step) * step;
}

VolumeNormalizationResult NormalizeOpeningVolume(const string symbol,
                                                  const double requested_volume,
                                                  const bool minimum_risk_valid) {
   VolumeNormalizationResult out;
   ZeroMemory(out);
   out.requested_volume = requested_volume;
   out.broker_min = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   out.broker_max = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   out.broker_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   out.action = "reject";
   out.reason = "invalid_volume_contract";
   if(requested_volume <= 0.0 || out.broker_min <= 0.0 || out.broker_max < out.broker_min || out.broker_step <= 0.0)
      return out;

   double normalized = MathMin(out.broker_max, _VolumeFloorToStep(requested_volume, out.broker_step));
   if(normalized < out.broker_min){
      if(!minimum_risk_valid){
         out.reason = "minimum_volume_would_exceed_risk";
         return out;
      }
      normalized = out.broker_min;
      out.action = "raise_to_minimum";
      out.reason = "broker_minimum_and_risk_valid";
   } else {
      out.action = "open";
      out.reason = "normalized_down_to_step";
   }
   out.normalized_volume = normalized;
   return out;
}

VolumeNormalizationResult NormalizeClosingVolume(const string symbol,
                                                  const double requested_volume,
                                                  const double current_volume,
                                                  const bool allow_close_all_below_minimum) {
   VolumeNormalizationResult out;
   ZeroMemory(out);
   out.requested_volume = requested_volume;
   out.broker_min = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   out.broker_max = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   out.broker_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   out.residual_volume = current_volume;
   out.action = "skip";
   out.reason = "invalid_or_zero_close_volume";
   if(requested_volume <= 0.0 || current_volume <= 0.0 || out.broker_min <= 0.0 || out.broker_step <= 0.0)
      return out;

   double bounded = MathMin(requested_volume, current_volume);
   if(bounded >= current_volume - out.broker_step * 1e-8){
      out.normalized_volume = current_volume;
      out.residual_volume = 0.0;
      out.action = "close_all";
      out.reason = "requested_full_close";
      return out;
   }

   double normalized = MathMin(current_volume, _VolumeFloorToStep(bounded, out.broker_step));
   if(normalized < out.broker_min){
      if(allow_close_all_below_minimum){
         out.normalized_volume = current_volume;
         out.residual_volume = 0.0;
         out.action = "close_all";
         out.reason = "explicit_below_minimum_close_all_policy";
      } else {
         out.reason = "requested_close_below_broker_minimum";
      }
      return out;
   }

   double residual = MathMax(0.0, current_volume - normalized);
   if(residual > 0.0 && residual < out.broker_min){
      double reduced = _VolumeFloorToStep(MathMax(0.0, current_volume - out.broker_min), out.broker_step);
      if(reduced < out.broker_min){
         if(allow_close_all_below_minimum){
            out.normalized_volume = current_volume;
            out.residual_volume = 0.0;
            out.action = "close_all";
            out.reason = "explicit_tiny_residual_close_all_policy";
         } else {
            out.reason = "partial_close_would_leave_tiny_residual";
         }
         return out;
      }
      normalized = MathMin(normalized, reduced);
      residual = MathMax(0.0, current_volume - normalized);
   }
   out.normalized_volume = MathMin(normalized, requested_volume);
   out.residual_volume = residual;
   out.action = "partial_close";
   out.reason = "floored_to_step";
   return out;
}

// Migration alias for opening paths only. Closing paths must call
// NormalizeClosingVolume so a partial close is never rounded upward.
double _ClampVolToStep(const string symbol, double vol) {
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   bool min_risk_valid = (vmin > 0.0 && vol >= vmin);
   return NormalizeOpeningVolume(symbol, vol, min_risk_valid).normalized_volume;
}

double _RiskMoneyForPosition(const string symbol, const bool is_buy, const double volume, const double entry, const double sl) {
   double profit = 0.0;
   ENUM_ORDER_TYPE typ = is_buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcProfit(typ, symbol, volume, entry, sl, profit)) return 0.0;
   return MathAbs(profit);
}

double CalcDesiredRiskMoney() {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(InpRiskPerTradeMoney > 0) return InpRiskPerTradeMoney;
   if(InpRiskPerTradePct > 0) {
      double pct = InpRiskPerTradePct;
      if(pct > InpMaxRiskPerTradePct || pct > 10.0){
         _RiskLog("risk input rejected: InpRiskPerTradePct=" + DoubleToString(pct, 4)
                  + " exceeds sanity guard max_pct=" + DoubleToString(InpMaxRiskPerTradePct, 2));
         return 0.0;
      }
      if(pct > 0.0 && pct < 0.05){
         _RiskLog("risk input notice: InpRiskPerTradePct is now a percent, not a fraction; effective_pct="
                  + DoubleToString(pct, 4));
      }
      return eq * (pct / 100.0);
   }
   return 0.0;
}

bool PositionMatchesMagic(const ulong ticket) {
   if(ticket == 0) return false;
   if(!PositionSelectByTicket(ticket)) return false;
   return ((ulong)PositionGetInteger(POSITION_MAGIC) == InpMagicNumber);
}

bool OrderMatchesMagic(const ulong ticket) {
   if(ticket == 0) return false;
   if(!OrderSelect(ticket)) return false;
   return ((ulong)OrderGetInteger(ORDER_MAGIC) == InpMagicNumber);
}

datetime _StartOfDay(const datetime t) {
   MqlDateTime ts;
   TimeToStruct(t, ts);
   ts.hour = 0;
   ts.min = 0;
   ts.sec = 0;
   return StructToTime(ts);
}

string _DailyAnchorKey(const string suffix) {
   return "PO3_AI_" + IntegerToString((int)InpMagicNumber) + "_" + suffix;
}

string _BehaviorStopKey() {
   return _DailyAnchorKey("behavior_stop_until");
}

datetime _BehaviorStopUntil() {
   string key = _BehaviorStopKey();
   if(!GlobalVariableCheck(key)) return 0;
   return (datetime)GlobalVariableGet(key);
}

void _StoreBehaviorStopUntil(const datetime until) {
   GlobalVariableSet(_BehaviorStopKey(), (double)until);
}

bool _ReadCommonText(const string rel_path, string &out) {
   int h = FileOpen(rel_path, FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return false;
   out = "";
   while(!FileIsEnding(h))
      out += FileReadString(h);
   FileClose(h);
   return true;
}

void _InsertRecentBehaviorTrade(const datetime closed_at, const double realized_r, const double fill_slippage_r,
                                const int limit, datetime &times[], double &rs[], double &slips[]) {
   if(limit <= 0 || closed_at <= 0) return;
   int n = ArraySize(times);
   int pos = n;
   for(int i=0; i<n; i++){
      if(closed_at > times[i]){
         pos = i;
         break;
      }
   }
   if(pos >= limit && n >= limit) return;

   int new_size = MathMin(limit, n + 1);
   ArrayResize(times, new_size);
   ArrayResize(rs, new_size);
   ArrayResize(slips, new_size);
   for(int i=new_size-1; i>pos; i--){
      times[i] = times[i-1];
      rs[i] = rs[i-1];
      slips[i] = slips[i-1];
   }
   if(pos >= new_size) return;
   times[pos] = closed_at;
   rs[pos] = realized_r;
   slips[pos] = fill_slippage_r;
}

void _LoadRecentBehaviorTrades(const int limit, datetime &times[], double &rs[], double &slips[]) {
   ArrayResize(times, 0);
   ArrayResize(rs, 0);
   ArrayResize(slips, 0);
   if(limit <= 0) return;

   string file_name = "";
   long handle = FileFindFirst(InpBusRoot + "\\logs\\trade_results\\trade_result_*.json", file_name, FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   do {
      string txt;
      string rel = InpBusRoot + "\\logs\\trade_results\\" + file_name;
      if(!_ReadCommonText(rel, txt)) continue;
      datetime closed_at = (datetime)(int)JsonGetNumber(txt, "closed_at", 0);
      double realized_r = JsonGetNumber(txt, "realized_r", 0.0);
      double fill_slippage_r = JsonGetNumber(txt, "fill_slippage_r", 0.0);
      _InsertRecentBehaviorTrade(closed_at, realized_r, fill_slippage_r, limit, times, rs, slips);
   } while(FileFindNext(handle, file_name));
   FileFindClose(handle);
}

bool _BehaviorStopTriggered(string &reason) {
   reason = "";
   int loss_limit = MathMax(0, InpStopAfterConsecutiveLosses);
   int slip_limit = MathMax(0, InpStopAfterBadSlippageCount);
   if(loss_limit <= 0 && slip_limit <= 0) return false;

   int inspect = MathMax(loss_limit, slip_limit);
   inspect = MathMax(inspect, 3);
   inspect += 4;

   datetime times[];
   double rs[];
   double slips[];
   _LoadRecentBehaviorTrades(inspect, times, rs, slips);
   int n = ArraySize(times);
   if(n <= 0) return false;

   if(loss_limit > 0){
      int consec_losses = 0;
      for(int i=0; i<n; i++){
         if(rs[i] < 0.0) consec_losses++;
         else break;
      }
      if(consec_losses >= loss_limit){
         reason = "consecutive_losses=" + IntegerToString(consec_losses);
         return true;
      }
   }

   if(slip_limit > 0){
      int bad_slips = 0;
      for(int i=0; i<n; i++){
         if(slips[i] >= InpBadSlippageRFloor) bad_slips++;
         else if(slips[i] > 0.0) break;
      }
      if(bad_slips >= slip_limit){
         reason = "bad_slippage_streak=" + IntegerToString(bad_slips)
                  + " floor_r=" + DoubleToString(InpBadSlippageRFloor, 2);
         return true;
      }
   }

   return false;
}

void _LoadDailyAnchor(datetime &day_anchor, double &balance_anchor, double &equity_anchor) {
   string day_key = _DailyAnchorKey("day");
   string bal_key = _DailyAnchorKey("bal");
   string eq_key = _DailyAnchorKey("eq");

   if(GlobalVariableCheck(day_key)) day_anchor = (datetime)GlobalVariableGet(day_key);
   else day_anchor = 0;
   if(GlobalVariableCheck(bal_key)) balance_anchor = GlobalVariableGet(bal_key);
   else balance_anchor = 0.0;
   if(GlobalVariableCheck(eq_key)) equity_anchor = GlobalVariableGet(eq_key);
   else equity_anchor = 0.0;
}

void _StoreDailyAnchor(const datetime day_anchor, const double balance_anchor, const double equity_anchor) {
   GlobalVariableSet(_DailyAnchorKey("day"), (double)day_anchor);
   GlobalVariableSet(_DailyAnchorKey("bal"), balance_anchor);
   GlobalVariableSet(_DailyAnchorKey("eq"), equity_anchor);
}

double _DailyLossCapMoney(const double balance_anchor, const double equity_anchor) {
   if(InpDailyLossCapMoney > 0) return InpDailyLossCapMoney;
   if(InpDailyLossCapPct > 0){
      double anchor = MathMax(balance_anchor, equity_anchor);
      return anchor * (InpDailyLossCapPct / 100.0);
   }
   return 0.0;
}

bool _ParseServerClockMinute(const string raw_value, int &minute_of_day) {
   minute_of_day = -1;
   string value = raw_value;
   StringTrimLeft(value);
   StringTrimRight(value);
   if(StringLen(value) <= 0) return false;

   int colon = StringFind(value, ":");
   if(colon < 0) return false;

   int hour = (int)StringToInteger(StringSubstr(value, 0, colon));
   int minute = (int)StringToInteger(StringSubstr(value, colon + 1));
   if(hour < 0 || hour > 23 || minute < 0 || minute > 59) return false;

   minute_of_day = hour * 60 + minute;
   return true;
}

int _ServerMinuteOfDay(const datetime now) {
   MqlDateTime ts;
   TimeToStruct(now, ts);
   return ts.hour * 60 + ts.min;
}

bool _MinuteInsideDailyWindow(const int minute, const int start_minute, const int end_minute) {
   if(start_minute == end_minute) return false;
   if(start_minute < end_minute)
      return (minute >= start_minute && minute < end_minute);
   return (minute >= start_minute || minute < end_minute);
}

string _ClockMinuteLabel(const int minute_of_day) {
   int normalized = minute_of_day % 1440;
   if(normalized < 0) normalized += 1440;
   int hour = normalized / 60;
   int minute = normalized % 60;
   return StringFormat("%02d:%02d", hour, minute);
}

bool PO3TradingFreezeActive(const datetime now, string &reason) {
   reason = "";
   if(!InpRolloverProtectionEnable) return false;

   int start_minute = -1, end_minute = -1;
   if(!_ParseServerClockMinute(InpTradingFreezeStartServerTime, start_minute) ||
      !_ParseServerClockMinute(InpTradingFreezeEndServerTime, end_minute)){
      reason = "invalid_trading_freeze_time_input";
      return false;
   }

   int minute = _ServerMinuteOfDay(now);
   if(!_MinuteInsideDailyWindow(minute, start_minute, end_minute)) return false;

   reason = "server_time_trading_freeze " + _ClockMinuteLabel(start_minute)
            + "-" + _ClockMinuteLabel(end_minute);
   return true;
}

bool PO3PreCloseFlattenActive(const datetime now, string &reason) {
   reason = "";
   if(!InpRolloverProtectionEnable) return false;

   int minutes_before = MathMax(0, InpCloseManagedTradesBeforeMarketCloseMin);
   if(minutes_before <= 0) return false;
   minutes_before = MathMin(minutes_before, 1439);

   int close_minute = -1;
   if(!_ParseServerClockMinute(InpServerMarketCloseTime, close_minute)){
      reason = "invalid_server_market_close_time_input";
      return false;
   }

   int start_minute = close_minute - minutes_before;
   while(start_minute < 0) start_minute += 1440;
   start_minute %= 1440;

   int minute = _ServerMinuteOfDay(now);
   if(!_MinuteInsideDailyWindow(minute, start_minute, close_minute)) return false;

   reason = "server_time_pre_close_flatten close=" + _ClockMinuteLabel(close_minute)
            + " minutes_before=" + IntegerToString(minutes_before);
   return true;
}

bool PO3EntryBlockedByRollover(const datetime now, string &reason) {
   if(PO3PreCloseFlattenActive(now, reason)) return true;
   if(PO3TradingFreezeActive(now, reason)) return true;
   reason = "";
   return false;
}

bool _DeleteManagedPendingOrders(CTrade &trade, const string reason) {
   bool acted = false;
   for(int i=OrdersTotal()-1; i>=0; i--){
      ulong ticket = OrderGetTicket(i);
      if(!OrderMatchesMagic(ticket)) continue;
      string sym = OrderGetString(ORDER_SYMBOL);
      bool ok = trade.OrderDelete(ticket);
      _RiskLog(sym + " pending order delete"
               + " ticket=" + IntegerToString((int)ticket)
               + " reason=" + reason
               + " ok=" + (ok ? "true" : "false"));
      acted = true;
   }
   return acted;
}

bool _FlattenManagedExposureWithReason(CTrade &trade, const string reason) {
   bool acted = false;
   for(int i=PositionsTotal()-1; i>=0; i--){
      ulong ticket = PositionGetTicket(i);
      if(!PositionMatchesMagic(ticket)) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      bool ok = trade.PositionClose(ticket);
      _RiskLog(sym + " position close"
               + " ticket=" + IntegerToString((int)ticket)
               + " reason=" + reason
               + " ok=" + (ok ? "true" : "false"));
      acted = true;
   }
   if(_DeleteManagedPendingOrders(trade, reason)) acted = true;
   return acted;
}

bool _FlattenManagedExposure(CTrade &trade) {
   return _FlattenManagedExposureWithReason(trade, "global_stop");
}

bool EnforceGlobalStops(CTrade &trade) {
   datetime now = TimeTradeServer();
   if(now <= 0) now = TimeLocal();

   datetime behavior_stop_until = _BehaviorStopUntil();
   if(behavior_stop_until > now){
      _RiskLog("behavioral stop active until=" + TimeToString(behavior_stop_until, TIME_DATE|TIME_MINUTES));
      _FlattenManagedExposure(trade);
      return true;
   }

   datetime day_anchor = 0;
   double balance_anchor = 0.0, equity_anchor = 0.0;
   _LoadDailyAnchor(day_anchor, balance_anchor, equity_anchor);
   datetime today = _StartOfDay(now);
   if(day_anchor != today || balance_anchor <= 0.0 || equity_anchor <= 0.0){
      balance_anchor = AccountInfoDouble(ACCOUNT_BALANCE);
      equity_anchor = AccountInfoDouble(ACCOUNT_EQUITY);
      day_anchor = today;
      _StoreDailyAnchor(day_anchor, balance_anchor, equity_anchor);
   }

   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);

   bool hit_absolute =
      (InpStopTradingEnable &&
       ((InpStopAtEquityBelow > 0 && eq <= InpStopAtEquityBelow) ||
        (InpStopAtBalanceBelow > 0 && bal <= InpStopAtBalanceBelow)));

   double daily_cap = _DailyLossCapMoney(balance_anchor, equity_anchor);
   bool hit_daily = false;
   if(daily_cap > 0){
      double anchor = MathMax(balance_anchor, equity_anchor);
      hit_daily = ((anchor - eq) >= daily_cap);
   }

   string behavior_reason = "";
   bool hit_behavioral = _BehaviorStopTriggered(behavior_reason);
   if(hit_behavioral){
      behavior_stop_until = now + MathMax(1, InpBehaviorStopCooldownMin) * 60;
      _StoreBehaviorStopUntil(behavior_stop_until);
   }

   if(hit_absolute || hit_daily || hit_behavioral) {
      if(hit_daily){
         double anchor = MathMax(balance_anchor, equity_anchor);
         double loss_pct = (anchor > 0.0 ? ((anchor - eq) / anchor) * 100.0 : 0.0);
         _RiskLog("[daily_loss_block] loss_pct=" + DoubleToString(loss_pct, 4)
                  + " cap_pct=" + DoubleToString(InpDailyLossCapPct, 4));
      }
      _RiskLog("global stop triggered absolute=" + (hit_absolute ? "true" : "false")
               + " daily=" + (hit_daily ? "true" : "false")
               + " behavioral=" + (hit_behavioral ? "true" : "false")
               + " behavioral_reason=" + behavior_reason
               + " balance=" + DoubleToString(bal, 2)
               + " equity=" + DoubleToString(eq, 2));
      _FlattenManagedExposure(trade);
      return true;
   }
   return false;
}

bool _ReadExactOriginalRiskMeta(const ulong position_ticket,
                                const long position_identifier,
                                const string symbol,
                                const string comment,
                                double &original_risk_per_lot,
                                double &original_entry,
                                double &original_sl,
                                string &reason) {
   original_risk_per_lot = 0.0;
   original_entry = 0.0;
   original_sl = 0.0;
   reason = "";
   string txt = "";
   string ticket_path = InpBusRoot + "\\logs\\trade_ticket_" + IntegerToString((long)position_ticket) + ".json";
   bool loaded = _ReadCommonText(ticket_path, txt);
   if(!loaded && StringLen(comment) > 0)
      loaded = _ReadCommonText(InpBusRoot + "\\logs\\trade_key_" + comment + ".json", txt);
   if(!loaded){ reason = "original_risk_metadata_missing"; return false; }
   if(JsonGetString(txt, "ledger_schema_version", "") != TRADE_LEDGER_SCHEMA_VERSION){
      reason = "original_risk_metadata_schema_incompatible";
      return false;
   }
   if(!JsonGetBool(txt, "execution_identity_verified", false) ||
      JsonGetBool(txt, "execution_identity_quarantined", true)){
      reason = "original_risk_metadata_identity_unverified";
      return false;
   }
   if(JsonGetString(txt, "symbol", "") != symbol ||
      (ulong)JsonGetNumber(txt, "broker_position_ticket", 0.0) != position_ticket ||
      (long)JsonGetNumber(txt, "broker_position_identifier", 0.0) != position_identifier){
      reason = "original_risk_metadata_identity_mismatch";
      return false;
   }
   original_risk_per_lot = JsonGetNumber(txt, "original_initial_risk_money_per_lot", 0.0);
   original_entry = JsonGetNumber(txt, "original_entry_for_risk", 0.0);
   original_sl = JsonGetNumber(txt, "original_sl_for_risk", 0.0);
   if(original_risk_per_lot <= 0.0 || original_entry <= 0.0 || original_sl <= 0.0){
      reason = "original_risk_metadata_incomplete";
      return false;
   }
   return true;
}

bool CurrentInitialRiskBreakdown(double &open_risk_money,
                                 double &pending_risk_money,
                                 string &reason) {
   open_risk_money = 0.0;
   pending_risk_money = 0.0;
   reason = "";
   for(int i=0; i<PositionsTotal(); i++){
      ulong ticket = PositionGetTicket(i);
      if(!PositionMatchesMagic(ticket)) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      string comment = PositionGetString(POSITION_COMMENT);
      double vol = PositionGetDouble(POSITION_VOLUME);
      long position_identifier = (long)PositionGetInteger(POSITION_IDENTIFIER);
      double per_lot = 0.0, original_entry = 0.0, original_sl = 0.0;
      string meta_reason = "";
      if(!_ReadExactOriginalRiskMeta(ticket, position_identifier, sym, comment,
                                    per_lot, original_entry, original_sl, meta_reason)){
         reason = meta_reason + " symbol=" + sym + " ticket=" + IntegerToString((long)ticket);
         return false;
      }
      open_risk_money += MathMax(0.0, vol) * per_lot;
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
      double entry = OrderGetDouble(ORDER_PRICE_OPEN);
      double sl = OrderGetDouble(ORDER_SL);
      double vol = OrderGetDouble(ORDER_VOLUME_CURRENT);
      if(entry <= 0 || sl <= 0 || vol <= 0){
         reason = "pending_original_risk_inputs_invalid ticket=" + IntegerToString((long)ticket);
         return false;
      }
      double pending_risk = _RiskMoneyForPosition(sym, is_pending_buy, vol, entry, sl);
      if(pending_risk <= 0.0){
         reason = "pending_original_risk_calculation_failed ticket=" + IntegerToString((long)ticket);
         return false;
      }
      pending_risk_money += pending_risk;
   }
   return true;
}

double _AggregateRiskBaseMoney() {
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(equity <= 0.0) return balance;
   if(balance <= 0.0) return equity;
   return MathMin(equity, balance);
}

bool EffectiveAggregateRiskCap(double &cap_money,
                               double &pct_cap_money,
                               string &cap_source,
                               string &reason) {
   cap_money = 0.0;
   pct_cap_money = 0.0;
   cap_source = "none";
   reason = "";
   if(!InpMaxTotalRiskEnable){ reason = "aggregate_risk_cap_disabled"; return false; }
   double base_money = _AggregateRiskBaseMoney();
   bool pct_valid = (InpMaxTotalRiskPct > 0.0 && base_money > 0.0);
   bool money_valid = (InpMaxTotalRiskMoney > 0.0);
   if(pct_valid) pct_cap_money = base_money * InpMaxTotalRiskPct / 100.0;
   if(!pct_valid && !money_valid){ reason = "no_valid_aggregate_risk_cap"; return false; }
   if(pct_valid && money_valid){
      cap_money = MathMin(pct_cap_money, InpMaxTotalRiskMoney);
      cap_source = (pct_cap_money <= InpMaxTotalRiskMoney ? "percentage" : "money");
   } else if(pct_valid){
      cap_money = pct_cap_money;
      cap_source = "percentage";
   } else {
      cap_money = InpMaxTotalRiskMoney;
      cap_source = "money";
   }
   return (cap_money > 0.0);
}

bool PortfolioInitialRiskGate(const double proposed_risk_money,
                              double &open_risk_money,
                              double &pending_risk_money,
                              double &post_trade_risk_money,
                              double &post_trade_risk_pct,
                              double &effective_cap_money,
                              string &reason) {
   open_risk_money = 0.0;
   pending_risk_money = 0.0;
   post_trade_risk_money = 0.0;
   post_trade_risk_pct = 0.0;
   effective_cap_money = 0.0;
   reason = "";
   if(!InpMaxTotalRiskEnable) return true;
   string breakdown_reason = "";
   if(!CurrentInitialRiskBreakdown(open_risk_money, pending_risk_money, breakdown_reason)){
      reason = "aggregate_initial_risk_unavailable:" + breakdown_reason;
      return false;
   }
   double pct_cap_money = 0.0;
   string cap_source = "";
   string cap_reason = "";
   if(!EffectiveAggregateRiskCap(effective_cap_money, pct_cap_money, cap_source, cap_reason)){
      reason = cap_reason;
      return false;
   }
   post_trade_risk_money = open_risk_money + pending_risk_money + MathMax(0.0, proposed_risk_money);
   double base_money = _AggregateRiskBaseMoney();
   post_trade_risk_pct = (base_money > 0.0 ? post_trade_risk_money / base_money * 100.0 : 0.0);
   if(post_trade_risk_money > effective_cap_money + 0.0000001){
      reason = "aggregate_initial_risk_cap_exceeded:" + cap_source;
      return false;
   }
   return true;
}

double CurrentTotalRiskMoney() {
   double open_risk = 0.0, pending_risk = 0.0;
   string reason = "";
   if(!CurrentInitialRiskBreakdown(open_risk, pending_risk, reason)) return DBL_MAX;
   return open_risk + pending_risk;
}

// Find a volume such that risk(sl) ~= target_risk (binary search)
double CalcVolumeForRisk(const string symbol, const bool is_buy, const double entry, const double sl, const double target_risk_money) {
   if(target_risk_money <= 0) return 0.0;
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(vmin <= 0 || vmax <= 0) return 0.0;

   // risk for min vol
   double rmin = _RiskMoneyForPosition(symbol, is_buy, vmin, entry, sl);
   if(rmin <= 0) return 0.0;
   if(rmin > target_risk_money) return 0.0;

   double lo=vmin, hi=vmax;
   for(int it=0; it<25; it++){
      double mid = (lo+hi)/2.0;
      double r = _RiskMoneyForPosition(symbol, is_buy, mid, entry, sl);
      if(r <= 0) { hi = mid; continue; }
      if(r > target_risk_money) hi = mid;
      else lo = mid;
   }
   VolumeNormalizationResult normalized = NormalizeOpeningVolume(symbol, lo, true);
   if(normalized.normalized_volume <= 0.0) return 0.0;
   double normalized_risk = _RiskMoneyForPosition(symbol, is_buy, normalized.normalized_volume, entry, sl);
   if(normalized_risk <= 0.0 || normalized_risk > target_risk_money + 0.0000001) return 0.0;
   return normalized.normalized_volume;
}

bool HasCapacityForNewTrade(const double new_trade_risk_money, double &risk_cap_remaining) {
   if(!InpMaxTotalRiskEnable) { risk_cap_remaining = 1e18; return true; }
   double open_risk = 0.0, pending_risk = 0.0, post_risk = 0.0, post_pct = 0.0, cap_money = 0.0;
   string reason = "";
   bool pass = PortfolioInitialRiskGate(new_trade_risk_money,
                                        open_risk,
                                        pending_risk,
                                        post_risk,
                                        post_pct,
                                        cap_money,
                                        reason);
   risk_cap_remaining = MathMax(0.0, cap_money - open_risk - pending_risk);
   if(!pass)
      _RiskLog("[portfolio_initial_risk] open_risk_money=" + DoubleToString(open_risk, 2)
               + " open_risk_pct=" + DoubleToString((_AggregateRiskBaseMoney() > 0.0 ? open_risk / _AggregateRiskBaseMoney() * 100.0 : 0.0), 4)
               + " pending_risk_money=" + DoubleToString(pending_risk, 2)
               + " proposed_risk_money=" + DoubleToString(new_trade_risk_money, 2)
               + " post_trade_risk_pct=" + DoubleToString(post_pct, 4)
               + " cap_pct=" + DoubleToString(InpMaxTotalRiskPct, 4)
               + " cap_money=" + DoubleToString(cap_money, 2)
               + " status=blocked reason=" + reason);
   return pass;
}

string _RiskAssetClassForSymbol(const string symbol) {
   string value = symbol;
   StringToUpper(value);
   if(StringFind(value, "XAU") >= 0 || StringFind(value, "GOLD") >= 0 ||
      StringFind(value, "XAG") >= 0 || StringFind(value, "SILVER") >= 0) return "metals";
   if(StringFind(value, "WTI") >= 0 || StringFind(value, "BRENT") >= 0 ||
      StringFind(value, "OIL") >= 0 || StringFind(value, "NGAS") >= 0) return "energy";
   if(StringFind(value, "BTC") >= 0 || StringFind(value, "ETH") >= 0 ||
      StringFind(value, "SOL") >= 0) return "crypto";
   if(StringFind(value, "US30") >= 0 || StringFind(value, "NAS") >= 0 ||
      StringFind(value, "SPX") >= 0 || StringFind(value, "GER") >= 0 ||
      StringFind(value, "DAX") >= 0 || StringFind(value, "UK100") >= 0 ||
      StringFind(value, "JP225") >= 0) return "indices";
   string letters = "";
   for(int i=0; i<StringLen(value); i++){
      ushort c = (ushort)StringGetCharacter(value, i);
      if(c >= 'A' && c <= 'Z') letters += StringSubstr(value, i, 1);
   }
   if(StringLen(letters) >= 6) return "fx";
   return "other";
}

void _SortDoubleAscending(double &values[]) {
   for(int i=1; i<ArraySize(values); i++){
      double key = values[i];
      int j = i - 1;
      while(j >= 0 && values[j] > key){
         values[j+1] = values[j];
         j--;
      }
      values[j+1] = key;
   }
}

double BrokerCostEstimatePerLot(const string symbol,
                                string &source,
                                int &sample_size,
                                double &median_cost,
                                datetime &window_start,
                                datetime &window_end) {
   source = "configured_fallback";
   sample_size = 0;
   median_cost = 0.0;
   window_start = 0;
   window_end = 0;
   double fallback = MathMax(0.00000001,
                             InpCommissionPerLotRoundTurn > 0.0
                             ? InpCommissionPerLotRoundTurn
                             : InpCommissionFallbackPerLotRoundTurn);
   if(!InpBrokerCostHistoryEnable) return fallback;

   datetime now = TimeTradeServer();
   if(now <= 0) now = TimeCurrent();
   datetime from = now - MathMax(1, InpBrokerCostHistoryDays) * 86400;
   if(!HistorySelect(from, now)) return fallback;

   long position_ids[];
   double entry_volumes[];
   double costs[];
   datetime first_times[];
   datetime last_times[];
   ArrayResize(position_ids, 0);
   int deals = HistoryDealsTotal();
   for(int i=0; i<deals; i++){
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != symbol) continue;
      long position_id = (long)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      if(position_id <= 0) continue;
      int idx = -1;
      for(int k=0; k<ArraySize(position_ids); k++) if(position_ids[k] == position_id){ idx = k; break; }
      if(idx < 0){
         idx = ArraySize(position_ids);
         ArrayResize(position_ids, idx+1);
         ArrayResize(entry_volumes, idx+1);
         ArrayResize(costs, idx+1);
         ArrayResize(first_times, idx+1);
         ArrayResize(last_times, idx+1);
         position_ids[idx] = position_id;
         entry_volumes[idx] = 0.0;
         costs[idx] = 0.0;
         first_times[idx] = 0;
         last_times[idx] = 0;
      }
      ENUM_DEAL_ENTRY entry_kind = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry_kind == DEAL_ENTRY_IN || entry_kind == DEAL_ENTRY_INOUT)
         entry_volumes[idx] += MathMax(0.0, HistoryDealGetDouble(deal, DEAL_VOLUME));
      costs[idx] += MathAbs(HistoryDealGetDouble(deal, DEAL_COMMISSION));
      costs[idx] += MathAbs(HistoryDealGetDouble(deal, DEAL_SWAP));
      costs[idx] += MathAbs(HistoryDealGetDouble(deal, DEAL_FEE));
      datetime deal_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      if(first_times[idx] <= 0 || deal_time < first_times[idx]) first_times[idx] = deal_time;
      if(deal_time > last_times[idx]) last_times[idx] = deal_time;
   }

   double per_lot[];
   ArrayResize(per_lot, 0);
   for(int i=0; i<ArraySize(position_ids); i++){
      if(entry_volumes[i] <= 0.0 || costs[i] <= 0.0) continue;
      int n = ArraySize(per_lot);
      ArrayResize(per_lot, n+1);
      per_lot[n] = costs[i] / entry_volumes[i];
      if(window_start <= 0 || first_times[i] < window_start) window_start = first_times[i];
      if(last_times[i] > window_end) window_end = last_times[i];
   }
   sample_size = ArraySize(per_lot);
   if(sample_size < MathMax(1, InpBrokerCostMinSamples)) return fallback;
   _SortDoubleAscending(per_lot);
   if(sample_size % 2 == 1) median_cost = per_lot[sample_size/2];
   else median_cost = (per_lot[sample_size/2 - 1] + per_lot[sample_size/2]) * 0.5;
   double pct = MathMax(0.50, MathMin(0.999, InpBrokerCostStressedPercentile));
   int stressed_index = (int)MathCeil(pct * (sample_size - 1));
   stressed_index = MathMax(0, MathMin(sample_size - 1, stressed_index));
   source = "broker_history_stressed_percentile";
   return MathMax(0.00000001, per_lot[stressed_index]);
}

string _RiskCommentSession(const string comment) {
   int first = StringFind(comment, "-");
   if(first < 0) return "OFF";
   int second = StringFind(comment, "-", first + 1);
   if(second < 0) return "OFF";
   string value = StringSubstr(comment, first + 1, second - first - 1);
   return (StringLen(value) > 0 ? value : "OFF");
}

void _AccumulateNamedRisk(const string factor,
                          const double risk_pct,
                          string &names[],
                          double &values[]) {
   if(StringLen(factor) == 0 || risk_pct <= 0.0) return;
   for(int i=0; i<ArraySize(names); i++){
      if(names[i] == factor){ values[i] += risk_pct; return; }
   }
   int n = ArraySize(names);
   ArrayResize(names, n+1);
   ArrayResize(values, n+1);
   names[n] = factor;
   values[n] = risk_pct;
}

string _SymbolLetters(const string symbol) {
   string upper = symbol;
   StringToUpper(upper);
   string letters = "";
   for(int i=0; i<StringLen(upper); i++){
      ushort c = (ushort)StringGetCharacter(upper, i);
      if(c >= 'A' && c <= 'Z') letters += StringSubstr(upper, i, 1);
   }
   return letters;
}

void _RiskFactorsForExposure(const string symbol,
                             const bool is_buy,
                             const string session,
                             const double risk_pct,
                             const string policy_json,
                             string &names[],
                             double &values[]) {
   string upper = symbol;
   StringToUpper(upper);
   string asset_class = _RiskAssetClassForSymbol(symbol);
   string aliases = JsonGetObject(policy_json, "symbol_aliases", "{}");
   string alias = JsonGetObject(aliases, upper, "{}");
   string alias_asset = JsonGetString(alias, "asset_class", "");
   if(StringLen(alias_asset) > 0) asset_class = alias_asset;
   _AccumulateNamedRisk("symbol:" + upper, risk_pct, names, values);
   _AccumulateNamedRisk("asset_class:" + asset_class, risk_pct, names, values);
   _AccumulateNamedRisk("session:" + session, risk_pct, names, values);

   string letters = _SymbolLetters(symbol);
   if(asset_class == "fx" && StringLen(letters) >= 6){
      string base = StringSubstr(letters, 0, 3);
      string quote = StringSubstr(letters, 3, 3);
      _AccumulateNamedRisk("currency:" + base + ":" + (is_buy ? "long" : "short"), risk_pct, names, values);
      _AccumulateNamedRisk("currency:" + quote + ":" + (is_buy ? "short" : "long"), risk_pct, names, values);
      if(base == "USD" || quote == "USD")
         _AccumulateNamedRisk("factor:USD_" + ((base == "USD") == is_buy ? "long" : "short"), risk_pct, names, values);
      if(base == "JPY" || quote == "JPY")
         _AccumulateNamedRisk("factor:JPY_carry", risk_pct, names, values);
   }
   string cluster = JsonGetString(alias, "cluster", "");
   if(StringLen(cluster) > 0) _AccumulateNamedRisk("cluster:" + cluster, risk_pct, names, values);
   string macro = JsonGetString(alias, "macro_direction", "");
   if(StringLen(macro) > 0)
      _AccumulateNamedRisk("macro:" + macro + ":" + (is_buy ? "long" : "short"), risk_pct, names, values);
   string factor = JsonGetString(alias, "primary_factor", "");
   if(StringLen(factor) > 0)
      _AccumulateNamedRisk("factor:" + factor, risk_pct * MathAbs(JsonGetNumber(alias, "factor_loading", 1.0)), names, values);
}

double _RiskFactorCapFor(const string factor, const string policy_json) {
   string caps = JsonGetObject(policy_json, "caps_pct", "{}");
   int colon = StringFind(factor, ":");
   string category = (colon > 0 ? StringSubstr(factor, 0, colon) : factor);
   if(category == "symbol"){
      string per_symbol = JsonGetObject(policy_json, "per_symbol_caps_pct", "{}");
      string symbol = StringSubstr(factor, colon + 1);
      double specific = JsonGetNumber(per_symbol, symbol, 0.0);
      if(specific > 0.0) return specific;
   }
   return JsonGetNumber(caps, category, 0.0);
}

bool RiskFactorGate(const string symbol,
                    const bool is_buy,
                    const string session,
                    const double proposed_risk_money,
                    string &contributions_json,
                    string &reason) {
   contributions_json = "{}";
   reason = "";
   if(!InpRiskFactorGateEnable) return true;
   string policy_json = "";
   if(!_ReadCommonText(InpRiskFactorPolicyFile, policy_json)){
      reason = "risk_factor_policy_missing";
      return false;
   }
   if(JsonGetString(policy_json, "schema_version", "") != RISK_FACTOR_SCHEMA_VERSION){
      reason = "risk_factor_schema_incompatible";
      return false;
   }
   double base_money = _AggregateRiskBaseMoney();
   if(base_money <= 0.0 || proposed_risk_money <= 0.0){ reason = "risk_factor_risk_base_invalid"; return false; }
   string existing_names[];
   double existing_values[];
   ArrayResize(existing_names, 0);
   ArrayResize(existing_values, 0);

   for(int i=0; i<PositionsTotal(); i++){
      ulong ticket = PositionGetTicket(i);
      if(!PositionMatchesMagic(ticket)) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      long position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
      string comment = PositionGetString(POSITION_COMMENT);
      double per_lot = 0.0, original_entry = 0.0, original_sl = 0.0;
      string meta_reason = "";
      if(!_ReadExactOriginalRiskMeta(ticket, position_id, sym, comment,
                                    per_lot, original_entry, original_sl, meta_reason)){
         reason = "risk_factor_existing_risk_unavailable:" + meta_reason;
         return false;
      }
      double risk_pct = per_lot * PositionGetDouble(POSITION_VOLUME) / base_money * 100.0;
      bool long_side = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      _RiskFactorsForExposure(sym, long_side, _RiskCommentSession(comment), risk_pct,
                              policy_json, existing_names, existing_values);
   }
   for(int i=0; i<OrdersTotal(); i++){
      ulong ticket = OrderGetTicket(i);
      if(!OrderMatchesMagic(ticket)) continue;
      ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      bool pending_buy = (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY_STOP_LIMIT);
      bool pending_sell = (type == ORDER_TYPE_SELL_LIMIT || type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL_STOP_LIMIT);
      if(!pending_buy && !pending_sell) continue;
      string sym = OrderGetString(ORDER_SYMBOL);
      double risk_money = _RiskMoneyForPosition(sym, pending_buy,
                                                OrderGetDouble(ORDER_VOLUME_CURRENT),
                                                OrderGetDouble(ORDER_PRICE_OPEN),
                                                OrderGetDouble(ORDER_SL));
      if(risk_money <= 0.0){ reason = "risk_factor_pending_risk_unavailable"; return false; }
      _RiskFactorsForExposure(sym, pending_buy, _RiskCommentSession(OrderGetString(ORDER_COMMENT)),
                              risk_money / base_money * 100.0,
                              policy_json, existing_names, existing_values);
   }

   string proposed_names[];
   double proposed_values[];
   ArrayResize(proposed_names, 0);
   ArrayResize(proposed_values, 0);
   _RiskFactorsForExposure(symbol, is_buy, session,
                           proposed_risk_money / base_money * 100.0,
                           policy_json, proposed_names, proposed_values);
   contributions_json = "{";
   bool first = true;
   for(int i=0; i<ArraySize(proposed_names); i++){
      double existing = 0.0;
      for(int k=0; k<ArraySize(existing_names); k++) if(existing_names[k] == proposed_names[i]){ existing = existing_values[k]; break; }
      double cap = _RiskFactorCapFor(proposed_names[i], policy_json);
      double post = existing + proposed_values[i];
      string status = (cap > 0.0 && post <= cap + 0.0000001 ? "pass" : "blocked");
      _RiskLog("[risk_factor_gate] symbol=" + symbol
               + " factor=" + proposed_names[i]
               + " direction=" + (is_buy ? "buy" : "sell")
               + " existing_risk_pct=" + DoubleToString(existing, 4)
               + " proposed_risk_pct=" + DoubleToString(proposed_values[i], 4)
               + " cap_pct=" + DoubleToString(cap, 4)
               + " status=" + status);
      if(!first) contributions_json += ",";
      first = false;
      contributions_json += JsonKVNum(proposed_names[i], proposed_values[i], 6);
      if(status == "blocked"){
         reason = "risk_factor_cap_exceeded:" + proposed_names[i];
         contributions_json += "}";
         return false;
      }
   }
   contributions_json += "}";
   return true;
}

datetime _SessionClockOnDate(const datetime date_value, const datetime clock_value) {
   MqlDateTime date_part, clock_part;
   TimeToStruct(date_value, date_part);
   TimeToStruct(clock_value, clock_part);
   date_part.hour = clock_part.hour;
   date_part.min = clock_part.min;
   date_part.sec = clock_part.sec;
   return StructToTime(date_part);
}

bool QueryBrokerSymbolSessionSchedule(const string symbol,
                                      const datetime now,
                                      datetime &session_open,
                                      datetime &session_close,
                                      datetime &no_entry_from,
                                      datetime &flatten_from,
                                      datetime &next_tradable,
                                      string &reason) {
   session_open = 0;
   session_close = 0;
   no_entry_from = 0;
   flatten_from = 0;
   next_tradable = 0;
   reason = "";
   if(!InpUseBrokerSymbolSessions){ reason = "broker_symbol_sessions_disabled"; return false; }
   for(int day_offset=0; day_offset<8; day_offset++){
      datetime date_value = now + day_offset * 86400;
      MqlDateTime date_parts;
      TimeToStruct(date_value, date_parts);
      ENUM_DAY_OF_WEEK day = (ENUM_DAY_OF_WEEK)date_parts.day_of_week;
      for(uint index=0; index<32; index++){
         datetime from_clock = 0, to_clock = 0;
         if(!SymbolInfoSessionTrade(symbol, day, index, from_clock, to_clock)) break;
         datetime open_value = _SessionClockOnDate(date_value, from_clock);
         datetime close_value = _SessionClockOnDate(date_value, to_clock);
         if(close_value <= open_value) close_value += 86400;
         if(day_offset == 0 && now >= close_value) continue;
         if(next_tradable <= 0 || open_value < next_tradable) next_tradable = open_value;
         if(day_offset == 0 && now >= open_value && now < close_value){
            session_open = open_value;
            session_close = close_value;
            no_entry_from = close_value - MathMax(0, InpSymbolNoEntryBeforeCloseMin) * 60;
            flatten_from = close_value - MathMax(0, InpSymbolFlattenBeforeCloseMin) * 60;
            next_tradable = open_value;
            return true;
         }
         if(day_offset == 0 && now < open_value){
            session_open = open_value;
            session_close = close_value;
            no_entry_from = close_value - MathMax(0, InpSymbolNoEntryBeforeCloseMin) * 60;
            flatten_from = close_value - MathMax(0, InpSymbolFlattenBeforeCloseMin) * 60;
            return true;
         }
         if(day_offset > 0 && next_tradable > 0){
            reason = "market_closed_until_next_broker_session";
            return false;
         }
      }
   }
   reason = "broker_symbol_session_unavailable";
   return false;
}

bool SymbolEntryAllowedByBrokerSession(const string symbol,
                                       const datetime now,
                                       string &reason,
                                       string &schedule_json) {
   reason = "";
   schedule_json = "{}";
   if(!InpUseBrokerSymbolSessions) return true;
   datetime session_open = 0, session_close = 0, no_entry_from = 0, flatten_from = 0, next_tradable = 0;
   string query_reason = "";
   bool current_session = QueryBrokerSymbolSessionSchedule(symbol, now,
                                                           session_open, session_close,
                                                           no_entry_from, flatten_from,
                                                           next_tradable, query_reason);
   schedule_json = "{" + JsonKVStr("schema_version", ENGINE_INPUT_SCHEMA) + ","
                   + JsonKVInt("open", (int)session_open) + ","
                   + JsonKVInt("close", (int)session_close) + ","
                   + JsonKVInt("no_entry_from", (int)no_entry_from) + ","
                   + JsonKVInt("flatten_from", (int)flatten_from) + ","
                   + JsonKVInt("next_tradable", (int)next_tradable) + ","
                   + JsonKVStr("source", "broker_session_api") + "}";
   _RiskLog("[symbol_session_schedule] symbol=" + symbol
            + " open=" + TimeToString(session_open, TIME_DATE|TIME_MINUTES)
            + " close=" + TimeToString(session_close, TIME_DATE|TIME_MINUTES)
            + " no_entry_from=" + TimeToString(no_entry_from, TIME_DATE|TIME_MINUTES)
            + " flatten_from=" + TimeToString(flatten_from, TIME_DATE|TIME_MINUTES)
            + " source=broker_session_api");
   if(!current_session){ reason = query_reason; return false; }
   if(now < session_open || now >= session_close){ reason = "broker_market_closed"; return false; }
   if(no_entry_from > 0 && now >= no_entry_from){ reason = "broker_symbol_no_entry_window"; return false; }
   return true;
}

#endif
