//+------------------------------------------------------------------+
//| PO3.mqh - PO3 / dealing range sweep + displacement + BOS         |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_PO3_MQH__
#define __PO3_AIGATE_PO3_MQH__
#include "Types.mqh"
#include "Indicators.mqh"
#include "Config.mqh"

struct PO3BuildStats {
   int    total_bars;
   int    dr_checked;
   int    dr_width_rejected;
   int    sweep_found;
   int    sweep_above_found;
   int    sweep_below_found;
   int    displacement_passed;
   int    displacement_missing;
   int    displacement_quality_failed;
   int    bos_missing;
   int    session_offhours;
   int    context_candidates;
   int    tier_a_contexts;
   int    tier_b_contexts;
   int    final_context_selected;
   string last_reject_reason;
};

class CPO3 {
private:
   void _ResetStats(PO3BuildStats &stats) const {
      stats.total_bars = 0;
      stats.dr_checked = 0;
      stats.dr_width_rejected = 0;
      stats.sweep_found = 0;
      stats.sweep_above_found = 0;
      stats.sweep_below_found = 0;
      stats.displacement_passed = 0;
      stats.displacement_missing = 0;
      stats.displacement_quality_failed = 0;
      stats.bos_missing = 0;
      stats.session_offhours = 0;
      stats.context_candidates = 0;
      stats.tier_a_contexts = 0;
      stats.tier_b_contexts = 0;
      stats.final_context_selected = 0;
      stats.last_reject_reason = "";
   }

   void _SetReject(PO3BuildStats &stats, const string reason) const {
      stats.last_reject_reason = reason;
   }

   int _HourOf(const datetime t) const {
      MqlDateTime ts;
      TimeToStruct(t, ts);
      return ts.hour;
   }

   datetime _MakeTime(const int year, const int mon, const int day, const int hour=0, const int minute=0, const int second=0) const {
      MqlDateTime ts;
      ZeroMemory(ts);
      ts.year = year;
      ts.mon = mon;
      ts.day = day;
      ts.hour = hour;
      ts.min = minute;
      ts.sec = second;
      return StructToTime(ts);
   }

   int _DaysInMonth(const int year, const int mon) const {
      if(mon == 2){
         bool leap = ((year % 4 == 0 && year % 100 != 0) || (year % 400 == 0));
         return (leap ? 29 : 28);
      }
      if(mon == 4 || mon == 6 || mon == 9 || mon == 11) return 30;
      return 31;
   }

   int _NthWeekdayOfMonth(const int year, const int mon, const int weekday, const int nth) const {
      int found = 0;
      for(int day=1; day<=_DaysInMonth(year, mon); day++){
         MqlDateTime ts;
         TimeToStruct(_MakeTime(year, mon, day, 0, 0, 0), ts);
         if(ts.day_of_week == weekday){
            found++;
            if(found == nth) return day;
         }
      }
      return 1;
   }

   int _LastWeekdayOfMonth(const int year, const int mon, const int weekday) const {
      for(int day=_DaysInMonth(year, mon); day>=1; day--){
         MqlDateTime ts;
         TimeToStruct(_MakeTime(year, mon, day, 0, 0, 0), ts);
         if(ts.day_of_week == weekday) return day;
      }
      return _DaysInMonth(year, mon);
   }

   int _ServerUtcOffsetSeconds() const {
      if(!InpUseAutoServerUtcOffset) return InpServerUtcOffsetHours * 3600;
      datetime server_now = TimeTradeServer();
      datetime gmt_now = TimeGMT();
      if(server_now <= 0 || gmt_now <= 0) return InpServerUtcOffsetHours * 3600;
      return (int)(server_now - gmt_now);
   }

   datetime _ToUtc(const datetime server_time) const {
      return server_time - _ServerUtcOffsetSeconds();
   }

   datetime _FromUtc(const datetime utc_time) const {
      return utc_time + _ServerUtcOffsetSeconds();
   }

   bool _IsLondonDst(const datetime utc_time) const {
      MqlDateTime ts;
      TimeToStruct(utc_time, ts);
      int year = ts.year;
      datetime start = _MakeTime(year, 3, _LastWeekdayOfMonth(year, 3, 0), 1, 0, 0);
      datetime finish = _MakeTime(year, 10, _LastWeekdayOfMonth(year, 10, 0), 1, 0, 0);
      return (utc_time >= start && utc_time < finish);
   }

   bool _IsNewYorkDst(const datetime utc_time) const {
      MqlDateTime ts;
      TimeToStruct(utc_time, ts);
      int year = ts.year;
      datetime start = _MakeTime(year, 3, _NthWeekdayOfMonth(year, 3, 0, 2), 7, 0, 0);
      datetime finish = _MakeTime(year, 11, _NthWeekdayOfMonth(year, 11, 0, 1), 6, 0, 0);
      return (utc_time >= start && utc_time < finish);
   }

   int _MarketUtcOffsetHours(const string market, const datetime utc_time) const {
      if(market == "LONDON") return (_IsLondonDst(utc_time) ? 1 : 0);
      if(market == "NEW_YORK") return (_IsNewYorkDst(utc_time) ? -4 : -5);
      return 0;
   }

   int _ClampMinute(const int value) const {
      if(value < 0) return 0;
      if(value > 59) return 59;
      return value;
   }

   bool _BuildMarketWindowForDay(const string market, const int year, const int mon, const int day,
                                 const int start_hour, const int start_minute,
                                 const int end_hour, const int end_minute,
                                 datetime &start_utc, datetime &end_utc) const {
      datetime midday_utc = _MakeTime(year, mon, day, 12, 0, 0);
      int offset_hours = _MarketUtcOffsetHours(market, midday_utc);
      datetime base_utc = _MakeTime(year, mon, day, 0, 0, 0);
      start_utc = base_utc + (start_hour - offset_hours) * 3600 + _ClampMinute(start_minute) * 60;
      end_utc = base_utc + (end_hour - offset_hours) * 3600 + _ClampMinute(end_minute) * 60;
      if(end_utc <= start_utc) end_utc += 86400;
      return true;
   }

   bool _ResolveMarketWindow(const string market, const datetime utc_time,
                             const int start_hour, const int end_hour,
                             datetime &start_utc, datetime &end_utc) const {
      return _ResolveMarketWindowEx(market, utc_time, start_hour, 0, end_hour, 0, start_utc, end_utc);
   }

   bool _ResolveMarketWindowEx(const string market, const datetime utc_time,
                               const int start_hour, const int start_minute,
                               const int end_hour, const int end_minute,
                               datetime &start_utc, datetime &end_utc) const {
      MqlDateTime ts;
      TimeToStruct(utc_time, ts);
      _BuildMarketWindowForDay(market, ts.year, ts.mon, ts.day, start_hour, start_minute,
                               end_hour, end_minute, start_utc, end_utc);
      if(utc_time >= start_utc && utc_time < end_utc) return true;

      datetime prev = utc_time - 86400;
      TimeToStruct(prev, ts);
      _BuildMarketWindowForDay(market, ts.year, ts.mon, ts.day, start_hour, start_minute,
                               end_hour, end_minute, start_utc, end_utc);
      return (utc_time >= start_utc && utc_time < end_utc);
   }

   bool _SessionWindow(const string session_name, const datetime utc_time,
                       datetime &start_utc, datetime &end_utc) const {
      if(session_name == "ASIA")
         return _ResolveMarketWindow("ASIA", utc_time, InpAsiaSessionStartHour, InpAsiaSessionEndHour, start_utc, end_utc);
      if(session_name == "LONDON")
         return _ResolveMarketWindow("LONDON", utc_time, InpLondonSessionStartHour, InpLondonSessionEndHour, start_utc, end_utc);
      if(session_name == "NEW_YORK")
         return _ResolveMarketWindow("NEW_YORK", utc_time, InpNewYorkSessionStartHour, InpNewYorkSessionEndHour, start_utc, end_utc);
      return false;
   }

   bool _KillzoneWindow(const string market, const datetime utc_time,
                        datetime &start_utc, datetime &end_utc) const {
      if(market == "ASIA")
         return (InpEnableAsiaKillzone &&
                 _ResolveMarketWindowEx("ASIA", utc_time,
                                        InpAsiaKillzoneStartHour, InpAsiaKillzoneStartMinute,
                                        InpAsiaKillzoneEndHour, InpAsiaKillzoneEndMinute,
                                        start_utc, end_utc));
      if(market == "LONDON")
         return _ResolveMarketWindowEx("LONDON", utc_time,
                                       InpLondonKillzoneStartHour, InpLondonKillzoneStartMinute,
                                       InpLondonKillzoneEndHour, InpLondonKillzoneEndMinute,
                                       start_utc, end_utc);
      if(market == "NEW_YORK")
         return _ResolveMarketWindowEx("NEW_YORK", utc_time,
                                       InpNewYorkKillzoneStartHour, InpNewYorkKillzoneStartMinute,
                                       InpNewYorkKillzoneEndHour, InpNewYorkKillzoneEndMinute,
                                       start_utc, end_utc);
      return false;
   }

   string _SessionName(const datetime server_time) const {
      datetime utc_time = _ToUtc(server_time);
      datetime start_utc, end_utc;
      if(_SessionWindow("ASIA", utc_time, start_utc, end_utc)) return "ASIA";
      if(_SessionWindow("LONDON", utc_time, start_utc, end_utc)) return "LONDON";
      if(_SessionWindow("NEW_YORK", utc_time, start_utc, end_utc)) return "NEW_YORK";
      return "OFF_HOURS";
   }

   string _SessionCode(const string session_name) const {
      if(session_name == "ASIA") return "ASIA";
      if(session_name == "LONDON") return "LON";
      if(session_name == "NEW_YORK") return "NY";
      return "OFF";
   }

   bool _IsKillZone(const datetime server_time) const {
      datetime utc_time = _ToUtc(server_time);
      datetime start_utc, end_utc;
      return (_KillzoneWindow("ASIA", utc_time, start_utc, end_utc) ||
              _KillzoneWindow("LONDON", utc_time, start_utc, end_utc) ||
              _KillzoneWindow("NEW_YORK", utc_time, start_utc, end_utc));
   }

   string _KillzoneName(const datetime server_time) const {
      datetime utc_time = _ToUtc(server_time);
      datetime start_utc, end_utc;
      if(_KillzoneWindow("ASIA", utc_time, start_utc, end_utc)) return "ASIA";
      if(_KillzoneWindow("LONDON", utc_time, start_utc, end_utc)) return "LONDON";
      if(_KillzoneWindow("NEW_YORK", utc_time, start_utc, end_utc)) return "NEW_YORK";
      return "NONE";
   }

   double _AverageRecentRange(const MqlRates &rates[], const int got, const int idx, const int count) const {
      double sum = 0.0;
      int used = 0;
      for(int i=idx + 1; i<got && used<count; i++, used++){
         sum += MathAbs(rates[i].high - rates[i].low);
      }
      if(used <= 0) return 0.0;
      return sum / used;
   }

   double _AverageRecentTickVolume(const MqlRates &rates[], const int got, const int idx, const int count) const {
      double sum = 0.0;
      int used = 0;
      for(int i=idx + 1; i<got && used<count; i++, used++){
         sum += (double)MathMax((long)1, rates[i].tick_volume);
      }
      if(used <= 0) return 0.0;
      return sum / used;
   }

   bool _CreatesDirectionalImbalance(const MqlRates &rates[], const int got, const int idx, const bool want_long) const {
      if(idx - 1 < 1 || idx + 1 >= got) return false;
      int older = idx + 1;
      int newer = idx - 1;
      if(want_long) return (rates[older].high < rates[newer].low);
      return (rates[older].low > rates[newer].high);
   }

   bool _HasDisplacementFollowThrough(const MqlRates &rates[], const int got, const int idx, const bool want_long) const {
      if(idx - 1 < 1 || idx >= got) return false;
      MqlRates bar = rates[idx];
      MqlRates next = rates[idx - 1];
      if(want_long){
         if(next.close <= next.open) return false;
         if(next.close < bar.close) return false;
         if(next.close <= bar.high) return false;
      } else {
         if(next.close >= next.open) return false;
         if(next.close > bar.close) return false;
         if(next.close >= bar.low) return false;
      }
      return (CandleBodyFrac(next) >= 0.35);
   }

   double _DisplacementQuality(const MqlRates &rates[], const int got, const int idx, const bool want_long, const double atr,
                               double &body_frac_out, double &range_atr_out, double &vol_ratio_out,
                               double &speed_score_out, double &follow_score_out) const {
      body_frac_out = 0.0;
      range_atr_out = 0.0;
      vol_ratio_out = 0.0;
      speed_score_out = 0.0;
      follow_score_out = 0.0;
      if(idx < 0 || idx >= got || atr <= 0.0) return 0.0;

      MqlRates bar = rates[idx];
      double body = MathAbs(bar.close - bar.open);
      double range = MathAbs(bar.high - bar.low);
      double body_frac = CandleBodyFrac(bar);
      double near_extreme = CloseNearExtremeFrac(bar, want_long);
      double recent_avg_range = _AverageRecentRange(rates, got, idx, 4);
      double recent_avg_volume = _AverageRecentTickVolume(rates, got, idx, 6);
      double range_atr = (atr > 0.0 ? range / atr : 0.0);
      double speed_score = (recent_avg_range > 0.0 ? range / recent_avg_range : range_atr);
      double vol_ratio = (recent_avg_volume > 0.0 ? (double)MathMax((long)1, bar.tick_volume) / recent_avg_volume : 1.0);
      bool imbalance = _CreatesDirectionalImbalance(rates, got, idx, want_long);
      bool follow = _HasDisplacementFollowThrough(rates, got, idx, want_long);

      double score = 0.0;
      score += MathMin(2.4, body_frac * 3.2);
      score += MathMin(2.0, range_atr * 1.45);
      score += MathMin(1.7, speed_score * 0.95);
      score += MathMin(1.3, MathMax(0.0, vol_ratio - 0.85) * 1.5);
      score += MathMax(0.0, 1.1 - near_extreme * 2.2);
      if(imbalance) score += 0.8;
      if(follow) score += 0.9;
      if(idx + 1 < got){
         if(want_long && bar.close > rates[idx + 1].high) score += 0.6;
         if(!want_long && bar.close < rates[idx + 1].low) score += 0.6;
      }

      body_frac_out = body_frac;
      range_atr_out = range_atr;
      vol_ratio_out = vol_ratio;
      speed_score_out = speed_score;
      follow_score_out = (follow ? 1.0 : 0.0);
      return MathMin(10.0, score);
   }

   bool _MatchesDisplacement(const MqlRates &rates[], const int got, const int idx, const bool want_long, const double atr) const {
      if(idx < 0 || idx >= got) return false;
      MqlRates bar = rates[idx];
      bool bull = (bar.close > bar.open);
      double body = MathAbs(bar.close - bar.open);
      double range = MathAbs(bar.high - bar.low);
      double body_frac = CandleBodyFrac(bar);
      double near_extreme = CloseNearExtremeFrac(bar, want_long);
      double recent_avg_range = _AverageRecentRange(rates, got, idx, 4);
      bool direction_ok = (want_long ? bull : !bull);
      double body_floor = PO3EffectiveDispBodyATRMin();
      double range_floor = PO3EffectiveDispRangeATRMin();
      double body_frac_floor = PO3EffectiveDispBodyFracMin();
      double recent_mult = PO3EffectiveDispRangeVsRecentMult();
      double quality_floor = PO3EffectiveDispMinQuality();
      bool body_ok = (body >= atr * body_floor);
      bool range_ok = (range >= atr * range_floor);
      bool body_frac_ok = (body_frac >= body_frac_floor);
      bool urgency_ok = (recent_avg_range <= 0.0 || range >= recent_avg_range * recent_mult);
      bool close_ok = (near_extreme <= InpDispCloseNearExtremeMax);
      bool imbalance_ok = (!InpDispRequireImbalance || _CreatesDirectionalImbalance(rates, got, idx, want_long));
      bool relative_break_ok = true;
      double disp_body_frac = 0.0, disp_range_atr = 0.0, disp_vol_ratio = 0.0, disp_speed_score = 0.0, disp_follow_score = 0.0;
      double quality = _DisplacementQuality(rates, got, idx, want_long, atr,
                                            disp_body_frac, disp_range_atr, disp_vol_ratio,
                                            disp_speed_score, disp_follow_score);
      if(idx + 1 < got){
         if(want_long) relative_break_ok = (bar.close > rates[idx + 1].high);
         else          relative_break_ok = (bar.close < rates[idx + 1].low);
      }
      bool volume_ok = (disp_vol_ratio >= InpDispVolumeVsRecentMult || quality >= quality_floor + 0.8);
      return (direction_ok && body_ok && range_ok && body_frac_ok && urgency_ok && close_ok &&
              imbalance_ok && relative_break_ok && volume_ok && quality >= quality_floor);
   }

   double _SweepStrength(const MqlRates &bar, const double dr_high, const double dr_low) const {
      double dr_range = dr_high - dr_low;
      if(dr_range <= 0) return 0.0;
      double pen = 0.0;
      if(bar.low < dr_low && bar.close > dr_low) pen = dr_low - bar.low;
      if(bar.high > dr_high && bar.close < dr_high) pen = MathMax(pen, bar.high - dr_high);
      double strength = pen / dr_range;
      if(strength < 0) strength = 0.0;
      if(strength > 3.0) strength = 3.0;
      return strength;
   }

   double _SweepPenetrationTicks(const MqlRates &bar, const double dr_high, const double dr_low, const double point) const {
      double p = MathMax(point, 0.0000001);
      double pen = 0.0;
      if(bar.low < dr_low && bar.close > dr_low) pen = MathMax(pen, dr_low - bar.low);
      if(bar.high > dr_high && bar.close < dr_high) pen = MathMax(pen, bar.high - dr_high);
      return pen / p;
   }

   bool _IsSwingHigh(const MqlRates &rates[], const int got, const int idx, const int span) const {
      if(idx - span < 0 || idx + span >= got) return false;
      for(int j=1; j<=span; j++){
         if(rates[idx].high <= rates[idx-j].high) return false;
         if(rates[idx].high < rates[idx+j].high) return false;
      }
      return true;
   }

   bool _IsSwingLow(const MqlRates &rates[], const int got, const int idx, const int span) const {
      if(idx - span < 0 || idx + span >= got) return false;
      for(int j=1; j<=span; j++){
         if(rates[idx].low >= rates[idx-j].low) return false;
         if(rates[idx].low > rates[idx+j].low) return false;
      }
      return true;
   }

   int _FindBarIndexAtOrBefore(const MqlRates &rates[], const int got, const datetime t) const {
      for(int i=0; i<got; i++){
         if(rates[i].time <= t) return i;
      }
      return -1;
   }

   int _FindRecentSwingHighBefore(const MqlRates &rates[], const int got, const int anchor_idx, const int span) const {
      for(int i=anchor_idx + span; i<got - span; i++){
         if(_IsSwingHigh(rates, got, i, span)) return i;
      }
      return -1;
   }

   int _FindRecentSwingLowBefore(const MqlRates &rates[], const int got, const int anchor_idx, const int span) const {
      for(int i=anchor_idx + span; i<got - span; i++){
         if(_IsSwingLow(rates, got, i, span)) return i;
      }
      return -1;
   }

   int _PreTrendDirection(const MqlRates &rates[], const int got, const int anchor_idx, const int span) const {
      int recent_high = -1, prev_high = -1, recent_low = -1, prev_low = -1;
      for(int i=anchor_idx + span; i<got - span; i++){
         if(recent_high < 0 && _IsSwingHigh(rates, got, i, span)){
            recent_high = i;
            continue;
         }
         if(recent_high >= 0 && prev_high < 0 && _IsSwingHigh(rates, got, i, span)){
            prev_high = i;
            continue;
         }
         if(recent_low < 0 && _IsSwingLow(rates, got, i, span)){
            recent_low = i;
            continue;
         }
         if(recent_low >= 0 && prev_low < 0 && _IsSwingLow(rates, got, i, span)){
            prev_low = i;
            continue;
         }
         if(prev_high >= 0 && prev_low >= 0) break;
      }

      bool down = false, up = false;
      if(recent_high >= 0 && prev_high >= 0 && recent_low >= 0 && prev_low >= 0){
         down = (rates[recent_high].high < rates[prev_high].high && rates[recent_low].low < rates[prev_low].low);
         up   = (rates[recent_high].high > rates[prev_high].high && rates[recent_low].low > rates[prev_low].low);
      }
      if(down) return -1;
      if(up) return 1;

      int lookback = MathMin(40, got - anchor_idx - 1);
      if(lookback < 5) return 0;
      double sum = 0.0;
      for(int i=anchor_idx + 1; i<anchor_idx + 1 + lookback && i<got; i++) sum += rates[i].close;
      double older_avg = sum / lookback;
      if(rates[anchor_idx].close < older_avg) return -1;
      if(rates[anchor_idx].close > older_avg) return 1;
      return 0;
   }

   bool _BodyCloseThroughLevel(const MqlRates &bar, const double level, const bool want_long) const {
      double body_hi = MathMax(bar.open, bar.close);
      double body_lo = MathMin(bar.open, bar.close);
      double body = body_hi - body_lo;
      if(body <= 0.0) return false;

      if(want_long){
         if(bar.close <= level) return false;
         double beyond = body_hi - MathMax(level, body_lo);
         if(beyond <= 0.0) return false;
         return (beyond / body >= InpStructureBodyBreakMinFrac);
      }

      if(bar.close >= level) return false;
      double beyond = MathMin(level, body_hi) - body_lo;
      if(beyond <= 0.0) return false;
      return (beyond / body >= InpStructureBodyBreakMinFrac);
   }

   bool _HasFollowThroughBreak(const MqlRates &rates[], const int got, const int break_idx,
                               const bool want_long, const double level) const {
      if(!InpStructureRequireFollowThrough) return true;
      if(break_idx - 1 < 1 || break_idx >= got) return false;

      MqlRates brk = rates[break_idx];
      MqlRates follow = rates[break_idx - 1];
      if(want_long){
         if(follow.close <= follow.open) return false;
         if(follow.close <= level) return false;
         if(follow.close < brk.close) return false;
      } else {
         if(follow.close >= follow.open) return false;
         if(follow.close >= level) return false;
         if(follow.close > brk.close) return false;
      }
      return (CandleBodyFrac(follow) >= 0.30);
   }

   bool _DetectStructureShift(const MqlRates &rates[], const int got, const int anchor_idx, const bool want_long,
                              const int span, bool &has_bos, bool &is_mss, bool &is_choch,
                              int &break_idx, double &break_level, int &pretrend_dir) const {
      has_bos = false;
      is_mss = false;
      is_choch = false;
      break_idx = -1;
      break_level = 0.0;
      pretrend_dir = 0;

      int pretrend = _PreTrendDirection(rates, got, anchor_idx, span);
      pretrend_dir = pretrend;
      if(want_long){
         int swing_high_idx = _FindRecentSwingHighBefore(rates, got, anchor_idx, span);
         if(swing_high_idx < 0) return false;
         break_level = rates[swing_high_idx].high;
         for(int i=anchor_idx-1; i>=1; i--){
            bool wick_only = (rates[i].high > break_level && rates[i].close <= break_level);
            if(InpStructureRejectWickOnly && wick_only) continue;
            if(_BodyCloseThroughLevel(rates[i], break_level, true) &&
               _HasFollowThroughBreak(rates, got, i, true, break_level)){
               break_idx = i;
               has_bos = true;
               is_mss = (pretrend <= 0);
               is_choch = (pretrend < 0);
               return true;
            }
         }
      } else {
         int swing_low_idx = _FindRecentSwingLowBefore(rates, got, anchor_idx, span);
         if(swing_low_idx < 0) return false;
         break_level = rates[swing_low_idx].low;
         for(int i=anchor_idx-1; i>=1; i--){
            bool wick_only = (rates[i].low < break_level && rates[i].close >= break_level);
            if(InpStructureRejectWickOnly && wick_only) continue;
            if(_BodyCloseThroughLevel(rates[i], break_level, false) &&
               _HasFollowThroughBreak(rates, got, i, false, break_level)){
               break_idx = i;
               has_bos = true;
               is_mss = (pretrend >= 0);
               is_choch = (pretrend > 0);
               return true;
            }
         }
      }
      return false;
   }

   string _StructureTypeLabel(const bool has_break, const bool is_mss, const bool is_choch,
                              const bool internal_break, const bool ltf) const {
      if(!has_break) return "";
      if(internal_break && ltf) return "internal_ltf_bos";
      if(is_choch) return "choch";
      if(is_mss) return "reversal_mss";
      return "continuation_bos";
   }

   string _FinalPO3Class(const PO3Context &ctx) const {
      string side = (ctx.bias_long ? "bullish" : "bearish");
      PO3State state = (ctx.state != PO3_IDLE ? ctx.state : PO3StateFromString(ctx.po3_state));
      if(state == PO3_CONFIRMED) return side + "_confirmed_po3";
      if(state == PO3_ENTRY_WAITING) return side + "_entry_waiting_po3";
      if(state == PO3_FVG_CONFIRMED) return side + "_fvg_confirmed_po3";
      if(state == PO3_STRUCTURE_CONFIRMED) return side + "_structure_confirmed_po3";
      if(state == PO3_DISPLACEMENT_CONFIRMED) return side + "_displacement_confirmed_po3";
      if(state == PO3_SWEEP_CONFIRMED) return side + "_sweep_confirmed_po3";
      if(state == PO3_DEVELOPING) return side + "_developing_po3";
      if(state == PO3_EXPIRED) return side + "_expired_po3";
      if(state == PO3_INVALIDATED) return side + "_invalidated_po3";
      return "none";
   }

   void _PopulateImpulseAnchors(const MqlRates &rates[], const int got,
                                const int sweep_idx, const int disp_idx, const int bos_idx,
                                const bool want_long, double &anchor_low, double &anchor_high) const {
      anchor_low = 0.0;
      anchor_high = 0.0;
      if(sweep_idx < 0 || sweep_idx >= got) return;

      int newest = bos_idx;
      if(newest < 0) newest = disp_idx;
      if(newest < 0) newest = sweep_idx;
      int oldest = sweep_idx;
      if(newest > oldest){
         int tmp = newest;
         newest = oldest;
         oldest = tmp;
      }

      double lo = DBL_MAX;
      double hi = -DBL_MAX;
      for(int i=newest; i<=oldest && i<got; i++){
         lo = MathMin(lo, rates[i].low);
         hi = MathMax(hi, rates[i].high);
      }
      if(lo == DBL_MAX || hi == -DBL_MAX){
         lo = rates[sweep_idx].low;
         hi = rates[sweep_idx].high;
      }

      if(want_long) lo = MathMin(lo, rates[sweep_idx].low);
      else          hi = MathMax(hi, rates[sweep_idx].high);

      anchor_low = lo;
      anchor_high = hi;
   }

   int _DirectionalBias(const string symbol, const ENUM_TIMEFRAMES tf) const {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, tf, 0, 120, rates);
      if(got < 60) return 0;
      double strength = TrendStrength(rates, got, 50);
      double slope = TrendSlopePct(rates, got, 50);
      double neutral = 0.00012;
      if(strength < 0.32 || MathAbs(slope) <= neutral) return 0;
      return (slope > 0.0 ? 1 : -1);
   }

   double _HierarchyAlignmentScore(const PO3Context &ctx) const {
      int bias_dir = (ctx.bias_long ? 1 : -1);
      double score = 0.0;
      if(ctx.daily_bias_dir == bias_dir) score += 2.4;
      if(ctx.h4_bias_dir == bias_dir) score += 2.1;
      if(ctx.h1_bias_dir == bias_dir) score += 1.6;
      if(ctx.htf_mss) score += 1.6;
      if(ctx.htf_choch) score += 1.2;
      if(ctx.ltf_mss) score += 0.8;
      if(ctx.ltf_choch) score += 0.6;
      return score;
   }

   double _ContextScore(const PO3Context &ctx) const {
      double score = 0.0;
      score += MathMin(4.0, ctx.sweep_strength * 4.5);
      score += MathMin(4.0, ctx.displacement_score * 0.42);
      if(ctx.has_bos) score += 2.6;
      if(ctx.has_follow_through) score += 0.8;
      if(ctx.in_killzone) score += 1.2;
      if(ctx.liquidity_cluster_count > 1) score += MathMin(1.8, ctx.liquidity_cluster_count * 0.6);
      score += _HierarchyAlignmentScore(ctx);
      int stale_bars = MathMax(0, ctx.context_age_bars - MathMax(0, InpContextAgeGraceBars));
      score -= MathMin(8.0, stale_bars * MathMax(0.0, InpContextAgePenaltyPerBar));
      return score;
   }

   bool _SessionExtremesForDay(const string symbol, const string session_name,
                               const datetime anchor_utc, double &high, double &low,
                               datetime &start_server, datetime &end_server) const {
      datetime start_utc, end_utc;
      high = 0.0;
      low = 0.0;
      start_server = 0;
      end_server = 0;

      MqlDateTime ts;
      TimeToStruct(anchor_utc, ts);
      if(session_name == "ASIA")
         _BuildMarketWindowForDay("ASIA", ts.year, ts.mon, ts.day, InpAsiaSessionStartHour, 0, InpAsiaSessionEndHour, 0, start_utc, end_utc);
      else if(session_name == "LONDON")
         _BuildMarketWindowForDay("LONDON", ts.year, ts.mon, ts.day, InpLondonSessionStartHour, 0, InpLondonSessionEndHour, 0, start_utc, end_utc);
      else if(session_name == "NEW_YORK")
         _BuildMarketWindowForDay("NEW_YORK", ts.year, ts.mon, ts.day, InpNewYorkSessionStartHour, 0, InpNewYorkSessionEndHour, 0, start_utc, end_utc);
      else
         return false;

      start_server = _FromUtc(start_utc);
      end_server = _FromUtc(end_utc);

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, InpSessionMapTF, 0, 800, rates);
      if(got < 8) return false;

      bool found = false;
      double hi = -DBL_MAX, lo = DBL_MAX;
      for(int i=0; i<got; i++){
         if(rates[i].time < start_server || rates[i].time >= end_server) continue;
         hi = MathMax(hi, rates[i].high);
         lo = MathMin(lo, rates[i].low);
         found = true;
      }
      if(!found) return false;
      high = hi;
      low = lo;
      return true;
   }

   bool _PreviousLevels(const string symbol, const ENUM_TIMEFRAMES tf, double &high, double &low) const {
      high = 0.0;
      low = 0.0;
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, tf, 1, 1, rates);
      if(got < 1) return false;
      high = rates[0].high;
      low = rates[0].low;
      return true;
   }

   bool _ConsiderLiquidityLevel(const bool want_high, const double threshold, const double level,
                                const string kind, const int cluster_count,
                                bool &found, double &best_level, string &best_kind,
                                int &best_cluster, double &best_dist) const {
      if(level <= 0) return false;
      if(want_high && level <= threshold) return false;
      if(!want_high && level >= threshold) return false;

      double dist = MathAbs(level - threshold);
      bool better = (!found || cluster_count > best_cluster || (cluster_count == best_cluster && dist < best_dist));
      if(!better) return false;

      found = true;
      best_level = level;
      best_kind = kind;
      best_cluster = cluster_count;
      best_dist = dist;
      return true;
   }

   double _LiquidityTarget(const MqlRates &rates[], const int got, const int sweep_idx, const bool want_high,
                           const double threshold, const double fallback, const double atr,
                           const PO3Context &ctx, string &kind, int &cluster_count) const {
      int span = MathMax(1, InpLiquiditySwingSpan);
      int start = sweep_idx + InpDRLookbackBars + 1;
      int end = MathMin(got - 1, start + MathMax(InpLiquidityLookbackBars, span * 2 + 10));
      kind = "fallback_extreme";
      cluster_count = 1;

      bool found = false;
      double best_level = fallback;
      double best_dist = DBL_MAX;
      int best_cluster = 0;
      string best_kind = kind;

      for(int i=start + span; i<=end - span; i++){
         bool swing = (want_high ? _IsSwingHigh(rates, got, i, span) : _IsSwingLow(rates, got, i, span));
         if(!swing) continue;

         double level = (want_high ? rates[i].high : rates[i].low);
         if(want_high && level <= threshold) continue;
         if(!want_high && level >= threshold) continue;

         double tol = MathMax(atr * InpLiquidityEqualTolAtrFrac, 0.0);
         int cluster = 1;
         for(int j=start + span; j<=end - span; j++){
            if(j == i) continue;
            bool other_swing = (want_high ? _IsSwingHigh(rates, got, j, span) : _IsSwingLow(rates, got, j, span));
            if(!other_swing) continue;
            double other = (want_high ? rates[j].high : rates[j].low);
            if(MathAbs(other - level) <= tol) cluster++;
         }

         string swing_kind = (cluster >= InpLiquidityClusterMinCount
                              ? (want_high ? "equal_high_cluster" : "equal_low_cluster")
                              : (want_high ? "swing_high" : "swing_low"));
         _ConsiderLiquidityLevel(want_high, threshold, level, swing_kind, cluster,
                                 found, best_level, best_kind, best_cluster, best_dist);
      }

      _ConsiderLiquidityLevel(want_high, threshold, (want_high ? ctx.session_high : ctx.session_low),
                              (want_high ? "session_high" : "session_low"), 2,
                              found, best_level, best_kind, best_cluster, best_dist);
      _ConsiderLiquidityLevel(want_high, threshold, (want_high ? ctx.asia_high : ctx.asia_low),
                              (want_high ? "asia_session_high" : "asia_session_low"), 2,
                              found, best_level, best_kind, best_cluster, best_dist);
      _ConsiderLiquidityLevel(want_high, threshold, (want_high ? ctx.london_high : ctx.london_low),
                              (want_high ? "london_session_high" : "london_session_low"), 2,
                              found, best_level, best_kind, best_cluster, best_dist);
      _ConsiderLiquidityLevel(want_high, threshold, (want_high ? ctx.newyork_high : ctx.newyork_low),
                              (want_high ? "newyork_session_high" : "newyork_session_low"), 2,
                              found, best_level, best_kind, best_cluster, best_dist);
      _ConsiderLiquidityLevel(want_high, threshold, (want_high ? ctx.prev_day_high : ctx.prev_day_low),
                              (want_high ? "prev_day_high" : "prev_day_low"), 3,
                              found, best_level, best_kind, best_cluster, best_dist);
      _ConsiderLiquidityLevel(want_high, threshold, (want_high ? ctx.prev_week_high : ctx.prev_week_low),
                              (want_high ? "prev_week_high" : "prev_week_low"), 4,
                              found, best_level, best_kind, best_cluster, best_dist);

      if(found){
         kind = best_kind;
         cluster_count = best_cluster;
         return best_level;
      }

      double target = fallback;
      for(int i=start; i<=end; i++){
         if(want_high) target = MathMax(target, rates[i].high);
         else          target = MathMin(target, rates[i].low);
      }
      return target;
   }

   void _PopulateLtfStructure(const string symbol, const bool want_long, PO3Context &ctx) const {
      ctx.ltf_bos = false;
      ctx.ltf_mss = false;
      ctx.ltf_choch = false;
      ctx.ltf_structure_type = "";
      ctx.ltf_structure_time = 0;
      ctx.ltf_structure_level = 0.0;

      MqlRates ltf_rates[];
      ArraySetAsSeries(ltf_rates, true);
      int got = CopyRates(symbol, PO3EffectiveEntryTF(), 0, MathMax(600, InpDispSearchBars * 6), ltf_rates);
      if(got < 80) return;

      int anchor_idx = _FindBarIndexAtOrBefore(ltf_rates, got, (ctx.t_disp > 0 ? ctx.t_disp : ctx.t_sweep));
      if(anchor_idx < 3) anchor_idx = _FindBarIndexAtOrBefore(ltf_rates, got, ctx.t_sweep);
      if(anchor_idx < 3) return;

      int break_idx = -1;
      double break_level = 0.0;
      bool bos = false, mss = false, choch = false;
      int pretrend_dir = 0;
      if(!_DetectStructureShift(ltf_rates, got, anchor_idx, want_long, MathMax(1, InpStructureSwingSpan),
                                bos, mss, choch, break_idx, break_level, pretrend_dir))
         return;

      bool internal_bos = false, internal_mss = false, internal_choch = false;
      int internal_idx = -1, internal_pretrend = 0;
      double internal_level = 0.0;
      _DetectStructureShift(ltf_rates, got, anchor_idx, want_long, MathMax(1, InpInternalStructureSpan),
                            internal_bos, internal_mss, internal_choch, internal_idx, internal_level, internal_pretrend);

      ctx.ltf_bos = (bos || internal_bos);
      ctx.ltf_swing_bos = bos;
      ctx.ltf_internal_bos = internal_bos;
      ctx.ltf_pretrend_dir = pretrend_dir;
      ctx.ltf_mss = (mss || internal_mss);
      ctx.ltf_choch = (choch || internal_choch);
      ctx.ltf_structure_type = _StructureTypeLabel(ctx.ltf_bos, ctx.ltf_mss, ctx.ltf_choch, internal_bos, true);
      ctx.ltf_structure_level = break_level;
      if(break_idx >= 0) ctx.ltf_structure_time = ltf_rates[break_idx].time;
   }

public:
   void PopulateSessionContext(const string symbol, const datetime server_time, PO3Context &ctx) const {
      datetime anchor = (server_time > 0 ? server_time : TimeCurrent());
      datetime anchor_utc = _ToUtc(anchor);
      datetime start_server = 0, end_server = 0;
      ctx.session_name = _SessionName(anchor);
      ctx.session_code = _SessionCode(ctx.session_name);
      ctx.killzone_name = _KillzoneName(anchor);
      ctx.in_killzone = _IsKillZone(anchor);
      ctx.session_start = 0;
      ctx.session_end = 0;
      ctx.session_high = 0.0;
      ctx.session_low = 0.0;
      ctx.asia_high = 0.0;
      ctx.asia_low = 0.0;
      ctx.london_high = 0.0;
      ctx.london_low = 0.0;
      ctx.newyork_high = 0.0;
      ctx.newyork_low = 0.0;

      if(ctx.session_name != "OFF_HOURS" &&
         _SessionExtremesForDay(symbol, ctx.session_name, anchor_utc,
                                ctx.session_high, ctx.session_low, start_server, end_server)){
         ctx.session_start = start_server;
         ctx.session_end = end_server;
      }
      _SessionExtremesForDay(symbol, "ASIA", anchor_utc, ctx.asia_high, ctx.asia_low, start_server, end_server);
      _SessionExtremesForDay(symbol, "LONDON", anchor_utc, ctx.london_high, ctx.london_low, start_server, end_server);
      _SessionExtremesForDay(symbol, "NEW_YORK", anchor_utc, ctx.newyork_high, ctx.newyork_low, start_server, end_server);
   }

   bool Build(const string symbol, const ENUM_TIMEFRAMES tf, PO3Context &ctx) {
      PO3BuildStats stats;
      return Build(symbol, tf, ctx, stats);
   }

   bool Build(const string symbol, const ENUM_TIMEFRAMES tf, PO3Context &ctx, PO3BuildStats &stats) {
      _ResetStats(stats);
      ZeroMemory(ctx);
      ctx.valid = false;
      PO3SetState(ctx, PO3_INVALIDATED, "not_built");
      ctx.final_setup_class = "none";

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int need = MathMax(InpLiquidityLookbackBars + InpDRLookbackBars + InpDispSearchBars + InpBOSLookbackBars + 160, 260);
      int got = CopyRates(symbol, tf, 0, need, rates);
      stats.total_bars = got;
      if(got < InpDRLookbackBars + 20){
         _SetReject(stats, "insufficient_bars");
         return false;
      }

      double atr = ATRFromRates(rates, got, 14);
      if(atr <= 0){
         _SetReject(stats, "atr_unavailable");
         return false;
      }
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      int daily_bias_dir = _DirectionalBias(symbol, PERIOD_D1);
      int h4_bias_dir = _DirectionalBias(symbol, PERIOD_H4);
      int h1_bias_dir = _DirectionalBias(symbol, PERIOD_H1);

      int max_sweep_idx = MathMin(got - InpDRLookbackBars - 2, MathMax(1, PO3EffectiveMaxContextAgeBars()));
      bool found = false;
      PO3Context best;
      double best_score = -DBL_MAX;
      for(int sweep_idx=1; sweep_idx<=max_sweep_idx; sweep_idx++){
         stats.dr_checked++;
         double drh = -DBL_MAX, drl = DBL_MAX;
         for(int j=sweep_idx+1; j<=sweep_idx+InpDRLookbackBars && j<got; j++){
            drh = MathMax(drh, rates[j].high);
            drl = MathMin(drl, rates[j].low);
         }
         if(drh == -DBL_MAX || drl == DBL_MAX){
            _SetReject(stats, "dr_unavailable");
            continue;
         }

         double px = rates[sweep_idx].close;
         if(px > 0 && InpDRMaxPct > 0){
            double width_pct = (drh - drl) / px * 100.0;
            if(width_pct > InpDRMaxPct){
               stats.dr_width_rejected++;
               _SetReject(stats, "dr_width_pct=" + DoubleToString(width_pct, 2));
               continue;
            }
         }

         bool sweep_above = (rates[sweep_idx].high > drh && rates[sweep_idx].close < drh);
         bool sweep_below = (rates[sweep_idx].low  < drl && rates[sweep_idx].close > drl);
         if(!sweep_above && !sweep_below){
            _SetReject(stats, "no_closed_sweep");
            continue;
         }

         stats.sweep_found++;
         if(sweep_above) stats.sweep_above_found++;
         if(sweep_below) stats.sweep_below_found++;

         double sweep_ticks = _SweepPenetrationTicks(rates[sweep_idx], drh, drl, point);
         if(sweep_ticks < MathMax(0, InpMinSweepStrengthTicks)){
            _SetReject(stats, "sweep_too_weak_ticks=" + DoubleToString(sweep_ticks, 1));
            continue;
         }

         bool want_long = sweep_below;
         int disp_idx = -1;
         int min_disp_idx = MathMax(1, sweep_idx - InpDispSearchBars);
         bool saw_directional_move = false;
         double best_disp_quality = 0.0;
         for(int i=sweep_idx-1; i>=min_disp_idx; i--){
            bool dir_ok = (want_long ? (rates[i].close > rates[i].open) : (rates[i].close < rates[i].open));
            if(dir_ok){
               saw_directional_move = true;
               double bf = 0.0, ra = 0.0, vr = 0.0, ss = 0.0, fs = 0.0;
               double q = _DisplacementQuality(rates, got, i, want_long, atr, bf, ra, vr, ss, fs);
               best_disp_quality = MathMax(best_disp_quality, q);
            }
            if(_MatchesDisplacement(rates, got, i, want_long, atr)){
               disp_idx = i;
               break;
            }
         }
         if(disp_idx < 0){
            if(saw_directional_move){
               stats.displacement_quality_failed++;
               _SetReject(stats, "displacement_quality_failed best=" + DoubleToString(best_disp_quality, 2));
            } else {
               stats.displacement_missing++;
               _SetReject(stats, "displacement_missing");
            }
            if(InpRequireDisplacement) continue;
         }
         else {
            stats.displacement_passed++;
         }

         int bos_idx = -1;
         double bos_level = 0.0;
         bool htf_bos = false, htf_mss = false, htf_choch = false;
         int structure_anchor = (disp_idx >= 0 ? disp_idx : sweep_idx);
         int htf_pretrend_dir = 0;
         _DetectStructureShift(rates, got, structure_anchor, want_long, MathMax(1, InpStructureSwingSpan),
                               htf_bos, htf_mss, htf_choch, bos_idx, bos_level, htf_pretrend_dir);

         bool htf_internal_bos = false, htf_internal_mss = false, htf_internal_choch = false;
         int htf_internal_idx = -1, htf_internal_pretrend = 0;
         double htf_internal_level = 0.0;
         _DetectStructureShift(rates, got, structure_anchor, want_long, MathMax(1, InpInternalStructureSpan),
                               htf_internal_bos, htf_internal_mss, htf_internal_choch,
                               htf_internal_idx, htf_internal_level, htf_internal_pretrend);

         datetime context_time = rates[(disp_idx >= 0 ? disp_idx : sweep_idx)].time;
         string session_name = _SessionName(context_time);
         string killzone_name = _KillzoneName(context_time);
         bool in_killzone = _IsKillZone(context_time);
         datetime anchor_utc = _ToUtc(context_time);
         if(session_name == "OFF_HOURS") stats.session_offhours++;

         PO3Context cand;
         ZeroMemory(cand);
         cand.valid = true;
         PO3SetState(cand, PO3_RANGE_DEFINED, "dealing_range_defined");
         cand.sweep_side = (want_long ? "sell_side" : "buy_side");
         cand.has_sweep = true;
         cand.has_displacement = (disp_idx >= 0);
         cand.has_bos = (htf_bos && bos_idx >= 0);
         cand.has_follow_through = (disp_idx >= 0 && _HasDisplacementFollowThrough(rates, got, disp_idx, want_long));
         cand.htf_bos = cand.has_bos;
         cand.htf_swing_bos = cand.has_bos;
         cand.htf_internal_bos = htf_internal_bos;
         cand.htf_structure_type = _StructureTypeLabel(cand.has_bos, htf_mss, htf_choch, false, false);
         cand.developing_bos = false;
         cand.context_tier = "A";
         cand.context_age_bars = sweep_idx;
         cand.htf_pretrend_dir = htf_pretrend_dir;
         cand.bias_long = want_long;
         cand.bias_short = !want_long;
         cand.t_sweep = rates[sweep_idx].time;
         cand.t_disp = (disp_idx >= 0 ? rates[disp_idx].time : 0);
         cand.t_bos = (cand.has_bos ? rates[bos_idx].time : 0);
         cand.t_follow = (cand.has_follow_through ? rates[disp_idx - 1].time : 0);
         cand.dr_high = drh;
         cand.dr_low = drl;
         cand.dr_mid = (drh + drl) / 2.0;
         cand.manip_low = rates[sweep_idx].low;
         cand.manip_high = rates[sweep_idx].high;
         cand.bos_level = (cand.has_bos ? bos_level : (htf_internal_level > 0.0 ? htf_internal_level : bos_level));
         cand.sweep_strength = _SweepStrength(rates[sweep_idx], drh, drl);
         cand.session_name = session_name;
         cand.session_code = _SessionCode(session_name);
         cand.killzone_name = killzone_name;
         cand.in_killzone = in_killzone;
         cand.htf_mss = (htf_mss || htf_internal_mss);
         cand.htf_choch = (htf_choch || htf_internal_choch);
         if(StringLen(cand.htf_structure_type) == 0 && htf_internal_bos)
            cand.htf_structure_type = _StructureTypeLabel(htf_internal_bos, htf_internal_mss, htf_internal_choch, false, false);
         if(StringLen(cand.htf_structure_type) == 0 && cand.has_displacement)
            cand.htf_structure_type = "htf_displacement_confirmation";
         cand.daily_bias_dir = daily_bias_dir;
         cand.h4_bias_dir = h4_bias_dir;
         cand.h1_bias_dir = h1_bias_dir;
         cand.session_start = 0;
         cand.session_end = 0;
         cand.displacement_score = 0.0;
         cand.displacement_body_frac = 0.0;
         cand.displacement_range_atr = 0.0;
         cand.displacement_volume_ratio = 0.0;
         cand.displacement_speed_score = 0.0;
         cand.displacement_follow_score = (cand.has_follow_through ? 1.0 : 0.0);

         PO3SetState(cand, PO3_SWEEP_CONFIRMED, "closed_sweep_confirmed");

         if(disp_idx >= 0){
            cand.displacement_score = _DisplacementQuality(rates, got, disp_idx, want_long, atr,
                                                           cand.displacement_body_frac,
                                                           cand.displacement_range_atr,
                                                           cand.displacement_volume_ratio,
                                                           cand.displacement_speed_score,
                                                           cand.displacement_follow_score);
            PO3SetState(cand, PO3_DISPLACEMENT_CONFIRMED, "displacement_after_sweep");
         }

         datetime session_start_server, session_end_server;
         if(session_name != "OFF_HOURS" &&
            _SessionExtremesForDay(symbol, session_name, anchor_utc, cand.session_high, cand.session_low, session_start_server, session_end_server)){
            cand.session_start = session_start_server;
            cand.session_end = session_end_server;
         }
         _SessionExtremesForDay(symbol, "ASIA", anchor_utc, cand.asia_high, cand.asia_low, session_start_server, session_end_server);
         _SessionExtremesForDay(symbol, "LONDON", anchor_utc, cand.london_high, cand.london_low, session_start_server, session_end_server);
         _SessionExtremesForDay(symbol, "NEW_YORK", anchor_utc, cand.newyork_high, cand.newyork_low, session_start_server, session_end_server);
         _PreviousLevels(symbol, PERIOD_D1, cand.prev_day_high, cand.prev_day_low);
         _PreviousLevels(symbol, PERIOD_W1, cand.prev_week_high, cand.prev_week_low);

         cand.liquidity_target_high = want_long;
         cand.liquidity_target = _LiquidityTarget(rates, got, sweep_idx, want_long, (want_long ? drh : drl),
                                                  (want_long ? drh : drl), atr, cand,
                                                  cand.liquidity_kind, cand.liquidity_cluster_count);

         int impulse_bos_idx = (cand.has_bos ? bos_idx : (htf_internal_idx >= 0 ? htf_internal_idx : -1));
         _PopulateImpulseAnchors(rates, got, sweep_idx, disp_idx, impulse_bos_idx, want_long, cand.swing_low, cand.swing_high);

         _PopulateLtfStructure(symbol, want_long, cand);

         if(!cand.has_bos) stats.bos_missing++;
         bool internal_structure_ok = (cand.htf_internal_bos || cand.ltf_bos || cand.ltf_mss || cand.ltf_choch);
          bool displacement_leg_ok = (cand.has_displacement && cand.displacement_score >= PO3EffectiveDispMinQuality());
         bool developing_ok = (!cand.has_bos &&
                               InpAllowDevelopingBosContext &&
                               cand.has_displacement &&
                               (internal_structure_ok || displacement_leg_ok));
         if(cand.has_bos){
            cand.context_tier = "A";
            cand.developing_bos = false;
            PO3SetState(cand, PO3_STRUCTURE_CONFIRMED, "closed_sweep_displacement_bos");
            stats.tier_a_contexts++;
         } else if(developing_ok){
            cand.context_tier = "B";
            cand.developing_bos = true;
            PO3SetState(cand, PO3_DISPLACEMENT_CONFIRMED, "developing_bos_missing_internal_structure");
            stats.tier_b_contexts++;
         } else {
            _SetReject(stats, "bos_missing");
            continue;
         }

          cand.structure_type = (StringLen(cand.htf_structure_type) > 0 ? cand.htf_structure_type : cand.ltf_structure_type);
          cand.final_setup_class = _FinalPO3Class(cand);
          cand.po3_scope = PO3ScopeLabel(tf);

         cand.context_score = _ContextScore(cand);
         if(cand.context_tier == "B")
            cand.context_score = MathMax(0.0, cand.context_score - InpDevelopingContextSelectionPenalty);
         stats.context_candidates++;
         bool better = (!found || cand.context_score > best_score ||
                        (MathAbs(cand.context_score - best_score) < 0.0001 && cand.t_sweep > best.t_sweep));
         if(better){
            best = cand;
            best_score = cand.context_score;
            found = true;
         }
      }

      if(found){
         ctx = best;
         stats.final_context_selected = 1;
         stats.last_reject_reason = "ok";
         return true;
      }

      if(InpPreferRunningSweep){
         double drh = -DBL_MAX, drl = DBL_MAX;
         for(int i=1; i<=InpDRLookbackBars && i<got; i++){
            drh = MathMax(drh, rates[i].high);
            drl = MathMin(drl, rates[i].low);
         }
         bool running_above = (rates[0].high > drh && rates[0].close < drh);
         bool running_below = (rates[0].low  < drl && rates[0].close > drl);
         if(running_above || running_below){
            stats.sweep_found++;
            if(running_above) stats.sweep_above_found++;
            if(running_below) stats.sweep_below_found++;
            ctx.has_sweep = true;
            ctx.has_displacement = false;
            ctx.has_bos = false;
            ctx.sweep_running = true;
            PO3SetState(ctx, PO3_SWEEP_CONFIRMED, "running_current_candle_sweep");
            ctx.sweep_side = (running_below ? "running_sell_side" : "running_buy_side");
            ctx.structure_type = "";
            ctx.htf_structure_type = "";
            ctx.ltf_structure_type = "";
            ctx.bias_long = running_below;
            ctx.bias_short = running_above;
            ctx.t_sweep = TimeCurrent();
            ctx.t_disp = 0;
            ctx.t_bos = 0;
            ctx.dr_high = drh;
            ctx.dr_low = drl;
            ctx.dr_mid = (drh + drl) / 2.0;
            ctx.manip_low = rates[0].low;
            ctx.manip_high = rates[0].high;
            ctx.session_name = _SessionName(TimeCurrent());
            ctx.session_code = _SessionCode(ctx.session_name);
            ctx.killzone_name = _KillzoneName(TimeCurrent());
            ctx.in_killzone = _IsKillZone(TimeCurrent());
            ctx.sweep_strength = _SweepStrength(rates[0], drh, drl) * InpRunningStrengthMult;
            ctx.htf_mss = false;
            ctx.htf_choch = false;
            ctx.htf_bos = false;
            ctx.htf_swing_bos = false;
            ctx.htf_internal_bos = false;
            ctx.developing_bos = true;
            ctx.context_tier = "C";
            ctx.context_age_bars = 0;
            ctx.htf_pretrend_dir = 0;
            ctx.has_follow_through = false;
            ctx.t_follow = 0;
            ctx.displacement_score = 0.0;
            ctx.displacement_body_frac = 0.0;
            ctx.displacement_range_atr = 0.0;
            ctx.displacement_volume_ratio = 0.0;
            ctx.displacement_speed_score = 0.0;
            ctx.displacement_follow_score = 0.0;
            ctx.daily_bias_dir = daily_bias_dir;
            ctx.h4_bias_dir = h4_bias_dir;
            ctx.h1_bias_dir = h1_bias_dir;
            ctx.liquidity_target_high = ctx.bias_long;
            ctx.liquidity_target = (ctx.bias_long ? drh : drl);
            ctx.liquidity_kind = "running_sweep_fallback";
            ctx.liquidity_cluster_count = 1;
            ctx.swing_low = (ctx.bias_long ? MathMin(rates[0].low, drl) : drl);
            ctx.swing_high = (ctx.bias_long ? drh : MathMax(rates[0].high, drh));
            _PreviousLevels(symbol, PERIOD_D1, ctx.prev_day_high, ctx.prev_day_low);
            _PreviousLevels(symbol, PERIOD_W1, ctx.prev_week_high, ctx.prev_week_low);
            _PopulateLtfStructure(symbol, ctx.bias_long, ctx);
             ctx.structure_type = (StringLen(ctx.ltf_structure_type) > 0 ? ctx.ltf_structure_type : "running_sweep_developing");
             ctx.final_setup_class = _FinalPO3Class(ctx);
             ctx.po3_scope = PO3ScopeLabel(tf);
            ctx.context_score = MathMax(0.0, _ContextScore(ctx) - InpDevelopingContextSelectionPenalty * 1.5);
            ctx.valid = true;
            stats.context_candidates++;
            stats.final_context_selected = 1;
            stats.last_reject_reason = "ok_running_sweep";
            return true;
         }
      }

      if(stats.last_reject_reason == ""){
         if(stats.sweep_found <= 0) stats.last_reject_reason = "no_closed_sweep";
         else if(stats.displacement_missing > 0 || stats.displacement_quality_failed > 0) stats.last_reject_reason = "displacement_missing_or_low_quality";
         else if(stats.bos_missing > 0) stats.last_reject_reason = "bos_missing";
         else stats.last_reject_reason = "no_context_candidate";
      }
      PO3SetState(ctx, PO3_INVALIDATED, stats.last_reject_reason);
      ctx.final_setup_class = "rejected_" + stats.last_reject_reason;
      return false;
   }

   bool CheckOTE(const TradePlan &p) const {
      if(!InpRequireOTE) return true;
      string branch = p.entry_branch;
      if(StringLen(branch) == 0) branch = p.entry_model;
      string family = p.setup_family;
      string fvg_class = p.fvg_execution_class;

      bool continuation_family = (family == "continuation" ||
                                  branch == "continuation_reentry" ||
                                  branch == "nested_htf_ltf_fvg" ||
                                  branch == "nested_fvg_edge" ||
                                  branch == "session_reentry" ||
                                  (branch == "range_reentry" && family != "reversal") ||
                                  branch == "breaker_retest" ||
                                  p.fvg.continuation ||
                                  fvg_class == "continuation_reentry");

      bool reversal_ote_family = (family == "reversal" ||
                                  branch == "ote_inside_fvg" ||
                                  (branch == "fvg_mid" && p.fvg.reversal) ||
                                  (branch == "range_reentry" && family == "reversal"));

      if(continuation_family && !reversal_ote_family) return true;
      if(!reversal_ote_family) return true;

      double a = p.po3.swing_low;
      double b = p.po3.swing_high;
      if(a<=0 || b<=0 || a==b) return true;
      double low = MathMin(a, b), high = MathMax(a, b);
      double range = high - low;
      if(range <= 0) return true;

      if(p.is_buy){
         double z1 = high - range * InpOTEHigh;
         double z2 = high - range * InpOTELow;
         double lo = MathMin(z1, z2), hi = MathMax(z1, z2);
         return (p.entry_est >= lo && p.entry_est <= hi);
      }

      double z1 = low + range * InpOTELow;
      double z2 = low + range * InpOTEHigh;
      double lo = MathMin(z1, z2), hi = MathMax(z1, z2);
      return (p.entry_est >= lo && p.entry_est <= hi);
   }
};

#endif
