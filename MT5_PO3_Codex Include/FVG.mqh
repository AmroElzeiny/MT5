//+------------------------------------------------------------------+
//| FVG.mqh - 3-candle FVG detection + scoring                       |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_FVG_MQH__
#define __PO3_AIGATE_FVG_MQH__
#include "Types.mqh"
#include "Indicators.mqh"
#include "Config.mqh"

struct FVGDiagnostics {
   int ltf_bars;
   int raw_fvg_found;
   int no_raw_fvg_found;
   int fvg_too_small;
   int fvg_wrong_direction;
   int fvg_before_manip;
   int fvg_before_sweep;
   int fvg_after_disp_required;
   int fvg_fully_mitigated;
   int fvg_structure_invalidated;
   int fvg_stale;
   int htf_overlap_failed;
   int no_enabled_entry_family;
   int all_candidates_rejected;
   int accepted;
};

void FVGDiagReset(FVGDiagnostics &diag) {
   ZeroMemory(diag);
}

string FVGDiagSummary(const FVGDiagnostics &diag) {
   return "ltf_bars=" + IntegerToString(diag.ltf_bars)
          + " no_raw_fvg_found=" + IntegerToString(diag.no_raw_fvg_found)
          + " raw_fvg_found=" + IntegerToString(diag.raw_fvg_found)
          + " fvg_too_small=" + IntegerToString(diag.fvg_too_small)
          + " fvg_wrong_direction=" + IntegerToString(diag.fvg_wrong_direction)
          + " fvg_before_sweep=" + IntegerToString(diag.fvg_before_sweep)
          + " fvg_before_manip=" + IntegerToString(diag.fvg_before_manip)
          + " fvg_after_disp_required=" + IntegerToString(diag.fvg_after_disp_required)
          + " fvg_fully_mitigated=" + IntegerToString(diag.fvg_fully_mitigated)
          + " fvg_structure_invalidated=" + IntegerToString(diag.fvg_structure_invalidated)
          + " fvg_stale=" + IntegerToString(diag.fvg_stale)
          + " htf_overlap_failed=" + IntegerToString(diag.htf_overlap_failed)
          + " no_enabled_entry_family=" + IntegerToString(diag.no_enabled_entry_family)
          + " all_candidates_rejected=" + IntegerToString(diag.all_candidates_rejected)
          + " accepted=" + IntegerToString(diag.accepted);
}

class CFVG {
private:
   string m_cache_ltf_symbol;
   ENUM_TIMEFRAMES m_cache_ltf_tf;
   datetime m_cache_ltf_bar;
   int m_cache_ltf_requested;
   MqlRates m_cache_ltf_rates[];
   string m_cache_htf_symbol;
   ENUM_TIMEFRAMES m_cache_htf_tf;
   datetime m_cache_htf_bar;
   int m_cache_htf_requested;
   MqlRates m_cache_htf_rates[];

   int _LoadRatesCached(const string symbol, const ENUM_TIMEFRAMES tf, const int requested,
                        string &cache_symbol, ENUM_TIMEFRAMES &cache_tf,
                        datetime &cache_bar, int &cache_requested, MqlRates &cache_rates[]) {
      datetime current_bar = iTime(symbol, tf, 0);
      if(cache_symbol == symbol && cache_tf == tf && cache_bar == current_bar &&
         cache_requested >= requested && ArraySize(cache_rates) > 0)
         return ArraySize(cache_rates);

      ArraySetAsSeries(cache_rates, true);
      int got = CopyRates(symbol, tf, 0, requested, cache_rates);
      cache_symbol = symbol;
      cache_tf = tf;
      cache_bar = current_bar;
      cache_requested = requested;
      return got;
   }

   void _RetraceBand(const FVGZone &z, double &band_low, double &band_high) const {
      double width = MathMax(0.0, z.upper - z.lower);
      band_low = z.lower + width * InpRetraceTouchLow;
      band_high = z.lower + width * InpRetraceTouchHigh;
      if(band_low > band_high){
         double tmp = band_low;
         band_low = band_high;
         band_high = tmp;
      }
   }

   double _RetraceAnchor(const double lower, const double upper) const {
      double width = MathMax(0.0, upper - lower);
      double band_low = lower + width * InpRetraceTouchLow;
      double band_high = lower + width * InpRetraceTouchHigh;
      if(band_low > band_high){
         double tmp = band_low;
         band_low = band_high;
         band_high = tmp;
      }
      return (band_low + band_high) * 0.5;
   }

   double _EntryReference(const bool bull, const double lower, const double upper) const {
      if(InpRequireFvgMidMitigation) return _RetraceAnchor(lower, upper);
      return (bull ? upper : lower);
   }

   bool _OverlapsRange(const double low_a, const double high_a, const double low_b, const double high_b) const {
      return (low_a <= high_b && high_a >= low_b);
   }

   void _UpdateHistoricalState(const MqlRates &rates[], const int n, const int idx_newer,
                               const double lower, const double upper, FVGZone &z) const {
      z.touched = false;
      z.mid_mitigated = false;
      z.fully_filled = false;
      z.invalidated = false;
      z.entry_invalid = false;
      z.structure_invalidated = false;
      z.mitigation_state = "virgin";
      z.invalidation_reason = "ok";
      z.mitigation_depth_frac = 0.0;
      z.age_bars = idx_newer;

      double width = MathMax(0.0000001, upper - lower);
      double eps = MathMax(width * 0.04, 0.0000001);
      double band_low = 0.0, band_high = 0.0;
      _RetraceBand(z, band_low, band_high);

      for(int i=1; i<idx_newer && i<n; i++){
         if(_OverlapsRange(rates[i].low, rates[i].high, lower, upper)) z.touched = true;
         if(_OverlapsRange(rates[i].low, rates[i].high, band_low, band_high)) z.mid_mitigated = true;

         double depth_frac = 0.0;
         if(z.bullish){
            if(rates[i].low < upper){
               double penetration = upper - MathMax(lower, rates[i].low);
               depth_frac = penetration / width;
            }
         } else {
            if(rates[i].high > lower){
               double penetration = MathMin(upper, rates[i].high) - lower;
               depth_frac = penetration / width;
            }
         }
         z.mitigation_depth_frac = MathMax(z.mitigation_depth_frac, MathMax(0.0, MathMin(1.0, depth_frac)));

         if(z.bullish){
            if(rates[i].low < (lower - eps)){
               z.invalidated = true;
               z.structure_invalidated = true;
               z.mitigation_state = "structure_invalidated";
               z.invalidation_reason = "fvg_structure_invalidated";
               break;
            }
            if(rates[i].low <= (lower + eps)){
               z.fully_filled = true;
               z.entry_invalid = true;
               z.mitigation_state = "fully_mitigated";
            } else if(z.mid_mitigated) z.mitigation_state = "mid_mitigated";
            else if(z.touched) z.mitigation_state = "touched";
         } else {
            if(rates[i].high > (upper + eps)){
               z.invalidated = true;
               z.structure_invalidated = true;
               z.mitigation_state = "structure_invalidated";
               z.invalidation_reason = "fvg_structure_invalidated";
               break;
            }
            if(rates[i].high >= (upper - eps)){
               z.fully_filled = true;
               z.entry_invalid = true;
               z.mitigation_state = "fully_mitigated";
            } else if(z.mid_mitigated) z.mitigation_state = "mid_mitigated";
            else if(z.touched) z.mitigation_state = "touched";
         }
      }
      z.mitigated = z.mid_mitigated;
      if(z.entry_invalid && z.invalidation_reason == "ok")
         z.invalidation_reason = "fvg_fully_mitigated_before_entry";
   }

   string _ExecutionClass(const FVGZone &z) const {
      if(z.invalidated || z.structure_invalidated) return "invalidated_fvg";
      if(z.fully_filled) return "fully_mitigated_fvg";
      if(z.entry_invalid) return "entry_invalid_fvg";
      int stale_soft = 36;
      ENUM_TIMEFRAMES htf = PO3EffectiveHTF();
      if(htf == PERIOD_H1) stale_soft = 96;
      else if(htf == PERIOD_H4) stale_soft = 192;
      else if(htf >= PERIOD_D1) stale_soft = 384;
      if(z.age_bars > stale_soft) return "stale_fvg";
      if(z.mid_mitigated) return "mid_mitigated_fvg";
      if(z.touched) return "touched_fvg";
      if(z.age_bars > MathMax(24, stale_soft / 2)) return "stale_fvg";
      return "virgin_fvg";
   }

   double _ExecutionClassScoreAdjustment(const FVGZone &z) const {
      string cls = _ExecutionClass(z);
      if(cls == "fresh_fvg" || cls == "virgin_fvg") return 1.4;
      if(cls == "touched_fvg") return 0.8;
      if(cls == "mid_mitigated_fvg") return 0.45;
      if(cls == "stale_fvg") return -3.0;
      if(cls == "fully_mitigated_fvg" || cls == "entry_invalid_fvg") return -8.0;
      return -8.0;
   }

   int _ExecutionClassMaxAgeBars(const string cls) const {
      ENUM_TIMEFRAMES htf = PO3EffectiveHTF();
      if(htf >= PERIOD_H4){
         if(cls == "fresh_fvg" || cls == "virgin_fvg") return MathMax(120, InpLookbackLtfBars);
         if(cls == "touched_fvg") return MathMax(180, InpLookbackLtfBars);
         if(cls == "mid_mitigated_fvg") return MathMax(180, InpLookbackLtfBars);
         if(cls == "stale_fvg") return MathMax(240, InpLookbackLtfBars);
         return 0;
      }
      if(htf == PERIOD_H1){
         if(cls == "fresh_fvg" || cls == "virgin_fvg") return 144;
         if(cls == "touched_fvg") return 180;
         if(cls == "mid_mitigated_fvg") return 180;
         if(cls == "stale_fvg") return 360;
         return 0;
      }
      if(cls == "fresh_fvg" || cls == "virgin_fvg") return 30;
      if(cls == "touched_fvg") return 32;
      if(cls == "mid_mitigated_fvg") return 28;
      if(cls == "stale_fvg") return 48;
      return 0;
   }

   double _OteZoneScore(const PO3Context &po3, const double price_mid) {
      double a = po3.swing_low;
      double b = po3.swing_high;
      if(a <= 0 || b <= 0 || a == b) return 0.0;
      double low = MathMin(a, b), high = MathMax(a, b);
      double range = high - low;
      if(range <= 0) return 0.0;

      double z1 = 0.0, z2 = 0.0;
      if(po3.bias_long){
         z1 = high - range * InpOTEHigh;
         z2 = high - range * InpOTELow;
      } else {
         z1 = low + range * InpOTELow;
         z2 = low + range * InpOTEHigh;
      }

      double lo = MathMin(z1, z2), hi = MathMax(z1, z2);
      if(price_mid >= lo && price_mid <= hi) return 16.0;

      double zone_dist = (price_mid < lo ? lo - price_mid : price_mid - hi);
      double ratio = zone_dist / range;
      return MathMax(0.0, 7.0 - ratio * 70.0);
   }

   double _WidthScore(const double width_atr) {
      if(width_atr <= 0) return 0.0;
      double centered = 18.0 - MathAbs(width_atr - 0.35) * 28.0;
      if(centered < 0) centered = 0.0;
      return centered;
   }

   double _OriginScore(const PO3Context &po3, const bool bull, const double lower, const double upper, const datetime t_form) {
      double entry_ref = _EntryReference(bull, lower, upper);
      double anchor = po3.bos_level;
      double leg = MathAbs(anchor - (bull ? po3.manip_low : po3.manip_high));
      double price_score = 0.0;
      if(leg > 0){
         double dist = MathAbs(entry_ref - anchor);
         price_score = MathMax(0.0, 6.0 - (dist / leg) * 8.0);
      }

      double time_score = 0.0;
      datetime disp_time = (po3.t_disp > 0 ? po3.t_disp : po3.t_sweep);
      if(disp_time > 0 && t_form > disp_time){
         double minutes_after = (double)(t_form - disp_time) / 60.0;
         time_score = MathMax(0.0, 6.0 - minutes_after / 45.0);
      }
      return price_score + time_score;
   }

   double _CleanlinessScore(const MqlRates &older_bar, const MqlRates &mid_bar, const MqlRates &newer_bar,
                            const bool bull, const double atr) {
      double body = MathAbs(mid_bar.close - mid_bar.open);
      double body_frac = CandleBodyFrac(mid_bar);
      double impulse = (atr > 0 ? body / atr : 0.0);
      double continuation = 0.0;
      if(bull){
         if(mid_bar.close > older_bar.high) continuation += 2.0;
         if(newer_bar.close >= mid_bar.close) continuation += 2.0;
      } else {
         if(mid_bar.close < older_bar.low) continuation += 2.0;
         if(newer_bar.close <= mid_bar.close) continuation += 2.0;
      }
      return MathMin(10.0, body_frac * 4.0 + impulse * 3.0 + continuation);
   }

   double _MiddleBodyQualityScore(const MqlRates &mid_bar) const {
      return MathMin(10.0, CandleBodyFrac(mid_bar) * 10.0);
   }

   double _VolumeImpulseScore(const MqlRates &rates[], const int got, const int mid_idx) const {
      double sum = 0.0;
      int used = 0;
      for(int i=mid_idx + 1; i<got && used<8; i++, used++)
         sum += (double)MathMax((long)1, rates[i].tick_volume);
      double avg = (used > 0 ? sum / used : 0.0);
      if(avg <= 0.0) return 5.0;
      double ratio = (double)MathMax((long)1, rates[mid_idx].tick_volume) / avg;
      return MathMin(10.0, MathMax(0.0, ratio * 5.0));
   }

   double _OpposingObstructionScore(const MqlRates &htf_rates[], const int got, const bool bull,
                                    const double lower, const double upper, const double atr) const {
      if(got < 10 || atr <= 0.0) return 8.0;
      double entry_ref = _EntryReference(bull, lower, upper);
      double best_dist = DBL_MAX;
      int limit = MathMin(got - 2, MathMax(20, InpHTFFvgLookbackBars));
      for(int idx=1; idx<limit; idx++){
         int older = idx + 2;
         int newer = idx;
         if(older >= got || newer >= got) continue;
         bool opposing = false;
         double boundary = 0.0;
         if(bull && htf_rates[older].low > htf_rates[newer].high){
            opposing = true;
            boundary = htf_rates[newer].high;
         } else if(!bull && htf_rates[older].high < htf_rates[newer].low){
            opposing = true;
            boundary = htf_rates[newer].low;
         }
         if(!opposing || boundary <= 0.0) continue;
         if(bull && boundary <= entry_ref) continue;
         if(!bull && boundary >= entry_ref) continue;
         best_dist = MathMin(best_dist, MathAbs(boundary - entry_ref));
      }
      if(best_dist == DBL_MAX) return 9.0;
      double dist_atr = best_dist / atr;
      if(dist_atr < 0.50) return 1.0;
      if(dist_atr < 1.00) return 3.0;
      if(dist_atr < 1.50) return 5.5;
      return 8.0;
   }

   double _AgeScore(const int idx_newer) {
      double decay = 0.35;
      ENUM_TIMEFRAMES htf = PO3EffectiveHTF();
      if(htf == PERIOD_H1) decay = 0.12;
      else if(htf == PERIOD_H4) decay = 0.055;
      else if(htf >= PERIOD_D1) decay = 0.030;
      return MathMax(0.0, 12.0 - (double)MathMax(0, idx_newer - 1) * decay);
   }

   void _HtfContextScore(const MqlRates &htf_rates[], const int got, const bool bull,
                         const double lower, const double upper,
                         double &overlap_score, double &nesting_score) {
      overlap_score = 0.0;
      nesting_score = 0.0;
      int limit = MathMin(got - 2, MathMax(20, InpHTFFvgLookbackBars));
      for(int idx=1; idx<limit; idx++){
         int older = idx + 2;
         int newer = idx;
         if(older >= got || newer >= got) continue;

         bool htf_bull = (htf_rates[older].high < htf_rates[newer].low);
         bool htf_bear = (htf_rates[older].low  > htf_rates[newer].high);
         if(bull && !htf_bull) continue;
         if(!bull && !htf_bear) continue;

         double htf_lower = htf_bull ? htf_rates[older].high : htf_rates[newer].high;
         double htf_upper = htf_bull ? htf_rates[newer].low  : htf_rates[older].low;
         if(htf_upper <= htf_lower) continue;

         double overlap = MathMin(upper, htf_upper) - MathMax(lower, htf_lower);
         if(overlap <= 0) continue;

         double zone_width = upper - lower;
         double htf_width = htf_upper - htf_lower;
         if(zone_width <= 0 || htf_width <= 0) continue;

         double overlap_frac = overlap / zone_width;
         overlap_score = MathMax(overlap_score, MathMin(10.0, overlap_frac * 8.0));

         if(lower >= htf_lower && upper <= htf_upper){
            nesting_score = MathMax(nesting_score, 8.0);
         } else {
            double nest_ratio = overlap / MathMax(zone_width, htf_width);
            nesting_score = MathMax(nesting_score, MathMin(6.0, nest_ratio * 10.0));
         }
      }
   }

   double _RetestQualityScore(const bool bull, const double lower, const double upper, const double current_px, const double point) {
      double zone_width = upper - lower;
      if(zone_width <= 0) return 0.0;
      double entry_ref = _EntryReference(bull, lower, upper);
      double dist_ticks = MathAbs(current_px - entry_ref) / MathMax(point, 0.0000001);
      double score = 9.0 / (1.0 + dist_ticks / 25.0);

      if(bull && current_px < lower) score *= 0.4;
      if(!bull && current_px > upper) score *= 0.4;
      if(bull && current_px > upper) score += 1.0;
      if(!bull && current_px < lower) score += 1.0;
      if(score < 0) score = 0.0;
      return MathMin(10.0, score);
   }

   string _ContextType(const PO3Context &po3, const bool bull) const {
      int dir = (bull ? 1 : -1);
      bool hierarchy_aligned = (po3.daily_bias_dir == dir || po3.h4_bias_dir == dir || po3.h1_bias_dir == dir);
      bool reversal_shift = (po3.htf_mss || po3.htf_choch || po3.ltf_mss || po3.ltf_choch);
      bool pretrend_same = (po3.htf_pretrend_dir == dir || po3.ltf_pretrend_dir == dir);
      if(reversal_shift && !pretrend_same) return "reversal";
      if(hierarchy_aligned && pretrend_same) return "continuation";
      if(reversal_shift) return "reversal";
      return "continuation";
   }

   void _ContextTypeScores(const PO3Context &po3, const bool bull,
                           double &continuation_score, double &reversal_score) const {
      continuation_score = 0.0;
      reversal_score = 0.0;
      int dir = (bull ? 1 : -1);
      if(po3.daily_bias_dir == dir) continuation_score += 2.0;
      if(po3.h4_bias_dir == dir) continuation_score += 1.8;
      if(po3.h1_bias_dir == dir) continuation_score += 1.2;
      if(po3.htf_pretrend_dir == dir) continuation_score += 1.2;
      if(po3.ltf_pretrend_dir == dir) continuation_score += 0.8;
      if(po3.htf_mss || po3.htf_choch) reversal_score += 2.1;
      if(po3.ltf_mss || po3.ltf_choch) reversal_score += 1.4;
      if(po3.has_bos) reversal_score += 1.0;
      if(po3.has_displacement) reversal_score += 0.8;
      if(po3.htf_pretrend_dir == -dir) reversal_score += 1.6;
      if(po3.ltf_pretrend_dir == -dir) reversal_score += 0.6;
      continuation_score = MathMin(8.0, continuation_score);
      reversal_score = MathMin(8.0, reversal_score);
   }

   void _SortCandidates(FVGZone &out[]) const {
      for(int i=1; i<ArraySize(out); i++){
         for(int j=i; j>0; j--){
            if(out[j].score > out[j-1].score){
               FVGZone tmp = out[j-1];
               out[j-1] = out[j];
               out[j] = tmp;
            } else {
               break;
            }
         }
      }
   }

   bool _ShouldCluster(const FVGZone &a, const FVGZone &b, const double point, const double atr) const {
      if(a.bullish != b.bullish) return false;
      if(StringLen(a.context_type) > 0 && StringLen(b.context_type) > 0 && a.context_type != b.context_type) return false;

      double overlap = MathMin(a.upper, b.upper) - MathMax(a.lower, b.lower);
      double width_a = MathMax(point, a.upper - a.lower);
      double width_b = MathMax(point, b.upper - b.lower);
      double min_width = MathMax(point, MathMin(width_a, width_b));
      double entry_dist = MathAbs(_EntryReference(a.bullish, a.lower, a.upper) - _EntryReference(b.bullish, b.lower, b.upper));
      double dist_limit = MathMax(MathMax(width_a, width_b) * 0.65, MathMax(point * PO3EffectiveMinFvgWidthTicks() * 1.5, atr * 0.18));
      double overlap_frac = (overlap > 0.0 ? overlap / min_width : 0.0);
      int time_gap = MathAbs(a.idx_newer - b.idx_newer);

      return ((overlap_frac >= 0.35) || (entry_dist <= dist_limit)) && time_gap <= MathMax(18, InpDRLookbackBars);
   }

   void _MergeCandidate(FVGZone &dst, const FVGZone &src) const {
      bool touched = dst.touched || src.touched;
      bool mitigated = dst.mitigated || src.mitigated;
      bool mid_mitigated = dst.mid_mitigated || src.mid_mitigated;
      bool fully_filled = dst.fully_filled || src.fully_filled;
      bool invalidated = dst.invalidated || src.invalidated;
      bool entry_invalid = dst.entry_invalid || src.entry_invalid;
      bool structure_invalidated = dst.structure_invalidated || src.structure_invalidated;

      if(src.score > dst.score){
         FVGZone keep = src;
         keep.touched = touched;
         keep.mitigated = mitigated;
         keep.mid_mitigated = mid_mitigated;
         keep.fully_filled = fully_filled;
         keep.invalidated = invalidated;
         keep.entry_invalid = entry_invalid;
         keep.structure_invalidated = structure_invalidated;
         if(StringLen(keep.mitigation_state) == 0) keep.mitigation_state = dst.mitigation_state;
         if(StringLen(keep.invalidation_reason) == 0) keep.invalidation_reason = dst.invalidation_reason;
         keep.execution_class = _ExecutionClass(keep);
         dst = keep;
      } else {
         dst.touched = touched;
         dst.mitigated = mitigated;
         dst.mid_mitigated = mid_mitigated;
         dst.fully_filled = fully_filled;
         dst.invalidated = invalidated;
         dst.entry_invalid = entry_invalid;
         dst.structure_invalidated = structure_invalidated;
         if(StringLen(dst.mitigation_state) == 0) dst.mitigation_state = src.mitigation_state;
         if(StringLen(dst.invalidation_reason) == 0 || dst.invalidation_reason == "ok") dst.invalidation_reason = src.invalidation_reason;
         dst.execution_class = _ExecutionClass(dst);
         dst.score = MathMax(dst.score, src.score);
         dst.origin_score = MathMax(dst.origin_score, src.origin_score);
         dst.cleanliness_score = MathMax(dst.cleanliness_score, src.cleanliness_score);
         dst.age_score = MathMax(dst.age_score, src.age_score);
         dst.nesting_score = MathMax(dst.nesting_score, src.nesting_score);
         dst.htf_overlap_score = MathMax(dst.htf_overlap_score, src.htf_overlap_score);
         dst.retest_score = MathMax(dst.retest_score, src.retest_score);
         dst.continuation_score = MathMax(dst.continuation_score, src.continuation_score);
         dst.reversal_score = MathMax(dst.reversal_score, src.reversal_score);
         dst.displacement_candle_score = MathMax(dst.displacement_candle_score, src.displacement_candle_score);
         dst.middle_candle_body_score = MathMax(dst.middle_candle_body_score, src.middle_candle_body_score);
         dst.volume_impulse_score = MathMax(dst.volume_impulse_score, src.volume_impulse_score);
         dst.gap_width_atr_score = MathMax(dst.gap_width_atr_score, src.gap_width_atr_score);
         dst.manipulation_distance_score = MathMax(dst.manipulation_distance_score, src.manipulation_distance_score);
         dst.premium_discount_score = MathMax(dst.premium_discount_score, src.premium_discount_score);
         dst.htf_nesting_score = MathMax(dst.htf_nesting_score, src.htf_nesting_score);
         dst.freshness_score = MathMax(dst.freshness_score, src.freshness_score);
         dst.retest_quality_score = MathMax(dst.retest_quality_score, src.retest_quality_score);
         dst.opposing_obstruction_score = MathMax(dst.opposing_obstruction_score, src.opposing_obstruction_score);
      }
   }

   void _InsertCandidate(FVGZone &out[], const FVGZone &cand, const int max_candidates, const double point, const double atr) {
      for(int i=0; i<ArraySize(out); i++){
         if(!_ShouldCluster(out[i], cand, point, atr)) continue;
         _MergeCandidate(out[i], cand);
         _SortCandidates(out);
         if(ArraySize(out) > max_candidates) ArrayResize(out, max_candidates);
         return;
      }

      int n = ArraySize(out);
      ArrayResize(out, n + 1);
      out[n] = cand;
      _SortCandidates(out);
      if(ArraySize(out) > max_candidates) ArrayResize(out, max_candidates);
   }

public:
   bool FindCandidates(const string symbol, const ENUM_TIMEFRAMES tf, const PO3Context &po3,
                       const int max_candidates, FVGZone &out[]) {
      FVGDiagnostics diag;
      FVGDiagReset(diag);
      return FindCandidates(symbol, tf, po3, max_candidates, out, diag);
   }

   bool FindCandidates(const string symbol, const ENUM_TIMEFRAMES tf, const PO3Context &po3,
                       const int max_candidates, FVGZone &out[], FVGDiagnostics &diag) {
      ArrayResize(out, 0);
      FVGDiagReset(diag);

      int got = _LoadRatesCached(symbol, tf, InpLookbackLtfBars,
                                 m_cache_ltf_symbol, m_cache_ltf_tf, m_cache_ltf_bar,
                                 m_cache_ltf_requested, m_cache_ltf_rates);
      diag.ltf_bars = got;
      if(got < InpMinLtfBars){
         diag.no_raw_fvg_found = 1;
         return false;
      }

      ENUM_TIMEFRAMES htf = PO3EffectiveHTF();
      int got_htf = _LoadRatesCached(symbol, htf, MathMax(60, InpHTFFvgLookbackBars),
                                     m_cache_htf_symbol, m_cache_htf_tf, m_cache_htf_bar,
                                     m_cache_htf_requested, m_cache_htf_rates);

      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double atr = ATRFromRates(m_cache_ltf_rates, got, 14);
      if(atr <= 0) atr = point * MathMax(10, PO3EffectiveMinFvgWidthTicks());
      double current_px = m_cache_ltf_rates[1].close;

      for(int idx=1; idx<got-2; idx++){
         int older = idx + 2;
         int mid = idx + 1;
         int newer = idx;
         if(older >= got || mid >= got || newer >= got) continue;

         bool bull = (m_cache_ltf_rates[older].high < m_cache_ltf_rates[newer].low);
         bool bear = (m_cache_ltf_rates[older].low  > m_cache_ltf_rates[newer].high);
         if(!bull && !bear) continue;
         diag.raw_fvg_found++;

         if(po3.bias_long && !bull){
            diag.fvg_wrong_direction++;
            continue;
         }
         if(po3.bias_short && !bear){
            diag.fvg_wrong_direction++;
            continue;
         }

         datetime t_form = m_cache_ltf_rates[newer].time;
         if(InpRequireFvgAfterManip && po3.t_sweep > 0 && t_form <= po3.t_sweep){
            diag.fvg_before_manip++;
            diag.fvg_before_sweep++;
            continue;
         }
         if(InpRequireFvgAfterDisp  && po3.t_disp  > 0 && t_form <= po3.t_disp){
            diag.fvg_after_disp_required++;
            continue;
         }
         bool synthetic_impulse_story = (po3.structure_type == "micro_continuation" ||
                                         po3.structure_type == "micro_failed_breakout_reclaim");
         if(synthetic_impulse_story && po3.t_disp > 0 && t_form < po3.t_disp){
            diag.fvg_after_disp_required++;
            continue;
         }

         double lower = bull ? m_cache_ltf_rates[older].high : m_cache_ltf_rates[newer].high;
         double upper = bull ? m_cache_ltf_rates[newer].low  : m_cache_ltf_rates[older].low;
         if(upper <= lower){
            diag.fvg_too_small++;
            continue;
         }

         double width_ticks = (upper - lower) / point;
          if(width_ticks < PO3EffectiveMinFvgWidthTicks()){
            diag.fvg_too_small++;
            continue;
         }

         double width_atr = (upper - lower) / atr;
         double entry_ref = _EntryReference(bull, lower, upper);
         double width_score = _WidthScore(width_atr);
         double prox_score = 16.0 / (1.0 + (MathAbs(current_px - entry_ref) / point) / 35.0);
         double ote_score = _OteZoneScore(po3, entry_ref);
         double session_score = (po3.in_killzone ? 5.0 : (po3.session_name == "OFF_HOURS" ? -5.0 : 1.0));
         double dr_context_score = 0.0;
         if(po3.bias_long && entry_ref <= po3.dr_mid) dr_context_score += 4.0;
         if(po3.bias_short && entry_ref >= po3.dr_mid) dr_context_score += 4.0;

         double origin_score = _OriginScore(po3, bull, lower, upper, t_form);
         double cleanliness_score = _CleanlinessScore(m_cache_ltf_rates[older], m_cache_ltf_rates[mid], m_cache_ltf_rates[newer], bull, atr);
         double age_score = _AgeScore(newer);
         double overlap_score = 0.0, nesting_score = 0.0;
         if(got_htf >= 10) _HtfContextScore(m_cache_htf_rates, got_htf, bull, lower, upper, overlap_score, nesting_score);
         if(InpRequireHtfFvgOverlap && overlap_score <= 0.0){
            diag.htf_overlap_failed++;
            continue;
         }
         double retest_score = _RetestQualityScore(bull, lower, upper, current_px, point);
         double continuation_score = 0.0, reversal_score = 0.0;
         _ContextTypeScores(po3, bull, continuation_score, reversal_score);
         string context_type = _ContextType(po3, bull);
         double displacement_candle_score = MathMin(10.0, MathMax(0.0, po3.displacement_score));
         double middle_candle_body_score = _MiddleBodyQualityScore(m_cache_ltf_rates[mid]);
         double volume_impulse_score = _VolumeImpulseScore(m_cache_ltf_rates, got, mid);
         double gap_width_atr_score = MathMin(10.0, MathMax(0.0, width_score));
         double manipulation_distance_score = MathMin(10.0, MathMax(0.0, origin_score));
         double premium_discount_score = MathMin(10.0, MathMax(0.0, dr_context_score + ote_score * 0.35));
         double htf_nesting_score = MathMin(10.0, MathMax(0.0, nesting_score));
         double freshness_score = MathMin(10.0, MathMax(0.0, age_score));
         double retest_quality_score = MathMin(10.0, MathMax(0.0, retest_score));
         double opposing_obstruction_score = _OpposingObstructionScore(m_cache_htf_rates, got_htf, bull, lower, upper, atr);

         double score = width_score + prox_score + age_score + ote_score + session_score + dr_context_score +
                        origin_score + cleanliness_score + overlap_score + nesting_score + retest_score;
         if(context_type == "continuation") score += continuation_score;
         else score += reversal_score;
         score += MathMax(0.0, opposing_obstruction_score - 5.0) * 0.6;
         score -= MathMax(0.0, 5.0 - opposing_obstruction_score) * 1.2;

         FVGZone cand;
         ZeroMemory(cand);
         cand.score = score;
         cand.bullish = bull;
         cand.t_form = t_form;
         cand.lower = lower;
         cand.upper = upper;
         cand.mid = (lower + upper) * 0.5;
         cand.idx_newer = newer;
         cand.origin_score = origin_score;
         cand.cleanliness_score = cleanliness_score;
         cand.age_score = age_score;
         cand.nesting_score = nesting_score;
         cand.htf_overlap_score = overlap_score;
         cand.retest_score = retest_score;
         cand.displacement_candle_score = displacement_candle_score;
         cand.middle_candle_body_score = middle_candle_body_score;
         cand.volume_impulse_score = volume_impulse_score;
         cand.gap_width_atr_score = gap_width_atr_score;
         cand.manipulation_distance_score = manipulation_distance_score;
         cand.premium_discount_score = premium_discount_score;
         cand.htf_nesting_score = htf_nesting_score;
         cand.freshness_score = freshness_score;
         cand.retest_quality_score = retest_quality_score;
         cand.opposing_obstruction_score = opposing_obstruction_score;
         cand.continuation = (context_type == "continuation");
         cand.reversal = (context_type == "reversal");
         cand.context_type = context_type;
         cand.continuation_score = continuation_score;
         cand.reversal_score = reversal_score;
         _UpdateHistoricalState(m_cache_ltf_rates, got, newer, lower, upper, cand);
         cand.execution_class = _ExecutionClass(cand);
         cand.age_bars = newer;
         cand.score += _ExecutionClassScoreAdjustment(cand);
         int max_age_bars = _ExecutionClassMaxAgeBars(cand.execution_class);
         if(cand.execution_class == "stale_fvg") diag.fvg_stale++;
         if(max_age_bars > 0 && cand.age_bars > max_age_bars){
            diag.fvg_stale++;
            continue;
         }
         if(cand.execution_class == "invalidated_fvg" ||
            cand.execution_class == "fully_mitigated_fvg" ||
            cand.execution_class == "entry_invalid_fvg"){
            if(cand.execution_class == "invalidated_fvg" || cand.structure_invalidated)
               diag.fvg_structure_invalidated++;
            if(cand.execution_class == "fully_mitigated_fvg" || cand.execution_class == "entry_invalid_fvg")
               diag.fvg_fully_mitigated++;
            continue;
         }
         _InsertCandidate(out, cand, MathMax(1, max_candidates), point, atr);
      }

      if(diag.raw_fvg_found <= 0) diag.no_raw_fvg_found = 1;
      diag.accepted = ArraySize(out);
      if(diag.raw_fvg_found > 0 && diag.accepted <= 0) diag.all_candidates_rejected = 1;
      return (ArraySize(out) > 0);
   }

   bool FindBest(const string symbol, const ENUM_TIMEFRAMES tf, const PO3Context &po3, FVGZone &best) {
      FVGZone cands[];
      best.score = -1e9;
      best.mitigated = true;
      if(!FindCandidates(symbol, tf, po3, 1, cands)) return false;
      best = cands[0];
      return true;
   }

   bool MidTouched(const string symbol, const ENUM_TIMEFRAMES tf, const FVGZone &z) {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(point <= 0) point = 0.00001;
      double spread = SymbolInfoDouble(symbol, SYMBOL_ASK) - SymbolInfoDouble(symbol, SYMBOL_BID);
      if(spread < 0) spread = 0.0;

      double width = MathMax(point, z.upper - z.lower);
      double band_low = 0.0, band_high = 0.0;
      _RetraceBand(z, band_low, band_high);
      double tol = width * InpFvgMidToleranceFrac;
      double min_tol = MathMax(point * MathMax(2.0, (double)PO3EffectiveMinFvgWidthTicks() * 0.5), spread * 0.35);
      if(tol < min_tol) tol = min_tol;

      // If live price is already inside the mitigation band of the gap, count it as touched.
      double live_px = SymbolInfoDouble(symbol, z.bullish ? SYMBOL_BID : SYMBOL_ASK);
      if(live_px > 0){
         if(live_px >= (band_low - tol) && live_px <= (band_high + tol)) return true;
      }

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int need = MathMax(2, InpMidTouchLookbackBars + 1);
      int got = CopyRates(symbol, tf, 0, need, rates);
      if(got < 2) return false;

      for(int i=1; i<got; i++){
         if(_OverlapsRange(rates[i].low, rates[i].high, band_low - tol, band_high + tol)) return true;
      }
      return false;
   }

   bool NearestOpposingHtfImbalance(const string symbol, const ENUM_TIMEFRAMES tf, const bool is_buy,
                                    const double entry_price, double &obstacle_price, string &obstacle_kind) {
      obstacle_price = 0.0;
      obstacle_kind = "";
      if(entry_price <= 0.0) return false;

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int got = CopyRates(symbol, tf, 0, MathMax(80, InpHTFFvgLookbackBars), rates);
      if(got < 10) return false;

      double best_dist = DBL_MAX;
      for(int idx=1; idx<got-2; idx++){
         int older = idx + 2;
         int newer = idx;
         if(older >= got || newer >= got) continue;
         bool bull = (rates[older].high < rates[newer].low);
         bool bear = (rates[older].low > rates[newer].high);
         if(!bull && !bear) continue;

         bool opposing = (is_buy ? bear : bull);
         if(!opposing) continue;

         double lower = bull ? rates[older].high : rates[newer].high;
         double upper = bull ? rates[newer].low : rates[older].low;
         if(upper <= lower) continue;

         double level = (lower + upper) * 0.5;
         if(is_buy && level <= entry_price) continue;
         if(!is_buy && level >= entry_price) continue;

         double dist = MathAbs(level - entry_price);
         if(dist < best_dist){
            best_dist = dist;
            obstacle_price = level;
            obstacle_kind = (bull ? "htf_bullish_imbalance" : "htf_bearish_imbalance");
         }
      }
      return (obstacle_price > 0.0);
   }
};

#endif
