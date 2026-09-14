//+------------------------------------------------------------------+
//| PO3_RepoIncludeCompileCheck.mq5                                  |
//| Compile-only harness: builds the REPOSITORY include tree (not the |
//| deployed terminal copy) so repo changes can be compile-verified   |
//| before deployment.  Never attach it to a chart: OnInit refuses.   |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>
#include "../MT5_PO3_Codex Include/Config.mqh"
#include "../MT5_PO3_Codex Include/MarketWatchScanner.mqh"
#include "../MT5_PO3_Codex Include/TradeEngine.mqh"

CTradeEngine g_compile_check_engine;

int OnInit()
{
   Print("[PO3_RepoIncludeCompileCheck] compile-only harness; refusing to run");
   return INIT_FAILED;
}

void OnDeinit(const int reason)
{
}

void OnTick()
{
}
