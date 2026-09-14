"""Historical shadow replay and baseline classification for the AI review gate.

Reads only archived artifacts (never the live queues) and never calls a provider:

* ``<bus>/rejected|completed|stale|...``  archived LIVE_FORWARD request payloads
* ``<bus>/response_debug``                the authoritative decision of each request
* ``<bus>/logs/openai_usage.ndjson``      provider calls per request and role
* ``<bus>/logs/ai_gate.log``              hard pre-gate rejections and repairs
* ``<terminal>/MQL5/Logs/*.log``          consumed sweeps, queued requests and
                                          the MQL watchlist outcome of approvals

The replay drives the production gate (``ai_review_gate.ReviewGate``) request
by request in server-time order per account and symbol, with an in-memory
state store, and compares what the gated system would have done against what
actually happened.

Historical requests pre-date ``ai_review_context``.  For replay only, the
session and killzone MQL would have reported are reconstructed with a port of
PO3.mqh ``_SessionName`` / ``_IsKillZone``; the port is validated against the
session token MQL itself wrote into every candidate's broker comment, and the
match rate is part of the output.  Production never uses this port: live
requests carry the MQL-computed context.

Muse token economics come only from Muse rows at the published OpenCode Go
rates; rows from other models are never repriced as Muse.
"""

from __future__ import annotations

import argparse
import calendar
import collections
import csv
import datetime as _dt
import io
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

PY_ROOT = Path(__file__).resolve().parents[1]
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

import ai_review_gate as gate  # noqa: E402
from bus_cohort_migration import read_json_any_encoding  # noqa: E402

RID_RE = re.compile(r"^([0-9]+)_([0-9]+)_([0-9]+)_([0-9]+)_(.+)_([0-9]+)$")
MUSE_MODEL = "muse-spark-1.3-contributor"
MUSE_INPUT_PER_M = 0.10
MUSE_OUTPUT_PER_M = 0.20
MUSE_CACHED_PER_M = 0.002
ARCHIVE_FOLDERS = ("rejected", "completed", "quarantined", "stale", "timed_out", "shutdown")
TESTER_COHORT_BASES = {"1785715200"}

# Traffic classes (python/docs + ai_gate usage ledger use the same names).
TRADING_DECISION = "TRADING_DECISION"
TRADING_CRITIC = "TRADING_CRITIC"
TRADING_ADJUDICATOR = "TRADING_ADJUDICATOR"
REPAIR = "REPAIR"
SHADOW = "SHADOW"
OTHER = "OTHER"


# ---------------------------------------------------------------------------
# Historical session reconstruction (analysis only; see module docstring)
# ---------------------------------------------------------------------------


def _utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return calendar.timegm((y, mo, d, h, mi, 0))


def _last_sunday(y: int, mo: int) -> int:
    for day in range(calendar.monthrange(y, mo)[1], 0, -1):
        if _dt.date(y, mo, day).weekday() == 6:
            return day
    return calendar.monthrange(y, mo)[1]


def _nth_sunday(y: int, mo: int, n: int) -> int:
    seen = 0
    for day in range(1, calendar.monthrange(y, mo)[1] + 1):
        if _dt.date(y, mo, day).weekday() == 6:
            seen += 1
            if seen == n:
                return day
    return 1


def _market_offset_hours(market: str, utc_time: int) -> int:
    year = time.gmtime(utc_time).tm_year
    if market == "LONDON":
        start = _utc(year, 3, _last_sunday(year, 3), 1)
        end = _utc(year, 10, _last_sunday(year, 10), 1)
        return 1 if start <= utc_time < end else 0
    if market == "NEW_YORK":
        start = _utc(year, 3, _nth_sunday(year, 3, 2), 7)
        end = _utc(year, 11, _nth_sunday(year, 11, 1), 6)
        return -4 if start <= utc_time < end else -5
    return 0


def _in_market_window(market: str, utc_time: int, sh: int, smin: int, eh: int, emin: int) -> bool:
    for back in (0, 86400):
        tm = time.gmtime(utc_time - back)
        offset = _market_offset_hours(market, _utc(tm.tm_year, tm.tm_mon, tm.tm_mday, 12))
        base = _utc(tm.tm_year, tm.tm_mon, tm.tm_mday)
        start = base + (sh - offset) * 3600 + smin * 60
        end = base + (eh - offset) * 3600 + emin * 60
        if end <= start:
            end += 86400
        if start <= utc_time < end:
            return True
    return False


# Config.mqh / preset defaults; historical requests do not carry session hours.
HISTORICAL_SESSION_HOURS = {"ASIA": (0, 10), "LONDON": (10, 17), "NEW_YORK": (16, 0)}


def historical_session_code(server_time: int, offset_hours: int) -> str:
    utc_time = server_time - offset_hours * 3600
    for market, code in (("ASIA", "ASIA"), ("LONDON", "LON"), ("NEW_YORK", "NY")):
        sh, eh = HISTORICAL_SESSION_HOURS[market]
        if _in_market_window(market, utc_time, sh, 0, eh, 0):
            return code
    return "OFF"


def historical_killzone_code(server_time: int, runtime_inputs: Mapping[str, Any], offset_hours: int) -> str:
    utc_time = server_time - offset_hours * 3600

    def _i(name: str, default: int) -> int:
        try:
            return int(runtime_inputs.get(name, default))
        except (TypeError, ValueError):
            return default

    if bool(runtime_inputs.get("enable_asia_killzone")) and _in_market_window(
        "ASIA", utc_time, _i("asia_killzone_start_hour", 0), _i("asia_killzone_start_minute", 0),
        _i("asia_killzone_end_hour", 2), _i("asia_killzone_end_minute", 0),
    ):
        return "K"
    if _in_market_window(
        "LONDON", utc_time, _i("london_killzone_start_hour", 10), _i("london_killzone_start_minute", 0),
        _i("london_killzone_end_hour", 11), _i("london_killzone_end_minute", 0),
    ):
        return "K"
    if _in_market_window(
        "NEW_YORK", utc_time, _i("newyork_killzone_start_hour", 16), _i("newyork_killzone_start_minute", 0),
        _i("newyork_killzone_end_hour", 17), _i("newyork_killzone_end_minute", 0),
    ):
        return "K"
    return "NK"


# ---------------------------------------------------------------------------
# Artifact loaders
# ---------------------------------------------------------------------------


def traffic_class_for_operation(operation: str, request_id: str) -> str:
    if "__shadow_repeat_" in (request_id or ""):
        return SHADOW
    op = (operation or "").replace("trade_gate.provider_neutral_", "")
    if op == "analyst":
        return TRADING_DECISION
    if op == "critic":
        return TRADING_CRITIC
    if op == "adjudicator":
        return TRADING_ADJUDICATOR
    if "repair" in op:
        return REPAIR
    return OTHER


def muse_cost(row: Mapping[str, Any]) -> float:
    inp = int(row.get("input_tokens") or 0)
    cached = int(row.get("cached_input_tokens") or 0)
    out = int(row.get("output_tokens") or 0)
    raw = row.get("raw_usage") if isinstance(row.get("raw_usage"), Mapping) else {}
    details = raw.get("input_tokens_details") if isinstance(raw.get("input_tokens_details"), Mapping) else {}
    written = int(details.get("cache_write_tokens") or 0)
    uncached = max(0, inp - cached - written)
    return (uncached * MUSE_INPUT_PER_M + cached * MUSE_CACHED_PER_M + written * MUSE_INPUT_PER_M + out * MUSE_OUTPUT_PER_M) / 1e6


def load_usage(path: Path) -> dict[str, dict[str, Any]]:
    usage: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"calls": collections.Counter(), "muse_calls": collections.Counter(), "muse_cost": 0.0,
                 "muse_cost_by_class": collections.Counter(), "models": collections.Counter(), "first_ts": None}
    )
    if not path.is_file():
        return usage
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            request_id = str(row.get("request_id") or "")
            base = request_id.split("__shadow_repeat_")[0]
            item = usage[base]
            klass = traffic_class_for_operation(str(row.get("operation") or ""), request_id)
            item["calls"][klass] += 1
            model = str(row.get("model") or "")
            item["models"][model] += 1
            ts = str(row.get("ts_utc") or "")
            if ts and (item["first_ts"] is None or ts < item["first_ts"]):
                item["first_ts"] = ts
            if model == MUSE_MODEL:
                cost = muse_cost(row)
                item["muse_cost"] += cost
                item["muse_calls"][klass] += 1
                item["muse_cost_by_class"][klass] += cost
    return usage


def load_gate_log(path: Path) -> tuple[dict[str, str], collections.Counter]:
    hard_rejected: dict[str, str] = {}
    repairs: collections.Counter = collections.Counter()
    if not path.is_file():
        return hard_rejected, repairs
    rid_re = re.compile(r"request_id=(\S+)")
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("[hard_pre_gate]") and "passed=false" in line:
                match = rid_re.search(line)
                if match:
                    source = re.search(r"decision_source=(\S+)", line)
                    hard_rejected[match.group(1)] = source.group(1) if source else "hard_pre_gate"
            elif line.startswith(("[critic_evidence_reference_repair]", "[evidence_reference_repair]", "[candidate_mapping_repair]")):
                if "result=" in line:
                    continue
                match = rid_re.search(line)
                if match:
                    repairs[match.group(1)] += 1
    return hard_rejected, repairs


def load_journals(journal_dir: Path | None) -> dict[str, Any]:
    """Consumed sweeps per EA process, queued requests and approval outcomes."""

    result = {
        "consumed_all_candidate_requests": set(),
        "approval_outcome": {},
        "queued": 0,
        "consumed_events": 0,
        "journal_files": [],
    }
    if journal_dir is None or not journal_dir.is_dir():
        return result
    consumed_re = re.compile(r"\] (\S+) sweep marked consumed after filled entry sweep=(\d{4}\.\d\d\.\d\d \d\d:\d\d)")
    queued_re = re.compile(r"\] (\S+) AI request queued candidates=\d+ req_id=(\S+)")
    advisory_re = re.compile(r"\] (\S+) AI advisory req_id=(\S+) .*?final_allow=(true|false)")
    added_re = re.compile(r"\] (\S+) added to watchlist entry=")
    skipped_re = re.compile(r"\] (\S+) watchlist add skipped: (.*)$")
    reject_re = re.compile(r"setup_reject symbol=(\S+) .*?reject_stage=(\S+) reject_reason=(\S+)")
    consumed: set[tuple[str, int]] = set()
    pending_approval: dict[str, str] = {}
    queued_with_consumed: list[tuple[str, set]] = []
    for name in sorted(os.listdir(journal_dir)):
        path = journal_dir / name
        if not name.endswith(".log") or path.stat().st_size == 0:
            continue
        result["journal_files"].append(name)
        with io.open(path, "r", encoding="utf-16", errors="replace") as handle:
            for line in handle:
                if "EA initialized timer=" in line:
                    consumed = set()
                    continue
                if "sweep marked consumed" in line:
                    match = consumed_re.search(line)
                    if match:
                        stamp = calendar.timegm(_dt.datetime.strptime(match.group(2), "%Y.%m.%d %H:%M").timetuple())
                        consumed.add((match.group(1), stamp))
                        result["consumed_events"] += 1
                    continue
                if "AI request queued" in line:
                    match = queued_re.search(line)
                    if match:
                        result["queued"] += 1
                        symbol = match.group(1)
                        keys = {stamp for sym, stamp in consumed if sym == symbol}
                        if keys:
                            queued_with_consumed.append((match.group(2), set(keys)))
                    continue
                if "AI advisory req_id=" in line:
                    match = advisory_re.search(line)
                    if match and match.group(3) == "true":
                        pending_approval[match.group(1)] = match.group(2)
                        result["approval_outcome"][match.group(2)] = "approved_no_watchlist_outcome_line"
                    continue
                if "added to watchlist entry=" in line:
                    match = added_re.search(line)
                    if match and match.group(1) in pending_approval:
                        result["approval_outcome"][pending_approval.pop(match.group(1))] = "added_to_watchlist"
                    continue
                if "watchlist add skipped:" in line:
                    match = skipped_re.search(line)
                    if match and match.group(1) in pending_approval:
                        reason = match.group(2).split(" sweep=")[0].split(" current=")[0].strip()
                        result["approval_outcome"][pending_approval.pop(match.group(1))] = "watchlist_skipped:" + reason
                    continue
                if "setup_reject" in line and any(stage in line for stage in ("reject_stage=watchlist", "reject_stage=ai_target", "reject_stage=decision_integrity", "reject_stage=recovered_entry_gate")):
                    match = reject_re.search(line)
                    if match and match.group(1) in pending_approval:
                        result["approval_outcome"][pending_approval.pop(match.group(1))] = f"rejected:{match.group(2)}:{match.group(3)}"
    result["_queued_with_consumed"] = queued_with_consumed
    return result


def _find_archived_payload(bus: Path, request_id: str) -> Path | None:
    for folder in ARCHIVE_FOLDERS:
        direct = bus / folder / f"{request_id}.json"
        if direct.is_file():
            return direct
        hits = list((bus / folder).glob(f"*__{request_id}.json"))
        if hits:
            return hits[0]
    return None


_CANDIDATE_KEEP = (
    "candidate_index", "candidate_id", "setup_id", "entry_est", "entry", "sl", "tp1", "tp2", "policy_bucket",
    "setup_class", "broker_comment", "source_t_sweep",
    *gate._CANDIDATE_DISCRETE_FIELDS, *gate._CANDIDATE_REGIME_FIELDS,
)


def load_live_requests(bus: Path) -> list[dict[str, Any]]:
    index: dict[str, Path] = {}
    for folder in ARCHIVE_FOLDERS:
        for path in (bus / folder).glob("*.json"):
            name = path.name
            if name.endswith(".meta.json"):
                continue
            request_id = name.split("__", 1)[1][:-5] if "__" in name else name[:-5]
            match = RID_RE.match(request_id)
            if not match or match.group(2) in TESTER_COHORT_BASES:
                continue
            index.setdefault(request_id, path)
    rows: list[dict[str, Any]] = []
    for request_id, path in index.items():
        try:
            payload = read_json_any_encoding(path)
        except Exception:
            continue
        if str(payload.get("workload_mode") or "") != "LIVE_FORWARD":
            continue
        decision: dict[str, Any] | None = None
        debug_path = bus / "response_debug" / f"{request_id}.json"
        if debug_path.is_file():
            try:
                decision = dict(read_json_any_encoding(debug_path).get("response") or {})
            except Exception:
                decision = None
        assessed_ids = {
            str(item.get("candidate_id"))
            for item in (decision or {}).get("candidate_assessments") or []
            if isinstance(item, Mapping)
        }
        candidates = []
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            if assessed_ids and str(candidate.get("candidate_id")) not in assessed_ids:
                continue
            candidates.append({key: candidate.get(key) for key in _CANDIDATE_KEEP if key in candidate})
        match = RID_RE.match(request_id)
        trimmed = {
            "id": request_id,
            "symbol": payload.get("symbol"),
            "workload_mode": payload.get("workload_mode"),
            "request_created_sim_time": int(payload.get("request_created_sim_time") or match.group(4)),
            "request_identity_hash": payload.get("request_identity_hash"),
            "is_buy": payload.get("is_buy"),
            "runtime_input_hash": payload.get("runtime_input_hash"),
            "decision_input_hash": payload.get("decision_input_hash"),
            "contract_manifest_hash": payload.get("contract_manifest_hash"),
            "po3": {k: (payload.get("po3") or {}).get(k) for k in gate._REQUEST_PO3_FIELDS},
            "regime": {"news_risk": (payload.get("regime") or {}).get("news_risk")},
            "runtime_inputs": dict(payload.get("runtime_inputs") or {}),
            "plan": {k: (payload.get("plan") or {}).get(k) for k in ("setup_family", "setup_class", "entry_branch", "entry_model")},
            "candidates": candidates,
        }
        rows.append({
            "request_id": request_id,
            "account": match.group(1),
            "ea_session": f"{match.group(1)}_{match.group(2)}_{match.group(3)}",
            "symbol": str(payload.get("symbol") or ""),
            "server_time": trimmed["request_created_sim_time"],
            "payload": trimmed,
            "decision": decision,
        })
    rows.sort(key=lambda row: (row["account"], row["symbol"], row["server_time"], row["request_id"]))
    return rows


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _model_family(model: str) -> str:
    if model.startswith("muse"):
        return "muse"
    if "luna" in model:
        return "luna"
    return "other" if model else "unknown"


def replay(
    rows: list[dict[str, Any]],
    config: gate.ReviewGateConfig,
    *,
    offset_hours: int,
    family_threshold_fn,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    review_gate = gate.ReviewGate(config, store=gate.ReviewStateStore(None, max_records=100000))
    results: list[dict[str, Any]] = []
    session_token_check = collections.Counter()
    last_identity: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        payload = dict(row["payload"])
        decision = row["decision"] or {}
        runtime_inputs = payload.get("runtime_inputs") or {}
        session_code = historical_session_code(row["server_time"], offset_hours)
        killzone_code = historical_killzone_code(row["server_time"], runtime_inputs, offset_hours)
        for candidate in payload.get("candidates") or []:
            tokens = str(candidate.get("broker_comment") or "").split("-")
            if len(tokens) > 2:
                session_token_check[tokens[1] == session_code] += 1
        payload["ai_review_context"] = {
            "context_version": gate.REVIEW_CONTEXT_VERSION,
            "server_time": row["server_time"],
            "session_code": session_code,
            "killzone_code": killzone_code,
            "scan_interval_min": 10,
        }
        scope = (row["account"], row["symbol"])
        model = str(decision.get("actual_model_id") or "")
        if model:
            identity = {
                "provider_id": str(decision.get("provider_id") or ""),
                "model_id": model,
                "prompt_contract_version": str(decision.get("prompt_contract_version") or ""),
                "decision_schema_version": str(decision.get("decision_schema_version") or ""),
                "family_profile_version": str(decision.get("family_profile_version") or ""),
            }
            last_identity[scope] = identity
        else:
            identity = last_identity.get(scope) or {"provider_id": "unknown", "model_id": "unknown"}
        verdict = review_gate.evaluate(payload, python_identity=identity)
        recorded = ""
        if verdict.action == gate.ACTION_CALL and decision:
            # A called request's real decision becomes the next prior exactly as
            # it does in production; a skipped request never does.
            _stored, recorded = review_gate.record(
                verdict,
                payload,
                decision,
                family_threshold=lambda index, _p=payload: family_threshold_fn(_p, index),
            )
        results.append({
            "request_id": row["request_id"],
            "account": row["account"],
            "ea_session": row["ea_session"],
            "symbol": row["symbol"],
            "server_time": row["server_time"],
            "session_code": session_code,
            "killzone_code": killzone_code,
            "action": verdict.action,
            "category": verdict.category,
            "reason": verdict.reason,
            "events": "|".join(verdict.events),
            "prior_request_id": verdict.prior_request_id,
            "prior_age_minutes": verdict.prior_age_minutes,
            "ttl_minutes": verdict.ttl_minutes,
            "decision_tier": str(decision.get("decision_quality_tier") or ""),
            "decision_source": str(decision.get("decision_source") or ""),
            "decision_state": str(decision.get("decision_state") or ""),
            "python_final_allow": bool(decision.get("python_final_allow")),
            "model_family": _model_family(model),
            "recorded": recorded,
            "final_resolver_reason": str(decision.get("final_resolver_reason") or ""),
            "adjudicator_ran": bool(decision.get("adjudicator_response_fingerprint")),
            "analyst_selected_state": next(
                (str(a.get("decision_state")) for a in decision.get("candidate_assessments") or []
                 if isinstance(a, Mapping) and a.get("candidate_id") == decision.get("selected_candidate_id")),
                "",
            ),
        })
    return results, {"broker_comment_session_token_match": dict(session_token_check)}


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def summarize(
    results: list[dict[str, Any]],
    usage: Mapping[str, Mapping[str, Any]],
    hard_rejected: Mapping[str, str],
    repairs: Mapping[str, int],
    journals: Mapping[str, Any],
    *,
    allowance_usd: float,
    premise_requests_per_week: int,
    config: gate.ReviewGateConfig,
) -> dict[str, Any]:
    # Only requests whose EVERY candidate belongs to a consumed sweep are
    # predetermined (attach_consumed_sweep_flags sets the flag).
    return_rows = []
    categories = collections.Counter()
    categories_muse = collections.Counter()
    calls_by_category = collections.defaultdict(collections.Counter)
    muse_cost_by_category = collections.Counter()
    for row in results:
        request_id = row["request_id"]
        item = usage.get(request_id) or {"calls": collections.Counter(), "muse_cost": 0.0, "muse_calls": collections.Counter(), "muse_cost_by_class": collections.Counter()}
        if request_id in hard_rejected:
            category = gate.CATEGORY_PREDETERMINED_OUTCOME
            detail = "python_hard_pre_gate_already_skips_provider"
        elif row.get("consumed_sweep_all_candidates"):
            category = gate.CATEGORY_PREDETERMINED_OUTCOME
            detail = "mql_sweep_already_consumed_all_candidates"
        elif row["action"] == gate.ACTION_REUSE:
            category = gate.CATEGORY_UNCHANGED_STATE
            detail = row["reason"]
        elif not row["decision_tier"]:
            category = gate.CATEGORY_UNKNOWN
            detail = "no_archived_decision"
        else:
            category = row["category"]
            detail = row["reason"]
        row["final_category"] = category
        row["final_category_detail"] = detail
        categories[category] += 1
        if row["model_family"] == "muse":
            categories_muse[category] += 1
        calls_by_category[category].update(item["calls"])
        muse_cost_by_category[category] += float(item.get("muse_cost") or 0.0)
        return_rows.append(row)
    total_requests = len(results)

    # Call-level overlays inside requests that stay called.
    kept = [r for r in results if r["final_category"] not in (gate.CATEGORY_UNCHANGED_STATE, gate.CATEGORY_PREDETERMINED_OUTCOME)]
    pointless_adjudications = [r for r in kept if r["adjudicator_ran"] and r["analyst_selected_state"] == "REJECT"]
    shadow_calls = sum((usage.get(r["request_id"]) or {}).get("calls", {}).get(SHADOW, 0) for r in kept)
    repair_calls = sum((usage.get(r["request_id"]) or {}).get("calls", {}).get(REPAIR, 0) for r in kept)
    repair_log_events = sum(repairs.get(r["request_id"], 0) for r in results)

    # Muse-era economics (Muse rows only).
    muse_rows = [r for r in results if r["model_family"] == "muse" and (usage.get(r["request_id"]) or {}).get("muse_cost", 0.0) > 0]
    muse_costs = [float(usage[r["request_id"]]["muse_cost"]) for r in muse_rows]
    muse_mean = statistics.fmean(muse_costs) if muse_costs else 0.0
    class_share = collections.Counter()
    class_calls = collections.Counter()
    for r in muse_rows:
        class_share.update(usage[r["request_id"]]["muse_cost_by_class"])
        class_calls.update(usage[r["request_id"]]["muse_calls"])
    muse_total = sum(class_share.values()) or 1.0

    # Opportunity analysis.
    approvals = [r for r in results if r["python_final_allow"]]
    outcomes = journals.get("approval_outcome") or {}
    opportunity_rows = []
    for r in approvals:
        outcome = outcomes.get(r["request_id"], "no_journal_line")
        actionable = outcome == "added_to_watchlist"
        preserved = r["final_category"] not in (gate.CATEGORY_UNCHANGED_STATE,) and not (
            r["final_category"] == gate.CATEGORY_PREDETERMINED_OUTCOME and r["final_category_detail"].startswith("mql_")
        )
        opportunity_rows.append({
            "request_id": r["request_id"], "symbol": r["symbol"], "session_code": r["session_code"],
            "model_family": r["model_family"], "mql_outcome": outcome, "actionable": actionable,
            "gated_action": "CALL" if preserved else "SKIP", "delay_minutes": 0.0 if preserved else None,
        })
    missed_actionable = [o for o in opportunity_rows if o["actionable"] and o["gated_action"] != "CALL"]
    missed_any = [o for o in opportunity_rows if o["gated_action"] != "CALL"]

    # Days actually covered by the live stream (for per-weekday normalization).
    per_day = collections.Counter(time.strftime("%Y-%m-%d", time.gmtime(r["server_time"] - 3 * 3600)) for r in results)
    reuse_fraction = _pct(categories[gate.CATEGORY_UNCHANGED_STATE], total_requests) / 100.0
    predetermined_new_fraction = _pct(sum(1 for r in results if r["final_category_detail"] == "mql_sweep_already_consumed_all_candidates"), total_requests) / 100.0
    reuse_fraction_muse = _pct(categories_muse[gate.CATEGORY_UNCHANGED_STATE], sum(categories_muse.values())) / 100.0

    summary = {
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "review_gate_config": config.safe_log_dict(),
        "requests_total": total_requests,
        "requests_by_model_family": dict(collections.Counter(r["model_family"] for r in results)),
        "requests_by_session": dict(collections.Counter(r["session_code"] for r in results)),
        "requests_per_utc_day": dict(sorted(per_day.items())),
        "request_categories": {k: {"count": categories[k], "pct": _pct(categories[k], total_requests), "provider_calls": dict(calls_by_category[k]), "muse_cost_usd": round(muse_cost_by_category[k], 4)} for k in gate.REQUEST_CATEGORIES},
        "request_categories_muse_only": {k: categories_muse[k] for k in gate.REQUEST_CATEGORIES},
        "gate_reasons": dict(collections.Counter(r["reason"] for r in results)),
        "gate_events": dict(collections.Counter(e for r in results for e in (r["events"].split("|") if r["events"] else []))),
        "call_level": {
            "pointless_adjudications_analyst_reject": len(pointless_adjudications),
            "pointless_adjudications_by_model": dict(collections.Counter(r["model_family"] for r in pointless_adjudications)),
            "shadow_repeat_calls_in_kept_requests": shadow_calls,
            "repair_calls_in_usage_ledger_kept_requests": repair_calls,
            "repair_passes_in_gate_log_all_requests": repair_log_events,
        },
        "muse_economics": {
            "requests_with_muse_cost": len(muse_rows),
            "mean_cost_per_request_usd": round(muse_mean, 6),
            "median_cost_per_request_usd": round(statistics.median(muse_costs), 6) if muse_costs else 0.0,
            "cost_share_by_traffic_class": {k: round(v / muse_total, 4) for k, v in class_share.items()},
            "calls_per_request_by_traffic_class": {k: round(v / max(1, len(muse_rows)), 4) for k, v in class_calls.items()},
        },
        "opportunities": {
            "python_approvals": len(approvals),
            "actionable_added_to_watchlist": sum(1 for o in opportunity_rows if o["actionable"]),
            "mql_outcomes": dict(collections.Counter(o["mql_outcome"] for o in opportunity_rows)),
            "missed_actionable": len(missed_actionable),
            "missed_any_approval": len(missed_any),
            "materially_delayed": 0,
            "detail": opportunity_rows,
        },
        "journals": {
            "files": journals.get("journal_files"),
            "queued_requests": journals.get("queued"),
            "consumed_sweep_events": journals.get("consumed_events"),
        },
        "fractions": {
            "unchanged_state_reuse_all": reuse_fraction,
            "unchanged_state_reuse_muse": reuse_fraction_muse,
            "predetermined_consumed_sweep_new": predetermined_new_fraction,
        },
        "allowance_usd_per_week": allowance_usd,
        "premise_requests_per_week": premise_requests_per_week,
    }
    return summary


def attach_consumed_sweep_flags(rows: list[dict[str, Any]], results: list[dict[str, Any]], journals: Mapping[str, Any]) -> None:
    queued = dict(journals.get("_queued_with_consumed") or [])
    by_id = {row["request_id"]: row for row in rows}
    for result in results:
        keys = queued.get(result["request_id"])
        if not keys:
            continue
        row = by_id.get(result["request_id"])
        candidates = (row or {}).get("payload", {}).get("candidates") or []
        sweeps = {int(c.get("source_t_sweep") or 0) for c in candidates}
        result["consumed_sweep_all_candidates"] = bool(sweeps) and all(s > 0 and s in keys for s in sweeps)


def write_outputs(out_dir: Path, summary: Mapping[str, Any], results: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "replay_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str), encoding="utf-8")
    fields = [
        "request_id", "account", "ea_session", "symbol", "server_time", "session_code", "killzone_code", "action",
        "final_category", "final_category_detail", "category", "reason", "events", "prior_request_id",
        "prior_age_minutes", "ttl_minutes", "decision_tier", "decision_source", "decision_state",
        "python_final_allow", "model_family", "recorded", "final_resolver_reason", "adjudicator_ran",
        "analyst_selected_state", "consumed_sweep_all_candidates",
    ]
    with (out_dir / "replay_requests.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)


def _family_threshold_factory():
    """Use the production threshold resolver without re-implementing it."""

    os.environ.setdefault("AI_REVIEW_GATE_MODE", "off")
    import ai_gate  # noqa: WPS433 (deliberately lazy: heavy module)

    def _threshold(payload: Mapping[str, Any], index: int) -> float:
        value, _source = ai_gate.effective_llm_quality_score_threshold(dict(payload), chosen_index=index)
        return float(value)

    return _threshold


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bus", required=True, type=Path)
    parser.add_argument("--journal-dir", type=Path, default=None)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--offset-hours", type=int, default=3)
    parser.add_argument("--allowance-usd", type=float, default=30.0)
    parser.add_argument("--premise-requests-per-week", type=int, default=11000)
    parser.add_argument("--policy", action="append", default=[], help="name:KEY=VALUE,KEY=VALUE (env-style gate overrides)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    usage = load_usage(args.bus / "logs" / "openai_usage.ndjson")
    hard_rejected, repairs = load_gate_log(args.bus / "logs" / "ai_gate.log")
    journals = load_journals(args.journal_dir)
    rows = load_live_requests(args.bus)
    threshold_fn = _family_threshold_factory()
    policies = [("default", {})]
    for spec in args.policy:
        name, _, rest = spec.partition(":")
        overrides = dict(item.split("=", 1) for item in rest.split(",") if "=" in item)
        policies.append((name, overrides))
    overview = {}
    for name, overrides in policies:
        env = {"AI_REVIEW_GATE_MODE": "enforce", **overrides}
        config = gate.ReviewGateConfig.from_env(env)
        results, checks = replay(rows, config, offset_hours=args.offset_hours, family_threshold_fn=threshold_fn)
        attach_consumed_sweep_flags(rows, results, journals)
        summary = summarize(
            results, usage, hard_rejected, repairs, journals,
            allowance_usd=args.allowance_usd, premise_requests_per_week=args.premise_requests_per_week, config=config,
        )
        summary["session_reconstruction_check"] = checks
        write_outputs(args.out_dir / name, summary, results)
        overview[name] = {
            "overrides": overrides,
            "requests": summary["requests_total"],
            "unchanged_state": summary["request_categories"][gate.CATEGORY_UNCHANGED_STATE]["count"],
            "predetermined": summary["request_categories"][gate.CATEGORY_PREDETERMINED_OUTCOME]["count"],
            "missed_actionable": summary["opportunities"]["missed_actionable"],
            "missed_any_approval": summary["opportunities"]["missed_any_approval"],
        }
    (args.out_dir / "policies_overview.json").write_text(json.dumps(overview, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"elapsed_sec": round(time.time() - started, 1), "overview": overview}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
