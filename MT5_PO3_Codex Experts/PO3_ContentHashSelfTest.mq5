//+------------------------------------------------------------------+
//| PO3_ContentHashSelfTest.mq5                                      |
//|                                                                  |
//| Determinism proof for _CommonFileContentHash.                    |
//|                                                                  |
//| _CommonFileContentHash feeds RuntimeInputHash() and              |
//| DecisionInputHash().  DecisionInputHash() is component 4 of every |
//| tester replay cache signature, so if it is not a pure function of |
//| the file on disk, the whole recorded cohort silently becomes      |
//| unreachable half way through a CACHE_ONLY replay.                 |
//|                                                                  |
//| The legacy reader opened the policy file with FILE_TXT (which is  |
//| UTF-16 in MQL5 unless FILE_ANSI is given) and concatenated        |
//| FileReadString() results.  Both policy files on disk have an ODD  |
//| byte count, so the byte stream does not divide into whole UTF-16  |
//| code units and the trailing read is not well defined.            |
//|                                                                  |
//| This EA calls each reader many times in a row against the same    |
//| untouched files and reports how many DISTINCT answers each one    |
//| produced.  A content hash must produce exactly one.               |
//|                                                                  |
//| Run in the Strategy Tester on any symbol and any short period.    |
//| It reports in OnInit and stops the run immediately.  It never     |
//| trades, never calls the AI and never reads market data.          |
//+------------------------------------------------------------------+
#property copyright "PO3_Codex"
#property version   "1.00"
#property strict

input string InpFileA = "PO3_AI_BUS\\config\\normalized_fvg_policy.v2.json";
input string InpFileB = "PO3_AI_BUS\\config\\invalidation_policy.v1.json";
input int    InpIterations = 400;

//--- FNV-1a over a string, verbatim from AIGateBridge.mqh -------------------
uint _TestFnv1a(const string value) {
   uint h = 2166136261;
   for(int i=0; i<StringLen(value); i++){
      h = (h ^ (uint)StringGetCharacter(value, i)) * 16777619;
   }
   return h;
}

//--- FNV-1a over raw bytes, the replacement primitive ----------------------
uint _TestFnv1aBytes(const uchar &bytes[], const int count) {
   uint h = 2166136261;
   for(int i=0; i<count; i++){
      h = (h ^ (uint)bytes[i]) * 16777619;
   }
   return h;
}

//--- LEGACY reader: verbatim copy of _CommonFileContentHash ----------------
// Copied unchanged from AIGateBridge.mqh before the fix.  This is the thing
// under test; whatever it answers is what the shipped signature contained.
string _LegacyCommonFileContentHash(const string path, int &out_len) {
   out_len = -1;
   int h = FileOpen(path, FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return "UNAVAILABLE";
   string content = "";
   while(!FileIsEnding(h)) content += FileReadString(h);
   FileClose(h);
   out_len = (int)StringLen(content);
   return IntegerToString((int)(_TestFnv1a(content) % 2147483647));
}

//--- NEW reader: byte exact, no encoding interpretation --------------------
string _BinaryCommonFileContentHash(const string path, int &out_len) {
   out_len = -1;
   int h = FileOpen(path, FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return "UNAVAILABLE";
   ulong size64 = FileSize(h);
   if(size64 > 268435456){ FileClose(h); return "UNAVAILABLE"; }
   int size = (int)size64;
   uchar bytes[];
   if(size > 0 && ArrayResize(bytes, size) != size){ FileClose(h); return "UNAVAILABLE"; }
   uint got = 0;
   if(size > 0) got = FileReadArray(h, bytes, 0, size);
   FileClose(h);
   if((int)got != size) return "UNAVAILABLE";
   out_len = size;
   return IntegerToString((int)(_TestFnv1aBytes(bytes, size) % 2147483647));
}

//--- distinct-value collector ---------------------------------------------
void _Tally(const string value, string &vals[], int &counts[]) {
   for(int i=0; i<ArraySize(vals); i++){
      if(vals[i] == value){ counts[i]++; return; }
   }
   int n = ArraySize(vals);
   ArrayResize(vals, n+1);
   ArrayResize(counts, n+1);
   vals[n] = value;
   counts[n] = 1;
}

string _Report(const string label, const string path, const bool binary) {
   string vals[]; int counts[];
   ArrayResize(vals, 0); ArrayResize(counts, 0);
   int len_vals[]; int len_counts[];
   ArrayResize(len_vals, 0); ArrayResize(len_counts, 0);
   int distinct_len = 0;
   for(int i=0; i<InpIterations; i++){
      int len = -1;
      string v = (binary ? _BinaryCommonFileContentHash(path, len)
                         : _LegacyCommonFileContentHash(path, len));
      _Tally(v, vals, counts);
      bool seen = false;
      for(int k=0; k<ArraySize(len_vals); k++) if(len_vals[k] == len){ len_counts[k]++; seen = true; break; }
      if(!seen){
         int n = ArraySize(len_vals);
         ArrayResize(len_vals, n+1); ArrayResize(len_counts, n+1);
         len_vals[n] = len; len_counts[n] = 1;
      }
   }
   distinct_len = ArraySize(len_vals);
   string detail = "";
   for(int i=0; i<ArraySize(vals); i++){
      if(StringLen(detail) > 0) detail += ",";
      detail += vals[i] + "x" + IntegerToString(counts[i]);
   }
   string len_detail = "";
   for(int i=0; i<ArraySize(len_vals); i++){
      if(StringLen(len_detail) > 0) len_detail += ",";
      len_detail += IntegerToString(len_vals[i]) + "x" + IntegerToString(len_counts[i]);
   }
   int distinct = ArraySize(vals);
   Print("[content_hash_selftest] reader=", label,
         " file=", path,
         " iterations=", InpIterations,
         " distinct_hashes=", distinct,
         " distinct_read_lengths=", distinct_len,
         " verdict=", (distinct == 1 ? "DETERMINISTIC" : "NON_DETERMINISTIC"),
         " hashes=", detail,
         " read_lengths=", len_detail);
   return (distinct == 1 ? "pass" : "fail");
}

int OnInit() {
   Print("[content_hash_selftest] begin build=", __MQLBUILD__, " tester=", (bool)MQLInfoInteger(MQL_TESTER));

   // Report the on-disk size so the odd-byte-count hazard is visible in the log.
   string paths[2]; paths[0] = InpFileA; paths[1] = InpFileB;
   for(int i=0; i<2; i++){
      int h = FileOpen(paths[i], FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE){
         Print("[content_hash_selftest] file=", paths[i], " status=UNREADABLE");
         continue;
      }
      ulong sz = FileSize(h);
      FileClose(h);
      Print("[content_hash_selftest] file=", paths[i], " bytes=", sz, " odd_byte_count=", (sz % 2 == 1 ? "true" : "false"));
   }

   string legacy_a = _Report("legacy_file_txt", InpFileA, false);
   string legacy_b = _Report("legacy_file_txt", InpFileB, false);
   string bin_a    = _Report("binary_file_bin", InpFileA, true);
   string bin_b    = _Report("binary_file_bin", InpFileB, true);

   bool legacy_stable = (legacy_a == "pass" && legacy_b == "pass");
   bool binary_stable = (bin_a == "pass" && bin_b == "pass");
   Print("[content_hash_selftest] result legacy_stable=", (legacy_stable ? "true" : "false"),
         " binary_stable=", (binary_stable ? "true" : "false"));
   Print("[content_hash_selftest] end");
   ExpertRemove();
   return INIT_SUCCEEDED;
}

void OnTick() {}
void OnDeinit(const int reason) {}
