//+------------------------------------------------------------------+
//| PO3_AIGate_PositivePath_Harness.mq5                              |
//| Tester-only wrapper around the production EA.                    |
//+------------------------------------------------------------------+
#define PO3_TEST_ORDER_ADAPTER
#include "PO3_AIGate_ScannerEA.mq5"

double OnTester() {
   int watchlist_added = g_engine.HarnessWatchlistAdded();
   int order_attempts = g_engine.HarnessOrderAttempts();
   int orders_accepted = g_engine.HarnessOrdersAccepted();
   int orders_rejected = g_engine.HarnessOrdersRejected();
   string order_counters = g_engine.HarnessOrderCountersJson();
   bool positive_path_reached = (watchlist_added > 0 && order_attempts > 0);

   Print("[positive_path_harness] watchlist_added=", IntegerToString(watchlist_added),
         " order_attempted=", (order_attempts > 0 ? "true" : "false"),
         " order_attempts=", IntegerToString(order_attempts),
         " orders_accepted=", IntegerToString(orders_accepted),
         " orders_rejected=", IntegerToString(orders_rejected),
         " order_counters=", order_counters,
         " positive_path_reached=", (positive_path_reached ? "true" : "false"));
   return (positive_path_reached ? 1.0 : 0.0);
}
