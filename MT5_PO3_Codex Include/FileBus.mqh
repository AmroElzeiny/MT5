//+------------------------------------------------------------------+
//| FileBus.mqh - common-folder request/response bus                 |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_FILEBUS_MQH__
#define __PO3_AIGATE_FILEBUS_MQH__
#include "JsonLite.mqh"

class CFileBus {
private:
   string m_root;

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

   bool Ensure() {
      // all in Common/Files
      if(!FolderCreate(m_root, FILE_COMMON)) {}
      if(!FolderCreate(ReqDir(), FILE_COMMON)) {}
      if(!FolderCreate(RespDir(), FILE_COMMON)) {}
      if(!FolderCreate(StaleDir(), FILE_COMMON)) {}
      if(!FolderCreate(LogDir(), FILE_COMMON)) {}
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

   bool ReadText(const string rel_path, string &out) {
      int h = FileOpen(rel_path, FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
      if(h == INVALID_HANDLE) return false;
      out = "";
      bool first_line = true;
      while(!FileIsEnding(h)){
         string line = FileReadString(h);
         if(!first_line) out += "\n";
         out += line;
         first_line = false;
      }
      FileClose(h);
      return true;
   }

   bool Delete(const string rel_path) {
      return FileDelete(rel_path, FILE_COMMON);
   }

   bool CopyToStale(const string rel_path) {
      string fname = rel_path;
      int pos = StringFind(rel_path, "\\");
      // derive leaf
      for(int i=0;i<10;i++){
         int p2 = StringFind(fname, "\\");
         if(p2<0) break;
         fname = StringSubstr(fname, p2+1);
      }
      string dst = StaleDir() + "\\" + fname;
      // best effort: copy then delete
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
