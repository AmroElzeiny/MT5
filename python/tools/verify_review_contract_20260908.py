"""Replay the 35 demoted 2026-09-08 requests through the repaired review contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate
from decision_evidence import build_decision_evidence_envelope
from evidence_catalog import build_evidence_catalog


CLASSIFICATION = ROOT / "docs" / "audit_scalp_20260908" / "lost_approvals_technical_classification.json"
BUS = Path(r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS")
OUT = ROOT / "docs" / "audit_scalp_20260908" / "review_contract_verification.json"
INTERNAL_FIELDS = {
    "candidate_id",
    "candidate_hash",
    "request_execution_fingerprint",
    "assessed_execution_fingerprint",
}


def _load(path: Path) -> dict:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"unreadable_json:{path}")


def _request_path(request_id: str) -> Path | None:
    for folder in ("rejected", "shutdown", "quarantined", "timed_out", "stale"):
        matches = list((BUS / folder).glob(f"*{request_id}.json"))
        if matches:
            return matches[0]
    return None


def main() -> int:
    source = _load(CLASSIFICATION)
    decisions = source.get("decisions") if isinstance(source, dict) else source
    rows: list[dict] = []
    totals = {
        "expected_requests": len(decisions or []),
        "found_requests": 0,
        "valid_envelopes": 0,
        "empty_or_inconsistent_asset_class": 0,
        "provider_identity_exposures": 0,
        "ambiguous_raw_target_label_exposures": 0,
        "missing_target_semantics": 0,
        "unsatisfied_approval_event_contracts": 0,
    }
    for decision in decisions or []:
        request_id = str(decision.get("request_id") or "")
        path = _request_path(request_id)
        if path is None:
            rows.append({"request_id": request_id, "found": False})
            continue
        totals["found_requests"] += 1
        payload = _load(path)
        result = build_decision_evidence_envelope(payload)
        if result.valid:
            totals["valid_envelopes"] += 1
        catalog = build_evidence_catalog(result.envelope)
        compact = ai_gate._compact_model_evidence_payload(result.envelope, catalog)
        instrument = result.envelope.get("instrument") or {}
        asset_bad = not instrument.get("asset_class") or not instrument.get("asset_class_consistent")
        totals["empty_or_inconsistent_asset_class"] += int(asset_bad)
        provider_rows = compact.get("entry_and_invalidation", {}).get("candidates", [])
        identity_exposures = sum(
            1 for row in provider_rows for field in INTERNAL_FIELDS if field in row
        )
        totals["provider_identity_exposures"] += identity_exposures
        ambiguous = sum(
            1
            for row in provider_rows
            for field in ("target_source", "target_model", "tp_model", "target_candidates")
            if field in row
        )
        totals["ambiguous_raw_target_label_exposures"] += ambiguous
        missing_target = sum(
            1
            for row in provider_rows
            if not isinstance(row.get("target_semantics"), dict)
            or not isinstance(row.get("target_choice_menu"), dict)
        )
        totals["missing_target_semantics"] += missing_target
        unsatisfied = sum(
            1
            for row in provider_rows
            if not bool((row.get("family_event_evidence") or {}).get("approval_contract_satisfied"))
        )
        totals["unsatisfied_approval_event_contracts"] += unsatisfied
        rows.append(
            {
                "request_id": request_id,
                "found": True,
                "source_path": str(path),
                "envelope_valid": result.valid,
                "asset_class": instrument.get("asset_class"),
                "asset_class_consistent": instrument.get("asset_class_consistent"),
                "candidate_count": len(provider_rows),
                "identity_exposures": identity_exposures,
                "ambiguous_target_label_exposures": ambiguous,
                "missing_target_semantics": missing_target,
                "unsatisfied_approval_event_contracts": unsatisfied,
            }
        )
    totals["technical_contract_clean"] = all(
        totals[name] == 0
        for name in (
            "empty_or_inconsistent_asset_class",
            "provider_identity_exposures",
            "ambiguous_raw_target_label_exposures",
            "missing_target_semantics",
            "unsatisfied_approval_event_contracts",
        )
    ) and totals["found_requests"] == totals["expected_requests"]
    OUT.write_text(
        json.dumps({"summary": totals, "requests": rows}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(totals, indent=2))
    return 0 if totals["technical_contract_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
