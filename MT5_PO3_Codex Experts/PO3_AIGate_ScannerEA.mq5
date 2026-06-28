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

bool _ShouldPauseScanForAI() {
   if(!g_engine.HasPendingAI()) return false;
   if(InpPauseScanWhilePendingAI) return true;
   int cap = MathMax(0, InpMaxPendingAiRequests);
   return (cap > 0 && g_engine.PendingAIRequestCount() >= cap);
}

bool _WaitForPendingAIInTester() {
   if(!MQLInfoInteger(MQL_TESTER)) return false;
   if(!InpAiWaitInTester) return false;
   if(!g_engine.HasPendingAI()) return false;

   int poll_ms = MathMax(50, InpAiWaitPollMs);
   int progress_ms = MathMax(1000, InpAiWaitSliceSeconds * 1000);
   int timeout_ms = MathMax(1000, InpAiWaitTimeoutRealMin * 60 * 1000);
   ulong started_ms = (ulong)GetTickCount();
   ulong next_progress_ms = (ulong)progress_ms;

   _JournalEA("tester wait started pending_candidates=" + IntegerToString(g_engine.PendingAICount())
              + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
              + " poll_ms=" + IntegerToString(poll_ms)
              + " timeout_min=" + IntegerToString(InpAiWaitTimeoutRealMin));

   while(g_engine.HasPendingAI() && !IsStopped()){
      ulong now_ms = (ulong)GetTickCount();
      ulong elapsed_ms = (now_ms >= started_ms ? now_ms - started_ms : 0);
      if(elapsed_ms >= (ulong)timeout_ms) break;
      if(elapsed_ms >= next_progress_ms){
         _JournalEA("tester wait progress pending_candidates=" + IntegerToString(g_engine.PendingAICount())
                    + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
                    + " elapsed_s=" + IntegerToString((int)(elapsed_ms / 1000)));
         next_progress_ms += (ulong)progress_ms;
      }
      int sleep_ms = (int)MathMin((long)poll_ms, (long)((ulong)timeout_ms - elapsed_ms));
      if(sleep_ms > 0) Sleep(sleep_ms);
      g_engine.ProcessPendingAI();
   }
   g_engine.ProcessPendingAI();

   if(g_engine.HasPendingAI()){
      _JournalEA("tester wait timeout pending_candidates=" + IntegerToString(g_engine.PendingAICount())
                 + " requests=" + IntegerToString(g_engine.PendingAIRequestCount())
                 + " timeout_min=" + IntegerToString(InpAiWaitTimeoutRealMin));
   } else {
      _JournalEA("tester wait finished: AI replied, resuming scan");
   }
   return !g_engine.HasPendingAI();
}

int OnInit() {
   Print("[PO3_AIGate] ENGINE_VERSION=", ENGINE_VERSION, " input_schema=", ENGINE_INPUT_SCHEMA);
   Print("[PO3_AIGate] Exclusive model mode: ", (InpOnlyBreakerRetestVirginStrongOrigin ? "ON" : "OFF"));
   Print("[PO3_AIGate] Exclusive model: breaker_retest + virgin_fvg + strong_origin");
   Print("[PO3_AIGate] Strong origin min score: ", DoubleToString(InpStrongOriginMinScore, 2));
   if(!g_engine.Init()) return INIT_FAILED;

   EventSetTimer(InpTimerTickSeconds);

   g_next_scan_time = TimeLocal(); // start immediately
   g_last_persist_time = TimeLocal();
   g_scan_active = false;
   _JournalEA("EA initialized timer=" + IntegerToString(InpTimerTickSeconds)
              + "s scan_interval=" + IntegerToString(InpScanIntervalMinutes)
              + "m scan_all=" + (InpScanAllMarketWatch ? "true" : "false")
              + " effective_htf=" + EnumToString(PO3EffectiveHTF())
              + " effective_entry_tf=" + EnumToString(PO3EffectiveEntryTF())
              + " effective_confirm_tf=" + EnumToString(PO3EffectiveConfirmTF())
              + " preset_tf_override=" + (InpPresetOverridesTimeframes ? "true" : "false")
              + " pause_for_ai=" + (InpPauseScanWhilePendingAI ? "true" : "false")
              + " pending_ai_cap=" + IntegerToString(InpMaxPendingAiRequests));

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {
   EventKillTimer();
   g_engine.Deinit();
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

      processed++;
   }

   if(g_scanner.Done()) _EndScan();
}

// The EA is timer-driven; OnTick can remain empty.
void OnTick() {}
