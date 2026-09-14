"""The setup-signature decision cache must not re-read or rewrite its whole file.

The live ``ai_decision_cache.jsonl`` was 154 MB / 3,119 rows on 2026-09-14.
Every ``lookup`` read all of it and strictly parsed it newest-first until a
match -- on the usual miss, every row: 11.2 s per request on a fast desktop.
Every ``store`` read it again and rewrote it plus one row with an fsync, after
every AI reply.

These tests pin the replacement: an append-only file plus an in-memory byte
index extended from the appended bytes only, whose lookups act on exactly the
rows the old newest-first scan acted on.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
from architecture_contracts import strict_json_loads  # noqa: E402


def _row(signature: str, base: str, *, ts: int | None = None, marker: str = "", **extra) -> dict:
    row = {
        "timestamp": int(time.time()) if ts is None else ts,
        "signature": signature,
        "base_signature": base,
        "fields": {},
        "semantic_state": {},
        "decision": {"marker": marker},
    }
    row.update(extra)
    return row


def _line(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"


def _old_scan_candidates(path: Path, signature: str, base_signature: str) -> list[dict]:
    """The rows the pre-index ``lookup`` loop acted on, in its order."""

    acted: list[dict] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            item = strict_json_loads(line)
        except Exception:
            continue
        if item.get("signature") == signature:
            return [item]
        if item.get("base_signature") == base_signature:
            acted.append(item)
    return acted


def _new_candidates(cache: ai_gate.AIDecisionCache, signature: str, base_signature: str) -> list[dict]:
    errors: list[BaseException] = []
    cache._refresh_index_locked()
    items = list(cache._lookup_candidates_locked(signature, base_signature, errors))
    assert not errors, errors
    return items


class _TempCache:
    def __enter__(self) -> tuple[ai_gate.AIDecisionCache, Path]:
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "ai_decision_cache.jsonl"
        return ai_gate.AIDecisionCache(path, ttl_sec=1800), path

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


class IndexEquivalenceTests(unittest.TestCase):
    def _hostile_file(self, rng: random.Random) -> bytes:
        """Valid rows mixed with every kind of line the old reader skipped."""

        chunks: list[bytes] = []
        for index in range(160):
            kind = rng.random()
            signature = f"s{rng.randrange(6)}"
            base = f"b{rng.randrange(3)}"
            if kind < 0.60:
                chunks.append(_line(_row(signature, base, marker=f"row{index}")).encode("utf-8"))
            elif kind < 0.65:
                chunks.append(b"\n")
            elif kind < 0.70:
                chunks.append(b"{not json\n")
            elif kind < 0.74:
                # Valid for json.loads, refused by the strict reader (duplicate key).
                chunks.append(
                    f'{{"timestamp":1,"signature":"{signature}","base_signature":"{base}","signature":"{signature}"}}\n'.encode()
                )
            elif kind < 0.77:
                chunks.append(b"[1,2,3]\n")
            elif kind < 0.81:
                # A raw U+2028 inside a string: splitlines tore this row apart.
                chunks.append(_line(_row(signature, base, marker=f"sep{index} x")).encode("utf-8"))
            elif kind < 0.86:
                chunks.append(_line(_row(signature, base, marker=f"crlf{index}")).encode("utf-8")[:-1] + b"\r\n")
            elif kind < 0.92:
                # Keys not in store's order: indexed through the fallback parser.
                reordered = {"decision": {"marker": f"re{index}"}, "base_signature": base, "signature": signature, "timestamp": 5}
                chunks.append((json.dumps(reordered, separators=(",", ":")) + "\n").encode("utf-8"))
            else:
                escaped = {"timestamp": 7, "signature": 'q"' + signature, "base_signature": base}
                chunks.append((json.dumps(escaped, separators=(",", ":")) + "\n").encode("utf-8"))
        data = b"".join(chunks)
        if rng.random() < 0.5:
            data = b"\xef\xbb\xbf" + data
        if rng.random() < 0.5:
            data += _line(_row("s1", "b1", marker="torn")).encode("utf-8")[:-1]  # unterminated tail
        return data

    def test_lookup_acts_on_exactly_the_rows_the_full_scan_acted_on(self) -> None:
        for seed in range(25):
            rng = random.Random(seed)
            with _TempCache() as (cache, path):
                path.write_bytes(self._hostile_file(rng))
                keys = [f"s{i}" for i in range(7)] + ['q"s1']
                for signature in keys:
                    for base in ("b0", "b1", "b2", "b9"):
                        with self.subTest(seed=seed, signature=signature, base=base):
                            self.assertEqual(
                                _new_candidates(cache, signature, base),
                                _old_scan_candidates(path, signature, base),
                            )

    def test_equivalence_holds_while_the_file_grows(self) -> None:
        rng = random.Random(99)
        with _TempCache() as (cache, path):
            path.write_bytes(b"")
            for step in range(40):
                with path.open("ab") as handle:
                    handle.write(_line(_row(f"s{rng.randrange(4)}", f"b{rng.randrange(2)}", marker=str(step))).encode())
                    if step % 7 == 3:
                        handle.write(b'{"timestamp":1,"signature":"s1","base_')  # torn, completed below
                if step % 7 == 4:
                    with path.open("ab") as handle:
                        handle.write(b'signature":"b1"}\n')
                for signature in ("s0", "s1", "s2", "s3"):
                    for base in ("b0", "b1"):
                        with self.subTest(step=step, signature=signature, base=base):
                            self.assertEqual(
                                _new_candidates(cache, signature, base),
                                _old_scan_candidates(path, signature, base),
                            )


class IncrementalReadTests(unittest.TestCase):
    def test_only_appended_lines_are_indexed(self) -> None:
        with _TempCache() as (cache, path):
            path.write_text("".join(_line(_row(f"s{i}", "b0")) for i in range(500)), encoding="utf-8")
            indexed: list[int] = []
            original = cache._index_line_locked

            def spy(offset: int, raw: bytes) -> None:
                indexed.append(offset)
                original(offset, raw)

            cache._index_line_locked = spy  # type: ignore[method-assign]
            self.assertEqual(cache.lookup("absent", "absent", None), (None, "miss"))
            self.assertEqual(len(indexed), 500)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(_line(_row("s-new", "b-new", ts=1)))
            self.assertEqual(cache.lookup("s-new", "b-new", None), (None, "expired_ttl"))
            self.assertEqual(len(indexed), 501, "a lookup re-indexed rows it had already read")
            self.assertEqual(cache.lookup("still-absent", "b-new", None), (None, "invalidated_material_field_changed"))
            self.assertEqual(len(indexed), 501)

    def test_a_replaced_file_is_indexed_again(self) -> None:
        """The startup cache quarantine rewrites the file in place."""

        with _TempCache() as (cache, path):
            path.write_text(_line(_row("old", "b0", ts=1)) + _line(_row("keep", "b0", ts=1)), encoding="utf-8")
            self.assertEqual(cache.lookup("old", "b0", None), (None, "expired_ttl"))
            replacement = _line(_row("new", "b1", ts=1)) + _line(_row("keep", "b0", ts=1)) + _line(_row("x", "b2", ts=1))
            path.write_text(replacement, encoding="utf-8")
            self.assertEqual(cache.lookup("old", "b9", None), (None, "miss"))
            self.assertEqual(cache.lookup("new", "b1", None), (None, "expired_ttl"))
            self.assertGreaterEqual(cache.index_rebuilds, 1)


class AppendOnlyStoreTests(unittest.TestCase):
    def _store(self, cache: ai_gate.AIDecisionCache, signature: str) -> None:
        decision = ai_gate.Decision(allow=False, score=0.0)
        cache.store(signature, "base", {}, decision, request_identity_hash="rih")

    def test_store_appends_one_row_and_never_rewrites_the_existing_bytes(self) -> None:
        with _TempCache() as (cache, path):
            existing = "".join(_line(_row(f"s{i}", "b0", ts=1)) for i in range(50)).encode("utf-8")
            path.write_bytes(existing)
            self._store(cache, "stored-sig")
            data = path.read_bytes()
            self.assertTrue(data.startswith(existing), "store rewrote rows it did not own")
            appended = data[len(existing):].decode("utf-8")
            self.assertEqual(appended.count("\n"), 1)
            self.assertEqual(json.loads(appended)["signature"], "stored-sig")
            self.assertEqual(
                _new_candidates(cache, "stored-sig", "base"),
                _old_scan_candidates(path, "stored-sig", "base"),
            )
            self.assertEqual(len(_new_candidates(cache, "stored-sig", "base")), 1)

    def test_a_torn_last_line_cannot_swallow_the_next_row(self) -> None:
        with _TempCache() as (cache, path):
            path.write_bytes(_line(_row("a", "b0", ts=1)).encode() + b'{"timestamp":1,"signa')
            self._store(cache, "after-torn")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[2])["signature"], "after-torn")
            self.assertEqual(len(_new_candidates(cache, "after-torn", "base")), 1)

    def test_the_store_path_no_longer_reads_the_file(self) -> None:
        source = Path(ai_gate.__file__).read_text(encoding="utf-8")
        cls = source.index("class AIDecisionCache")
        start = source.index("    def store(", cls)
        body = source[start : source.index("AI_DECISION_CACHE = AIDecisionCache(", start)]
        self.assertNotIn("read_text(", body)
        self.assertNotIn("os.replace(", body)
        self.assertIn('self.path.open("a+b")', body)
        lookup = source[source.index("    def lookup(", cls) : start]
        self.assertNotIn("read_text(", lookup)
        self.assertNotIn("splitlines(", lookup)


if __name__ == "__main__":
    unittest.main()
