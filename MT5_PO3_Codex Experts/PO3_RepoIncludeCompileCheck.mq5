//+------------------------------------------------------------------+
//| PO3_RepoIncludeCompileCheck.mq5                                  |
//| Compile-only harness: builds the REPOSITORY include tree (not the |
//| deployed terminal copy) so repo changes can be compile-verified   |
//| before deployment.  Never attach it to a chart: OnInit refuses.   |
//| REPO-ONLY: compiles ONLY from the repository layout via the       |
//| quote-relative includes into "MT5_PO3_Codex Include/" below.      |
//| MUST be excluded from the repo->deployed mirror (deployed path    |
//| "MQL5\Experts\MT5_PO3_Codex\"): the relative path does not exist  |
//| there and breaks the final compile step (error 106 class).        |
//| Compile it in place in the repo with the FxPro MetaEditor.        |
//| Do NOT convert to angle-bracket includes (<MT5_PO3_Codex/...>):   |
//| that would silently compile the deployed copies under             |
//| "MQL5\Include\MT5_PO3_Codex\" and destroy the repo check.         |
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
