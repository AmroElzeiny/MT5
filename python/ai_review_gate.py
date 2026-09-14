"""Session-aware AI review gate: market monitoring is not AI invocation.

MT5 scans every symbol on its own timer and sends a request whenever a
candidate group survives the deterministic funnel.  Before this gate every such
scan paid for a full analyst + critic panel even when nothing that could change
the model's answer had changed since the last authoritative decision.

The gate answers one question per live request, before any provider call:
"could a fresh AI review of this request produce a different executable
outcome than the authoritative decision already made for the same state?"

It only ever answers "no" under a conjunction that was measured on the live
archive (docs: AI_CALL_EFFICIENCY_IMPLEMENTATION_REPORT.md):

* the previous authoritative decision for the same account/symbol/inputs was a
  full structured AI decision that did NOT approve any candidate;
* every candidate assessed then was *decisively* non-approving -- its
  llm_quality_score sat at least ``decisive_margin`` below its family threshold.
  Measured on 2,090 archived live decisions: 0 of 250 decisive non-approvals
  flipped to an approval on the next unchanged review, while borderline ones
  flipped 1.7%-18% of the time depending on model and margin;
* nothing in the decision-state fingerprint changed: the same candidate
  identities (setup lineage, branch, taxonomy), the same PO3/FVG/obstacle/
  target/trigger states, MQL's own regime labels, the session and killzone
  MQL reports for "now", entry/SL/TP drift within ``price_tolerance_r``, the
  same runtime/decision input hashes, contract versions and provider identity;
* the prior decision is younger than its session-aware TTL.

Anything else -- a missing or malformed review context, an unknown prior, a
prior approval, any change, an expired TTL, a clock regression -- is a CALL.
When the gate is uncertain it calls the AI.  A reuse never creates trading
authority: the response is a RULE_ONLY_NON_TRADING envelope bound to the
current request identity, and approvals are never reused.

Session authority stays in MQL (PO3.mqh ``SessionCodeAt``); MQL sends the
session and killzone codes it computed for the request time in
``ai_review_context``.  Python does not re-derive session boundaries.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


REVIEW_GATE_VERSION = "20260914_ai_review_gate_v1"
# Must equal the MQL constant AI_REVIEW_CONTEXT_VERSION (Config.mqh).  A
# governance test pins both sides.
REVIEW_CONTEXT_VERSION = "20260914_ai_review_context_v1"
FINGERPRINT_VERSION = "20260914_decision_state_fingerprint_v1"
REVIEW_STATE_SCHEMA_VERSION = "20260914_ai_review_state_v1"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
REVIEW_GATE_MODES = (MODE_OFF, MODE_SHADOW, MODE_ENFORCE)

ACTION_CALL = "CALL"
ACTION_REUSE = "REUSE"

# Request-level classification vocabulary shared with the replay tool.
CATEGORY_REQUIRED_NEW_DECISION = "REQUIRED_NEW_DECISION"
CATEGORY_SCHEDULED_REVIEW_WITH_MEANINGFUL_CHANGE = "SCHEDULED_REVIEW_WITH_MEANINGFUL_CHANGE"
CATEGORY_UNCHANGED_STATE = "UNCHANGED_STATE"
CATEGORY_PREDETERMINED_OUTCOME = "PREDETERMINED_OUTCOME"
CATEGORY_POINTLESS_ADJUDICATION = "POINTLESS_ADJUDICATION"
CATEGORY_REPAIR = "REPAIR"
CATEGORY_QA_OR_NON_PRODUCTION = "QA_OR_NON_PRODUCTION"
CATEGORY_OTHER_REQUIRED = "OTHER_REQUIRED"
CATEGORY_UNKNOWN = "UNKNOWN"
REQUEST_CATEGORIES = (
    CATEGORY_REQUIRED_NEW_DECISION,
    CATEGORY_SCHEDULED_REVIEW_WITH_MEANINGFUL_CHANGE,
    CATEGORY_UNCHANGED_STATE,
    CATEGORY_PREDETERMINED_OUTCOME,
    CATEGORY_POINTLESS_ADJUDICATION,
    CATEGORY_REPAIR,
    CATEGORY_QA_OR_NON_PRODUCTION,
    CATEGORY_OTHER_REQUIRED,
    CATEGORY_UNKNOWN,
)

# Session codes exactly as PO3.mqh ``_SessionCode`` emits them.
SESSION_ASIA = "ASIA"
SESSION_LONDON = "LON"
SESSION_NEW_YORK = "NY"
SESSION_OFF_HOURS = "OFF"
SESSION_CODES = (SESSION_ASIA, SESSION_LONDON, SESSION_NEW_YORK, SESSION_OFF_HOURS)
KILLZONE_CODES = ("K", "NK")

REUSE_DECISION_SOURCE = "ai_review_gate_reuse"
REUSE_REJECTION_CODE = "ai_review_reused_prior_non_approval"
REUSE_NARRATIVE_STATE = "ai_review_reused_prior_decision"

# Decision sources that are authoritative full-structured AI outcomes.  Integrity
# failures, cache replays and deterministic rejections never become a prior.
AUTHORITATIVE_DECISION_SOURCES = frozenset({"ai_approved", "ai_rejected", "ai_abstained"})
FULL_STRUCTURED_TIER = "FULL_STRUCTURED"

# Rejection codes that prove the decision is a local integrity outcome rather
# than a model judgement about the market.  A decision carrying any of them is
# never reused.
_INTEGRITY_REJECTION_CODES = frozenset(
    {
        "candidate_hash_mismatch",
        "ai_quality_schema_incomplete",
        "degraded_ai_response_non_trading",
        "decision_integrity_failure",
        "request_identity_mismatch",
        "structured_response_invalid",
        "provider_transport_error",
        "local_pipeline_error",
    }
)

# Every component below is either shown to the model by the evidence envelope
# (decision_evidence._candidate_evidence / sequence / market_regime) or is the
# deterministic source of a target or trigger projection the model reads.
# Scheduler-only diagnostics (portfolio concentration, runner/bucket/subtype
# policy flags) are deliberately absent: they never reach the model, so they
# cannot change its answer, and the portfolio counts move on almost every scan
# (measured: 880 of 1,655 change events on the live archive were those counts).
_CANDIDATE_DISCRETE_FIELDS = (
    "setup_taxonomy_enum",
    "setup_family",
    "entry_branch",
    "po3_state",
    "structure_type",
    "fvg_mitigation_state",
    "fvg_execution_class",
    "fvg_touched",
    "fvg_mid_mitigated",
    "fvg_fully_filled",
    "fvg_invalidated",
    "fvg_entry_invalid",
    "fvg_structure_invalidated",
    "entry_trigger_phase",
    "source_context_tier",
    "ote_state",
    "obstacle_kind",
    "obstacle_tf",
    "target_arbitration_required",
    "liquidity_target_blocked_by_obstacle",
    "liquidity_target_valid_structurally",
    "tp_model",
    "target_source",
    "target_model",
)
_CANDIDATE_REGIME_FIELDS = ("volatility_profile", "regime_profile")
_REQUEST_PO3_FIELDS = (
    "has_sweep",
    "has_displacement",
    "has_bos",
    "has_follow_through",
    "developing_bos",
    "htf_bos",
    "htf_mss",
    "htf_choch",
    "ltf_bos",
    "ltf_mss",
    "ltf_choch",
    "context_tier",
    "po3_state",
    "structure_type",
    "sweep_side",
    "t_sweep",
    "t_disp",
    "t_bos",
    "liquidity_kind",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "nonfinite"
        return repr(round(value, 10))
    return str(value).strip()


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    number = _finite_float(value)
    if number is None:
        return None
    return int(number)


def _canonical_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _news_risk_band(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return "unknown"
    if number <= 0.0:
        return "none"
    if number <= 0.5:
        return "low"
    return "high"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewGateConfig:
    # enforce by default: the historical replay (2,168 live requests) lost 0 of
    # 44 actionable approvals and delayed none.  AI_REVIEW_GATE_MODE=shadow keeps
    # every call while logging what would have been reused; off disables it.
    mode: str = MODE_ENFORCE
    cadence_minutes: Mapping[str, float] = field(
        default_factory=lambda: {
            SESSION_ASIA: 30.0,
            SESSION_LONDON: 15.0,
            SESSION_NEW_YORK: 7.0,
            SESSION_OFF_HOURS: 30.0,
        }
    )
    decisive_margin: float = 2.0
    decisive_ttl_cadences: float = 1.0
    max_reuse_minutes: float = 60.0
    price_tolerance_r: float = 0.10
    defer_borderline: bool = False
    state_file: Path | None = None
    max_records: int = 2000
    warnings: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.mode in (MODE_SHADOW, MODE_ENFORCE)

    @property
    def enforcing(self) -> bool:
        return self.mode == MODE_ENFORCE

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        resolve_path: Callable[[str], Path] | None = None,
    ) -> "ReviewGateConfig":
        env = os.environ if env is None else env
        warnings: list[str] = []

        def _float(name: str, default: float, low: float, high: float) -> float:
            raw = env.get(name)
            if raw is None or str(raw).strip() == "":
                return default
            number = _finite_float(str(raw).strip())
            if number is None:
                warnings.append(f"{name}=invalid_float")
                return default
            if number < low or number > high:
                warnings.append(f"{name}=out_of_range")
                return default
            return number

        def _bool(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None or str(raw).strip() == "":
                return default
            text = str(raw).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            warnings.append(f"{name}=invalid_bool")
            return default

        raw_mode = str(env.get("AI_REVIEW_GATE_MODE") or "").strip().lower()
        if not raw_mode:
            mode = cls.mode
        elif raw_mode in REVIEW_GATE_MODES:
            mode = raw_mode
        else:
            # An unreadable mode must never enable suppression.
            warnings.append("AI_REVIEW_GATE_MODE=invalid_value_forced_off")
            mode = MODE_OFF
        cadence = {
            SESSION_ASIA: _float("AI_REVIEW_CADENCE_ASIA_MIN", 30.0, 1.0, 240.0),
            SESSION_LONDON: _float("AI_REVIEW_CADENCE_LONDON_MIN", 15.0, 1.0, 240.0),
            SESSION_NEW_YORK: _float("AI_REVIEW_CADENCE_NEW_YORK_MIN", 7.0, 1.0, 240.0),
            SESSION_OFF_HOURS: _float("AI_REVIEW_CADENCE_OFF_HOURS_MIN", 30.0, 1.0, 240.0),
        }
        state_raw = str(env.get("AI_REVIEW_STATE_FILE") or "").strip() or "data/ai_review_state.json"
        state_file = resolve_path(state_raw) if resolve_path is not None else Path(state_raw)
        return cls(
            mode=mode,
            cadence_minutes=cadence,
            decisive_margin=_float("AI_REVIEW_DECISIVE_MARGIN", 2.0, 0.5, 10.0),
            decisive_ttl_cadences=_float("AI_REVIEW_DECISIVE_TTL_CADENCES", 1.0, 0.0, 12.0),
            max_reuse_minutes=_float("AI_REVIEW_MAX_REUSE_MINUTES", 60.0, 0.0, 720.0),
            price_tolerance_r=_float("AI_REVIEW_PRICE_TOLERANCE_R", 0.10, 0.0, 1.0),
            defer_borderline=_bool("AI_REVIEW_DEFER_BORDERLINE", False),
            state_file=state_file,
            max_records=int(_float("AI_REVIEW_STATE_MAX_RECORDS", 2000.0, 10.0, 100000.0)),
            warnings=tuple(warnings),
        )

    def safe_log_dict(self) -> dict[str, Any]:
        return {
            "review_gate_version": REVIEW_GATE_VERSION,
            "mode": self.mode,
            "cadence_minutes": dict(self.cadence_minutes),
            "decisive_margin": self.decisive_margin,
            "decisive_ttl_cadences": self.decisive_ttl_cadences,
            "max_reuse_minutes": self.max_reuse_minutes,
            "price_tolerance_r": self.price_tolerance_r,
            "defer_borderline": self.defer_borderline,
            "state_file": str(self.state_file) if self.state_file is not None else "",
            "max_records": self.max_records,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Review context (produced by MQL)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewContext:
    server_time: int
    session_code: str
    killzone_code: str
    scan_interval_min: float


def extract_review_context(payload: Mapping[str, Any]) -> tuple[ReviewContext | None, str]:
    """Validate the MQL-authored session context; any doubt returns None."""

    raw = payload.get("ai_review_context")
    if not isinstance(raw, Mapping):
        return None, "review_context_missing"
    if str(raw.get("context_version") or "") != REVIEW_CONTEXT_VERSION:
        return None, "review_context_version_mismatch"
    session_code = str(raw.get("session_code") or "").strip().upper()
    if session_code not in SESSION_CODES:
        return None, "review_context_session_invalid"
    killzone_code = str(raw.get("killzone_code") or "").strip().upper()
    if killzone_code not in KILLZONE_CODES:
        return None, "review_context_killzone_invalid"
    server_time = _int_or_none(raw.get("server_time"))
    if server_time is None or server_time <= 0:
        return None, "review_context_time_invalid"
    request_time = _int_or_none(payload.get("request_created_sim_time"))
    # The context must describe the request itself, not some other moment.
    if request_time is None or abs(request_time - server_time) > 120:
        return None, "review_context_time_mismatch"
    scan_interval = _finite_float(raw.get("scan_interval_min"))
    return (
        ReviewContext(
            server_time=server_time,
            session_code=session_code,
            killzone_code=killzone_code,
            scan_interval_min=float(scan_interval) if scan_interval is not None else 0.0,
        ),
        "",
    )


# ---------------------------------------------------------------------------
# Decision-state fingerprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateState:
    semantic_id: str
    candidate_index: int
    candidate_id: str
    discrete: tuple[tuple[str, str], ...]
    regime: tuple[tuple[str, str], ...]
    prices: tuple[float, float, float, float]
    risk: float

    def as_record(self) -> dict[str, Any]:
        return {
            "semantic_id": self.semantic_id,
            "candidate_index": self.candidate_index,
            "candidate_id": self.candidate_id,
            "discrete": [list(item) for item in self.discrete],
            "regime": [list(item) for item in self.regime],
            "prices": list(self.prices),
            "risk": self.risk,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CandidateState":
        prices = [float(value) for value in record.get("prices") or []]
        if len(prices) != 4 or not all(math.isfinite(value) for value in prices):
            raise ValueError("review_state_candidate_prices_invalid")
        risk = float(record.get("risk"))
        if not math.isfinite(risk):
            raise ValueError("review_state_candidate_risk_invalid")
        return cls(
            semantic_id=str(record["semantic_id"]),
            candidate_index=int(record["candidate_index"]),
            candidate_id=str(record.get("candidate_id") or ""),
            discrete=tuple((str(k), str(v)) for k, v in record.get("discrete") or []),
            regime=tuple((str(k), str(v)) for k, v in record.get("regime") or []),
            prices=(prices[0], prices[1], prices[2], prices[3]),
            risk=risk,
        )


def candidate_semantic_id(candidate: Mapping[str, Any]) -> str:
    """Setup lineage identity that survives price refreshes.

    ``setup_id`` is ``symbol|t_sweep|t_disp|t_bos|branch|fvg_mid`` (MQL
    ``_NarrativeIdFromPlan``).  The trailing entry price of ``candidate_id`` is
    deliberately excluded: entry drift is judged against a tolerance instead.
    """

    setup_id = _text(candidate.get("setup_id"))
    if not setup_id:
        parts = _text(candidate.get("candidate_id")).split("|")
        setup_id = "|".join(parts[:-2]) if len(parts) > 2 else _text(candidate.get("candidate_id"))
    branch = _text(candidate.get("entry_branch") or candidate.get("entry_model"))
    taxonomy = _text(candidate.get("setup_taxonomy_enum"))
    if not setup_id or not branch or not taxonomy:
        return ""
    return f"{setup_id}#{branch}#{taxonomy}"


def _candidate_state(candidate: Mapping[str, Any]) -> CandidateState | None:
    semantic_id = candidate_semantic_id(candidate)
    if not semantic_id:
        return None
    entry = _finite_float(candidate.get("entry_est", candidate.get("entry")))
    sl = _finite_float(candidate.get("sl"))
    tp1 = _finite_float(candidate.get("tp1"))
    tp2 = _finite_float(candidate.get("tp2"))
    index = _int_or_none(candidate.get("candidate_index"))
    if entry is None or sl is None or tp2 is None or index is None or entry <= 0.0 or sl <= 0.0 or tp2 <= 0.0:
        return None
    risk = abs(entry - sl)
    if risk <= 0.0:
        return None
    policy_bucket = _text(candidate.get("policy_bucket"))
    regime = [(name, _text(candidate.get(name))) for name in _CANDIDATE_REGIME_FIELDS]
    regime.append(("policy_bucket_regime", policy_bucket.split("|")[0] if policy_bucket else ""))
    return CandidateState(
        semantic_id=semantic_id,
        candidate_index=index,
        candidate_id=_text(candidate.get("candidate_id")),
        discrete=tuple((name, _text(candidate.get(name))) for name in _CANDIDATE_DISCRETE_FIELDS),
        regime=tuple(regime),
        prices=(entry, sl, tp1 if tp1 is not None else 0.0, tp2),
        risk=risk,
    )


@dataclass(frozen=True)
class DecisionStateFingerprint:
    scope_key: str
    account: str
    symbol: str
    server_time: int
    session_code: str
    killzone_code: str
    identity: tuple[tuple[str, str], ...]
    request_state: tuple[tuple[str, str], ...]
    candidates: tuple[CandidateState, ...]

    def digest(self) -> str:
        return _canonical_hash(
            {
                "fingerprint_version": FINGERPRINT_VERSION,
                "scope_key": self.scope_key,
                "session_code": self.session_code,
                "killzone_code": self.killzone_code,
                "identity": [list(item) for item in self.identity],
                "request_state": [list(item) for item in self.request_state],
                "candidates": [
                    {
                        "semantic_id": c.semantic_id,
                        "discrete": [list(item) for item in c.discrete],
                        "regime": [list(item) for item in c.regime],
                    }
                    for c in sorted(self.candidates, key=lambda c: c.semantic_id)
                ],
            }
        )

    def candidate(self, semantic_id: str) -> CandidateState | None:
        return next((c for c in self.candidates if c.semantic_id == semantic_id), None)


def _account_from_request_id(request_id: str) -> str:
    head = str(request_id or "").split("_", 1)[0]
    return head if head.isdigit() else ""


def build_fingerprint(
    payload: Mapping[str, Any],
    context: ReviewContext,
    *,
    python_identity: Mapping[str, Any],
) -> tuple[DecisionStateFingerprint | None, str]:
    symbol = _text(payload.get("symbol"))
    account = _account_from_request_id(_text(payload.get("id")))
    workload = _text(payload.get("workload_mode"))
    if not symbol or not account:
        return None, "fingerprint_scope_unresolved"
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list) or not candidates_raw:
        return None, "fingerprint_candidates_missing"
    states: list[CandidateState] = []
    seen: set[str] = set()
    for candidate in candidates_raw:
        if not isinstance(candidate, Mapping):
            return None, "fingerprint_candidate_not_object"
        state = _candidate_state(candidate)
        if state is None:
            return None, "fingerprint_candidate_incomplete"
        if state.semantic_id in seen:
            # Two candidates collapsing onto one lineage identity would make
            # per-candidate comparison ambiguous; call the AI instead.
            return None, "fingerprint_candidate_identity_collision"
        seen.add(state.semantic_id)
        states.append(state)
    po3 = payload.get("po3") if isinstance(payload.get("po3"), Mapping) else {}
    regime = payload.get("regime") if isinstance(payload.get("regime"), Mapping) else {}
    request_state: list[tuple[str, str]] = [("is_buy", _text(payload.get("is_buy")))]
    request_state.extend((f"po3.{name}", _text(po3.get(name))) for name in _REQUEST_PO3_FIELDS)
    request_state.append(("regime.news_risk_band", _news_risk_band(regime.get("news_risk"))))
    identity: list[tuple[str, str]] = [
        ("workload_mode", workload),
        ("runtime_input_hash", _text(payload.get("runtime_input_hash"))),
        ("decision_input_hash", _text(payload.get("decision_input_hash"))),
        ("contract_manifest_hash", _text(payload.get("contract_manifest_hash"))),
    ]
    identity.extend((f"python.{key}", _text(value)) for key, value in sorted(python_identity.items()))
    scope_key = _canonical_hash({"account": account, "symbol": symbol, "workload_mode": workload})
    return (
        DecisionStateFingerprint(
            scope_key=scope_key,
            account=account,
            symbol=symbol,
            server_time=context.server_time,
            session_code=context.session_code,
            killzone_code=context.killzone_code,
            identity=tuple(identity),
            request_state=tuple(request_state),
            candidates=tuple(sorted(states, key=lambda c: c.semantic_id)),
        ),
        "",
    )


# ---------------------------------------------------------------------------
# Authoritative decision record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateVerdict:
    semantic_id: str
    decision_state: str
    llm_quality_score: float
    family_threshold: float

    @property
    def quality_gap(self) -> float:
        return self.family_threshold - self.llm_quality_score


@dataclass(frozen=True)
class ReviewRecord:
    request_id: str
    request_identity_hash: str
    server_time: int
    session_code: str
    killzone_code: str
    decision_state: str
    python_final_allow: bool
    identity: tuple[tuple[str, str], ...]
    request_state: tuple[tuple[str, str], ...]
    candidates: tuple[CandidateState, ...]
    verdicts: tuple[CandidateVerdict, ...]
    recorded_at_utc: float

    def verdict(self, semantic_id: str) -> CandidateVerdict | None:
        return next((v for v in self.verdicts if v.semantic_id == semantic_id), None)

    def candidate(self, semantic_id: str) -> CandidateState | None:
        return next((c for c in self.candidates if c.semantic_id == semantic_id), None)

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": REVIEW_STATE_SCHEMA_VERSION,
            "fingerprint_version": FINGERPRINT_VERSION,
            "request_id": self.request_id,
            "request_identity_hash": self.request_identity_hash,
            "server_time": self.server_time,
            "session_code": self.session_code,
            "killzone_code": self.killzone_code,
            "decision_state": self.decision_state,
            "python_final_allow": self.python_final_allow,
            "identity": [list(item) for item in self.identity],
            "request_state": [list(item) for item in self.request_state],
            "candidates": [c.as_record() for c in self.candidates],
            "verdicts": [
                {
                    "semantic_id": v.semantic_id,
                    "decision_state": v.decision_state,
                    "llm_quality_score": v.llm_quality_score,
                    "family_threshold": v.family_threshold,
                }
                for v in self.verdicts
            ],
            "recorded_at_utc": self.recorded_at_utc,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ReviewRecord":
        if record.get("schema_version") != REVIEW_STATE_SCHEMA_VERSION:
            raise ValueError("review_state_schema_mismatch")
        if record.get("fingerprint_version") != FINGERPRINT_VERSION:
            raise ValueError("review_state_fingerprint_version_mismatch")
        verdicts = []
        for row in record.get("verdicts") or []:
            quality = float(row["llm_quality_score"])
            threshold = float(row["family_threshold"])
            if not (math.isfinite(quality) and math.isfinite(threshold)):
                raise ValueError("review_state_verdict_invalid")
            verdicts.append(
                CandidateVerdict(
                    semantic_id=str(row["semantic_id"]),
                    decision_state=str(row["decision_state"]).upper(),
                    llm_quality_score=quality,
                    family_threshold=threshold,
                )
            )
        return cls(
            request_id=str(record["request_id"]),
            request_identity_hash=str(record.get("request_identity_hash") or ""),
            server_time=int(record["server_time"]),
            session_code=str(record["session_code"]),
            killzone_code=str(record["killzone_code"]),
            decision_state=str(record["decision_state"]).upper(),
            python_final_allow=bool(record["python_final_allow"]),
            identity=tuple((str(k), str(v)) for k, v in record.get("identity") or []),
            request_state=tuple((str(k), str(v)) for k, v in record.get("request_state") or []),
            candidates=tuple(CandidateState.from_record(row) for row in record.get("candidates") or []),
            verdicts=tuple(verdicts),
            recorded_at_utc=float(record.get("recorded_at_utc") or 0.0),
        )


class ReviewStateStore:
    """Minimal persisted state: one authoritative record per scope.

    Thread-safe within the gate process (the request pool is a thread pool and
    the single-instance guard keeps one gate per bus).  Writes are atomic
    (temp file + ``os.replace``).  A corrupt or foreign file is never trusted:
    it is ignored and the gate calls the AI until fresh decisions are recorded.
    """

    def __init__(self, path: Path | None, *, max_records: int = 2000, logger: Callable[[str], None] | None = None) -> None:
        self.path = path
        self.max_records = max(10, int(max_records))
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._logger = logger

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)

    def _load_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path is None or not self.path.is_file():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, Mapping) or document.get("schema_version") != REVIEW_STATE_SCHEMA_VERSION:
                raise ValueError("review_state_file_schema_mismatch")
            records = document.get("records")
            if not isinstance(records, Mapping):
                raise ValueError("review_state_file_records_invalid")
            self._records = {str(k): dict(v) for k, v in records.items() if isinstance(v, Mapping)}
        except Exception as exc:  # corrupt state must never suppress a call
            self._records = {}
            self._log(
                "[ai_review_gate] state_load_failed"
                f" path={self.path} error={type(exc).__name__}:{exc} action=ignore_state_call_ai"
            )

    def get(self, scope_key: str) -> ReviewRecord | None:
        with self._lock:
            self._load_locked()
            raw = self._records.get(scope_key)
        if raw is None:
            return None
        try:
            return ReviewRecord.from_record(raw)
        except Exception as exc:
            self._log(
                "[ai_review_gate] state_record_invalid"
                f" scope={scope_key[:12]} error={type(exc).__name__}:{exc} action=call_ai"
            )
            return None

    def put(self, scope_key: str, record: ReviewRecord) -> bool:
        with self._lock:
            self._load_locked()
            existing = self._records.get(scope_key)
            if existing is not None:
                try:
                    if int(existing.get("server_time") or 0) > record.server_time:
                        # A late, older decision never overwrites a newer one.
                        return False
                except (TypeError, ValueError):
                    pass
            self._records[scope_key] = record.as_record()
            if len(self._records) > self.max_records:
                ordered = sorted(
                    self._records.items(), key=lambda item: int(item[1].get("server_time") or 0)
                )
                self._records = dict(ordered[-self.max_records:])
            self._persist_locked()
            return True

    def _persist_locked(self) -> None:
        if self.path is None:
            return
        document = {
            "schema_version": REVIEW_STATE_SCHEMA_VERSION,
            "review_gate_version": REVIEW_GATE_VERSION,
            "updated_at_utc": time.time(),
            "records": self._records,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except Exception as exc:
            # A failed persist only loses future reuse after a restart; the
            # in-memory record is still correct for this process.
            self._log(f"[ai_review_gate] state_persist_failed path={self.path} error={type(exc).__name__}:{exc}")

    def clear(self) -> None:
        with self._lock:
            self._records = {}
            self._loaded = True
            self._persist_locked()


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewVerdict:
    mode: str
    action: str
    category: str
    reason: str
    events: tuple[str, ...] = ()
    session_code: str = ""
    killzone_code: str = ""
    cadence_minutes: float = 0.0
    ttl_minutes: float = 0.0
    prior_request_id: str = ""
    prior_decision_state: str = ""
    prior_age_minutes: float | None = None
    prior_decisive: bool | None = None
    fingerprint_digest: str = ""
    fingerprint: DecisionStateFingerprint | None = None

    @property
    def would_reuse(self) -> bool:
        return self.action == ACTION_REUSE

    @property
    def enforced_reuse(self) -> bool:
        return self.action == ACTION_REUSE and self.mode == MODE_ENFORCE

    def as_log_fields(self) -> str:
        age = "none" if self.prior_age_minutes is None else f"{self.prior_age_minutes:.2f}"
        decisive = "none" if self.prior_decisive is None else str(self.prior_decisive).lower()
        return (
            f" mode={self.mode} action={self.action} category={self.category} reason={self.reason}"
            f" events={','.join(self.events) or 'none'}"
            f" session={self.session_code or 'unknown'} killzone={self.killzone_code or 'unknown'}"
            f" cadence_min={self.cadence_minutes:.2f} ttl_min={self.ttl_minutes:.2f}"
            f" prior_request_id={self.prior_request_id or 'none'}"
            f" prior_state={self.prior_decision_state or 'none'} prior_age_min={age}"
            f" prior_decisive={decisive} fingerprint={self.fingerprint_digest[:16] or 'none'}"
        )

    def as_reason_dict(self) -> dict[str, Any]:
        return {
            "review_gate_version": REVIEW_GATE_VERSION,
            "mode": self.mode,
            "action": self.action,
            "category": self.category,
            "reason": self.reason,
            "events": list(self.events),
            "session_code": self.session_code,
            "killzone_code": self.killzone_code,
            "cadence_minutes": self.cadence_minutes,
            "ttl_minutes": self.ttl_minutes,
            "prior_request_id": self.prior_request_id,
            "prior_decision_state": self.prior_decision_state,
            "prior_age_minutes": self.prior_age_minutes,
            "prior_decisive": self.prior_decisive,
            "fingerprint_digest": self.fingerprint_digest,
            "trading_authority": False,
        }


def compare_states(
    fingerprint: DecisionStateFingerprint,
    prior: ReviewRecord,
    *,
    price_tolerance_r: float,
) -> tuple[str, ...]:
    """Named components that changed between the prior decision and now."""

    events: list[str] = []
    if dict(fingerprint.identity) != dict(prior.identity):
        changed = sorted(
            key
            for key in set(dict(fingerprint.identity)) | set(dict(prior.identity))
            if dict(fingerprint.identity).get(key) != dict(prior.identity).get(key)
        )
        events.append("identity:" + "+".join(changed))
    if fingerprint.session_code != prior.session_code:
        events.append("session_transition")
    if fingerprint.killzone_code != prior.killzone_code:
        events.append("killzone_transition")
    if dict(fingerprint.request_state) != dict(prior.request_state):
        changed = sorted(
            key
            for key in set(dict(fingerprint.request_state)) | set(dict(prior.request_state))
            if dict(fingerprint.request_state).get(key) != dict(prior.request_state).get(key)
        )
        events.append("request_state:" + "+".join(changed))
    for candidate in fingerprint.candidates:
        previous = prior.candidate(candidate.semantic_id)
        if previous is None:
            events.append("new_candidate")
            continue
        if candidate.discrete != previous.discrete:
            changed = sorted(
                key for (key, value), (_, old) in zip(candidate.discrete, previous.discrete) if value != old
            )
            events.append("candidate_state:" + "+".join(changed or ["field_set"]))
        if candidate.regime != previous.regime:
            events.append("regime_label")
        drift = max(abs(now - then) for now, then in zip(candidate.prices, previous.prices)) / max(previous.risk, 1e-12)
        if drift > price_tolerance_r:
            events.append("price_drift")
    # Each event name once, in a stable order.
    return tuple(dict.fromkeys(events))


def decisive_non_approval(prior: ReviewRecord, semantic_ids: Iterable[str], margin: float) -> bool:
    ids = list(semantic_ids)
    if not ids or prior.python_final_allow or prior.decision_state == "APPROVE":
        return False
    for semantic_id in ids:
        verdict = prior.verdict(semantic_id)
        if verdict is None:
            return False
        if verdict.decision_state not in ("REJECT", "ABSTAIN"):
            return False
        if verdict.quality_gap < margin:
            return False
    return True


class ReviewGate:
    def __init__(self, config: ReviewGateConfig, *, logger: Callable[[str], None] | None = None, store: ReviewStateStore | None = None) -> None:
        self.config = config
        self._logger = logger
        self.store = store if store is not None else ReviewStateStore(
            config.state_file if config.active else None,
            max_records=config.max_records,
            logger=logger,
        )
        self._counter_lock = threading.Lock()
        self.counters: dict[str, int] = {}

    def _count(self, key: str) -> None:
        with self._counter_lock:
            self.counters[key] = self.counters.get(key, 0) + 1

    def _verdict(self, action: str, category: str, reason: str, **fields: Any) -> ReviewVerdict:
        verdict = ReviewVerdict(mode=self.config.mode, action=action, category=category, reason=reason, **fields)
        self._count(f"{action.lower()}:{reason}")
        return verdict

    def evaluate(
        self,
        payload: Mapping[str, Any],
        *,
        python_identity: Mapping[str, Any],
    ) -> ReviewVerdict:
        if not self.config.active:
            return self._verdict(ACTION_CALL, CATEGORY_OTHER_REQUIRED, "review_gate_off")
        if _text(payload.get("workload_mode")) != "LIVE_FORWARD":
            return self._verdict(ACTION_CALL, CATEGORY_OTHER_REQUIRED, "non_live_workload")
        context, context_error = extract_review_context(payload)
        if context is None:
            return self._verdict(ACTION_CALL, CATEGORY_OTHER_REQUIRED, context_error)
        cadence = float(self.config.cadence_minutes.get(context.session_code, 0.0) or 0.0)
        fingerprint, fingerprint_error = build_fingerprint(payload, context, python_identity=python_identity)
        if fingerprint is None:
            return self._verdict(
                ACTION_CALL, CATEGORY_OTHER_REQUIRED, fingerprint_error,
                session_code=context.session_code, killzone_code=context.killzone_code, cadence_minutes=cadence,
            )
        common = {
            "session_code": context.session_code,
            "killzone_code": context.killzone_code,
            "cadence_minutes": cadence,
            "fingerprint_digest": fingerprint.digest(),
            "fingerprint": fingerprint,
        }
        prior = self.store.get(fingerprint.scope_key)
        if prior is None:
            return self._verdict(ACTION_CALL, CATEGORY_REQUIRED_NEW_DECISION, "no_prior_authoritative_decision", **common)
        age_minutes = (fingerprint.server_time - prior.server_time) / 60.0
        prior_fields = {
            "prior_request_id": prior.request_id,
            "prior_decision_state": prior.decision_state,
            "prior_age_minutes": age_minutes,
        }
        if age_minutes < 0.0:
            return self._verdict(ACTION_CALL, CATEGORY_OTHER_REQUIRED, "clock_regression", **common, **prior_fields)
        if prior.python_final_allow or prior.decision_state == "APPROVE" or any(
            v.decision_state == "APPROVE" for v in prior.verdicts
        ):
            return self._verdict(ACTION_CALL, CATEGORY_REQUIRED_NEW_DECISION, "prior_decision_approved_never_reused", **common, **prior_fields)
        events = compare_states(fingerprint, prior, price_tolerance_r=self.config.price_tolerance_r)
        if events:
            category = (
                CATEGORY_SCHEDULED_REVIEW_WITH_MEANINGFUL_CHANGE
                if age_minutes >= cadence
                else CATEGORY_REQUIRED_NEW_DECISION
            )
            return self._verdict(ACTION_CALL, category, "meaningful_change", events=events, **common, **prior_fields)
        present = [c.semantic_id for c in fingerprint.candidates]
        decisive = decisive_non_approval(prior, present, self.config.decisive_margin)
        if decisive:
            ttl = cadence * self.config.decisive_ttl_cadences
            if self.config.max_reuse_minutes > 0.0:
                ttl = min(ttl, self.config.max_reuse_minutes)
            if age_minutes < ttl:
                return self._verdict(
                    ACTION_REUSE, CATEGORY_UNCHANGED_STATE, "unchanged_decisive_non_approval_within_ttl",
                    ttl_minutes=ttl, prior_decisive=True, **common, **prior_fields,
                )
            return self._verdict(
                ACTION_CALL, CATEGORY_OTHER_REQUIRED, "decisive_prior_ttl_expired",
                ttl_minutes=ttl, prior_decisive=True, **common, **prior_fields,
            )
        if self.config.defer_borderline:
            ttl = min(cadence, self.config.max_reuse_minutes) if self.config.max_reuse_minutes > 0.0 else cadence
            if age_minutes < ttl:
                return self._verdict(
                    ACTION_REUSE, CATEGORY_UNCHANGED_STATE, "unchanged_borderline_within_session_cadence",
                    ttl_minutes=ttl, prior_decisive=False, **common, **prior_fields,
                )
        return self._verdict(
            ACTION_CALL, CATEGORY_OTHER_REQUIRED, "borderline_prior_requires_fresh_review",
            prior_decisive=False, **common, **prior_fields,
        )

    def record(
        self,
        verdict: ReviewVerdict,
        payload: Mapping[str, Any],
        decision: Mapping[str, Any],
        *,
        family_threshold: Callable[[int], float],
    ) -> tuple[bool, str]:
        """Store an authoritative provider decision as the new prior."""

        if not self.config.active:
            return False, "review_gate_off"
        fingerprint = verdict.fingerprint
        if fingerprint is None:
            return False, "no_fingerprint"
        if verdict.enforced_reuse:
            return False, "reused_decision_is_not_a_new_prior"
        if str(decision.get("decision_quality_tier") or "") != FULL_STRUCTURED_TIER:
            return False, "non_full_structured_decision"
        if str(decision.get("decision_source") or "") not in AUTHORITATIVE_DECISION_SOURCES:
            return False, "non_authoritative_decision_source"
        if not bool(decision.get("mandatory_fields_complete")):
            return False, "mandatory_fields_incomplete"
        codes = {str(code) for code in decision.get("rejection_codes") or []}
        if codes & _INTEGRITY_REJECTION_CODES:
            return False, "integrity_rejection_codes_present"
        assessments = decision.get("candidate_assessments")
        if not isinstance(assessments, list) or not assessments:
            return False, "candidate_assessments_missing"
        # The fingerprint carries the CONFIGURED provider identity (what the
        # next call would use).  The record must carry the identity that
        # actually answered: a decision served by a fallback leg therefore
        # never matches the configured primary and is never reused as if the
        # primary had made it.
        identity = dict(fingerprint.identity)
        actual_provider = _text(decision.get("provider_id"))
        actual_model = _text(decision.get("actual_model_id") or decision.get("model_version"))
        if not actual_provider or not actual_model:
            return False, "decision_provider_identity_missing"
        identity["python.provider_id"] = actual_provider
        identity["python.model_id"] = actual_model
        by_index = {c.candidate_index: c for c in fingerprint.candidates}
        verdicts: list[CandidateVerdict] = []
        for assessment in assessments:
            if not isinstance(assessment, Mapping):
                return False, "candidate_assessment_not_object"
            index = _int_or_none(assessment.get("candidate_index"))
            candidate = by_index.get(index) if index is not None else None
            if candidate is None or _text(assessment.get("candidate_id")) != candidate.candidate_id:
                return False, "candidate_assessment_identity_unbound"
            quality = _finite_float(assessment.get("llm_quality_score"))
            threshold = _finite_float(family_threshold(candidate.candidate_index))
            state = _text(assessment.get("decision_state")).upper()
            if quality is None or threshold is None or state not in ("APPROVE", "REJECT", "ABSTAIN"):
                return False, "candidate_assessment_incomplete"
            verdicts.append(CandidateVerdict(candidate.semantic_id, state, quality, threshold))
        record = ReviewRecord(
            request_id=_text(payload.get("id")),
            request_identity_hash=_text(payload.get("request_identity_hash")),
            server_time=fingerprint.server_time,
            session_code=fingerprint.session_code,
            killzone_code=fingerprint.killzone_code,
            decision_state=_text(decision.get("decision_state")).upper(),
            python_final_allow=bool(decision.get("python_final_allow")),
            identity=tuple(sorted(identity.items())),
            request_state=fingerprint.request_state,
            candidates=fingerprint.candidates,
            verdicts=tuple(verdicts),
            recorded_at_utc=time.time(),
        )
        stored = self.store.put(fingerprint.scope_key, record)
        self._count("record:stored" if stored else "record:older_than_existing")
        return stored, ("stored" if stored else "older_than_existing_record")


def shadow_outcome_equivalent(verdict: ReviewVerdict, decision: Mapping[str, Any]) -> bool | None:
    """Would reusing have produced the same executable outcome as the real call?"""

    if not verdict.would_reuse:
        return None
    return not bool(decision.get("python_final_allow"))


__all__ = [
    "ACTION_CALL",
    "ACTION_REUSE",
    "AUTHORITATIVE_DECISION_SOURCES",
    "CATEGORY_OTHER_REQUIRED",
    "CATEGORY_POINTLESS_ADJUDICATION",
    "CATEGORY_PREDETERMINED_OUTCOME",
    "CATEGORY_QA_OR_NON_PRODUCTION",
    "CATEGORY_REPAIR",
    "CATEGORY_REQUIRED_NEW_DECISION",
    "CATEGORY_SCHEDULED_REVIEW_WITH_MEANINGFUL_CHANGE",
    "CATEGORY_UNCHANGED_STATE",
    "CATEGORY_UNKNOWN",
    "CandidateState",
    "CandidateVerdict",
    "DecisionStateFingerprint",
    "FINGERPRINT_VERSION",
    "MODE_ENFORCE",
    "MODE_OFF",
    "MODE_SHADOW",
    "REQUEST_CATEGORIES",
    "REUSE_DECISION_SOURCE",
    "REUSE_NARRATIVE_STATE",
    "REUSE_REJECTION_CODE",
    "REVIEW_CONTEXT_VERSION",
    "REVIEW_GATE_VERSION",
    "REVIEW_STATE_SCHEMA_VERSION",
    "ReviewContext",
    "ReviewGate",
    "ReviewGateConfig",
    "ReviewRecord",
    "ReviewStateStore",
    "ReviewVerdict",
    "SESSION_CODES",
    "build_fingerprint",
    "candidate_semantic_id",
    "compare_states",
    "decisive_non_approval",
    "extract_review_context",
    "shadow_outcome_equivalent",
]
