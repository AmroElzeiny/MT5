"""The gate must stop re-reading the whole shadow ledger for every request.

The EA appends to ``shadow_candidates.jsonl`` on every scan, decision and price
station, so the old "re-parse when size or mtime changed" rule re-read the whole
file -- 220 MB of UTF-16, 31,190 events, 2.2 s and a 732 MB allocation peak on a
fast desktop -- for nearly every request.

``ShadowLedgerTail`` reads only the bytes appended since its last refresh.  The
tests below pin that its view is identical to ``read_shadow_events`` on the same
bytes after every append, and that it really reads only the new bytes.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
from shadow_outcome_ledger import (  # noqa: E402
    SHADOW_LEDGER_SCHEMA_VERSION,
    ShadowLedgerTail,
    consolidate_shadow_lifecycle,
    read_shadow_events,
)


BOM = "﻿"


def _event(index: int, rng: random.Random) -> str:
    variant = f"v{rng.randrange(8)}"
    kind = rng.choice(
        [
            "shadow_candidate_observed",
            "shadow_decision_recorded",
            "shadow_terminal_resolution",
            "shadow_path_progress",
            "candidate_observed",  # legacy vocabulary
        ]
    )
    row = {
        "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        "event_type": kind,
        "event_at": 1_789_000_000 + rng.randrange(5_000),
        "candidate_variant_id": variant,
        "sweep_opportunity_id": "o1",
        "note": rng.choice(["plain", "ünïcødé", "emoji \U0001f600", "raw line separator   inside", "cr\\r"]),
        "i": index,
    }
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def _document(rng: random.Random, count: int) -> str:
    parts: list[str] = []
    for index in range(count):
        roll = rng.random()
        if roll < 0.06:
            parts.append("\n")
        elif roll < 0.10:
            parts.append("{broken json\n")
        elif roll < 0.13:
            parts.append("[1,2]\n")
        elif roll < 0.17:
            parts.append(_event(index, rng) + "\r\n")
        else:
            parts.append(_event(index, rng) + "\n")
    return "".join(parts)


def _as_read(read) -> tuple:
    return (read.rows, read.unparseable, read.legacy_rows, read.total_lines)


class TailEquivalenceTests(unittest.TestCase):
    ENCODINGS = (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"), ("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"))

    def test_the_tail_view_equals_a_full_read_after_every_append(self) -> None:
        for seed in range(12):
            for encoding, bom in self.ENCODINGS:
                rng = random.Random(seed)
                text = _document(rng, 120)
                # Cut points at character boundaries, including inside lines, so
                # a refresh regularly lands on an unterminated line.
                cuts = sorted(rng.sample(range(1, len(text)), 25)) + [len(text)]
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "shadow_candidates.jsonl"
                    path.write_bytes(bom)
                    tail = ShadowLedgerTail(path)
                    previous = 0
                    for cut in cuts:
                        with path.open("ab") as handle:
                            handle.write(text[previous:cut].encode(encoding))
                        previous = cut
                        with self.subTest(seed=seed, encoding=encoding, bom=bom, cut=cut):
                            view, _changed = tail.refresh()
                            self.assertEqual(_as_read(view), _as_read(read_shadow_events(path)))

    def test_consolidation_over_the_tail_view_is_the_full_consolidation(self) -> None:
        rng = random.Random(7)
        text = _document(rng, 400)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_candidates.jsonl"
            path.write_bytes(b"\xff\xfe")
            tail = ShadowLedgerTail(path)
            for start in range(0, len(text), 997):
                with path.open("ab") as handle:
                    handle.write(text[start : start + 997].encode("utf-16-le"))
                view, _ = tail.refresh()
            full = consolidate_shadow_lifecycle(read_shadow_events(path).rows)
            incremental = consolidate_shadow_lifecycle(view.rows)
            self.assertEqual(
                [record.as_dict() for record in incremental.variants],
                [record.as_dict() for record in full.variants],
            )
            self.assertEqual(incremental.rejected, full.rejected)
            self.assertEqual(incremental.conflicts, full.conflicts)


class IncrementalReadTests(unittest.TestCase):
    def test_a_refresh_reads_only_the_appended_bytes(self) -> None:
        rng = random.Random(3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_candidates.jsonl"
            initial = (BOM + _document(rng, 2_000)).encode("utf-16-le")
            path.write_bytes(initial)
            tail = ShadowLedgerTail(path)
            _, changed = tail.refresh()
            self.assertTrue(changed)
            self.assertEqual(tail.full_reads, 1)
            self.assertEqual(tail.last_bytes_read, len(initial))

            _, changed = tail.refresh()
            self.assertFalse(changed, "nothing was appended")
            self.assertEqual(tail.last_bytes_read, 0)

            appended = (_event(1, rng) + "\n").encode("utf-16-le")
            with path.open("ab") as handle:
                handle.write(appended)
            view, changed = tail.refresh()
            self.assertTrue(changed)
            self.assertEqual(tail.full_reads, 1, "an append must not trigger a full re-read")
            self.assertEqual(tail.last_bytes_read, len(appended))
            self.assertEqual(_as_read(view), _as_read(read_shadow_events(path)))

    def test_a_truncated_or_replaced_file_is_read_again_from_the_start(self) -> None:
        rng = random.Random(4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_candidates.jsonl"
            path.write_bytes((BOM + _document(rng, 60)).encode("utf-16-le"))
            tail = ShadowLedgerTail(path)
            tail.refresh()
            path.write_bytes((BOM + _document(rng, 20)).encode("utf-16-le"))  # shorter
            view, changed = tail.refresh()
            self.assertTrue(changed)
            self.assertEqual(_as_read(view), _as_read(read_shadow_events(path)))
            path.write_bytes((BOM + _document(random.Random(5), 90)).encode("utf-16-le"))  # longer, different
            view, changed = tail.refresh()
            self.assertTrue(changed)
            self.assertEqual(_as_read(view), _as_read(read_shadow_events(path)))
            self.assertEqual(tail.full_reads, 3)

    def test_a_bomless_utf16_ledger_keeps_the_full_readers_decoding(self) -> None:
        rng = random.Random(6)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_candidates.jsonl"
            path.write_bytes(_document(rng, 30).encode("utf-16-le"))
            view, _ = ShadowLedgerTail(path).refresh()
            self.assertEqual(_as_read(view), _as_read(read_shadow_events(path)))


class GateRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        ai_gate._SHADOW_LEDGER_CACHE.clear()
        self.addCleanup(ai_gate._SHADOW_LEDGER_CACHE.clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "shadow_candidates.jsonl"
        patcher = mock.patch.dict(os.environ, {"PO3_SHADOW_LEDGER_PATH": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _observation(variant: str) -> str:
        return json.dumps(
            {
                "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
                "event_type": "shadow_candidate_observed",
                "event_at": 1_789_000_000,
                "candidate_variant_id": variant,
                "sweep_opportunity_id": "o1",
            },
            separators=(",", ":"),
        ) + "\n"

    def _append(self, variant: str) -> None:
        with self.path.open("ab") as handle:
            if handle.tell() == 0:
                handle.write(b"\xff\xfe")
            handle.write(self._observation(variant).encode("utf-16-le"))

    def test_new_events_are_picked_up_without_a_refresh_floor(self) -> None:
        self._append("v1")
        with mock.patch.dict(os.environ, {"AI_SHADOW_EVIDENCE_REFRESH_SEC": "0"}), mock.patch.object(ai_gate, "log"):
            self.assertEqual([r.candidate_variant_id for r in ai_gate._shadow_outcome_records()], ["v1"])
            self._append("v2")
            self.assertEqual(
                sorted(r.candidate_variant_id for r in ai_gate._shadow_outcome_records()), ["v1", "v2"]
            )
            tail = ai_gate._SHADOW_LEDGER_CACHE["tail"]
            self.assertEqual(tail.full_reads, 1, "the gate re-read the whole ledger for a request")

    def test_the_refresh_floor_serves_the_last_consolidation(self) -> None:
        self._append("v1")
        with mock.patch.dict(os.environ, {"AI_SHADOW_EVIDENCE_REFRESH_SEC": "600"}), mock.patch.object(ai_gate, "log"):
            first = ai_gate._shadow_outcome_records()
            self._append("v2")
            self.assertIs(ai_gate._shadow_outcome_records(), first)
            ai_gate._SHADOW_LEDGER_CACHE["refreshed_at"] = 0.0  # the floor elapsed
            self.assertEqual(len(ai_gate._shadow_outcome_records()), 2)

    def test_the_refresh_floor_setting_is_bounded(self) -> None:
        for raw, expected in (("", 120.0), ("0", 0.0), ("-5", 0.0), ("abc", 120.0), ("nan", 120.0), ("99999", 3600.0)):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"AI_SHADOW_EVIDENCE_REFRESH_SEC": raw}):
                self.assertEqual(ai_gate._shadow_evidence_refresh_sec(), expected)


if __name__ == "__main__":
    unittest.main()
