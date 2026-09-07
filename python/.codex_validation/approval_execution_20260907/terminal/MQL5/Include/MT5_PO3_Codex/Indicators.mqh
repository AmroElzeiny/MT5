//+------------------------------------------------------------------+
//| Indicators.mqh - lightweight indicator functions                  |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_INDICATORS_MQH__
#define __PO3_AIGATE_INDICATORS_MQH__

double _TR(const MqlRates &cur, const MqlRates &prev) {
   double h = cur.high;
   double l = cur.low;
   double pc = prev.close;
   double tr1 = h - l;
   double tr2 = MathAbs(h - pc);
   double tr3 = MathAbs(l - pc);
   return MathMax(tr1, MathMax(tr2, tr3));
}

// Simple ATR (SMA of TR) over `period` bars using series rates.
// rates[0] = current bar, rates[1] = prev closed, ...
double ATRFromRates(const MqlRates &rates[], const int n_rates, const int period=14) {
   if(n_rates < period + 2) return 0.0;
   double sum = 0.0;
   for(int i=1; i<=period; i++){
      sum += _TR(rates[i], rates[i+1]);
   }
   return sum / period;
}

double ATRPctFromRates(const MqlRates &rates[], const int n_rates, const int period=14) {
   double atr = ATRFromRates(rates, n_rates, period);
   double px = rates[1].close;
   if(px <= 0) return 0.0;
   return atr / px;
}

double ADXValue(const string symbol, const ENUM_TIMEFRAMES tf, const int period=14) {
   int handle = iADX(symbol, tf, period);
   if(handle == INVALID_HANDLE) return 0.0;
   double buf[];
   ArraySetAsSeries(buf, true);
   int got = CopyBuffer(handle, 0, 1, 1, buf);
   IndicatorRelease(handle);
   if(got < 1) return 0.0;
   return buf[0];
}

double AverageDailyRangePct(const string symbol, const int days=20) {
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int got = CopyRates(symbol, PERIOD_D1, 1, MathMax(2, days), rates);
   if(got < 2) return 0.0;
   double sum = 0.0;
   int count = 0;
   for(int i=0; i<got; i++){
      if(rates[i].close <= 0) continue;
      sum += (rates[i].high - rates[i].low) / rates[i].close;
      count++;
   }
   if(count <= 0) return 0.0;
   return sum / count;
}

double _Clamp01(const double v) {
   if(v < 0.0) return 0.0;
   if(v > 1.0) return 1.0;
   return v;
}

// Signed trend slope in percent-per-bar.
double TrendSlopePct(const MqlRates &rates[], const int n_rates, const int N=50) {
   if(n_rates < N + 2) return 0.0;
   // Series arrays are newest-first, so map x from oldest -> newest to keep the sign intuitive.
   double sumx=0, sumy=0, sumxx=0, sumxy=0;
   for(int i=1; i<=N; i++){
      double x = (double)(N - i + 1);
      double y = rates[i].close;
      sumx += x;
      sumy += y;
      sumxx += x*x;
      sumxy += x*y;
   }
   double denom = (N*sumxx - sumx*sumx);
   if(MathAbs(denom) < 1e-9) return 0.0;
   double slope = (N*sumxy - sumx*sumy) / denom; // price units per bar
   double px = rates[1].close;
   if(px <= 0) return 0.0;
   return slope / px;
}

double _TrendEfficiencyRatio(const MqlRates &rates[], const int n_rates, const int N=50) {
   if(n_rates < N + 2) return 0.0;
   double net_move = MathAbs(rates[1].close - rates[N].close);
   double path = 0.0;
   for(int i=1; i<N; i++){
      path += MathAbs(rates[i].close - rates[i+1].close);
   }
   if(path <= 0.0) return 0.0;
   return _Clamp01(net_move / path);
}

double _TrendDirectionalFraction(const MqlRates &rates[], const int n_rates, const int N=50) {
   if(n_rates < N + 2) return 0.0;
   double net_delta = rates[1].close - rates[N].close;
   if(MathAbs(net_delta) < 1e-9) return 0.5;
   bool want_up = (net_delta > 0.0);
   int matches = 0;
   int count = 0;
   for(int i=1; i<N; i++){
      double delta = rates[i].close - rates[i+1].close;
      if(MathAbs(delta) < 1e-9) continue;
      bool up = (delta > 0.0);
      if(up == want_up) matches++;
      count++;
   }
   if(count <= 0) return 0.5;
   return (double)matches / (double)count;
}

double _TrendRegressionFit(const MqlRates &rates[], const int n_rates, const int N=50) {
   if(n_rates < N + 2) return 0.0;
   double sumx=0, sumy=0, sumxx=0, sumxy=0;
   for(int i=1; i<=N; i++){
      double x = (double)(N - i + 1);
      double y = rates[i].close;
      sumx += x;
      sumy += y;
      sumxx += x*x;
      sumxy += x*y;
   }
   double denom = (N*sumxx - sumx*sumx);
   if(MathAbs(denom) < 1e-9) return 0.0;
   double slope = (N*sumxy - sumx*sumy) / denom;
   double intercept = (sumy - slope * sumx) / N;
   double mean = sumy / N;
   double ss_res = 0.0;
   double ss_tot = 0.0;
   for(int i=1; i<=N; i++){
      double x = (double)(N - i + 1);
      double y = rates[i].close;
      double yhat = intercept + slope * x;
      ss_res += (y - yhat) * (y - yhat);
      ss_tot += (y - mean) * (y - mean);
   }
   if(ss_tot <= 1e-9) return 0.0;
   return _Clamp01(1.0 - (ss_res / ss_tot));
}

// Trend strength (0..1): directional travel, path efficiency, candle-to-candle agreement, and regression fit.
double TrendStrength(const MqlRates &rates[], const int n_rates, const int N=50) {
   if(n_rates < N + 2) return 0.0;
   double atr = ATRFromRates(rates, n_rates, 14);
   if(atr <= 0.0) return 0.0;

   double px = rates[1].close;
   if(px <= 0.0) return 0.0;

   double net_move = MathAbs(rates[1].close - rates[N].close);
   double slope_pct = MathAbs(TrendSlopePct(rates, n_rates, N));
   double slope_move = slope_pct * px * (N - 1);
   double travel_atr = MathMax(net_move, slope_move) / atr;
   double move_component = 1.0 - MathExp(-0.55 * travel_atr);
   double efficiency = _TrendEfficiencyRatio(rates, n_rates, N);
   double directional = _TrendDirectionalFraction(rates, n_rates, N);
   double fit = _TrendRegressionFit(rates, n_rates, N);

   double strength = move_component * 0.45
                   + efficiency * 0.25
                   + directional * 0.15
                   + fit * 0.15;
   return _Clamp01(strength);
}

double ExpansionRatioFromRates(const MqlRates &rates[], const int n_rates, const int short_period=14, const int long_period=50) {
   double short_atr = ATRFromRates(rates, n_rates, short_period);
   double long_atr = ATRFromRates(rates, n_rates, long_period);
   if(short_atr <= 0 || long_atr <= 0) return 0.0;
   return short_atr / long_atr;
}

double AnchoredVWAPFromRates(const MqlRates &rates[], const int n_rates, const datetime anchor_time) {
   double num = 0.0;
   double den = 0.0;
   for(int i=1; i<n_rates; i++){
      if(anchor_time > 0 && rates[i].time < anchor_time) break;
      double typical = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double weight = (double)MathMax((long)1, rates[i].tick_volume);
      num += typical * weight;
      den += weight;
   }
   if(den <= 0) return 0.0;
   return num / den;
}

double CandleBodyFrac(const MqlRates &r) {
   double range = r.high - r.low;
   if(range <= 0) return 0.0;
   double body = MathAbs(r.close - r.open);
   return body / range;
}

double CloseNearExtremeFrac(const MqlRates &r, const bool want_close_near_high) {
   double range = r.high - r.low;
   if(range <= 0) return 1.0;
   if(want_close_near_high) return (r.high - r.close) / range; // 0 means at high
   return (r.close - r.low) / range; // 0 means at low
}

#endif
