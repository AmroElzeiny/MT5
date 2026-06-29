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

bool _JsonLocateKeyValue(const string json, const string key, int &value_pos) {
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

#endif
