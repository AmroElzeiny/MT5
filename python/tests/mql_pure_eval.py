"""Execute a pure MQL5 function body from the real source file.

Test-only.  The translated code is the production MQL text itself, not a
Python re-implementation, so a change to the MQL arithmetic or guard order
changes what these tests execute.  Only the deliberately small subset used by
the pure helpers is accepted -- declarations, assignments, ``if`` with a single
statement or a braced block, ``return``, ``&&``/``||``/``!``, ``MathMin``,
``MathMax``, ``MathAbs`` and numeric casts.  Anything else raises, so a helper
that grows beyond the subset fails loudly instead of being mistranslated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_MQL_INCLUDE = REPO_ROOT / "MT5_PO3_Codex Include"

_TYPES = ("double", "int", "long", "bool", "string", "datetime", "uint")


def read_repo_mql(name: str) -> str:
    return (REPO_MQL_INCLUDE / name).read_text(encoding="utf-8-sig")


def mql_constants(source: str) -> dict[str, Any]:
    constants: dict[str, Any] = {}
    for match in re.finditer(r"^\s*const\s+(double|int|long)\s+(\w+)\s*=\s*([-0-9.eE+]+)\s*;", source, re.M):
        kind, name, value = match.groups()
        constants[name] = float(value) if kind == "double" else int(value)
    return constants


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def function_body(source: str, name: str) -> tuple[str, str]:
    """Return (parameter list, body) of a top-level or member MQL function."""

    signature = re.search(rf"^\s*[\w\s\*&]*\b{re.escape(name)}\s*\(", source, re.M)
    if signature is None:
        raise AssertionError(f"MQL function not found: {name}")
    depth = 1
    index = signature.end()
    while depth:
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        index += 1
    params = source[signature.end(): index - 1]
    opening = source.index("{", index)
    depth = 0
    cursor = opening
    in_string = False
    while True:
        char = source[cursor]
        if in_string:
            if char == "\\":
                cursor += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif source.startswith("//", cursor):
            cursor = source.index("\n", cursor)
            continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return params, source[opening + 1: cursor]
        cursor += 1


def _expr(text: str, rename: dict[str, str]) -> str:
    out = text.strip()
    out = re.sub(r"\((?:long|int|double|datetime|uint)\)", "", out)
    out = out.replace("&&", " and ").replace("||", " or ")
    out = re.sub(r"!(?!=)", " not ", out)
    out = re.sub(r"\bMathMin\b", "min", out)
    out = re.sub(r"\bMathMax\b", "max", out)
    out = re.sub(r"\bMathAbs\b", "abs", out)
    out = re.sub(r"\btrue\b", "True", out)
    out = re.sub(r"\bfalse\b", "False", out)
    for old, new in rename.items():
        out = re.sub(rf"\b{re.escape(old)}\b", new, out)
    if re.search(r"[?{}\[\]]|\bStringLen\b|\bJson", out):
        raise ValueError(f"expression outside the pure subset: {text!r}")
    return out


class _Parser:
    def __init__(self, body: str, rename: dict[str, str]) -> None:
        self.text = _strip_comments(body)
        self.pos = 0
        self.rename = rename

    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _balanced_parens(self) -> str:
        assert self.text[self.pos] == "("
        depth = 0
        start = self.pos
        while True:
            char = self.text[self.pos]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    self.pos += 1
                    return self.text[start + 1: self.pos - 1]
            self.pos += 1

    def block(self, indent: int, until_brace: bool) -> list[str]:
        lines: list[str] = []
        while True:
            self._skip_ws()
            if self.pos >= len(self.text):
                if until_brace:
                    raise ValueError("unterminated block")
                return lines
            if self.text[self.pos] == "}":
                if not until_brace:
                    raise ValueError("unexpected closing brace")
                self.pos += 1
                return lines
            lines.extend(self.statement(indent))

    def statement(self, indent: int) -> list[str]:
        pad = "    " * indent
        self._skip_ws()
        if re.match(r"if\s*\(", self.text[self.pos:]):
            self.pos = self.text.index("(", self.pos)
            cond = _expr(self._balanced_parens(), self.rename)
            self._skip_ws()
            if self.text[self.pos] == "{":
                self.pos += 1
                inner = self.block(indent + 1, until_brace=True)
            else:
                inner = self.statement(indent + 1)
            lines = [f"{pad}if {cond}:"] + (inner or [f"{pad}    pass"])
            self._skip_ws()
            if self.text.startswith("else", self.pos):
                self.pos += 4
                self._skip_ws()
                if self.text[self.pos] == "{":
                    self.pos += 1
                    other = self.block(indent + 1, until_brace=True)
                else:
                    other = self.statement(indent + 1)
                lines += [f"{pad}else:"] + (other or [f"{pad}    pass"])
            return lines
        end = self.text.index(";", self.pos)
        raw = self.text[self.pos:end].strip()
        self.pos = end + 1
        decl = re.match(rf"^(?:const\s+)?(?:{'|'.join(_TYPES)})\s+(\w+)\s*=\s*(.+)$", raw, re.S)
        if decl:
            name, value = decl.groups()
            target = self.rename.get(name, name)
            return [f"{pad}{target} = {_expr(value, self.rename)}"]
        ret = re.match(r"^return\s+(.+)$", raw, re.S)
        if ret:
            return [f"{pad}return ({_expr(ret.group(1), self.rename)}), out"]
        assign = re.match(r"^(\w+)\s*=\s*(.+)$", raw, re.S)
        if assign:
            name, value = assign.groups()
            target = self.rename.get(name, name)
            return [f"{pad}{target} = {_expr(value, self.rename)}"]
        raise ValueError(f"statement outside the pure subset: {raw!r}")


def load_mql_function(source: str, name: str, constants: dict[str, Any]) -> Callable[..., tuple[Any, dict[str, Any]]]:
    params_text, body = function_body(source, name)
    inputs: list[str] = []
    outputs: list[str] = []
    for raw in [part.strip() for part in params_text.split(",") if part.strip()]:
        param = re.match(r"^(?:const\s+)?\w+\s*(&)?\s*(\w+)$", raw)
        if not param:
            raise ValueError(f"parameter outside the pure subset: {raw!r}")
        (outputs if param.group(1) else inputs).append(param.group(2))
    rename = {out_name: f"out[{out_name!r}]" for out_name in outputs}
    lines = _Parser(body, rename).block(1, until_brace=False)
    source_py = "\n".join(
        [f"def {name}({', '.join(inputs)}):", f"    out = {{{', '.join(repr(o) + ': None' for o in outputs)}}}"]
        + lines
    )
    namespace: dict[str, Any] = dict(constants)
    exec(compile(source_py, f"<mql:{name}>", "exec"), namespace)  # noqa: S102 - test-only executor
    return namespace[name]
