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

double _ClampVolToStep(const string symbol, double vol) {
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = vmin;
   if(vol < vmin) vol = vmin;
   if(vol > vmax) vol = vmax;
   // round down to step
   double steps = MathFloor((vol - vmin) / step);
   double out = vmin + steps * step;
   if(out < vmin) out = vmin;
   return out;
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

double CurrentTotalRiskMoney() {
   double total = 0.0;
   for(int i=0; i<PositionsTotal(); i++){
      ulong ticket = PositionGetTicket(i);
      if(!PositionMatchesMagic(ticket)) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      if(sl <= 0) continue;
      double vol = PositionGetDouble(POSITION_VOLUME);
      bool is_buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      total += _RiskMoneyForPosition(sym, is_buy, vol, entry, sl);
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
      if(entry <= 0 || sl <= 0 || vol <= 0) continue;
      total += _RiskMoneyForPosition(sym, is_pending_buy, vol, entry, sl);
   }
   return total;
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
   return _ClampVolToStep(symbol, lo);
}

bool HasCapacityForNewTrade(const double new_trade_risk_money, double &risk_cap_remaining) {
   if(!InpMaxTotalRiskEnable) { risk_cap_remaining = 1e18; return true; }
   double cur = CurrentTotalRiskMoney();
   risk_cap_remaining = MathMax(0.0, InpMaxTotalRiskMoney - cur);
   return (risk_cap_remaining >= new_trade_risk_money);
}

#endif
