//+------------------------------------------------------------------+
//| MarketWatchScanner.mqh - iterate Market Watch symbols              |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_MARKETWATCHSCANNER_MQH__
#define __PO3_AIGATE_MARKETWATCHSCANNER_MQH__

class CMarketWatchScanner {
private:
   int m_total;
   int m_idx;
   bool m_all_market_watch;
   string m_single_symbol;
   string m_symbols[];
   int m_market_watch_detected;
   int m_tester_symbols_available;
   int m_excluded;
   string m_excluded_reasons;

   void _ResetDiagnostics(){
      ArrayResize(m_symbols, 0);
      m_market_watch_detected = SymbolsTotal(true);
      m_tester_symbols_available = 0;
      m_excluded = 0;
      m_excluded_reasons = "";
   }

   void _Exclude(const string symbol, const string reason){
      m_excluded++;
      if(StringLen(m_excluded_reasons) > 0) m_excluded_reasons += ";";
      m_excluded_reasons += symbol + ":" + reason;
   }

   bool _AddEligible(const string symbol, const ENUM_TIMEFRAMES history_tf, const bool require_history){
      string normalized = symbol;
      StringTrimLeft(normalized);
      StringTrimRight(normalized);
      if(StringLen(normalized) == 0){
         _Exclude("<blank>", "empty_symbol");
         return false;
      }
      bool custom = false;
      if(!SymbolExist(normalized, custom)){
         _Exclude(normalized, "symbol_not_found");
         return false;
      }
      if(!SymbolSelect(normalized, true)){
         _Exclude(normalized, "symbol_select_failed");
         return false;
      }
      if(require_history && Bars(normalized, history_tf) <= 0){
         _Exclude(normalized, "history_unavailable");
         return false;
      }
      for(int i=0; i<ArraySize(m_symbols); i++){
         if(m_symbols[i] == normalized){
            _Exclude(normalized, "duplicate");
            return false;
         }
      }
      int n = ArraySize(m_symbols);
      ArrayResize(m_symbols, n + 1);
      m_symbols[n] = normalized;
      if(MQLInfoInteger(MQL_TESTER)) m_tester_symbols_available++;
      return true;
   }

public:
   CMarketWatchScanner(){
      m_total=0;
      m_idx=0;
      m_all_market_watch=true;
      m_single_symbol="";
      m_market_watch_detected=0;
      m_tester_symbols_available=0;
      m_excluded=0;
      m_excluded_reasons="";
   }

   void Refresh(const bool all_market_watch=true,
                const string single_symbol="",
                const string tester_symbols="",
                const ENUM_TIMEFRAMES history_tf=PERIOD_M1){
      m_all_market_watch = all_market_watch;
      m_single_symbol = single_symbol;
      _ResetDiagnostics();
      bool tester = (bool)MQLInfoInteger(MQL_TESTER);
      string explicit_symbols = tester_symbols;
      StringTrimLeft(explicit_symbols);
      StringTrimRight(explicit_symbols);
      if(tester && StringLen(explicit_symbols) > 0){
         string requested[];
         int count = StringSplit(explicit_symbols, ',', requested);
         for(int i=0; i<count; i++) _AddEligible(requested[i], history_tf, true);
      } else if(m_all_market_watch){
         int detected = SymbolsTotal(true);
         for(int i=0; i<detected; i++)
            _AddEligible(SymbolName(i, true), history_tf, tester);
      } else {
         _AddEligible(m_single_symbol, history_tf, tester);
      }
      m_total = ArraySize(m_symbols);
      m_idx = 0;
   }

   int Total() const { return m_total; }
   int Index() const { return m_idx; }
   int MarketWatchDetected() const { return m_market_watch_detected; }
   int TesterSymbolsAvailable() const { return m_tester_symbols_available; }
   int ExcludedCount() const { return m_excluded; }
   string ExcludedReasons() const { return m_excluded_reasons; }

   bool Next(string &symbol){
      if(m_total <= 0) return false;
      if(m_idx >= m_total) return false;
      symbol = m_symbols[m_idx];
      m_idx++;
      return (StringLen(symbol) > 0);
   }

   bool Done() const { return (m_total>0 && m_idx>=m_total); }
};

#endif
