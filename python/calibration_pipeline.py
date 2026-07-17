"""Shadow-only empirical outcome and chronological calibration pipeline."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from governance_contracts import (
    CALIBRATION_CONTRACT_VERSION,
    EMPIRICAL_PRE_ENTRY_FEATURES,
    FEATURE_LINEAGE_VERSION,
    LedgerIntegrityStatus,
    SETUP_TAXONOMY_VERSION,
    stable_data_hash,
)
from architecture_contracts import (
    ENTRY_MODEL_VERSION,
    HIERARCHICAL_OUTCOME_MODEL_VERSION,
    build_entry_and_management_shadow_artifacts,
    build_hierarchical_outcome_artifact,
    cohort_metadata,
    require_homogeneous_cohort,
)


def _finite(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except Exception:
        return False


def _number(value: Any, default: float = 0.0) -> float:
    return float(value) if _finite(value) else default


def _time(record: Mapping[str, Any]) -> int:
    for key in ("opened_at", "filled_at", "planned_at", "closed_at"):
        try:
            value = int(record.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


@dataclass(frozen=True)
class Standardizer:
    center: tuple[float, ...]
    scale: tuple[float, ...]


@dataclass(frozen=True)
class LogisticOutcomeModel:
    feature_names: tuple[str, ...]
    standardizer: Standardizer
    intercept: float
    coefficients: tuple[float, ...]
    l2: float


@dataclass(frozen=True)
class RidgeOutcomeModel:
    feature_names: tuple[str, ...]
    standardizer: Standardizer
    intercept: float
    coefficients: tuple[float, ...]
    l2: float


def _matrix(records: Sequence[Mapping[str, Any]], feature_names: Sequence[str]) -> list[list[float]]:
    return [[_number(row.get(name)) for name in feature_names] for row in records]


def _standardizer(matrix: Sequence[Sequence[float]]) -> Standardizer:
    if not matrix:
        return Standardizer((), ())
    columns = list(zip(*matrix))
    centers = tuple(statistics.fmean(column) for column in columns)
    scales = tuple(max(statistics.pstdev(column), 1e-6) for column in columns)
    return Standardizer(centers, scales)


def _standardize(row: Sequence[float], standardizer: Standardizer) -> list[float]:
    return [(value - center) / scale for value, center, scale in zip(row, standardizer.center, standardizer.scale)]


def fit_logistic_outcome_model(
    records: Sequence[Mapping[str, Any]],
    *,
    target_field: str = "target_before_stop",
    feature_names: Sequence[str] | None = None,
    l2: float = 0.20,
    iterations: int = 1200,
    learning_rate: float = 0.04,
) -> LogisticOutcomeModel:
    names = tuple(feature_names or (spec["name"] for spec in EMPIRICAL_PRE_ENTRY_FEATURES))
    matrix = _matrix(records, names)
    if len(matrix) < 20:
        raise ValueError("insufficient_clean_training_records")
    if any(row.get(target_field) not in {True, False} for row in records):
        raise ValueError(f"primary_target_missing:{target_field}")
    y = [1.0 if bool(row.get(target_field)) else 0.0 for row in records]
    scaler = _standardizer(matrix)
    x = [_standardize(row, scaler) for row in matrix]
    base = min(0.99, max(0.01, statistics.fmean(y)))
    intercept = math.log(base / (1.0 - base))
    weights = [0.0] * len(names)
    n = float(len(x))
    for _ in range(iterations):
        grad_b = 0.0
        grad_w = [0.0] * len(weights)
        for features, target in zip(x, y):
            pred = _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, features)))
            error = pred - target
            grad_b += error
            for index, value in enumerate(features):
                grad_w[index] += error * value
        intercept -= learning_rate * grad_b / n
        for index in range(len(weights)):
            weights[index] -= learning_rate * (grad_w[index] / n + l2 * weights[index])
    return LogisticOutcomeModel(names, scaler, intercept, tuple(weights), l2)


def predict_logistic(model: LogisticOutcomeModel, record: Mapping[str, Any]) -> float:
    raw = [_number(record.get(name)) for name in model.feature_names]
    features = _standardize(raw, model.standardizer)
    return _sigmoid(model.intercept + sum(weight * value for weight, value in zip(model.coefficients, features)))


def fit_ridge_expected_r_model(
    records: Sequence[Mapping[str, Any]],
    *,
    target_field: str = "unmanaged_original_plan_net_r",
    feature_names: Sequence[str] | None = None,
    l2: float = 0.40,
    iterations: int = 1400,
    learning_rate: float = 0.02,
) -> RidgeOutcomeModel:
    names = tuple(feature_names or (spec["name"] for spec in EMPIRICAL_PRE_ENTRY_FEATURES))
    matrix = _matrix(records, names)
    if len(matrix) < 20:
        raise ValueError("insufficient_clean_training_records")
    if any(not _finite(row.get(target_field)) for row in records):
        raise ValueError(f"primary_target_missing:{target_field}")
    y = [_number(row.get(target_field)) for row in records]
    scaler = _standardizer(matrix)
    x = [_standardize(row, scaler) for row in matrix]
    intercept = statistics.fmean(y)
    weights = [0.0] * len(names)
    n = float(len(x))
    for _ in range(iterations):
        grad_b = 0.0
        grad_w = [0.0] * len(weights)
        for features, target in zip(x, y):
            pred = intercept + sum(weight * value for weight, value in zip(weights, features))
            error = pred - target
            grad_b += error
            for index, value in enumerate(features):
                grad_w[index] += error * value
        intercept -= learning_rate * grad_b / n
        for index in range(len(weights)):
            weights[index] -= learning_rate * (grad_w[index] / n + l2 * weights[index])
    return RidgeOutcomeModel(names, scaler, intercept, tuple(weights), l2)


def predict_expected_r(model: RidgeOutcomeModel, record: Mapping[str, Any]) -> float:
    raw = [_number(record.get(name)) for name in model.feature_names]
    features = _standardize(raw, model.standardizer)
    return model.intercept + sum(weight * value for weight, value in zip(model.coefficients, features))


def fit_platt(probabilities: Sequence[float], targets: Sequence[int]) -> tuple[float, float]:
    if len(probabilities) != len(targets) or len(probabilities) < 20:
        raise ValueError("insufficient_platt_calibration_records")
    logits = [math.log(max(1e-6, min(1 - 1e-6, p)) / (1 - max(1e-6, min(1 - 1e-6, p)))) for p in probabilities]
    slope = 1.0
    intercept = 0.0
    n = float(len(logits))
    for _ in range(1000):
        grad_slope = grad_intercept = 0.0
        for logit, target in zip(logits, targets):
            pred = _sigmoid(intercept + slope * logit)
            error = pred - target
            grad_intercept += error
            grad_slope += error * logit
        intercept -= 0.03 * grad_intercept / n
        slope -= 0.03 * (grad_slope / n + 0.01 * slope)
    return intercept, slope


def apply_platt(probability: float, params: tuple[float, float]) -> float:
    p = max(1e-6, min(1 - 1e-6, probability))
    logit = math.log(p / (1 - p))
    return _sigmoid(params[0] + params[1] * logit)


def fit_isotonic(probabilities: Sequence[float], targets: Sequence[int]) -> list[tuple[float, float]]:
    if len(probabilities) != len(targets) or len(probabilities) < 60:
        raise ValueError("insufficient_isotonic_calibration_records")
    ordered = sorted(zip(probabilities, targets), key=lambda item: item[0])
    blocks: list[dict[str, float]] = []
    for probability, target in ordered:
        blocks.append({"lo": probability, "hi": probability, "sum": float(target), "count": 1.0})
        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            if left["sum"] / left["count"] <= right["sum"] / right["count"]:
                break
            blocks[-2:] = [{
                "lo": left["lo"],
                "hi": right["hi"],
                "sum": left["sum"] + right["sum"],
                "count": left["count"] + right["count"],
            }]
    return [(block["hi"], block["sum"] / block["count"]) for block in blocks]


def apply_isotonic(probability: float, points: Sequence[tuple[float, float]]) -> float:
    for upper, value in points:
        if probability <= upper:
            return value
    return points[-1][1] if points else probability


def reliability_metrics(probabilities: Sequence[float], targets: Sequence[int], bins: int = 10) -> dict[str, Any]:
    if len(probabilities) != len(targets) or not probabilities:
        raise ValueError("calibration_metric_input_invalid")
    eps = 1e-12
    brier = statistics.fmean((p - y) ** 2 for p, y in zip(probabilities, targets))
    log_loss = -statistics.fmean(y * math.log(max(eps, p)) + (1 - y) * math.log(max(eps, 1 - p)) for p, y in zip(probabilities, targets))
    buckets: list[dict[str, Any]] = []
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [(p, y) for p, y in zip(probabilities, targets) if lower <= p < upper or (index == bins - 1 and p == 1.0)]
        if not members:
            continue
        predicted = statistics.fmean(p for p, _ in members)
        observed = statistics.fmean(y for _, y in members)
        count = len(members)
        se = math.sqrt(max(observed * (1 - observed), 0.0) / count)
        lower_bound = max(0.0, observed - 1.96 * se)
        upper_bound = min(1.0, observed + 1.96 * se)
        ece += count / len(probabilities) * abs(predicted - observed)
        buckets.append({
            "bucket": f"{lower:.1f}-{upper:.1f}",
            "sample_size": count,
            "predicted_probability": predicted,
            "observed_event_rate": observed,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
        })
    observed_rates = [row["observed_event_rate"] for row in buckets]
    monotonic = all(left <= right + 0.05 for left, right in zip(observed_rates, observed_rates[1:]))
    return {
        "brier_score": brier,
        "log_loss": log_loss,
        "expected_calibration_error": ece,
        "reliability_curve": buckets,
        "reasonably_monotonic": monotonic,
    }


def chronological_split(records: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    ordered = sorted(records, key=_time)
    if len(ordered) < 80:
        raise ValueError("insufficient_clean_records_for_chronological_split")
    train_end = max(20, int(len(ordered) * 0.50))
    calibration_end = max(train_end + 20, int(len(ordered) * 0.75))
    return ordered[:train_end], ordered[train_end:calibration_end], ordered[calibration_end:]


def run_shadow_calibration(
    records: Sequence[Mapping[str, Any]],
    *,
    ledger_status: str,
    engine_version: str,
    decision_schema_version: str,
    code_commit: str = "unknown",
    min_clean_sample: int = 120,
) -> dict[str, Any]:
    primary_target = "unmanaged_original_plan_target_before_stop"
    expected_r_target = "unmanaged_original_plan_net_r"
    clean = [
        row for row in records
        if row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value
        and bool(row.get("learning_eligible"))
        and str(row.get("attribution_status") or "").lower() in {"exact", "verified", "exact_verified"}
        and not bool(row.get("execution_identity_quarantined"))
        and bool(cohort_metadata(row)["cohort_complete"])
        and row.get(primary_target) in {True, False}
        and _finite(row.get(expected_r_target))
        and all(_finite(row.get(spec["name"])) for spec in EMPIRICAL_PRE_ENTRY_FEATURES)
    ]
    unavailable = {
        "calibration_contract_version": CALIBRATION_CONTRACT_VERSION,
        "hierarchical_outcome_model_version": HIERARCHICAL_OUTCOME_MODEL_VERSION,
        "entry_model_version": ENTRY_MODEL_VERSION,
        "primary_target": primary_target,
        "expected_r_target": expected_r_target,
        "calibration_available": False,
        "trading_activation": False,
        "status": "shadow_unavailable",
        "input_record_count": len(records),
        "clean_exact_homogeneous_record_count": len(clean),
        "activation_block_reasons": [],
    }
    if ledger_status != LedgerIntegrityStatus.CLEAN.value:
        unavailable["activation_block_reasons"] = ["ledger_not_clean"]
        return unavailable
    if len(clean) < min_clean_sample:
        unavailable["activation_block_reasons"] = ["minimum_clean_exact_homogeneous_sample_not_met"]
        return unavailable
    try:
        cohort_id = require_homogeneous_cohort(clean)
    except ValueError as exc:
        unavailable["activation_block_reasons"] = [str(exc)]
        return unavailable
    train, calibration, validation = chronological_split(clean)
    outcome_model = fit_logistic_outcome_model(train, target_field=primary_target)
    expected_r_model = fit_ridge_expected_r_model(train, target_field=expected_r_target)
    calibration_raw = [predict_logistic(outcome_model, row) for row in calibration]
    calibration_y = [1 if bool(row.get(primary_target)) else 0 for row in calibration]
    validation_raw = [predict_logistic(outcome_model, row) for row in validation]
    validation_y = [1 if bool(row.get(primary_target)) else 0 for row in validation]
    candidates: list[tuple[str, Any, list[float], dict[str, Any]]] = []
    platt = fit_platt(calibration_raw, calibration_y)
    platt_validation = [apply_platt(value, platt) for value in validation_raw]
    candidates.append(("platt", platt, platt_validation, reliability_metrics(platt_validation, validation_y)))
    if len(calibration) >= 60:
        isotonic = fit_isotonic(calibration_raw, calibration_y)
        isotonic_validation = [apply_isotonic(value, isotonic) for value in validation_raw]
        candidates.append(("isotonic", isotonic, isotonic_validation, reliability_metrics(isotonic_validation, validation_y)))
    method, params, probabilities, metrics = min(candidates, key=lambda item: item[3]["brier_score"])
    base_rate = statistics.fmean(validation_y)
    base_brier = statistics.fmean((base_rate - value) ** 2 for value in validation_y)
    block_reasons: list[str] = []
    if metrics["brier_score"] >= base_brier:
        block_reasons.append("calibration_does_not_beat_unconditional_base_rate")
    if not metrics["reasonably_monotonic"]:
        block_reasons.append("probability_buckets_not_monotonic")
    if not engine_version or not decision_schema_version:
        block_reasons.append("live_schema_identity_missing")
    artifact = {
        "calibration_contract_version": CALIBRATION_CONTRACT_VERSION,
        "model_version": f"po3_shadow_{method}_v1",
        "feature_version": FEATURE_LINEAGE_VERSION,
        "taxonomy_version": SETUP_TAXONOMY_VERSION,
        "engine_version": engine_version,
        "decision_schema_version": decision_schema_version,
        "cohort_id": cohort_id,
        "cohort_schema_version": cohort_metadata(clean[0])["cohort_schema_version"],
        "code_commit": code_commit,
        "primary_target": primary_target,
        "expected_r_target": expected_r_target,
        "business_labels_authority": "reporting_only",
        "method": method,
        "parameters": params,
        "training_window": [_time(train[0]), _time(train[-1])],
        "calibration_window": [_time(calibration[0]), _time(calibration[-1])],
        "validation_window": [_time(validation[0]), _time(validation[-1])],
        "training_count": len(train),
        "calibration_count": len(calibration),
        "validation_count": len(validation),
        "data_hash": stable_data_hash(clean),
        "metrics": metrics,
        "unconditional_base_rate": base_rate,
        "unconditional_base_brier": base_brier,
        "calibration_available": not block_reasons,
        "trading_activation": False,
        "status": "validated_shadow" if not block_reasons else "shadow_rejected",
        "activation_block_reasons": block_reasons,
        "outcome_model": asdict(outcome_model),
        "expected_r_shadow_model": asdict(expected_r_model),
        "hierarchical_outcome_artifact": build_hierarchical_outcome_artifact(
            clean,
            target_field=primary_target,
            min_clean_sample=min_clean_sample,
        ),
        "separate_entry_management_models": build_entry_and_management_shadow_artifacts(
            clean,
            min_clean_sample=min_clean_sample,
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Even a validated artifact remains shadow-only until an explicit future
    # activation process binds it to matching live feature/schema versions.
    return artifact


def artifact_compatible(
    artifact: Mapping[str, Any],
    *,
    engine_version: str,
    decision_schema_version: str,
    feature_version: str = FEATURE_LINEAGE_VERSION,
    taxonomy_version: str = SETUP_TAXONOMY_VERSION,
) -> bool:
    return bool(
        artifact.get("calibration_available")
        and artifact.get("engine_version") == engine_version
        and artifact.get("decision_schema_version") == decision_schema_version
        and artifact.get("feature_version") == feature_version
        and artifact.get("taxonomy_version") == taxonomy_version
        and artifact.get("calibration_contract_version") == CALIBRATION_CONTRACT_VERSION
    )


def write_calibration_reports(artifact: Mapping[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(dict(artifact), indent=2, sort_keys=True), encoding="utf-8")
    metrics = artifact.get("metrics") if isinstance(artifact.get("metrics"), Mapping) else {}
    lines = [
        "# PO3 shadow calibration report",
        "",
        f"- Status: `{artifact.get('status', 'unknown')}`",
        f"- Method: `{artifact.get('method', 'unavailable')}`",
        f"- Calibration available: `{bool(artifact.get('calibration_available'))}`",
        f"- Trading activation: `{bool(artifact.get('trading_activation'))}`",
        f"- Brier score: `{metrics.get('brier_score', 'n/a')}`",
        f"- Log loss: `{metrics.get('log_loss', 'n/a')}`",
        f"- ECE: `{metrics.get('expected_calibration_error', 'n/a')}`",
        "",
        "## Activation blocks",
        "",
    ]
    blocks = list(artifact.get("activation_block_reasons") or [])
    lines.extend(f"- {reason}" for reason in blocks or ["none; artifact remains shadow-only by contract"])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
