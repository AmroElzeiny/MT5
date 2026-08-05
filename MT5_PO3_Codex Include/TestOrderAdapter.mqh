//+------------------------------------------------------------------+
//| TestOrderAdapter.mqh - instrumented broker boundary for harnesses |
//|                                                                  |
//| Purpose                                                          |
//| -------                                                          |
//| Everything between the Python response write and OrderSend was    |
//| untested code: mql_final_allow, watchlist_added, and              |
//| order_attempted had never been observed together in a controlled  |
//| run. This adapter makes that boundary observable and fault-       |
//| injectable WITHOUT replacing the real response parser, identity   |
//| validator, candidate binder, target arbitration, watchlist logic, |
//| confirmation logic, risk sizing, or order construction. All of    |
//| those remain the production implementations; only the final       |
//| broker call is wrapped.                                           |
//|                                                                   |
//| Two modes                                                         |
//| ---------                                                         |
//|  PASSTHROUGH - delegates to the real CTrade and journals the      |
//|                attempt. In the Strategy Tester this is a          |
//|                simulated broker, so orders are real to the        |
//|                tester and produce genuine deal history, which is  |
//|                what _ResolveExactExecutionIdentity needs. No live |
//|                order is possible (see the account guard below).   |
//|  INTERCEPT   - never calls the broker at all; returns a           |
//|                configurable retcode so negative fixtures can      |
//|                force specific rejection paths on demand.          |
//|                                                                   |
//| Safety                                                            |
//| ------                                                            |
//| This header is compiled in only when PO3_TEST_ORDER_ADAPTER is    |
//| defined. A production build never defines it, so production       |
//| binaries are byte-identical to before. When it IS defined, the    |
//| adapter hard-refuses to reach a real broker on a live account:    |
//| passthrough is permitted only under MQL_TESTER or on an account   |
//| whose trade mode is DEMO/CONTEST.                                 |
//+------------------------------------------------------------------+
#ifndef __PO3_TEST_ORDER_ADAPTER_MQH__
#define __PO3_TEST_ORDER_ADAPTER_MQH__

#include <Trade/Trade.mqh>
#include "FileBus.mqh"
#include "JsonLite.mqh"

#define PO3_ADAPTER_MODE_PASSTHROUGH 0
#define PO3_ADAPTER_MODE_INTERCEPT   1

// Harness-only inputs. These exist only in a PO3_TEST_ORDER_ADAPTER build, so
// they cannot appear in, or alter, a production EA's input set.
input int InpHarnessOrderAdapterMode = PO3_ADAPTER_MODE_PASSTHROUGH; // 0=passthrough (tester broker), 1=intercept (no broker call)
input int InpHarnessForcedRetcode    = 0;                            // 0=none, else force this retcode on every order

// Derives from CTrade rather than wrapping it.
//
// Three production call sites hand the engine's trade object to helpers typed
// `CTrade&` -- _FlattenManagedExposureWithReason, _DeleteManagedPendingOrders,
// and CPenaltyWatcher::Tick. Those are exposure-maintenance paths, not the
// AI-gated entry path this harness exists to prove, and they must keep behaving
// exactly as production. Inheritance gives them the real CTrade behaviour for
// free, while the entry-path methods below shadow the base versions and are the
// ones TradeEngine calls through the concrete type.
class CPO3TestTrade : public CTrade {
private:
   CFileBus *m_bus;
   int      m_mode;
   string   m_journal_path;

   // Fault injection for negative fixtures.
   uint     m_forced_retcode;
   bool     m_force_failure;

   // Last-result mirror, so callers see a coherent result in both modes.
   uint     m_retcode;
   string   m_retcode_text;
   ulong    m_order;
   ulong    m_deal;
   double   m_volume;
   double   m_price;

   // Counters the harness asserts on.
   int      m_attempts;
   int      m_market_attempts;
   int      m_pending_attempts;
   int      m_accepted;
   int      m_rejected;

   ulong    m_synthetic_ticket;

   bool LiveAccountBlocked() const {
      if((bool)MQLInfoInteger(MQL_TESTER)) return false;
      long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
      if(mode == ACCOUNT_TRADE_MODE_DEMO || mode == ACCOUNT_TRADE_MODE_CONTEST) return false;
      return true; // real account: never let a test build reach the broker
   }

   void ResetResult(){
      m_retcode = 0; m_retcode_text = ""; m_order = 0; m_deal = 0;
      m_volume = 0.0; m_price = 0.0;
   }

   void MirrorRealResult(){
      m_retcode      = CTrade::ResultRetcode();
      m_retcode_text = CTrade::ResultRetcodeDescription();
      m_order        = CTrade::ResultOrder();
      m_deal         = CTrade::ResultDeal();
      m_volume       = CTrade::ResultVolume();
      m_price        = CTrade::ResultPrice();
   }

   void SynthResult(const uint retcode, const string text, const double vol, const double px){
      m_synthetic_ticket++;
      m_retcode      = retcode;
      m_retcode_text = text;
      m_order        = (retcode == TRADE_RETCODE_DONE ? m_synthetic_ticket : 0);
      m_deal         = (retcode == TRADE_RETCODE_DONE ? m_synthetic_ticket : 0);
      m_volume       = (retcode == TRADE_RETCODE_DONE ? vol : 0.0);
      m_price        = (retcode == TRADE_RETCODE_DONE ? px : 0.0);
   }

   void Journal(const string op, const string symbol, const double vol,
                const double price, const double sl, const double tp,
                const bool ok, const string note){
      if(m_bus == NULL) return;
      string j = "{";
      j += JsonKVStr("event", "order_attempt") + ",";
      j += JsonKVStr("adapter_mode", (m_mode == PO3_ADAPTER_MODE_INTERCEPT ? "INTERCEPT" : "PASSTHROUGH")) + ",";
      j += JsonKVInt("attempt_seq", m_attempts) + ",";
      j += JsonKVStr("op", op) + ",";
      j += JsonKVStr("symbol", symbol) + ",";
      j += JsonKVNum("volume", vol, 4) + ",";
      j += JsonKVNum("price", price, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",";
      j += JsonKVNum("sl", sl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",";
      j += JsonKVNum("tp", tp, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",";
      j += JsonKVBool("method_returned_ok", ok) + ",";
      j += JsonKVInt("retcode", (int)m_retcode) + ",";
      j += JsonKVStr("retcode_text", m_retcode_text) + ",";
      j += JsonKVStr("result_order", IntegerToString((long)m_order)) + ",";
      j += JsonKVStr("result_deal", IntegerToString((long)m_deal)) + ",";
      j += JsonKVBool("tester", (bool)MQLInfoInteger(MQL_TESTER)) + ",";
      j += JsonKVInt("account_trade_mode", (int)AccountInfoInteger(ACCOUNT_TRADE_MODE)) + ",";
      j += JsonKVStr("time", IntegerToString((long)TimeCurrent())) + ",";
      j += JsonKVStr("note", note);
      j += "}";
      m_bus.AppendText(m_journal_path, j + "\r\n");
      _EmitVerdictOnFirstAttempt(op, symbol, vol, ok);
   }

   // The acceptance verdict is emitted the moment the broker boundary is
   // actually reached, not only from OnTester().  A live-wait tester run costs
   // roughly ten wall minutes per simulated hour, so a run that is stopped
   // before its ToDate never reaches OnTester -- and the previous harness run
   // therefore produced no [harness_verdict] line at all despite having
   // genuinely executed 1,149 execution attempts.  An interrupted run must
   // still be able to prove what it reached.
   void _EmitVerdictOnFirstAttempt(const string op, const string symbol,
                                   const double vol, const bool ok){
      if(m_attempts != 1) return;
      PrintFormat("[harness_funnel] phase=first_order_attempt symbol=%s op=%s volume=%.4f"
                  " order_attempted=true order_attempt_count=%d method_returned_ok=%s"
                  " adapter_mode=%s retcode=%d tester=%s",
                  symbol, op, vol, m_attempts, (ok ? "true" : "false"),
                  (m_mode == PO3_ADAPTER_MODE_INTERCEPT ? "INTERCEPT" : "PASSTHROUGH"),
                  (int)m_retcode,
                  (MQLInfoInteger(MQL_TESTER) ? "true" : "false"));
      PrintFormat("[harness_verdict] phase=first_order_attempt positive_path_reached=true"
                  " requires=watchlist_added>0_and_order_attempted>0"
                  " evidence=production_TradeEngine_invoked_the_broker_boundary");
   }

public:
   CPO3TestTrade(){
      m_bus = NULL;
      m_mode = PO3_ADAPTER_MODE_PASSTHROUGH;
      m_journal_path = "";
      m_forced_retcode = TRADE_RETCODE_DONE;
      m_force_failure = false;
      m_attempts = 0; m_market_attempts = 0; m_pending_attempts = 0;
      m_accepted = 0; m_rejected = 0;
      m_synthetic_ticket = 900000000;
      ResetResult();
   }

   void BindHarness(CFileBus &bus, const int mode){
      m_bus = &bus;
      m_mode = mode;
      m_journal_path = bus.LogDir() + "\\harness_order_attempts.jsonl";
      if(LiveAccountBlocked() && mode == PO3_ADAPTER_MODE_PASSTHROUGH){
         m_mode = PO3_ADAPTER_MODE_INTERCEPT;
         PrintFormat("[test_order_adapter] live account detected: forcing INTERCEPT, no broker call will be made");
      }
      if(InpHarnessForcedRetcode != 0) ForceRetcode((uint)InpHarnessForcedRetcode);
      PrintFormat("[test_order_adapter] bound mode=%s forced_retcode=%d journal=%s",
                  (m_mode == PO3_ADAPTER_MODE_INTERCEPT ? "INTERCEPT" : "PASSTHROUGH"),
                  InpHarnessForcedRetcode,
                  m_journal_path);
   }

   // -- fault injection ------------------------------------------------
   void ForceRetcode(const uint retcode){ m_forced_retcode = retcode; m_force_failure = (retcode != TRADE_RETCODE_DONE); }
   void ClearForcedRetcode(){ m_forced_retcode = TRADE_RETCODE_DONE; m_force_failure = false; }
   void SetMode(const int mode){ m_mode = mode; }

   // -- counters -------------------------------------------------------
   int  Attempts()        const { return m_attempts; }
   int  MarketAttempts()  const { return m_market_attempts; }
   int  PendingAttempts() const { return m_pending_attempts; }
   int  Accepted()        const { return m_accepted; }
   int  Rejected()        const { return m_rejected; }
   bool OrderAttempted()  const { return m_attempts > 0; }
   int  Mode()            const { return m_mode; }

   string CountersJson() const {
      string j = "{";
      j += JsonKVInt("order_attempts", m_attempts) + ",";
      j += JsonKVInt("market_attempts", m_market_attempts) + ",";
      j += JsonKVInt("pending_attempts", m_pending_attempts) + ",";
      j += JsonKVInt("accepted", m_accepted) + ",";
      j += JsonKVInt("rejected", m_rejected) + ",";
      j += JsonKVBool("order_attempted", m_attempts > 0);
      j += "}";
      return j;
   }

   // -- CTrade surface --------------------------------------------------

   uint   ResultRetcode()            const { return m_retcode; }
   string ResultRetcodeDescription() const { return m_retcode_text; }
   ulong  ResultOrder()              const { return m_order; }
   ulong  ResultDeal()               const { return m_deal; }
   double ResultVolume()             const { return m_volume; }
   double ResultPrice()              const { return m_price; }

   bool Buy(const double volume, const string symbol, const double price,
            const double sl, const double tp, const string comment=""){
      return _Market(true, volume, symbol, price, sl, tp, comment);
   }

   bool Sell(const double volume, const string symbol, const double price,
             const double sl, const double tp, const string comment=""){
      return _Market(false, volume, symbol, price, sl, tp, comment);
   }

   bool BuyLimit(const double volume, const double price, const string symbol,
                 const double sl, const double tp,
                 const ENUM_ORDER_TYPE_TIME type_time, const datetime expiration,
                 const string comment=""){
      return _Pending(true, volume, price, symbol, sl, tp, type_time, expiration, comment, "BUY_LIMIT");
   }

   bool SellLimit(const double volume, const double price, const string symbol,
                  const double sl, const double tp,
                  const ENUM_ORDER_TYPE_TIME type_time, const datetime expiration,
                  const string comment=""){
      return _Pending(false, volume, price, symbol, sl, tp, type_time, expiration, comment, "SELL_LIMIT");
   }

   // Every remaining CTrade method that can CREATE exposure is shadowed too,
   // even though TradeEngine does not call them today. Inheritance means an
   // unshadowed base method would silently reach the broker and never appear in
   // the journal, so coverage here is by exposure semantics, not by current
   // call sites. tests/test_order_adapter_contract.py fails if CTrade gains a
   // new exposure-creating method that is not listed here.
   bool BuyStop(const double volume, const double price, const string symbol,
                const double sl, const double tp,
                const ENUM_ORDER_TYPE_TIME type_time, const datetime expiration,
                const string comment=""){
      return _Pending(true, volume, price, symbol, sl, tp, type_time, expiration, comment, "BUY_STOP");
   }

   bool SellStop(const double volume, const double price, const string symbol,
                 const double sl, const double tp,
                 const ENUM_ORDER_TYPE_TIME type_time, const datetime expiration,
                 const string comment=""){
      return _Pending(false, volume, price, symbol, sl, tp, type_time, expiration, comment, "SELL_STOP");
   }

   bool PositionOpen(const string symbol, const ENUM_ORDER_TYPE order_type,
                     const double volume, const double price,
                     const double sl, const double tp, const string comment=""){
      bool is_buy = (order_type == ORDER_TYPE_BUY);
      return _Market(is_buy, volume, symbol, price, sl, tp, comment);
   }

   bool OrderOpen(const string symbol, const ENUM_ORDER_TYPE order_type,
                  const double volume, const double limit_price, const double price,
                  const double sl, const double tp,
                  ENUM_ORDER_TYPE_TIME type_time=ORDER_TIME_GTC,
                  const datetime expiration=0, const string comment=""){
      bool is_buy = (order_type == ORDER_TYPE_BUY_LIMIT || order_type == ORDER_TYPE_BUY_STOP
                     || order_type == ORDER_TYPE_BUY || order_type == ORDER_TYPE_BUY_STOP_LIMIT);
      return _Pending(is_buy, volume, price, symbol, sl, tp, type_time, expiration, comment, "ORDER_OPEN");
   }

   bool OrderDelete(const ulong ticket){
      if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_DONE, "intercepted_order_delete", 0.0, 0.0);
         return true;
      }
      bool ok = CTrade::OrderDelete(ticket);
      MirrorRealResult();
      return ok;
   }

   bool OrderModify(const ulong ticket, const double price, const double sl,
                    const double tp, const ENUM_ORDER_TYPE_TIME type_time,
                    const datetime expiration, const double stoplimit=0.0){
      if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_DONE, "intercepted_order_modify", 0.0, price);
         return true;
      }
      bool ok = CTrade::OrderModify(ticket, price, sl, tp, type_time, expiration, stoplimit);
      MirrorRealResult();
      return ok;
   }

   bool PositionClose(const ulong ticket, const ulong deviation=ULONG_MAX){
      if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_DONE, "intercepted_position_close", 0.0, 0.0);
         return true;
      }
      bool ok = CTrade::PositionClose(ticket, deviation);
      MirrorRealResult();
      return ok;
   }

   bool PositionClosePartial(const ulong ticket, const double volume,
                             const ulong deviation=ULONG_MAX){
      if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_DONE, "intercepted_position_close_partial", volume, 0.0);
         return true;
      }
      bool ok = CTrade::PositionClosePartial(ticket, volume, deviation);
      MirrorRealResult();
      return ok;
   }

   bool PositionModify(const ulong ticket, const double sl, const double tp){
      if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_DONE, "intercepted_position_modify", 0.0, 0.0);
         return true;
      }
      bool ok = CTrade::PositionModify(ticket, sl, tp);
      MirrorRealResult();
      return ok;
   }

private:
   bool _Market(const bool is_buy, const double volume, const string symbol,
                const double price, const double sl, const double tp,
                const string comment){
      ResetResult();
      m_attempts++;
      m_market_attempts++;

      bool ok = false;
      string note = "";

      if(m_force_failure){
         SynthResult(m_forced_retcode, "forced_retcode_fault_injection", volume, price);
         ok = false;
         note = "fault_injection";
      } else if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_DONE, "intercepted_market_order", volume,
                     (is_buy ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                             : SymbolInfoDouble(symbol, SYMBOL_BID)));
         ok = true;
         note = "no_broker_call";
      } else {
         ok = (is_buy ? CTrade::Buy(volume, symbol, price, sl, tp, comment)
                      : CTrade::Sell(volume, symbol, price, sl, tp, comment));
         MirrorRealResult();
         note = "delegated_to_ctrade";
      }

      if(ok && (m_retcode == TRADE_RETCODE_DONE || m_retcode == TRADE_RETCODE_DONE_PARTIAL
                || m_retcode == TRADE_RETCODE_PLACED)) m_accepted++;
      else m_rejected++;

      Journal(is_buy ? "MARKET_BUY" : "MARKET_SELL", symbol, volume, price, sl, tp, ok, note);
      return ok;
   }

   bool _Pending(const bool is_buy, const double volume, const double price,
                 const string symbol, const double sl, const double tp,
                 const ENUM_ORDER_TYPE_TIME type_time, const datetime expiration,
                 const string comment, const string op_label=""){
      ResetResult();
      m_attempts++;
      m_pending_attempts++;

      bool ok = false;
      string note = "";

      if(m_force_failure){
         SynthResult(m_forced_retcode, "forced_retcode_fault_injection", volume, price);
         ok = false;
         note = "fault_injection";
      } else if(m_mode == PO3_ADAPTER_MODE_INTERCEPT){
         SynthResult(TRADE_RETCODE_PLACED, "intercepted_pending_order", volume, price);
         ok = true;
         note = "no_broker_call";
      } else {
         ok = (is_buy ? CTrade::BuyLimit(volume, price, symbol, sl, tp, type_time, expiration, comment)
                      : CTrade::SellLimit(volume, price, symbol, sl, tp, type_time, expiration, comment));
         MirrorRealResult();
         note = "delegated_to_ctrade";
      }

      if(ok && (m_retcode == TRADE_RETCODE_DONE || m_retcode == TRADE_RETCODE_PLACED)) m_accepted++;
      else m_rejected++;

      Journal(StringLen(op_label) > 0 ? op_label : (is_buy ? "BUY_LIMIT" : "SELL_LIMIT"), symbol, volume, price, sl, tp, ok, note);
      return ok;
   }
};

#endif



