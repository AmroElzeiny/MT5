//+------------------------------------------------------------------+
//| PO3_AIGate_ScannerEA.mq5                                          |
//| Single-chart EA scanning all Market Watch symbols every X minutes |
//| and maintaining watchlist + penalties continuously.               |
//+------------------------------------------------------------------+
#property strict
#property version   "5.00"

#include <Trade/Trade.mqh>
#include "../../Include/MT5_PO3_Codex/Config.mqh"
#include "../../Include/MT5_PO3_Codex/MarketWatchScanner.mqh"
#include "../../Include/MT5_PO3_Codex/TradeEngine.mqh"

CTradeEngine         g_engine;
CMarketWatchScanner  g_scanner;

datetime g_next_scan_time = 0;
bool     g_scan_active = false;
datetime g_last_ai_pause_log = 0;
datetime g_last_busy_pause_log = 0;
datetime g_last_persist_time = 0;

bool _ShouldJournalEA() {
   if(!InpVerboseJournal) return false;
   if(InpJournalTesterOnly && !MQLInfoInteger(MQL_TESTER)) return false;
   return true;
}

void _JournalEA(const string msg) {
   if(!_ShouldJournalEA()) return;
   Print("[PO3_AIGate] ", msg);
}

bool _TesterLiveAiWaitMode() {
   return (MQLInfoInteger(MQL_TESTER) && InpUseAI && InpAiWaitInTester && InpTesterAiMode == TESTER_AI_LIVE_WAIT_DEBUG);
}

string _TesterAiModeName(const TesterAiMode mode) {
   if(mode == TESTER_AI_LIVE_WAIT_DEBUG) return "TESTER_AI_LIVE_WAIT_DEBUG";
   if(mode == TESTER_AI_CACHE_ONLY) return "TESTER_AI_CACHE_ONLY";
   if(mode == TESTER_AI_RECORD_ONLY) return "TESTER_AI_RECORD_ONLY";
   return "TESTER_AI_UNKNOWN";
}

string _TesterAiModeLabel(const TesterAiMode mode) {
   if(mode == TESTER_AI_CACHE_ONLY) return "cache_only";
   if(mode == TESTER_AI_RECORD_ONLY) return "record_only";
   if(mode == TESTER_AI_LIVE_WAIT_DEBUG) return "live_wait_debug";
   return "unknown";
}

string _TesterAiModeLabel() {
   return _TesterAiModeLabel(InpTesterAiMode);
}

bool _EffectivePauseScanWhilePendingAI() {
   if(_TesterLiveAiWaitMode()) return true;
   return InpPauseScanWhilePendingAI;
}

bool _ShouldPauseScanForAI() {
   if(!g_engine.HasPendingAI()) return false;
   if(_EffectivePauseScanWhilePendingAI()) return true;
   int cap = MathMax(0, InpMaxPendingAiRequests);
   return (cap > 0 && g_engine.PendingAIRequestCount() >= cap);
}

bool _WaitForPendingAIInTester() {
   if(!MQLInfoInteger(MQL_TESTER)) return false;
   if(!InpAiWaitInTester) return false;
   if(!g_engine.HasPendingAI()) return false;

   string req_ids = g_engine.PendingAIRequestIds();
   bool snapshot_saved = g_engine.PendingAISnapshotSaved();
   int poll_ms = MathMax(50, InpAiWaitPollMs);
   int progress_ms = MathMax(1000, InpAiWaitSliceSeconds * 1000);
   int timeout_ms = MathMax(1000, InpAiWaitTimeoutRealMin * 60 * 1000);
   ulong started_ms = (ulong)GetTickCount();
   ulong next_progress_ms = (ulong)progress_ms;
   int timeout_total_before = g_engine.TesterAiWaitTimeoutTotal();

   g_engine.NoteTesterAiWaitStarted();
   _JournalEA("[tester_ai_wait] started req_id=" + req_ids
              + " pause_scan=true"
              + " snapshot_saved=" + (snapshot_saved ? "true" : "false")
              + " pending_candidates=" + IntegerToString(g_engine.PendingAICount())
              + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
              + " poll_ms=" + IntegerToString(poll_ms)
              + " timeout_min=" + IntegerToString(InpAiWaitTimeoutRealMin));

   while(g_engine.HasPendingAI() && !IsStopped()){
      ulong now_ms = (ulong)GetTickCount();
      ulong elapsed_ms = (now_ms >= started_ms ? now_ms - started_ms : 0);
      if(elapsed_ms >= (ulong)timeout_ms) break;
      if(elapsed_ms >= next_progress_ms){
         _JournalEA("[tester_ai_wait] poll req_id=" + req_ids
                    + " elapsed_wall_sec=" + IntegerToString((int)(elapsed_ms / 1000))
                    + " pending_candidates=" + IntegerToString(g_engine.PendingAICount())
                    + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
                    + " pause_scan=true");
         next_progress_ms += (ulong)progress_ms;
      }
      int sleep_ms = (int)MathMin((long)poll_ms, (long)((ulong)timeout_ms - elapsed_ms));
      if(sleep_ms > 0) Sleep(sleep_ms);
      g_engine.ProcessPendingAI();
   }
   g_engine.ProcessPendingAI();

   ulong finished_ms = (ulong)GetTickCount();
   ulong elapsed_final_ms = (finished_ms >= started_ms ? finished_ms - started_ms : 0);
   bool engine_timed_out = (g_engine.TesterAiWaitTimeoutTotal() > timeout_total_before);
   if(g_engine.HasPendingAI()){
      g_engine.NoteTesterAiWaitTimeout();
      _JournalEA("[tester_ai_wait] timeout req_id=" + req_ids
                 + " elapsed_wall_sec=" + IntegerToString((int)(elapsed_final_ms / 1000))
                 + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
                 + " timeout_min=" + IntegerToString(InpAiWaitTimeoutRealMin)
                 + " action=fail_closed");
   } else if(engine_timed_out){
      _JournalEA("[tester_ai_wait] timeout req_id=" + req_ids
                 + " elapsed_wall_sec=" + IntegerToString((int)(elapsed_final_ms / 1000))
                 + " requests=0"
                 + " timeout_min=" + IntegerToString(InpAiWaitTimeoutRealMin)
                 + " action=fail_closed");
   } else {
      g_engine.NoteTesterAiWaitCompleted();
      _JournalEA("[tester_ai_wait] completed req_id=" + req_ids
                 + " elapsed_wall_sec=" + IntegerToString((int)(elapsed_final_ms / 1000))
                 + " applying_response_before_resuming_scan=true");
      _JournalEA("[tester_ai_wait] scan_resumed req_id=" + req_ids);
   }
   return !g_engine.HasPendingAI();
}

int OnInit() {
   Print("[PO3_AIGate] ENGINE_VERSION=", ENGINE_VERSION, " input_schema=", ENGINE_INPUT_SCHEMA);
   Print("[PO3_AIGate] Exclusive model mode: ", (InpOnlyBreakerRetestVirginStrongOrigin ? "ON" : "OFF"));
   Print("[PO3_AIGate] Exclusive model: breaker_retest + virgin_fvg + strong_origin");
   Print("[PO3_AIGate] Strong origin min score: ", DoubleToString(InpStrongOriginMinScore, 2));
   if(MQLInfoInteger(MQL_TESTER)){
      Print("[PO3_AIGate] [tester_ai_mode] raw_value=", IntegerToString((int)InpTesterAiMode),
            " raw_name=", _TesterAiModeName(InpTesterAiMode),
            " effective_name=", _TesterAiModeName(InpTesterAiMode),
            " allow_live_wait_debug_trading=", (InpTesterAllowLiveWaitDebugTrading ? "true" : "false"));
   }
   if(!g_engine.Init()) return INIT_FAILED;

   EventSetTimer(InpTimerTickSeconds);

   g_next_scan_time = TimeLocal(); // start immediately
   g_last_persist_time = TimeLocal();
   g_scan_active = false;
   bool effective_pause = _EffectivePauseScanWhilePendingAI();
   if(_TesterLiveAiWaitMode() && !InpPauseScanWhilePendingAI){
      _JournalEA("[tester_ai_wait] forcing_pause_scan_while_pending_ai=true"
                 + " reason=tester_live_ai_sync raw_input_pause=false effective_pause=true");
   }
   if(MQLInfoInteger(MQL_TESTER)){
      string tester_mode = _TesterAiModeLabel();
      _JournalEA("[tester_ai_mode] raw_value=" + IntegerToString((int)InpTesterAiMode)
                 + " raw_name=" + _TesterAiModeName(InpTesterAiMode)
                 + " effective_name=" + _TesterAiModeName(InpTesterAiMode)
                 + " allow_live_wait_debug_trading=" + (InpTesterAllowLiveWaitDebugTrading ? "true" : "false"));
      _JournalEA("[tester_ai_workflow] clean_backtest_requires_cache_replay=true");
      _JournalEA("[tester_ai_workflow] Step 1: run RECORD_ONLY to export requests.");
      _JournalEA("[tester_ai_workflow] Step 2: run python ai_gate.py / batch processor to fill cache.");
      _JournalEA("[tester_ai_workflow] Step 3: rerun with CACHE_ONLY and InpTesterAiCache=true.");
      if(InpTesterAiMode == TESTER_AI_CACHE_ONLY){
         _JournalEA("[tester_ai_mode] mode=cache_only"
                    + " cache_enabled=" + (InpTesterAiCache ? "true" : "false")
                    + " live_ai_calls=false backtest_safe=true");
         if(!InpTesterAiCache)
            _JournalEA("[runtime_input_mismatch] field=InpTesterAiCache expected=true actual=false reason=cache_only_requires_cache");
      } else if(InpTesterAiMode == TESTER_AI_RECORD_ONLY){
         _JournalEA("[tester_ai_mode] mode=record_only cache_lookup=false cache_export=true live_ai_calls=false trading=false backtest_safe=true");
      } else {
         _JournalEA("[tester_ai_mode] mode=live_wait_debug warning=tester_live_ai_wait_not_backtest_safe"
                    + " allow_trading=" + (InpTesterAllowLiveWaitDebugTrading ? "true" : "false")
                    + " default_action=" + (InpTesterAllowLiveWaitDebugTrading ? "trade_only_if_sim_age_safe" : "record_response_do_not_trade"));
      }
      _JournalEA("[runtime_inputs] InpTesterAiCache=" + (InpTesterAiCache ? "true" : "false"));
      _JournalEA("[runtime_inputs] InpTesterAiMode=" + tester_mode);
      _JournalEA("[runtime_inputs] InpTesterAllowLiveWaitDebugTrading=" + (InpTesterAllowLiveWaitDebugTrading ? "true" : "false"));
      _JournalEA("[runtime_inputs] InpFallbackRR2=" + DoubleToString(InpFallbackRR2, 4));
      _JournalEA("[runtime_inputs] InpMaxTargetAtrMult=" + DoubleToString(InpMaxTargetAtrMult, 4));
      _JournalEA("[runtime_inputs] InpMaxTargetAdrFrac=" + DoubleToString(InpMaxTargetAdrFrac, 4));
      _JournalEA("[runtime_inputs] runtime_input_hash=" + g_engine.RuntimeInputHash());
   }
   _JournalEA("[ai_mode] tester=" + (MQLInfoInteger(MQL_TESTER) ? "true" : "false")
              + " live_ai_wait=" + (_TesterLiveAiWaitMode() ? "true" : "false")
              + " effective_pause_scan_while_pending_ai=" + (effective_pause ? "true" : "false"));
   _JournalEA("EA initialized timer=" + IntegerToString(InpTimerTickSeconds)
              + "s scan_interval=" + IntegerToString(InpScanIntervalMinutes)
              + "m scan_all=" + (InpScanAllMarketWatch ? "true" : "false")
              + " effective_htf=" + EnumToString(PO3EffectiveHTF())
              + " effective_entry_tf=" + EnumToString(PO3EffectiveEntryTF())
              + " effective_confirm_tf=" + EnumToString(PO3EffectiveConfirmTF())
              + " preset_tf_override=" + (InpPresetOverridesTimeframes ? "true" : "false")
              + " pause_for_ai=" + (effective_pause ? "true" : "false")
              + " raw_pause_for_ai=" + (InpPauseScanWhilePendingAI ? "true" : "false")
              + " pending_ai_cap=" + IntegerToString(InpMaxPendingAiRequests));

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {
   EventKillTimer();
   g_engine.Deinit();
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result) {
   g_engine.HandleTradeTransaction(trans);
}

void _StartScan() {
   g_scanner.Refresh(InpScanAllMarketWatch, _Symbol);
   g_engine.BeginScan();
   g_scan_active = true;
   _JournalEA("scan started symbols=" + IntegerToString(g_scanner.Total())
              + " mode=" + (InpScanAllMarketWatch ? "market_watch" : _Symbol));
}

void _EndScan() {
   g_scan_active = false;
   g_next_scan_time = TimeLocal() + InpScanIntervalMinutes * 60;
   g_engine.FinalizeScan();
   if(_TesterLiveAiWaitMode() && g_engine.HasPendingAI()){
      bool resolved = _WaitForPendingAIInTester();
      if(resolved){
         g_engine.MaintainWatchlist();
         g_engine.MaintainOrders();
         g_engine.MaintainPositions();
      }
   }
   bool persist_now = !MQLInfoInteger(MQL_TESTER) || InpTesterPersistIntervalMin <= 0 ||
                      g_last_persist_time <= 0 ||
                      (TimeLocal() - g_last_persist_time) >= InpTesterPersistIntervalMin * 60;
   if(persist_now){
      g_engine.Persist();
      g_last_persist_time = TimeLocal();
   }
   _JournalEA("scan finished next_scan=" + TimeToString(g_next_scan_time, TIME_MINUTES|TIME_SECONDS));
}

void OnTimer() {
   // Global stop / exposure maintenance
   CTrade tmp;
   if(EnforceGlobalStops(tmp)) return;
   g_engine.MaintainRolloverProtection();

   // AI responses / watchlist / positions / penalties
   g_engine.ProcessPendingAI();
   g_engine.MaintainWatchlist();
   g_engine.MaintainOrders();
   g_engine.MaintainPositions();

   bool ai_resolved_during_wait = _WaitForPendingAIInTester();
   if(ai_resolved_during_wait){
      g_engine.MaintainWatchlist();
      g_engine.MaintainOrders();
      g_engine.MaintainPositions();
   }

   // Scan scheduler
   datetime now = TimeLocal();
   string rollover_reason = "";
   if(g_engine.EntryFreezeActive(rollover_reason)){
      if(g_scan_active){
         g_engine.AbortScan(rollover_reason);
         g_scan_active = false;
      }
      g_next_scan_time = now + MathMax(1, InpScanIntervalMinutes) * 60;
      if(g_last_busy_pause_log <= 0 || (now - g_last_busy_pause_log) >= 60){
         _JournalEA("scan paused by server-time rollover guard reason=" + rollover_reason);
         g_last_busy_pause_log = now;
      }
      return;
   }
   g_last_busy_pause_log = 0;

   if(_ShouldPauseScanForAI()){
      if(g_last_ai_pause_log <= 0 || (now - g_last_ai_pause_log) >= 60){
         _JournalEA("scan paused by AI backpressure pending_candidates=" + IntegerToString(g_engine.PendingAICount())
                    + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
                    + " cap=" + IntegerToString(InpMaxPendingAiRequests));
         g_last_ai_pause_log = now;
      }
      return;
   }
   g_last_ai_pause_log = 0;
   // Scan continues even with active symbol state (trades or pending orders exist)
   // System continuously evaluates all symbols for new opportunities

   if(!g_scan_active && now >= g_next_scan_time) _StartScan();

   if(!g_scan_active) return;

   int processed = 0;
   while(processed < InpMaxSymbolsPerTick){
      string sym;
      if(!g_scanner.Next(sym)) break;

      // Evaluate symbol and let the engine rank candidates across the full scan.
      g_engine.EvaluateSymbol(sym);
      if(_TesterLiveAiWaitMode() && g_engine.HasPendingAI()){
         bool resolved = _WaitForPendingAIInTester();
         if(resolved){
            g_engine.MaintainWatchlist();
            g_engine.MaintainOrders();
            g_engine.MaintainPositions();
         } else {
            return;
         }
      }

      processed++;
   }

   if(g_scanner.Done()) _EndScan();
}

// Keep tick work intentionally narrow: only executable-side path evidence for
// managed positions on this chart symbol. Scanning, AI, history and management
// execution remain timer-driven.
void OnTick() {
   g_engine.ObserveChartTick(_Symbol);
}
