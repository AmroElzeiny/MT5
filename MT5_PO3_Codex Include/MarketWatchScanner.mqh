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

public:
   CMarketWatchScanner(){
      m_total=0;
      m_idx=0;
      m_all_market_watch=true;
      m_single_symbol="";
   }

   void Refresh(const bool all_market_watch=true, const string single_symbol=""){
      m_all_market_watch = all_market_watch;
      m_single_symbol = single_symbol;
      if(m_all_market_watch){
         m_total = SymbolsTotal(true); // Market Watch only
      } else {
         m_total = (StringLen(m_single_symbol) > 0 ? 1 : 0);
      }
      m_idx = 0;
   }

   int Total() const { return m_total; }
   int Index() const { return m_idx; }

   bool Next(string &symbol){
      if(m_total <= 0) Refresh(m_all_market_watch, m_single_symbol);
      if(m_idx >= m_total) return false;
      if(m_all_market_watch) symbol = SymbolName(m_idx, true);
      else                   symbol = m_single_symbol;
      m_idx++;
      return (StringLen(symbol) > 0);
   }

   bool Done() const { return (m_total>0 && m_idx>=m_total); }
};

#endif
