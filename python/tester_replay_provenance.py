"""Preserve recorded request provenance separately from replay-time measurements.

The candidate hash binds the immutable economic plan. The request execution
fingerprint additionally binds transient costs; it must continue to describe the
recorded request, even when a permitted live cost change occurs during replay.
No response, candidate identity, price or approval is rewritten here.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

REPLAY_PROVENANCE_SCHEMA = "20260907_recorded_request_costs_v1"
COST_FIELDS = ("spread_r", "slippage_r", "execution_cost_r", "net_reward_after_cost_r")


def build_replay_provenance(request: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("id") or "")
    if not request_id or request_id != str(response.get("id") or ""):
        raise ValueError("replay_provenance_request_id_mismatch")
    candidates = request.get("candidates") or []
    indexed = {}
    for candidate in candidates:
        key = str(candidate.get("candidate_hash") or "")
        if not key or key in indexed:
            raise ValueError("replay_provenance_candidate_hash_missing_or_duplicate")
        indexed[key] = candidate
    snapshots = []
    seen = set()
    for assessment in response.get("candidate_assessments") or []:
        key = str(assessment.get("candidate_hash") or "")
        candidate = indexed.get(key)
        if candidate is None or key in seen:
            raise ValueError("replay_provenance_assessment_not_uniquely_recorded")
        seen.add(key)
        for field in ("candidate_id", "candidate_hash", "request_execution_fingerprint"):
            if not candidate.get(field) or candidate.get(field) != assessment.get(field):
                raise ValueError(f"replay_provenance_{field}_mismatch")
        snapshot = {field: candidate[field] for field in ("candidate_id", "candidate_hash", "request_execution_fingerprint")}
        for field in COST_FIELDS:
            value = float(candidate[field])
            if not math.isfinite(value) or (field != "net_reward_after_cost_r" and value < 0):
                raise ValueError(f"replay_provenance_invalid_{field}")
            snapshot[field] = value
        snapshots.append(snapshot)
    if not snapshots or str(response.get("selected_candidate_hash") or "") not in seen:
        raise ValueError("replay_provenance_selected_candidate_not_recorded")
    return {"schema": REPLAY_PROVENANCE_SCHEMA, "request_id": request_id, "candidates": snapshots}
