"""Provider-grade runtime governance contracts for the PO3/FVG system.

This module deliberately keeps research evidence separate from trading authority.
It provides deterministic, dependency-free primitives used by the Python bridge,
offline analytics, and regression tests. MQL mirrors the live execution gates.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


MODULE_ROOT = Path(__file__).resolve().parent

RUNTIME_GOVERNANCE_VERSION = "20260717_repeatability_priors_risk_v1"
REPEATABILITY_SCHEMA_VERSION = "20260717_repeatability_v1"
HIERARCHICAL_PRIOR_SCHEMA_VERSION = "20260717_hierarchical_prior_v1"
RISK_FACTOR_SCHEMA_VERSION = "20260717_risk_factor_v1"
COMMISSION_MODEL_SCHEMA_VERSION = "20260717_broker_cost_v1"
MANAGEMENT_SCHEMA_VERSION = "20260717_management_state_v3"
MANAGEMENT_EXPERIMENT_SCHEMA_VERSION = "20260717_management_experiment_v1"
MANAGEMENT_COUNTERFACTUAL_SCHEMA_VERSION = "20260717_management_counterfactual_v2"
INVALIDATION_POLICY_SCHEMA_VERSION = "20260717_invalidation_asset_class_v1"
SHADOW_CANDIDATE_SCHEMA_VERSION = "20260717_shadow_candidate_v3"
NORMALIZED_FVG_SCHEMA_VERSION = "20260717_normalized_fvg_v2"

REPEATABLE = "REPEATABLE"
SCORE_NON_REPEATABLE = "SCORE_NON_REPEATABLE"
DECISION_NON_REPEATABLE = "DECISION_NON_REPEATABLE"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
UNAVAILABLE = "UNAVAILABLE"

MANAGEMENT_HEALTHY = "HEALTHY"
MANAGEMENT_WARNING = "WARNING"
MANAGEMENT_THESIS_INVALID = "THESIS_INVALID"
MANAGEMENT_EXITED = "EXITED"

FVG_MODE_OFF = "OFF"
FVG_MODE_SHADOW = "SHADOW"
FVG_MODE_ENFORCE = "ENFORCE"


def resolve_project_path(value: str | os.PathLike[str] | Path) -> Path:
    """Resolve repository configuration independently of the process CWD."""

    path = Path(value).expanduser()
    return path if path.is_absolute() else (MODULE_ROOT / path).resolve()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except Exception:
            pass


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(value) + "\n")
        handle.flush()


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _text(value: Any, default: str = "unknown") -> str:
    result = str(value or "").strip()
    return result if result else default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_fingerprint(
    payload: Mapping[str, Any],
    *,
    model: str,
    reasoning_effort: str,
    decision_quality_tier: str,
    prompt_contract_version: str,
    decision_schema_version: str,
    target_schema_version: str,
    prior_artifact_hash: str,
    prior_artifact_version: str,
) -> dict[str, Any]:
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    candidate_contracts = [
        {
            "candidate_index": int(candidate.get("candidate_index", index)),
            "candidate_id": _text(candidate.get("candidate_id"), ""),
            "candidate_hash": _text(candidate.get("candidate_hash"), ""),
            "request_execution_fingerprint": _text(candidate.get("request_execution_fingerprint"), ""),
        }
        for index, candidate in enumerate(candidates)
        if isinstance(candidate, Mapping)
    ]
    canonical_payload_hash = canonical_hash(payload)
    contract = {
        "request_id": _text(payload.get("id"), ""),
        "session_id": _text(payload.get("session_id"), ""),
        "candidate_contracts": candidate_contracts,
        "candidate_order": [item["candidate_index"] for item in candidate_contracts],
        "runtime_input_hash": _text(payload.get("runtime_input_hash"), ""),
        "prompt_contract_version": prompt_contract_version,
        "decision_schema_version": decision_schema_version,
        "target_schema_version": target_schema_version,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "decision_quality_tier": decision_quality_tier,
        "prior_artifact_hash": prior_artifact_hash,
        "prior_artifact_version": prior_artifact_version,
        "canonical_payload_hash": canonical_payload_hash,
        "fingerprint_schema_version": REPEATABILITY_SCHEMA_VERSION,
    }
    contract["request_fingerprint"] = canonical_hash(contract)
    return contract


def response_fingerprint(response: Mapping[str, Any]) -> dict[str, Any]:
    assessments = response.get("candidate_assessments")
    if not isinstance(assessments, list):
        assessments = []
    contract = {
        "response_id": _text(response.get("response_id") or response.get("id"), ""),
        "model_returned": _text(response.get("model_returned") or response.get("model_version"), ""),
        "decision_quality_tier": _text(response.get("decision_quality_tier"), ""),
        "decision_state": _text(response.get("decision_state"), ""),
        "candidate_assessments": assessments,
        "selected_candidate_id": _text(response.get("selected_candidate_id"), ""),
        "selected_candidate_hash": _text(response.get("selected_candidate_hash"), ""),
        "chosen_index": int(_finite(response.get("chosen_index"), -1)),
        "veto_enabled": _bool(response.get("veto_enabled")),
        "veto_reason": _text(response.get("veto_reason"), ""),
        "llm_quality_score": _finite(response.get("llm_quality_score")),
        "suggested_risk_multiplier": _finite(response.get("suggested_risk_multiplier")),
        "target_arbitration": response.get("target_arbitration")
        if isinstance(response.get("target_arbitration"), Mapping)
        else {},
        "fingerprint_schema_version": REPEATABILITY_SCHEMA_VERSION,
    }
    contract["full_structured_response_hash"] = canonical_hash(response)
    contract["response_fingerprint"] = canonical_hash(contract)
    return contract


@dataclass(frozen=True)
class RepeatabilityThresholds:
    minimum_evaluated_candidates: int = 30
    maximum_score_stddev: float = 0.75
    minimum_decision_agreement: float = 0.90
    minimum_chosen_candidate_agreement: float = 0.90
    minimum_veto_agreement: float = 0.90
    minimum_target_choice_agreement: float = 0.85


def _mode_agreement(values: Sequence[Any]) -> float:
    if not values:
        return 0.0
    counts: dict[str, int] = {}
    for value in values:
        key = canonical_json(value)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(values)


def evaluate_repeatability(
    responses: Sequence[Mapping[str, Any]],
    *,
    model: str,
    prompt_contract_version: str,
    decision_schema_version: str,
    target_schema_version: str,
    decision_quality_tier: str,
    thresholds: RepeatabilityThresholds,
) -> dict[str, Any]:
    scores = [_finite(row.get("llm_quality_score")) for row in responses]
    decisions = [_text(row.get("decision_state"), "") for row in responses]
    chosen = [_text(row.get("selected_candidate_hash"), "") for row in responses]
    vetoes = [_bool(row.get("veto_enabled")) for row in responses]
    risks = [_finite(row.get("suggested_risk_multiplier")) for row in responses]
    target_choices = [
        _text(
            row.get("chosen_target_model")
            or (row.get("target_arbitration") or {}).get("chosen_target_model")
            if isinstance(row.get("target_arbitration"), Mapping)
            else row.get("chosen_target_model"),
            "",
        )
        for row in responses
    ]
    response_hashes = [response_fingerprint(row)["response_fingerprint"] for row in responses]
    metrics = {
        "llm_quality_score_stddev": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        "decision_agreement_rate": _mode_agreement(decisions),
        "chosen_candidate_agreement_rate": _mode_agreement(chosen),
        "veto_agreement_rate": _mode_agreement(vetoes),
        "risk_multiplier_stddev": statistics.pstdev(risks) if len(risks) > 1 else 0.0,
        "target_choice_agreement_rate": _mode_agreement(target_choices),
        "response_fingerprint_uniqueness": len(set(response_hashes)) / len(response_hashes) if responses else 0.0,
    }
    evaluated = len(responses)
    score_repeatable = metrics["llm_quality_score_stddev"] <= thresholds.maximum_score_stddev
    decision_repeatable = all(
        (
            metrics["decision_agreement_rate"] >= thresholds.minimum_decision_agreement,
            metrics["chosen_candidate_agreement_rate"] >= thresholds.minimum_chosen_candidate_agreement,
            metrics["veto_agreement_rate"] >= thresholds.minimum_veto_agreement,
            metrics["target_choice_agreement_rate"] >= thresholds.minimum_target_choice_agreement,
        )
    )
    if evaluated <= 0:
        status = UNAVAILABLE
    elif evaluated < thresholds.minimum_evaluated_candidates:
        status = INSUFFICIENT_SAMPLE
    elif not decision_repeatable:
        status = DECISION_NON_REPEATABLE
    elif not score_repeatable:
        status = SCORE_NON_REPEATABLE
    else:
        status = REPEATABLE
    artifact = {
        "schema_version": REPEATABILITY_SCHEMA_VERSION,
        "model": model,
        "prompt_contract_version": prompt_contract_version,
        "decision_schema_version": decision_schema_version,
        "target_schema_version": target_schema_version,
        "decision_quality_tier": decision_quality_tier,
        "evaluated_responses": evaluated,
        "thresholds": asdict(thresholds),
        "metrics": metrics,
        "status": status,
        "score_threshold_authority": status == REPEATABLE,
        "trading_eligible": status not in {SCORE_NON_REPEATABLE, DECISION_NON_REPEATABLE},
        "generated_at": _utc_now(),
    }
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact


def repeatability_authority(artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    status = _text((artifact or {}).get("status"), UNAVAILABLE)
    return {
        "status": status,
        "score_threshold_authority": status == REPEATABLE,
        "trading_eligible": status not in {SCORE_NON_REPEATABLE, DECISION_NON_REPEATABLE},
        "reason": "repeatability_decision_or_veto_failed" if status == DECISION_NON_REPEATABLE else "",
    }


def _clean_record(row: Mapping[str, Any]) -> bool:
    integrity = _text(row.get("ledger_integrity_status"), "").lower()
    attribution = _text(row.get("attribution_status"), "").lower()
    schema_ok = bool(row.get("ledger_schema_version")) and bool(row.get("decision_schema_version"))
    eligible = all(
        _bool(row.get(name))
        for name in ("learning_eligible", "optimization_eligible", "suppression_eligible")
    )
    return (
        integrity in {"clean", "verified_clean", "reconciled_clean"}
        and attribution in {"verified", "exact", "exact_verified"}
        and not _bool(row.get("execution_identity_quarantined"))
        and schema_ok
        and eligible
    )


def infer_asset_class(symbol: str, explicit: str = "") -> str:
    if explicit:
        return explicit.strip().lower()
    value = symbol.upper()
    if any(token in value for token in ("XAU", "GOLD", "XAG", "SILVER")):
        return "metals"
    if any(token in value for token in ("WTI", "BRENT", "OIL", "NGAS")):
        return "energy"
    if any(token in value for token in ("BTC", "ETH", "SOL", "CRYPTO")):
        return "crypto"
    if any(token in value for token in ("US30", "NAS", "SPX", "GER", "DAX", "UK100", "JP225", "INDEX")):
        return "indices"
    letters = "".join(ch for ch in value if ch.isalpha())
    if len(letters) >= 6:
        return "fx"
    return "other"


def _record_dimensions(row: Mapping[str, Any]) -> dict[str, str]:
    symbol = _text(row.get("symbol")).upper()
    family = _text(row.get("setup_family") or row.get("setup_code")).lower()
    session = _text(row.get("session") or row.get("session_code")).upper()
    return {
        "global": "ALL",
        "asset_class": infer_asset_class(symbol, _text(row.get("asset_class"), "")),
        "symbol": symbol,
        "family": family,
        "branch": _text(row.get("entry_branch") or row.get("entry_model")).lower(),
        "session": session,
        "killzone": _text(row.get("killzone") or row.get("killzone_code")).upper(),
        "family_symbol": f"{family}|{symbol}",
        "family_session": f"{family}|{session}",
    }


PRIOR_LEVELS = (
    "global",
    "asset_class",
    "symbol",
    "family",
    "branch",
    "session",
    "killzone",
    "family_symbol",
    "family_session",
)


def _uncertainty_interval(values: Sequence[float]) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, float("inf")
    mean_value = statistics.fmean(values)
    if len(values) <= 1:
        return mean_value, mean_value, float("inf")
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return mean_value - 1.96 * standard_error, mean_value + 1.96 * standard_error, standard_error


def _raw_prior(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    results = [_finite(row.get("result_r_initial_risk", row.get("full_close_r", row.get("realized_r")))) for row in rows]
    wins = sum(value > 0.0 for value in results)
    losses = sum(value < 0.0 for value in results)
    neutral = len(results) - wins - losses
    lower, upper, standard_error = _uncertainty_interval(results)
    times = sorted(_text(row.get("closed_at"), "") for row in rows if row.get("closed_at"))
    versions = sorted(
        {
            "|".join(
                (
                    _text(row.get("engine_version"), "unknown"),
                    _text(row.get("input_snapshot_hash"), "unknown"),
                    _text(row.get("prompt_contract_version"), "unknown"),
                )
            )
            for row in rows
        }
    )
    stuck_count = sum(_bool(row.get("stuck_no_mfe_triggered")) for row in rows)
    structural_invalid_count = sum(
        _bool(row.get("dr_and_structural_invalid_triggered"))
        or _bool(row.get("structural_invalid_triggered"))
        for row in rows
    )
    mfe_times = [
        _finite(row.get("minutes_to_0_25r_mfe"))
        for row in rows
        if row.get("minutes_to_0_25r_mfe") not in (None, "")
        and _finite(row.get("minutes_to_0_25r_mfe")) >= 0.0
    ]
    return {
        "clean_sample_size": len(rows),
        "effective_sample_size": float(len(rows)),
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "mean_net_r": statistics.fmean(results) if results else 0.0,
        "uncertainty_interval": {"lower": lower, "upper": upper, "standard_error": standard_error},
        "data_window": {"start": times[0] if times else "", "end": times[-1] if times else ""},
        "source_versions": versions,
        "ledger_integrity_status": "clean_only",
        "stale": False,
        "stuck_no_mfe_rate": stuck_count / len(rows) if rows else 0.0,
        "dr_structural_invalid_rate": structural_invalid_count / len(rows) if rows else 0.0,
        "avg_minutes_to_0_25r_mfe": statistics.fmean(mfe_times) if mfe_times else None,
    }


def _shrink_prior(
    raw: Mapping[str, Any],
    parent: Mapping[str, Any] | None,
    *,
    prior_strength: float,
) -> dict[str, Any]:
    n = max(0.0, _finite(raw.get("effective_sample_size")))
    raw_mean = _finite(raw.get("mean_net_r"))
    parent_mean = _finite((parent or {}).get("shrunk_estimate", (parent or {}).get("mean_net_r", raw_mean)))
    interval = raw.get("uncertainty_interval") if isinstance(raw.get("uncertainty_interval"), Mapping) else {}
    standard_error = _finite(interval.get("standard_error"), float("inf"))
    uncertainty_penalty = 1.0 if math.isfinite(standard_error) else 4.0
    if math.isfinite(standard_error):
        uncertainty_penalty += min(4.0, max(0.0, standard_error))
    parent_n = max(0.0, _finite((parent or {}).get("effective_sample_size")))
    parent_support = prior_strength * uncertainty_penalty
    if parent_n > 0:
        parent_support *= min(4.0, max(1.0, math.log10(parent_n + 10.0)))
    weight = n / (n + parent_support) if n > 0 else 0.0
    shrunk = weight * raw_mean + (1.0 - weight) * parent_mean
    lower = _finite(interval.get("lower"), raw_mean)
    upper = _finite(interval.get("upper"), raw_mean)
    parent_interval = (parent or {}).get("posterior_uncertainty") if isinstance((parent or {}).get("posterior_uncertainty"), Mapping) else {}
    posterior_lower = weight * lower + (1.0 - weight) * _finite(parent_interval.get("lower"), parent_mean)
    posterior_upper = weight * upper + (1.0 - weight) * _finite(parent_interval.get("upper"), parent_mean)
    return {
        **dict(raw),
        "raw_prior": {"mean_net_r": raw_mean, "sample_size": int(n)},
        "parent_prior": {
            "mean_net_r": parent_mean,
            "effective_sample_size": parent_n,
            "prior_hash": _text((parent or {}).get("prior_hash"), ""),
        },
        "shrinkage_weight": weight,
        "shrunk_estimate": shrunk,
        "posterior_uncertainty": {"lower": posterior_lower, "upper": posterior_upper},
    }


def build_hierarchical_prior_artifact(
    records: Sequence[Mapping[str, Any]],
    *,
    prior_strength: float = 20.0,
    stale_after_days: int = 30,
) -> dict[str, Any]:
    clean = [dict(row) for row in records if _clean_record(row)]
    rejected = len(records) - len(clean)
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = {level: {} for level in PRIOR_LEVELS}
    for row in clean:
        for level, key in _record_dimensions(row).items():
            grouped[level].setdefault(key, []).append(row)

    artifact: dict[str, Any] = {
        "schema_version": HIERARCHICAL_PRIOR_SCHEMA_VERSION,
        "prior_version": HIERARCHICAL_PRIOR_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "clean_record_count": len(clean),
        "rejected_record_count": rejected,
        "ledger_integrity_status": "clean_only" if clean else "no_clean_records",
        "stale_after_days": stale_after_days,
        "levels": {level: {} for level in PRIOR_LEVELS},
    }
    global_raw = _raw_prior(grouped["global"].get("ALL", []))
    global_prior = _shrink_prior(global_raw, None, prior_strength=prior_strength)
    global_prior["selected_hierarchy_path"] = ["global:ALL"]
    global_prior["prior_hash"] = canonical_hash(global_prior)
    artifact["levels"]["global"]["ALL"] = global_prior

    parent_level_for = {
        "asset_class": "global",
        "symbol": "asset_class",
        "family": "global",
        "branch": "family",
        "session": "global",
        "killzone": "session",
        "family_symbol": "family",
        "family_session": "family",
    }
    for level in PRIOR_LEVELS[1:]:
        for key, rows in sorted(grouped[level].items()):
            dims = _record_dimensions(rows[0])
            parent_level = parent_level_for[level]
            parent_key = "ALL" if parent_level == "global" else dims[parent_level]
            parent = artifact["levels"].get(parent_level, {}).get(parent_key, global_prior)
            prior = _shrink_prior(_raw_prior(rows), parent, prior_strength=prior_strength)
            prior["selected_hierarchy_path"] = list(parent.get("selected_hierarchy_path") or ["global:ALL"]) + [f"{level}:{key}"]
            prior["prior_hash"] = canonical_hash(prior)
            artifact["levels"][level][key] = prior

    windows = [
        prior.get("data_window", {})
        for buckets in artifact["levels"].values()
        for prior in buckets.values()
        if isinstance(prior, Mapping)
    ]
    starts = sorted(_text(window.get("start"), "") for window in windows if window.get("start"))
    ends = sorted(_text(window.get("end"), "") for window in windows if window.get("end"))
    artifact["data_window"] = {"start": starts[0] if starts else "", "end": ends[-1] if ends else ""}
    artifact["bucket_count"] = sum(len(buckets) for buckets in artifact["levels"].values())
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact


def priors_for_candidate(artifact: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    levels = artifact.get("levels") if isinstance(artifact.get("levels"), Mapping) else {}
    dims = _record_dimensions(candidate)
    result: dict[str, Any] = {
        "schema_version": _text(artifact.get("schema_version"), ""),
        "prior_version": _text(artifact.get("prior_version"), ""),
        "artifact_hash": _text(artifact.get("artifact_hash"), ""),
        "ledger_integrity_status": _text(artifact.get("ledger_integrity_status"), ""),
        "data_window": artifact.get("data_window") or {},
        "hierarchy": {},
    }
    for level in PRIOR_LEVELS:
        key = dims[level]
        buckets = levels.get(level) if isinstance(levels.get(level), Mapping) else {}
        result["hierarchy"][level] = {
            "key": key,
            "available": key in buckets,
            "prior": buckets.get(key) or {},
        }
    result["candidate_prior_hash"] = canonical_hash(result)
    return result


def audit_prior_artifact(
    path: Path,
    *,
    mandatory: bool,
    max_age_days: int,
    expected_schema: str = HIERARCHICAL_PRIOR_SCHEMA_VERSION,
    now_timestamp: float | None = None,
) -> dict[str, Any]:
    path = resolve_project_path(path)
    audit: dict[str, Any] = {
        "absolute_path": str(path),
        "exists": path.is_file(),
        "hash": "",
        "mtime": None,
        "prior_version": "",
        "data_window": {},
        "bucket_count": 0,
        "rejected_count": 0,
        "status": "missing",
        "mandatory": mandatory,
        "trading_eligible": not mandatory,
    }
    if not path.is_file():
        return audit
    try:
        stat = path.stat()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("prior artifact must be an object")
        audit.update(
            {
                "hash": file_sha256(path),
                "mtime": stat.st_mtime,
                "prior_version": _text(data.get("prior_version"), ""),
                "data_window": data.get("data_window") or {},
                "bucket_count": int(_finite(data.get("bucket_count"))),
                "rejected_count": int(_finite(data.get("rejected_record_count"))),
            }
        )
        schema_ok = data.get("schema_version") == expected_schema
        integrity_ok = data.get("ledger_integrity_status") == "clean_only"
        now = now_timestamp if now_timestamp is not None else datetime.now(timezone.utc).timestamp()
        stale = max_age_days > 0 and (now - stat.st_mtime) > max_age_days * 86400
        if not schema_ok:
            audit["status"] = "incompatible_schema"
        elif not integrity_ok:
            audit["status"] = "dirty_or_empty_ledger"
        elif stale:
            audit["status"] = "stale"
        else:
            audit["status"] = "ready"
            audit["trading_eligible"] = True
        audit["artifact"] = data
    except Exception as exc:
        audit["status"] = "unreadable"
        audit["error"] = str(exc)
    if mandatory and audit["status"] != "ready":
        audit["trading_eligible"] = False
    return audit


@dataclass(frozen=True)
class RiskExposure:
    symbol: str
    original_entry: float
    original_sl: float
    remaining_volume: float
    original_risk_money_per_lot: float
    kind: str = "open"
    asset_class: str = "other"
    session: str = "OFF"
    direction: str = "buy"

    @property
    def initial_risk_money(self) -> float:
        return max(0.0, self.remaining_volume) * max(0.0, self.original_risk_money_per_lot)


def effective_aggregate_risk_cap(
    *,
    base_money: float,
    percentage_cap: float | None,
    money_cap: float | None,
) -> dict[str, Any]:
    pct_money = None
    if percentage_cap is not None and percentage_cap > 0 and base_money > 0:
        pct_money = base_money * percentage_cap / 100.0
    valid_money = money_cap if money_cap is not None and money_cap > 0 else None
    candidates = [value for value in (pct_money, valid_money) if value is not None]
    effective = min(candidates) if candidates else None
    source = "none"
    if effective is not None:
        if pct_money is not None and valid_money is not None:
            source = "percentage" if pct_money <= valid_money else "money"
        else:
            source = "percentage" if pct_money is not None else "money"
    return {
        "base_money": base_money,
        "percentage_cap": percentage_cap,
        "percentage_cap_money": pct_money,
        "money_cap": valid_money,
        "effective_cap_money": effective,
        "effective_cap_source": source,
        "valid_cap_available": effective is not None,
    }


def aggregate_initial_risk_gate(
    *,
    open_exposures: Sequence[RiskExposure],
    pending_exposures: Sequence[RiskExposure],
    proposed_exposure: RiskExposure,
    base_money: float,
    percentage_cap: float | None,
    money_cap: float | None,
) -> dict[str, Any]:
    open_money = sum(exposure.initial_risk_money for exposure in open_exposures)
    pending_money = sum(exposure.initial_risk_money for exposure in pending_exposures)
    proposed_money = proposed_exposure.initial_risk_money
    post_money = open_money + pending_money + proposed_money
    cap = effective_aggregate_risk_cap(
        base_money=base_money,
        percentage_cap=percentage_cap,
        money_cap=money_cap,
    )
    effective = cap["effective_cap_money"]
    status = "pass" if effective is not None and post_money <= effective + 1e-9 else "blocked"
    return {
        **cap,
        "open_risk_money": open_money,
        "open_risk_pct": open_money / base_money * 100.0 if base_money > 0 else None,
        "pending_risk_money": pending_money,
        "proposed_risk_money": proposed_money,
        "post_trade_risk_money": post_money,
        "post_trade_risk_pct": post_money / base_money * 100.0 if base_money > 0 else None,
        "status": status,
        "reason": "no_valid_aggregate_risk_cap" if effective is None else ("aggregate_initial_risk_cap_exceeded" if status == "blocked" else ""),
    }


def fx_factor_contributions(symbol: str, direction: str, risk_pct: float) -> dict[str, float]:
    letters = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(letters) < 6:
        return {}
    base, quote = letters[:3], letters[3:6]
    sign = 1.0 if direction.lower() == "buy" else -1.0
    return {f"currency:{base}": sign * risk_pct, f"currency:{quote}": -sign * risk_pct}


def factor_contributions(
    exposure: RiskExposure,
    *,
    base_money: float,
    config: Mapping[str, Any],
) -> dict[str, float]:
    risk_pct = exposure.initial_risk_money / base_money * 100.0 if base_money > 0 else 0.0
    contributions: dict[str, float] = {
        f"symbol:{exposure.symbol.upper()}": risk_pct,
        f"asset_class:{exposure.asset_class.lower()}": risk_pct,
        f"session:{exposure.session.upper()}": risk_pct,
    }
    contributions.update(fx_factor_contributions(exposure.symbol, exposure.direction, risk_pct))
    aliases = config.get("symbol_aliases") if isinstance(config.get("symbol_aliases"), Mapping) else {}
    alias = aliases.get(exposure.symbol.upper()) if isinstance(aliases.get(exposure.symbol.upper()), Mapping) else {}
    factors = alias.get("factors") if isinstance(alias.get("factors"), Mapping) else {}
    sign = 1.0 if exposure.direction.lower() == "buy" else -1.0
    for factor, loading in factors.items():
        contributions[f"factor:{factor}"] = risk_pct * _finite(loading) * sign
    macro = _text(alias.get("macro_direction"), "")
    if macro:
        contributions[f"macro:{macro}:{'long' if sign > 0 else 'short'}"] = risk_pct
    cluster = _text(alias.get("cluster"), "")
    if cluster:
        contributions[f"cluster:{cluster}"] = risk_pct
    return contributions


def risk_factor_gate(
    *,
    existing: Sequence[RiskExposure],
    proposed: RiskExposure,
    base_money: float,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    if config.get("schema_version") != RISK_FACTOR_SCHEMA_VERSION:
        return {"status": "blocked", "reason": "risk_factor_schema_incompatible", "checks": []}
    caps = config.get("caps_pct") if isinstance(config.get("caps_pct"), Mapping) else {}
    aggregate: dict[str, float] = {}
    for exposure in existing:
        for factor, contribution in factor_contributions(exposure, base_money=base_money, config=config).items():
            aggregate[factor] = aggregate.get(factor, 0.0) + contribution
    proposed_factors = factor_contributions(proposed, base_money=base_money, config=config)
    checks: list[dict[str, Any]] = []
    blocked = False
    for factor, contribution in sorted(proposed_factors.items()):
        category = factor.split(":", 1)[0]
        cap = _finite(caps.get(factor, caps.get(category, 0.0)))
        existing_abs = abs(aggregate.get(factor, 0.0))
        post_abs = abs(aggregate.get(factor, 0.0) + contribution)
        status = "pass" if cap > 0 and post_abs <= cap + 1e-9 else "blocked"
        if status == "blocked":
            blocked = True
        checks.append(
            {
                "factor": factor,
                "direction": "positive" if contribution >= 0 else "negative",
                "existing_risk_pct": existing_abs,
                "proposed_risk_pct": abs(contribution),
                "post_risk_pct": post_abs,
                "cap_pct": cap,
                "status": status,
            }
        )
    return {
        "status": "blocked" if blocked else "pass",
        "reason": "risk_factor_cap_exceeded" if blocked else "",
        "schema_version": RISK_FACTOR_SCHEMA_VERSION,
        "factor_contributions": proposed_factors,
        "checks": checks,
        "covariance_shadow": {"enabled": False, "authority": False, "reason": "clean_synchronized_return_evidence_unavailable"},
    }


@dataclass(frozen=True)
class BrokerCostSample:
    symbol: str
    asset_class: str
    lot_size: float
    account_currency: str
    broker_account_id: str
    direction: str
    entry_commission: float
    exit_commission: float
    swap: float
    fees: float
    observed_spread_money: float
    observed_slippage_money: float
    timestamp: str

    @property
    def round_turn_cost_per_lot(self) -> float:
        if self.lot_size <= 0:
            return 0.0
        total = sum(
            abs(value)
            for value in (
                self.entry_commission,
                self.exit_commission,
                self.swap,
                self.fees,
                self.observed_spread_money,
                self.observed_slippage_money,
            )
        )
        return total / self.lot_size


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def estimate_broker_cost(
    samples: Sequence[BrokerCostSample],
    *,
    symbol: str,
    asset_class: str,
    account_currency: str,
    broker_account_id: str,
    stressed_percentile: float,
    minimum_samples: int,
    fallback_per_lot: float,
) -> dict[str, Any]:
    matching = [
        sample
        for sample in samples
        if sample.symbol.upper() == symbol.upper()
        and sample.account_currency.upper() == account_currency.upper()
        and sample.broker_account_id == broker_account_id
        and sample.round_turn_cost_per_lot > 0
    ]
    if len(matching) < minimum_samples:
        matching = [
            sample
            for sample in samples
            if sample.asset_class.lower() == asset_class.lower()
            and sample.account_currency.upper() == account_currency.upper()
            and sample.broker_account_id == broker_account_id
            and sample.round_turn_cost_per_lot > 0
        ]
    values = [sample.round_turn_cost_per_lot for sample in matching]
    source = "broker_history" if len(values) >= minimum_samples else "configured_fallback"
    if source == "configured_fallback":
        stressed = max(0.00000001, fallback_per_lot)
        median = stressed
    else:
        median = statistics.median(values)
        stressed = _percentile(values, stressed_percentile)
    times = sorted(sample.timestamp for sample in matching if sample.timestamp)
    return {
        "schema_version": COMMISSION_MODEL_SCHEMA_VERSION,
        "symbol": symbol,
        "asset_class": asset_class,
        "median_round_turn_cost_per_lot": median,
        "stressed_round_turn_cost_per_lot": stressed,
        "stressed_percentile": stressed_percentile,
        "sample_size": len(values),
        "data_window": {"start": times[0] if times else "", "end": times[-1] if times else ""},
        "last_update": times[-1] if times else "",
        "source": source,
        "fallback": source == "configured_fallback",
        "stale": not bool(times),
    }


def cost_prediction_error(predicted: float, actual: float) -> dict[str, float]:
    return {
        "predicted_cost": predicted,
        "actual_realized_cost": actual,
        "prediction_error": actual - predicted,
        "absolute_prediction_error": abs(actual - predicted),
    }


@dataclass
class ManagementState:
    position_id: str
    current_state: str = MANAGEMENT_HEALTHY
    previous_state: str = ""
    transition_time: str = ""
    transition_reason: str = ""
    evidence_snapshot: dict[str, Any] = field(default_factory=dict)
    action_executed: str = "none"
    action_id: str = ""
    management_version: str = MANAGEMENT_SCHEMA_VERSION
    executed_action_ids: set[str] = field(default_factory=set)


_ALLOWED_TRANSITIONS = {
    MANAGEMENT_HEALTHY: {MANAGEMENT_WARNING, MANAGEMENT_THESIS_INVALID, MANAGEMENT_EXITED},
    MANAGEMENT_WARNING: {MANAGEMENT_HEALTHY, MANAGEMENT_THESIS_INVALID, MANAGEMENT_EXITED},
    MANAGEMENT_THESIS_INVALID: {MANAGEMENT_EXITED},
    MANAGEMENT_EXITED: set(),
}


def transition_management_state(
    state: ManagementState,
    *,
    target_state: str,
    reason: str,
    evidence: Mapping[str, Any],
    policy_action: str,
    transition_time: str,
) -> dict[str, Any]:
    if target_state == state.current_state:
        return {"changed": False, "action_executed": False, "reason": "state_unchanged", "state": state}
    if target_state not in _ALLOWED_TRANSITIONS.get(state.current_state, set()):
        return {"changed": False, "action_executed": False, "reason": "invalid_state_transition", "state": state}
    action_id = canonical_hash(
        {
            "position_id": state.position_id,
            "from": state.current_state,
            "to": target_state,
            "reason": reason,
            "management_version": state.management_version,
        }
    )
    if action_id in state.executed_action_ids:
        return {"changed": False, "action_executed": False, "reason": "duplicate_action_id", "action_id": action_id, "state": state}
    previous = state.current_state
    state.previous_state = previous
    state.current_state = target_state
    state.transition_time = transition_time
    state.transition_reason = reason
    state.evidence_snapshot = dict(evidence)
    state.action_executed = policy_action
    state.action_id = action_id
    state.executed_action_ids.add(action_id)
    return {
        "changed": True,
        "action_executed": policy_action != "none",
        "action": policy_action,
        "action_id": action_id,
        "from": previous,
        "to": target_state,
        "state": state,
    }


def management_policy_authority(
    *,
    clean_ledger: bool,
    exact_identity: bool,
    sample_size: int,
    minimum_sample: int,
    oos_management_alpha: float | None,
    uncertainty_passed: bool,
    uncontaminated_holdout: bool,
) -> dict[str, Any]:
    eligible = all(
        (
            clean_ledger,
            exact_identity,
            sample_size >= minimum_sample,
            oos_management_alpha is not None and oos_management_alpha > 0,
            uncertainty_passed,
            uncontaminated_holdout,
        )
    )
    return {
        "schema_version": MANAGEMENT_EXPERIMENT_SCHEMA_VERSION,
        "active_policy_change_eligible": eligible,
        "alternatives_shadow_only": not eligible,
        "reason": "" if eligible else "management_experiment_evidence_gate_not_passed",
    }


@dataclass(frozen=True)
class VolumeNormalization:
    requested_volume: float
    normalized_volume: float
    action: str
    reason: str
    residual_volume: float
    broker_min: float
    broker_step: float
    broker_max: float


def _floor_to_step(volume: float, step: float) -> float:
    if step <= 0:
        return 0.0
    return math.floor((volume + step * 1e-10) / step) * step


def normalize_opening_volume(
    requested: float,
    *,
    minimum: float,
    maximum: float,
    step: float,
    minimum_risk_valid: bool,
) -> VolumeNormalization:
    if requested <= 0 or minimum <= 0 or maximum < minimum or step <= 0:
        return VolumeNormalization(requested, 0.0, "reject", "invalid_volume_contract", 0.0, minimum, step, maximum)
    floored = min(maximum, _floor_to_step(requested, step))
    if floored < minimum:
        if minimum_risk_valid:
            return VolumeNormalization(requested, minimum, "raise_to_minimum", "broker_minimum_and_risk_valid", 0.0, minimum, step, maximum)
        return VolumeNormalization(requested, 0.0, "reject", "minimum_volume_would_exceed_risk", 0.0, minimum, step, maximum)
    return VolumeNormalization(requested, floored, "open", "normalized_down_to_step", 0.0, minimum, step, maximum)


def normalize_closing_volume(
    requested: float,
    *,
    current: float,
    minimum: float,
    maximum: float,
    step: float,
    allow_close_all_below_minimum: bool,
) -> VolumeNormalization:
    bounded = min(max(0.0, requested), max(0.0, current))
    if bounded <= 0 or current <= 0 or minimum <= 0 or step <= 0:
        return VolumeNormalization(requested, 0.0, "skip", "invalid_or_zero_close_volume", current, minimum, step, maximum)
    if bounded >= current - step * 1e-8:
        return VolumeNormalization(requested, current, "close_all", "requested_full_close", 0.0, minimum, step, maximum)
    floored = min(current, _floor_to_step(bounded, step))
    residual = max(0.0, current - floored)
    if floored < minimum:
        if allow_close_all_below_minimum:
            return VolumeNormalization(requested, current, "close_all", "explicit_below_minimum_close_all_policy", 0.0, minimum, step, maximum)
        return VolumeNormalization(requested, 0.0, "skip", "requested_close_below_broker_minimum", current, minimum, step, maximum)
    if 0 < residual < minimum:
        reduced = _floor_to_step(max(0.0, current - minimum), step)
        if reduced < minimum:
            if allow_close_all_below_minimum:
                return VolumeNormalization(requested, current, "close_all", "explicit_tiny_residual_close_all_policy", 0.0, minimum, step, maximum)
            return VolumeNormalization(requested, 0.0, "skip", "partial_close_would_leave_tiny_residual", current, minimum, step, maximum)
        floored = min(floored, reduced)
        residual = current - floored
    return VolumeNormalization(requested, floored, "partial_close", "floored_to_step", residual, minimum, step, maximum)


@dataclass
class InvalidationConfirmationState:
    mode: str
    reference_timeframe: str
    trigger_level: float
    spread: float
    buffer: float
    first_breach_time: int | None = None
    confirmed_time: int | None = None
    confirming_bar: int | None = None


def confirm_invalidation(
    state: InvalidationConfirmationState,
    *,
    is_buy: bool,
    current_price: float,
    now_epoch: int,
    persistence_seconds: int = 0,
    closed_bar_close: float | None = None,
    closed_bar_time: int | None = None,
) -> dict[str, Any]:
    adjusted_level = state.trigger_level - state.spread - state.buffer if is_buy else state.trigger_level + state.spread + state.buffer
    breached = current_price < adjusted_level if is_buy else current_price > adjusted_level
    if not breached:
        state.first_breach_time = None
        return {"confirmed": False, "reason": "no_sustained_breach", "state": state}
    if state.first_breach_time is None:
        state.first_breach_time = now_epoch
    mode = state.mode.upper()
    confirmed = False
    if mode in {"CLOSED_M1_BAR", "CLOSED_ENTRY_TF_BAR"}:
        if closed_bar_close is not None:
            confirmed = closed_bar_close < adjusted_level if is_buy else closed_bar_close > adjusted_level
            if confirmed:
                state.confirming_bar = closed_bar_time
    elif mode == "N_SECOND_PERSISTENCE":
        confirmed = now_epoch - state.first_breach_time >= max(1, persistence_seconds)
    elif mode == "PRICE_SPREAD_BUFFER":
        confirmed = breached
    elif mode == "TICK":
        confirmed = breached
    if confirmed:
        state.confirmed_time = now_epoch
    return {"confirmed": confirmed, "reason": "confirmed" if confirmed else "awaiting_confirmation", "state": state}


def resolve_invalidation_policy(
    *,
    asset_class: str,
    default_mode: str,
    default_reference_timeframe: str,
    default_persistence_seconds: int,
    default_spread_buffer_multiple: float,
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve an optional versioned asset-class policy with explicit fallback.

    This resolver never invents a class override. An absent class uses the
    configured global policy, while a malformed enabled policy fails closed.
    """

    base = {
        "mode": default_mode,
        "reference_timeframe": default_reference_timeframe,
        "persistence_seconds": max(1, int(default_persistence_seconds)),
        "spread_buffer_multiple": max(0.0, float(default_spread_buffer_multiple)),
        "source": "configured_global_fallback",
        "policy_version": "global_inputs",
        "valid": True,
    }
    if policy is None:
        return base
    if not isinstance(policy, Mapping):
        return {**base, "valid": False, "source": "invalid_asset_class_policy"}
    version = _text(policy.get("schema_version"), "")
    classes = policy.get("asset_classes")
    if not version or not isinstance(classes, Mapping):
        return {**base, "valid": False, "source": "invalid_asset_class_policy"}
    selected = classes.get(asset_class.lower())
    if selected is None:
        return {**base, "source": "asset_class_policy_global_fallback", "policy_version": version}
    if not isinstance(selected, Mapping):
        return {**base, "valid": False, "source": "invalid_asset_class_override", "policy_version": version}
    mode = _text(selected.get("mode"), "").upper()
    allowed_modes = {
        "CLOSED_M1_BAR",
        "CLOSED_ENTRY_TF_BAR",
        "N_SECOND_PERSISTENCE",
        "PRICE_SPREAD_BUFFER",
        "TICK",
    }
    if mode not in allowed_modes:
        return {**base, "valid": False, "source": "invalid_asset_class_override", "policy_version": version}
    try:
        persistence = int(selected.get("persistence_seconds", base["persistence_seconds"]))
        spread_multiple = float(selected.get("spread_buffer_multiple", base["spread_buffer_multiple"]))
    except (TypeError, ValueError):
        return {**base, "valid": False, "source": "invalid_asset_class_override", "policy_version": version}
    if persistence < 1 or not math.isfinite(spread_multiple) or spread_multiple < 0.0:
        return {**base, "valid": False, "source": "invalid_asset_class_override", "policy_version": version}
    return {
        "mode": mode,
        "reference_timeframe": _text(
            selected.get("reference_timeframe"), base["reference_timeframe"]
        ),
        "persistence_seconds": persistence,
        "spread_buffer_multiple": spread_multiple,
        "source": "asset_class_policy",
        "policy_version": version,
        "valid": True,
    }


def evaluate_price_path_contract(
    *,
    bars: Sequence[Mapping[str, Any]],
    is_buy: bool,
    entry: float,
    stop: float,
    target: float,
    horizon_reached: bool,
    execution_cost_r: float = 0.0,
) -> dict[str, Any]:
    """Evaluate a stop/target path without guessing same-bar sequencing.

    If neither terminal level has hit and the configured horizon is still in
    the future, the result stays PENDING. This prevents early managed exits
    from fabricating an original-policy counterfactual at the close timestamp.
    """

    risk = abs(entry - stop)
    direction_valid = (
        entry > 0.0
        and stop > 0.0
        and target > 0.0
        and risk > 0.0
        and ((is_buy and stop < entry < target) or (not is_buy and target < entry < stop))
    )
    if not direction_valid:
        return {
            "status": "INVALID",
            "complete": True,
            "ambiguous": True,
            "reason": "invalid_price_path_contract",
            "result_r": None,
            "mfe_r": None,
            "mae_r": None,
            "time_to_event_sec": None,
        }
    max_favorable = 0.0
    max_adverse = 0.0
    last_close = entry
    first_time: int | None = None
    event_time: int | None = None
    result_r: float | None = None
    for bar in bars:
        try:
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            timestamp = int(bar.get("time", 0))
        except (KeyError, TypeError, ValueError):
            return {
                "status": "INVALID",
                "complete": True,
                "ambiguous": True,
                "reason": "malformed_price_path_bar",
                "result_r": None,
                "mfe_r": None,
                "mae_r": None,
                "time_to_event_sec": None,
            }
        if first_time is None:
            first_time = timestamp
        last_close = close
        favorable = (high - entry) / risk if is_buy else (entry - low) / risk
        adverse = (low - entry) / risk if is_buy else (entry - high) / risk
        max_favorable = max(max_favorable, favorable)
        max_adverse = min(max_adverse, adverse)
        stop_hit = low <= stop if is_buy else high >= stop
        target_hit = high >= target if is_buy else low <= target
        if stop_hit and target_hit:
            return {
                "status": "AMBIGUOUS",
                "complete": True,
                "ambiguous": True,
                "reason": "sl_and_tp_touched_without_tick_sequence",
                "result_r": None,
                "mfe_r": max_favorable,
                "mae_r": max_adverse,
                "time_to_event_sec": max(0, timestamp - (first_time or timestamp)),
            }
        if stop_hit:
            event_time = timestamp
            result_r = -1.0 - max(0.0, execution_cost_r)
            break
        if target_hit:
            event_time = timestamp
            result_r = abs(target - entry) / risk - max(0.0, execution_cost_r)
            break
    if result_r is not None:
        return {
            "status": "RESOLVED_STOP" if result_r < 0.0 else "RESOLVED_TARGET",
            "complete": True,
            "ambiguous": False,
            "reason": "terminal_price_level_reached",
            "result_r": result_r,
            "mfe_r": max_favorable,
            "mae_r": max_adverse,
            "time_to_event_sec": max(0, (event_time or 0) - (first_time or event_time or 0)),
        }
    if not horizon_reached:
        return {
            "status": "PENDING",
            "complete": False,
            "ambiguous": False,
            "reason": "awaiting_original_policy_horizon",
            "result_r": None,
            "mfe_r": max_favorable,
            "mae_r": max_adverse,
            "time_to_event_sec": None,
        }
    result_r = ((last_close - entry) / risk if is_buy else (entry - last_close) / risk) - max(
        0.0, execution_cost_r
    )
    return {
        "status": "RESOLVED_HORIZON",
        "complete": True,
        "ambiguous": False,
        "reason": "configured_horizon_reached",
        "result_r": result_r,
        "mfe_r": max_favorable,
        "mae_r": max_adverse,
        "time_to_event_sec": None,
    }


def management_alpha(
    *,
    actual_managed_result_r: float,
    original_sl_tp_result_r: float | None,
    ambiguous: bool,
) -> dict[str, Any]:
    if ambiguous or original_sl_tp_result_r is None:
        return {
            "actual_managed_result_r": actual_managed_result_r,
            "counterfactual_original_sl_tp_result_r": None,
            "management_alpha": None,
            "eligible_for_policy_selection": False,
            "ambiguity_reason": "sl_and_tp_touched_without_tick_sequence" if ambiguous else "counterfactual_unavailable",
        }
    return {
        "actual_managed_result_r": actual_managed_result_r,
        "counterfactual_original_sl_tp_result_r": original_sl_tp_result_r,
        "management_alpha": actual_managed_result_r - original_sl_tp_result_r,
        "eligible_for_policy_selection": True,
        "ambiguity_reason": "",
    }


@dataclass(frozen=True)
class SessionWindow:
    symbol: str
    day_of_week: int
    open_epoch: int
    close_epoch: int
    no_entry_from_epoch: int
    flatten_from_epoch: int
    next_tradable_epoch: int
    source: str = "broker_session_api"


def session_action(window: SessionWindow, now_epoch: int) -> dict[str, Any]:
    market_open = window.open_epoch <= now_epoch < window.close_epoch
    flatten = market_open and now_epoch >= window.flatten_from_epoch
    no_entry = (not market_open) or now_epoch >= window.no_entry_from_epoch
    return {
        "market_closed": not market_open,
        "no_new_entry": no_entry,
        "flatten": flatten,
        "next_tradable_epoch": window.next_tradable_epoch,
    }


@dataclass
class FlattenRetryState:
    symbol: str
    last_state_hash: str = ""
    last_attempt_epoch: int = 0
    attempts: int = 0
    failures: int = 0
    last_retcode: str = ""


def flatten_retry_allowed(
    state: FlattenRetryState,
    *,
    exposure_snapshot: Mapping[str, Any],
    now_epoch: int,
    retry_seconds: int,
) -> bool:
    current_hash = canonical_hash(exposure_snapshot)
    changed = current_hash != state.last_state_hash
    elapsed = now_epoch - state.last_attempt_epoch >= max(1, retry_seconds)
    return changed or elapsed or state.last_attempt_epoch <= 0


def append_shadow_candidate(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    immutable = dict(record)
    immutable.update(
        {
            "schema_version": SHADOW_CANDIDATE_SCHEMA_VERSION,
            "can_trade": False,
            "authority": "RECOMMENDATION_ONLY",
        }
    )
    immutable["record_hash"] = canonical_hash(immutable)
    append_jsonl(resolve_project_path(path), immutable)
    return immutable


def normalized_fvg_minimum(
    *,
    raw_width_price: float,
    point: float,
    minimum_ticks: float,
    spread_price: float,
    spread_multiple: float,
    atr_price: float,
    atr_fraction: float,
    session_noise_price: float,
    session_noise_fraction: float,
    mode: str,
    asset_class: str,
    policy_version: str,
    sample_size: int,
    minimum_asset_class_samples: int = 0,
) -> dict[str, Any]:
    components = {
        "minimum_ticks_price": max(0.0, minimum_ticks) * max(0.0, point),
        "spread_multiple_price": max(0.0, spread_price) * max(0.0, spread_multiple),
        "atr_fraction_price": max(0.0, atr_price) * max(0.0, atr_fraction),
        "session_noise_fraction_price": max(0.0, session_noise_price) * max(0.0, session_noise_fraction),
    }
    selected = max(components.values())
    passed = raw_width_price + 1e-12 >= selected
    mode_upper = mode.upper()
    if mode_upper not in {FVG_MODE_OFF, FVG_MODE_SHADOW, FVG_MODE_ENFORCE}:
        mode_upper = FVG_MODE_SHADOW
    evidence_sufficient = sample_size >= max(0, minimum_asset_class_samples)
    if mode_upper in {FVG_MODE_OFF, FVG_MODE_SHADOW}:
        enforced_pass = True
        authority_reason = "non_authoritative_mode"
    elif not evidence_sufficient:
        enforced_pass = False
        authority_reason = "insufficient_asset_class_samples"
    else:
        enforced_pass = passed
        authority_reason = "asset_class_policy_enforced"
    return {
        "schema_version": NORMALIZED_FVG_SCHEMA_VERSION,
        "raw_fvg_width_price": raw_width_price,
        "raw_fvg_width_ticks": raw_width_price / point if point > 0 else None,
        **components,
        "normalized_minimum_price": selected,
        "normalized_minimum_ticks": selected / point if point > 0 else None,
        "shadow_pass": passed,
        "enforced_pass": enforced_pass,
        "mode": mode_upper,
        "asset_class": asset_class,
        "asset_class_policy_version": policy_version,
        "sample_size": sample_size,
        "minimum_asset_class_samples": max(0, minimum_asset_class_samples),
        "asset_class_evidence_sufficient": evidence_sufficient,
        "authority_reason": authority_reason,
        "per_symbol_tuning": False,
    }


def runtime_governance_versions() -> dict[str, str]:
    return {
        "runtime_governance_version": RUNTIME_GOVERNANCE_VERSION,
        "repeatability_schema_version": REPEATABILITY_SCHEMA_VERSION,
        "hierarchical_prior_schema_version": HIERARCHICAL_PRIOR_SCHEMA_VERSION,
        "risk_factor_schema_version": RISK_FACTOR_SCHEMA_VERSION,
        "commission_model_schema_version": COMMISSION_MODEL_SCHEMA_VERSION,
        "management_schema_version": MANAGEMENT_SCHEMA_VERSION,
        "management_experiment_schema_version": MANAGEMENT_EXPERIMENT_SCHEMA_VERSION,
        "management_counterfactual_schema_version": MANAGEMENT_COUNTERFACTUAL_SCHEMA_VERSION,
        "invalidation_policy_schema_version": INVALIDATION_POLICY_SCHEMA_VERSION,
        "shadow_candidate_schema_version": SHADOW_CANDIDATE_SCHEMA_VERSION,
        "normalized_fvg_schema_version": NORMALIZED_FVG_SCHEMA_VERSION,
    }
