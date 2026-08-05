//+------------------------------------------------------------------+
//| FileBus.mqh - common-folder request/response bus                 |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_FILEBUS_MQH__
#define __PO3_AIGATE_FILEBUS_MQH__
#include "JsonLite.mqh"

class CFileBus {
private:
   string m_root;

   string LeafName(const string rel_path) const {
      string leaf = rel_path;
      int at = StringFind(leaf, "\\");
      while(at >= 0){
         leaf = StringSubstr(leaf, at + 1);
         at = StringFind(leaf, "\\");
      }
      return leaf;
   }

   string PathJoin(const string a, const string b) {
      if(StringLen(a)==0) return b;
      if(StringLen(b)==0) return a;
      string sep = "\\";
      if(StringSubstr(a, StringLen(a)-1, 1) == sep) return a + b;
      return a + sep + b;
   }

public:
   CFileBus(const string root="PO3_AI_BUS") { m_root = root; }

   string Root() const { return m_root; }

   string ReqDir()   const { return m_root + "\\requests"; }
   string RespDir()  const { return m_root + "\\responses"; }
   string StaleDir() const { return m_root + "\\stale"; }
   string LogDir()   const { return m_root + "\\logs"; }
   string ProcessingDir() const { return m_root + "\\processing"; }
   string CompletedDir() const { return m_root + "\\completed"; }
   string RejectedDir() const { return m_root + "\\rejected"; }
   string TimedOutDir() const { return m_root + "\\timed_out"; }
   string QuarantinedDir() const { return m_root + "\\quarantined"; }
   string ShutdownDir() const { return m_root + "\\shutdown"; }

   bool Ensure() {
      // all in Common/Files
      if(!FolderCreate(m_root, FILE_COMMON)) {}
      if(!FolderCreate(ReqDir(), FILE_COMMON)) {}
      if(!FolderCreate(RespDir(), FILE_COMMON)) {}
      if(!FolderCreate(StaleDir(), FILE_COMMON)) {}
      if(!FolderCreate(ProcessingDir(), FILE_COMMON)) {}
      if(!FolderCreate(CompletedDir(), FILE_COMMON)) {}
      if(!FolderCreate(RejectedDir(), FILE_COMMON)) {}
      if(!FolderCreate(TimedOutDir(), FILE_COMMON)) {}
      if(!FolderCreate(QuarantinedDir(), FILE_COMMON)) {}
      if(!FolderCreate(ShutdownDir(), FILE_COMMON)) {}
      if(!FolderCreate(LogDir(), FILE_COMMON)) {}
      if(!FolderCreate(m_root + "\\config", FILE_COMMON)) {}
      return true;
   }

   bool WriteText(const string rel_path, const string content) {
      string tmp_path = rel_path + ".tmp";
      int h = FileOpen(tmp_path, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE) return false;
      FileWriteString(h, content);
      FileClose(h);
      if(FileIsExist(rel_path, FILE_COMMON)) FileDelete(rel_path, FILE_COMMON);
      bool moved = FileMove(tmp_path, FILE_COMMON, rel_path, FILE_COMMON);
      if(!moved) FileDelete(tmp_path, FILE_COMMON);
      return moved;
   }

   // Decode UTF-16LE/BE (with or without BOM) and UTF-8 (with or without BOM).
   bool DecodeTextBytes(const uchar &bytes[], const int count, string &out) const {
      out = "";
      if(count <= 0) return true;

      bool utf16le = (count >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE);
      bool utf16be = (count >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF);
      int start = 0;
      if(utf16le || utf16be) start = 2;
      else if(count >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) start = 3;
      else if(count >= 2 && bytes[0] != 0 && bytes[1] == 0){
         // No BOM, but an ASCII first byte followed by a zero high byte is
         // UTF-16LE in practice for every writer in this system.
         utf16le = true;
      }

      if(utf16le || utf16be){
         int units = (count - start) / 2;
         if(units <= 0) return true;
         ushort chars[];
         if(ArrayResize(chars, units) != units) return false;
         for(int i = 0; i < units; i++){
            int b = start + i * 2;
            chars[i] = utf16le
                       ? (ushort)(bytes[b] | ((ushort)bytes[b + 1] << 8))
                       : (ushort)(bytes[b + 1] | ((ushort)bytes[b] << 8));
         }
         out = ShortArrayToString(chars, 0, units);
         return true;
      }

      out = CharArrayToString(bytes, start, count - start, CP_UTF8);
      return true;
   }

   // Encoding-aware reader.
   //
   // FILE_TXT without FILE_ANSI/FILE_UNICODE defaults to UTF-16 in MQL5. Python
   // writes bus *responses* as UTF-16 (AI_RESPONSE_ENCODING) but writes config,
   // policy, and deployment artifacts as UTF-8, and hand-authored config files
   // are UTF-8 as well. Reading a UTF-8 document under the UTF-16 default makes
   // the first "character" of `{"schema...` the value 0x227B instead of '{',
   // which surfaced as json_root_not_object on files Python considered
   // perfectly valid. Both sides must read the same bytes, so this reads binary
   // and decodes by BOM/heuristic exactly like Python's read_json_any_encoding.
   bool ReadText(const string rel_path, string &out) {
      out = "";
      int h = FileOpen(rel_path, FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE) return false;
      ulong size64 = FileSize(h);
      if(size64 == 0){ FileClose(h); return true; }
      if(size64 > 268435456){ FileClose(h); return false; }
      int size = (int)size64;
      uchar bytes[];
      if(ArrayResize(bytes, size) != size){ FileClose(h); return false; }
      uint got = FileReadArray(h, bytes, 0, size);
      FileClose(h);
      if((int)got != size) return false;
      return DecodeTextBytes(bytes, (int)got, out);
   }

   bool AppendText(const string rel_path, const string content) {
      int h = FileOpen(rel_path, FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE)
         h = FileOpen(rel_path, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE) return false;
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, content);
      FileClose(h);
      return true;
   }

   bool Delete(const string rel_path) {
      return FileDelete(rel_path, FILE_COMMON);
   }

   bool CopyToStale(const string rel_path) {
      return ArchiveTerminal(rel_path, "stale");
   }

   string ArtifactKind(const string rel_path) const {
      if(StringFind(rel_path, ReqDir() + "\\") == 0) return "request";
      if(StringFind(rel_path, RespDir() + "\\") == 0) return "response";
      if(StringFind(rel_path, ProcessingDir() + "\\") == 0) return "processing";
      return "artifact";
   }

   bool ArchiveTerminal(const string rel_path, const string terminal_state) {
      string target_dir = QuarantinedDir();
      if(terminal_state == "completed") target_dir = CompletedDir();
      else if(terminal_state == "rejected") target_dir = RejectedDir();
      else if(terminal_state == "timed_out") target_dir = TimedOutDir();
      else if(terminal_state == "stale") target_dir = StaleDir();
      else if(terminal_state == "shutdown") target_dir = ShutdownDir();
      else if(terminal_state != "quarantined") return false;
      string leaf = ArtifactKind(rel_path) + "__" + LeafName(rel_path);
      string dst = target_dir + "\\" + leaf;
      if(FileIsExist(dst, FILE_COMMON))
         dst = target_dir + "\\" + ArtifactKind(rel_path) + "__" + IntegerToString((int)GetTickCount()) + "__" + LeafName(rel_path);
      bool ok = FileCopy(rel_path, FILE_COMMON, dst, FILE_COMMON);
      if(ok) FileDelete(rel_path, FILE_COMMON);
      return ok;
   }

   // Find the first file that matches pattern in a directory (returns relative path)
   bool FindFirstInDir(const string dir_rel, const string pattern, string &out_rel_path) {
      long handle = FileFindFirst(dir_rel + "\\" + pattern, out_rel_path, FILE_COMMON);
      if(handle == INVALID_HANDLE) return false;
      // out_rel_path is filename only; return full rel path
      string fname = out_rel_path;
      FileFindClose(handle);
      out_rel_path = dir_rel + "\\" + fname;
      return true;
   }
};

#endif
