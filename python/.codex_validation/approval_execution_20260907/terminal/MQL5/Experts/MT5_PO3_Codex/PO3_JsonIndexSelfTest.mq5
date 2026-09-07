//+------------------------------------------------------------------+
//| PO3_JsonIndexSelfTest.mq5                                        |
//|                                                                  |
//| Equivalence proof for the JsonLite key index.                    |
//|                                                                  |
//| JsonLite used to locate a key by rescanning the whole document    |
//| from position 0, materializing a temporary string for every       |
//| quoted token it walked past.  Reading one ~76,000 character trade |
//| meta cost ~800 of those scans and was measured at 505 ms, which   |
//| is what collapsed Strategy Tester throughput the moment a         |
//| position opened.  The index replaces the rescan.                  |
//|                                                                  |
//| A faster parser that returns a different answer is not a fix, so  |
//| this file carries VERBATIM COPIES of the pre-index implementations|
//| (_RefJsonReadString / _RefJsonLocateKeyValue) and asserts that the|
//| shipped functions agree with them on every key of every document  |
//| below -- including the malformed, duplicated and escaped cases    |
//| where "equivalent" is not obvious.                                |
//|                                                                  |
//| Run it in the Strategy Tester on any symbol and any short period; |
//| it reports in OnInit and stops the run immediately.  It never     |
//| trades, never touches the bus and never reads market data.        |
//+------------------------------------------------------------------+
#property copyright "PO3_Codex"
#property version   "1.00"
#property strict

#include <MT5_PO3_Codex\JsonLite.mqh>

//--- Pre-index reference implementations -----------------------------------
// Copied unchanged from JsonLite.mqh before the index was introduced.  These are
// the definition of "correct" for this test: whatever they answer, the shipped
// functions must answer.

bool _RefJsonReadString(const string json, const int quote_pos, string &out, int &end_pos) {
   int len = (int)StringLen(json);
   if(quote_pos < 0 || quote_pos >= len) return false;
   if((ushort)StringGetCharacter(json, quote_pos) != '\"') return false;

   out = "";
   bool esc = false;
   for(int i=quote_pos+1; i<len; i++){
      ushort c = (ushort)StringGetCharacter(json, i);
      if(esc){
         if(c == '\"' || c == '\\' || c == '/'){
            out += StringSubstr(json, i, 1);
         } else if(c == 'n'){
            out += "\n";
         } else if(c == 'r'){
            out += "\r";
         } else if(c == 't'){
            out += "\t";
         } else {
            out += StringSubstr(json, i, 1);
         }
         esc = false;
         continue;
      }
      if(c == '\\'){
         esc = true;
         continue;
      }
      if(c == '\"'){
         end_pos = i;
         return true;
      }
      out += StringSubstr(json, i, 1);
   }
   return false;
}

bool _RefJsonLocateKeyValue(const string json, const string key, int &value_pos) {
   int len = (int)StringLen(json);
   value_pos = -1;
   for(int i=0; i<len; i++){
      if((ushort)StringGetCharacter(json, i) != '\"') continue;
      string token;
      int end_pos = -1;
      if(!_RefJsonReadString(json, i, token, end_pos)) return false;
      int colon = _JsonSkipWs(json, end_pos + 1);
      if(token == key && colon < len && (ushort)StringGetCharacter(json, colon) == ':'){
         value_pos = _JsonSkipWs(json, colon + 1);
         return (value_pos >= 0 && value_pos < len);
      }
      i = end_pos;
   }
   return false;
}

//--- Harness ---------------------------------------------------------------

int    g_checks = 0;
int    g_failures = 0;
string g_first_failure = "";

void _Fail(const string where, const string detail) {
   g_failures++;
   if(StringLen(g_first_failure) == 0) g_first_failure = where + ": " + detail;
   if(g_failures <= 20)
      Print("[json_index_selftest] FAIL case=", where, " ", detail);
}

// The whole contract in one place: for this key, on this document, the shipped
// locator must return the same boolean AND the same value position as the
// pre-index scan.
void _AssertSameLocate(const string where, const string json, const string key) {
   int ref_pos = -1;
   int new_pos = -1;
   bool ref_ok = _RefJsonLocateKeyValue(json, key, ref_pos);
   bool new_ok = _JsonLocateKeyValue(json, key, new_pos);
   g_checks++;
   if(ref_ok != new_ok){
      _Fail(where, "key=" + key + " ref_ok=" + (ref_ok ? "true" : "false")
                 + " new_ok=" + (new_ok ? "true" : "false"));
      return;
   }
   if(ref_ok && ref_pos != new_pos)
      _Fail(where, "key=" + key + " ref_pos=" + IntegerToString(ref_pos)
                 + " new_pos=" + IntegerToString(new_pos));
}

void _AssertSameReadString(const string where, const string json, const int quote_pos) {
   string ref_out = "";
   string new_out = "";
   int ref_end = -1;
   int new_end = -1;
   bool ref_ok = _RefJsonReadString(json, quote_pos, ref_out, ref_end);
   bool new_ok = _JsonReadString(json, quote_pos, new_out, new_end);
   g_checks++;
   if(ref_ok != new_ok || (ref_ok && (ref_out != new_out || ref_end != new_end)))
      _Fail(where, "quote_pos=" + IntegerToString(quote_pos)
                 + " ref_ok=" + (ref_ok ? "true" : "false")
                 + " new_ok=" + (new_ok ? "true" : "false")
                 + " ref_out=[" + ref_out + "] new_out=[" + new_out + "]"
                 + " ref_end=" + IntegerToString(ref_end)
                 + " new_end=" + IntegerToString(new_end));
}

// Every token that the pre-index scan would have compared against, so the sweep
// covers real keys, string values that merely look like keys, and anything else
// the document happens to quote.
void _CollectTokens(const string json, string &tokens[]) {
   ArrayResize(tokens, 0, 4096);
   int len = (int)StringLen(json);
   for(int i=0; i<len; i++){
      if((ushort)StringGetCharacter(json, i) != '\"') continue;
      string token;
      int end_pos = -1;
      if(!_RefJsonReadString(json, i, token, end_pos)) break;
      int n = ArraySize(tokens);
      ArrayResize(tokens, n + 1, 4096);
      tokens[n] = token;
      i = end_pos;
   }
}

void _SweepEveryToken(const string where, const string json) {
   string tokens[];
   _CollectTokens(json, tokens);
   for(int i=0; i<ArraySize(tokens); i++)
      _AssertSameLocate(where, json, tokens[i]);
   // Keys that are not there must be absent on both sides too.
   _AssertSameLocate(where, json, "key_that_does_not_exist_anywhere");
   _AssertSameLocate(where, json, "");
   _AssertSameLocate(where, json, "symbol ");
}

//--- Documents -------------------------------------------------------------

// Shaped like a trade meta: flat, several hundred keys, mixed value types, one
// embedded object and one embedded array, comfortably over the index threshold.
string _BuildLargeFlatDocument(const int fields, const string salt) {
   string j = "{";
   StringReserve(j, 262144);
   j += "\"symbol\":\"#Japan225" + salt + "\",";
   j += "\"is_buy\":false,";
   j += "\"entry\":62679.50000000,";
   j += "\"narrative_state\":\"executed\",";
   j += "\"risk_factor_contributions\":{\"spread\":0.25,\"slippage\":-0.10},";
   j += "\"ai_retrieved_analogue_ids\":[11,12,13],";
   for(int i=0; i<fields; i++){
      string k = "field_" + IntegerToString(i);
      j += "\"" + k + "_num\":" + DoubleToString(i * 1.5, 6) + ",";
      j += "\"" + k + "_str\":\"value " + IntegerToString(i) + " " + salt + "\",";
      j += "\"" + k + "_bool\":" + ((i % 2) == 0 ? "true" : "false") + ",";
   }
   j += "\"latest_observed_tick_time\":1785770399,";
   j += "\"latest_observed_tick_msc\":1785770399123";
   j += "}";
   return j;
}

//--- Cases -----------------------------------------------------------------

void _CaseLargeFlat() {
   string doc = _BuildLargeFlatDocument(400, "a");
   Print("[json_index_selftest] case=large_flat chars=", StringLen(doc));
   _SweepEveryToken("large_flat", doc);
}

void _CaseSmallDocumentKeepsScanPath() {
   // Below JSON_INDEX_MIN_CHARS: the shipped locator must still agree, and this is
   // the path that proves the threshold branch did not change any answer.
   string doc = "{\"symbol\":\"EURUSD\",\"is_buy\":true,\"entry\":1.15230,\"tp1\":0}";
   Print("[json_index_selftest] case=small chars=", StringLen(doc));
   _SweepEveryToken("small", doc);
}

void _CaseDuplicateKeys() {
   // The scan returns the FIRST occurrence and stops.  The index records only the
   // first.  If that ever diverges, a reloaded position would silently bind to a
   // different value than the one the engine wrote.
   string doc = "{\"dup\":111,";
   StringReserve(doc, 65536);
   for(int i=0; i<300; i++) doc += "\"pad_" + IntegerToString(i) + "\":\"" + IntegerToString(i) + "\",";
   doc += "\"dup\":222,\"tail\":1}";
   Print("[json_index_selftest] case=duplicate chars=", StringLen(doc));
   _SweepEveryToken("duplicate", doc);
   _AssertSameLocate("duplicate", doc, "dup");
}

void _CaseEscapedKeysAndValues() {
   string doc = "{\"quote\\\"key\":1,\"back\\\\slash\":2,\"news\\nline\":3,\"tab\\tkey\":4,";
   StringReserve(doc, 65536);
   for(int i=0; i<300; i++)
      doc += "\"esc_" + IntegerToString(i) + "\":\"a\\\"b\\\\c\\nd " + IntegerToString(i) + "\",";
   doc += "\"tail\":\"done\"}";
   Print("[json_index_selftest] case=escaped chars=", StringLen(doc));
   _SweepEveryToken("escaped", doc);
   // Read every quoted token position directly, so the fast path and the escape
   // decoder are both compared character for character.
   int len = (int)StringLen(doc);
   for(int i=0; i<len; i++)
      if((ushort)StringGetCharacter(doc, i) == '\"')
         _AssertSameReadString("escaped_readstring", doc, i);
}

void _CaseUnterminatedStringMidDocument() {
   // The scan aborts at the malformed token and answers false for every key it had
   // not yet reached.  The index build stops at the same character, so keys past
   // the break must be absent there too.
   string doc = "{\"before_a\":1,\"before_b\":2,";
   StringReserve(doc, 65536);
   // Padded past JSON_INDEX_MIN_CHARS on purpose: below the threshold this case
   // would only exercise the scan it is supposed to be compared against.
   for(int i=0; i<600; i++) doc += "\"pad_" + IntegerToString(i) + "\":" + IntegerToString(i) + ",";
   doc += "\"broken\":\"no closing quote here";
   Print("[json_index_selftest] case=unterminated chars=", StringLen(doc));
   _SweepEveryToken("unterminated", doc);
   _AssertSameLocate("unterminated", doc, "before_a");
   _AssertSameLocate("unterminated", doc, "pad_299");
   _AssertSameLocate("unterminated", doc, "broken");
}

void _CaseKeyWithNoValueAtEnd() {
   string doc = "{";
   StringReserve(doc, 65536);
   // Same reason as the unterminated case: it must land on the index path.
   for(int i=0; i<600; i++) doc += "\"pad_" + IntegerToString(i) + "\":" + IntegerToString(i) + ",";
   doc += "\"trailing\":";
   Print("[json_index_selftest] case=no_value chars=", StringLen(doc));
   _SweepEveryToken("no_value", doc);
   _AssertSameLocate("no_value", doc, "trailing");
}

void _CaseInterleavedLargeDocuments() {
   // Only one document is resident, so alternating lookups force a rebuild every
   // time.  Correctness must not depend on which document was indexed last.
   string a = _BuildLargeFlatDocument(200, "a");
   string b = _BuildLargeFlatDocument(200, "b");
   // Same length, different characters: this is what defeats a length-only guard.
   Print("[json_index_selftest] case=interleaved chars_a=", StringLen(a), " chars_b=", StringLen(b));
   for(int i=0; i<120; i++){
      string k = "field_" + IntegerToString(i) + "_str";
      _AssertSameLocate("interleaved_a", a, k);
      _AssertSameLocate("interleaved_b", b, k);
      _AssertSameLocate("interleaved_a", a, "symbol");
      _AssertSameLocate("interleaved_b", b, "symbol");
   }
   // And the values actually read back must differ per document.
   g_checks++;
   if(JsonGetString(a, "symbol", "") == JsonGetString(b, "symbol", ""))
      _Fail("interleaved", "two different documents returned the same symbol");
}

void _CaseAccessorsAgree() {
   // The locator feeds every public accessor; a position that is right but a value
   // that is read from it wrongly would still be a defect.
   string doc = _BuildLargeFlatDocument(300, "c");
   g_checks++;
   if(JsonGetString(doc, "narrative_state", "") != "executed") _Fail("accessors", "narrative_state");
   g_checks++;
   if(JsonGetNumber(doc, "entry", 0) != 62679.50) _Fail("accessors", "entry");
   g_checks++;
   if(JsonGetBool(doc, "is_buy", true) != false) _Fail("accessors", "is_buy");
   g_checks++;
   if(JsonGetNumber(doc, "latest_observed_tick_time", 0) != 1785770399) _Fail("accessors", "watermark_time");
   g_checks++;
   if(JsonGetObject(doc, "risk_factor_contributions", "") != "{\"spread\":0.25,\"slippage\":-0.10}")
      _Fail("accessors", "object");
   g_checks++;
   if(JsonGetArray(doc, "ai_retrieved_analogue_ids", "") != "[11,12,13]") _Fail("accessors", "array");
   g_checks++;
   if(JsonGetNumber(doc, "field_299_num", -1) != 448.5) _Fail("accessors", "field_299_num");
   g_checks++;
   if(JsonGetString(doc, "field_0_str", "") != "value 0 c") _Fail("accessors", "field_0_str");
   g_checks++;
   if(JsonHasKey(doc, "field_299_bool") != true) _Fail("accessors", "has_key_present");
   g_checks++;
   if(JsonHasKey(doc, "field_300_bool") != false) _Fail("accessors", "has_key_absent");
   // Strict readers walk their own top-level scan and must be unaffected.
   string strict_out = "";
   g_checks++;
   if(!JsonGetStringStrict(doc, "narrative_state", strict_out) || strict_out != "executed")
      _Fail("accessors", "strict_string");
}

void _CaseRealTradeMeta() {
   // Best evidence available: the actual document the engine writes.  Skipped
   // rather than failed when no run has produced one yet.
   string path = "PO3_AI_BUS\\logs\\trade_position_2.json";
   int h = FileOpen(path, FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE){
      Print("[json_index_selftest] case=real_trade_meta skipped=file_not_available path=", path);
      return;
   }
   int size = (int)FileSize(h);
   uchar bytes[];
   ArrayResize(bytes, size);
   uint got = FileReadArray(h, bytes, 0, size);
   FileClose(h);
   string doc = "";
   StringReserve(doc, size);
   int start = 0;
   bool utf16 = (got >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE);
   if(utf16){
      for(int i=2; i+1<(int)got; i+=2)
         doc += ShortToString((ushort)(bytes[i] | (bytes[i+1] << 8)));
   } else {
      for(int i=start; i<(int)got; i++)
         doc += ShortToString((ushort)bytes[i]);
   }
   Print("[json_index_selftest] case=real_trade_meta chars=", StringLen(doc), " utf16=", utf16);
   if(StringLen(doc) < 1000){
      Print("[json_index_selftest] case=real_trade_meta skipped=document_too_small");
      return;
   }
   _SweepEveryToken("real_trade_meta", doc);
}

//+------------------------------------------------------------------+
int OnInit() {
   Print("[json_index_selftest] start threshold_chars=", JSON_INDEX_MIN_CHARS,
         " buckets=", JSON_INDEX_BUCKETS);

   _CaseSmallDocumentKeepsScanPath();
   _CaseLargeFlat();
   _CaseDuplicateKeys();
   _CaseEscapedKeysAndValues();
   _CaseUnterminatedStringMidDocument();
   _CaseKeyWithNoValueAtEnd();
   _CaseInterleavedLargeDocuments();
   _CaseAccessorsAgree();
   _CaseRealTradeMeta();

   Print("[json_index_selftest] result=", (g_failures == 0 ? "PASS" : "FAIL"),
         " checks=", g_checks, " failures=", g_failures,
         " first_failure=", (StringLen(g_first_failure) > 0 ? g_first_failure : "none"));

   // Timing, on the same document shape the engine reads, so the claim that the
   // index is faster is a measurement and not an assertion.
   string doc = _BuildLargeFlatDocument(400, "t");
   string tokens[];
   _CollectTokens(doc, tokens);
   int n = ArraySize(tokens);
   ulong t0 = GetMicrosecondCount();
   for(int i=0; i<n; i++){ int p = -1; _RefJsonLocateKeyValue(doc, tokens[i], p); }
   ulong ref_us = GetMicrosecondCount() - t0;
   ulong t1 = GetMicrosecondCount();
   for(int i=0; i<n; i++){ int p = -1; _JsonLocateKeyValue(doc, tokens[i], p); }
   ulong new_us = GetMicrosecondCount() - t1;
   Print("[json_index_selftest] timing chars=", StringLen(doc), " lookups=", n,
         " scan_ms=", DoubleToString((double)ref_us / 1000.0, 1),
         " index_ms=", DoubleToString((double)new_us / 1000.0, 1),
         " speedup=", DoubleToString(new_us > 0 ? (double)ref_us / (double)new_us : 0.0, 1));

   return INIT_FAILED;   // report only; never let this run trade
}

void OnTick() {}
