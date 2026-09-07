//+------------------------------------------------------------------+
//| JsonLite.mqh - tiny JSON helpers                                 |
//| NOTE: intentionally minimal (single-level parse).                 |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_JSONLITE_MQH__
#define __PO3_AIGATE_JSONLITE_MQH__

bool _JsonIsWs(const ushort c) {
   return (c == ' ' || c == '\t' || c == '\r' || c == '\n');
}

int _JsonSkipWs(const string json, int pos) {
   int len = (int)StringLen(json);
   while(pos < len && _JsonIsWs((ushort)StringGetCharacter(json, pos))) pos++;
   return pos;
}

bool _JsonIsHex(const ushort c) {
   return ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'));
}

// Validates the complete JSON transport envelope before any key lookup.  The
// field readers below remain deliberately small, but they never receive a
// partial/trailing document or malformed Unicode sequence.
bool JsonValidateDocumentStrict(const string json, string &reason) {
   reason = "";
   int len = (int)StringLen(json);
   int pos = _JsonSkipWs(json, 0);
   if(pos >= len || (ushort)StringGetCharacter(json, pos) != '{'){
      reason = "json_root_not_object";
      return false;
   }
   int object_depth = 0;
   int array_depth = 0;
   bool in_string = false;
   bool escaped = false;
   int root_end = -1;
   for(int i=pos; i<len; i++){
      ushort c = (ushort)StringGetCharacter(json, i);
      if(in_string){
         if(escaped){
            if(c == 'u'){
               if(i + 4 >= len){ reason = "json_unicode_escape_truncated"; return false; }
               for(int h=1; h<=4; h++){
                  if(!_JsonIsHex((ushort)StringGetCharacter(json, i+h))){
                     reason = "json_unicode_escape_invalid";
                     return false;
                  }
               }
               i += 4;
            } else if(c != '"' && c != '\\' && c != '/' && c != 'b' && c != 'f' &&
                      c != 'n' && c != 'r' && c != 't'){
               reason = "json_escape_invalid";
               return false;
            }
            escaped = false;
            continue;
         }
         if(c == '\\'){ escaped = true; continue; }
         if(c == '"'){ in_string = false; continue; }
         if(c < 0x20){ reason = "json_control_character_invalid"; return false; }
         if(c >= 0xD800 && c <= 0xDBFF){
            if(i + 1 >= len){ reason = "json_unicode_high_surrogate_unpaired"; return false; }
            ushort low = (ushort)StringGetCharacter(json, i+1);
            if(low < 0xDC00 || low > 0xDFFF){ reason = "json_unicode_high_surrogate_unpaired"; return false; }
            i++;
            continue;
         }
         if(c >= 0xDC00 && c <= 0xDFFF){ reason = "json_unicode_low_surrogate_unpaired"; return false; }
         continue;
      }
      if(c == '"'){ in_string = true; continue; }
      if(c == '{') object_depth++;
      else if(c == '}'){
         object_depth--;
         if(object_depth < 0){ reason = "json_object_depth_invalid"; return false; }
         if(object_depth == 0 && array_depth == 0){ root_end = i; break; }
      } else if(c == '[') array_depth++;
      else if(c == ']'){
         array_depth--;
         if(array_depth < 0){ reason = "json_array_depth_invalid"; return false; }
      }
   }
   if(in_string || escaped){ reason = "json_string_unterminated"; return false; }
   if(root_end < 0 || object_depth != 0 || array_depth != 0){ reason = "json_document_incomplete"; return false; }
   if(_JsonSkipWs(json, root_end + 1) != len){ reason = "json_trailing_content"; return false; }
   return true;
}

string JsonEscape(const string s) {
   string out = s;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\r", "\\r");
   StringReplace(out, "\t", "\\t");
   return out;
}

string JsonKVStr(const string k, const string v) {
   return "\"" + JsonEscape(k) + "\":\"" + JsonEscape(v) + "\"";
}

string JsonKVNum(const string k, const double v, const int digits=6) {
   return "\"" + JsonEscape(k) + "\":" + DoubleToString(v, digits);
}

string JsonKVInt(const string k, const int v) {
   return "\"" + JsonEscape(k) + "\":" + IntegerToString(v);
}

string JsonKVBool(const string k, const bool v) {
   return "\"" + JsonEscape(k) + "\":" + (v ? "true" : "false");
}

bool _JsonReadString(const string json, const int quote_pos, string &out, int &end_pos) {
   int len = (int)StringLen(json);
   if(quote_pos < 0 || quote_pos >= len) return false;
   if((ushort)StringGetCharacter(json, quote_pos) != '\"') return false;

   // Fast path.  The decoder below appends ONE character at a time, and every append
   // of a one-character StringSubstr allocates; the key scan walks past every quoted
   // token in the document, so reading a ~76,000 character trade meta cost tens of
   // millions of those allocations.  A token containing no backslash decodes to
   // exactly its own characters, so it is one substring.  Escaped tokens fall through
   // to the original decoder unchanged -- this adds a shortcut, it does not add a
   // second definition of what an escape means.
   bool has_escape = false;
   for(int scan=quote_pos+1; scan<len; ){
      ushort ch = (ushort)StringGetCharacter(json, scan);
      if(ch == '\\'){ has_escape = true; scan += 2; continue; }
      if(ch == '\"'){
         if(has_escape) break;
         out = (scan > quote_pos + 1 ? StringSubstr(json, quote_pos + 1, scan - quote_pos - 1) : "");
         end_pos = scan;
         return true;
      }
      scan++;
   }
   if(!has_escape) return false;   // unterminated, and no escape to re-decode

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

// The original locator: one full scan from position 0 per key.  Kept as the only
// implementation for small documents and as the definition the index below must
// reproduce.
bool _JsonScanLocateKeyValue(const string json, const string key, int &value_pos) {
   int len = (int)StringLen(json);
   value_pos = -1;
   for(int i=0; i<len; i++){
      if((ushort)StringGetCharacter(json, i) != '\"') continue;
      string token;
      int end_pos = -1;
      if(!_JsonReadString(json, i, token, end_pos)) return false;
      int colon = _JsonSkipWs(json, end_pos + 1);
      if(token == key && colon < len && (ushort)StringGetCharacter(json, colon) == ':'){
         value_pos = _JsonSkipWs(json, colon + 1);
         return (value_pos >= 0 && value_pos < len);
      }
      i = end_pos;
   }
   return false;
}

//--- One-pass key index for large documents ---------------------------------
// _JsonScanLocateKeyValue() rescans the whole document for every key.  A trade meta
// is ~76,000 characters holding ~800 keys and StateStore reads all of them from the
// same string, so one document cost ~800 full scans.  Measured at 505 ms per
// document, and the position-maintenance loop reads two of them per simulated
// second -- which is why replay throughput collapsed the moment a position opened
// and never recovered.
//
// The index is built in ONE pass and records the FIRST occurrence of each distinct
// key in document order, which is exactly what the scan returned, so a lookup is
// equivalent to the scan rather than merely similar.  Three details make that
// equality exact rather than approximate:
//   * A scan that aborts on a malformed string returns false for every key it had
//     not yet reached.  The build stops at the same character, so keys past that
//     point are absent from the index and their lookups return false too.
//   * The scan returns the first occurrence of a duplicated key; only the first is
//     recorded here.
//   * The scan's final "value_pos < len" test is applied at lookup, not at build.
//
// The resident document is validated by a full character comparison, never by a
// hash or a fingerprint: a fingerprint collision would silently hand a caller some
// other document's field, and this parses live position state.  Documents below
// the threshold keep the direct scan -- they are already cheap, and excluding them
// stops a stream of small documents from evicting the one large document that a
// long read is walking through.
#define JSON_INDEX_MIN_CHARS 4096
#define JSON_INDEX_BUCKETS   4096

string _json_index_doc = "";
bool   _json_index_ready = false;
string _json_index_key[];
int    _json_index_value_pos[];
int    _json_index_next[];
int    _json_index_head[JSON_INDEX_BUCKETS];

uint _JsonKeyHash(const string s) {
   uint h = 2166136261;
   int n = (int)StringLen(s);
   for(int i=0; i<n; i++){
      h ^= (uint)StringGetCharacter(s, i);
      h *= 16777619;
   }
   return h;
}

// Bucket walk with a full key comparison on every candidate: the hash chooses
// where to look, it never decides that two keys are the same.
int _JsonIndexFindEntry(const string key) {
   int bucket = (int)(_JsonKeyHash(key) % JSON_INDEX_BUCKETS);
   for(int e=_json_index_head[bucket]; e>=0; e=_json_index_next[e]){
      if(_json_index_key[e] == key) return e;
   }
   return -1;
}

void _JsonIndexBuild(const string json) {
   _json_index_doc   = "";
   _json_index_ready = false;
   ArrayResize(_json_index_key, 0, 2048);
   ArrayResize(_json_index_value_pos, 0, 2048);
   ArrayResize(_json_index_next, 0, 2048);
   ArrayInitialize(_json_index_head, -1);

   int len = (int)StringLen(json);
   for(int i=0; i<len; i++){
      if((ushort)StringGetCharacter(json, i) != '\"') continue;
      string token;
      int end_pos = -1;
      if(!_JsonReadString(json, i, token, end_pos)) break;   // the scan stopped here too
      int colon = _JsonSkipWs(json, end_pos + 1);
      if(colon < len && (ushort)StringGetCharacter(json, colon) == ':' &&
         _JsonIndexFindEntry(token) < 0){
         int n = ArraySize(_json_index_key);
         ArrayResize(_json_index_key, n + 1, 2048);
         ArrayResize(_json_index_value_pos, n + 1, 2048);
         ArrayResize(_json_index_next, n + 1, 2048);
         int bucket = (int)(_JsonKeyHash(token) % JSON_INDEX_BUCKETS);
         _json_index_key[n]       = token;
         _json_index_value_pos[n] = _JsonSkipWs(json, colon + 1);
         _json_index_next[n]      = _json_index_head[bucket];
         _json_index_head[bucket] = n;
      }
      i = end_pos;
   }
   _json_index_doc   = json;
   _json_index_ready = true;
}

bool _JsonLocateKeyValue(const string json, const string key, int &value_pos) {
   value_pos = -1;
   int len = (int)StringLen(json);
   if(len < JSON_INDEX_MIN_CHARS) return _JsonScanLocateKeyValue(json, key, value_pos);
   if(!_json_index_ready || StringLen(_json_index_doc) != len || _json_index_doc != json)
      _JsonIndexBuild(json);
   int entry = _JsonIndexFindEntry(key);
   if(entry < 0) return false;
   value_pos = _json_index_value_pos[entry];
   return (value_pos >= 0 && value_pos < len);
}

bool JsonHasKey(const string json, const string key) {
   int pos = -1;
   return _JsonLocateKeyValue(json, key, pos);
}

bool _JsonReadComposite(const string json, const int start_pos, const ushort open_ch, const ushort close_ch,
                        string &out, int &end_pos) {
   int len = (int)StringLen(json);
   if(start_pos < 0 || start_pos >= len) return false;
   if((ushort)StringGetCharacter(json, start_pos) != open_ch) return false;

   bool in_string = false;
   bool esc = false;
   int depth = 0;
   for(int i=start_pos; i<len; i++){
      ushort c = (ushort)StringGetCharacter(json, i);
      if(in_string){
         if(esc){
            esc = false;
            continue;
         }
         if(c == '\\'){
            esc = true;
            continue;
         }
         if(c == '\"'){
            in_string = false;
         }
         continue;
      }

      if(c == '\"'){
         in_string = true;
         continue;
      }
      if(c == open_ch){
         depth++;
         continue;
      }
      if(c == close_ch){
         depth--;
         if(depth == 0){
            end_pos = i;
            out = StringSubstr(json, start_pos, i - start_pos + 1);
            return true;
         }
      }
   }
   return false;
}

string JsonGetObject(const string json, const string key, const string def="") {
   int pos = -1;
   if(!_JsonLocateKeyValue(json, key, pos)) return def;
   string out;
   int end_pos = -1;
   if(!_JsonReadComposite(json, pos, '{', '}', out, end_pos)) return def;
   return out;
}

string JsonGetArray(const string json, const string key, const string def="[]") {
   int pos = -1;
   if(!_JsonLocateKeyValue(json, key, pos)) return def;
   string out;
   int end_pos = -1;
   if(!_JsonReadComposite(json, pos, '[', ']', out, end_pos)) return def;
   return out;
}

string JsonGetString(const string json, const string key, const string def="") {
   int q1 = -1;
   if(!_JsonLocateKeyValue(json, key, q1)) return def;
   if(q1 < 0 || q1 >= (int)StringLen(json)) return def;
   if((ushort)StringGetCharacter(json, q1) != '\"') return def;

   string out;
   int q2 = -1;
   if(!_JsonReadString(json, q1, out, q2)) return def;
   return out;
}

double JsonGetNumber(const string json, const string key, const double def=0.0) {
   int start = -1;
   if(!_JsonLocateKeyValue(json, key, start)) return def;
   int end = start;
   while(end < (int)StringLen(json)) {
      ushort c = (ushort)StringGetCharacter(json, end);
      if((c>='0' && c<='9') || c=='-' || c=='+' || c=='.' || c=='e' || c=='E') { end++; continue; }
      break;
   }
   string num = StringSubstr(json, start, end-start);
   if(StringLen(num)==0) return def;
   return StringToDouble(num);
}

bool JsonGetBool(const string json, const string key, const bool def=false) {
   int start = -1;
   if(!_JsonLocateKeyValue(json, key, start)) return def;
   string tail = StringSubstr(json, start, 6);
   if(StringFind(tail, "true")==0) return true;
   if(StringFind(tail, "false")==0) return false;
   return def;
}

bool _JsonLocateTopLevelKeyValue(const string json, const string key, int &value_pos, int &key_count) {
   value_pos = -1;
   key_count = 0;
   int len = (int)StringLen(json);
   int depth_object = 0;
   int depth_array = 0;
   bool in_string = false;
   bool esc = false;
   for(int i=0; i<len; i++){
      ushort c = (ushort)StringGetCharacter(json, i);
      if(in_string){
         if(esc){ esc = false; continue; }
         if(c == '\\'){ esc = true; continue; }
         if(c == '"') in_string = false;
         continue;
      }
      if(c == '"'){
         if(depth_object == 1 && depth_array == 0){
            string token;
            int end_pos = -1;
            if(!_JsonReadString(json, i, token, end_pos)) return false;
            int colon = _JsonSkipWs(json, end_pos + 1);
            if(token == key && colon < len && (ushort)StringGetCharacter(json, colon) == ':'){
               key_count++;
               if(value_pos < 0) value_pos = _JsonSkipWs(json, colon + 1);
            }
            i = end_pos;
            continue;
         }
         in_string = true;
         continue;
      }
      if(c == '{') depth_object++;
      else if(c == '}') depth_object--;
      else if(c == '[') depth_array++;
      else if(c == ']') depth_array--;
   }
   return (value_pos >= 0 && key_count == 1);
}

int JsonTopLevelKeyCount(const string json, const string key) {
   int pos = -1;
   int count = 0;
   _JsonLocateTopLevelKeyValue(json, key, pos, count);
   return count;
}

bool JsonValueIsNullStrict(const string json, const string key) {
   int pos = -1;
   int count = 0;
   if(!_JsonLocateTopLevelKeyValue(json, key, pos, count)) return false;
   return (StringFind(StringSubstr(json, pos, 4), "null") == 0);
}

bool JsonGetStringStrict(const string json, const string key, string &out, const bool allow_empty=false) {
   int pos = -1;
   int count = 0;
   if(!_JsonLocateTopLevelKeyValue(json, key, pos, count)) return false;
   int end_pos = -1;
   if(!_JsonReadString(json, pos, out, end_pos)) return false;
   return (allow_empty || StringLen(out) > 0);
}

bool JsonGetBoolStrict(const string json, const string key, bool &out) {
   int pos = -1;
   int count = 0;
   if(!_JsonLocateTopLevelKeyValue(json, key, pos, count)) return false;
   string tail = StringSubstr(json, pos, 5);
   if(StringFind(tail, "true") == 0){ out = true; return true; }
   if(StringFind(tail, "false") == 0){ out = false; return true; }
   return false;
}

bool JsonGetNumberStrict(const string json, const string key, double &out) {
   int start = -1;
   int count = 0;
   if(!_JsonLocateTopLevelKeyValue(json, key, start, count)) return false;
   int len = (int)StringLen(json);
   int end = start;
   while(end < len){
      ushort c = (ushort)StringGetCharacter(json, end);
      if((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E'){
         end++;
         continue;
      }
      break;
   }
   if(end <= start) return false;
   int after = _JsonSkipWs(json, end);
   if(after < len){
      ushort delim = (ushort)StringGetCharacter(json, after);
      if(delim != ',' && delim != '}' && delim != ']') return false;
   }
   string token = StringSubstr(json, start, end - start);
   out = StringToDouble(token);
   return MathIsValidNumber(out);
}

bool JsonGetObjectStrict(const string json, const string key, string &out) {
   int pos = -1;
   int count = 0;
   if(!_JsonLocateTopLevelKeyValue(json, key, pos, count)) return false;
   int end_pos = -1;
   return _JsonReadComposite(json, pos, '{', '}', out, end_pos);
}

bool JsonGetArrayStrict(const string json, const string key, string &out) {
   int pos = -1;
   int count = 0;
   if(!_JsonLocateTopLevelKeyValue(json, key, pos, count)) return false;
   int end_pos = -1;
   return _JsonReadComposite(json, pos, '[', ']', out, end_pos);
}

bool JsonArrayGetObject(const string array_json, const int wanted_index, string &out) {
   out = "";
   if(wanted_index < 0) return false;
   int len = (int)StringLen(array_json);
   int index = 0;
   for(int pos=1; pos<len-1; ){
      pos = _JsonSkipWs(array_json, pos);
      while(pos < len && (ushort)StringGetCharacter(array_json, pos) == ',') pos = _JsonSkipWs(array_json, pos + 1);
      if(pos >= len || (ushort)StringGetCharacter(array_json, pos) == ']') break;
      if((ushort)StringGetCharacter(array_json, pos) != '{') return false;
      int end_pos = -1;
      string item;
      if(!_JsonReadComposite(array_json, pos, '{', '}', item, end_pos)) return false;
      if(index == wanted_index){ out = item; return true; }
      index++;
      pos = end_pos + 1;
   }
   return false;
}

int JsonArrayObjectCount(const string array_json) {
   int count = 0;
   string item;
   while(JsonArrayGetObject(array_json, count, item)) count++;
   return count;
}

#endif
