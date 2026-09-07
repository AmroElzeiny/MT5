from __future__ import annotations
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import FactoryConfig
from .models import CodeSnippet
from .utils import atomic_write_json, sha256_file, tokenize_query, utc_now


class CodeDiscovery:
    def __init__(self, config: FactoryConfig):
        self.config = config

    def build_index(self) -> dict[str, Any]:
        files = []
        root = self.config.repo_root
        excludes = set(self.config.source_exclude_dirs)
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in self.config.source_extensions:
                continue
            rel = path.relative_to(root)
            if any(part in excludes for part in rel.parts):
                continue
            if path.stat().st_size > self.config.max_scan_file_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            symbols = sorted(set(re.findall(r"(?:class|def|void|bool|int|double|string)\s+([A-Za-z_][A-Za-z0-9_]*)", text)))[:200]
            files.append({"path": rel.as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size, "symbols": symbols})
        payload = {"generated_at": utc_now(), "repo_root": str(root), "file_count": len(files), "files": files}
        atomic_write_json(self.config.source_index_path, payload)
        return payload

    def load_index(self) -> dict[str, Any]:
        if not self.config.source_index_path.exists():
            return self.build_index()
        try:
            return json.loads(self.config.source_index_path.read_text(encoding="utf-8"))
        except Exception:
            return self.build_index()

    def search(self, text: str, *, extra_terms: list[str] | None = None) -> list[CodeSnippet]:
        terms = tokenize_query(text)
        for term in extra_terms or []:
            if term and term not in terms:
                terms.append(term)
        terms = terms[:20]
        if not terms or self.config.max_code_snippets <= 0:
            return []
        matches = self._ripgrep(terms) if shutil.which("rg") else self._python_search(terms)
        snippets = []
        seen = set()
        for path, line_no, matched in matches:
            key = (str(path), line_no)
            if key in seen: continue
            seen.add(key)
            snippet = self._snippet(path, line_no, matched)
            if snippet:
                snippets.append(snippet)
            if len(snippets) >= self.config.max_code_snippets:
                break
        return snippets

    def _eligible(self, path: Path) -> bool:
        try: rel = path.relative_to(self.config.repo_root)
        except ValueError: return False
        if path.suffix.lower() not in self.config.source_extensions: return False
        if any(part in set(self.config.source_exclude_dirs) for part in rel.parts): return False
        return path.is_file() and path.stat().st_size <= self.config.max_scan_file_bytes

    def _ripgrep(self, terms: list[str]) -> list[tuple[Path,int,list[str]]]:
        pattern = "|".join(re.escape(x) for x in terms)
        try:
            proc = subprocess.run(["rg","-n","-i","--no-heading","--color","never",pattern,str(self.config.repo_root)], capture_output=True, text=True, timeout=15)
        except Exception:
            return self._python_search(terms)
        out = []
        for line in proc.stdout.splitlines():
            try:
                raw_path, raw_no, content = line.split(":",2); path = Path(raw_path); no = int(raw_no)
            except ValueError: continue
            if not self._eligible(path): continue
            found = [t for t in terms if t.lower() in content.lower()]
            out.append((path,no,found))
        return out

    def _python_search(self, terms: list[str]) -> list[tuple[Path,int,list[str]]]:
        out = []
        for path in self.config.repo_root.rglob("*"):
            if not self._eligible(path): continue
            try: lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError: continue
            for i, line in enumerate(lines, start=1):
                found = [t for t in terms if t.lower() in line.lower()]
                if found: out.append((path,i,found))
                if len(out) >= self.config.max_code_snippets * 12: return out
        return out

    def _snippet(self, path: Path, line_no: int, terms: list[str]) -> CodeSnippet | None:
        try: lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError: return None
        radius = max(10, self.config.max_code_snippet_lines // 2)
        start = max(1, line_no-radius); end = min(len(lines), start+self.config.max_code_snippet_lines-1)
        numbered = "\n".join(f"{i:05d}: {lines[i-1]}" for i in range(start,end+1))
        return CodeSnippet(path.relative_to(self.config.repo_root).as_posix(), start, end, terms, numbered, sha256_file(path))
