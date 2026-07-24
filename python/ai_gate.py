#!/usr/bin/env python3
"""
ai_gate.py
----------
A PO3-aware AI gate for MT5 <-> Python communication via the MT5 Common folder.

- EA writes:   Common/Files/<BUS_ROOT>/requests/<id>.json
- Python reads, scores, and writes: Common/Files/<BUS_ROOT>/responses/<id>.json
- After processing, request/response can be moved to stale/ (bin).

The scorer combines rule-based structure checks with an LLM advisory layer so the
MT5 side can send a richer PO3 narrative: dealing range, sweep/displacement/BOS timing,
session/killzone context, liquidity target, and planned execution levels. MT5 remains
the final execution authority; Python only reranks, explains, or suggests a veto.
"""

from __future__ import annotations
import argparse
import base64
import json
import math
import os
import shutil
import socket
import statistics
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Tuple

from ai_provider import (
    AIProvider,
    LocalOpenAICompatibleProvider,
    PROVIDER_CONTRACT_VERSION,
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_REMOTE,
    ProviderCallError,
    RemoteAPIProvider,
    UnavailableProvider,
    endpoint_class,
)

from architecture_contracts import (
    ARCHITECTURE_CONTRACT_VERSION,
    COHORT_SCHEMA_VERSION,
    FILE_BUS_LIFECYCLE_VERSION,
    HIERARCHICAL_OUTCOME_MODEL_VERSION,
    LIVE_FORWARD,
    LIVE_FORWARD_CONTRACT_VERSION,
    POLICY_MANIFEST_SCHEMA_VERSION,
    SEMANTIC_CACHE_SCHEMA_VERSION,
    FileBusClaimDeferredError,
    FileBusLifecycle,
    PolicySpec,
    build_startup_policy_manifest,
    decision_field_authority_manifest,
    live_forward_behavior_contract,
    resolve_multiplier,
    response_binding_hash,
    semantic_cache_invalidation_reasons,
    semantic_cache_row,
    semantic_cache_state,
    strict_json_loads,
    workload_mode as canonical_workload_mode,
)
from decision_integrity import (
    AI_DECISION_SCHEMA_VERSION,
    AI_IDENTITY_CANONICALIZATION_VERSION,
    AI_PROMPT_CONTRACT_VERSION,
    AI_REQUEST_IDENTITY_VERSION,
    AI_TARGET_ARBITRATION_SCHEMA_VERSION,
    DECISION_ABSTAIN,
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_QUALITY_CACHE_FULL_STRUCTURED,
    DECISION_QUALITY_DEGRADED_NON_TRADING,
    DECISION_QUALITY_FULL_STRUCTURED,
    DECISION_QUALITY_RULE_ONLY_NON_TRADING,
    LLM_VETO_CODES,
    RESPONSE_CACHE_FULL_STRUCTURED,
    RESPONSE_DEGRADED_NON_TRADING,
    RESPONSE_FULL_STRUCTURED,
    RESPONSE_RULE_ONLY_NON_TRADING,
    FrozenAIRequest,
    assessed_execution_fingerprint as deterministic_assessed_execution_fingerprint,
    build_ai_request_identity,
    candidate_hash as deterministic_candidate_hash,
    freeze_ai_request,
    response_can_trade,
    validate_candidate_assessment,
    validate_decision_envelope,
    validate_request_identity_echo,
)
from decision_evidence import EVIDENCE_ENVELOPE_VERSION, build_decision_evidence_envelope
from decision_pipeline import (
    CONSENSUS_RESOLVER_VERSION,
    ROLE_CONTRACT_VERSION,
    evidence_path_exists,
    run_qualitative_consensus,
)
from family_context import FAMILY_PROFILE_VERSION
from governance_contracts import (
    CALIBRATION_CONTRACT_VERSION,
    SETUP_TAXONOMY_VERSION,
    SetupTaxonomy,
    classify_setup_taxonomy,
)
from openai_usage_logger import (
    log_ai_usage,
    set_ai_usage_bus,
)
from po3_env import load_dotenv, peek_dotenv_value
from runtime_governance import (
    DECISION_NON_REPEATABLE,
    HIERARCHICAL_PRIOR_SCHEMA_VERSION,
    INVALIDATION_POLICY_SCHEMA_VERSION,
    MANAGEMENT_SCHEMA_VERSION,
    NORMALIZED_FVG_SCHEMA_VERSION,
    INSUFFICIENT_SAMPLE,
    REPEATABILITY_SCHEMA_VERSION,
    RISK_FACTOR_SCHEMA_VERSION,
    REPEATABLE,
    SCORE_NON_REPEATABLE,
    UNAVAILABLE,
    RepeatabilityThresholds,
    atomic_write_json as governance_atomic_write_json,
    audit_prior_artifact,
    canonical_hash,
    evaluate_repeatability,
    priors_for_candidate,
    repeatability_authority,
    request_fingerprint,
    resolve_project_path,
    response_fingerprint,
    runtime_governance_versions,
)
from request_lifecycle import (
    REQUEST_LIFECYCLE_VERSION,
    RequestHeartbeat,
    RequestIdempotencyLedger,
)
from trade_memory import RETRIEVAL_POLICY_VERSION, TRADE_MEMORY_SCHEMA_VERSION, TradeMemoryStore
from structured_models import (
    AIGateEnvelope,
    CandidateAssessment,
    ModelAIGateOutput,
    all_authoritative_structured_models,
    strict_structured_schema,
)

# Read only the provider selector before importing the rest of the private env.
# In local/invalid mode the remote secret keys are never parsed from .env and
# any stale parent-process values are removed before provider construction.
_DOTENV_PROVIDER_SWITCH = peek_dotenv_value(None, "AI_USE_REMOTE_API")
_BOOTSTRAP_PROVIDER_SWITCH = (
    _DOTENV_PROVIDER_SWITCH
    if _DOTENV_PROVIDER_SWITCH is not None
    else os.environ.get("AI_USE_REMOTE_API")
)
_BOOTSTRAP_REMOTE = str(_BOOTSTRAP_PROVIDER_SWITCH or "").strip().lower() == "true"
_BOOTSTRAP_EXCLUDED_KEYS = (
    ("LOCAL_AI_API_KEY", "LOCAL_AI_MODEL_PATH")
    if _BOOTSTRAP_REMOTE
    else ("OPENAI_API_KEY", "OPENAI_BASE_URL")
)
load_dotenv(
    override=True,
    exclude_keys=_BOOTSTRAP_EXCLUDED_KEYS,
)
if _BOOTSTRAP_REMOTE:
    os.environ.pop("LOCAL_AI_API_KEY", None)
    os.environ.pop("LOCAL_AI_MODEL_PATH", None)
else:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("OPENAI_BASE_URL", None)

try:
    from expectancy_report import run_analytics_suite, set_expectancy_ai_provider
    ANALYTICS_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - defensive import fallback
    run_analytics_suite = None
    set_expectancy_ai_provider = None
    ANALYTICS_IMPORT_ERROR = str(exc)

DEFAULT_BUS_ROOT = "PO3_AI_BUS"

def _env_lookup(env: Mapping[str, str], names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _env_bool(
    env: Mapping[str, str],
    name: str,
    default: bool,
    warnings: list[str],
    *,
    safe_default: bool | None = None,
) -> bool:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    warnings.append(f"{name}=invalid_bool")
    return default if safe_default is None else safe_default


def _env_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    warnings: list[str],
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except Exception:
            warnings.append(f"{name}=invalid_int")
            value = default
    if min_value is not None and value < min_value:
        warnings.append(f"{name}=below_min")
        value = min_value
    if max_value is not None and value > max_value:
        warnings.append(f"{name}=above_max")
        value = max_value
    return value


def _env_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    warnings: list[str],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except Exception:
            warnings.append(f"{name}=invalid_float")
            value = default
    if min_value is not None and value < min_value:
        warnings.append(f"{name}=below_min")
        value = min_value
    if max_value is not None and value > max_value:
        warnings.append(f"{name}=above_max")
        value = max_value
    return value


@dataclass(frozen=True)
class AIGateRuntimeConfig:
    use_remote_api: bool | None
    provider_config_valid: bool
    provider_config_errors: tuple[str, ...]
    model: str
    expectancy_model: str
    fallback_models: list[str]
    reasoning_effort: str
    max_output_tokens: int
    legacy_min_self_reported_confidence_diagnostic: float
    prompt_cache_enable: bool
    prompt_cache_key: str
    prompt_cache_retention: str
    decision_cache_enable: bool
    decision_cache_ttl_sec: int
    decision_cache_file: Path
    use_batch_api: bool
    batch_only_for_backtest: bool
    batch_output_dir: Path
    batch_max_pending: int
    use_flex: bool
    allow_flex_for_live: bool
    flex_live_ack: bool
    service_tier: str
    openai_timeout_sec: float
    openai_flex_timeout_sec: float
    flex_unavailable_retry_enable: bool
    flex_unavailable_max_retries: int
    flex_unavailable_cooldown_sec: float
    require_runtime_inputs_live: bool
    reject_on_missing_runtime_inputs_live: bool
    hard_pre_gate_before_openai: bool
    log_skipped_calls: bool
    enable_snapshots: bool
    cost_report_enable: bool
    cost_report_file: Path
    live_bucket_priors_file: Path
    require_live_bucket_priors: bool
    live_bucket_priors_max_age_days: int
    request_response_fingerprint_file: Path
    require_repeatability_live: bool
    shadow_repeat_enable: bool
    shadow_repeat_sample_rate: float
    shadow_repeat_count: int
    shadow_repeat_min_evaluated_candidates: int
    shadow_repeat_max_score_stddev: float
    shadow_repeat_min_decision_agreement: float
    shadow_repeat_min_chosen_candidate_agreement: float
    shadow_repeat_min_veto_agreement: float
    shadow_repeat_min_target_choice_agreement: float
    shadow_repeat_artifact_file: Path
    local_base_url: str
    local_api_key: str
    local_model: str
    local_analyst_model: str
    local_critic_model: str
    local_adjudicator_model: str
    local_fallback_models: list[str]
    local_model_path: str
    local_healthcheck_path: str
    local_timeout_sec: float
    local_max_retries: int
    local_max_output_tokens: int
    local_temperature: float
    local_top_p: float
    local_seed: int
    local_enable_thinking: bool
    local_require_json_schema: bool
    local_parallelism: int
    local_context_budget_tokens: int
    local_retrieval_top_k: int
    local_allow_non_loopback_ack: bool
    shadow_compare_providers: bool
    trade_memory_file: Path
    provider_circuit_failure_threshold: int
    provider_circuit_cooldown_sec: float
    validation_warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AIGateRuntimeConfig":
        env = os.environ if env is None else env
        warnings: list[str] = []
        provider_errors: list[str] = []
        switch_raw = env.get("AI_USE_REMOTE_API")
        switch_text = str(switch_raw or "").strip().lower()
        if switch_text == "true":
            use_remote_api: bool | None = True
        elif switch_text == "false":
            use_remote_api = False
        else:
            use_remote_api = None
            provider_errors.append("AI_USE_REMOTE_API=missing_or_invalid")

        # Preserve the existing Version Z remote defaults. The new provider
        # switch changes transport selection, never the configured model.
        remote_model = _env_lookup(env, ("AI_GATE_MODEL", "OPENAI_MODEL"), "gpt-5.5-mini") or "gpt-5.5-mini"
        remote_fallback_raw = _env_lookup(
            env,
            ("AI_GATE_FALLBACK_MODELS", "OPENAI_FALLBACK_MODELS"),
            "gpt-5.4-mini,gpt-5.4-nano",
        )
        local_model = _env_lookup(env, ("LOCAL_AI_MODEL",), "qwen3.5-9b") or "qwen3.5-9b"
        selected_model = remote_model if use_remote_api is not False else (
            _env_lookup(env, ("LOCAL_AI_ANALYST_MODEL",), local_model) or local_model
        )
        fallback_raw = remote_fallback_raw if use_remote_api is not False else _env_lookup(env, ("LOCAL_AI_FALLBACK_MODELS",), "")
        fallback_models: list[str] = []
        for item in fallback_raw.split(","):
            name = item.strip()
            if name and name != selected_model and name not in fallback_models:
                fallback_models.append(name)

        effort = _env_lookup(env, ("AI_GATE_REASONING_EFFORT", "AI_REASONING_EFFORT"), "low").lower()
        if effort not in {"auto", "none", "minimal", "low", "medium", "high", "xhigh"}:
            warnings.append("AI_REASONING_EFFORT=invalid")
            effort = "low"

        service_tier = _env_lookup(env, ("AI_SERVICE_TIER",), "auto").lower()
        if service_tier not in {"auto", "default", "flex"}:
            warnings.append("AI_SERVICE_TIER=invalid")
            service_tier = "auto"

        prompt_key = _env_lookup(env, ("AI_PROMPT_CACHE_KEY",), "po3_live_gate_v2") or "po3_live_gate_v2"
        prompt_retention = _env_lookup(env, ("AI_PROMPT_CACHE_RETENTION",), "24h") or "24h"
        if prompt_retention not in {"ephemeral", "24h"}:
            warnings.append("AI_PROMPT_CACHE_RETENTION=invalid")
            prompt_retention = "24h"

        legacy_confidence_raw = _env_lookup(
            env,
            ("AI_LEGACY_MIN_SELF_REPORTED_CONFIDENCE_DIAGNOSTIC", "AI_MIN_CONFIDENCE"),
            "0.45",
        )
        try:
            legacy_confidence_diagnostic = max(0.0, min(1.0, float(legacy_confidence_raw)))
        except Exception:
            warnings.append("AI_LEGACY_MIN_SELF_REPORTED_CONFIDENCE_DIAGNOSTIC=invalid")
            legacy_confidence_diagnostic = 0.45

        shadow_compare_providers = _env_bool(
            env,
            "AI_SHADOW_COMPARE_PROVIDERS",
            False,
            warnings,
            safe_default=False,
        )
        # Non-selected credentials are intentionally ignored. Remote mode may
        # read local shadow settings only when the explicit non-authoritative
        # research comparison switch is enabled; local mode never reads the
        # remote API secret under any circumstance.
        read_local_settings = use_remote_api is False or shadow_compare_providers
        local_base_url = (
            _env_lookup(env, ("LOCAL_AI_BASE_URL",), "http://127.0.0.1:1234/v1")
            if read_local_settings
            else "http://127.0.0.1:1234/v1"
        )
        local_endpoint_class = endpoint_class(local_base_url)
        local_allow_non_loopback_ack = _env_bool(
            env,
            "LOCAL_AI_ALLOW_NON_LOOPBACK_ACK",
            False,
            warnings,
            safe_default=False,
        )
        if use_remote_api is True:
            if not str(env.get("OPENAI_API_KEY") or "").strip():
                provider_errors.append("OPENAI_API_KEY=missing_remote")
            if not remote_model:
                provider_errors.append("AI_GATE_MODEL=missing_remote")
        elif use_remote_api is False:
            if local_endpoint_class == "invalid":
                provider_errors.append("LOCAL_AI_BASE_URL=invalid")
            elif local_endpoint_class != "loopback" and not local_allow_non_loopback_ack:
                provider_errors.append("LOCAL_AI_BASE_URL=non_loopback_without_ack")
            if not local_model:
                provider_errors.append("LOCAL_AI_MODEL=missing")

        local_fallback_models: list[str] = []
        for item in _env_lookup(env, ("LOCAL_AI_FALLBACK_MODELS",), "").split(","):
            name = item.strip()
            if name and name not in local_fallback_models:
                local_fallback_models.append(name)
        local_parallelism = _env_int(env, "LOCAL_AI_PARALLELISM", 1, warnings, min_value=1, max_value=1)

        return cls(
            use_remote_api=use_remote_api,
            provider_config_valid=not provider_errors,
            provider_config_errors=tuple(provider_errors),
            model=selected_model,
            expectancy_model=(
                _env_lookup(env, ("EXPECTANCY_AI_MODEL",), "gpt-5.5") or "gpt-5.5"
                if use_remote_api is not False
                else (_env_lookup(env, ("LOCAL_AI_ANALYST_MODEL",), local_model) or local_model)
            ),
            fallback_models=fallback_models,
            reasoning_effort=effort,
            max_output_tokens=_env_int(env, "AI_MAX_OUTPUT_TOKENS", 25000, warnings, min_value=1024, max_value=128000),
            legacy_min_self_reported_confidence_diagnostic=legacy_confidence_diagnostic,
            prompt_cache_enable=_env_bool(env, "AI_PROMPT_CACHE_ENABLE", True, warnings, safe_default=False),
            prompt_cache_key=prompt_key,
            prompt_cache_retention=prompt_retention,
            decision_cache_enable=_env_bool(env, "AI_DECISION_CACHE_ENABLE", True, warnings, safe_default=False),
            decision_cache_ttl_sec=_env_int(env, "AI_DECISION_CACHE_TTL_SEC", 1800, warnings, min_value=30, max_value=86400),
            decision_cache_file=resolve_project_path(_env_lookup(env, ("AI_DECISION_CACHE_FILE",), "data/ai_decision_cache.jsonl")),
            use_batch_api=_env_bool(env, "AI_USE_BATCH_API", False, warnings, safe_default=False),
            batch_only_for_backtest=_env_bool(env, "AI_BATCH_ONLY_FOR_BACKTEST", True, warnings, safe_default=True),
            batch_output_dir=resolve_project_path(_env_lookup(env, ("AI_BATCH_OUTPUT_DIR",), "data/ai_batch")),
            batch_max_pending=_env_int(env, "AI_BATCH_MAX_PENDING", 1000, warnings, min_value=1, max_value=100000),
            use_flex=_env_bool(env, "AI_USE_FLEX", False, warnings, safe_default=False),
            allow_flex_for_live=_env_bool(env, "AI_ALLOW_FLEX_FOR_LIVE", False, warnings, safe_default=False),
            flex_live_ack=_env_bool(env, "AI_FLEX_LIVE_ACK", False, warnings, safe_default=False),
            service_tier=service_tier,
            openai_timeout_sec=_env_float(env, "AI_OPENAI_TIMEOUT_SEC", 180.0, warnings, min_value=10.0, max_value=1800.0),
            openai_flex_timeout_sec=_env_float(env, "AI_OPENAI_FLEX_TIMEOUT_SEC", 600.0, warnings, min_value=30.0, max_value=1800.0),
            flex_unavailable_retry_enable=_env_bool(env, "AI_FLEX_UNAVAILABLE_RETRY_ENABLE", True, warnings, safe_default=True),
            flex_unavailable_max_retries=_env_int(env, "AI_FLEX_UNAVAILABLE_MAX_RETRIES", 20, warnings, min_value=0, max_value=100),
            flex_unavailable_cooldown_sec=_env_float(env, "AI_FLEX_UNAVAILABLE_COOLDOWN_SEC", 30.0, warnings, min_value=0.0, max_value=3600.0),
            require_runtime_inputs_live=_env_bool(env, "AI_REQUIRE_RUNTIME_INPUTS_LIVE", True, warnings, safe_default=True),
            reject_on_missing_runtime_inputs_live=_env_bool(env, "AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE", True, warnings, safe_default=True),
            hard_pre_gate_before_openai=_env_bool(env, "AI_HARD_PRE_GATE_BEFORE_OPENAI", True, warnings, safe_default=True),
            log_skipped_calls=_env_bool(env, "AI_LOG_SKIPPED_CALLS", True, warnings, safe_default=True),
            enable_snapshots=_env_bool(env, "AI_ENABLE_SNAPSHOTS", False, warnings, safe_default=False),
            cost_report_enable=_env_bool(env, "AI_COST_REPORT_ENABLE", True, warnings, safe_default=True),
            cost_report_file=resolve_project_path(_env_lookup(env, ("AI_COST_REPORT_FILE",), "logs/ai_cost_report.jsonl")),
            live_bucket_priors_file=resolve_project_path(_env_lookup(env, ("AI_LIVE_BUCKET_PRIORS_FILE",), "data/live_bucket_priors.json")),
            require_live_bucket_priors=_env_bool(env, "AI_REQUIRE_LIVE_BUCKET_PRIORS", False, warnings, safe_default=True),
            live_bucket_priors_max_age_days=_env_int(env, "AI_LIVE_BUCKET_PRIORS_MAX_AGE_DAYS", 30, warnings, min_value=1, max_value=3650),
            request_response_fingerprint_file=resolve_project_path(
                _env_lookup(env, ("AI_REQUEST_RESPONSE_FINGERPRINT_FILE",), "logs/ai_request_response_fingerprints.jsonl")
            ),
            require_repeatability_live=_env_bool(
                env, "AI_REQUIRE_REPEATABILITY_LIVE", True, warnings, safe_default=True
            ),
            shadow_repeat_enable=_env_bool(env, "AI_SHADOW_REPEAT_ENABLE", False, warnings, safe_default=False),
            shadow_repeat_sample_rate=_env_float(env, "AI_SHADOW_REPEAT_SAMPLE_RATE", 0.02, warnings, min_value=0.0, max_value=1.0),
            shadow_repeat_count=_env_int(env, "AI_SHADOW_REPEAT_COUNT", 3, warnings, min_value=2, max_value=20),
            shadow_repeat_min_evaluated_candidates=_env_int(env, "AI_SHADOW_REPEAT_MIN_EVALUATED_CANDIDATES", 30, warnings, min_value=2, max_value=100000),
            shadow_repeat_max_score_stddev=_env_float(env, "AI_SHADOW_REPEAT_MAX_SCORE_STDDEV", 0.75, warnings, min_value=0.0, max_value=10.0),
            shadow_repeat_min_decision_agreement=_env_float(env, "AI_SHADOW_REPEAT_MIN_DECISION_AGREEMENT", 0.90, warnings, min_value=0.0, max_value=1.0),
            shadow_repeat_min_chosen_candidate_agreement=_env_float(env, "AI_SHADOW_REPEAT_MIN_CHOSEN_CANDIDATE_AGREEMENT", 0.90, warnings, min_value=0.0, max_value=1.0),
            shadow_repeat_min_veto_agreement=_env_float(env, "AI_SHADOW_REPEAT_MIN_VETO_AGREEMENT", 0.90, warnings, min_value=0.0, max_value=1.0),
            shadow_repeat_min_target_choice_agreement=_env_float(env, "AI_SHADOW_REPEAT_MIN_TARGET_CHOICE_AGREEMENT", 0.85, warnings, min_value=0.0, max_value=1.0),
            shadow_repeat_artifact_file=resolve_project_path(
                _env_lookup(env, ("AI_SHADOW_REPEAT_ARTIFACT_FILE",), "data/ai_repeatability_artifact.json")
            ),
            local_base_url=local_base_url,
            local_api_key=(
                _env_lookup(env, ("LOCAL_AI_API_KEY",), "local") or "local"
                if read_local_settings
                else "local"
            ),
            local_model=local_model,
            local_analyst_model=_env_lookup(env, ("LOCAL_AI_ANALYST_MODEL",), local_model) or local_model,
            local_critic_model=_env_lookup(env, ("LOCAL_AI_CRITIC_MODEL",), local_model) or local_model,
            local_adjudicator_model=_env_lookup(env, ("LOCAL_AI_ADJUDICATOR_MODEL",), local_model) or local_model,
            local_fallback_models=local_fallback_models,
            local_model_path=_env_lookup(env, ("LOCAL_AI_MODEL_PATH",), ""),
            local_healthcheck_path=_env_lookup(env, ("LOCAL_AI_HEALTHCHECK_PATH",), "/models") or "/models",
            local_timeout_sec=_env_float(env, "LOCAL_AI_TIMEOUT_SEC", 180.0, warnings, min_value=10.0, max_value=1800.0),
            local_max_retries=_env_int(env, "LOCAL_AI_MAX_RETRIES", 1, warnings, min_value=0, max_value=1),
            local_max_output_tokens=_env_int(env, "LOCAL_AI_MAX_OUTPUT_TOKENS", 4096, warnings, min_value=512, max_value=32768),
            local_temperature=_env_float(env, "LOCAL_AI_TEMPERATURE", 0.15, warnings, min_value=0.0, max_value=2.0),
            local_top_p=_env_float(env, "LOCAL_AI_TOP_P", 0.85, warnings, min_value=0.0, max_value=1.0),
            local_seed=_env_int(env, "LOCAL_AI_SEED", 42, warnings, min_value=0, max_value=2147483647),
            local_enable_thinking=_env_bool(env, "LOCAL_AI_ENABLE_THINKING", True, warnings, safe_default=False),
            local_require_json_schema=_env_bool(env, "LOCAL_AI_REQUIRE_JSON_SCHEMA", True, warnings, safe_default=True),
            local_parallelism=local_parallelism,
            local_context_budget_tokens=_env_int(env, "LOCAL_AI_CONTEXT_BUDGET_TOKENS", 7000, warnings, min_value=2048, max_value=131072),
            local_retrieval_top_k=_env_int(env, "LOCAL_AI_RETRIEVAL_TOP_K", 7, warnings, min_value=5, max_value=10),
            local_allow_non_loopback_ack=local_allow_non_loopback_ack,
            shadow_compare_providers=shadow_compare_providers,
            trade_memory_file=resolve_project_path(_env_lookup(env, ("AI_TRADE_MEMORY_FILE",), "data/ai_trade_memory.sqlite3")),
            provider_circuit_failure_threshold=_env_int(env, "AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", 3, warnings, min_value=1, max_value=100),
            provider_circuit_cooldown_sec=_env_float(env, "AI_PROVIDER_CIRCUIT_COOLDOWN_SEC", 60.0, warnings, min_value=5.0, max_value=3600.0),
            validation_warnings=tuple(warnings),
        )

    def safe_log_dict(self) -> Dict[str, Any]:
        return {
            "use_remote_api": self.use_remote_api,
            "provider_config_valid": self.provider_config_valid,
            "provider_config_errors": list(self.provider_config_errors),
            "provider_mode": (
                PROVIDER_MODE_REMOTE if self.use_remote_api is True
                else PROVIDER_MODE_LOCAL if self.use_remote_api is False
                else "UNAVAILABLE"
            ),
            "model": self.model,
            "expectancy_model": self.expectancy_model,
            "fallback_models": self.fallback_models,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
            "legacy_min_self_reported_confidence_diagnostic": self.legacy_min_self_reported_confidence_diagnostic,
            "prompt_cache_enable": self.prompt_cache_enable,
            "prompt_cache_key": self.prompt_cache_key if self.prompt_cache_enable else "",
            "prompt_cache_retention": self.prompt_cache_retention if self.prompt_cache_enable else "",
            "decision_cache_enable": self.decision_cache_enable,
            "decision_cache_ttl_sec": self.decision_cache_ttl_sec,
            "decision_cache_file": str(self.decision_cache_file),
            "use_batch_api": self.use_batch_api,
            "batch_only_for_backtest": self.batch_only_for_backtest,
            "batch_output_dir": str(self.batch_output_dir),
            "batch_max_pending": self.batch_max_pending,
            "use_flex": self.use_flex,
            "allow_flex_for_live": self.allow_flex_for_live,
            "flex_live_ack": self.flex_live_ack,
            "service_tier": self.service_tier,
            "openai_timeout_sec": self.openai_timeout_sec,
            "openai_flex_timeout_sec": self.openai_flex_timeout_sec,
            "flex_unavailable_retry_enable": self.flex_unavailable_retry_enable,
            "flex_unavailable_max_retries": self.flex_unavailable_max_retries,
            "flex_unavailable_cooldown_sec": self.flex_unavailable_cooldown_sec,
            "require_runtime_inputs_live": self.require_runtime_inputs_live,
            "reject_on_missing_runtime_inputs_live": self.reject_on_missing_runtime_inputs_live,
            "hard_pre_gate_before_openai": self.hard_pre_gate_before_openai,
            "log_skipped_calls": self.log_skipped_calls,
            "enable_snapshots": self.enable_snapshots,
            "cost_report_enable": self.cost_report_enable,
            "cost_report_file": str(self.cost_report_file),
            "live_bucket_priors_file": str(self.live_bucket_priors_file),
            "require_live_bucket_priors": self.require_live_bucket_priors,
            "live_bucket_priors_max_age_days": self.live_bucket_priors_max_age_days,
            "request_response_fingerprint_file": str(self.request_response_fingerprint_file),
            "require_repeatability_live": self.require_repeatability_live,
            "shadow_repeat_enable": self.shadow_repeat_enable,
            "shadow_repeat_sample_rate": self.shadow_repeat_sample_rate,
            "shadow_repeat_count": self.shadow_repeat_count,
            "shadow_repeat_min_evaluated_candidates": self.shadow_repeat_min_evaluated_candidates,
            "shadow_repeat_max_score_stddev": self.shadow_repeat_max_score_stddev,
            "shadow_repeat_min_decision_agreement": self.shadow_repeat_min_decision_agreement,
            "shadow_repeat_min_chosen_candidate_agreement": self.shadow_repeat_min_chosen_candidate_agreement,
            "shadow_repeat_min_veto_agreement": self.shadow_repeat_min_veto_agreement,
            "shadow_repeat_min_target_choice_agreement": self.shadow_repeat_min_target_choice_agreement,
            "shadow_repeat_artifact_file": str(self.shadow_repeat_artifact_file),
            "local_base_url_class": endpoint_class(self.local_base_url),
            "local_model": self.local_model if self.use_remote_api is False else "",
            "local_analyst_model": self.local_analyst_model if self.use_remote_api is False else "",
            "local_critic_model": self.local_critic_model if self.use_remote_api is False else "",
            "local_adjudicator_model": self.local_adjudicator_model if self.use_remote_api is False else "",
            "local_fallback_models": self.local_fallback_models if self.use_remote_api is False else [],
            "local_model_path_configured": bool(self.local_model_path) if self.use_remote_api is False else False,
            "local_healthcheck_path": self.local_healthcheck_path if self.use_remote_api is False else "",
            "local_timeout_sec": self.local_timeout_sec if self.use_remote_api is False else 0.0,
            "local_max_retries": self.local_max_retries if self.use_remote_api is False else 0,
            "local_max_output_tokens": self.local_max_output_tokens if self.use_remote_api is False else 0,
            "local_temperature": self.local_temperature if self.use_remote_api is False else 0.0,
            "local_top_p": self.local_top_p if self.use_remote_api is False else 0.0,
            "local_seed": self.local_seed if self.use_remote_api is False else 0,
            "local_enable_thinking": self.local_enable_thinking if self.use_remote_api is False else False,
            "local_require_json_schema": self.local_require_json_schema if self.use_remote_api is False else False,
            "local_parallelism": self.local_parallelism if self.use_remote_api is False else 0,
            "local_context_budget_tokens": self.local_context_budget_tokens if self.use_remote_api is False else 0,
            "local_retrieval_top_k": self.local_retrieval_top_k,
            "local_non_loopback_ack": self.local_allow_non_loopback_ack,
            "shadow_compare_providers": self.shadow_compare_providers,
            "trade_memory_file": str(self.trade_memory_file),
            "provider_circuit_failure_threshold": self.provider_circuit_failure_threshold,
            "provider_circuit_cooldown_sec": self.provider_circuit_cooldown_sec,
            "validation_warnings": list(self.validation_warnings),
        }


# ---------- Provider-neutral AI transport ----------
AI_CONFIG = AIGateRuntimeConfig.from_env()
# Explicit migration globals remain for old diagnostics/tests. In local mode
# the remote secret variables are not read and remain empty.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() if AI_CONFIG.use_remote_api is True else ""
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() if AI_CONFIG.use_remote_api is True else ""
OPENAI_MODEL = AI_CONFIG.model
OPENAI_FALLBACK_MODELS = AI_CONFIG.fallback_models
AI_REASONING_EFFORT = AI_CONFIG.reasoning_effort
SNAPSHOT_MAX_BYTES = max(64_000, int(os.getenv("AI_SNAPSHOT_MAX_BYTES", "350000")))
AI_ENABLE_SNAPSHOTS = AI_CONFIG.enable_snapshots
AI_MIN_OUTPUT_TOKENS = 1024
AI_DEFAULT_OUTPUT_TOKENS = 25000
AI_MAX_OUTPUT_TOKENS = AI_CONFIG.max_output_tokens
AI_PROMPT_CACHE_KEY = AI_CONFIG.prompt_cache_key if AI_CONFIG.prompt_cache_enable else ""
_MAX_PROVIDER_ROLE_TIMEOUT_SEC = max(
    float(AI_CONFIG.openai_timeout_sec),
    float(AI_CONFIG.openai_flex_timeout_sec),
    float(AI_CONFIG.local_timeout_sec),
)
EXPECTED_MAX_PROVIDER_DURATION_SEC = max(
    300.0,
    _MAX_PROVIDER_ROLE_TIMEOUT_SEC * 6.0 + 120.0,
)
REQUEST_LOCK_STALE_SEC = max(
    int(EXPECTED_MAX_PROVIDER_DURATION_SEC),
    int(os.getenv("AI_REQUEST_LOCK_STALE_SEC", "1800")),
)
REQUEST_STABLE_MS = max(20, int(os.getenv("AI_REQUEST_STABLE_MS", "120")))
PRODUCER_LOCK_TIMEOUT_SEC = max(
    5.0, float(os.getenv("AI_PRODUCER_LOCK_TIMEOUT_SEC", "60"))
)
RESP_ENCODING = os.getenv("AI_RESPONSE_ENCODING", "utf-16")
ANALYTICS_AUTO_ACTIVATE = os.getenv("ANALYTICS_AUTO_ACTIVATE", "false").strip().lower() in {"1", "true", "yes", "on"}
AI_GATE_MODEL_VERSION = "po3-provider-neutral-consensus-20260718-v1"
LIVE_BUCKET_PRIORS_FILE = AI_CONFIG.live_bucket_priors_file
LOG_FILE = None  # will be set in main() once the bus path is known
_AI_RUNTIME_CONFIG_LOGGED = False
_REPEATABILITY_LOCK = Lock()
_FINGERPRINT_LOG_LOCK = Lock()
FILE_BUS_LIFECYCLE: FileBusLifecycle | None = None
REQUEST_IDEMPOTENCY_LEDGER: RequestIdempotencyLedger | None = None
_CLAIM_DEFERRED_STATE: Dict[str, Dict[str, float]] = {}
_UNSTABLE_FILE_STATE: Dict[str, Dict[str, float]] = {}
AI_PROVIDER: AIProvider | None = None
SHADOW_AI_PROVIDER: AIProvider | None = None
_PROVIDER_STARTUP_HEALTH: Dict[str, Any] = {}
_PROVIDER_HEALTH_CHECKED_MONOTONIC = 0.0
_TRADE_MEMORY_STORE: TradeMemoryStore | None = None
_TRADE_MEMORY_LOCK = Lock()
_SHADOW_PROVIDER_LOCK = Lock()
_SHADOW_COMPARISON_LOG_LOCK = Lock()
_STRUCTURED_SCHEMA_PREFLIGHT: Dict[str, Any] = {}

def log(msg: str) -> None:
    print(msg, flush=True)
    global LOG_FILE
    if LOG_FILE:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def _build_ai_provider(config: AIGateRuntimeConfig | None = None) -> AIProvider:
    cfg = config or AI_CONFIG
    if not cfg.provider_config_valid or cfg.use_remote_api is None:
        return UnavailableProvider(";".join(cfg.provider_config_errors) or "provider_configuration_invalid")
    if cfg.use_remote_api:
        # This is the only branch allowed to read the remote secret.
        remote_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not remote_key:
            return UnavailableProvider("OPENAI_API_KEY=missing_remote")
        return RemoteAPIProvider(
            api_key=remote_key,
            base_url=os.environ.get("OPENAI_BASE_URL", "").strip(),
            primary_model=cfg.model,
            fallback_models=cfg.fallback_models,
            analytics_model=cfg.expectancy_model,
            reasoning_effort=cfg.reasoning_effort,
            timeout_sec=cfg.openai_timeout_sec,
            max_output_tokens=cfg.max_output_tokens,
            prompt_cache_enable=cfg.prompt_cache_enable,
            prompt_cache_key=cfg.prompt_cache_key,
            prompt_cache_retention=cfg.prompt_cache_retention,
            service_tier=cfg.service_tier,
            flex_unavailable_retry_enable=cfg.flex_unavailable_retry_enable,
            flex_unavailable_max_retries=cfg.flex_unavailable_max_retries,
            flex_unavailable_cooldown_sec=cfg.flex_unavailable_cooldown_sec,
            circuit_failure_threshold=cfg.provider_circuit_failure_threshold,
            circuit_cooldown_sec=cfg.provider_circuit_cooldown_sec,
            log=log,
        )
    return LocalOpenAICompatibleProvider(
        base_url=cfg.local_base_url,
        api_key=cfg.local_api_key,
        analyst_model=cfg.local_analyst_model,
        critic_model=cfg.local_critic_model,
        adjudicator_model=cfg.local_adjudicator_model,
        fallback_models=cfg.local_fallback_models,
        healthcheck_path=cfg.local_healthcheck_path,
        timeout_sec=cfg.local_timeout_sec,
        max_retries=cfg.local_max_retries,
        max_output_tokens=cfg.local_max_output_tokens,
        temperature=cfg.local_temperature,
        top_p=cfg.local_top_p,
        seed=cfg.local_seed,
        enable_thinking=cfg.local_enable_thinking,
        require_json_schema=cfg.local_require_json_schema,
        parallelism=cfg.local_parallelism,
        context_budget_tokens=cfg.local_context_budget_tokens,
        circuit_failure_threshold=cfg.provider_circuit_failure_threshold,
        circuit_cooldown_sec=cfg.provider_circuit_cooldown_sec,
        log=log,
    )


def _provider() -> AIProvider:
    global AI_PROVIDER
    if AI_PROVIDER is None:
        AI_PROVIDER = _build_ai_provider()
    return AI_PROVIDER


def _build_non_selected_shadow_provider() -> AIProvider:
    """Build the non-selected provider for research-only comparison.

    Local-authoritative mode never reads the remote API secret, even for a
    shadow request. Remote-authoritative research may explicitly compare the
    configured loopback-compatible local server.
    """

    selected = _provider()
    if selected.provider_mode == PROVIDER_MODE_LOCAL:
        return UnavailableProvider("remote_shadow_forbidden_by_local_privacy_contract")
    local_class = endpoint_class(AI_CONFIG.local_base_url)
    if local_class == "invalid":
        return UnavailableProvider("shadow_local_endpoint_invalid")
    if local_class != "loopback" and not AI_CONFIG.local_allow_non_loopback_ack:
        return UnavailableProvider("shadow_local_non_loopback_without_ack")
    return LocalOpenAICompatibleProvider(
        base_url=AI_CONFIG.local_base_url,
        api_key=AI_CONFIG.local_api_key,
        analyst_model=AI_CONFIG.local_analyst_model,
        critic_model=AI_CONFIG.local_critic_model,
        adjudicator_model=AI_CONFIG.local_adjudicator_model,
        fallback_models=AI_CONFIG.local_fallback_models,
        healthcheck_path=AI_CONFIG.local_healthcheck_path,
        timeout_sec=AI_CONFIG.local_timeout_sec,
        max_retries=AI_CONFIG.local_max_retries,
        max_output_tokens=AI_CONFIG.local_max_output_tokens,
        temperature=AI_CONFIG.local_temperature,
        top_p=AI_CONFIG.local_top_p,
        seed=AI_CONFIG.local_seed,
        enable_thinking=AI_CONFIG.local_enable_thinking,
        require_json_schema=AI_CONFIG.local_require_json_schema,
        parallelism=1,
        context_budget_tokens=AI_CONFIG.local_context_budget_tokens,
        circuit_failure_threshold=AI_CONFIG.provider_circuit_failure_threshold,
        circuit_cooldown_sec=AI_CONFIG.provider_circuit_cooldown_sec,
        log=log,
    )


def _shadow_provider() -> AIProvider:
    global SHADOW_AI_PROVIDER
    with _SHADOW_PROVIDER_LOCK:
        if SHADOW_AI_PROVIDER is None:
            SHADOW_AI_PROVIDER = _build_non_selected_shadow_provider()
        return SHADOW_AI_PROVIDER


def _refresh_provider_health(*, force: bool = False) -> Dict[str, Any]:
    global _PROVIDER_STARTUP_HEALTH, _PROVIDER_HEALTH_CHECKED_MONOTONIC
    now = time.monotonic()
    if not force and _PROVIDER_STARTUP_HEALTH and now - _PROVIDER_HEALTH_CHECKED_MONOTONIC < 60.0:
        return dict(_PROVIDER_STARTUP_HEALTH)
    provider = _provider()
    probe = provider.provider_mode == PROVIDER_MODE_LOCAL
    health = provider.healthcheck(probe_structured=probe)
    _PROVIDER_STARTUP_HEALTH = asdict(health)
    _PROVIDER_HEALTH_CHECKED_MONOTONIC = now
    log(
        "[ai_provider_health]"
        f" healthy={str(health.healthy).lower()} provider_mode={health.provider_mode}"
        f" provider_id={health.provider_id} endpoint_class={health.endpoint_class}"
        f" model_id={health.model_id} model_available={str(health.model_available).lower()}"
        f" structured_output_available={str(health.structured_output_available).lower()}"
        f" reason={health.reason or 'none'}"
    )
    return dict(_PROVIDER_STARTUP_HEALTH)


def _run_structured_schema_preflight() -> Dict[str, Any]:
    global _STRUCTURED_SCHEMA_PREFLIGHT
    provider = _provider()
    rows: list[dict[str, Any]] = []
    for model in all_authoritative_structured_models():
        result = strict_structured_schema(model)
        row = {
            "schema": result.schema_name,
            "valid": result.valid,
            "schema_fingerprint": result.schema_fingerprint,
            "errors": list(result.errors),
        }
        rows.append(row)
        log(
            "[structured_schema_preflight]"
            f" provider={provider.provider_id} schema={result.schema_name}"
            f" valid={str(result.valid).lower()}"
            f" schema_fingerprint={result.schema_fingerprint[:16]}"
            f" errors={json.dumps(list(result.errors), separators=(',', ':'))}"
        )
    _STRUCTURED_SCHEMA_PREFLIGHT = {
        "provider_id": provider.provider_id,
        "valid": all(row["valid"] for row in rows),
        "schemas": rows,
    }
    return dict(_STRUCTURED_SCHEMA_PREFLIGHT)


def _trade_memory_store() -> TradeMemoryStore:
    global _TRADE_MEMORY_STORE
    with _TRADE_MEMORY_LOCK:
        if _TRADE_MEMORY_STORE is None or _TRADE_MEMORY_STORE.path != AI_CONFIG.trade_memory_file:
            _TRADE_MEMORY_STORE = TradeMemoryStore(AI_CONFIG.trade_memory_file)
        return _TRADE_MEMORY_STORE


def _archive_request_terminal(path: Path, state: str, reason: str) -> tuple[bool, str]:
    lifecycle = FILE_BUS_LIFECYCLE
    if lifecycle is not None and path.exists():
        try:
            lifecycle.archive(path, state, reason=reason)
            return True, state
        except Exception as exc:
            log(f"[file_bus] terminal_archive_failed file={path.name} state={state} error={exc}")
    return _try_move_to_stale(path, path.parent.parent / "stale")


def _move_claimed_request_to_processing(path: Path) -> Path:
    lifecycle = FILE_BUS_LIFECYCLE
    if lifecycle is None:
        return path
    return lifecycle.claim(path)


def _append_fingerprint_record(record: Mapping[str, Any]) -> None:
    path = AI_CONFIG.request_response_fingerprint_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FINGERPRINT_LOG_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
            handle.flush()


def _repeatability_thresholds() -> RepeatabilityThresholds:
    return RepeatabilityThresholds(
        minimum_evaluated_candidates=AI_CONFIG.shadow_repeat_min_evaluated_candidates,
        maximum_score_stddev=AI_CONFIG.shadow_repeat_max_score_stddev,
        minimum_decision_agreement=AI_CONFIG.shadow_repeat_min_decision_agreement,
        minimum_chosen_candidate_agreement=AI_CONFIG.shadow_repeat_min_chosen_candidate_agreement,
        minimum_veto_agreement=AI_CONFIG.shadow_repeat_min_veto_agreement,
        minimum_target_choice_agreement=AI_CONFIG.shadow_repeat_min_target_choice_agreement,
    )


def _repeatability_group_fields(
    model: str,
    quality_tier: str,
    *,
    provider_mode: str = "",
    provider_id: str = "",
    model_fingerprint: str = "",
    generation_settings_hash: str = "",
) -> Dict[str, str]:
    identity = _provider().identity("analyst")
    return {
        "model": str(model or AI_CONFIG.model),
        "provider_mode": str(provider_mode or identity.get("provider_mode") or "UNAVAILABLE"),
        "provider_id": str(provider_id or identity.get("provider_id") or "unavailable"),
        "model_fingerprint": str(model_fingerprint or identity.get("model_fingerprint") or "unavailable"),
        "reasoning_effort": AI_CONFIG.reasoning_effort,
        "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "target_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        "family_profile_version": FAMILY_PROFILE_VERSION,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        "generation_settings_hash": str(
            generation_settings_hash or identity.get("generation_settings_hash") or "unavailable"
        ),
        "decision_quality_tier": str(quality_tier or DECISION_QUALITY_FULL_STRUCTURED),
    }


def _repeatability_group_key(model: str, quality_tier: str) -> str:
    return canonical_hash(_repeatability_group_fields(model, quality_tier))


def _empty_repeatability_artifact(load_status: str, detail: str = "") -> Dict[str, Any]:
    return {
        "schema_version": REPEATABILITY_SCHEMA_VERSION,
        "groups": {},
        "generated_at": "",
        "_load_status": load_status,
        "_load_detail": detail,
    }


def _load_repeatability_artifact() -> Dict[str, Any]:
    path = AI_CONFIG.shadow_repeat_artifact_file
    if not path.is_file():
        return _empty_repeatability_artifact("missing_artifact")
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return _empty_repeatability_artifact("empty_artifact")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return _empty_repeatability_artifact("invalid_artifact_type")
        if data.get("schema_version") != REPEATABILITY_SCHEMA_VERSION:
            return _empty_repeatability_artifact(
                "incompatible_schema", str(data.get("schema_version") or "missing")
            )
        if not isinstance(data.get("groups"), dict):
            return _empty_repeatability_artifact("invalid_groups")
        data["_load_status"] = "ok"
        data["_load_detail"] = ""
        return data
    except Exception as exc:
        log(f"[repeatability] artifact_load_failed path={path} error={exc}")
        return _empty_repeatability_artifact("unreadable_or_invalid_json", str(exc))


def _ensure_repeatability_artifact_container() -> bool:
    """Create a zero-observation container only when shadow collection is enabled.

    The container never grants authority: an absent exact group still resolves
    to UNAVAILABLE.  It only makes the configured collector ready for atomic,
    schema-versioned observations instead of reporting a missing output path.
    """

    path = AI_CONFIG.shadow_repeat_artifact_file
    if not AI_CONFIG.shadow_repeat_enable or path.is_file():
        return False
    with _REPEATABILITY_LOCK, _repeatability_update_lock():
        if path.is_file():
            return False
        artifact: Dict[str, Any] = {
            "schema_version": REPEATABILITY_SCHEMA_VERSION,
            "groups": {},
            "generated_at": int(time.time()),
        }
        artifact["artifact_hash"] = canonical_hash(artifact)
        governance_atomic_write_json(path, artifact)
    log(
        "[repeatability] artifact_container_initialized=true"
        f" path={path} groups=0 trading_authority=false"
    )
    return True


@contextmanager
def _repeatability_update_lock(timeout_sec: float = 10.0):
    """Cross-process lock for the repeatability artifact.

    The bridge can be restarted while an older process is still draining the
    file bus, so the in-process Lock alone is insufficient.
    """

    lock_path = AI_CONFIG.shadow_repeat_artifact_file.with_suffix(
        AI_CONFIG.shadow_repeat_artifact_file.suffix + ".lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.1, timeout_sec)
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()} created={time.time():.6f}\n".encode("ascii"))
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 120.0:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"repeatability_artifact_lock_timeout:{lock_path}")
            time.sleep(0.025)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            lock_path.unlink(missing_ok=True)


def _repeatability_authority_for(
    model: str,
    quality_tier: str,
    *,
    provider_mode: str = "",
    provider_id: str = "",
    model_fingerprint: str = "",
    generation_settings_hash: str = "",
) -> Dict[str, Any]:
    artifact = _load_repeatability_artifact()
    expected_fields = _repeatability_group_fields(
        model,
        quality_tier,
        provider_mode=provider_mode,
        provider_id=provider_id,
        model_fingerprint=model_fingerprint,
        generation_settings_hash=generation_settings_hash,
    )
    group_key = canonical_hash(expected_fields)
    load_status = str(artifact.get("_load_status") or "ok")
    if load_status != "ok":
        return {
            "status": UNAVAILABLE,
            "score_threshold_authority": False,
            "trading_eligible": False,
            "reason": "repeatability_unavailable",
            "artifact_state": load_status,
            "artifact_detail": str(artifact.get("_load_detail") or ""),
            "group_key": group_key,
            "artifact_hash": "",
        }
    groups = artifact.get("groups") if isinstance(artifact.get("groups"), dict) else {}
    group = groups.get(group_key)
    if not isinstance(group, dict):
        return {
            "status": UNAVAILABLE,
            "score_threshold_authority": False,
            "trading_eligible": False,
            "reason": "repeatability_unavailable",
            "artifact_state": "missing_group",
            "artifact_detail": "",
            "group_key": group_key,
            "artifact_hash": str(artifact.get("artifact_hash") or ""),
        }
    mismatches = [
        name for name, expected in expected_fields.items()
        if str(group.get(name) or "") != str(expected)
    ]
    if mismatches:
        return {
            "status": UNAVAILABLE,
            "score_threshold_authority": False,
            "trading_eligible": False,
            "reason": "repeatability_group_mismatch",
            "artifact_state": "group_identity_mismatch",
            "artifact_detail": ",".join(mismatches),
            "group_key": group_key,
            "artifact_hash": str(group.get("artifact_hash") or ""),
        }
    authority = repeatability_authority(group)
    authority["group_key"] = group_key
    authority["artifact_state"] = "ok"
    authority["artifact_detail"] = ""
    authority["artifact_hash"] = str(group.get("artifact_hash") or "")
    return authority


def _decision_repeatability_view(decision: "Decision") -> Dict[str, Any]:
    return {
        "id": decision.decision_id,
        "model_version": decision.model_version,
        "decision_quality_tier": decision.decision_quality_tier,
        "decision_state": decision.decision_state,
        "selected_candidate_id": decision.selected_candidate_id,
        "selected_candidate_hash": decision.selected_candidate_hash,
        "chosen_index": decision.chosen_index,
        "veto_enabled": decision.veto_enabled,
        "veto_code": decision.veto_code,
        "veto_evidence_fields": list(decision.veto_evidence_fields or []),
        "veto_reason": decision.veto_reason,
        "llm_quality_score": decision.llm_quality_score,
        "suggested_risk_multiplier": decision.suggested_risk_multiplier,
        "chosen_target_model": decision.chosen_target_model,
        "target_arbitration": decision.target_arbitration or {},
        "candidate_assessments": decision.candidate_assessments or [],
        "provider_mode": decision.provider_mode,
        "provider_id": decision.provider_id,
        "model_fingerprint": decision.model_fingerprint,
        "generation_settings_hash": decision.generation_settings_hash,
    }


def _update_repeatability_artifact(
    payload: Dict[str, Any],
    primary: "Decision",
    repeated: List["Decision"],
) -> None:
    responses = [_decision_repeatability_view(primary)] + [_decision_repeatability_view(item) for item in repeated]
    model = str(primary.model_version or AI_CONFIG.model)
    quality_tier = str(primary.decision_quality_tier or DECISION_QUALITY_FULL_STRUCTURED)
    per_candidate_thresholds = RepeatabilityThresholds(
        minimum_evaluated_candidates=max(2, len(responses)),
        maximum_score_stddev=AI_CONFIG.shadow_repeat_max_score_stddev,
        minimum_decision_agreement=AI_CONFIG.shadow_repeat_min_decision_agreement,
        minimum_chosen_candidate_agreement=AI_CONFIG.shadow_repeat_min_chosen_candidate_agreement,
        minimum_veto_agreement=AI_CONFIG.shadow_repeat_min_veto_agreement,
        minimum_target_choice_agreement=AI_CONFIG.shadow_repeat_min_target_choice_agreement,
    )
    observation = evaluate_repeatability(
        responses,
        model=model,
        prompt_contract_version=AI_PROMPT_CONTRACT_VERSION,
        decision_schema_version=AI_DECISION_SCHEMA_VERSION,
        target_schema_version=AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        decision_quality_tier=quality_tier,
        thresholds=per_candidate_thresholds,
    )
    observation["request_id"] = str(payload.get("id") or "")
    observation["candidate_contract_hash"] = canonical_hash(
        [
            {
                "candidate_id": candidate.get("candidate_id"),
                "candidate_hash": candidate.get("candidate_hash"),
                "request_execution_fingerprint": candidate.get("request_execution_fingerprint"),
            }
            for candidate in payload.get("candidates", [])
            if isinstance(candidate, dict)
        ]
    )
    group_fields = _repeatability_group_fields(
        model,
        quality_tier,
        provider_mode=primary.provider_mode,
        provider_id=primary.provider_id,
        model_fingerprint=primary.model_fingerprint,
        generation_settings_hash=primary.generation_settings_hash,
    )
    group_key = canonical_hash(group_fields)
    observation["observation_id"] = canonical_hash(
        {
            "request_id": observation["request_id"],
            "candidate_contract_hash": observation["candidate_contract_hash"],
            "group_key": group_key,
        }
    )
    with _REPEATABILITY_LOCK, _repeatability_update_lock():
        artifact = _load_repeatability_artifact()
        if str(artifact.get("_load_status") or "ok") != "ok":
            artifact = _empty_repeatability_artifact("ok")
        groups = artifact.setdefault("groups", {})
        group = groups.get(group_key) if isinstance(groups.get(group_key), dict) else {}
        observations = group.get("observations") if isinstance(group.get("observations"), list) else []
        if any(
            isinstance(item, dict)
            and str(item.get("observation_id") or "") == observation["observation_id"]
            for item in observations
        ):
            log(
                f"[repeatability] duplicate_observation_skipped request_id={observation['request_id']} "
                f"group={group_key[:12]}"
            )
            return
        observations.append(observation)
        observations = observations[-1000:]
        metric_names = (
            "llm_quality_score_stddev",
            "decision_agreement_rate",
            "chosen_candidate_agreement_rate",
            "veto_agreement_rate",
            "risk_multiplier_stddev",
            "target_choice_agreement_rate",
            "response_fingerprint_uniqueness",
        )
        aggregate_metrics = {
            name: statistics.fmean(float(item.get("metrics", {}).get(name, 0.0)) for item in observations)
            for name in metric_names
        }
        thresholds = _repeatability_thresholds()
        evaluated_candidates = len(observations)
        if evaluated_candidates < thresholds.minimum_evaluated_candidates:
            status = INSUFFICIENT_SAMPLE
        elif (
            aggregate_metrics["decision_agreement_rate"] < thresholds.minimum_decision_agreement
            or aggregate_metrics["chosen_candidate_agreement_rate"] < thresholds.minimum_chosen_candidate_agreement
            or aggregate_metrics["veto_agreement_rate"] < thresholds.minimum_veto_agreement
            or aggregate_metrics["target_choice_agreement_rate"] < thresholds.minimum_target_choice_agreement
        ):
            status = DECISION_NON_REPEATABLE
        elif aggregate_metrics["llm_quality_score_stddev"] > thresholds.maximum_score_stddev:
            status = SCORE_NON_REPEATABLE
        else:
            status = REPEATABLE
        group = {
            **group_fields,
            "status": status,
            "evaluated_candidates": evaluated_candidates,
            "thresholds": asdict(thresholds),
            "metrics": aggregate_metrics,
            "observations": observations,
            # Repeatability can remove live authority, but it never grants
            # numeric LLM-score authority. Family score comparisons remain
            # diagnostic under the Version Z qualitative-veto contract.
            "score_threshold_authority": status == REPEATABLE,
            "trading_eligible": status == REPEATABLE,
            "updated_at": int(time.time()),
        }
        group["artifact_hash"] = canonical_hash({key: value for key, value in group.items() if key != "observations"})
        groups[group_key] = group
        artifact["schema_version"] = REPEATABILITY_SCHEMA_VERSION
        artifact["generated_at"] = int(time.time())
        artifact["artifact_hash"] = canonical_hash({"schema_version": artifact["schema_version"], "groups": groups})
        governance_atomic_write_json(AI_CONFIG.shadow_repeat_artifact_file, artifact)
    log(
        f"[repeatability] group={group_key[:12]} model={model} status={status} "
        f"evaluated_candidates={evaluated_candidates} score_stddev={aggregate_metrics['llm_quality_score_stddev']:.4f} "
        f"decision_agreement={aggregate_metrics['decision_agreement_rate']:.4f} "
        f"chosen_agreement={aggregate_metrics['chosen_candidate_agreement_rate']:.4f} "
        f"veto_agreement={aggregate_metrics['veto_agreement_rate']:.4f}"
    )


def _shadow_repeat_sampled(payload: Dict[str, Any]) -> bool:
    if not AI_CONFIG.shadow_repeat_enable or AI_CONFIG.shadow_repeat_sample_rate <= 0:
        return False
    request_id = str(payload.get("id") or canonical_hash(payload))
    bucket = int(sha256(request_id.encode("utf-8")).hexdigest()[:12], 16) / float(0xFFFFFFFFFFFF)
    return bucket < AI_CONFIG.shadow_repeat_sample_rate


def _run_shadow_repeat_evaluation(payload: Dict[str, Any], primary: "Decision") -> None:
    if not _shadow_repeat_sampled(payload):
        return
    if (
        primary.decision_quality_tier != DECISION_QUALITY_FULL_STRUCTURED
        or not primary.mandatory_fields_complete
        or primary.decision_source
        in {
            "request_identity_mismatch",
            "structured_response_invalid",
            "provider_failure",
        }
    ):
        log(
            f"[repeatability] skipped request_id={payload.get('id')}"
            " reason=primary_transport_or_identity_not_authoritative"
            f" quality_tier={primary.decision_quality_tier}"
            f" source={primary.decision_source}"
        )
        return
    repeated: List[Decision] = []
    for repeat_index in range(max(0, AI_CONFIG.shadow_repeat_count - 1)):
        shadow_payload = json.loads(json.dumps(payload))
        primary_request_id = str(payload.get("id") or "")
        shadow_payload["id"] = (
            f"{primary_request_id}__shadow_repeat_{repeat_index + 1}"
        )
        if shadow_payload.get("request_nonce"):
            shadow_payload["request_nonce"] = (
                f"{shadow_payload['request_nonce']}__shadow_{repeat_index + 1}"
            )
        shadow_payload["shadow_repeat"] = {
            "enabled": True,
            "repeat_index": repeat_index + 1,
            "primary_request_id": primary_request_id,
            "trading_authority": False,
            "cache_eligible": False,
        }
        shadow_payload["workload_mode"] = "research"
        try:
            shadow_frozen = _freeze_request_for_provider(
                shadow_payload,
                _provider(),
            )
            shadow_payload = shadow_frozen.thaw_payload()
            _attach_frozen_identity(shadow_payload, shadow_frozen)
            repeated.append(
                _score_setup_ai(
                    shadow_payload,
                    non_authoritative_shadow=True,
                    frozen_request=shadow_frozen,
                )
            )
        except Exception as exc:
            log(
                f"[repeatability] shadow_repeat_failed"
                f" primary_request_id={primary_request_id}"
                f" shadow_request_id={shadow_payload.get('id')}"
                f" index={repeat_index + 1} error={exc}"
            )
    if repeated:
        try:
            _update_repeatability_artifact(payload, primary, repeated)
        except Exception as exc:
            log(
                f"[repeatability] artifact_update_failed request_id={payload.get('id')} "
                f"error={exc} trading_authority=false"
            )


def _apply_repeatability_authority(
    decision: "Decision",
    authority: Dict[str, Any] | None = None,
    *,
    payload: Dict[str, Any] | None = None,
) -> "Decision":
    authority = authority or _repeatability_authority_for(
        str(decision.model_version or AI_CONFIG.model),
        str(decision.decision_quality_tier or DECISION_QUALITY_FULL_STRUCTURED),
        provider_mode=decision.provider_mode,
        provider_id=decision.provider_id,
        model_fingerprint=decision.model_fingerprint,
        generation_settings_hash=decision.generation_settings_hash,
    )
    strict_live = bool(AI_CONFIG.require_repeatability_live and _is_live_payload(payload))
    authority = dict(authority)
    authority["required_live"] = strict_live
    if strict_live and authority.get("status") != REPEATABLE:
        decision.allow = False
        decision.raw_allow = False
        decision.model_raw_allow = False
        decision.python_final_allow = False
        decision.decision_state = DECISION_ABSTAIN
        decision.decision_source = "repeatability_authority_gate"
        if decision.rejection_codes is None:
            decision.rejection_codes = []
        repeatability_code = str(authority.get("reason") or "repeatability_unavailable")
        if authority.get("status") == INSUFFICIENT_SAMPLE:
            repeatability_code = "repeatability_insufficient_sample"
        elif authority.get("status") == SCORE_NON_REPEATABLE:
            repeatability_code = "model_prompt_score_non_repeatable"
        elif authority.get("status") == DECISION_NON_REPEATABLE:
            repeatability_code = "model_prompt_decision_non_repeatable"
        elif authority.get("artifact_state") == "group_identity_mismatch":
            repeatability_code = "repeatability_group_mismatch"
        for code in ("ai_abstain", repeatability_code):
            if code not in decision.rejection_codes:
                decision.rejection_codes.append(code)
        decision.suggested_risk_multiplier = 0.0
        log(
            f"[repeatability_authority] request_id={str((payload or {}).get('id') or '')} "
            f"workload_mode={_payload_workload_mode(payload)} required_live=true "
            f"status={authority.get('status')} artifact_state={authority.get('artifact_state')} "
            f"action=fail_closed rejection_code={repeatability_code}"
        )
    if not isinstance(decision.reasons, dict):
        decision.reasons = {"ai_reasons": str(decision.reasons or "")}
    decision.reasons["repeatability_authority"] = authority
    return decision


def _payload_workload_mode(payload: Dict[str, Any] | None) -> str:
    payload = payload or {}
    mode = canonical_workload_mode(payload)
    if mode == LIVE_FORWARD:
        return LIVE_FORWARD
    if mode in {"TESTER_AI_RECORD_ONLY", "TESTER_AI_CACHE_ONLY", "TESTER_AI_LIVE_WAIT_DEBUG"}:
        return "backtest"
    if mode in {"backtest", "replay", "research", "analytics"}:
        return mode
    # Fail safe: an unclassified file-bus approval gets the strict forward
    # contract, never a more permissive research contract.
    return LIVE_FORWARD


def _is_live_payload(payload: Dict[str, Any] | None) -> bool:
    return _payload_workload_mode(payload) == LIVE_FORWARD


def _log_ai_runtime_config_once() -> None:
    global _AI_RUNTIME_CONFIG_LOGGED
    if _AI_RUNTIME_CONFIG_LOGGED:
        return
    _AI_RUNTIME_CONFIG_LOGGED = True
    log(f"[ai_gate] Active AI config: {json.dumps(AI_CONFIG.safe_log_dict(), sort_keys=True)}")
    log(
        "[ai_gate] prompt_cache_enabled="
        + str(AI_CONFIG.prompt_cache_enable).lower()
        + f" prompt_cache_key={AI_CONFIG.prompt_cache_key if AI_CONFIG.prompt_cache_enable else ''}"
        + f" prompt_cache_retention={AI_CONFIG.prompt_cache_retention if AI_CONFIG.prompt_cache_enable else ''}"
    )
    forward = live_forward_behavior_contract()
    log(
        "[live_forward_mode]"
        f" workload_mode={LIVE_FORWARD} behavior_contract_hash={forward['behavior_contract_hash']}"
        " demo_real_equivalent=true account_destination_only_difference=true"
    )


def _effective_service_tier(payload: Dict[str, Any] | None) -> tuple[str, bool, str]:
    requested = "flex" if AI_CONFIG.use_flex else AI_CONFIG.service_tier
    requested = (requested or "auto").lower()
    if _is_live_payload(payload) and requested == "flex":
        if AI_CONFIG.use_flex and AI_CONFIG.allow_flex_for_live and AI_CONFIG.flex_live_ack:
            return "flex", True, ""
        return "auto", False, "flex_disabled_for_live"
    return requested, requested == "flex", ""


def _openai_timeout_for_payload(payload: Dict[str, Any] | None) -> float:
    _service_tier, flex_used, _disabled_reason = _effective_service_tier(payload)
    if flex_used:
        return float(AI_CONFIG.openai_flex_timeout_sec)
    return float(AI_CONFIG.openai_timeout_sec)


def _provider_request_metadata(
    payload: Dict[str, Any] | None,
    provider: AIProvider | None = None,
) -> Dict[str, Any]:
    selected_provider = provider or _provider()
    service_tier, _flex_used, _disabled_reason = _effective_service_tier(payload)
    timeout_sec = (
        _openai_timeout_for_payload(payload)
        if selected_provider.provider_mode == PROVIDER_MODE_REMOTE
        else AI_CONFIG.local_timeout_sec
    )
    return {
        "service_tier": service_tier if selected_provider.provider_mode == PROVIDER_MODE_REMOTE else "auto",
        "timeout_sec": float(timeout_sec),
        "workload_mode": _payload_workload_mode(payload or {}),
        "request_id": str((payload or {}).get("id") or ""),
        "request_identity_hash": str((payload or {}).get("request_identity_hash") or ""),
        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
    }


def _apply_prompt_cache_kwargs(kwargs: Dict[str, Any]) -> None:
    if not AI_CONFIG.prompt_cache_enable:
        return
    kwargs["prompt_cache_key"] = AI_CONFIG.prompt_cache_key
    kwargs["prompt_cache_retention"] = AI_CONFIG.prompt_cache_retention


def _apply_service_tier_kwargs(kwargs: Dict[str, Any], payload: Dict[str, Any] | None) -> tuple[str, bool]:
    service_tier, flex_used, disabled_reason = _effective_service_tier(payload)
    if disabled_reason:
        log(f"[ai_gate] {disabled_reason}")
    if service_tier:
        kwargs["service_tier"] = service_tier
    return service_tier, flex_used

def read_json_any_encoding(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()

    # UTF-16 BOMs
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    # UTF-8 BOM
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        # default (most common)
        text = raw.decode("utf-8")

    value = strict_json_loads(text)
    if not isinstance(value, dict):
        raise ValueError("file_bus_json_root_must_be_object")
    return value

def _remote_batch_client(timeout_sec: float | None = None):
    """Build the remote SDK client for the remote-only research Batch API."""

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "Missing OpenAI API key. Set OPENAI_API_KEY as an environment variable."
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Missing dependency 'openai'. Install: pip install openai pydantic") from e
    kwargs = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    if timeout_sec is not None and timeout_sec > 0:
        kwargs["timeout"] = float(timeout_sec)
    return OpenAI(**kwargs)


def _reasoning_config_for_model(model_name: str) -> Dict[str, str] | None:
    name = str(model_name or "").strip().lower()
    effort = AI_REASONING_EFFORT
    if effort == "auto":
        # Older GPT-5 family models default to medium reasoning, which can consume
        # the entire max_output_tokens budget before any visible JSON is emitted.
        if name.startswith("gpt-5.4-nano") or name.startswith("gpt-5.4-mini") or name.startswith("gpt-5.1"):
            effort = "low"
        elif name.startswith("gpt-5.5"):
            effort = "low"
        elif name.startswith("gpt-5"):
            effort = "minimal"
        else:
            effort = ""
    # Some newer GPT-5 family models reject "minimal"; use the nearest supported
    # effort so one bad parameter does not force a model-family fallback.
    if effort == "minimal" and (name.startswith("gpt-5.5") or name.startswith("gpt-5.4-nano")):
        effort = "low"
    if effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        return {"effort": effort}
    return None


def _snapshot_to_input_part(path_str: str, label: str) -> tuple[Dict[str, Any] | None, str]:
    # Handle empty/null paths gracefully
    if not path_str or not str(path_str).strip():
        return None, f"{label}=not_provided"
    path_str = str(path_str).strip()
    if path_str == "__tester_skipped__":
        return None, f"{label}=tester_skipped"
    if path_str == "__capture_failed__":
        return None, f"{label}=capture_failed"
    try:
        path = Path(path_str)
    except Exception as e:
        return None, f"{label}=bad_path({type(e).__name__})"
    if not path.exists() or not path.is_file():
        return None, f"{label}=file_not_found"
    size = path.stat().st_size
    if size <= 0:
        return None, f"{label}=empty_file"
    if size > SNAPSHOT_MAX_BYTES:
        return None, f"{label}=too_large({size})"
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    try:
        raw = path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"}, f"{label}=ok({size})"
    except Exception as e:
        return None, f"{label}=read_error({type(e).__name__})"


def _snapshot_parts(payload: Dict[str, Any]) -> tuple[list[Dict[str, Any]], list[str]]:
    if not AI_ENABLE_SNAPSHOTS:
        return [], ["snapshots=disabled"]
    snaps = _as_dict(payload.get("snapshots"))
    parts: list[Dict[str, Any]] = []
    notes: list[str] = []
    for key, label in (("htf_path", "htf"), ("ltf_path", "ltf")):
        part, note = _snapshot_to_input_part(str(snaps.get(key, "") or ""), label)
        notes.append(note)
        if part is not None:
            parts.append(part)
    return parts, notes


def _ascii_compact(text: str, max_len: int = 480) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = "".join(ch if 32 <= ord(ch) <= 126 else " " for ch in text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 3].rstrip() + "..."
    return text or "no_reason"

_LIVE_BUCKET_PRIORS_CACHE: tuple[float, Dict[str, Any]] = (0.0, {})
_PRIOR_STARTUP_AUDIT: Dict[str, Any] = {}

def _live_bucket_priors_path() -> Path:
    return resolve_project_path(LIVE_BUCKET_PRIORS_FILE)


def _refresh_prior_startup_audit() -> Dict[str, Any]:
    global _PRIOR_STARTUP_AUDIT
    audit = audit_prior_artifact(
        _live_bucket_priors_path(),
        mandatory=AI_CONFIG.require_live_bucket_priors,
        max_age_days=AI_CONFIG.live_bucket_priors_max_age_days,
    )
    _PRIOR_STARTUP_AUDIT = audit
    log(
        "[prior_startup_audit] "
        f"absolute_path={audit.get('absolute_path')} exists={str(bool(audit.get('exists'))).lower()} "
        f"hash={audit.get('hash') or ''} mtime={audit.get('mtime')} "
        f"prior_version={audit.get('prior_version') or ''} data_window={json.dumps(audit.get('data_window') or {}, separators=(',', ':'))} "
        f"bucket_count={int(audit.get('bucket_count') or 0)} rejected_count={int(audit.get('rejected_count') or 0)} "
        f"status={audit.get('status')} mandatory={str(AI_CONFIG.require_live_bucket_priors).lower()}"
    )
    return audit

def _load_live_bucket_priors() -> Dict[str, Any]:
    global _LIVE_BUCKET_PRIORS_CACHE
    path = _live_bucket_priors_path()
    try:
        mtime = path.stat().st_mtime
    except Exception:
        return {}
    cached_mtime, cached = _LIVE_BUCKET_PRIORS_CACHE
    if cached_mtime == mtime:
        return cached
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and data.get("schema_version") == HIERARCHICAL_PRIOR_SCHEMA_VERSION
            and data.get("ledger_integrity_status") == "clean_only"
        ):
            _LIVE_BUCKET_PRIORS_CACHE = (mtime, data)
            return data
    except Exception as exc:
        log(f"[ai_gate] live_bucket_priors_load_failed path={path} error={exc}")
    return {}

def _setup_code_for_item(item: Dict[str, Any], payload: Dict[str, Any]) -> str:
    code = str(_get_any(item, ["setup_code", "model_code"], payload.get("setup_code") or payload.get("model_code")) or "").strip().upper()
    if code:
        return code
    comment = str(_get_any(item, ["broker_comment"], payload.get("broker_comment")) or "").strip().upper()
    if "-" in comment:
        prefix = comment.split("-", 1)[0]
        if prefix:
            return prefix
    family = _norm_text(str(_get_any(item, ["setup_family", "setup_class", "entry_branch"], "") or ""))
    if "micro" in family:
        return "MPC"
    return "UNK"

def _session_bucket_for_item(item: Dict[str, Any], payload: Dict[str, Any]) -> str:
    po3 = _as_dict(payload.get("po3"))
    code = _setup_code_for_item(item, payload)
    session = str(_get_any(item, ["session_code"], _get_any(po3, ["session_code"], "")) or "").strip().upper()
    if not session:
        session_name = _norm_text(_get_any(item, ["session_name"], _get_any(po3, ["session_name"], "")) or "")
        if "london" in session_name:
            session = "LON"
        elif "new" in session_name or "ny" in session_name:
            session = "NY"
        elif "asia" in session_name:
            session = "ASIA"
        else:
            session = "OFF"
    killzone = str(_get_any(item, ["killzone_code"], "") or "").strip().upper()
    if not killzone:
        in_killzone = _boolish(_get_any(item, ["in_killzone"], _get_any(po3, ["in_killzone"], False)), False)
        killzone = "K" if in_killzone else "NK"
    return f"{code}-{session}-{killzone}"

def _bucket_prior_for_item(item: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    artifact = _load_live_bucket_priors()
    setup_code = _setup_code_for_item(item, payload)
    session_bucket = _session_bucket_for_item(item, payload)
    symbol = str(payload.get("symbol") or item.get("symbol") or "").upper()
    if not artifact:
        return {
            "schema_version": HIERARCHICAL_PRIOR_SCHEMA_VERSION,
            "artifact_hash": "",
            "available": False,
            "setup_code": setup_code,
            "session_bucket": session_bucket,
            "symbol": symbol,
            "hierarchy": {},
        }
    po3 = _as_dict(payload.get("po3"))
    merged = {
        **item,
        "symbol": symbol,
        "setup_code": setup_code,
        "setup_family": _get_any(item, ["setup_family", "setup_class"], setup_code),
        "session": _get_any(item, ["session_name", "session_code"], _get_any(po3, ["session_name", "session_code"], "OFF")),
        "killzone": _get_any(item, ["killzone_code"], "K" if _boolish(_get_any(po3, ["in_killzone"], False), False) else "NK"),
        "entry_branch": _get_any(item, ["entry_branch", "entry_model"], "unknown"),
        "asset_class": _get_any(item, ["asset_class"], ""),
    }
    result = priors_for_candidate(artifact, merged)
    result.update(
        {
            "available": True,
            "setup_code": setup_code,
            "session_bucket": session_bucket,
            "symbol": symbol,
        }
    )
    return result

def _bucket_prior_hash_for_item(item: Dict[str, Any], payload: Dict[str, Any]) -> str:
    prior = _bucket_prior_for_item(item, payload)
    if not prior or not prior.get("available"):
        return ""
    return str(prior.get("candidate_prior_hash") or canonical_hash(prior))


def _mandatory_live_prior_rejection(payload: Dict[str, Any], best_index: int) -> "Decision | None":
    if not AI_CONFIG.require_live_bucket_priors or not _is_live_payload(payload):
        return None
    audit = _PRIOR_STARTUP_AUDIT or _refresh_prior_startup_audit()
    if audit.get("status") == "ready" and audit.get("trading_eligible"):
        return None
    log(
        "[startup_reject] reason=mandatory_live_priors_unavailable "
        f"status={audit.get('status')} path={audit.get('absolute_path')}"
    )
    return Decision(
        allow=False,
        raw_allow=False,
        score=0.0,
        confidence=0.0,
        chosen_index=best_index,
        decision_state=DECISION_REJECT,
        decision_quality_tier=DECISION_QUALITY_RULE_ONLY_NON_TRADING,
        mandatory_fields_complete=False,
        missing_mandatory_fields=["hierarchical_live_bucket_priors"],
        reasons={"prior_startup_audit": audit},
        decision_source="mandatory_prior_hard_gate",
        rejection_codes=["mandatory_live_priors_unavailable"],
        narrative_state="rejected_before_ai",
        invalidation_risks=["prior_evidence_unavailable"],
        suggested_risk_multiplier=0.0,
        model_version=AI_GATE_MODEL_VERSION,
    )


def _compact_model_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    po3 = _as_dict(payload.get("po3"))
    regime = _as_dict(payload.get("regime"))
    plan = _as_dict(payload.get("plan"))
    fvg = _as_dict(payload.get("fvg"))
    watchlist = _as_dict(payload.get("watchlist"))
    root_target_candidates = _compact_target_candidates(_target_candidates(payload, plan))
    compact: Dict[str, Any] = {
        "id": payload.get("id"),
        "symbol": payload.get("symbol"),
        "is_buy": payload.get("is_buy"),
        "configured_stop_model": payload.get("configured_stop_model"),
        "po3": {
            "has_sweep": po3.get("has_sweep"),
            "has_displacement": po3.get("has_displacement"),
            "has_bos": po3.get("has_bos"),
            "po3_state": po3.get("po3_state"),
            "po3_state_reason": po3.get("po3_state_reason"),
            "sweep_side": po3.get("sweep_side"),
            "structure_type": po3.get("structure_type"),
            "htf_structure_type": po3.get("htf_structure_type"),
            "ltf_structure_type": po3.get("ltf_structure_type"),
            "final_setup_class": po3.get("final_setup_class"),
            "po3_scope": po3.get("po3_scope"),
            "t_sweep": po3.get("t_sweep"),
            "t_disp": po3.get("t_disp"),
            "t_bos": po3.get("t_bos"),
            "dr_high": po3.get("dr_high"),
            "dr_low": po3.get("dr_low"),
            "dr_mid": po3.get("dr_mid"),
            "manip_low": po3.get("manip_low"),
            "manip_high": po3.get("manip_high"),
            "bos_level": po3.get("bos_level"),
            "sweep_strength": po3.get("sweep_strength"),
            "session_name": po3.get("session_name"),
            "in_killzone": po3.get("in_killzone"),
            "liquidity_target": po3.get("liquidity_target"),
            "liquidity_target_high": po3.get("liquidity_target_high"),
            "liquidity_kind": po3.get("liquidity_kind"),
            "liquidity_cluster_count": po3.get("liquidity_cluster_count"),
            "htf_mss": po3.get("htf_mss"),
            "htf_choch": po3.get("htf_choch"),
            "ltf_bos": po3.get("ltf_bos"),
            "ltf_mss": po3.get("ltf_mss"),
            "ltf_choch": po3.get("ltf_choch"),
        },
        "regime": {
            "atr_pct": regime.get("atr_pct"),
            "trend_strength": regime.get("trend_strength"),
            "trend_slope_pct": regime.get("trend_slope_pct"),
            "adx_value": regime.get("adx_value"),
            "adr_pct": regime.get("adr_pct"),
            "session_vol_ratio": regime.get("session_vol_ratio"),
            "vwap_dist_atr": regime.get("vwap_dist_atr"),
            "compression_score": regime.get("compression_score"),
            "expansion_score": regime.get("expansion_score"),
            "news_risk": regime.get("news_risk"),
        },
        "plan": {
            "setup_code": _setup_code_for_item(plan, payload),
            "bucket_prior": _bucket_prior_for_item(plan, payload),
            "entry_est": plan.get("entry_est"),
            "entry_model": plan.get("entry_model"),
            "entry_branch": plan.get("entry_branch"),
            "configured_stop_model": plan.get("configured_stop_model") or payload.get("configured_stop_model"),
            "stop_model": plan.get("stop_model"),
            "tp_model": plan.get("tp_model"),
            "target_source": plan.get("target_source"),
            "sl": plan.get("sl"),
            "tp1": plan.get("tp1"),
            "tp2": plan.get("tp2"),
            "rr2": plan.get("rr2"),
            "effective_rr2": plan.get("effective_rr2"),
            "setup_family": plan.get("setup_family"),
            "setup_class": plan.get("setup_class"),
            "setup_taxonomy_version": plan.get("setup_taxonomy_version"),
            "setup_taxonomy_enum": plan.get("setup_taxonomy_enum"),
            "taxonomy_mapping_source": plan.get("taxonomy_mapping_source"),
            "fvg_execution_class": plan.get("fvg_execution_class"),
            "management_profile": plan.get("management_profile"),
            "target_model": plan.get("target_model"),
            "analytics_key": plan.get("analytics_key"),
            "execution_cost_r": plan.get("execution_cost_r"),
            "slippage_r": plan.get("slippage_r"),
            "commission_r": plan.get("commission_r"),
            "runner_trade": plan.get("runner_trade"),
            "runner_downgraded": plan.get("runner_downgraded"),
            "runner_downgrade_reason": plan.get("runner_downgrade_reason"),
            "obstacle_kind": plan.get("obstacle_kind"),
            "obstacle_price": plan.get("obstacle_price"),
            "obstacle_r": plan.get("obstacle_r"),
            "obstacle_distance_r": plan.get("obstacle_distance_r"),
            "liquidity_rr": plan.get("liquidity_rr"),
            "target_arbitration_required": plan.get("target_arbitration_required"),
            "liquidity_target_preserved": plan.get("liquidity_target_preserved"),
            "liquidity_target_model": plan.get("liquidity_target_model"),
            "liquidity_target_valid_structurally": plan.get("liquidity_target_valid_structurally"),
            "liquidity_target_blocked_by_obstacle": plan.get("liquidity_target_blocked_by_obstacle"),
            "fallback_tp": plan.get("fallback_tp"),
            "fallback_rr": plan.get("fallback_rr"),
            "fallback_source": plan.get("fallback_source"),
            "capped_before_obstacle_tp": plan.get("capped_before_obstacle_tp"),
            "capped_before_obstacle_rr": plan.get("capped_before_obstacle_rr"),
            "capped_before_obstacle_source": plan.get("capped_before_obstacle_source"),
            "original_planned_tp_before_ai": plan.get("original_planned_tp_before_ai"),
            "original_planned_rr_before_ai": plan.get("original_planned_rr_before_ai"),
        },
        "target_candidates": root_target_candidates,
        "watchlist": {
            "armed": watchlist.get("armed"),
            "mid_touched": watchlist.get("mid_touched"),
            "b50_touched": watchlist.get("b50_touched"),
            "bars_waited": watchlist.get("bars_waited"),
            "setup_id": watchlist.get("setup_id"),
            "lineage_version": watchlist.get("lineage_version"),
            "lineage_root_id": watchlist.get("lineage_root_id"),
            "attempt_number_for_sweep": watchlist.get("attempt_number_for_sweep"),
            "narrative_state": watchlist.get("narrative_state"),
            "superseded_by": watchlist.get("superseded_by"),
        },
        "source_story": {
            "context_tier": _get_any(payload, ["source_context_tier"], _as_dict(payload.get("story")).get("source_context_tier")),
            "sweep_side": _get_any(payload, ["source_sweep_side"], _as_dict(payload.get("story")).get("source_sweep_side")),
            "t_sweep": _get_any(payload, ["source_t_sweep"], _as_dict(payload.get("story")).get("source_t_sweep")),
            "t_disp": _get_any(payload, ["source_t_disp"], _as_dict(payload.get("story")).get("source_t_disp")),
            "t_bos": _get_any(payload, ["source_t_bos"], _as_dict(payload.get("story")).get("source_t_bos")),
            "manip_low": _get_any(payload, ["source_manip_low"], _as_dict(payload.get("story")).get("source_manip_low")),
            "manip_high": _get_any(payload, ["source_manip_high"], _as_dict(payload.get("story")).get("source_manip_high")),
            "dr_high": _get_any(payload, ["source_dr_high"], _as_dict(payload.get("story")).get("source_dr_high")),
            "dr_low": _get_any(payload, ["source_dr_low"], _as_dict(payload.get("story")).get("source_dr_low")),
            "liquidity_target": _get_any(payload, ["source_liquidity_target"], _as_dict(payload.get("story")).get("source_liquidity_target")),
            "liquidity_kind": _get_any(payload, ["source_liquidity_kind"], _as_dict(payload.get("story")).get("source_liquidity_kind")),
        },
        "fvg": {
            "t_form": fvg.get("t_form"),
            "lower": fvg.get("lower"),
            "upper": fvg.get("upper"),
            "mid": fvg.get("mid"),
            "score": fvg.get("score"),
            "execution_class": fvg.get("execution_class"),
            "mitigation_state": fvg.get("mitigation_state"),
            "invalidation_reason": fvg.get("invalidation_reason"),
            "entry_invalid": fvg.get("entry_invalid"),
            "structure_invalidated": fvg.get("structure_invalidated"),
            "displacement_candle_score": fvg.get("displacement_candle_score"),
            "middle_candle_body_score": fvg.get("middle_candle_body_score"),
            "volume_impulse_score": fvg.get("volume_impulse_score"),
            "gap_width_atr_score": fvg.get("gap_width_atr_score"),
            "premium_discount_score": fvg.get("premium_discount_score"),
            # This legacy MT5 field is a clearance score: high means no nearby
            # opposing imbalance, low means a close obstruction.
            "opposing_clearance_score": fvg.get("opposing_obstruction_score"),
        },
        "snapshot_metadata": _as_dict(payload.get("snapshot_metadata")),
    }
    compact["candidates"] = []
    for cand in (payload.get("candidates") or [])[:12]:
        if not isinstance(cand, dict):
            continue
        compact["candidates"].append({
            "candidate_index": cand.get("candidate_index"),
            "setup_code": _setup_code_for_item(cand, payload),
            "is_mpc": cand.get("is_mpc"),
            "bucket_prior": _bucket_prior_for_item(cand, payload),
            "entry_model": cand.get("entry_model"),
            "entry_branch": cand.get("entry_branch"),
            "configured_stop_model": cand.get("configured_stop_model") or payload.get("configured_stop_model"),
            "stop_model": cand.get("stop_model"),
            "tp_model": cand.get("tp_model"),
            "target_source": cand.get("target_source"),
            "entry_est": cand.get("entry_est"),
            "sl": cand.get("sl"),
            "tp1": cand.get("tp1"),
            "tp2": cand.get("tp2"),
            "rr2": cand.get("rr2"),
            "effective_rr2": cand.get("effective_rr2"),
            "liquidity_rr": cand.get("liquidity_rr"),
            "setup_family": cand.get("setup_family"),
            "setup_class": cand.get("setup_class"),
            "setup_taxonomy_version": cand.get("setup_taxonomy_version"),
            "setup_taxonomy_enum": cand.get("setup_taxonomy_enum"),
            "taxonomy_mapping_source": cand.get("taxonomy_mapping_source"),
            "fvg_execution_class": cand.get("fvg_execution_class"),
            "management_profile": cand.get("management_profile"),
            "target_model": cand.get("target_model"),
            "analytics_key": cand.get("analytics_key"),
            "execution_cost_r": cand.get("execution_cost_r"),
            "slippage_r": cand.get("slippage_r"),
            "commission_r": cand.get("commission_r"),
            "runner_trade": cand.get("runner_trade"),
            "runner_downgraded": cand.get("runner_downgraded"),
            "runner_downgrade_reason": cand.get("runner_downgrade_reason"),
            "obstacle_kind": cand.get("obstacle_kind"),
            "obstacle_price": cand.get("obstacle_price"),
            "obstacle_r": cand.get("obstacle_r"),
            "obstacle_distance_r": cand.get("obstacle_distance_r"),
            "target_arbitration_required": cand.get("target_arbitration_required"),
            "liquidity_target_preserved": cand.get("liquidity_target_preserved"),
            "liquidity_target_model": cand.get("liquidity_target_model"),
            "liquidity_target_valid_structurally": cand.get("liquidity_target_valid_structurally"),
            "liquidity_target_blocked_by_obstacle": cand.get("liquidity_target_blocked_by_obstacle"),
            "fallback_tp": cand.get("fallback_tp"),
            "fallback_rr": cand.get("fallback_rr"),
            "fallback_source": cand.get("fallback_source"),
            "capped_before_obstacle_tp": cand.get("capped_before_obstacle_tp"),
            "capped_before_obstacle_rr": cand.get("capped_before_obstacle_rr"),
            "capped_before_obstacle_source": cand.get("capped_before_obstacle_source"),
            "original_planned_tp_before_ai": cand.get("original_planned_tp_before_ai"),
            "original_planned_rr_before_ai": cand.get("original_planned_rr_before_ai"),
            "target_candidates": _compact_target_candidates(_target_candidates(payload, cand)),
            "po3_state": cand.get("po3_state"),
            "sweep_side": cand.get("sweep_side"),
            "structure_type": cand.get("structure_type"),
            "po3_scope": cand.get("po3_scope"),
            "source_context_tier": cand.get("source_context_tier"),
            "source_t_sweep": cand.get("source_t_sweep"),
            "attempt_number_for_sweep": cand.get("attempt_number_for_sweep"),
            "fvg_lower": cand.get("fvg_lower"),
            "fvg_upper": cand.get("fvg_upper"),
            "fvg_mid": cand.get("fvg_mid"),
            "fvg_score": cand.get("fvg_score"),
            "origin_score": cand.get("origin_score"),
            "cleanliness_score": cand.get("cleanliness_score"),
            "nesting_score": cand.get("nesting_score"),
            "htf_overlap_score": cand.get("htf_overlap_score"),
            "retest_score": cand.get("retest_score"),
            "displacement_candle_score": cand.get("displacement_candle_score"),
            "middle_candle_body_score": cand.get("middle_candle_body_score"),
            "volume_impulse_score": cand.get("volume_impulse_score"),
            "gap_width_atr_score": cand.get("gap_width_atr_score"),
            "premium_discount_score": cand.get("premium_discount_score"),
            "opposing_clearance_score": cand.get("opposing_obstruction_score"),
        })
    return compact


def _decision_reason_text(reasons: Dict[str, Any] | str) -> str:
    if isinstance(reasons, dict):
        parts: list[str] = []
        ai_reasons = reasons.get("ai_reasons")
        if ai_reasons:
            parts.append(str(ai_reasons))
        rule_notes = reasons.get("rule_notes")
        if rule_notes:
            parts.append(f"rule_notes={rule_notes}")
        if reasons.get("fallback"):
            parts.append(f"fallback={reasons['fallback']}")
        if reasons.get("rejection_codes"):
            parts.append("reject=" + ",".join(str(x) for x in reasons["rejection_codes"]))
        if reasons.get("narrative_state"):
            parts.append(f"state={reasons['narrative_state']}")
        if reasons.get("decision_source"):
            parts.append(f"source={reasons['decision_source']}")
        if reasons.get("error"):
            parts.append(f"error={reasons['error']}")
        if reasons.get("bridge_error"):
            parts.append(f"bridge_error={reasons['bridge_error']}")
        if reasons.get("rule_score") is not None:
            try:
                parts.append(f"rule_score={float(reasons['rule_score']):.2f}")
            except Exception:
                parts.append(f"rule_score={reasons['rule_score']}")
        if reasons.get("agreement") is not None:
            try:
                parts.append(f"agreement={float(reasons['agreement']):.2f}")
            except Exception:
                parts.append(f"agreement={reasons['agreement']}")
        if reasons.get("best_candidate_score") is not None:
            try:
                parts.append(f"best_candidate_score={float(reasons['best_candidate_score']):.2f}")
            except Exception:
                parts.append(f"best_candidate_score={reasons['best_candidate_score']}")
        text = "; ".join(part for part in parts if part)
    else:
        text = str(reasons or "")
    return _ascii_compact(text)


def _degraded_non_trading_decision(
    payload: Dict[str, Any],
    reason: str,
    *,
    missing: list[str] | None = None,
    invalid: list[str] | None = None,
    source: str = "degraded_ai_response",
) -> "Decision":
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    return Decision(
        allow=False,
        raw_allow=False,
        score=0.0,
        chosen_index=int(first.get("candidate_index") or 0),
        confidence=0.0,
        decision_state=DECISION_REJECT,
        decision_quality_tier=DECISION_QUALITY_DEGRADED_NON_TRADING,
        mandatory_fields_complete=False,
        missing_mandatory_fields=list(missing or []),
        invalid_mandatory_fields=list(invalid or []),
        selected_candidate_id=str(first.get("candidate_id") or ""),
        selected_candidate_hash=str(first.get("candidate_hash") or ""),
        request_execution_fingerprint=str(first.get("request_execution_fingerprint") or ""),
        assessed_execution_fingerprint=str(first.get("assessed_execution_fingerprint") or ""),
        candidate_assessments=[],
        reasons=reason,
        decision_source=source,
        rejection_codes=["ai_quality_schema_incomplete", "degraded_ai_response_non_trading"],
        narrative_state="degraded_non_trading",
        invalidation_risks=["ai_decision_contract_invalid"],
        missing_confirmations=list(missing or []),
        suggested_risk_multiplier=0.0,
        model_version=AI_GATE_MODEL_VERSION,
        rule_score=0.0,
        llm_quality_score=0.0,
        blended_legacy_score=0.0,
        legacy_agreement_confidence=0.0,
        llm_self_reported_confidence=0.0,
        calibration_available=False,
    )


def _freeze_request_for_provider(
    payload: Mapping[str, Any],
    provider: AIProvider,
) -> FrozenAIRequest:
    provider_identity = provider.generation_identity(
        "analyst",
        _provider_request_metadata(dict(payload), provider),
    )
    schema_preflight = strict_structured_schema(ModelAIGateOutput)
    if not schema_preflight.valid:
        raise ValueError(
            "structured_schema_preflight_failed:"
            + "|".join(schema_preflight.errors)
        )
    return freeze_ai_request(
        payload,
        provider_identity=provider_identity,
        schema_fingerprint=schema_preflight.schema_fingerprint,
        family_profile_version=FAMILY_PROFILE_VERSION,
        retrieval_policy_version=RETRIEVAL_POLICY_VERSION,
        candidate_cap=12,
    )


def _attach_frozen_identity(
    payload: Dict[str, Any],
    frozen_request: FrozenAIRequest,
) -> None:
    identity = frozen_request.identity
    payload["request_identity_version"] = AI_REQUEST_IDENTITY_VERSION
    payload["identity_schema_version"] = AI_REQUEST_IDENTITY_VERSION
    payload["canonicalization_version"] = AI_IDENTITY_CANONICALIZATION_VERSION
    payload["request_identity_hash"] = identity["request_identity_hash"]
    payload["request_identity"] = identity
    payload["ordered_candidate_identities"] = list(
        identity["ordered_candidate_identities"]
    )
    payload["candidate_count"] = int(identity["candidate_count"])


def _bind_python_owned_analyst_envelope(
    *,
    model_output: Mapping[str, Any],
    candidates: list[Dict[str, Any]],
    enriched_candidates: list[Dict[str, Any]],
    request_id: str,
    request_identity_hash: str,
    ordered_candidate_identities: list[Dict[str, Any]],
    provider_result: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map analytical indexes to the immutable request and attach identity."""

    raw_assessments = model_output.get("candidate_assessments")
    if not isinstance(raw_assessments, list):
        raise ValueError("model_candidate_assessments_missing")
    candidate_by_index = {
        int(candidate.get("candidate_index", position)): candidate
        for position, candidate in enumerate(candidates)
    }
    expected_indexes = list(candidate_by_index)
    assessment_by_index: dict[int, Dict[str, Any]] = {}
    duplicate_indexes: list[int] = []
    unknown_indexes: list[int] = []
    for raw_assessment in raw_assessments:
        if not isinstance(raw_assessment, Mapping):
            raise ValueError("model_candidate_assessment_not_object")
        index = int(raw_assessment.get("candidate_index", -1))
        if index in assessment_by_index:
            duplicate_indexes.append(index)
            continue
        if index not in candidate_by_index:
            unknown_indexes.append(index)
            continue
        assessment_by_index[index] = dict(raw_assessment)
    missing_indexes = [
        index for index in expected_indexes if index not in assessment_by_index
    ]
    mapping_diagnostics = {
        "expected_count": len(expected_indexes),
        "actual_count": len(raw_assessments),
        "expected_order": expected_indexes,
        "actual_order": [
            int(item.get("candidate_index", -1))
            for item in raw_assessments
            if isinstance(item, Mapping)
        ],
        "missing_candidate_indexes": missing_indexes,
        "duplicate_candidate_indexes": sorted(set(duplicate_indexes)),
        "unknown_candidate_indexes": sorted(set(unknown_indexes)),
    }
    if missing_indexes or duplicate_indexes or unknown_indexes or len(raw_assessments) != len(candidates):
        raise ValueError(
            "model_candidate_index_mapping_invalid:"
            + json.dumps(mapping_diagnostics, sort_keys=True, separators=(",", ":"))
        )

    selected_index = int(model_output.get("selected_candidate_index", -1))
    if selected_index not in candidate_by_index:
        raise ValueError(f"model_selected_candidate_index_out_of_range:{selected_index}")
    quality_tier = str(model_output.get("decision_quality_tier") or "")
    response_quality = model_output.get("response_quality")
    if quality_tier != DECISION_QUALITY_FULL_STRUCTURED:
        raise ValueError("model_decision_quality_tier_invalid")
    if response_quality is not None and str(response_quality) != quality_tier:
        raise ValueError("model_response_quality_alias_conflict")

    rule_by_index = {
        int(candidate.get("candidate_index", position)): float(
            enriched_candidates[position]["rule_score"]
        )
        for position, candidate in enumerate(candidates)
    }
    bound_assessments: list[Dict[str, Any]] = []
    for index in expected_indexes:
        candidate = candidate_by_index[index]
        analytical = dict(assessment_by_index[index])
        rule_value = rule_by_index[index]
        quality = float(analytical["llm_quality_score"])
        blended = max(0.0, min(10.0, 0.72 * rule_value + 0.28 * quality))
        agreement = max(0.0, min(1.0, 1.0 - abs(rule_value - quality) / 6.0))
        legacy_confidence = max(
            0.0,
            min(
                1.0,
                0.65 * agreement
                + 0.35 * float(analytical["llm_self_reported_confidence"]),
            ),
        )
        arbitration = _as_dict(analytical.get("target_arbitration"))
        chosen_model = str(arbitration.get("chosen_target_model") or "")
        chosen_tp1 = float(arbitration.get("chosen_tp1") or 0.0)
        chosen_tp2 = float(arbitration.get("chosen_tp2") or 0.0)
        entry = float(candidate.get("entry_est") or candidate.get("entry") or 0.0)
        sl = float(candidate.get("sl") or 0.0)
        tp1 = chosen_tp1 if chosen_tp1 > 0.0 else float(candidate.get("tp1") or 0.0)
        tp2 = chosen_tp2 if chosen_tp2 > 0.0 else float(candidate.get("tp2") or 0.0)
        canonical = {
            **analytical,
            "request_id": request_id,
            "request_identity_hash": request_identity_hash,
            "provider_id": str(provider_result.provider_id),
            "model_id": str(provider_result.actual_model),
            "role_schema_version": ROLE_CONTRACT_VERSION,
            "candidate_index": index,
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "candidate_hash": str(candidate.get("candidate_hash") or ""),
            "request_execution_fingerprint": str(
                candidate.get("request_execution_fingerprint") or ""
            ),
            "setup_taxonomy_version": str(
                candidate.get("setup_taxonomy_version") or ""
            ),
            "setup_taxonomy_enum": str(
                candidate.get("setup_taxonomy_enum") or ""
            ),
            "taxonomy_mapping_source": str(
                candidate.get("taxonomy_mapping_source") or ""
            ),
            "rule_score": round(rule_value, 4),
            "blended_legacy_score": round(blended, 4),
            "legacy_agreement_confidence": round(legacy_confidence, 4),
            "calibrated_win_probability": None,
            "expected_net_r": None,
            "oos_predicted_probability": None,
            "calibration_bucket": "",
            "calibration_sample_size": 0,
            "calibration_lower_bound": None,
            "calibration_upper_bound": None,
            "calibration_model_version": "",
            "calibration_data_window_start": "",
            "calibration_data_window_end": "",
            "calibration_available": False,
            "role_contract_version": ROLE_CONTRACT_VERSION,
            "role": "analyst",
            "selected_target_identity": chosen_model,
            "selected_target_price": tp2,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "model_version": str(provider_result.actual_model),
        }
        canonical["assessed_execution_fingerprint"] = (
            deterministic_assessed_execution_fingerprint(candidate, canonical)
        )
        bound_assessments.append(canonical)

    selected_candidate = candidate_by_index[selected_index]
    final_envelope = AIGateEnvelope.model_validate(
        {
            "request_id": request_id,
            "request_identity_hash": request_identity_hash,
            "provider_id": str(provider_result.provider_id),
            "model_id": str(provider_result.actual_model),
            "role_schema_version": ROLE_CONTRACT_VERSION,
            "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
            "decision_quality_tier": DECISION_QUALITY_FULL_STRUCTURED,
            "response_quality": DECISION_QUALITY_FULL_STRUCTURED,
            "candidate_count": len(candidates),
            "ordered_candidate_identities": ordered_candidate_identities,
            "selected_candidate_id": str(selected_candidate.get("candidate_id") or ""),
            "selected_candidate_hash": str(
                selected_candidate.get("candidate_hash") or ""
            ),
            "candidate_assessments": bound_assessments,
            "reasons": str(model_output.get("reasons") or ""),
        }
    ).model_dump()
    return final_envelope, mapping_diagnostics


def _score_setup_ai(
    payload: Dict[str, Any],
    *,
    provider_override: AIProvider | None = None,
    non_authoritative_shadow: bool = False,
    frozen_request: FrozenAIRequest | None = None,
) -> Decision:
    """Score every candidate through the startup-selected provider and one contract."""
    provider = provider_override or _provider()
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    contract_missing: list[str] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            contract_missing.append(f"candidates[{index}]")
            continue
        for field in (
            "candidate_id",
            "candidate_hash",
            "request_execution_fingerprint",
            "setup_taxonomy_version",
            "setup_taxonomy_enum",
            "taxonomy_mapping_source",
        ):
            if not str(candidate.get(field) or ""):
                contract_missing.append(f"candidates[{index}].{field}")
    if not candidates:
        contract_missing.append("candidates[]")
    if contract_missing:
        return _degraded_non_trading_decision(
            payload,
            "candidate integrity contract missing before selected AI provider",
            missing=contract_missing,
            source="request_contract_reject",
        )

    if frozen_request is None:
        frozen_request = _freeze_request_for_provider(payload, provider)
        payload = frozen_request.thaw_payload()
        _attach_frozen_identity(payload, frozen_request)
    frozen_request.assert_unchanged(payload)
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []

    model_payload = _compact_model_payload(payload)
    model_candidates = model_payload.get("candidates") if isinstance(model_payload.get("candidates"), list) else []
    enriched_candidates: list[Dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        single_payload = dict(payload)
        single_payload["candidates"] = [candidate]
        single_payload["plan"] = candidate
        candidate_rule_score, candidate_rule_notes = _rule_score(single_payload)
        compact_candidate = dict(model_candidates[index]) if index < len(model_candidates) and isinstance(model_candidates[index], dict) else dict(candidate)
        compact_candidate.update(
            {
                "candidate_index": int(candidate.get("candidate_index", index)),
                "candidate_id": str(candidate.get("candidate_id")),
                "candidate_hash": str(candidate.get("candidate_hash")),
                "request_execution_fingerprint": str(candidate.get("request_execution_fingerprint")),
                "assessed_execution_fingerprint": str(candidate.get("assessed_execution_fingerprint")),
                "setup_taxonomy_version": str(candidate.get("setup_taxonomy_version")),
                "setup_taxonomy_enum": str(candidate.get("setup_taxonomy_enum")),
                "taxonomy_mapping_source": str(candidate.get("taxonomy_mapping_source")),
                "rule_score": round(float(candidate_rule_score), 4),
                "rule_notes": str(candidate_rule_notes),
            }
        )
        enriched_candidates.append(compact_candidate)
    model_payload["candidates"] = enriched_candidates
    model_payload["decision_schema_version"] = AI_DECISION_SCHEMA_VERSION
    model_payload["target_arbitration_schema_version"] = AI_TARGET_ARBITRATION_SCHEMA_VERSION
    model_payload["prompt_contract_version"] = AI_PROMPT_CONTRACT_VERSION
    model_payload["workload_mode"] = _payload_workload_mode(payload)
    model_payload["authority_contract"] = {
        "deterministic": "candidate_entry_sl_tp_cost_broker_risk_session_fingerprint",
        "statistical": "empirical_probability_expected_r_time_to_event_shadow_only_until_validated",
        "llm": "anomaly_contradiction_missing_data_veto_and_narrative_only",
        "portfolio": "aggregate_risk_factor_exposure",
        "management": "independently_validated_exit_policy",
        "architecture_contract_version": ARCHITECTURE_CONTRACT_VERSION,
    }

    memory_store = _trade_memory_store()
    if FILE_BUS_LIFECYCLE is not None:
        try:
            memory_summary = memory_store.ingest_completed_ledger(
                FILE_BUS_LIFECYCLE.root / "logs" / "completed_ai_trades.jsonl"
            )
            if any(memory_summary.values()):
                log("[trade_memory_ingest] " + json.dumps(memory_summary, sort_keys=True, separators=(",", ":")))
        except Exception as exc:
            # Historical memory is contextual only. A corrupt store cannot
            # crash the file bus and cannot be converted into a fabricated prior.
            log(f"[trade_memory_ingest] status=unavailable error={type(exc).__name__}")
    request_id = str(payload.get("id") or "")
    lineage_id = str(payload.get("setup_lineage_id") or payload.get("story_id") or request_id)
    retrieval_by_hash: Dict[str, Any] = {}
    analogue_rows: list[Dict[str, Any]] = []
    for candidate in candidates:
        candidate_hash = str(candidate.get("candidate_hash") or "")
        try:
            retrieval = memory_store.retrieve_analogues(
                candidate,
                request_id=request_id,
                lineage_id=lineage_id,
                top_k=AI_CONFIG.local_retrieval_top_k,
            )
            retrieval_by_hash[candidate_hash] = retrieval
            for analogue in retrieval.analogues:
                analogue_rows.append({"for_candidate_hash": candidate_hash, **dict(analogue)})
        except Exception as exc:
            log(
                f"[historical_retrieval] candidate_hash={candidate_hash}"
                f" state=INSUFFICIENT_SAMPLE error={type(exc).__name__}"
            )

    evidence_payload = dict(payload)
    evidence_candidates: list[Dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        row = dict(candidate)
        row["candidate_index"] = int(candidate.get("candidate_index", index))
        row["rule_score"] = float(enriched_candidates[index]["rule_score"])
        retrieval = retrieval_by_hash.get(str(candidate.get("candidate_hash") or ""))
        row["historical_evidence_state"] = retrieval.state if retrieval is not None else "INSUFFICIENT_SAMPLE"
        row["retrieved_analogue_ids"] = list(retrieval.analogue_ids) if retrieval is not None else []
        evidence_candidates.append(row)
    evidence_payload["candidates"] = evidence_candidates
    evidence_result = build_decision_evidence_envelope(
        evidence_payload,
        historical_analogues=analogue_rows,
        provider_identity=provider.identity("analyst"),
    )
    if not evidence_result.valid:
        return _degraded_non_trading_decision(
            payload,
            "canonical deterministic evidence envelope invalid",
            missing=list(evidence_result.missing_fields),
            invalid=[*evidence_result.invalid_fields, *evidence_result.hard_blockers],
            source="decision_evidence_no_trade",
        )
    model_payload = evidence_result.envelope
    request_identity_hash = str(payload.get("request_identity_hash") or "")
    ordered_candidate_identities = list(
        payload.get("ordered_candidate_identities") or []
    )
    snapshot_parts, snapshot_notes = _snapshot_parts(payload)
    symbol = str(payload.get("symbol", "") or "unknown_symbol")
    if snapshot_notes:
        log(f"[ai_gate] snapshot status for {symbol}: {'; '.join(snapshot_notes)}")

    runtime = _as_dict(payload.get("runtime"))
    snapshots_required = _runtime_bool(runtime.get("require_snapshots"), False)
    system_msg = f"""You are the independent Analyst in a disciplined PO3 + FVG trade audit. Assess every candidate independently. Never copy a score, veto, target choice, confidence, or risk multiplier between candidates. Reference candidates only by the supplied candidate_index. Python exclusively owns request IDs, hashes, provider/model identity, candidate IDs/hashes, execution fingerprints, schema versions, and final plan prices; do not return or reconstruct those fields.

For each candidate return candidate_index and verdict equal to decision_state. Fill thesis_supported, material_contradictions, missing_required_evidence, historical_evidence_state, major_risks, evidence_refs, confidence_band, and summary. Evidence refs must point to exact paths in the canonical evidence envelope. Historical evidence state must be SUPPORTIVE, MIXED, ADVERSE, or INSUFFICIENT_SAMPLE. Do not request or reveal hidden chain-of-thought; provide only concise auditable conclusions.

Structured MT5 fields are primary evidence; chart snapshots are supporting evidence. Missing or failed chart captures are not a rejection when runtime.require_snapshots is false. The field opposing_clearance_score is favorable when high and means a nearby obstruction when low.

Use decision_state exactly APPROVE, REJECT, or ABSTAIN. ABSTAIN when evidence is mixed, timing/follow-through is unclear, data is incomplete, a prior is statistically weak, target choice is unstable, or the assessed plan may not survive execution. ABSTAIN is never a reduced-risk approval. raw_allow must agree with decision_state: true only for APPROVE.

Score semantics are strict. rule_score is supplied deterministic evidence and must not be returned. llm_quality_score is your 0..10 technical-quality assessment. llm_self_reported_confidence is your uncertainty report, not a probability. Do not return calibrated probability, expected R, blended scores, or transport identity. Never fabricate probability or expected R.

Fill every veto/risk/expectancy field. follow_through_probability, invalidation_risk, chop_risk, cost_risk, symbol_bucket_risk, session_bucket_risk, post_entry_failure_risk, and final_trade_expectancy_score are explicitly uncalibrated diagnostic estimates. They have no direct positive or negative trade authority and crossing a numeric threshold is not a veto. Your negative authority is limited to an identifiable qualitative contradiction, anomaly, missing-data failure, or integrity failure supported by exact structured evidence fields. When veto.enabled=true, veto.code must be exactly one of: ai_veto_missing_mandatory_evidence, ai_veto_structural_contradiction, ai_veto_sequence_contradiction, ai_veto_target_arbitration_incoherent, ai_veto_execution_plan_mismatch, ai_veto_prior_override_unsupported, ai_veto_data_integrity_failure. veto.evidence_fields must list the exact payload field paths that prove the veto and veto.reason must explain the contradiction concisely. A REJECT requires this evidence-backed veto. Use ABSTAIN, not an invented numeric cutoff, when evidence is merely weak or uncertain. When veto.enabled=false, return empty code, evidence_fields, and reason. You do not own calibrated probability, empirical expected R, exact entry/SL/TP construction, broker feasibility, final risk size, portfolio authority, or final execution. A zero suggested_risk_multiplier means reject; never replace zero with one. Each candidate.bucket_prior contains separate global, asset_class, symbol, family, branch, session, killzone, family_symbol, and family_session priors. Use the persisted shrunk_estimate, shrinkage_weight, uncertainty, clean sample size, and hierarchy path. Never let a tiny narrow prior override a supported broad parent. Priors are contextual evidence only; a negative supported hierarchy requires exceptional current evidence and an explicit bucket_prior_override_justification.

Target arbitration rule: when target_candidates.arbitration_required is true, do not assume the provisional tp2 is final. Fill target_arbitration_schema_version and prompt_contract_version with the exact current constants. Compare liquidity_target, partial_before_obstacle_then_liquidity, capped_before_obstacle, synthetic_rr_fallback, synthetic_rr_capped_to_max_distance, and reject by filling target_comparison for all choices with usable, reason, risk, and expected_role. You may recommend only an identity and exact price already constructed and marked feasible by the deterministic engine; Python and MQL remain the authority that sanitize and apply it. Never invent or modify target prices. Never choose a target candidate whose feasible_for_tp2=false or whose infeasible_reason is non-empty. Do not choose synthetic_rr_fallback just because an obstacle exists. If configured synthetic_rr_fallback is infeasible because it exceeds max distance, use synthetic_rr_capped_to_max_distance only if it is marked feasible and still passes min RR. If liquidity RR is strong and the blocker is minor or moderate, prefer liquidity_target or partial_before_obstacle_then_liquidity. If TP1 before obstacle is possible and liquidity TP2 remains valid, prefer partial_before_obstacle_then_liquidity. If the blocker is major but capped RR is valid, prefer capped_before_obstacle. Choose raw synthetic_rr_fallback only if liquidity, partial, capped, and capped synthetic choices are all worse and raw synthetic is marked feasible. If choosing any synthetic fallback, provide specific why_not_liquidity_target, why_not_partial_before_obstacle, why_not_capped_before_obstacle, and target_decision_reason. Use target_candidates.blocker_features as evidence; do not classify crossed_opposing_imbalance or any obstacle as major/killer by name alone, and never use 7.0 as a default severity. Missing blocker evidence means blocker_class=unknown and blocker_severity=-1 unless you can infer from explicit numeric facts. Fill target_arbitration with arbitration_required, chosen_target_model, chosen_tp1/tp2, chosen_rr1/rr2, rejected_target_models, blocker severity/class/kind, blocker_is_trade_killer, all why_not fields, target_comparison, and target_decision_reason. If no arbitration is required, use arbitration_required=false, chosen_target_model=current_plan, blocker_severity=-1, blocker_class=unknown, and chosen_tp2=plan.tp2.

The deterministic setup taxonomy is evidence, not a model output. UNKNOWN_UNCLASSIFIED is never eligible for assessment or trading.

Return decision_quality_tier={DECISION_QUALITY_FULL_STRUCTURED}, response_quality={DECISION_QUALITY_FULL_STRUCTURED} as a compatibility alias, one complete candidate_assessments item for every input candidate, and selected_candidate_index identifying one item. Assessment order may differ from request order; Python normalizes it by candidate_index. A selected candidate may be REJECT or ABSTAIN; do not silently select a different candidate to rescue an invalid one. Raw llm_quality_score is not compressed or capped. Scores above 8 should be rare but must be returned unchanged. Keep text concise ASCII and return strict JSON only."""
    snapshot_status = ", ".join(snapshot_notes) if snapshot_notes else "none"
    errors: list[str] = []
    # A single provider call owns its same-provider model fallback and bounded
    # schema/transport retry. This loop deliberately has one iteration so the
    # existing strict post-processing remains one linear authority path.
    for _provider_attempt in range(1):
            try:
                service_tier, flex_used, disabled_reason = _effective_service_tier(payload)
                if disabled_reason:
                    log(f"[ai_gate] {disabled_reason}")
                provider_result = provider.generate_structured(
                    role="analyst",
                    system_prompt=system_msg,
                    evidence=model_payload,
                    response_schema=ModelAIGateOutput,
                    request_metadata={
                        "request_id": request_id,
                        "request_identity_hash": request_identity_hash,
                        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
                        "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
                        "symbol": symbol,
                        "image_parts": snapshot_parts,
                        "snapshot_status": snapshot_status,
                        "service_tier": service_tier if provider.provider_mode == PROVIDER_MODE_REMOTE else "auto",
                        "timeout_sec": _provider_request_metadata(payload, provider)["timeout_sec"],
                        "workload_mode": _payload_workload_mode(payload),
                        "non_trading_shadow": bool(non_authoritative_shadow),
                    },
                )
                resp = provider_result.raw_response
                out = provider_result.parsed
                model_name = provider_result.actual_model
                budget = (
                    AI_CONFIG.max_output_tokens
                    if provider_result.provider_mode == PROVIDER_MODE_REMOTE
                    else AI_CONFIG.local_max_output_tokens
                )
                reasoning = _reasoning_config_for_model(model_name) if provider_result.provider_mode == PROVIDER_MODE_REMOTE else None
                log_ai_usage(
                    source="ai_gate",
                    operation="trade_gate.provider_neutral_analyst",
                    model=model_name,
                    response=resp,
                    request_id=request_id,
                    reasoning_effort=reasoning.get("effort", "") if reasoning else "",
                    max_output_tokens=budget,
                    provider_mode=provider_result.provider_mode,
                    provider_id=provider_result.provider_id,
                    endpoint_class=provider_result.endpoint_class,
                    model_fingerprint=provider_result.model_fingerprint,
                    tokens_per_second=provider_result.tokens_per_second,
                    estimated_context_tokens=provider_result.estimated_context_tokens,
                    extra={
                        "symbol": symbol,
                        "snapshot_count": len(snapshot_parts),
                        "service_tier": service_tier,
                        "flex_used": flex_used,
                    },
                )
                _write_ai_cost_report(
                    payload,
                    request_id=request_id,
                    decision_source="ai_provider_full_structured",
                    model=model_name,
                    reasoning_effort=reasoning.get("effort", "") if reasoning else "",
                    service_tier=service_tier,
                    prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
                    cache_status="provider_call",
                    batch_used=False,
                    flex_used=flex_used,
                    response=resp,
                    openai_called=provider_result.provider_mode == PROVIDER_MODE_REMOTE,
                    skip_reason="",
                )
                frozen_request.assert_unchanged(payload)
                raw_model_output = out.model_dump()
                try:
                    raw_envelope, mapping_diagnostics = (
                        _bind_python_owned_analyst_envelope(
                            model_output=raw_model_output,
                            candidates=candidates,
                            enriched_candidates=enriched_candidates,
                            request_id=request_id,
                            request_identity_hash=request_identity_hash,
                            ordered_candidate_identities=ordered_candidate_identities,
                            provider_result=provider_result,
                        )
                    )
                except Exception as mapping_error:
                    log(
                        "[identity_validation] valid=false"
                        f" request_id={request_id}"
                        f" request_identity_hash={request_identity_hash[:16]}"
                        " reason=model_candidate_mapping_invalid"
                        f" error={_ascii_compact(str(mapping_error))}"
                    )
                    return _degraded_non_trading_decision(
                        payload,
                        "provider analytical response could not be bound to frozen candidates",
                        invalid=[str(mapping_error)],
                        source="structured_response_invalid",
                    )
                identity_echo = validate_request_identity_echo(
                    raw_envelope,
                    {
                        "request_id": request_id,
                        "request_identity_hash": request_identity_hash,
                        "provider_id": str(provider_result.provider_id),
                        "model_id": str(provider_result.actual_model),
                        "candidate_count": len(candidates),
                        "ordered_candidate_identities": ordered_candidate_identities,
                    },
                )
                if not identity_echo.valid:
                    log(
                        "[identity_validation] valid=false"
                        f" request_id={request_id}"
                        f" request_identity_hash={request_identity_hash[:16]}"
                        f" provider={provider.provider_id}"
                        f" model={provider_result.actual_model}"
                        f" missing={','.join(identity_echo.missing_fields)}"
                        f" mismatched={','.join(identity_echo.invalid_fields)}"
                        f" expected_count={len(candidates)}"
                        f" actual_count={mapping_diagnostics.get('actual_count', -1)}"
                        f" expected_order={mapping_diagnostics.get('expected_order', [])}"
                        f" actual_order={mapping_diagnostics.get('actual_order', [])}"
                        f" missing_candidate_indexes={mapping_diagnostics.get('missing_candidate_indexes', [])}"
                        f" duplicate_candidate_indexes={mapping_diagnostics.get('duplicate_candidate_indexes', [])}"
                        f" unknown_candidate_indexes={mapping_diagnostics.get('unknown_candidate_indexes', [])}"
                        f" request_hash_expected={request_identity_hash[:16]}"
                        f" request_hash_actual={str(raw_envelope.get('request_identity_hash') or '')[:16]}"
                        f" identity_schema_version={AI_REQUEST_IDENTITY_VERSION}"
                        f" canonicalization_version={AI_IDENTITY_CANONICALIZATION_VERSION}"
                    )
                    return _degraded_non_trading_decision(
                        payload,
                        "provider response request identity mismatch",
                        missing=list(identity_echo.missing_fields),
                        invalid=list(identity_echo.invalid_fields),
                        source="request_identity_mismatch",
                    )
                log(
                    "[identity_validation] valid=true"
                    f" request_id={request_id}"
                    f" request_identity_hash={request_identity_hash[:16]}"
                    f" expected_count={len(candidates)}"
                    f" actual_count={mapping_diagnostics.get('actual_count', -1)}"
                    f" expected_order={mapping_diagnostics.get('expected_order', [])}"
                    f" model_assessment_order={mapping_diagnostics.get('actual_order', [])}"
                    f" normalized_order={mapping_diagnostics.get('expected_order', [])}"
                    f" identity_schema_version={AI_REQUEST_IDENTITY_VERSION}"
                    f" canonicalization_version={AI_IDENTITY_CANONICALIZATION_VERSION}"
                )
                validation = validate_decision_envelope(raw_envelope, candidates)
                if not validation.valid:
                    log(
                        "[ai_schema_validation] valid=false"
                        f" decision_schema_version={raw_envelope.get('decision_schema_version', '')}"
                        f" missing_fields={','.join(validation.missing_fields)}"
                        f" invalid_fields={','.join(validation.invalid_fields)}"
                    )
                    return _degraded_non_trading_decision(
                        payload,
                        "strict AI candidate schema validation failed",
                        missing=list(validation.missing_fields),
                        invalid=list(validation.invalid_fields),
                    )

                candidate_by_hash = {
                    str(candidate.get("candidate_hash")): candidate
                    for candidate in candidates
                }
                rule_by_hash = {
                    str(candidate.get("candidate_hash")): float(enriched_candidates[index]["rule_score"])
                    for index, candidate in enumerate(candidates)
                }
                assessments: list[Dict[str, Any]] = []
                for assessment in raw_envelope["candidate_assessments"]:
                    canonical = dict(assessment)
                    assessment_hash = str(canonical["candidate_hash"])
                    binding_mismatches = [
                        field
                        for field, expected in (
                            ("request_id", request_id),
                            ("request_identity_hash", request_identity_hash),
                            ("provider_id", str(provider_result.provider_id)),
                            ("model_id", str(provider_result.actual_model)),
                            ("role_schema_version", ROLE_CONTRACT_VERSION),
                        )
                        if str(canonical.get(field) or "") != str(expected)
                    ]
                    if binding_mismatches:
                        return _degraded_non_trading_decision(
                            payload,
                            "candidate assessment request identity mismatch",
                            invalid=[
                                f"candidate[{assessment_hash}].{field}"
                                for field in binding_mismatches
                            ],
                            source="request_identity_mismatch",
                        )
                    evidence_refs = list(canonical.get("evidence_refs") or [])
                    if any(not evidence_path_exists(model_payload, str(ref)) for ref in evidence_refs):
                        return _degraded_non_trading_decision(
                            payload,
                            "Analyst cited an invalid canonical evidence path",
                            invalid=[f"candidate[{assessment_hash}].evidence_refs"],
                            source="decision_evidence_reference_reject",
                        )
                    analyst_veto = _as_dict(canonical.get("veto"))
                    if bool(analyst_veto.get("enabled")) and any(
                        not evidence_path_exists(model_payload, str(ref))
                        for ref in (analyst_veto.get("evidence_fields") or [])
                    ):
                        return _degraded_non_trading_decision(
                            payload,
                            "Analyst veto cited an invalid canonical evidence path",
                            invalid=[f"candidate[{assessment_hash}].veto.evidence_fields"],
                            source="decision_evidence_reference_reject",
                        )
                    statistical_authority_violations: list[str] = []
                    for field in (
                        "calibrated_win_probability",
                        "expected_net_r",
                        "oos_predicted_probability",
                        "calibration_lower_bound",
                        "calibration_upper_bound",
                    ):
                        if canonical.get(field) is not None:
                            statistical_authority_violations.append(
                                f"candidate[{assessment_hash}].{field}_llm_authority_forbidden"
                            )
                    if bool(canonical.get("calibration_available")):
                        statistical_authority_violations.append(
                            f"candidate[{assessment_hash}].calibration_available_llm_authority_forbidden"
                        )
                    for field in (
                        "calibration_bucket",
                        "calibration_model_version",
                        "calibration_data_window_start",
                        "calibration_data_window_end",
                    ):
                        if str(canonical.get(field) or ""):
                            statistical_authority_violations.append(
                                f"candidate[{assessment_hash}].{field}_llm_authority_forbidden"
                            )
                    if int(canonical.get("calibration_sample_size") or 0) != 0:
                        statistical_authority_violations.append(
                            f"candidate[{assessment_hash}].calibration_sample_size_llm_authority_forbidden"
                        )
                    if statistical_authority_violations:
                        return _degraded_non_trading_decision(
                            payload,
                            "LLM attempted to populate statistical-authority fields",
                            invalid=statistical_authority_violations,
                            source="layered_authority_reject",
                        )
                    candidate = candidate_by_hash[assessment_hash]
                    rule_value = rule_by_hash[assessment_hash]
                    tick = max(
                        abs(float(candidate.get("symbol_tick_size") or 0.0)),
                        10.0 ** (-max(0, int(candidate.get("symbol_digits") or 5))),
                    )
                    if abs(float(canonical["entry"]) - float(candidate.get("entry_est") or 0.0)) > 2.0 * tick:
                        return _degraded_non_trading_decision(
                            payload,
                            "AI changed candidate entry outside the immutable plan",
                            invalid=[f"candidate[{assessment_hash}].entry"],
                        )
                    if abs(float(canonical["sl"]) - float(candidate.get("sl") or 0.0)) > 2.0 * tick:
                        return _degraded_non_trading_decision(
                            payload,
                            "AI changed candidate stop outside the immutable plan",
                            invalid=[f"candidate[{assessment_hash}].sl"],
                        )
                    canonical["entry"] = float(candidate.get("entry_est") or 0.0)
                    canonical["sl"] = float(candidate.get("sl") or 0.0)
                    arbitration = _as_dict(canonical.get("target_arbitration"))
                    chosen_model = str(arbitration.get("chosen_target_model") or "")
                    chosen_tp1 = float(arbitration.get("chosen_tp1") or 0.0)
                    chosen_tp2 = float(arbitration.get("chosen_tp2") or 0.0)
                    selected_identity = str(canonical.get("selected_target_identity") or "")
                    state = str(canonical.get("decision_state") or "").upper()
                    if state == DECISION_APPROVE:
                        if _norm_text(selected_identity) != _norm_text(chosen_model):
                            return _degraded_non_trading_decision(
                                payload,
                                "selected target identity does not match target arbitration",
                                invalid=[f"candidate[{assessment_hash}].selected_target_identity"],
                            )
                        if chosen_tp2 <= 0.0 or abs(float(canonical["selected_target_price"]) - chosen_tp2) > 2.0 * tick:
                            return _degraded_non_trading_decision(
                                payload,
                                "selected target price does not match target arbitration",
                                invalid=[f"candidate[{assessment_hash}].selected_target_price"],
                            )
                    canonical["selected_target_identity"] = chosen_model or selected_identity
                    canonical["selected_target_price"] = chosen_tp2 if chosen_tp2 > 0.0 else float(canonical["selected_target_price"])
                    canonical["tp1"] = chosen_tp1 if chosen_tp1 > 0.0 else float(candidate.get("tp1") or canonical["tp1"])
                    canonical["tp2"] = chosen_tp2 if chosen_tp2 > 0.0 else float(candidate.get("tp2") or canonical["tp2"])
                    quality = float(canonical["llm_quality_score"])
                    blended = max(0.0, min(10.0, 0.72 * rule_value + 0.28 * quality))
                    agreement = max(0.0, min(1.0, 1.0 - abs(rule_value - quality) / 6.0))
                    legacy_confidence = max(
                        0.0,
                        min(1.0, 0.65 * agreement + 0.35 * float(canonical["llm_self_reported_confidence"])),
                    )
                    canonical["rule_score"] = round(rule_value, 4)
                    canonical["blended_legacy_score"] = round(blended, 4)
                    canonical["legacy_agreement_confidence"] = round(legacy_confidence, 4)
                    canonical["calibration_available"] = False
                    canonical["calibrated_win_probability"] = None
                    canonical["expected_net_r"] = None
                    canonical["oos_predicted_probability"] = None
                    canonical["calibration_bucket"] = ""
                    canonical["calibration_sample_size"] = 0
                    canonical["calibration_lower_bound"] = None
                    canonical["calibration_upper_bound"] = None
                    canonical["calibration_model_version"] = ""
                    canonical["calibration_data_window_start"] = ""
                    canonical["calibration_data_window_end"] = ""
                    canonical["model_id"] = str(provider_result.actual_model or model_name)
                    canonical["request_execution_fingerprint"] = str(candidate.get("request_execution_fingerprint") or "")
                    canonical["assessed_execution_fingerprint"] = deterministic_assessed_execution_fingerprint(candidate, canonical)
                    assessments.append(canonical)

                selected_hash = str(raw_envelope["selected_candidate_hash"])
                selected = next(a for a in assessments if str(a["candidate_hash"]) == selected_hash)
                selected_index = int(selected["candidate_index"])
                state = str(selected["decision_state"]).upper()
                multiplier_resolution = resolve_multiplier(
                    selected.get("suggested_risk_multiplier"),
                    present="suggested_risk_multiplier" in selected,
                    optional=False,
                    source="ai_decision",
                )
                risk_multiplier = float(multiplier_resolution.resolved_value or 0.0)
                veto = _as_dict(selected.get("veto"))
                allow = bool(
                    response_can_trade(RESPONSE_FULL_STRUCTURED, state, risk_multiplier)
                    and bool(selected["raw_allow"])
                    and not bool(veto.get("enabled"))
                    and multiplier_resolution.valid
                    and not multiplier_resolution.blocked
                )
                if state == DECISION_ABSTAIN:
                    log(
                        f"[ai_abstain] candidate_id={selected['candidate_id']}"
                        f" candidate_hash={selected_hash} reason={selected.get('reasons', '')}"
                    )
                target_kwargs = _target_kwargs_from_dict(_as_dict(selected.get("target_arbitration")))
                decision = Decision(
                    allow=allow,
                    raw_allow=bool(selected["raw_allow"]),
                    score=float(selected["llm_quality_score"]),
                    chosen_index=selected_index,
                    confidence=float(selected["llm_self_reported_confidence"]),
                    decision_state=state,
                    decision_quality_tier=DECISION_QUALITY_FULL_STRUCTURED,
                    mandatory_fields_complete=True,
                    missing_mandatory_fields=[],
                    invalid_mandatory_fields=[],
                    selected_candidate_id=str(selected["candidate_id"]),
                    selected_candidate_hash=selected_hash,
                    request_execution_fingerprint=str(selected["request_execution_fingerprint"]),
                    assessed_execution_fingerprint=str(selected["assessed_execution_fingerprint"]),
                    selected_target_identity=str(selected["selected_target_identity"]),
                    selected_target_price=float(selected["selected_target_price"]),
                    assessed_entry=float(selected["entry"]),
                    assessed_sl=float(selected["sl"]),
                    assessed_tp1=float(selected["tp1"]),
                    assessed_tp2=float(selected["tp2"]),
                    candidate_assessments=assessments,
                    rule_score=float(selected["rule_score"]),
                    llm_quality_score=float(selected["llm_quality_score"]),
                    blended_legacy_score=float(selected["blended_legacy_score"]),
                    legacy_agreement_confidence=float(selected["legacy_agreement_confidence"]),
                    llm_self_reported_confidence=float(selected["llm_self_reported_confidence"]),
                    calibrated_win_probability=None,
                    expected_net_r=None,
                    oos_predicted_probability=None,
                    calibration_available=False,
                    reasons=f"provider={provider_result.provider_id}; model={model_name}; tokens={budget}; snapshots={len(snapshot_parts)}; {selected.get('reasons', '')}",
                    decision_source="ai_provider_full_structured",
                    rejection_codes=list(selected.get("rejection_codes") or []),
                    narrative_state=str(selected.get("narrative_state") or "audited"),
                    invalidation_risks=list(selected.get("invalidation_risks") or []),
                    missing_confirmations=list(selected.get("missing_confirmations") or []),
                    suggested_risk_multiplier=risk_multiplier,
                    model_version=str(provider_result.actual_model or model_name),
                    structure_quality_score=float(selected["structure_quality_score"]),
                    entry_timing_score=float(selected["entry_timing_score"]),
                    follow_through_probability=float(selected["follow_through_probability"]),
                    invalidation_risk=float(selected["invalidation_risk"]),
                    chop_risk=float(selected["chop_risk"]),
                    cost_risk=float(selected["cost_risk"]),
                    symbol_bucket_risk=float(selected["symbol_bucket_risk"]),
                    session_bucket_risk=float(selected["session_bucket_risk"]),
                    post_entry_failure_risk=float(selected["post_entry_failure_risk"]),
                    final_trade_expectancy_score=float(selected["final_trade_expectancy_score"]),
                    veto_enabled=bool(veto.get("enabled")),
                    veto_code=str(veto.get("code") or ""),
                    veto_evidence_fields=list(veto.get("evidence_fields") or []),
                    veto_reason=str(veto.get("reason") or ""),
                    bucket_prior_override_justification=str(selected.get("bucket_prior_override_justification") or ""),
                    provider_mode=provider_result.provider_mode,
                    provider_id=provider_result.provider_id,
                    endpoint_class=provider_result.endpoint_class,
                    endpoint_identity_hash=str(
                        provider.identity("analyst").get("endpoint_identity_hash") or ""
                    ),
                    configured_models_hash=str(
                        provider.identity("analyst").get("configured_models_hash") or ""
                    ),
                    actual_model_id=provider_result.actual_model,
                    fallback_model=provider_result.fallback_model,
                    model_fingerprint=provider_result.model_fingerprint or "unavailable",
                    generation_settings_hash=provider_result.generation_settings_hash,
                    input_fingerprint=str(model_payload.get("input_fingerprint") or ""),
                    retrieved_analogue_ids=list(
                        retrieval_by_hash.get(selected_hash).analogue_ids
                        if retrieval_by_hash.get(selected_hash) is not None
                        else []
                    ),
                    historical_evidence_state=(
                        retrieval_by_hash.get(selected_hash).state
                        if retrieval_by_hash.get(selected_hash) is not None
                        else "INSUFFICIENT_SAMPLE"
                    ),
                    analyst_response_fingerprint=canonical_hash(selected),
                    provider_health_state=provider_result.health_state,
                    role_latencies={"analyst": provider_result.latency_sec},
                    provider_retry_counts={
                        "analyst_transport": provider_result.transport_retry_count,
                        "analyst_schema": provider_result.schema_retry_count,
                    },
                    provider_usage={
                        "analyst": {
                            "prompt_tokens": provider_result.prompt_tokens,
                            "completion_tokens": provider_result.completion_tokens,
                            "total_tokens": provider_result.total_tokens,
                            "tokens_per_second": provider_result.tokens_per_second,
                        }
                    },
                    estimated_context_tokens=provider_result.estimated_context_tokens,
                    unsupported_generation_parameters=list(
                        provider_result.unsupported_generation_parameters
                    ),
                    analyst_output=dict(selected),
                    **target_kwargs,
                )
                consensus = run_qualitative_consensus(
                    provider=provider,
                    evidence=model_payload,
                    analyst_assessment=selected,
                    request_metadata={
                        **_provider_request_metadata(payload, provider),
                        "request_id": request_id,
                        "symbol": symbol,
                        "non_trading_shadow": bool(
                            non_authoritative_shadow
                            or _as_dict(payload.get("shadow_repeat")).get("trading_authority") is False
                        ),
                    },
                    near_deterministic_boundary=False,
                )
                decision.critic_output = dict(consensus.critic)
                decision.adjudicator_output = dict(consensus.adjudicator)
                decision.critic_response_fingerprint = canonical_hash(consensus.critic)
                decision.adjudicator_response_fingerprint = (
                    canonical_hash(consensus.adjudicator) if consensus.adjudicator else ""
                )
                decision.final_resolver_reason = consensus.reason
                decision.role_latencies = dict(decision.role_latencies or {})
                decision.role_latencies["critic"] = consensus.critic_result.latency_sec
                if consensus.adjudicator_result is not None:
                    decision.role_latencies["adjudicator"] = consensus.adjudicator_result.latency_sec
                decision.provider_retry_counts = dict(decision.provider_retry_counts or {})
                decision.provider_retry_counts.update(
                    {
                        "critic_transport": consensus.critic_result.transport_retry_count,
                        "critic_schema": consensus.critic_result.schema_retry_count,
                        "adjudicator_transport": (
                            consensus.adjudicator_result.transport_retry_count
                            if consensus.adjudicator_result is not None
                            else 0
                        ),
                        "adjudicator_schema": (
                            consensus.adjudicator_result.schema_retry_count
                            if consensus.adjudicator_result is not None
                            else 0
                        ),
                    }
                )
                decision.provider_usage = dict(decision.provider_usage or {})
                decision.provider_usage["critic"] = {
                    "prompt_tokens": consensus.critic_result.prompt_tokens,
                    "completion_tokens": consensus.critic_result.completion_tokens,
                    "total_tokens": consensus.critic_result.total_tokens,
                    "tokens_per_second": consensus.critic_result.tokens_per_second,
                }
                if consensus.adjudicator_result is not None:
                    decision.provider_usage["adjudicator"] = {
                        "prompt_tokens": consensus.adjudicator_result.prompt_tokens,
                        "completion_tokens": consensus.adjudicator_result.completion_tokens,
                        "total_tokens": consensus.adjudicator_result.total_tokens,
                        "tokens_per_second": consensus.adjudicator_result.tokens_per_second,
                    }
                for role_name, role_result in (
                    ("critic", consensus.critic_result),
                    ("adjudicator", consensus.adjudicator_result),
                ):
                    if role_result is None:
                        continue
                    log_ai_usage(
                        source="ai_gate",
                        operation=f"trade_gate.provider_neutral_{role_name}",
                        model=role_result.actual_model,
                        response=role_result.raw_response,
                        request_id=request_id,
                        reasoning_effort=(
                            AI_CONFIG.reasoning_effort
                            if role_result.provider_mode == PROVIDER_MODE_REMOTE
                            else ""
                        ),
                        max_output_tokens=(
                            AI_CONFIG.max_output_tokens
                            if role_result.provider_mode == PROVIDER_MODE_REMOTE
                            else AI_CONFIG.local_max_output_tokens
                        ),
                        provider_mode=role_result.provider_mode,
                        provider_id=role_result.provider_id,
                        endpoint_class=role_result.endpoint_class,
                        model_fingerprint=role_result.model_fingerprint,
                        tokens_per_second=role_result.tokens_per_second,
                        estimated_context_tokens=role_result.estimated_context_tokens,
                        extra={"symbol": symbol, "role": role_name},
                    )
                unsupported = set(decision.unsupported_generation_parameters or [])
                unsupported.update(consensus.critic_result.unsupported_generation_parameters)
                if consensus.adjudicator_result is not None:
                    unsupported.update(consensus.adjudicator_result.unsupported_generation_parameters)
                decision.unsupported_generation_parameters = sorted(unsupported)
                if not consensus.python_allow:
                    decision.allow = False
                    decision.python_final_allow = False
                    decision.decision_state = consensus.decision_state
                    decision.suggested_risk_multiplier = 0.0
                    if decision.rejection_codes is None:
                        decision.rejection_codes = []
                    resolver_code = (
                        "ai_qualitative_consensus_reject"
                        if consensus.decision_state == DECISION_REJECT
                        else "ai_qualitative_consensus_abstain"
                    )
                    if resolver_code not in decision.rejection_codes:
                        decision.rejection_codes.append(resolver_code)
                decision.decision_source = "ai_consensus_full_structured"
                try:
                    if not non_authoritative_shadow:
                        memory_store.record_pending_decision(
                            request_id=request_id,
                            lineage_id=lineage_id,
                            candidate_hash=selected_hash,
                            immutable_pre_entry_evidence={
                                "candidate": candidate_by_hash[selected_hash],
                                "evidence_input_fingerprint": model_payload.get("input_fingerprint"),
                                "family_profile_version": FAMILY_PROFILE_VERSION,
                                "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
                            },
                            provider_observability={
                                "provider_mode": decision.provider_mode,
                                "provider_id": decision.provider_id,
                                "endpoint_class": decision.endpoint_class,
                                "actual_model_id": decision.actual_model_id,
                                "fallback_model": decision.fallback_model,
                                "model_fingerprint": decision.model_fingerprint,
                                "generation_settings_hash": decision.generation_settings_hash,
                                "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
                                "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
                            },
                            analyst_output=selected,
                            critic_output=consensus.critic,
                            adjudicator_output=consensus.adjudicator,
                            python_final_decision={
                                "decision_state": decision.decision_state,
                                "python_final_allow": bool(decision.allow),
                                "final_resolver_reason": consensus.reason,
                            },
                        )
                except Exception as exc:
                    log(
                        f"[trade_memory] pending_snapshot_failed request_id={request_id}"
                        f" candidate_hash={selected_hash} error={type(exc).__name__}"
                    )
                log(
                    "[decision_scores]"
                    f" rule_score={decision.rule_score:.4f}"
                    f" llm_quality_score={decision.llm_quality_score:.4f}"
                    f" blended_legacy_score={decision.blended_legacy_score:.4f}"
                    " calibrated_win_probability=unavailable expected_net_r=unavailable"
                    f" llm_self_reported_confidence={decision.llm_self_reported_confidence:.4f}"
                )
                log(
                    "[ai_schema_validation] valid=true"
                    f" decision_schema_version={AI_DECISION_SCHEMA_VERSION}"
                    f" candidate_count={len(assessments)} selected_candidate_hash={selected_hash}"
                )
                return decision
            except Exception as e:
                identity = provider.identity("analyst")
                msg = (
                    f"provider={identity.get('provider_id')} model={identity.get('model_id')}"
                    f" error={type(e).__name__}:{e}"
                )
                errors.append(msg)
                log(f"[ai_gate] selected_provider_call_failed {msg} cross_provider_fallback=false")
                if isinstance(e, ProviderCallError):
                    raise

    raise RuntimeError("selected_ai_provider_failed_closed: " + " | ".join(errors))


def _score_setup_openai(payload: Dict[str, Any]) -> Decision:
    """Compatibility hook for existing callers/tests; transport is provider-neutral."""

    return _score_setup_ai(payload)

# ---------- Model placeholders ----------

@dataclass
class Decision:
    allow: bool
    # Migration aliases only. score is mirrored from llm_quality_score and
    # confidence from llm_self_reported_confidence for old diagnostics.
    score: float
    raw_allow: bool = False
    model_raw_allow: bool | None = None
    python_final_allow: bool | None = None
    mql_final_allow: bool | None = None
    chosen_index: int = 0
    confidence: float = 0.0
    decision_state: str = DECISION_REJECT
    decision_quality_tier: str = DECISION_QUALITY_DEGRADED_NON_TRADING
    decision_schema_version: str = AI_DECISION_SCHEMA_VERSION
    mandatory_fields_complete: bool = False
    missing_mandatory_fields: list[str] | None = None
    invalid_mandatory_fields: list[str] | None = None
    selected_candidate_id: str = ""
    selected_candidate_hash: str = ""
    request_execution_fingerprint: str = ""
    assessed_execution_fingerprint: str = ""
    selected_target_identity: str = ""
    selected_target_price: float = 0.0
    assessed_entry: float = 0.0
    assessed_sl: float = 0.0
    assessed_tp1: float = 0.0
    assessed_tp2: float = 0.0
    candidate_assessments: list[Dict[str, Any]] | None = None
    rule_score: float = 0.0
    llm_quality_score: float = 0.0
    blended_legacy_score: float = 0.0
    legacy_agreement_confidence: float = 0.0
    llm_self_reported_confidence: float = 0.0
    calibrated_win_probability: float | None = None
    expected_net_r: float | None = None
    oos_predicted_probability: float | None = None
    calibration_bucket: str = ""
    calibration_sample_size: int = 0
    calibration_lower_bound: float | None = None
    calibration_upper_bound: float | None = None
    calibration_model_version: str = ""
    calibration_data_window_start: str = ""
    calibration_data_window_end: str = ""
    calibration_available: bool = False
    reasons: Dict[str, Any] | str = ""
    decision_source: str = ""
    rejection_codes: list[str] | None = None
    narrative_state: str = ""
    invalidation_risks: list[str] | None = None
    missing_confirmations: list[str] | None = None
    suggested_risk_multiplier: float | None = None
    model_version: str = AI_GATE_MODEL_VERSION
    decision_id: str = ""
    llm_quality_score_threshold: float = 0.0
    llm_quality_threshold_source: str = ""
    global_llm_quality_as_hard_floor: bool = False
    llm_quality_threshold_passed: bool = False
    llm_quality_reject_reason: str = ""
    structure_quality_score: float = 0.0
    entry_timing_score: float = 0.0
    follow_through_probability: float = 0.0
    invalidation_risk: float = 1.0
    chop_risk: float = 1.0
    cost_risk: float = 1.0
    symbol_bucket_risk: float = 1.0
    session_bucket_risk: float = 1.0
    post_entry_failure_risk: float = 1.0
    final_trade_expectancy_score: float = 0.0
    veto_enabled: bool = False
    veto_code: str = ""
    veto_evidence_fields: list[str] | None = None
    veto_reason: str = ""
    llm_numeric_diagnostics_authority: str = "uncalibrated_diagnostic_only_no_direct_trade_authority"
    bucket_prior_override_justification: str = ""
    target_arbitration: Dict[str, Any] | None = None
    chosen_target_model: str = ""
    chosen_tp1: float = 0.0
    chosen_tp2: float = 0.0
    chosen_rr1: float = 0.0
    chosen_rr2: float = 0.0
    rejected_target_models: list[str] | None = None
    target_blocker_kind: str = ""
    target_blocker_severity: float = -1.0
    target_blocker_class: str = ""
    target_blocker_is_trade_killer: bool = False
    target_decision_reason: str = ""
    target_blocker_severity_present: bool = False
    target_blocker_class_present: bool = False
    target_blocker_is_trade_killer_present: bool = False
    target_decision_reason_present: bool = False
    why_not_liquidity_target: str = ""
    why_not_partial_before_obstacle: str = ""
    why_not_capped_before_obstacle: str = ""
    why_not_synthetic_fallback: str = ""
    target_arbitration_schema_version: str = AI_TARGET_ARBITRATION_SCHEMA_VERSION
    prompt_contract_version: str = AI_PROMPT_CONTRACT_VERSION
    target_comparison_json: str = "{}"
    provider_mode: str = "UNAVAILABLE"
    provider_id: str = "unavailable"
    endpoint_class: str = "invalid"
    endpoint_identity_hash: str = ""
    configured_models_hash: str = ""
    actual_model_id: str = ""
    fallback_model: str = ""
    model_fingerprint: str = ""
    provider_contract_version: str = PROVIDER_CONTRACT_VERSION
    evidence_envelope_version: str = EVIDENCE_ENVELOPE_VERSION
    family_profile_version: str = FAMILY_PROFILE_VERSION
    memory_schema_version: str = TRADE_MEMORY_SCHEMA_VERSION
    retrieval_policy_version: str = RETRIEVAL_POLICY_VERSION
    role_contract_version: str = ROLE_CONTRACT_VERSION
    consensus_resolver_version: str = CONSENSUS_RESOLVER_VERSION
    generation_settings_hash: str = ""
    input_fingerprint: str = ""
    retrieved_analogue_ids: list[str] | None = None
    historical_evidence_state: str = "INSUFFICIENT_SAMPLE"
    analyst_response_fingerprint: str = ""
    critic_response_fingerprint: str = ""
    adjudicator_response_fingerprint: str = ""
    final_resolver_reason: str = ""
    provider_health_state: str = "unavailable"
    role_latencies: Dict[str, float] | None = None
    provider_retry_counts: Dict[str, int] | None = None
    provider_usage: Dict[str, Any] | None = None
    estimated_context_tokens: int | None = None
    unsupported_generation_parameters: list[str] | None = None
    analyst_output: Dict[str, Any] | None = None
    critic_output: Dict[str, Any] | None = None
    adjudicator_output: Dict[str, Any] | None = None

    @property
    def response_quality(self) -> str:
        """Read-only compatibility alias; never an independent authority."""

        return self.decision_quality_tier

def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _synchronize_decision_authority_fields(decision: Decision) -> Decision:
    """Keep the three decision stages explicit without inventing MQL authority."""

    decision.model_raw_allow = bool(
        decision.raw_allow if decision.model_raw_allow is None else decision.model_raw_allow
    )
    decision.raw_allow = bool(decision.model_raw_allow)
    decision.python_final_allow = bool(
        decision.allow if decision.python_final_allow is None else decision.python_final_allow
    )
    decision.allow = bool(decision.python_final_allow)
    # Python cannot claim the downstream MQL result. It remains unavailable
    # until the EA applies candidate, freshness, risk, session, and broker gates.
    decision.mql_final_allow = None
    return decision

def _get_any(d: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None and d[k] != "":
            return d[k]
    return default

def _boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default

def _target_candidates(payload: Dict[str, Any], item: Dict[str, Any] | None = None) -> Dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    plan = _as_dict(payload.get("plan"))
    for obj in (item, plan, _as_dict(payload.get("target_candidates"))):
        tc = obj.get("target_candidates") if isinstance(obj, dict) else None
        if isinstance(tc, dict):
            return tc
    root_tc = payload.get("target_candidates")
    return root_tc if isinstance(root_tc, dict) else {}

def _target_candidate_option(candidates: Dict[str, Any], *names: str) -> Dict[str, Any]:
    for name in names:
        value = candidates.get(name)
        if isinstance(value, dict):
            return value
    return {}

def _target_arbitration_required(payload: Dict[str, Any], item: Dict[str, Any] | None = None) -> bool:
    item = item if isinstance(item, dict) else {}
    plan = _as_dict(payload.get("plan"))
    candidates = _target_candidates(payload, item)
    if (
        _boolish(item.get("target_arbitration_required"), False)
        or _boolish(plan.get("target_arbitration_required"), False)
        or _boolish(candidates.get("arbitration_required"), False)
    ):
        return True
    obstacle = _norm_text(
        _get_any(candidates, ["obstacle_kind"], _get_any(item, ["obstacle_kind"], _get_any(plan, ["obstacle_kind"], "")))
    )
    target_source = _norm_text(
        _get_any(item, ["target_source", "tp_model", "target_model"], _get_any(plan, ["target_source", "tp_model", "target_model"], ""))
    )
    liquidity = _target_candidate_option(candidates, "liquidity_target", "real_liquidity", "real_liquidity_target")
    capped = _target_candidate_option(candidates, "capped_before_obstacle")
    if _has_real_liquidity_target_candidate(payload, item) and obstacle:
        return True
    if _boolish(capped.get("available"), False) or _floatish(_get_any(capped, ["tp2", "tp", "target"], 0.0), 0.0) > 0:
        return True
    if "synthetic_rr_fallback" in target_source and obstacle:
        return True
    if obstacle in {"crossed_opposing_imbalance", "crossed_htf_opposing_imbalance", "opposing_imbalance"}:
        return True
    if "opposing" in obstacle or "imbalance" in obstacle:
        return True
    return False

def _has_real_liquidity_target_candidate(payload: Dict[str, Any], item: Dict[str, Any] | None = None) -> bool:
    item = item if isinstance(item, dict) else {}
    plan = _as_dict(payload.get("plan"))
    candidates = _target_candidates(payload, item)
    liquidity = _target_candidate_option(candidates, "liquidity_target", "real_liquidity", "real_liquidity_target")
    tp = _floatish(
        _get_any(
            liquidity,
            ["tp2", "target", "price"],
            _get_any(item, ["liquidity_target_preserved"], _get_any(plan, ["liquidity_target_preserved"], 0.0)),
        ),
        0.0,
    )
    return tp > 0.0 and _boolish(liquidity.get("available"), True)

def _compact_target_candidates(candidates: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(candidates, dict) or not candidates:
        return {}
    out: Dict[str, Any] = {
        "arbitration_required": candidates.get("arbitration_required"),
        "current_target_source": candidates.get("current_target_source"),
        "current_tp_model": candidates.get("current_tp_model"),
        "current_tp2": candidates.get("current_tp2"),
        "current_rr2": candidates.get("current_rr2"),
        "obstacle_kind": candidates.get("obstacle_kind"),
        "obstacle_price": candidates.get("obstacle_price"),
        "obstacle_r": candidates.get("obstacle_r"),
        "obstacle_distance_r": candidates.get("obstacle_distance_r"),
        "obstacle_tf": candidates.get("obstacle_tf"),
        "obstacle_strength_features": candidates.get("obstacle_strength_features"),
        "effective_fallback_rr": candidates.get("effective_fallback_rr"),
        "blocker_features": candidates.get("blocker_features") if isinstance(candidates.get("blocker_features"), dict) else {},
    }
    for key in (
        "liquidity_target",
        "partial_before_obstacle_then_liquidity",
        "capped_before_obstacle",
        "synthetic_rr_fallback",
        "synthetic_rr_capped_to_max_distance",
    ):
        option = candidates.get(key)
        if isinstance(option, dict):
            out[key] = {
                "available": option.get("available"),
                "model": option.get("model"),
                "tp": option.get("tp"),
                "tp1": option.get("tp1"),
                "rr": option.get("rr"),
                "rr1": option.get("rr1"),
                "tp2": option.get("tp2"),
                "rr2": option.get("rr2"),
                "configured_rr": option.get("configured_rr"),
                "effective_rr2": option.get("effective_rr2"),
                "reward_distance_price": option.get("reward_distance_price"),
                "max_allowed_distance": option.get("max_allowed_distance"),
                "exceeds_max_target_distance": option.get("exceeds_max_target_distance"),
                "min_rr_pass": option.get("min_rr_pass"),
                "max_rr_pass": option.get("max_rr_pass"),
                "feasible_for_tp2": option.get("feasible_for_tp2"),
                "feasible_for_tp1_only": option.get("feasible_for_tp1_only"),
                "infeasible_reason": option.get("infeasible_reason"),
                "reason": option.get("reason"),
                "valid_structurally": option.get("valid_structurally"),
                "blocked_by_obstacle": option.get("blocked_by_obstacle"),
                "partial_allowed": option.get("partial_allowed"),
                "crosses_obstacle": option.get("crosses_obstacle"),
            }
    return out

def _target_model_aliases(model: str) -> set[str]:
    m = _norm_text(model)
    aliases = {m} if m else set()
    if m in {"fallback", "synthetic"} or "synthetic_rr_fallback" in m:
        aliases.update({"synthetic_rr_fallback", "ai_selected_synthetic_rr_fallback"})
    if "synthetic_rr_capped" in m:
        aliases.update({"synthetic_rr_capped_to_max_distance", "ai_selected_synthetic_rr_capped_to_max_distance"})
    if "partial_before_obstacle" in m or "partial_then_liquidity" in m:
        aliases.update({"partial_before_obstacle_then_liquidity", "partial_then_liquidity"})
    if "capped_before" in m or "cap_before" in m:
        aliases.update({"capped_before_obstacle", "ai_selected_capped_before_obstacle"})
    if "liquidity" in m:
        aliases.update({"liquidity_target", "real_liquidity_target", "ai_selected_liquidity_target"})
    return aliases

def _target_option_feasible(option: Dict[str, Any]) -> bool:
    if not isinstance(option, dict) or not option:
        return False
    available = _boolish(option.get("available"), True)
    feasible = _boolish(option.get("feasible_for_tp2"), available)
    reason = str(option.get("infeasible_reason") or "").strip()
    direction_valid = _boolish(option.get("target_direction_valid"), True)
    already_reached = _boolish(option.get("target_already_reached"), False)
    exceeds_max = _boolish(option.get("exceeds_max_target_distance"), False)
    max_distance_pass = _boolish(option.get("max_distance_pass"), not exceeds_max)
    return bool(available and feasible and not reason and direction_valid and not already_reached and not exceeds_max and max_distance_pass)

def _target_option_price_rr(option: Dict[str, Any]) -> tuple[float, float]:
    return (
        _floatish(_get_any(option, ["tp2", "tp", "target", "price"], 0.0), 0.0),
        _floatish(_get_any(option, ["rr2", "rr", "effective_rr2"], 0.0), 0.0),
    )

def _target_blocker_is_killer(target_candidates: Dict[str, Any]) -> bool:
    blocker = _as_dict(target_candidates.get("blocker_features"))
    cls = _norm_text(
        _get_any(
            blocker,
            ["blocker_class", "obstacle_class", "class"],
            _get_any(target_candidates, ["blocker_class", "obstacle_class"], ""),
        )
    )
    severity = _floatish(
        _get_any(
            blocker,
            ["blocker_severity", "obstacle_severity", "severity"],
            _get_any(target_candidates, ["blocker_severity"], 0.0),
        ),
        0.0,
    )
    return cls in {"killer", "kill"} or _boolish(_get_any(blocker, ["blocker_is_trade_killer", "is_trade_killer"], False), False) or severity >= 8.0

def _apply_target_choice_to_decision(decision: Decision, model: str, option: Dict[str, Any], reason: str) -> Decision:
    tp, rr = _target_option_price_rr(option)
    tp1 = _floatish(_get_any(option, ["tp1", "partial_tp"], decision.chosen_tp1), 0.0)
    rr1 = _floatish(_get_any(option, ["rr1", "partial_rr"], decision.chosen_rr1), 0.0)
    decision.chosen_target_model = model
    decision.chosen_tp1 = tp1
    decision.chosen_tp2 = tp
    decision.chosen_rr1 = rr1
    decision.chosen_rr2 = rr
    decision.target_decision_reason = (decision.target_decision_reason + " | " if decision.target_decision_reason else "") + reason
    if isinstance(decision.target_arbitration, dict):
        decision.target_arbitration["chosen_target_model"] = model
        decision.target_arbitration["chosen_tp1"] = tp1
        decision.target_arbitration["chosen_tp2"] = tp
        decision.target_arbitration["chosen_rr1"] = rr1
        decision.target_arbitration["chosen_rr2"] = rr
        decision.target_arbitration["target_decision_reason"] = decision.target_decision_reason
    return decision

def _reject_no_feasible_target(decision: Decision) -> Decision:
    decision.allow = False
    if decision.rejection_codes is None:
        decision.rejection_codes = []
    if "no_feasible_target" not in decision.rejection_codes:
        decision.rejection_codes.append("no_feasible_target")
    decision.llm_quality_reject_reason = "no_feasible_target"
    if isinstance(decision.reasons, dict):
        decision.reasons["target_choice_reject"] = "no_feasible_target"
    else:
        decision.reasons = {"ai_reasons": decision.reasons, "target_choice_reject": "no_feasible_target"}
    return decision

def _validate_ai_target_choice_against_feasibility(payload: Dict[str, Any], decision: Decision, chosen_index: int) -> Decision:
    candidates_list = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    item = candidates_list[chosen_index] if 0 <= chosen_index < len(candidates_list) and isinstance(candidates_list[chosen_index], dict) else _as_dict(payload.get("plan"))
    tc = _target_candidates(payload, item)
    if not tc:
        return decision
    chosen = _norm_text(decision.chosen_target_model)
    if not chosen or chosen in {"current", "current_plan", "keep_current"}:
        return decision
    feasible_by_key: Dict[str, Dict[str, Any]] = {}
    for key, option in tc.items():
        if isinstance(option, dict) and _target_option_feasible(option):
            feasible_by_key[_norm_text(key)] = option
            model = _norm_text(str(option.get("model") or ""))
            if model:
                feasible_by_key[model] = option
    chosen_aliases = _target_model_aliases(chosen)
    feasible = any(alias in feasible_by_key for alias in chosen_aliases)
    log(f"[ai_target_choice_validation] chosen={chosen} feasible={str(feasible).lower()}")
    if feasible:
        return decision
    priority = [
        ("partial_before_obstacle_then_liquidity", "partial-before-obstacle TP1 with valid liquidity TP2"),
        ("liquidity_target", "liquidity target feasible and blocker is not killer"),
        ("capped_before_obstacle", "capped-before-obstacle target feasible"),
        ("synthetic_rr_capped_to_max_distance", "raw configured target infeasible; capped target is feasible"),
        ("synthetic_rr_fallback", "synthetic fallback feasible"),
    ]
    blocker_killer = _target_blocker_is_killer(tc)
    for model, reason in priority:
        if model == "liquidity_target" and blocker_killer:
            continue
        option = _target_candidate_option(tc, model)
        if _target_option_feasible(option):
            log(f"[ai_target_choice_validation] chosen={chosen} feasible=false action=rewrite to={model}")
            return _apply_target_choice_to_decision(decision, model, option, reason)
    log(f"[ai_target_choice_validation] chosen={chosen} feasible=false reject_reason=no_feasible_target")
    return _reject_no_feasible_target(decision)


def _synchronize_selected_assessment_contract(payload: Dict[str, Any], decision: Decision) -> Decision:
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("candidate_hash") or "") == decision.selected_candidate_hash
            and str(item.get("candidate_id") or "") == decision.selected_candidate_id
        ),
        None,
    )
    if candidate is None:
        decision.allow = False
        decision.mandatory_fields_complete = False
        decision.llm_quality_reject_reason = "candidate_hash_mismatch"
        return decision
    assessments = list(decision.candidate_assessments or [])
    selected = next(
        (
            item
            for item in assessments
            if isinstance(item, dict)
            and str(item.get("candidate_hash") or "") == decision.selected_candidate_hash
        ),
        None,
    )
    if selected is None:
        decision.allow = False
        decision.mandatory_fields_complete = False
        decision.llm_quality_reject_reason = "ai_quality_schema_incomplete"
        return decision

    entry = float(candidate.get("entry_est") or 0.0)
    sl = float(candidate.get("sl") or 0.0)
    tp1 = float(decision.chosen_tp1 if decision.chosen_tp1 > 0.0 else candidate.get("tp1") or 0.0)
    tp2 = float(decision.chosen_tp2 if decision.chosen_tp2 > 0.0 else candidate.get("tp2") or 0.0)
    target_identity = str(decision.chosen_target_model or selected.get("selected_target_identity") or "")
    request_fingerprint = str(candidate.get("request_execution_fingerprint") or "")
    selected.update(
        {
            "candidate_index": int(decision.chosen_index),
            "candidate_id": decision.selected_candidate_id,
            "candidate_hash": decision.selected_candidate_hash,
            "request_execution_fingerprint": request_fingerprint,
            "selected_target_identity": target_identity,
            "selected_target_price": tp2,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
        }
    )
    if isinstance(selected.get("target_arbitration"), dict):
        selected["target_arbitration"].update(
            {
                "chosen_target_model": target_identity,
                "chosen_tp1": tp1,
                "chosen_tp2": tp2,
                "chosen_rr1": float(decision.chosen_rr1),
                "chosen_rr2": float(decision.chosen_rr2),
                "target_decision_reason": str(decision.target_decision_reason or ""),
            }
        )
    selected["assessed_execution_fingerprint"] = deterministic_assessed_execution_fingerprint(candidate, selected)
    decision.request_execution_fingerprint = request_fingerprint
    decision.assessed_execution_fingerprint = str(selected["assessed_execution_fingerprint"])
    decision.selected_target_identity = target_identity
    decision.selected_target_price = tp2
    decision.assessed_entry = entry
    decision.assessed_sl = sl
    decision.assessed_tp1 = tp1
    decision.assessed_tp2 = tp2
    decision.candidate_assessments = assessments
    return decision


def _bind_cached_decision_to_current_request(
    payload: Dict[str, Any],
    decision: Decision,
) -> tuple[bool, str]:
    """Rebind a semantically identical cached decision to one bus request.

    Economic identity is enforced by the cache signature and candidate
    fingerprints. Request ID and request-identity hash are transport bindings,
    so a cached assessment must echo the current request before it can be
    serialized back to MQL.
    """

    request_id = str(payload.get("id") or "")
    request_identity_hash = str(payload.get("request_identity_hash") or "")
    candidates = payload.get("candidates")
    assessments = decision.candidate_assessments
    if not request_id or not request_identity_hash:
        return False, "cache_current_request_identity_missing"
    if not isinstance(candidates, list) or not isinstance(assessments, list):
        return False, "cache_candidate_contract_missing"
    if len(candidates) != len(assessments):
        return False, "cache_candidate_count_mismatch"

    assessment_by_index: dict[int, Dict[str, Any]] = {}
    for raw in assessments:
        if not isinstance(raw, dict):
            return False, "cache_candidate_assessment_invalid"
        try:
            index = int(raw.get("candidate_index"))
        except (TypeError, ValueError):
            return False, "cache_candidate_index_invalid"
        if index in assessment_by_index:
            return False, "cache_candidate_index_duplicate"
        assessment_by_index[index] = raw

    provider_id = str(decision.provider_id or "")
    model_id = str(decision.actual_model_id or decision.model_version or "")
    if not provider_id or not model_id:
        return False, "cache_provider_identity_missing"
    rebound: list[Dict[str, Any]] = []
    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            return False, "cache_current_candidate_invalid"
        try:
            candidate_index = int(candidate.get("candidate_index"))
        except (TypeError, ValueError):
            return False, "cache_current_candidate_index_invalid"
        assessment = assessment_by_index.get(candidate_index)
        if assessment is None:
            return False, "cache_candidate_index_mismatch"
        for field in (
            "candidate_id",
            "candidate_hash",
            "request_execution_fingerprint",
        ):
            if str(assessment.get(field) or "") != str(candidate.get(field) or ""):
                return False, f"cache_{field}_mismatch"
        assessment["request_id"] = request_id
        assessment["request_identity_hash"] = request_identity_hash
        assessment["provider_id"] = provider_id
        assessment["model_id"] = model_id
        assessment["role_schema_version"] = ROLE_CONTRACT_VERSION
        rebound.append(assessment)

    decision.candidate_assessments = rebound
    return True, ""


def _synchronize_candidate_authority_fields(decision: Decision) -> Decision:
    decision = _synchronize_decision_authority_fields(decision)
    assessments = list(decision.candidate_assessments or [])
    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        model_allow = bool(assessment.get("raw_allow", False))
        assessment["model_raw_allow"] = model_allow
        if str(assessment.get("candidate_hash") or "") == decision.selected_candidate_hash:
            assessment["python_final_allow"] = bool(decision.python_final_allow)
        else:
            # Non-selected candidates have an independent model assessment but
            # were not chosen by Python, so they carry no final authority.
            assessment["python_final_allow"] = False
        assessment["mql_final_allow"] = None
        assessment["authority_sources"] = {
            "entry_sl_tp": "deterministic",
            "broker_feasibility": "deterministic",
            "llm_quality_score": "llm_uncalibrated_diagnostic_only",
            "llm_numeric_risk_fields": "llm_uncalibrated_diagnostic_only",
            "veto": "llm_enumerated_evidence_backed_only",
            "calibrated_probability": "statistical_unavailable",
            "expected_net_r": "statistical_unavailable",
            "risk_size": "deterministic_portfolio",
        }
    decision.candidate_assessments = assessments
    return decision

def _runtime_threshold_float(payload: Dict[str, Any], key: str, default: float) -> float:
    runtime_inputs = _runtime_inputs(payload)
    return _floatish(runtime_inputs.get(key), default)

def _apply_ai_veto_gate(payload: Dict[str, Any], decision: Decision) -> Decision:
    runtime_inputs = _runtime_inputs(payload)
    threshold_crossings: list[str] = []
    if decision.follow_through_probability < _runtime_threshold_float(payload, "ai_min_follow_through_prob", 0.58):
        threshold_crossings.append("follow_through_probability")
    if decision.invalidation_risk > _runtime_threshold_float(payload, "ai_max_invalidation_risk", 0.62):
        threshold_crossings.append("invalidation_risk")
    if decision.chop_risk > _runtime_threshold_float(payload, "ai_max_chop_risk", 0.65):
        threshold_crossings.append("chop_risk")
    if decision.post_entry_failure_risk > _runtime_threshold_float(payload, "ai_max_post_entry_failure_risk", 0.62):
        threshold_crossings.append("post_entry_failure_risk")
    if decision.final_trade_expectancy_score < _runtime_threshold_float(payload, "ai_min_final_expectancy_score", 6.80):
        threshold_crossings.append("final_trade_expectancy_score")
    log(
        "[llm_numeric_diagnostics]"
        f" req_id={payload.get('id') or ''} authority=diagnostic_only_no_direct_trade_authority"
        f" threshold_crossings={','.join(threshold_crossings) or 'none'}"
        f" follow_through={decision.follow_through_probability:.4f}"
        f" invalidation_risk={decision.invalidation_risk:.4f}"
        f" chop_risk={decision.chop_risk:.4f}"
        f" cost_risk={decision.cost_risk:.4f}"
        f" post_entry_failure_risk={decision.post_entry_failure_risk:.4f}"
        f" expectancy_score={decision.final_trade_expectancy_score:.4f}"
    )
    if not _boolish(runtime_inputs.get("ai_veto_enable"), True):
        return decision
    if not decision.veto_enabled:
        return decision
    evidence = [str(field).strip() for field in (decision.veto_evidence_fields or []) if str(field).strip()]
    if decision.veto_code not in LLM_VETO_CODES or not evidence or not decision.veto_reason.strip():
        decision.allow = False
        decision.raw_allow = False
        decision.model_raw_allow = False
        decision.python_final_allow = False
        decision.decision_state = DECISION_REJECT
        decision.suggested_risk_multiplier = 0.0
        decision.llm_quality_reject_reason = "ai_quality_schema_incomplete"
        if decision.rejection_codes is None:
            decision.rejection_codes = []
        if "ai_quality_schema_incomplete" not in decision.rejection_codes:
            decision.rejection_codes.append("ai_quality_schema_incomplete")
        return decision
    decision.allow = False
    decision.python_final_allow = False
    decision.llm_quality_reject_reason = "ai_veto"
    if decision.rejection_codes is None:
        decision.rejection_codes = []
    if "ai_veto" not in decision.rejection_codes:
        decision.rejection_codes.append("ai_veto")
    if decision.veto_code not in decision.rejection_codes:
        decision.rejection_codes.append(decision.veto_code)
    if isinstance(decision.reasons, dict):
        decision.reasons["ai_veto"] = {
            "code": decision.veto_code,
            "evidence_fields": evidence,
            "reason": decision.veto_reason,
        }
    else:
        decision.reasons = {
            "ai_reasons": decision.reasons,
            "ai_veto": {
                "code": decision.veto_code,
                "evidence_fields": evidence,
                "reason": decision.veto_reason,
            },
        }
    log(
        "[ai_veto]"
        f" req_id={payload.get('id') or ''}"
        f" code={decision.veto_code} evidence_fields={json.dumps(evidence, separators=(',', ':'))}"
        f" reason={decision.veto_reason} authority=qualitative_evidence_backed_veto"
    )
    return decision

def _floatish(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if out != out or out in (float("inf"), float("-inf")):
            return default
        return out
    except Exception:
        return default

def _target_kwargs_from_dict(raw: Dict[str, Any] | None, present_fields: set[str] | None = None) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    present = present_fields if present_fields is not None else set(data.keys())
    rejected = data.get("rejected_target_models")
    if not isinstance(rejected, list):
        rejected = []
    comparison = data.get("target_comparison")
    if not isinstance(comparison, dict):
        comparison = {}
    severity_value = data.get("blocker_severity", data.get("target_blocker_severity"))
    class_value = data.get("blocker_class", data.get("target_blocker_class"))
    killer_value = data.get("blocker_is_trade_killer", data.get("target_blocker_is_trade_killer"))
    reason_value = data.get("target_decision_reason", data.get("reason"))
    return {
        "target_arbitration": data,
        "chosen_target_model": str(data.get("chosen_target_model") or data.get("chosen_model") or ""),
        "chosen_tp1": _floatish(data.get("chosen_tp1"), 0.0),
        "chosen_tp2": _floatish(data.get("chosen_tp2"), 0.0),
        "chosen_rr1": _floatish(data.get("chosen_rr1"), 0.0),
        "chosen_rr2": _floatish(data.get("chosen_rr2"), 0.0),
        "rejected_target_models": [str(x) for x in rejected if str(x or "").strip()],
        "target_blocker_kind": str(data.get("blocker_kind") or data.get("target_blocker_kind") or ""),
        "target_blocker_severity": _floatish(severity_value, -1.0),
        "target_blocker_class": str(class_value or ""),
        "target_blocker_is_trade_killer": _boolish(killer_value, False),
        "target_decision_reason": str(reason_value or ""),
        "target_blocker_severity_present": ("blocker_severity" in present or "target_blocker_severity" in present) and severity_value is not None,
        "target_blocker_class_present": ("blocker_class" in present or "target_blocker_class" in present) and class_value is not None,
        "target_blocker_is_trade_killer_present": ("blocker_is_trade_killer" in present or "target_blocker_is_trade_killer" in present) and killer_value is not None,
        "target_decision_reason_present": ("target_decision_reason" in present or "reason" in present) and reason_value is not None,
        "why_not_liquidity_target": str(data.get("why_not_liquidity_target") or ""),
        "why_not_partial_before_obstacle": str(data.get("why_not_partial_before_obstacle") or ""),
        "why_not_capped_before_obstacle": str(data.get("why_not_capped_before_obstacle") or ""),
        "why_not_synthetic_fallback": str(data.get("why_not_synthetic_fallback") or ""),
        "target_arbitration_schema_version": str(data.get("target_arbitration_schema_version") or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
        "prompt_contract_version": str(data.get("prompt_contract_version") or AI_PROMPT_CONTRACT_VERSION),
        "target_comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")) if comparison else "{}",
    }

def _target_kwargs_from_model(model_obj: Any) -> Dict[str, Any]:
    if model_obj is None:
        return _target_kwargs_from_dict({})
    if hasattr(model_obj, "model_dump"):
        try:
            present_fields = set(getattr(model_obj, "model_fields_set", set()) or set())
            return _target_kwargs_from_dict(model_obj.model_dump(), present_fields=present_fields)
        except Exception:
            pass
    if isinstance(model_obj, dict):
        return _target_kwargs_from_dict(model_obj)
    return _target_kwargs_from_dict({})

def _ai_quality_kwargs_from_model(model_obj: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if hasattr(model_obj, "model_dump"):
        try:
            data = model_obj.model_dump()
        except Exception:
            data = {}
    elif isinstance(model_obj, dict):
        data = model_obj
    veto = data.get("veto") if isinstance(data.get("veto"), dict) else {}
    return {
        "raw_allow": _boolish(data.get("raw_allow"), _boolish(data.get("allow"), True)),
        "structure_quality_score": _floatish(data.get("structure_quality_score"), 6.5),
        "entry_timing_score": _floatish(data.get("entry_timing_score"), 6.5),
        "follow_through_probability": _floatish(data.get("follow_through_probability"), 0.65),
        "invalidation_risk": _floatish(data.get("invalidation_risk"), 0.50),
        "chop_risk": _floatish(data.get("chop_risk"), 0.50),
        "cost_risk": _floatish(data.get("cost_risk"), 0.50),
        "symbol_bucket_risk": _floatish(data.get("symbol_bucket_risk"), 0.50),
        "session_bucket_risk": _floatish(data.get("session_bucket_risk"), 0.50),
        "post_entry_failure_risk": _floatish(data.get("post_entry_failure_risk"), 0.50),
        "final_trade_expectancy_score": _floatish(data.get("final_trade_expectancy_score"), 7.0),
        "veto_enabled": _boolish(data.get("veto_enabled"), _boolish(veto.get("enabled"), False)),
        "veto_code": str(data.get("veto_code") or veto.get("code") or ""),
        "veto_evidence_fields": list(
            data.get("veto_evidence_fields")
            if isinstance(data.get("veto_evidence_fields"), list)
            else (veto.get("evidence_fields") if isinstance(veto.get("evidence_fields"), list) else [])
        ),
        "veto_reason": str(data.get("veto_reason") or veto.get("reason") or ""),
        "bucket_prior_override_justification": str(data.get("bucket_prior_override_justification") or ""),
    }


def _json_object_from_text(text: Any) -> Dict[str, Any]:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip().startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _response_usage_tokens(response: Any) -> tuple[Any, Any]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    def _read(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    input_tokens = _read(usage, "input_tokens")
    output_tokens = _read(usage, "output_tokens")
    if input_tokens is None:
        input_tokens = _read(usage, "prompt_tokens")
    if output_tokens is None:
        output_tokens = _read(usage, "completion_tokens")
    return input_tokens, output_tokens


def _cost_report_path() -> Path:
    return resolve_project_path(AI_CONFIG.cost_report_file)


def _write_ai_cost_report(
    payload: Dict[str, Any],
    *,
    request_id: str = "",
    decision_source: str = "",
    model: str = "",
    reasoning_effort: str = "",
    service_tier: str = "",
    prompt_cache_enabled: bool | None = None,
    cache_status: str = "",
    batch_used: bool = False,
    flex_used: bool = False,
    response: Any = None,
    openai_called: bool = False,
    skip_reason: str = "",
    input_tokens: Any = None,
    output_tokens: Any = None,
    llm_quality_score_threshold: Any = None,
    llm_quality_threshold_source: str = "",
    llm_quality_threshold_passed: Any = None,
) -> None:
    if not AI_CONFIG.cost_report_enable:
        return
    try:
        plan = _as_dict(payload.get("plan"))
        po3 = _as_dict(payload.get("po3"))
        cands = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        first_cand = cands[0] if cands and isinstance(cands[0], dict) else {}
        if input_tokens is None and output_tokens is None and response is not None:
            input_tokens, output_tokens = _response_usage_tokens(response)
        provider_identity = _provider().identity("analyst")
        row = {
            "timestamp": int(time.time()),
            "request_id": str(request_id or payload.get("id") or ""),
            "symbol": str(payload.get("symbol") or ""),
            "setup_family": str(
                _get_any(first_cand, ["setup_family"], _get_any(plan, ["setup_family"], _get_any(po3, ["final_setup_class"], "")))
                or ""
            ),
            "decision_source": str(decision_source or ""),
            "model": str(model or ""),
            "provider_mode": str(provider_identity.get("provider_mode") or "UNAVAILABLE"),
            "provider_id": str(provider_identity.get("provider_id") or "unavailable"),
            "endpoint_class": str(provider_identity.get("endpoint_class") or "invalid"),
            "reasoning_effort": str(reasoning_effort or ""),
            "service_tier": str(service_tier or ""),
            "prompt_cache_enabled": bool(AI_CONFIG.prompt_cache_enable if prompt_cache_enabled is None else prompt_cache_enabled),
            "cache_status": str(cache_status or ""),
            "batch_used": bool(batch_used),
            "flex_used": bool(flex_used),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": None,
            "openai_called": bool(openai_called),
            "provider_called": bool(response is not None or openai_called),
            "remote_cost_applicable": bool(
                str(provider_identity.get("provider_mode") or "") == PROVIDER_MODE_REMOTE
            ),
            "skip_reason": str(skip_reason or ""),
            "llm_quality_score_threshold": llm_quality_score_threshold,
            "llm_quality_threshold_source": str(llm_quality_threshold_source or ""),
            "llm_quality_threshold_passed": llm_quality_threshold_passed,
            "target_arbitration_required": _target_arbitration_required(payload, first_cand or plan),
            "target_candidates": _compact_target_candidates(_target_candidates(payload, first_cand or plan)),
        }
        path = _cost_report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        log(f"[ai_gate] cost_report_write_failed error={exc}")


RUNTIME_INPUT_MIGRATION_ALIASES = {
    "min_llm_quality_score_trend": "min_ai_score_trend",
    "llm_quality_score_full_po3": "ai_score_full_po3",
    "llm_quality_score_micro_po3": "ai_score_micro_po3",
    "llm_quality_score_continuation": "ai_score_continuation",
    "llm_quality_score_range": "ai_score_range",
    "llm_quality_score_failed_breakout": "ai_score_failed_breakout",
    "global_llm_quality_as_hard_floor": "global_ai_score_as_hard_floor",
    "legacy_min_ai_confidence_diagnostic": "min_ai_confidence",
}
_RUNTIME_INPUT_MIGRATION_LOGGED: set[str] = set()


def _runtime_inputs(payload: Dict[str, Any]) -> Dict[str, Any]:
    runtime_inputs = dict(_as_dict(payload.get("runtime_inputs")))
    for canonical, legacy in RUNTIME_INPUT_MIGRATION_ALIASES.items():
        if canonical in runtime_inputs or legacy not in runtime_inputs:
            continue
        runtime_inputs[canonical] = runtime_inputs[legacy]
        if legacy not in _RUNTIME_INPUT_MIGRATION_LOGGED:
            _RUNTIME_INPUT_MIGRATION_LOGGED.add(legacy)
            authority = "diagnostic_only" if canonical == "legacy_min_ai_confidence_diagnostic" else "llm_quality_threshold_alias"
            log(
                "[runtime_input_migration]"
                f" legacy_field={legacy} canonical_field={canonical} authority={authority}"
            )
    return runtime_inputs


AI_GATE_COUNTERS: Dict[str, int] = {
    "requests_processed": 0,
    "schema_valid_responses": 0,
    "schema_invalid_responses": 0,
    "degraded_responses": 0,
    "identity_mismatches": 0,
    "ai_approvals": 0,
    "ai_rejections": 0,
    "ai_abstentions": 0,
    "cache_writes": 0,
    "hard_pre_gate_checked": 0,
    "hard_pre_gate_rejected": 0,
    "ai_calls_skipped_by_hard_gate": 0,
    "ai_cache_hit": 0,
    "ai_cache_miss": 0,
}
AI_GATE_COUNTERS_LOCK = Lock()


def _inc_counter(name: str) -> None:
    with AI_GATE_COUNTERS_LOCK:
        AI_GATE_COUNTERS[name] = AI_GATE_COUNTERS.get(name, 0) + 1


CRITICAL_RUNTIME_INPUT_KEYS = [
    "runtime_input_hash",
    "trade_only_killzones",
    "enable_asia_killzone",
    "asia_killzone_start_hour",
    "asia_killzone_start_minute",
    "asia_killzone_end_hour",
    "asia_killzone_end_minute",
    "london_killzone_start_hour",
    "london_killzone_start_minute",
    "london_killzone_end_hour",
    "london_killzone_end_minute",
    "newyork_killzone_start_hour",
    "newyork_killzone_start_minute",
    "newyork_killzone_end_hour",
    "newyork_killzone_end_minute",
    "enable_mpc_trading",
    "enable_bucket_risk_policy",
    "bucket_risk_policy_file",
    "suppress_micro_bisi_sibi_edge",
    "suppress_stale_fvg_branches",
    "suppress_touched_continuation_unless_retested",
    "suppress_continuation_touched_fvg",
    "suppress_continuation_stale_fvg",
    "reject_synthetic_fallback_after_crossed_obstacle",
    "require_ai_target_arbitration_on_obstacle",
    "hard_reject_crossed_obstacle_target",
    "allow_ai_to_use_liquidity_target_behind_minor_blocker",
    "allow_partial_before_obstacle",
    "blocker_kill_severity",
    "blocker_major_severity",
    "blocker_minor_max_severity",
    "execution_reject_cost_r",
    "execution_reduce_risk_cost_r",
    "micro_scalp_max_cost_frac_of_planned_r",
    "require_displacement",
    "allow_synthetic_rr_target",
    "min_live_rr2",
    "fallback_rr2",
    "fallback_rr_buffer_r",
    "tester_ai_cache",
    "tester_ai_mode",
    "tester_allow_live_wait_debug_trading",
    "standard_trade_liquidity_rr_floor",
    "max_target_atr_mult",
    "max_target_adr_frac",
    "obstacle_reject_r",
    "use_ai",
    "ai_strict",
    "min_llm_quality_score_trend",
    "ai_veto_enable",
    "ai_min_follow_through_prob",
    "ai_max_invalidation_risk",
    "ai_max_chop_risk",
    "ai_max_post_entry_failure_risk",
    "ai_min_final_expectancy_score",
    "llm_quality_score_full_po3",
    "llm_quality_score_micro_po3",
    "llm_quality_score_continuation",
    "llm_quality_score_range",
    "llm_quality_score_failed_breakout",
    "global_llm_quality_as_hard_floor",
    "legacy_min_ai_confidence_diagnostic",
    "use_snapshot_ai",
    "require_snapshots",
    "exclusive_trading_enabled",
    "virtual_ledger_mode",
    "backend_pnl_mode",
]

# Workflow/debug-only tester controls must not enter the AI decision cache key.
# RECORD_ONLY decisions may replay in CACHE_ONLY for the same market setup;
# LIVE_WAIT_DEBUG remains diagnostic and is not an authoritative replay source.
# MQL may still include these values in runtime_input_hash and logs.
AI_DECISION_CACHE_SIGNATURE_IGNORED_FIELDS = {
    "runtime_input_hash",
    "inp_tester_ai_cache",
    "inp_tester_ai_mode",
    "inp_tester_allow_live_wait_debug_trading",
}


def _bucket_float(value: Any, step: float = 0.0001) -> Any:
    try:
        val = float(value)
        if not (val == val) or val in (float("inf"), float("-inf")):
            return None
        if step <= 0:
            return round(val, 6)
        return round(round(val / step) * step, 6)
    except Exception:
        return None


def _cache_payload_parts(payload: Dict[str, Any], best_index: int) -> tuple[Dict[str, Any], Dict[str, Any]]:
    plan = _as_dict(payload.get("plan"))
    po3 = _as_dict(payload.get("po3"))
    fvg = _as_dict(payload.get("fvg"))
    cands = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    cand = cands[best_index] if 0 <= best_index < len(cands) and isinstance(cands[best_index], dict) else {}
    merged = {**plan, **fvg, **cand}
    return merged, po3


def _decision_cache_signature(payload: Dict[str, Any], best_index: int) -> tuple[str, str, Dict[str, Any]]:
    merged, po3 = _cache_payload_parts(payload, best_index)
    runtime_inputs = _runtime_inputs(payload)
    compact_targets = _compact_target_candidates(_target_candidates(payload, merged))
    blocker_features = compact_targets.get("blocker_features") if isinstance(compact_targets.get("blocker_features"), dict) else {}
    target_blob = json.dumps(compact_targets, sort_keys=True, separators=(",", ":"))
    session_name = _get_any(merged, ["session_name"], _get_any(po3, ["session_name"], payload.get("session_name")))
    in_killzone = _get_any(merged, ["in_killzone"], _get_any(po3, ["in_killzone"], payload.get("in_killzone")))
    direction = _get_any(merged, ["direction"], "buy" if payload.get("is_buy") else "sell")
    execution_cost_r = (
        _floatish(_get_any(merged, ["execution_cost_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["slippage_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["commission_r"], 0.0), 0.0)
    )
    spread_r = _get_any(merged, ["spread_r"], _get_any(payload, ["spread_r"], None))
    snapshot_metadata = _as_dict(payload.get("snapshot_metadata"))
    candidate_contracts = []
    for candidate in payload.get("candidates") if isinstance(payload.get("candidates"), list) else []:
        if not isinstance(candidate, dict):
            continue
        candidate_contracts.append(
            {
                "candidate_index": int(candidate.get("candidate_index") or 0),
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "candidate_hash": str(candidate.get("candidate_hash") or ""),
                "request_execution_fingerprint": str(candidate.get("request_execution_fingerprint") or ""),
                "target_candidates": _compact_target_candidates(_target_candidates(payload, candidate)),
            }
        )
    candidate_contract_blob = json.dumps(candidate_contracts, sort_keys=True, separators=(",", ":"))
    prior_artifact = _load_live_bucket_priors()
    provider_identity = _provider().generation_identity(
        "analyst",
        _provider_request_metadata(payload),
    )
    try:
        retrieval_contract = _trade_memory_store().retrieve_analogues(
            merged,
            request_id=str(payload.get("id") or ""),
            lineage_id=str(payload.get("setup_lineage_id") or payload.get("story_id") or payload.get("id") or ""),
            top_k=AI_CONFIG.local_retrieval_top_k,
        )
        retrieval_contract_hash = retrieval_contract.retrieval_hash
    except Exception:
        retrieval_contract_hash = canonical_hash(
            {
                "state": "INSUFFICIENT_SAMPLE",
                "policy_version": RETRIEVAL_POLICY_VERSION,
                "candidate_hash": str(merged.get("candidate_hash") or ""),
            }
        )
    repeatability_artifact = _load_repeatability_artifact()
    repeatability_groups = repeatability_artifact.get("groups") if isinstance(repeatability_artifact.get("groups"), dict) else {}
    repeatability_group = repeatability_groups.get(
        canonical_hash(
            _repeatability_group_fields(
                str(provider_identity.get("model_id") or AI_CONFIG.model),
                DECISION_QUALITY_FULL_STRUCTURED,
                provider_mode=str(provider_identity.get("provider_mode") or ""),
                provider_id=str(provider_identity.get("provider_id") or ""),
                model_fingerprint=str(provider_identity.get("model_fingerprint") or ""),
                generation_settings_hash=str(provider_identity.get("generation_settings_hash") or ""),
            )
        ), {}
    )
    fields = {
        "semantic_cache_schema_version": SEMANTIC_CACHE_SCHEMA_VERSION,
        "architecture_contract_version": ARCHITECTURE_CONTRACT_VERSION,
        "ai_decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "ai_target_arbitration_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        "ai_prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
        "provider_contract_version": PROVIDER_CONTRACT_VERSION,
        "provider_mode": str(provider_identity.get("provider_mode") or "UNAVAILABLE"),
        "provider_id": str(provider_identity.get("provider_id") or "unavailable"),
        "endpoint_identity_hash": str(provider_identity.get("endpoint_identity_hash") or ""),
        "configured_models_hash": str(provider_identity.get("configured_models_hash") or ""),
        "model_fingerprint": str(provider_identity.get("model_fingerprint") or "unavailable"),
        "family_profile_version": FAMILY_PROFILE_VERSION,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        "retrieval_contract_hash": retrieval_contract_hash,
        "generation_settings_hash": str(provider_identity.get("generation_settings_hash") or ""),
        "candidate_contract_hash": sha256(candidate_contract_blob.encode("utf-8")).hexdigest(),
        "symbol": str(payload.get("symbol") or ""),
        "entry_bar_id": str(
            _get_any(
                snapshot_metadata,
                ["candle_time", "entry_candle_time"],
                _get_any(merged, ["entry_bar_id", "last_confirm_bar_time", "source_t_disp"], ""),
            )
            or ""
        ),
        "direction": str(direction or "").lower(),
        "setup_family": str(_get_any(merged, ["setup_family"], "") or "").lower(),
        "setup_class": str(_get_any(merged, ["setup_class"], "") or "").lower(),
        "entry_branch": str(_get_any(merged, ["entry_branch", "entry_model"], "") or "").lower(),
        "source_t_sweep": str(_get_any(merged, ["source_t_sweep"], _get_any(po3, ["t_sweep"], "")) or ""),
        "source_t_disp": str(_get_any(merged, ["source_t_disp"], _get_any(po3, ["t_disp", "t_displacement"], "")) or ""),
        "source_t_bos": str(_get_any(merged, ["source_t_bos"], _get_any(po3, ["t_bos"], "")) or ""),
        "fvg_lower": _bucket_float(_get_any(merged, ["fvg_lower", "lower"], None)),
        "fvg_upper": _bucket_float(_get_any(merged, ["fvg_upper", "upper"], None)),
        "entry_est": _bucket_float(_get_any(merged, ["entry_est", "entry"], None)),
        "sl": _bucket_float(_get_any(merged, ["sl", "stop_loss", "stop"], None)),
        "tp2": _bucket_float(_get_any(merged, ["tp2", "tp", "final_tp"], None)),
        "structure_state": str(
            _get_any(merged, ["structure_state", "structure_type"], _get_any(po3, ["po3_state", "structure_type"], ""))
            or ""
        ).lower(),
        "fvg_mitigation_state": str(
            _get_any(merged, ["fvg_mitigation_state", "mitigation_state", "fvg_execution_class"], "") or ""
        ).lower(),
        "candidate_hash": str(merged.get("candidate_hash") or ""),
        "execution_fingerprint": str(
            merged.get("request_execution_fingerprint") or merged.get("assessed_execution_fingerprint") or ""
        ),
        "target_source": str(_get_any(merged, ["target_source"], payload.get("target_source")) or "").lower(),
        "target_model": str(_get_any(merged, ["target_model", "tp_model"], payload.get("target_model")) or "").lower(),
        "chosen_target_model": str(_get_any(merged, ["chosen_target_model", "ai_chosen_target_model"], payload.get("chosen_target_model")) or "").lower(),
        "target_candidates_hash": sha256(target_blob.encode("utf-8")).hexdigest() if target_blob != "{}" else "",
        "liquidity_target_preserved": _bucket_float(_get_any(merged, ["liquidity_target_preserved"], None)),
        "liquidity_target": _bucket_float(_get_any(merged, ["liquidity_target", "liquidity_target_preserved"], _get_any(po3, ["liquidity_target"], None))),
        "liquidity_rr": _bucket_float(_get_any(merged, ["liquidity_rr"], None), 0.01),
        "fallback_tp": _bucket_float(_get_any(merged, ["fallback_tp"], None)),
        "fallback_rr": _bucket_float(_get_any(merged, ["fallback_rr"], None), 0.01),
        "effective_fallback_rr": _bucket_float(compact_targets.get("effective_fallback_rr"), 0.01),
        "capped_before_obstacle_tp": _bucket_float(_get_any(merged, ["capped_before_obstacle_tp"], None)),
        "capped_before_obstacle_rr": _bucket_float(_get_any(merged, ["capped_before_obstacle_rr"], None), 0.01),
        "obstacle_kind": str(_get_any(merged, ["obstacle_kind"], payload.get("obstacle_kind")) or "").lower(),
        "obstacle_tf": str(_get_any(merged, ["obstacle_tf"], payload.get("obstacle_tf")) or "").lower(),
        "obstacle_price": _bucket_float(_get_any(merged, ["obstacle_price"], payload.get("obstacle_price"))),
        "obstacle_distance_r": _bucket_float(_get_any(merged, ["obstacle_distance_r"], None), 0.01),
        "obstacle_width_atr": _bucket_float(blocker_features.get("obstacle_width_atr"), 0.01),
        "obstacle_age_bars": _bucket_float(blocker_features.get("obstacle_age_bars"), 1.0),
        "obstacle_mitigated_percent": _bucket_float(blocker_features.get("obstacle_mitigated_percent"), 1.0),
        "tp1_before_obstacle_possible": _boolish(blocker_features.get("tp1_before_obstacle_possible"), False),
        "distance_from_obstacle_to_liquidity_target_r": _bucket_float(blocker_features.get("distance_from_obstacle_to_liquidity_target_r"), 0.01),
        "target_arbitration_required": _boolish(_get_any(merged, ["target_arbitration_required"], payload.get("target_arbitration_required")), False),
        "inp_require_ai_target_arbitration_on_obstacle": _boolish(runtime_inputs.get("require_ai_target_arbitration_on_obstacle"), False),
        "inp_hard_reject_crossed_obstacle_target": _boolish(runtime_inputs.get("hard_reject_crossed_obstacle_target"), False),
        "inp_reject_synthetic_fallback_after_crossed_obstacle": _boolish(runtime_inputs.get("reject_synthetic_fallback_after_crossed_obstacle"), True),
        "inp_allow_ai_to_use_liquidity_target_behind_minor_blocker": _boolish(runtime_inputs.get("allow_ai_to_use_liquidity_target_behind_minor_blocker"), False),
        "inp_allow_partial_before_obstacle": _boolish(runtime_inputs.get("allow_partial_before_obstacle"), False),
        "inp_min_live_rr2": _bucket_float(runtime_inputs.get("min_live_rr2"), 0.01),
        "inp_fallback_rr2": _bucket_float(runtime_inputs.get("fallback_rr2"), 0.01),
        "inp_fallback_rr_buffer_r": _bucket_float(runtime_inputs.get("fallback_rr_buffer_r"), 0.01),
        "inp_tester_ai_cache": _boolish(runtime_inputs.get("tester_ai_cache"), False),
        "inp_tester_ai_mode": str(runtime_inputs.get("tester_ai_mode") or ""),
        "inp_tester_allow_live_wait_debug_trading": _boolish(runtime_inputs.get("tester_allow_live_wait_debug_trading"), False),
        "inp_max_target_atr_mult": _bucket_float(runtime_inputs.get("max_target_atr_mult"), 0.01),
        "inp_max_target_adr_frac": _bucket_float(runtime_inputs.get("max_target_adr_frac"), 0.01),
        "inp_standard_trade_liquidity_rr_floor": _bucket_float(runtime_inputs.get("standard_trade_liquidity_rr_floor"), 0.01),
        "llm_quality_score_full_po3": _bucket_float(runtime_inputs.get("llm_quality_score_full_po3"), 0.01),
        "llm_quality_score_micro_po3": _bucket_float(runtime_inputs.get("llm_quality_score_micro_po3"), 0.01),
        "llm_quality_score_continuation": _bucket_float(runtime_inputs.get("llm_quality_score_continuation"), 0.01),
        "llm_quality_score_range": _bucket_float(runtime_inputs.get("llm_quality_score_range"), 0.01),
        "llm_quality_score_failed_breakout": _bucket_float(runtime_inputs.get("llm_quality_score_failed_breakout"), 0.01),
        "session_name": str(session_name or "").lower(),
        "killzone_code": str(_get_any(merged, ["killzone_code"], "K" if _boolish(in_killzone, False) else "NK") or "").upper(),
        "runtime_input_hash": str(payload.get("runtime_input_hash") or _runtime_inputs(payload).get("runtime_input_hash") or ""),
        "execution_cost_r_bucket": _bucket_float(execution_cost_r, 0.01),
        "spread_r_bucket": _bucket_float(spread_r, 0.01),
        "bucket_prior_hash": _bucket_prior_hash_for_item(merged, payload),
        "policy_version": str(
            payload.get("policy_hash")
            or payload.get("policy_version")
            or runtime_inputs.get("active_policy_hash")
            or runtime_inputs.get("active_policy_id")
            or ""
        ),
        "model_version": str(provider_identity.get("model_id") or AI_CONFIG.model),
        "hierarchical_prior_schema_version": HIERARCHICAL_PRIOR_SCHEMA_VERSION,
        "hierarchical_prior_artifact_hash": str(prior_artifact.get("artifact_hash") or ""),
        "repeatability_schema_version": REPEATABILITY_SCHEMA_VERSION,
        "repeatability_authority_hash": str(repeatability_group.get("artifact_hash") or ""),
        "runtime_governance_versions": runtime_governance_versions(),
    }
    signature_fields = {k: v for k, v in fields.items() if k not in AI_DECISION_CACHE_SIGNATURE_IGNORED_FIELDS}
    full_blob = json.dumps(signature_fields, sort_keys=True, separators=(",", ":"))
    base_fields = dict(signature_fields)
    for key in (
        "tp2",
        "target_source",
        "target_model",
        "chosen_target_model",
        "target_candidates_hash",
        "liquidity_target_preserved",
        "liquidity_target",
        "liquidity_rr",
        "fallback_tp",
        "fallback_rr",
        "effective_fallback_rr",
        "capped_before_obstacle_tp",
        "capped_before_obstacle_rr",
        "obstacle_kind",
        "obstacle_tf",
        "obstacle_price",
        "obstacle_distance_r",
        "obstacle_width_atr",
        "obstacle_age_bars",
        "obstacle_mitigated_percent",
        "tp1_before_obstacle_possible",
        "distance_from_obstacle_to_liquidity_target_r",
        "target_arbitration_required",
        "inp_require_ai_target_arbitration_on_obstacle",
        "inp_hard_reject_crossed_obstacle_target",
        "inp_reject_synthetic_fallback_after_crossed_obstacle",
        "inp_allow_ai_to_use_liquidity_target_behind_minor_blocker",
        "inp_allow_partial_before_obstacle",
        "inp_min_live_rr2",
        "inp_fallback_rr2",
        "inp_fallback_rr_buffer_r",
        "inp_tester_ai_cache",
        "inp_tester_ai_mode",
        "inp_tester_allow_live_wait_debug_trading",
        "inp_max_target_atr_mult",
        "inp_max_target_adr_frac",
        "inp_standard_trade_liquidity_rr_floor",
        "execution_cost_r_bucket",
        "spread_r_bucket",
    ):
        base_fields.pop(key, None)
    base_blob = json.dumps(base_fields, sort_keys=True, separators=(",", ":"))
    return sha256(full_blob.encode("utf-8")).hexdigest(), sha256(base_blob.encode("utf-8")).hexdigest(), fields


def _cached_decision_schema_miss_reason(
    dec_raw: Dict[str, Any],
    current_fields: Mapping[str, Any] | None = None,
) -> str:
    if str(dec_raw.get("decision_schema_version") or "") != AI_DECISION_SCHEMA_VERSION:
        return "cache_miss_due_to_schema_version"
    tier = str(dec_raw.get("decision_quality_tier") or "")
    if tier not in {
        DECISION_QUALITY_FULL_STRUCTURED,
        DECISION_QUALITY_CACHE_FULL_STRUCTURED,
    }:
        return "cache_miss_due_to_schema_version"
    alias = dec_raw.get("response_quality")
    if alias is not None and str(alias) != tier:
        return "cache_miss_due_to_schema_version"
    if dec_raw.get("mandatory_fields_complete") is not True:
        return "cache_miss_due_to_schema_version"
    assessments = dec_raw.get("candidate_assessments")
    if not isinstance(assessments, list) or not assessments:
        return "cache_miss_due_to_schema_version"
    if not str(dec_raw.get("selected_candidate_hash") or ""):
        return "cache_miss_due_to_schema_version"
    if not str(dec_raw.get("assessed_execution_fingerprint") or ""):
        return "cache_miss_due_to_schema_version"
    if not str(dec_raw.get("request_execution_fingerprint") or ""):
        return "cache_miss_due_to_schema_version"
    for required in (
        "decision_state",
        "raw_allow",
        "model_raw_allow",
        "python_final_allow",
        "mql_final_allow",
        "rule_score",
        "llm_quality_score",
        "blended_legacy_score",
        "legacy_agreement_confidence",
        "llm_self_reported_confidence",
        "suggested_risk_multiplier",
        "selected_candidate_id",
        "veto_code",
        "veto_evidence_fields",
        "llm_numeric_diagnostics_authority",
        "provider_contract_version",
        "provider_mode",
        "provider_id",
        "endpoint_class",
        "endpoint_identity_hash",
        "configured_models_hash",
        "actual_model_id",
        "model_fingerprint",
        "family_profile_version",
        "retrieval_policy_version",
        "role_contract_version",
        "consensus_resolver_version",
        "generation_settings_hash",
        "input_fingerprint",
        "analyst_response_fingerprint",
        "critic_response_fingerprint",
        "final_resolver_reason",
    ):
        if required not in dec_raw or dec_raw.get(required) is None:
            if required != "mql_final_allow":
                return "cache_miss_due_to_schema_version"
    provider_identity = _provider().identity("analyst")
    if str(dec_raw.get("provider_contract_version") or "") != PROVIDER_CONTRACT_VERSION:
        return "cache_miss_due_to_schema_version"
    if current_fields is not None:
        if str(dec_raw.get("provider_mode") or "") != str(provider_identity.get("provider_mode") or ""):
            return "cache_miss_due_to_provider_identity"
        if str(dec_raw.get("provider_id") or "") != str(provider_identity.get("provider_id") or ""):
            return "cache_miss_due_to_provider_identity"
        if str(dec_raw.get("endpoint_identity_hash") or "") != str(
            provider_identity.get("endpoint_identity_hash") or ""
        ):
            return "cache_miss_due_to_provider_identity"
        if str(dec_raw.get("configured_models_hash") or "") != str(
            provider_identity.get("configured_models_hash") or ""
        ):
            return "cache_miss_due_to_provider_identity"
        allowed_models = {
            str(value)
            for value in provider_identity.get("configured_model_ids") or []
            if str(value)
        }
        if str(dec_raw.get("actual_model_id") or "") not in allowed_models:
            return "cache_miss_due_to_provider_identity"
        if str(dec_raw.get("generation_settings_hash") or "") != str(
            current_fields.get("generation_settings_hash") or ""
        ):
            return "cache_miss_due_to_provider_identity"
    if str(dec_raw.get("family_profile_version") or "") != FAMILY_PROFILE_VERSION:
        return "cache_miss_due_to_schema_version"
    if str(dec_raw.get("retrieval_policy_version") or "") != RETRIEVAL_POLICY_VERSION:
        return "cache_miss_due_to_schema_version"
    if str(dec_raw.get("role_contract_version") or "") != ROLE_CONTRACT_VERSION:
        return "cache_miss_due_to_schema_version"
    if str(dec_raw.get("consensus_resolver_version") or "") != CONSENSUS_RESOLVER_VERSION:
        return "cache_miss_due_to_schema_version"
    if "mql_final_allow" not in dec_raw or dec_raw.get("mql_final_allow") is not None:
        return "cache_miss_due_to_schema_version"
    if dec_raw.get("missing_mandatory_fields") or dec_raw.get("invalid_mandatory_fields"):
        return "cache_miss_due_to_schema_version"

    # A cache row has exactly the same authority as a fresh response, so its
    # per-candidate contract must still be complete.  Reconstruct only the
    # immutable request identities needed by the shared validator; no legacy
    # top-level defaults are allowed to repair an incomplete assessment.
    cache_candidates: list[Dict[str, Any]] = []
    seen_hashes: set[str] = set()
    selected_assessment: Dict[str, Any] | None = None
    selected_hash = str(dec_raw.get("selected_candidate_hash") or "")
    for assessment in assessments:
        if not isinstance(assessment, dict):
            return "cache_miss_due_to_schema_version"
        candidate_hash = str(assessment.get("candidate_hash") or "")
        if not candidate_hash or candidate_hash in seen_hashes:
            return "cache_miss_due_to_schema_version"
        seen_hashes.add(candidate_hash)
        candidate_validation = validate_candidate_assessment(assessment)
        if not candidate_validation.valid:
            return "cache_miss_due_to_schema_version"
        cache_candidates.append(
            {
                "candidate_index": assessment.get("candidate_index"),
                "candidate_id": assessment.get("candidate_id"),
                "candidate_hash": candidate_hash,
                "request_execution_fingerprint": assessment.get("request_execution_fingerprint"),
                "setup_taxonomy_version": assessment.get("setup_taxonomy_version"),
                "setup_taxonomy_enum": assessment.get("setup_taxonomy_enum"),
                "taxonomy_mapping_source": assessment.get("taxonomy_mapping_source"),
            }
        )
        if candidate_hash == selected_hash:
            selected_assessment = assessment

    validation_envelope = dict(dec_raw)
    validation_envelope["decision_quality_tier"] = DECISION_QUALITY_FULL_STRUCTURED
    validation_envelope["response_quality"] = DECISION_QUALITY_FULL_STRUCTURED
    envelope_validation = validate_decision_envelope(validation_envelope, cache_candidates)
    if not envelope_validation.valid or selected_assessment is None:
        return "cache_miss_due_to_schema_version"
    if str(selected_assessment.get("candidate_id") or "") != str(dec_raw.get("selected_candidate_id") or ""):
        return "cache_miss_due_to_schema_version"
    if str(selected_assessment.get("decision_state") or "").upper() != str(dec_raw.get("decision_state") or "").upper():
        return "cache_miss_due_to_schema_version"
    if str(selected_assessment.get("assessed_execution_fingerprint") or "") != str(
        dec_raw.get("assessed_execution_fingerprint") or ""
    ):
        return "cache_miss_due_to_schema_version"
    if str(selected_assessment.get("request_execution_fingerprint") or "") != str(
        dec_raw.get("request_execution_fingerprint") or ""
    ):
        return "cache_miss_due_to_schema_version"
    if str(selected_assessment.get("selected_target_identity") or "") != str(
        dec_raw.get("selected_target_identity") or ""
    ):
        return "cache_miss_due_to_schema_version"
    try:
        selected_risk = float(selected_assessment["suggested_risk_multiplier"])
        cached_risk = float(dec_raw["suggested_risk_multiplier"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return "cache_miss_due_to_schema_version"
    if not math.isfinite(selected_risk) or not math.isfinite(cached_risk) or abs(selected_risk - cached_risk) > 1.0e-12:
        return "cache_miss_due_to_schema_version"
    schema = str(dec_raw.get("target_arbitration_schema_version") or "")
    prompt_contract = str(dec_raw.get("prompt_contract_version") or "")
    if schema != AI_TARGET_ARBITRATION_SCHEMA_VERSION or prompt_contract != AI_PROMPT_CONTRACT_VERSION:
        return "cache_miss_due_to_schema_version"
    chosen = str(dec_raw.get("chosen_target_model") or "").strip().lower()
    target_arbitration = dec_raw.get("target_arbitration") if isinstance(dec_raw.get("target_arbitration"), dict) else {}
    comparison = dec_raw.get("target_comparison")
    if not isinstance(comparison, dict):
        comparison = target_arbitration.get("target_comparison") if isinstance(target_arbitration.get("target_comparison"), dict) else {}
    if chosen in {"synthetic_rr_fallback", "fallback", "synthetic"}:
        if not str(dec_raw.get("why_not_liquidity_target") or "").strip():
            return "cache_miss_due_to_schema_version"
        if not str(dec_raw.get("why_not_partial_before_obstacle") or "").strip():
            return "cache_miss_due_to_schema_version"
        if not str(dec_raw.get("why_not_capped_before_obstacle") or "").strip():
            return "cache_miss_due_to_schema_version"
        if not str(dec_raw.get("target_decision_reason") or "").strip():
            return "cache_miss_due_to_schema_version"
    required_comparison_keys = {
        "liquidity_target",
        "partial_before_obstacle_then_liquidity",
        "capped_before_obstacle",
        "synthetic_rr_fallback",
    }
    if chosen and chosen != "current_plan" and not required_comparison_keys.issubset(set(comparison.keys())):
        return "cache_miss_due_to_schema_version"
    return ""


class AIDecisionCache:
    def __init__(self, path: Path, ttl_sec: int) -> None:
        self.path = path
        self.ttl_sec = ttl_sec
        self._lock = Lock()

    def _acquire_write_lock(self, timeout_sec: float = 3.0) -> Path:
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        deadline = time.monotonic() + max(0.1, timeout_sec)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="ascii") as handle:
                    handle.write(f"{os.getpid()}|{int(time.time())}")
                    handle.flush()
                    os.fsync(handle.fileno())
                return lock_path
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > 60.0:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("ai_decision_cache_write_lock_timeout")
                time.sleep(0.025)

    @staticmethod
    def _release_write_lock(lock_path: Path | None) -> None:
        if lock_path is None:
            return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

    def lookup(
        self,
        signature: str,
        base_signature: str,
        current_fields: Mapping[str, Any] | None = None,
    ) -> tuple[Decision | None, str]:
        now = int(time.time())
        with self._lock:
            if not self.path.exists():
                return None, "miss"
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except Exception:
                return None, "miss_read_error"
            base_seen = False
            base_semantic_reason = ""
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    item = strict_json_loads(line)
                except Exception:
                    continue
                ts = int(item.get("timestamp") or 0)
                if item.get("signature") == signature:
                    cached_semantic = item.get("semantic_state")
                    if not isinstance(cached_semantic, Mapping):
                        cached_semantic = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
                    if current_fields is not None:
                        reasons = semantic_cache_invalidation_reasons(
                            semantic_cache_state(cached_semantic),
                            semantic_cache_state(current_fields),
                        )
                        if reasons:
                            return None, reasons[0]
                    if ts and now - ts > self.ttl_sec:
                        return None, "expired_ttl"
                    dec_raw = item.get("decision") if isinstance(item.get("decision"), dict) else {}
                    schema_miss = _cached_decision_schema_miss_reason(dec_raw, current_fields)
                    if schema_miss:
                        log(
                            "[ai_cache] hit=false reason=cache_miss_due_to_schema_version"
                            f" cached_schema={str(dec_raw.get('target_arbitration_schema_version') or '')}"
                            f" required_schema={AI_TARGET_ARBITRATION_SCHEMA_VERSION}"
                        )
                        return None, schema_miss
                    dec = Decision(
                        allow=bool(dec_raw.get("allow")),
                        score=float(dec_raw.get("llm_quality_score")),
                        raw_allow=bool(dec_raw.get("raw_allow", dec_raw.get("allow", True))),
                        model_raw_allow=bool(dec_raw.get("model_raw_allow", dec_raw.get("raw_allow", False))),
                        python_final_allow=bool(dec_raw.get("python_final_allow", dec_raw.get("allow", False))),
                        mql_final_allow=None,
                        chosen_index=int(dec_raw.get("chosen_index", 0)),
                        confidence=float(dec_raw.get("llm_self_reported_confidence")),
                        decision_state=str(dec_raw.get("decision_state") or DECISION_REJECT),
                        decision_quality_tier=DECISION_QUALITY_CACHE_FULL_STRUCTURED,
                        decision_schema_version=AI_DECISION_SCHEMA_VERSION,
                        mandatory_fields_complete=True,
                        missing_mandatory_fields=[],
                        invalid_mandatory_fields=[],
                        selected_candidate_id=str(dec_raw.get("selected_candidate_id") or ""),
                        selected_candidate_hash=str(dec_raw.get("selected_candidate_hash") or ""),
                        request_execution_fingerprint=str(dec_raw.get("request_execution_fingerprint") or ""),
                        assessed_execution_fingerprint=str(dec_raw.get("assessed_execution_fingerprint") or ""),
                        selected_target_identity=str(dec_raw.get("selected_target_identity") or ""),
                        selected_target_price=float(dec_raw.get("selected_target_price") or 0.0),
                        assessed_entry=float(dec_raw.get("assessed_entry") or 0.0),
                        assessed_sl=float(dec_raw.get("assessed_sl") or 0.0),
                        assessed_tp1=float(dec_raw.get("assessed_tp1") or 0.0),
                        assessed_tp2=float(dec_raw.get("assessed_tp2") or 0.0),
                        candidate_assessments=list(dec_raw.get("candidate_assessments") or []),
                        rule_score=float(dec_raw.get("rule_score")),
                        llm_quality_score=float(dec_raw.get("llm_quality_score")),
                        blended_legacy_score=float(dec_raw.get("blended_legacy_score")),
                        legacy_agreement_confidence=float(dec_raw.get("legacy_agreement_confidence")),
                        llm_self_reported_confidence=float(dec_raw.get("llm_self_reported_confidence")),
                        calibrated_win_probability=None,
                        expected_net_r=None,
                        oos_predicted_probability=None,
                        calibration_available=False,
                        reasons={
                            "decision_source": "ai_cache_hit",
                            "cached_source": dec_raw.get("decision_source", ""),
                            "cached_model_version": dec_raw.get("model_version", ""),
                            "cached_at": ts,
                            "cached_reasons": dec_raw.get("reasons", ""),
                        },
                        decision_source="ai_cache_hit_full_structured",
                        rejection_codes=list(dec_raw.get("rejection_codes") or []),
                        narrative_state=str(dec_raw.get("narrative_state") or "cached"),
                        invalidation_risks=list(dec_raw.get("invalidation_risks") or []),
                        missing_confirmations=list(dec_raw.get("missing_confirmations") or []),
                        suggested_risk_multiplier=float(dec_raw["suggested_risk_multiplier"]),
                        model_version=str(dec_raw.get("model_version") or AI_GATE_MODEL_VERSION),
                        decision_id=str(dec_raw.get("decision_id") or ""),
                        llm_quality_score_threshold=float(dec_raw.get("llm_quality_score_threshold") or 0.0),
                        llm_quality_threshold_source=str(dec_raw.get("llm_quality_threshold_source") or ""),
                        global_llm_quality_as_hard_floor=bool(dec_raw.get("global_llm_quality_as_hard_floor")),
                        llm_quality_threshold_passed=bool(dec_raw.get("llm_quality_threshold_passed", True)),
                        llm_quality_reject_reason=str(dec_raw.get("llm_quality_reject_reason") or ""),
                        structure_quality_score=float(dec_raw.get("structure_quality_score") or 0.0),
                        entry_timing_score=float(dec_raw.get("entry_timing_score") or 0.0),
                        follow_through_probability=float(dec_raw.get("follow_through_probability", 0.0)),
                        invalidation_risk=float(dec_raw.get("invalidation_risk", 1.0)),
                        chop_risk=float(dec_raw.get("chop_risk", 1.0)),
                        cost_risk=float(dec_raw.get("cost_risk", 1.0)),
                        symbol_bucket_risk=float(dec_raw.get("symbol_bucket_risk", 1.0)),
                        session_bucket_risk=float(dec_raw.get("session_bucket_risk", 1.0)),
                        post_entry_failure_risk=float(dec_raw.get("post_entry_failure_risk", 1.0)),
                        final_trade_expectancy_score=float(dec_raw.get("final_trade_expectancy_score", 0.0)),
                        veto_enabled=bool(dec_raw.get("veto_enabled", False)),
                        veto_code=str(dec_raw.get("veto_code") or ""),
                        veto_evidence_fields=list(dec_raw.get("veto_evidence_fields") or []),
                        veto_reason=str(dec_raw.get("veto_reason") or ""),
                        llm_numeric_diagnostics_authority=str(
                            dec_raw.get("llm_numeric_diagnostics_authority") or ""
                        ),
                        bucket_prior_override_justification=str(dec_raw.get("bucket_prior_override_justification") or ""),
                        target_arbitration=dec_raw.get("target_arbitration") if isinstance(dec_raw.get("target_arbitration"), dict) else {},
                        chosen_target_model=str(dec_raw.get("chosen_target_model") or ""),
                        chosen_tp1=float(dec_raw.get("chosen_tp1") or 0.0),
                        chosen_tp2=float(dec_raw.get("chosen_tp2") or 0.0),
                        chosen_rr1=float(dec_raw.get("chosen_rr1") or 0.0),
                        chosen_rr2=float(dec_raw.get("chosen_rr2") or 0.0),
                        rejected_target_models=list(dec_raw.get("rejected_target_models") or []),
                        target_blocker_kind=str(dec_raw.get("target_blocker_kind") or ""),
                        target_blocker_severity=float(dec_raw.get("target_blocker_severity", -1.0) if dec_raw.get("target_blocker_severity") is not None else -1.0),
                        target_blocker_class=str(dec_raw.get("target_blocker_class") or ""),
                        target_blocker_is_trade_killer=bool(dec_raw.get("target_blocker_is_trade_killer")),
                        target_decision_reason=str(dec_raw.get("target_decision_reason") or ""),
                        target_blocker_severity_present=bool(dec_raw.get("target_blocker_severity_present")),
                        target_blocker_class_present=bool(dec_raw.get("target_blocker_class_present")),
                        target_blocker_is_trade_killer_present=bool(dec_raw.get("target_blocker_is_trade_killer_present")),
                        target_decision_reason_present=bool(dec_raw.get("target_decision_reason_present")),
                        why_not_liquidity_target=str(dec_raw.get("why_not_liquidity_target") or ""),
                        why_not_partial_before_obstacle=str(dec_raw.get("why_not_partial_before_obstacle") or ""),
                        why_not_capped_before_obstacle=str(dec_raw.get("why_not_capped_before_obstacle") or ""),
                        why_not_synthetic_fallback=str(dec_raw.get("why_not_synthetic_fallback") or ""),
                        target_arbitration_schema_version=str(dec_raw.get("target_arbitration_schema_version") or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
                        prompt_contract_version=str(dec_raw.get("prompt_contract_version") or AI_PROMPT_CONTRACT_VERSION),
                        target_comparison_json=json.dumps(dec_raw.get("target_comparison") or {}, ensure_ascii=False, separators=(",", ":")) if isinstance(dec_raw.get("target_comparison"), dict) else str(dec_raw.get("target_comparison_json") or "{}"),
                        provider_mode=str(dec_raw.get("provider_mode") or "UNAVAILABLE"),
                        provider_id=str(dec_raw.get("provider_id") or "unavailable"),
                        endpoint_class=str(dec_raw.get("endpoint_class") or "invalid"),
                        endpoint_identity_hash=str(dec_raw.get("endpoint_identity_hash") or ""),
                        configured_models_hash=str(dec_raw.get("configured_models_hash") or ""),
                        actual_model_id=str(dec_raw.get("actual_model_id") or ""),
                        fallback_model=str(dec_raw.get("fallback_model") or ""),
                        model_fingerprint=str(dec_raw.get("model_fingerprint") or "unavailable"),
                        provider_contract_version=str(dec_raw.get("provider_contract_version") or ""),
                        evidence_envelope_version=str(dec_raw.get("evidence_envelope_version") or ""),
                        family_profile_version=str(dec_raw.get("family_profile_version") or ""),
                        memory_schema_version=str(dec_raw.get("memory_schema_version") or ""),
                        retrieval_policy_version=str(dec_raw.get("retrieval_policy_version") or ""),
                        role_contract_version=str(dec_raw.get("role_contract_version") or ""),
                        consensus_resolver_version=str(dec_raw.get("consensus_resolver_version") or ""),
                        generation_settings_hash=str(dec_raw.get("generation_settings_hash") or ""),
                        input_fingerprint=str(dec_raw.get("input_fingerprint") or ""),
                        retrieved_analogue_ids=list(dec_raw.get("retrieved_analogue_ids") or []),
                        historical_evidence_state=str(dec_raw.get("historical_evidence_state") or "INSUFFICIENT_SAMPLE"),
                        analyst_response_fingerprint=str(dec_raw.get("analyst_response_fingerprint") or ""),
                        critic_response_fingerprint=str(dec_raw.get("critic_response_fingerprint") or ""),
                        adjudicator_response_fingerprint=str(dec_raw.get("adjudicator_response_fingerprint") or ""),
                        final_resolver_reason=str(dec_raw.get("final_resolver_reason") or ""),
                        provider_health_state=str(dec_raw.get("provider_health_state") or "unavailable"),
                        role_latencies=dict(dec_raw.get("role_latencies") or {}),
                        provider_retry_counts=dict(dec_raw.get("provider_retry_counts") or {}),
                        provider_usage=dict(dec_raw.get("provider_usage") or {}),
                        estimated_context_tokens=(
                            int(dec_raw["estimated_context_tokens"])
                            if dec_raw.get("estimated_context_tokens") is not None
                            else None
                        ),
                        unsupported_generation_parameters=list(
                            dec_raw.get("unsupported_generation_parameters") or []
                        ),
                        analyst_output=dict(dec_raw.get("analyst_output") or {}),
                        critic_output=dict(dec_raw.get("critic_output") or {}),
                        adjudicator_output=dict(dec_raw.get("adjudicator_output") or {}),
                    )
                    return dec, "hit"
                if item.get("base_signature") == base_signature:
                    base_seen = True
                    if current_fields is not None and not base_semantic_reason:
                        cached_semantic = item.get("semantic_state")
                        if not isinstance(cached_semantic, Mapping):
                            cached_semantic = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
                        reasons = semantic_cache_invalidation_reasons(
                            semantic_cache_state(cached_semantic),
                            semantic_cache_state(current_fields),
                        )
                        if reasons:
                            base_semantic_reason = reasons[0]
            if base_semantic_reason:
                return None, base_semantic_reason
            return None, "invalidated_material_field_changed" if base_seen else "miss"

    def store(
        self,
        signature: str,
        base_signature: str,
        fields: Dict[str, Any],
        decision: Decision,
        *,
        request_identity_hash: str = "",
    ) -> None:
        with self._lock:
            process_lock: Path | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                process_lock = self._acquire_write_lock()
                target_comparison: Dict[str, Any] = {}
                if decision.target_comparison_json:
                    try:
                        parsed_comparison = json.loads(decision.target_comparison_json)
                        if isinstance(parsed_comparison, dict):
                            target_comparison = parsed_comparison
                    except Exception:
                        target_comparison = {}
                row = {
                    "timestamp": int(time.time()),
                    "signature": signature,
                    "base_signature": base_signature,
                    "source_request_identity_hash": str(request_identity_hash),
                    "fields": fields,
                    "semantic_state": semantic_cache_row(semantic_cache_state(fields)),
                    "decision": {
                        "request_id": str(
                            (decision.candidate_assessments or [{}])[0].get("request_id")
                            if isinstance((decision.candidate_assessments or [{}])[0], dict)
                            else ""
                        ),
                        "request_identity_hash": str(
                            request_identity_hash
                            or (
                                (decision.candidate_assessments or [{}])[0].get(
                                    "request_identity_hash"
                                )
                                if isinstance(
                                    (decision.candidate_assessments or [{}])[0],
                                    dict,
                                )
                                else ""
                            )
                        ),
                        "provider_id": str(decision.provider_id),
                        "model_id": str(
                            decision.actual_model_id or decision.model_version
                        ),
                        "role_schema_version": ROLE_CONTRACT_VERSION,
                        "candidate_count": len(decision.candidate_assessments or []),
                        "ordered_candidate_identities": [
                            {
                                "candidate_index": assessment.get("candidate_index"),
                                "candidate_id": assessment.get("candidate_id"),
                                "candidate_hash": assessment.get("candidate_hash"),
                                "request_execution_fingerprint": assessment.get(
                                    "request_execution_fingerprint"
                                ),
                            }
                            for assessment in (decision.candidate_assessments or [])
                            if isinstance(assessment, dict)
                        ],
                        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
                        "decision_quality_tier": DECISION_QUALITY_FULL_STRUCTURED,
                        "response_quality": DECISION_QUALITY_FULL_STRUCTURED,
                        "decision_state": str(decision.decision_state),
                        "mandatory_fields_complete": bool(decision.mandatory_fields_complete),
                        "missing_mandatory_fields": list(decision.missing_mandatory_fields or []),
                        "invalid_mandatory_fields": list(decision.invalid_mandatory_fields or []),
                        "selected_candidate_id": str(decision.selected_candidate_id),
                        "selected_candidate_hash": str(decision.selected_candidate_hash),
                        "request_execution_fingerprint": str(decision.request_execution_fingerprint),
                        "assessed_execution_fingerprint": str(decision.assessed_execution_fingerprint),
                        "selected_target_identity": str(decision.selected_target_identity),
                        "selected_target_price": float(decision.selected_target_price),
                        "assessed_entry": float(decision.assessed_entry),
                        "assessed_sl": float(decision.assessed_sl),
                        "assessed_tp1": float(decision.assessed_tp1),
                        "assessed_tp2": float(decision.assessed_tp2),
                        "candidate_assessments": list(decision.candidate_assessments or []),
                        "rule_score": float(decision.rule_score),
                        "llm_quality_score": float(decision.llm_quality_score),
                        "blended_legacy_score": float(decision.blended_legacy_score),
                        "legacy_agreement_confidence": float(decision.legacy_agreement_confidence),
                        "llm_self_reported_confidence": float(decision.llm_self_reported_confidence),
                        "calibrated_win_probability": None,
                        "expected_net_r": None,
                        "oos_predicted_probability": None,
                        "calibration_available": False,
                        "allow": bool(decision.allow),
                        "model_raw_allow": bool(decision.model_raw_allow),
                        "python_final_allow": bool(decision.python_final_allow),
                        "mql_final_allow": None,
                        "score": float(decision.score),
                        "raw_allow": bool(decision.raw_allow),
                        "chosen_index": int(decision.chosen_index),
                        "confidence": float(decision.confidence),
                        "reasons": _decision_reason_text(decision.reasons),
                        "decision_source": str(decision.decision_source or ""),
                        "rejection_codes": list(decision.rejection_codes or []),
                        "narrative_state": str(decision.narrative_state or ""),
                        "invalidation_risks": list(decision.invalidation_risks or []),
                        "missing_confirmations": list(decision.missing_confirmations or []),
                        "suggested_risk_multiplier": float(decision.suggested_risk_multiplier if decision.suggested_risk_multiplier is not None else 0.0),
                        "model_version": str(decision.model_version or AI_GATE_MODEL_VERSION),
                        "decision_id": str(decision.decision_id or ""),
                        "llm_quality_score_threshold": float(decision.llm_quality_score_threshold),
                        "llm_quality_threshold_source": str(decision.llm_quality_threshold_source or ""),
                        "global_llm_quality_as_hard_floor": bool(decision.global_llm_quality_as_hard_floor),
                        "llm_quality_threshold_passed": bool(decision.llm_quality_threshold_passed),
                        "llm_quality_reject_reason": str(decision.llm_quality_reject_reason or ""),
                        "structure_quality_score": float(decision.structure_quality_score),
                        "entry_timing_score": float(decision.entry_timing_score),
                        "follow_through_probability": float(decision.follow_through_probability),
                        "invalidation_risk": float(decision.invalidation_risk),
                        "chop_risk": float(decision.chop_risk),
                        "cost_risk": float(decision.cost_risk),
                        "symbol_bucket_risk": float(decision.symbol_bucket_risk),
                        "session_bucket_risk": float(decision.session_bucket_risk),
                        "post_entry_failure_risk": float(decision.post_entry_failure_risk),
                        "final_trade_expectancy_score": float(decision.final_trade_expectancy_score),
                        "veto_enabled": bool(decision.veto_enabled),
                        "veto_code": str(decision.veto_code or ""),
                        "veto_evidence_fields": list(decision.veto_evidence_fields or []),
                        "veto_reason": str(decision.veto_reason or ""),
                        "llm_numeric_diagnostics_authority": str(
                            decision.llm_numeric_diagnostics_authority
                        ),
                        "bucket_prior_override_justification": str(decision.bucket_prior_override_justification or ""),
                        "target_arbitration": decision.target_arbitration or {},
                        "chosen_target_model": str(decision.chosen_target_model or ""),
                        "chosen_tp1": float(decision.chosen_tp1 or 0.0),
                        "chosen_tp2": float(decision.chosen_tp2 or 0.0),
                        "chosen_rr1": float(decision.chosen_rr1 or 0.0),
                        "chosen_rr2": float(decision.chosen_rr2 or 0.0),
                        "rejected_target_models": list(decision.rejected_target_models or []),
                        "target_blocker_kind": str(decision.target_blocker_kind or ""),
                        "target_blocker_severity": float(decision.target_blocker_severity if decision.target_blocker_severity is not None else -1.0),
                        "target_blocker_class": str(decision.target_blocker_class or ""),
                        "target_blocker_is_trade_killer": bool(decision.target_blocker_is_trade_killer),
                        "target_decision_reason": str(decision.target_decision_reason or ""),
                        "target_blocker_severity_present": bool(decision.target_blocker_severity_present),
                        "target_blocker_class_present": bool(decision.target_blocker_class_present),
                        "target_blocker_is_trade_killer_present": bool(decision.target_blocker_is_trade_killer_present),
                        "target_decision_reason_present": bool(decision.target_decision_reason_present),
                        "why_not_liquidity_target": str(decision.why_not_liquidity_target or ""),
                        "why_not_partial_before_obstacle": str(decision.why_not_partial_before_obstacle or ""),
                        "why_not_capped_before_obstacle": str(decision.why_not_capped_before_obstacle or ""),
                        "why_not_synthetic_fallback": str(decision.why_not_synthetic_fallback or ""),
                        "target_arbitration_schema_version": str(decision.target_arbitration_schema_version or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
                        "prompt_contract_version": str(decision.prompt_contract_version or AI_PROMPT_CONTRACT_VERSION),
                        "target_comparison": target_comparison,
                        "target_comparison_json": str(decision.target_comparison_json or "{}"),
                        "provider_contract_version": str(decision.provider_contract_version),
                        "provider_mode": str(decision.provider_mode),
                        "provider_id": str(decision.provider_id),
                        "endpoint_class": str(decision.endpoint_class),
                        "endpoint_identity_hash": str(decision.endpoint_identity_hash),
                        "configured_models_hash": str(decision.configured_models_hash),
                        "actual_model_id": str(decision.actual_model_id),
                        "fallback_model": str(decision.fallback_model),
                        "model_fingerprint": str(decision.model_fingerprint),
                        "evidence_envelope_version": str(decision.evidence_envelope_version),
                        "family_profile_version": str(decision.family_profile_version),
                        "memory_schema_version": str(decision.memory_schema_version),
                        "retrieval_policy_version": str(decision.retrieval_policy_version),
                        "role_contract_version": str(decision.role_contract_version),
                        "consensus_resolver_version": str(decision.consensus_resolver_version),
                        "generation_settings_hash": str(decision.generation_settings_hash),
                        "input_fingerprint": str(decision.input_fingerprint),
                        "retrieved_analogue_ids": list(decision.retrieved_analogue_ids or []),
                        "historical_evidence_state": str(decision.historical_evidence_state),
                        "analyst_response_fingerprint": str(decision.analyst_response_fingerprint),
                        "critic_response_fingerprint": str(decision.critic_response_fingerprint),
                        "adjudicator_response_fingerprint": str(decision.adjudicator_response_fingerprint),
                        "final_resolver_reason": str(decision.final_resolver_reason),
                        "provider_health_state": str(decision.provider_health_state),
                        "role_latencies": dict(decision.role_latencies or {}),
                        "provider_retry_counts": dict(decision.provider_retry_counts or {}),
                        "provider_usage": dict(decision.provider_usage or {}),
                        "estimated_context_tokens": decision.estimated_context_tokens,
                        "unsupported_generation_parameters": list(
                            decision.unsupported_generation_parameters or []
                        ),
                        "analyst_output": dict(decision.analyst_output or {}),
                        "critic_output": dict(decision.critic_output or {}),
                        "adjudicator_output": dict(decision.adjudicator_output or {}),
                    },
                }
                row_line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                previous = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
                temp_path = self.path.with_name(
                    f".{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp"
                )
                try:
                    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(previous)
                        handle.write(row_line)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, self.path)
                finally:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass
                log(
                    "[cache_write]"
                    f" request_identity_hash={str(request_identity_hash)[:16]}"
                    f" signature={signature[:12]}"
                    f" provider={decision.provider_id}"
                    f" model={decision.actual_model_id}"
                    " quality_tier=FULL_STRUCTURED"
                )
                _inc_counter("cache_writes")
            except Exception as exc:
                log(f"[ai_gate] ai_decision_cache_store_failed error={exc}")
            finally:
                self._release_write_lock(process_lock)


AI_DECISION_CACHE = AIDecisionCache(AI_CONFIG.decision_cache_file, AI_CONFIG.decision_cache_ttl_sec)

def _live_payload_requires_runtime_inputs(payload: Dict[str, Any]) -> bool:
    return AI_CONFIG.require_runtime_inputs_live and _is_live_payload(payload)

def _missing_critical_runtime_inputs(payload: Dict[str, Any]) -> list[str]:
    runtime_inputs = _runtime_inputs(payload)
    missing = [key for key in CRITICAL_RUNTIME_INPUT_KEYS if key not in runtime_inputs]
    if not payload.get("runtime_input_hash"):
        missing.append("payload.runtime_input_hash")
    return missing


def _entry_stop_target_values(item: Dict[str, Any], payload: Dict[str, Any]) -> tuple[float, float, float, bool]:
    plan = _as_dict(payload.get("plan"))
    merged = {**plan, **item}
    entry = _floatish(_get_any(merged, ["entry_est", "entry"], 0.0), 0.0)
    sl = _floatish(_get_any(merged, ["sl", "stop_loss", "stop"], 0.0), 0.0)
    tp2 = _floatish(_get_any(merged, ["tp2", "tp", "final_tp"], 0.0), 0.0)
    is_buy = _boolish(_get_any(merged, ["is_buy"], payload.get("is_buy")), False)
    return entry, sl, tp2, is_buy


def _target_already_reached(item: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    plan = _as_dict(payload.get("plan"))
    merged = {**plan, **item}
    if _boolish(_get_any(merged, ["target_already_reached"], payload.get("target_already_reached")), False):
        return True
    entry, _sl, tp2, is_buy = _entry_stop_target_values(item, payload)
    if entry <= 0.0 or tp2 <= 0.0:
        return False
    mkt = _as_dict(payload.get("mkt"))
    meta = _as_dict(payload.get("snapshot_metadata"))
    bid = _floatish(_get_any(mkt, ["bid"], _get_any(meta, ["bid"], payload.get("bid"))), 0.0)
    ask = _floatish(_get_any(mkt, ["ask"], _get_any(meta, ["ask"], payload.get("ask"))), 0.0)
    if is_buy and bid > 0.0 and bid >= tp2:
        return True
    if (not is_buy) and ask > 0.0 and ask <= tp2:
        return True
    return False


def _payload_hard_block_reason(payload: Dict[str, Any]) -> str:
    runtime_inputs = _runtime_inputs(payload)
    po3 = _as_dict(payload.get("po3"))
    mkt = _as_dict(payload.get("mkt"))
    positions = _as_dict(payload.get("positions")) or _as_dict(payload.get("position_state"))
    risk = _as_dict(payload.get("risk")) or _as_dict(payload.get("portfolio"))

    if _boolish(runtime_inputs.get("require_displacement"), False):
        has_disp = _boolish(_get_any(po3, ["has_displacement", "has_disp"], payload.get("has_displacement")), False)
        if not has_disp:
            return "no_displacement_required"

    spread_valid = _get_any(mkt, ["spread_valid"], payload.get("spread_valid"))
    if spread_valid is not None and not _boolish(spread_valid, True):
        return "invalid_spread"
    spread_r = _get_any(mkt, ["spread_r"], payload.get("spread_r"))
    if spread_r is not None and _floatish(spread_r, 0.0) < 0.0:
        return "invalid_spread"

    if _boolish(_get_any(positions, ["max_open_positions_reached"], payload.get("max_open_positions_reached")), False):
        return "max_open_positions_reached"
    open_count = _floatish(_get_any(positions, ["open_positions", "open_positions_count"], payload.get("open_positions_count")), -1.0)
    max_count = _floatish(_get_any(positions, ["max_open_positions"], runtime_inputs.get("max_open_positions")), 0.0)
    if max_count > 0.0 and open_count >= max_count:
        return "max_open_positions_reached"
    if _boolish(_get_any(positions, ["duplicate_symbol_block", "symbol_position_blocked"], payload.get("duplicate_symbol_block")), False):
        return "duplicate_symbol_position_block"
    if _boolish(_get_any(risk, ["portfolio_risk_cap_reached", "risk_cap_reached"], payload.get("portfolio_risk_cap_reached")), False):
        return "portfolio_risk_cap_reached"
    return ""

def _candidate_family_group(item: Dict[str, Any]) -> str:
    taxonomy = classify_setup_taxonomy(item).taxonomy
    if taxonomy in {
        SetupTaxonomy.MICRO_CONTINUATION_FVG,
        SetupTaxonomy.MICRO_NESTED_CONTINUATION,
        SetupTaxonomy.FULL_PO3_CONTINUATION,
    }:
        return "continuation"
    if taxonomy in {SetupTaxonomy.MICRO_RANGE_REENTRY, SetupTaxonomy.MICRO_SESSION_REENTRY}:
        return "range"
    if taxonomy == SetupTaxonomy.FAILED_BREAKOUT_RECLAIM:
        return "failed_breakout"
    if taxonomy in {SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL, SetupTaxonomy.MICRO_BREAKER_RETEST}:
        return "edge"
    if taxonomy in {
        SetupTaxonomy.MICRO_FVG_MID_REVERSAL,
        SetupTaxonomy.MICRO_OTE_REVERSAL,
        SetupTaxonomy.FULL_PO3_REVERSAL,
    }:
        return "reversal"
    return "unknown"

def _candidate_fvg_state(item: Dict[str, Any]) -> tuple[bool, bool]:
    state_text = " ".join(
        _norm_text(item.get(key))
        for key in (
            "fvg_execution_class",
            "fvg_zone_execution_class",
            "fvg_mitigation_state",
            "mitigation_state",
        )
    )
    stale = "stale" in state_text
    touched = (
        _boolish(item.get("fvg_touched"), False)
        or _boolish(item.get("fvg_mid_mitigated"), False)
        or "touched" in state_text
        or "mid_mitigated" in state_text
    )
    return stale, touched

def _candidate_hard_block_reason(item: Dict[str, Any], payload: Dict[str, Any]) -> str:
    runtime_inputs = _runtime_inputs(payload)
    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    merged = {**plan, **item}

    if _boolish(runtime_inputs.get("trade_only_killzones"), False):
        in_killzone = _get_any(merged, ["in_killzone"], _get_any(po3, ["in_killzone"], payload.get("in_killzone")))
        if not _boolish(in_killzone, False):
            return "trade_only_killzone_block"

    family_group = _candidate_family_group(merged)
    branch = _norm_text(merged.get("entry_branch") or merged.get("entry_model"))
    family = _norm_text(merged.get("setup_family"))
    setup_class = _norm_text(merged.get("setup_class"))
    stale, touched = _candidate_fvg_state(merged)

    if not _boolish(runtime_inputs.get("enable_mpc_trading"), True):
        setup_code = _setup_code_for_item(merged, payload)
        comment = str(_get_any(merged, ["broker_comment"], payload.get("broker_comment")) or "").strip().upper()
        if setup_code == "MPC" or comment.startswith("MPC-") or _boolish(merged.get("is_mpc"), False):
            log(
                "[mpc_block]"
                f" symbol={payload.get('symbol') or merged.get('symbol') or ''}"
                f" setup_code=MPC setup_class={merged.get('setup_class') or ''}"
                f" session={_get_any(merged, ['session_code'], '')}"
                f" killzone={_get_any(merged, ['killzone_code'], '')}"
                " action=blocked_before_ai"
            )
            return "mpc_trading_disabled"

    if _boolish(runtime_inputs.get("suppress_micro_bisi_sibi_edge"), False):
        micro_bisi_sibi_family = (
            family in {"micro_bisi_sibi", "micro_bisi_sibi_edge"}
            or "micro_bisi_sibi" in family
            or setup_class in {"micro_bisi_sibi", "micro_bisi_sibi_edge"}
            or "micro_bisi_sibi" in setup_class
        )
        if micro_bisi_sibi_family:
            return "suppressed_micro_bisi_sibi_edge"
    if _boolish(runtime_inputs.get("suppress_stale_fvg_branches"), False) and stale:
        return "suppressed_stale_fvg_branch"
    if family_group == "continuation":
        if _boolish(runtime_inputs.get("suppress_continuation_stale_fvg"), False) and stale:
            return "suppressed_continuation_stale_fvg"
        if _boolish(runtime_inputs.get("suppress_continuation_touched_fvg"), False) and touched:
            return "suppressed_continuation_touched_fvg"
        if _boolish(runtime_inputs.get("suppress_touched_continuation_unless_retested"), False) and touched:
            retest = max(
                _floatish(merged.get("retest_score"), 0.0),
                _floatish(merged.get("retest_quality_score"), 0.0),
            )
            if retest < 6.0:
                return "suppressed_touched_continuation_not_retested"

    target_source = _norm_text(_get_any(merged, ["target_source"], payload.get("target_source")))
    target_model = _norm_text(_get_any(merged, ["target_model", "tp_model", "chosen_target_model"], payload.get("target_model")))
    obstacle_kind = _norm_text(_get_any(merged, ["obstacle_kind"], payload.get("obstacle_kind")))
    crossed_opposing_obstacle = "crossed" in obstacle_kind and ("opposing" in obstacle_kind or "imbalance" in obstacle_kind)
    synthetic_target = (
        "synthetic_rr_fallback" in target_source
        or "synthetic_rr_fallback" in target_model
        or target_source == "ai_selected_synthetic_rr_fallback"
    )
    if crossed_opposing_obstacle and _boolish(runtime_inputs.get("hard_reject_crossed_obstacle_target"), False):
        return "synthetic_fallback_crossed_obstacle_blocked"
    if _boolish(runtime_inputs.get("reject_synthetic_fallback_after_crossed_obstacle"), True):
        if synthetic_target and crossed_opposing_obstacle:
            if _target_arbitration_required(payload, merged) and _has_real_liquidity_target_candidate(payload, merged):
                return ""
            return "synthetic_fallback_crossed_obstacle_blocked"

    total_cost_r = (
        _floatish(_get_any(merged, ["execution_cost_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["slippage_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["commission_r"], 0.0), 0.0)
    )
    reject_cost = _floatish(runtime_inputs.get("execution_reject_cost_r"), 0.0)
    micro_cost_cap = _floatish(runtime_inputs.get("micro_scalp_max_cost_frac_of_planned_r"), 0.0)
    if reject_cost > 0.0 and total_cost_r >= reject_cost:
        return "execution_cost_r_too_high"
    if family_group in {"continuation", "edge"} and micro_cost_cap > 0.0 and total_cost_r >= micro_cost_cap:
        return "micro_scalp_cost_exceeds_10pct_r"

    entry, sl, tp2, is_buy = _entry_stop_target_values(item, payload)
    rr2 = _floatish(_get_any(merged, ["rr2"], 0.0), 0.0)
    min_live_rr2 = _floatish(runtime_inputs.get("min_live_rr2"), 0.0)
    if entry <= 0.0:
        return "no_valid_entry"
    if sl <= 0.0 or sl == entry:
        return "no_valid_stop"
    if is_buy and sl >= entry:
        return "no_valid_stop"
    if (not is_buy) and sl <= entry:
        return "no_valid_stop"
    if tp2 <= 0.0:
        return "invalid_target"
    if is_buy and tp2 <= entry:
        return "invalid_target"
    if (not is_buy) and tp2 >= entry:
        return "invalid_target"
    if rr2 <= 0.0:
        risk_dist = abs(entry - sl)
        rr2 = abs(tp2 - entry) / risk_dist if risk_dist > 0.0 else 0.0
    if rr2 <= 0.0 or (min_live_rr2 > 0.0 and rr2 < min_live_rr2):
        return "invalid_rr"
    if _target_already_reached(item, payload):
        return "target_already_reached"
    return ""

def hard_pre_gate(payload: Dict[str, Any], runtime_config: AIGateRuntimeConfig = AI_CONFIG, best_index: int = 0) -> Decision:
    _inc_counter("hard_pre_gate_checked")
    if _live_payload_requires_runtime_inputs(payload):
        missing = _missing_critical_runtime_inputs(payload)
        if missing and runtime_config.reject_on_missing_runtime_inputs_live:
            _inc_counter("hard_pre_gate_rejected")
            _inc_counter("ai_calls_skipped_by_hard_gate")
            if runtime_config.log_skipped_calls:
                log(f"[ai_gate] runtime_inputs_missing_live_reject missing={','.join(missing)}")
            return Decision(
                allow=False,
                score=0.0,
                chosen_index=best_index,
                confidence=0.0,
                reasons={
                    "decision_source": "hard_pre_gate",
                    "rejection_codes": ["runtime_inputs_missing_live_reject"],
                    "missing_runtime_inputs": missing,
                },
                decision_source="hard_pre_gate",
                rejection_codes=["runtime_inputs_missing_live_reject"],
                narrative_state="runtime_inputs_missing",
                invalidation_risks=["runtime_input_snapshot_missing"],
                missing_confirmations=missing,
                suggested_risk_multiplier=0.0,
                model_version=AI_GATE_MODEL_VERSION,
            )

    payload_reason = _payload_hard_block_reason(payload)
    if payload_reason:
        _inc_counter("hard_pre_gate_rejected")
        _inc_counter("ai_calls_skipped_by_hard_gate")
        if runtime_config.log_skipped_calls:
            log(f"[ai_gate] hard_pre_gate_rejected reason={payload_reason} skipped_openai={AI_GATE_COUNTERS.get('ai_calls_skipped_by_hard_gate', 0)}")
        return Decision(
            allow=False,
            score=0.0,
            chosen_index=best_index,
            confidence=0.0,
            reasons={
                "decision_source": "hard_pre_gate",
                "rejection_codes": [payload_reason],
            },
            decision_source="hard_pre_gate",
            rejection_codes=[payload_reason],
            narrative_state="hard_pre_gate_rejected",
            invalidation_risks=["objective_pre_trade_gate_failed"],
            missing_confirmations=[],
            suggested_risk_multiplier=0.0,
            model_version=AI_GATE_MODEL_VERSION,
        )

    cands = payload.get("candidates") or []
    items = [cand for cand in cands if isinstance(cand, dict)] if isinstance(cands, list) else []
    if not items:
        plan = _as_dict(payload.get("plan"))
        items = [plan] if plan else []
    if not items:
        return None

    reasons: list[str] = []
    for item in items:
        reason = _candidate_hard_block_reason(item, payload)
        if not reason:
            return Decision(
                allow=True,
                score=0.0,
                chosen_index=best_index,
                confidence=0.0,
                reasons={"decision_source": "hard_pre_gate_pass"},
                decision_source="hard_pre_gate_pass",
                rejection_codes=[],
                narrative_state="hard_pre_gate_pass",
                invalidation_risks=[],
                missing_confirmations=[],
                suggested_risk_multiplier=0.0,
                model_version=AI_GATE_MODEL_VERSION,
            )
        reasons.append(reason)

    code = reasons[0] if reasons else "hard_pre_gate_reject"
    _inc_counter("hard_pre_gate_rejected")
    _inc_counter("ai_calls_skipped_by_hard_gate")
    if runtime_config.log_skipped_calls:
        log(f"[ai_gate] hard_pre_gate_rejected reason={code} skipped_openai={AI_GATE_COUNTERS.get('ai_calls_skipped_by_hard_gate', 0)}")
    return Decision(
        allow=False,
        score=0.0,
        chosen_index=best_index,
        confidence=0.0,
        reasons={
            "decision_source": "hard_pre_gate",
            "rejection_codes": [code],
            "candidate_reject_reasons": reasons,
        },
        decision_source="hard_pre_gate",
        rejection_codes=[code],
        narrative_state="hard_pre_gate_rejected",
        invalidation_risks=["objective_pre_trade_gate_failed"],
        missing_confirmations=[],
        suggested_risk_multiplier=0.0,
        model_version=AI_GATE_MODEL_VERSION,
    )


def _hard_pretrade_decision(payload: Dict[str, Any], best_index: int) -> Decision | None:
    if not AI_CONFIG.hard_pre_gate_before_openai:
        return None
    decision = hard_pre_gate(payload, AI_CONFIG, best_index)
    if decision.allow and decision.decision_source == "hard_pre_gate_pass":
        return None
    return decision

def _po3_state_name(value: Any) -> str:
    state = str(value or "").strip().upper()
    if not state:
        return ""
    if state in {"CONFIRMED", "DEVELOPING", "EXPIRED", "INVALIDATED"}:
        return f"PO3_{state}"
    return state

PO3_TERMINAL_REJECT_STATES = {"PO3_INVALIDATED", "PO3_EXPIRED"}
PO3_ACTIONABLE_AUDIT_STATES = {
    "PO3_SWEEP_CONFIRMED",
    "PO3_DISPLACEMENT_CONFIRMED",
    "PO3_STRUCTURE_CONFIRMED",
    "PO3_FVG_CONFIRMED",
    "PO3_ENTRY_WAITING",
    "PO3_CONFIRMED",
    "PO3_DEVELOPING",
}

def _candidate_score(cand: Dict[str, Any]) -> float:
    """Deterministic raw-feature ordering only; legacy setup_score is excluded."""
    score = 0.0
    try:
        fvg_score = float(cand.get("fvg_score", 0.0))
        if 0.0 <= fvg_score <= 1.0:
            fvg_score *= 10.0
        score += min(10.0, max(0.0, fvg_score)) * 0.7
    except Exception:
        pass
    for key, weight in (
        ("origin_score", 0.5),
        ("cleanliness_score", 0.4),
        ("nesting_score", 0.35),
        ("htf_overlap_score", 0.35),
        ("retest_score", 0.25),
    ):
        try:
            value = min(10.0, max(0.0, float(cand.get(key, 0.0))))
            score += value * weight
        except Exception:
            pass
    return score

def _best_candidate(cands: Any) -> Tuple[Dict[str, Any], int, float]:
    best_candidate: Dict[str, Any] = {}
    best_index = 0
    best_score = -1e18
    if not isinstance(cands, list):
        return best_candidate, best_index, best_score
    for idx, cand in enumerate(cands):
        if not isinstance(cand, dict):
            continue
        score_val = _candidate_score(cand)
        if score_val > best_score:
            best_candidate = cand
            best_index = int(_get_any(cand, ["candidate_index"], idx))
            best_score = score_val
    if best_score < -1e10:
        best_score = 0.0
    return best_candidate, best_index, best_score


def _setup_family_from_payload(payload: Dict[str, Any], plan: Dict[str, Any] | None = None, cands: Any = None) -> str:
    plan = plan if isinstance(plan, dict) else _as_dict(payload.get("plan"))
    if cands is None:
        cands = payload.get("candidates") or []
    best_candidate, _, _ = _best_candidate(cands)
    po3 = _as_dict(payload.get("po3"))
    merged = {**po3, **plan, **best_candidate}
    resolution = classify_setup_taxonomy(merged)
    mapping = {
        SetupTaxonomy.MICRO_FVG_MID_REVERSAL: "micro_po3_reversal",
        SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL: "micro_bisi_sibi_edge",
        SetupTaxonomy.MICRO_BREAKER_RETEST: "micro_breaker_retest",
        SetupTaxonomy.MICRO_OTE_REVERSAL: "micro_po3_reversal",
        SetupTaxonomy.MICRO_CONTINUATION_FVG: "micro_continuation_fvg",
        SetupTaxonomy.MICRO_NESTED_CONTINUATION: "micro_nested_continuation",
        SetupTaxonomy.MICRO_RANGE_REENTRY: "micro_range_reentry",
        SetupTaxonomy.MICRO_SESSION_REENTRY: "micro_session_reentry",
        SetupTaxonomy.FAILED_BREAKOUT_RECLAIM: "micro_failed_breakout_reclaim",
        SetupTaxonomy.FULL_PO3_REVERSAL: "full_po3_reversal",
        SetupTaxonomy.FULL_PO3_CONTINUATION: "full_po3_continuation",
    }
    return mapping.get(resolution.taxonomy, "unknown_unclassified")


def _family_requires_sweep_story(setup_family: str, payload: Dict[str, Any] | None = None) -> bool:
    family = str(setup_family or "").strip().lower()
    if family in {
        "micro_continuation_fvg",
        "micro_failed_breakout_reclaim",
        "micro_range_reentry",
        "range_reentry",
        "session_reentry",
    }:
        return False
    return family in {"", "full_po3_reversal", "full_po3_continuation", "micro_po3_reversal", "micro_bisi_sibi_edge"}

def _rule_score(payload: Dict[str, Any]) -> Tuple[float, str]:
    """
    Deterministic technical-quality score (0..10). It is not probability,
    calibration, or empirical expected R and is never blended into authority.
    """
    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    fvg = _as_dict(payload.get("fvg"))
    regime = _as_dict(payload.get("regime"))
    watchlist = _as_dict(payload.get("watchlist"))
    cands = payload.get("candidates") or []
    is_buy = bool(_get_any(payload, ["is_buy"], True))

    best_candidate, _, _ = _best_candidate(cands)

    t_sweep = _get_any(po3, ["t_sweep", "sweep_time"], _get_any(payload, ["t_sweep"]))
    t_disp = _get_any(po3, ["t_disp", "t_displacement", "disp_time"], _get_any(payload, ["t_disp"]))
    t_bos = _get_any(po3, ["t_bos", "bos_time"], _get_any(payload, ["t_bos"]))
    dr_high = _get_any(po3, ["dr_high", "dr_hi"], _get_any(payload, ["dr_high", "dr_hi"]))
    dr_low = _get_any(po3, ["dr_low", "dr_lo"], _get_any(payload, ["dr_low", "dr_lo"]))
    entry_est = _get_any(best_candidate, ["entry_est"], _get_any(plan, ["entry_est", "entry"], _get_any(payload, ["entry_est", "entry"])))
    entry_model = str(_get_any(best_candidate, ["entry_model"], _get_any(plan, ["entry_model"], _get_any(payload, ["entry_model"], ""))) or "")
    setup_family = _setup_family_from_payload(payload, plan, cands)
    micro_non_reversal = setup_family in {
        "micro_failed_breakout_reclaim",
        "micro_continuation_fvg",
        "micro_range_reentry",
        "micro_bisi_sibi_edge",
    }
    continuation_family = setup_family in {"micro_continuation_fvg", "full_po3_continuation"}
    failed_breakout_family = setup_family == "micro_failed_breakout_reclaim"
    full_po3_family = setup_family in {"full_po3_reversal", "full_po3_continuation", ""}
    requires_sweep_story = _family_requires_sweep_story(setup_family, payload)
    tp_model = str(_get_any(best_candidate, ["tp_model"], _get_any(plan, ["tp_model"], _get_any(payload, ["tp_model"], ""))) or "")
    sl = _get_any(best_candidate, ["sl"], _get_any(plan, ["sl", "stop_loss", "stop"], _get_any(payload, ["sl", "stop_loss", "stop"])))
    tp2 = _get_any(best_candidate, ["tp2"], _get_any(plan, ["tp2", "tp", "final_tp"], _get_any(payload, ["tp2", "tp", "final_tp"])))
    liquidity_target = _get_any(po3, ["liquidity_target"], _get_any(payload, ["liquidity_target"]))
    liquidity_kind = str(_get_any(po3, ["liquidity_kind"], _get_any(payload, ["liquidity_kind"], "")) or "")
    liquidity_cluster_count = _get_any(po3, ["liquidity_cluster_count"], _get_any(payload, ["liquidity_cluster_count"], 0))
    liquidity_rr = _get_any(best_candidate, ["liquidity_rr"], _get_any(plan, ["liquidity_rr"], _get_any(payload, ["liquidity_rr"])))
    session_name = str(_get_any(po3, ["session_name"], _get_any(payload, ["session_name"], "")) or "")
    in_killzone = bool(_get_any(po3, ["in_killzone"], _get_any(payload, ["in_killzone"], False)))
    htf_mss = bool(_get_any(po3, ["htf_mss"], _get_any(payload, ["htf_mss"], False)))
    htf_choch = bool(_get_any(po3, ["htf_choch"], _get_any(payload, ["htf_choch"], False)))
    ltf_bos = bool(_get_any(po3, ["ltf_bos"], _get_any(payload, ["ltf_bos"], False)))
    ltf_mss = bool(_get_any(po3, ["ltf_mss"], _get_any(payload, ["ltf_mss"], False)))
    ltf_choch = bool(_get_any(po3, ["ltf_choch"], _get_any(payload, ["ltf_choch"], False)))
    po3_scope = str(_get_any(po3, ["po3_scope"], _get_any(payload, ["po3_scope"], "")) or "")

    crit = []
    if not cands:
        crit.append("candidates")
    if requires_sweep_story and not t_sweep:
        crit.append("po3.t_sweep")
    if requires_sweep_story and (dr_high in (None, 0, "") or dr_low in (None, 0, "")):
        crit.append("po3.dr_high/dr_low")

    has_disp = bool(po3.get("has_displacement") or po3.get("has_disp"))
    has_sweep = bool(po3.get("has_sweep"))
    has_bos = bool(po3.get("has_bos"))
    po3_state = _po3_state_name(po3.get("po3_state") or payload.get("po3_state"))
    fvg_state = str(fvg.get("mitigation_state") or "").lower()
    fvg_exec = str(_get_any(best_candidate, ["fvg_zone_execution_class", "fvg_execution_class"], fvg.get("execution_class") or "") or "").lower()
    if po3_state in PO3_TERMINAL_REJECT_STATES and not micro_non_reversal:
        return 0.0, f"po3_state_{po3_state.lower()}"
    if fvg_exec in {"invalidated_fvg", "fully_mitigated_fvg", "entry_invalid_fvg"}:
        return 0.0, f"fvg_not_tradable_{fvg_exec}"
    liquidity_runner_models = {
        "liquidity_target",
        "weekly_liquidity",
        "daily_liquidity",
        "equal_high_low_liquidity",
        "session_liquidity",
        "session_expansion",
        "htf_liquidity",
        "narrative_liquidity",
        "trend_projection",
    }
    recognized_entry_models = {
        "fvg_mid",
        "fvg_upper",
        "fvg_lower",
        "fvg_edge",
        "bisi_sibi_edge",
        "b50_retrace",
        "breaker_retest",
        "ote_inside_fvg",
        "nested_fvg_edge",
        "nested_htf_ltf_fvg",
        "continuation_reentry",
        "htf_nested_fvg",
        "session_fvg_mid",
        "session_reentry",
        "range_reentry",
    }
    runner_entry_models = {
        "continuation_reentry",
        "htf_nested_fvg",
        "nested_fvg_edge",
        "nested_htf_ltf_fvg",
        "ote_inside_fvg",
        "breaker_retest",
        "range_reentry",
    }
    runner_liquidity_plan = tp_model in liquidity_runner_models
    structured_impulse = has_sweep and has_disp and has_bos
    runner_context = runner_liquidity_plan and (structured_impulse or entry_model in runner_entry_models)
    if has_disp and not t_disp:
        crit.append("po3.t_disp")
    if has_bos and not t_bos:
        crit.append("po3.t_bos")

    if entry_est in (None, 0, ""):
        crit.append("plan.entry_est")
    if sl in (None, 0, ""):
        crit.append("plan.sl")
    if tp2 in (None, 0, ""):
        crit.append("plan.tp2")

    if crit:
        return 0.0, "missing_critical=" + ",".join(crit)

    score = 1.35
    notes = []
    if po3_scope:
        notes.append(po3_scope)

    # PO3 structure (smaller weights, with real penalties)
    if po3_state == "PO3_CONFIRMED":
        score += 0.55
    elif po3_state == "PO3_ENTRY_WAITING":
        score += 0.25
        notes.append("po3_entry_waiting")
    elif po3_state in {"PO3_FVG_CONFIRMED", "PO3_STRUCTURE_CONFIRMED"}:
        score += 0.05
        notes.append("po3_pre_entry")
    elif po3_state == "PO3_DEVELOPING":
        score -= 1.25
        notes.append("po3_developing")
    elif not po3_state:
        score -= 0.5
        notes.append("po3_state_missing")

    if has_sweep:
        score += 0.9
    elif failed_breakout_family:
        score += 0.35
        notes.append("failed_breakout_reclaim")
    elif continuation_family and not full_po3_family:
        notes.append("continuation_no_sweep_required")
    else:
        score -= 1.5
        notes.append("no_sweep")

    if has_disp:
        score += 0.9
    else:
        score -= 1.2
        notes.append("no_displacement")

    if has_bos:
        score += 0.7
    elif (continuation_family and not full_po3_family) or failed_breakout_family:
        notes.append("micro_structure_flow")
    else:
        score -= 0.9
        notes.append("no_bos")

    if htf_mss:
        score += 0.35
    if htf_choch:
        score += 0.25
    if ltf_bos:
        score += 0.35
    if ltf_mss:
        score += 0.2
    if ltf_choch:
        score += 0.15

    try:
        ts = int(t_sweep or 0)
        td = int(t_disp or 0)
        tb = int(t_bos or 0)
        if has_disp and td > 0:
            if requires_sweep_story and ts > 0 and td <= ts:
                score -= 1.0
                notes.append("disp_not_after_sweep")
            else:
                score += 0.25
        if has_bos and tb > 0:
            anchor = td if td > 0 else ts
            if tb <= anchor:
                score -= 1.0
                notes.append("bos_not_after_disp")
            else:
                score += 0.3
    except Exception:
        pass

    # FVG quality: up to +2.2 (not +3.0)
    fvg_score = 0.0
    try:
        if best_candidate and "fvg_score" in best_candidate:
            fvg_score = float(best_candidate.get("fvg_score", 0.0))
        else:
            fvg_score = float(fvg.get("score", 0.0))
    except Exception:
        fvg_score = 0.0

    # accept 0..1 normalized, convert to 0..10
    if 0.0 <= fvg_score <= 1.0:
        fvg_score *= 10.0

    score += max(0.0, min(1.8, (fvg_score / 10.0) * 1.8))
    if fvg_state == "fully_mitigated":
        score -= 1.2
        notes.append("fvg_fully_mitigated")
    elif fvg_state == "mid_mitigated":
        score -= 0.2
    try:
        obstruction_score = float(_get_any(best_candidate, ["opposing_obstruction_score"], fvg.get("opposing_obstruction_score", 5.0)))
        if obstruction_score < 4.5:
            score -= 0.75
            notes.append("opposing_obstruction")
    except Exception:
        pass

    for key, cap in (
        ("origin_score", 0.50),
        ("cleanliness_score", 0.42),
        ("nesting_score", 0.34),
        ("htf_overlap_score", 0.34),
        ("retest_score", 0.24),
    ):
        try:
            value = float(_get_any(best_candidate, [key], 0.0))
            score += min(cap, max(0.0, value) / 10.0 * cap)
        except Exception:
            pass

    # RR sanity (prefer 1.2..4.5, penalize extremes)
    rr2 = _get_any(best_candidate, ["effective_rr2", "rr2"], _get_any(plan, ["effective_rr2", "rr2"], _get_any(payload, ["effective_rr2", "rr2"])))
    if rr2 is None:
        try:
            entry = float(entry_est)
            stop = float(sl)
            target = float(tp2)
            r = abs(entry - stop)
            rr2 = abs(target - entry) / r if r > 0 else None
        except Exception:
            rr2 = None

    if rr2 is not None:
        try:
            rr2 = float(rr2)
            if runner_context:
                if rr2 < 0.8:
                    score -= 1.2; notes.append("rr_too_small")
                elif rr2 < 1.2:
                    score -= 0.3; notes.append("rr_small")
                elif rr2 <= 6.0:
                    score += 0.9
                elif rr2 <= 15.0:
                    score += 0.35; notes.append("rr_runner")
                elif rr2 <= 40.0:
                    score += 0.05; notes.append("rr_runner")
                else:
                    notes.append("rr_runner")
            else:
                if rr2 < 0.8:
                    score -= 1.2; notes.append("rr_too_small")
                elif rr2 < 1.2:
                    score -= 0.3; notes.append("rr_small")
                elif rr2 <= 4.5:
                    score += 0.9
                elif rr2 <= 7.5:
                    score += 0.2; notes.append("rr_high")
                elif rr2 <= 12.0:
                    score -= 0.9; notes.append("rr_very_high")
                elif rr2 <= 25.0:
                    score -= 1.6; notes.append("rr_extreme")
                else:
                    score -= 2.4; notes.append("rr_unrealistic")
        except Exception:
            pass

    try:
        cost_r = float(_get_any(best_candidate, ["execution_cost_r"], _get_any(plan, ["execution_cost_r"], 0.0)))
        slip_r = float(_get_any(best_candidate, ["slippage_r"], _get_any(plan, ["slippage_r"], 0.0)) or 0.0)
        comm_r = float(_get_any(best_candidate, ["commission_r"], _get_any(plan, ["commission_r"], 0.0)) or 0.0)
        total_cost_r = cost_r + slip_r + comm_r
        if total_cost_r >= 0.35:
            score -= 1.2
            notes.append("cost_edge_eroded")
        elif total_cost_r >= 0.22:
            score -= 0.45
            notes.append("cost_heavy")
    except Exception:
        pass

    # Regime alignment: volatility, signed slope, and trend strength should agree with trade direction.
    try:
        atr_pct = float(_get_any(regime, ["atr_pct"], _get_any(payload, ["atr_pct"], 0.0)))
        if atr_pct <= 0:
            score -= 0.25
            notes.append("atr_pct_missing")
        elif atr_pct > 6.0:
            score -= 0.35
            notes.append("atr_pct_very_high")
        else:
            score += 0.15
    except Exception:
        pass

    try:
        trend_strength = float(_get_any(regime, ["trend_strength"], _get_any(payload, ["trend_strength"], 0.0)))
        if trend_strength >= 0.75:
            score += 0.3
        elif trend_strength < 0.45:
            score -= 0.35
            notes.append("weak_trend")
    except Exception:
        pass

    try:
        trend_slope_pct = float(_get_any(regime, ["trend_slope_pct"], _get_any(payload, ["trend_slope_pct"], 0.0)))
        if is_buy:
            if trend_slope_pct > 0:
                score += 0.45
            else:
                score -= 0.9
                notes.append("trend_against_long")
        else:
            if trend_slope_pct < 0:
                score += 0.45
            else:
                score -= 0.9
                notes.append("trend_against_short")
    except Exception:
        pass

    try:
        adx_value = float(_get_any(regime, ["adx_value"], _get_any(payload, ["adx_value"], 0.0)))
        if adx_value <= 0:
            notes.append("adx_missing")
        elif adx_value < 18.0:
            score -= 0.6
            notes.append("adx_too_low")
        elif adx_value <= 45.0:
            score += 0.35
        else:
            score += 0.15
    except Exception:
        pass

    try:
        adr_pct = float(_get_any(regime, ["adr_pct"], _get_any(payload, ["adr_pct"], 0.0)))
        if adr_pct <= 0:
            notes.append("adr_missing")
        elif adr_pct < 0.002:
            score -= 0.55
            notes.append("adr_too_small")
        elif adr_pct <= 0.05:
            score += 0.2
    except Exception:
        pass

    try:
        session_vol_ratio = float(_get_any(regime, ["session_vol_ratio"], _get_any(payload, ["session_vol_ratio"], 0.0)))
        if session_name != "OFF_HOURS":
            if session_vol_ratio <= 0:
                notes.append("session_vol_missing")
            elif session_vol_ratio < 0.12:
                score -= 0.6
                notes.append("session_underdeveloped")
            elif session_vol_ratio <= 0.75:
                score += 0.3
            else:
                score += 0.1
    except Exception:
        pass

    try:
        vwap_dist_atr = float(_get_any(regime, ["vwap_dist_atr"], _get_any(payload, ["vwap_dist_atr"], 0.0)))
        if vwap_dist_atr > 0:
            if runner_context:
                if vwap_dist_atr > 8.0:
                    score -= 0.35
                    notes.append("vwap_extended_runner")
                elif vwap_dist_atr > 4.0:
                    score -= 0.1
                    notes.append("vwap_extended_runner")
                elif vwap_dist_atr <= 1.1:
                    score += 0.3
            else:
                if vwap_dist_atr > 4.0:
                    score -= 1.8
                    notes.append("too_far_from_vwap")
                elif vwap_dist_atr > 2.0:
                    score -= 1.1
                    notes.append("too_far_from_vwap")
                elif vwap_dist_atr <= 0.9:
                    score += 0.3
    except Exception:
        pass

    try:
        expansion_score = float(_get_any(regime, ["expansion_score"], _get_any(payload, ["expansion_score"], 0.0)))
        compression_score = float(_get_any(regime, ["compression_score"], _get_any(payload, ["compression_score"], 0.0)))
        if expansion_score > 0:
            if expansion_score < 0.7:
                score -= 0.55
                notes.append("compression_regime")
            elif expansion_score <= 2.4:
                score += 0.3
            else:
                score -= 0.7
                notes.append("shock_expansion")
        elif compression_score > 0.75:
            score -= 0.35
            notes.append("compressed_state")
    except Exception:
        pass

    try:
        news_risk = float(_get_any(regime, ["news_risk"], _get_any(payload, ["news_risk"], 0.0)))
        if news_risk >= 1.0:
            score -= 1.3
            notes.append("news_risk_high")
        elif news_risk > 0:
            score -= min(0.8, 0.8 * news_risk)
    except Exception:
        pass

    if in_killzone:
        score += 0.35
    elif session_name == "OFF_HOURS":
        score -= 0.45
        notes.append("off_hours")
    elif session_name:
        score += 0.1

    if entry_model == "fvg_mid":
        score += 0.15
    elif entry_model in {"fvg_upper", "fvg_lower", "fvg_edge", "bisi_sibi_edge"}:
        score += 0.05
    elif entry_model in {"continuation_reentry", "htf_nested_fvg", "nested_fvg_edge", "nested_htf_ltf_fvg", "session_fvg_mid", "session_reentry"}:
        score += 0.18
    elif entry_model in {"ote_inside_fvg", "b50_retrace", "breaker_retest", "range_reentry"}:
        score += 0.12
    elif entry_model in recognized_entry_models:
        score += 0.05
    else:
        notes.append("entry_model_unknown")

    if tp_model == "liquidity_target":
        score += 0.25
    elif tp_model in liquidity_runner_models:
        score += 0.22
    elif (
        tp_model not in {"", "fib_extension", "fixed_rr", "synthetic_rr_fallback", "target_arbitration_pending", "current_plan"}
        and not tp_model.startswith("capped_before_")
        and not tp_model.startswith("runner_downgrade_")
    ):
        notes.append("tp_model_unknown")

    if liquidity_kind in {"equal_high_cluster", "equal_low_cluster"}:
        score += 0.25
    elif liquidity_kind in {"prev_day_high", "prev_day_low", "prev_week_high", "prev_week_low"}:
        score += 0.35
    try:
        cluster_count = int(liquidity_cluster_count or 0)
        if cluster_count >= 2:
            score += min(0.35, 0.1 * cluster_count)
    except Exception:
        pass

    if liquidity_rr is None and liquidity_target not in (None, 0, ""):
        try:
            entry = float(entry_est)
            stop = float(sl)
            liq = float(liquidity_target)
            risk = abs(entry - stop)
            reward = (liq - entry) if is_buy else (entry - liq)
            liquidity_rr = (reward / risk) if risk > 0 and reward > 0 else 0.0
        except Exception:
            liquidity_rr = None

    if liquidity_rr is not None:
        try:
            liquidity_rr = float(liquidity_rr)
            if runner_context:
                if liquidity_rr <= 0.75:
                    score -= 0.7
                    notes.append("liquidity_target_weak")
                elif liquidity_rr <= 8.0:
                    score += 0.45
                elif liquidity_rr <= 20.0:
                    score += 0.2
                    notes.append("liquidity_runner")
                else:
                    score += 0.05
                    notes.append("liquidity_runner")
            else:
                if liquidity_rr <= 0.75:
                    score -= 0.7
                    notes.append("liquidity_target_weak")
                elif liquidity_rr <= 5.0:
                    score += 0.45
                elif liquidity_rr <= 8.0:
                    score += 0.15
                elif liquidity_rr <= 12.0:
                    score -= 0.15
                    notes.append("liquidity_target_stretched")
                else:
                    score -= 0.75
                    notes.append("liquidity_target_far")
        except Exception:
            pass

    if bool(watchlist.get("armed")):
        score += 0.1

    score = max(0.0, min(10.0, score))
    return score, ";".join(notes)


def _runtime_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_mode(payload: Dict[str, Any]) -> tuple[str, bool, bool, bool, float]:
    runtime = _as_dict(payload.get("runtime"))
    mode = str(runtime.get("account_trade_mode") or os.getenv("AI_GATE_ACCOUNT_MODE", "") or "").strip().lower()
    fail_closed = _runtime_bool(runtime.get("live_fail_closed_on_ai_failure"), os.getenv("AI_LIVE_FAIL_CLOSED_ON_FAILURE", "true").lower() in {"1", "true", "yes", "on"})
    allow_rule_fallback = _runtime_bool(runtime.get("allow_rule_only_fallback"), os.getenv("AI_ALLOW_RULE_ONLY_FALLBACK", "true").lower() in {"1", "true", "yes", "on"})
    allow_rule_live = _runtime_bool(runtime.get("allow_rule_only_live"), os.getenv("AI_ALLOW_RULE_ONLY_LIVE", "false").lower() in {"1", "true", "yes", "on"})
    try:
        fallback_mult = float(runtime.get("fallback_risk_multiplier", os.getenv("AI_FALLBACK_RISK_MULTIPLIER", "0.0")))
    except Exception:
        fallback_mult = 0.0
    return mode, fail_closed, allow_rule_live, allow_rule_fallback, max(0.0, min(1.0, fallback_mult))


def _threshold_identity(payload: Dict[str, Any], chosen_index: int | None = None) -> tuple[str, str, str]:
    plan = _as_dict(payload.get("plan"))
    cand: Dict[str, Any] = {}
    cands = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    if chosen_index is not None and 0 <= chosen_index < len(cands) and isinstance(cands[chosen_index], dict):
        cand = cands[chosen_index]
    merged = {**plan, **cand}
    family = _norm_text(_get_any(merged, ["setup_family"], payload.get("setup_family")))
    setup_class = _norm_text(_get_any(merged, ["setup_class"], payload.get("setup_class")))
    branch = _norm_text(_get_any(merged, ["entry_branch", "entry_model"], payload.get("entry_branch")))
    return family, setup_class, branch


def effective_llm_quality_score_threshold(payload: Dict[str, Any], chosen_index: int | None = None) -> tuple[float, str]:
    runtime = _runtime_inputs(payload)
    family, setup_class, branch = _threshold_identity(payload, chosen_index)

    fallback = _floatish(runtime.get("min_llm_quality_score_trend"), 7.0)
    threshold = fallback
    source = "min_llm_quality_score_trend"

    if family in {"full_po3", "full_po3_reversal", "full_po3_continuation"} or "full_po3" in setup_class:
        threshold = _floatish(runtime.get("llm_quality_score_full_po3"), fallback)
        source = "llm_quality_score_full_po3"
    elif (
        family in {"micro_po3", "micro_po3_reversal", "micro_bisi_sibi_edge"}
        or "micro_po3" in setup_class
        or "micro_bisi_sibi" in setup_class
    ):
        threshold = _floatish(runtime.get("llm_quality_score_micro_po3"), fallback)
        source = "llm_quality_score_micro_po3"
    elif family in {"micro_continuation_fvg", "continuation"} or branch == "continuation_reentry" or "continuation" in setup_class:
        threshold = _floatish(runtime.get("llm_quality_score_continuation"), fallback)
        source = "llm_quality_score_continuation"
    elif family in {"micro_range_reentry", "range"} or branch == "range_reentry" or "range_reentry" in setup_class:
        threshold = _floatish(runtime.get("llm_quality_score_range"), fallback)
        source = "llm_quality_score_range"
    elif family in {"micro_failed_breakout_reclaim", "failed_breakout"} or "failed_breakout" in setup_class or "reclaim" in setup_class:
        threshold = _floatish(runtime.get("llm_quality_score_failed_breakout"), fallback)
        source = "llm_quality_score_failed_breakout"

    if _boolish(runtime.get("global_llm_quality_as_hard_floor"), False):
        threshold = max(fallback, threshold)
        source = source + "+global_floor"

    return max(0.0, min(10.0, threshold)), source


def _apply_family_ai_threshold_gate(payload: Dict[str, Any], decision: Decision, chosen_index: int | None = None) -> Decision:
    threshold, source = effective_llm_quality_score_threshold(payload, chosen_index)
    family, setup_class, branch = _threshold_identity(payload, chosen_index)
    repeatability = _repeatability_authority_for(
        str(decision.model_version or AI_CONFIG.model),
        str(decision.decision_quality_tier or DECISION_QUALITY_FULL_STRUCTURED),
        provider_mode=decision.provider_mode,
        provider_id=decision.provider_id,
        model_fingerprint=decision.model_fingerprint,
        generation_settings_hash=decision.generation_settings_hash,
    )
    # Preserve configured values for longitudinal diagnostics. They are not
    # calibrated and therefore cannot independently approve or reject.
    passed = float(decision.llm_quality_score) >= threshold
    decision.llm_quality_score_threshold = threshold
    decision.llm_quality_threshold_source = source
    decision.global_llm_quality_as_hard_floor = _boolish(_runtime_inputs(payload).get("global_llm_quality_as_hard_floor"), False)
    decision.llm_quality_threshold_passed = passed
    if isinstance(decision.reasons, dict):
        decision.reasons["llm_quality_score_threshold_diagnostic"] = threshold
        decision.reasons["llm_quality_threshold_source"] = source
        decision.reasons["llm_quality_threshold_passed_diagnostic"] = passed
        decision.reasons["llm_quality_threshold_authority"] = "diagnostic_only"
    elif not decision.llm_quality_reject_reason:
        decision.reasons = {
            "previous_reasons": str(decision.reasons or ""),
            "llm_quality_score_threshold_diagnostic": threshold,
            "llm_quality_threshold_source": source,
            "llm_quality_threshold_passed_diagnostic": passed,
            "llm_quality_threshold_authority": "diagnostic_only",
        }
    log(
        f"[ai_gate] family_threshold family={family or 'unknown'} class={setup_class or 'unknown'} "
        f"branch={branch or 'unknown'} llm_quality_score={float(decision.llm_quality_score):.2f} threshold={threshold:.2f} "
        f"source={source} pass={str(passed).lower()} "
        f"repeatability_status={repeatability.get('status')} authority=diagnostic_only"
    )
    return decision


def _snapshots_required(payload: Dict[str, Any]) -> bool:
    return _runtime_bool(_as_dict(payload.get("runtime")).get("require_snapshots"), False)


def _normalize_advisory_metadata(payload: Dict[str, Any], dec: Decision) -> tuple[list[str], list[str], list[str]]:
    codes = [str(code) for code in (dec.rejection_codes or []) if str(code).strip()]
    risks = [str(risk) for risk in (dec.invalidation_risks or []) if str(risk).strip()]
    missing = [str(item) for item in (dec.missing_confirmations or []) if str(item).strip()]
    if _snapshots_required(payload):
        return codes, risks, missing

    def snapshot_only(text: str) -> bool:
        normalized = text.strip().lower().replace("-", "_").replace(" ", "_")
        return "snapshot" in normalized or "capture_failed" in normalized or "chart_capture" in normalized

    return (
        [code for code in codes if not snapshot_only(code)],
        [risk for risk in risks if not snapshot_only(risk)],
        [item for item in missing if not snapshot_only(item)],
    )


def _hard_model_rejection_codes(codes: list[str]) -> list[str]:
    # Model-emitted free-form rejection codes are diagnostics. Objective hard
    # failures are recomputed from the structured candidate in
    # _candidate_hard_block_reason; qualitative LLM rejection authority flows
    # only through the enumerated evidence-backed veto contract.
    return []

def _snapshot_integrity_rejections(payload: Dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    meta = _as_dict(payload.get("snapshot_metadata"))
    po3 = _as_dict(payload.get("po3"))
    fvg = _as_dict(payload.get("fvg"))
    codes: list[str] = []
    risks: list[str] = []
    missing: list[str] = []
    if not meta:
        return ["snapshot_metadata_missing"], ["snapshot_integrity_unknown"], ["snapshot_metadata"]

    root_symbol = str(payload.get("symbol") or "")
    if not meta.get("symbol"):
        codes.append("snapshot_symbol_missing")
        missing.append("snapshot_metadata.symbol")
    elif root_symbol and str(meta.get("symbol")) != root_symbol:
        codes.append("snapshot_wrong_symbol")
        risks.append("snapshot_symbol_mismatch")

    if meta.get("timeframe") in (None, "", 0):
        codes.append("snapshot_timeframe_missing")
        missing.append("snapshot_metadata.timeframe")
    if meta.get("server_time") in (None, "", 0):
        codes.append("snapshot_server_time_missing")
        missing.append("snapshot_metadata.server_time")
    if meta.get("candle_time") in (None, "", 0):
        codes.append("snapshot_candle_time_missing")
        missing.append("snapshot_metadata.candle_time")
    else:
        try:
            server_time = int(meta.get("server_time") or 0)
            candle_time = int(meta.get("candle_time") or 0)
            if server_time > 0 and candle_time > 0 and server_time - candle_time > 6 * 3600:
                codes.append("snapshot_stale")
                risks.append("snapshot_candle_too_old")
        except Exception:
            codes.append("snapshot_time_invalid")

    if meta.get("bid") in (None, "", 0) or meta.get("ask") in (None, "", 0):
        codes.append("snapshot_bid_ask_missing")
        missing.append("snapshot_metadata.bid_ask")
    if not meta.get("setup_id"):
        codes.append("snapshot_setup_id_missing")
        missing.append("snapshot_metadata.setup_id")
    if _po3_state_name(meta.get("po3_state") or po3.get("po3_state")) not in PO3_ACTIONABLE_AUDIT_STATES:
        codes.append("snapshot_po3_state_invalid")
        risks.append("snapshot_po3_state_not_actionable")
    for key in ("fvg_lower", "fvg_upper"):
        if meta.get(key) in (None, "", 0):
            codes.append(f"snapshot_{key}_missing")
            missing.append(f"snapshot_metadata.{key}")
    if bool(fvg.get("structure_invalidated")):
        codes.append("fvg_structure_invalidated")
        risks.append("fvg_structural_break")
    return codes, risks, missing


def _fatal_snapshot_integrity_codes(codes: list[str]) -> list[str]:
    if not codes:
        return []
    if os.getenv("AI_REQUIRE_SNAPSHOT_INTEGRITY", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return codes
    hard_fatal = {
        "snapshot_wrong_symbol",
        "fvg_structure_invalidated",
    }
    return [code for code in codes if code in hard_fatal]


_UNKNOWN_TAXONOMY_LOCK = Lock()


def _strict_taxonomy_failures(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Bind canonical taxonomy before AI; unknown candidates fail closed."""

    failures: list[Dict[str, Any]] = []
    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            failures.append({"candidate_index": index, "reason": "candidate_not_object"})
            continue
        resolution = classify_setup_taxonomy({**po3, **plan, **candidate})
        candidate["setup_taxonomy_version"] = SETUP_TAXONOMY_VERSION
        candidate["setup_taxonomy_enum"] = resolution.taxonomy.value
        candidate["taxonomy_mapping_source"] = resolution.mapping_source
        if resolution.taxonomy == SetupTaxonomy.UNKNOWN_UNCLASSIFIED:
            failures.append(
                {
                    "request_id": str(payload.get("id") or ""),
                    "symbol": str(payload.get("symbol") or candidate.get("symbol") or ""),
                    "candidate_index": int(candidate.get("candidate_index", index)),
                    "entry_branch": candidate.get("entry_branch"),
                    "setup_family": candidate.get("setup_family"),
                    "setup_class": candidate.get("setup_class"),
                    "po3_scope": candidate.get("po3_scope") or po3.get("po3_scope"),
                    "structure_state": candidate.get("structure_state") or candidate.get("structure_type"),
                    "fvg_state": candidate.get("fvg_state") or candidate.get("fvg_execution_class"),
                    "mapping_failure_reason": resolution.failure_reason,
                }
            )
    if failures:
        output = resolve_project_path("data/unknown_setup_taxonomy.jsonl")
        with _UNKNOWN_TAXONOMY_LOCK:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8", newline="\n") as handle:
                for failure in failures:
                    handle.write(json.dumps(failure, sort_keys=True, separators=(",", ":")) + "\n")
    return failures


def _provider_shadow_comparison_path() -> Path | None:
    if FILE_BUS_LIFECYCLE is None:
        return None
    return FILE_BUS_LIFECYCLE.root / "logs" / "ai_provider_shadow_comparisons.jsonl"


def _append_provider_shadow_comparison(row: Mapping[str, Any]) -> bool:
    path = _provider_shadow_comparison_path()
    comparison_id = str(row.get("comparison_id") or "")
    if path is None or not comparison_id:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with _SHADOW_COMPARISON_LOG_LOCK:
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as existing:
                    for line in existing:
                        if comparison_id not in line:
                            continue
                        try:
                            parsed = json.loads(line)
                        except Exception:
                            continue
                        if str(parsed.get("comparison_id") or "") == comparison_id:
                            return False
            except OSError:
                pass
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
            handle.flush()
    return True


def _shadow_decision_summary(decision: Decision) -> Dict[str, Any]:
    critic = dict(decision.critic_output or {})
    return {
        "provider_mode": decision.provider_mode,
        "provider_id": decision.provider_id,
        "actual_model_id": decision.actual_model_id,
        "model_fingerprint": decision.model_fingerprint,
        "generation_settings_hash": decision.generation_settings_hash,
        "decision_state": decision.decision_state,
        "python_final_allow": bool(decision.allow),
        "selected_candidate_id": decision.selected_candidate_id,
        "selected_candidate_hash": decision.selected_candidate_hash,
        "veto_code": decision.veto_code,
        "veto_evidence_fields": list(decision.veto_evidence_fields or []),
        "critic_verdict": str(critic.get("verdict") or ""),
        "critic_blocking_objections": list(critic.get("blocking_objections") or []),
        "historical_evidence_state": decision.historical_evidence_state,
        "final_resolver_reason": decision.final_resolver_reason,
        "role_latencies": dict(decision.role_latencies or {}),
        "provider_retry_counts": dict(decision.provider_retry_counts or {}),
        "schema_valid": bool(decision.mandatory_fields_complete),
    }


def _run_provider_shadow_comparison(payload: Dict[str, Any], selected_decision: Decision) -> None:
    """Compare a non-selected provider without changing live authority or memory."""

    if not AI_CONFIG.shadow_compare_providers:
        return
    workload = canonical_workload_mode(payload)
    if workload == LIVE_FORWARD:
        log("[provider_shadow_compare] skipped=true reason=live_forward_non_authoritative_only")
        return
    shadow = _shadow_provider()
    selected_identity = _provider().identity("analyst")
    shadow_identity = shadow.identity("analyst")
    request_id = str(payload.get("id") or "")
    comparison_id = canonical_hash(
        {
            "request_id": request_id,
            "selected_provider": selected_identity,
            "shadow_provider": shadow_identity,
            "input_fingerprint": selected_decision.input_fingerprint,
            "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
            "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        }
    )
    row: Dict[str, Any] = {
        "schema_version": "20260718_provider_shadow_comparison_v1",
        "comparison_id": comparison_id,
        "request_id": request_id,
        "workload_mode": workload,
        "trading_authority": False,
        "outcome_attribution": False,
        "outcome_join_candidate_hash": selected_decision.selected_candidate_hash,
        "selected": _shadow_decision_summary(selected_decision),
        "shadow_provider_identity": shadow_identity,
        "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "family_profile_version": FAMILY_PROFILE_VERSION,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
    }
    health = shadow.healthcheck(probe_structured=shadow.provider_mode == PROVIDER_MODE_LOCAL)
    if not health.healthy:
        row.update(
            {
                "shadow_status": "UNAVAILABLE",
                "malformed_output": False,
                "failure_reason": health.reason,
            }
        )
        _append_provider_shadow_comparison(row)
        log(
            "[provider_shadow_compare] completed=false trading_authority=false"
            f" reason={health.reason} request_id={request_id}"
        )
        return
    try:
        shadow_decision = _score_setup_ai(
            payload,
            provider_override=shadow,
            non_authoritative_shadow=True,
        )
        shadow_authority = _repeatability_authority_for(
            str(shadow_decision.model_version or shadow.model_for_role("analyst")),
            str(shadow_decision.decision_quality_tier or DECISION_QUALITY_FULL_STRUCTURED),
            provider_mode=shadow_decision.provider_mode,
            provider_id=shadow_decision.provider_id,
            model_fingerprint=shadow_decision.model_fingerprint,
            generation_settings_hash=shadow_decision.generation_settings_hash,
        )
        shadow_decision = _apply_repeatability_authority(
            shadow_decision,
            shadow_authority,
            payload=payload,
        )
        shadow_decision = _validate_ai_target_choice_against_feasibility(
            payload,
            shadow_decision,
            shadow_decision.chosen_index,
        )
        shadow_decision = _synchronize_selected_assessment_contract(payload, shadow_decision)
        shadow_decision = _apply_ai_veto_gate(payload, shadow_decision)
        shadow_decision = _apply_family_ai_threshold_gate(
            payload,
            shadow_decision,
            shadow_decision.chosen_index,
        )
        shadow_decision = _synchronize_candidate_authority_fields(shadow_decision)
        row.update(
            {
                "shadow_status": "COMPLETE",
                "malformed_output": False,
                "shadow": _shadow_decision_summary(shadow_decision),
                "shadow_repeatability_status": str(shadow_authority.get("status") or UNAVAILABLE),
                "decision_agreement": bool(
                    selected_decision.decision_state == shadow_decision.decision_state
                    and bool(selected_decision.allow) == bool(shadow_decision.allow)
                ),
                "candidate_agreement": bool(
                    selected_decision.selected_candidate_hash == shadow_decision.selected_candidate_hash
                ),
                "veto_agreement": bool(selected_decision.veto_code == shadow_decision.veto_code),
            }
        )
        written = _append_provider_shadow_comparison(row)
        log(
            "[provider_shadow_compare] completed=true trading_authority=false"
            f" request_id={request_id} written={str(written).lower()}"
            f" selected={selected_decision.provider_id}:{selected_decision.actual_model_id}"
            f" shadow={shadow_decision.provider_id}:{shadow_decision.actual_model_id}"
        )
    except Exception as exc:
        row.update(
            {
                "shadow_status": "FAILED_CLOSED_NON_TRADING",
                "malformed_output": isinstance(exc, (ValueError, TypeError)),
                "failure_reason": f"{type(exc).__name__}:{exc}",
            }
        )
        _append_provider_shadow_comparison(row)
        log(
            "[provider_shadow_compare] completed=false trading_authority=false"
            f" request_id={request_id} error={type(exc).__name__}"
        )


def _score_setup_impl(
    payload: Dict[str, Any],
    *,
    frozen_request: FrozenAIRequest | None = None,
) -> Decision:
    """
    Rule-based scoring blended with LLM advisory reranking/veto metadata.
    MT5 remains the final execution authority.
    """
    taxonomy_failures = _strict_taxonomy_failures(payload)
    if taxonomy_failures:
        log(
            "[setup_reject] reject_stage=taxonomy reject_reason=unknown_unclassified"
            f" count={len(taxonomy_failures)}"
        )
        return Decision(
            allow=False,
            raw_allow=False,
            score=0.0,
            confidence=0.0,
            decision_state=DECISION_REJECT,
            decision_quality_tier=DECISION_QUALITY_RULE_ONLY_NON_TRADING,
            mandatory_fields_complete=False,
            reasons={"taxonomy_failures": taxonomy_failures},
            decision_source="taxonomy_hard_gate",
            rejection_codes=["unknown_unclassified"],
            narrative_state="taxonomy_rejected_before_ai",
            invalidation_risks=["unproven_setup_taxonomy"],
            suggested_risk_multiplier=0.0,
        )
    rule_score, rule_notes = _rule_score(payload)
    cands = payload.get("candidates") or []
    best_candidate, best_index, best_composite = _best_candidate(cands)
    mandatory_prior_reject = _mandatory_live_prior_rejection(payload, best_index)
    if mandatory_prior_reject is not None:
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=mandatory_prior_reject.decision_source,
            model="",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="mandatory_prior_hard_gate",
            batch_used=False,
            flex_used=False,
            openai_called=False,
            skip_reason="mandatory_live_priors_unavailable",
        )
        return mandatory_prior_reject
    if AI_CONFIG.use_batch_api and _is_live_payload(payload):
        log("[ai_gate] batch_api_disabled_for_live")
    hard_pre_decision = _hard_pretrade_decision(payload, best_index)
    if hard_pre_decision is not None:
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=hard_pre_decision.decision_source,
            model="",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="hard_pre_gate",
            batch_used=False,
            flex_used=False,
            openai_called=False,
            skip_reason=(hard_pre_decision.rejection_codes or ["hard_pre_gate"])[0],
        )
        return hard_pre_decision
    integrity_codes, integrity_risks, integrity_missing = _snapshot_integrity_rejections(payload)
    fatal_integrity_codes = _fatal_snapshot_integrity_codes(integrity_codes)
    if fatal_integrity_codes:
        decision = Decision(
            allow=False,
            score=0.0,
            chosen_index=best_index,
            confidence=0.0,
            reasons={
                "decision_source": "snapshot_integrity",
                "rejection_codes": fatal_integrity_codes,
                "snapshot_integrity_codes": integrity_codes,
                "rule_score": rule_score,
                "rule_notes": rule_notes,
            },
            decision_source="snapshot_integrity",
            rejection_codes=fatal_integrity_codes,
            narrative_state="snapshot_rejected",
            invalidation_risks=integrity_risks,
            missing_confirmations=integrity_missing,
            suggested_risk_multiplier=0.0,
            model_version=AI_GATE_MODEL_VERSION,
        )
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=decision.decision_source,
            model="",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="snapshot_integrity",
            batch_used=False,
            flex_used=False,
            openai_called=False,
            skip_reason=";".join(fatal_integrity_codes),
        )
        return decision

    health = _refresh_provider_health(force=False)
    if _is_live_payload(payload) and not bool(health.get("healthy")):
        decision = _degraded_non_trading_decision(
            payload,
            "selected provider health or structured-output capability unavailable",
            missing=["healthy_selected_ai_provider"],
            invalid=[str(health.get("reason") or "provider_health_unavailable")],
            source="provider_health_no_trade",
        )
        decision.decision_state = DECISION_ABSTAIN
        decision.rejection_codes = ["provider_unavailable_no_trade", "degraded_ai_response_non_trading"]
        decision.suggested_risk_multiplier = 0.0
        return decision

    cache_signature = ""
    cache_base_signature = ""
    cache_fields: Dict[str, Any] = {}
    tester_workflow_source = _tester_workflow_source(payload)
    decision_cache_allowed = (
        AI_CONFIG.decision_cache_enable
        and tester_workflow_source != "live_wait_debug"
    )
    if decision_cache_allowed:
        cache_signature, cache_base_signature, cache_fields = _decision_cache_signature(payload, best_index)
        cached_decision, cache_status = AI_DECISION_CACHE.lookup(
            cache_signature,
            cache_base_signature,
            cache_fields,
        )
        if cached_decision is not None:
            cache_bound, cache_bind_reason = _bind_cached_decision_to_current_request(
                payload,
                cached_decision,
            )
            if cache_bound:
                _inc_counter("ai_cache_hit")
                log(
                    "[cache_hit]"
                    f" request_id={str(payload.get('id') or '')}"
                    f" request_identity_hash={str(payload.get('request_identity_hash') or '')[:16]}"
                    f" signature={cache_signature[:12]}"
                    f" provider={cached_decision.provider_id}"
                    f" model={cached_decision.actual_model_id}"
                    " quality_tier=CACHE_OF_FULL_STRUCTURED"
                )
                cached_decision = _validate_ai_target_choice_against_feasibility(payload, cached_decision, cached_decision.chosen_index)
                cached_decision = _synchronize_selected_assessment_contract(payload, cached_decision)
                cached_decision = _apply_repeatability_authority(cached_decision, payload=payload)
                cached_decision = _apply_ai_veto_gate(payload, cached_decision)
                cached_decision = _apply_family_ai_threshold_gate(payload, cached_decision, cached_decision.chosen_index)
                cached_decision = _synchronize_candidate_authority_fields(cached_decision)
                _write_ai_cost_report(
                    payload,
                    request_id=str(payload.get("id") or ""),
                    decision_source=cached_decision.decision_source,
                    model=cached_decision.model_version,
                    reasoning_effort=AI_CONFIG.reasoning_effort,
                    service_tier="",
                    prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
                    cache_status="hit",
                    batch_used=False,
                    flex_used=False,
                    openai_called=False,
                    skip_reason="ai_cache_hit",
                )
                return cached_decision
            cache_status = cache_bind_reason
            log(
                "[cache_miss]"
                f" request_id={str(payload.get('id') or '')}"
                f" request_identity_hash={str(payload.get('request_identity_hash') or '')[:16]}"
                f" reason={cache_bind_reason}"
                f" signature={cache_signature[:12]}"
            )
        _inc_counter("ai_cache_miss")
        if cache_status == "cache_miss_due_to_schema_version":
            _inc_counter("ai_cache_miss_due_to_schema_version")
            log(f"[ai_gate] ai_cache_miss_due_to_schema_version request_id={str(payload.get('id') or '')} signature={cache_signature[:12]}")
        elif cache_status.startswith("invalidated") or cache_status.startswith("cache_stale_"):
            log(f"[ai_gate] ai_cache_invalidated_reason={cache_status} request_id={str(payload.get('id') or '')}")
        else:
            log(f"[ai_gate] ai_cache_miss request_id={str(payload.get('id') or '')} signature={cache_signature[:12]}")

    try:
        dec = _score_setup_ai(payload, frozen_request=frozen_request)
    except Exception as e:
        category = (
            e.category
            if isinstance(e, ProviderCallError)
            else "PROVIDER_TRANSPORT_ERROR"
        )
        source = category.lower()
        rejection_code = {
            "PROVIDER_CONFIGURATION_ERROR": "provider_configuration_block",
            "PROVIDER_TRANSPORT_ERROR": "provider_transport_error",
            "STRUCTURED_SCHEMA_INVALID": "structured_schema_invalid",
            "STRUCTURED_RESPONSE_INVALID": "structured_response_invalid",
            "REQUEST_IDENTITY_MISMATCH": "request_identity_mismatch",
            "RESPONSE_STALE": "response_stale",
            "REPEATABILITY_UNAVAILABLE": "repeatability_unavailable",
        }.get(category, "provider_transport_error")
        log(
            "[provider_call_failed]"
            f" request_id={str(payload.get('id') or '')}"
            f" request_identity_hash={str(payload.get('request_identity_hash') or '')[:16]}"
            f" symbol={str(payload.get('symbol', '') or 'unknown_symbol')}"
            f" provider={_provider().provider_id}"
            f" model={_provider().model_for_role('analyst')}"
            f" error_category={category}"
            f" http_status={getattr(e, 'status_code', None) or 0}"
            f" repair_attempted={str(bool(getattr(e, 'repair_attempted', False))).lower()}"
            f" repair_result={getattr(e, 'repair_result', 'not_attempted')}"
            " final_quality_tier=DEGRADED_NON_TRADING"
        )
        decision = _degraded_non_trading_decision(
            payload,
            f"Selected AI provider unavailable: {e}",
            missing=["full_structured_ai_response"],
            source=source,
        )
        decision.decision_quality_tier = DECISION_QUALITY_DEGRADED_NON_TRADING
        decision.decision_source = source
        decision.rejection_codes = [rejection_code, "degraded_ai_response_non_trading"]
        decision.rule_score = float(rule_score)
        decision.score = 0.0
        decision.reasons = {
            "error": str(e),
            "rule_score": rule_score,
            "rule_notes": rule_notes,
            "best_candidate_score": round(best_composite, 2),
        }
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=decision.decision_source,
            model="rule_only_non_trading",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="provider_failure",
            batch_used=False,
            flex_used=False,
            openai_called=AI_CONFIG.use_remote_api is True,
            skip_reason="degraded_ai_response_non_trading",
        )
        return decision

    repeatability_authority_before_shadow = _repeatability_authority_for(
        str(dec.model_version or AI_CONFIG.model),
        str(dec.decision_quality_tier or DECISION_QUALITY_FULL_STRUCTURED),
        provider_mode=dec.provider_mode,
        provider_id=dec.provider_id,
        model_fingerprint=dec.model_fingerprint,
        generation_settings_hash=dec.generation_settings_hash,
    )
    _run_shadow_repeat_evaluation(payload, dec)
    dec = _apply_repeatability_authority(dec, repeatability_authority_before_shadow, payload=payload)

    inherited_codes, inherited_risks, inherited_missing = _normalize_advisory_metadata(payload, dec)
    for risk in integrity_risks:
        if risk not in inherited_risks:
            inherited_risks.append(risk)
    for field in integrity_missing:
        if field not in inherited_missing:
            inherited_missing.append(field)
    hard_model_codes = _hard_model_rejection_codes(inherited_codes)
    chosen_index = int(dec.chosen_index)
    selected_candidate = next(
        (
            candidate
            for candidate in cands
            if isinstance(candidate, dict)
            and int(candidate.get("candidate_index", -1)) == chosen_index
            and str(candidate.get("candidate_id") or "") == dec.selected_candidate_id
            and str(candidate.get("candidate_hash") or "") == dec.selected_candidate_hash
            and str(candidate.get("request_execution_fingerprint") or "") == dec.request_execution_fingerprint
        ),
        None,
    )
    if selected_candidate is None:
        dec.allow = False
        inherited_codes.append("candidate_hash_mismatch")
        inherited_risks.append("decision_integrity_failure")
    else:
        chosen_hard_reason = _candidate_hard_block_reason(selected_candidate, payload)
        if chosen_hard_reason:
            dec.allow = False
            if chosen_hard_reason not in inherited_codes:
                inherited_codes.append(chosen_hard_reason)
            if "objective_pre_trade_gate_failed" not in inherited_risks:
                inherited_risks.append("objective_pre_trade_gate_failed")

    if dec.decision_quality_tier not in {
        DECISION_QUALITY_FULL_STRUCTURED,
        DECISION_QUALITY_CACHE_FULL_STRUCTURED,
    }:
        dec.allow = False
        inherited_codes.append("degraded_ai_response_non_trading")
    if not dec.mandatory_fields_complete:
        dec.allow = False
        inherited_codes.append("ai_quality_schema_incomplete")
    if dec.decision_state == DECISION_ABSTAIN:
        dec.allow = False
        inherited_codes.append("ai_abstain")
    if dec.decision_state != DECISION_APPROVE:
        dec.allow = False
    if dec.suggested_risk_multiplier is None:
        dec.allow = False
        inherited_codes.append("ai_quality_schema_incomplete")
    elif float(dec.suggested_risk_multiplier) <= 0.0:
        dec.allow = False
        inherited_codes.append("resolved_risk_multiplier_zero")
    if hard_model_codes:
        dec.allow = False

    dec.rejection_codes = list(dict.fromkeys(inherited_codes))
    dec.invalidation_risks = list(dict.fromkeys(inherited_risks))
    dec.missing_confirmations = list(dict.fromkeys(inherited_missing))
    dec.score = dec.llm_quality_score
    dec.confidence = dec.llm_self_reported_confidence
    repeatability_reason = (
        dict(dec.reasons.get("repeatability_authority") or {})
        if isinstance(dec.reasons, dict)
        else {}
    )
    dec.reasons = {
        "ai_reasons": dec.reasons,
        "decision_source": dec.decision_source,
        "rule_score": dec.rule_score,
        "llm_quality_score": dec.llm_quality_score,
        "blended_legacy_score_diagnostic_only": dec.blended_legacy_score,
        "legacy_agreement_confidence_diagnostic_only": dec.legacy_agreement_confidence,
        "llm_self_reported_confidence_not_probability": dec.llm_self_reported_confidence,
        "calibration_available": False,
        "snapshot_integrity_codes": integrity_codes,
        "rejection_codes": dec.rejection_codes,
        "repeatability_authority": repeatability_reason,
    }
    final_decision = _validate_ai_target_choice_against_feasibility(payload, dec, chosen_index)
    final_decision = _synchronize_selected_assessment_contract(payload, final_decision)
    final_decision = _apply_ai_veto_gate(payload, final_decision)
    final_decision = _apply_family_ai_threshold_gate(payload, final_decision, chosen_index)
    final_decision = _synchronize_candidate_authority_fields(final_decision)
    if final_decision.decision_quality_tier == DECISION_QUALITY_FULL_STRUCTURED:
        prior_source = str(final_decision.decision_source or "")
        if prior_source not in {
            "request_identity_mismatch",
            "structured_response_invalid",
            "provider_failure",
            "repeatability_unavailable",
            "risk_gate_rejected",
        }:
            if (
                final_decision.allow
                and final_decision.decision_state == DECISION_APPROVE
            ):
                final_decision.decision_source = "ai_approved"
            elif final_decision.decision_state == DECISION_ABSTAIN:
                final_decision.decision_source = "ai_abstained"
            else:
                final_decision.decision_source = "ai_rejected"
    _run_provider_shadow_comparison(payload, final_decision)
    repeatability_blocked_live = bool(
        AI_CONFIG.require_repeatability_live
        and _is_live_payload(payload)
        and isinstance(final_decision.reasons, dict)
        and isinstance(final_decision.reasons.get("repeatability_authority"), dict)
        and final_decision.reasons["repeatability_authority"].get("status") != REPEATABLE
    )
    if decision_cache_allowed and cache_signature and not repeatability_blocked_live:
        if final_decision.decision_quality_tier == DECISION_QUALITY_FULL_STRUCTURED and final_decision.mandatory_fields_complete:
            AI_DECISION_CACHE.store(
                cache_signature,
                cache_base_signature,
                cache_fields,
                final_decision,
                request_identity_hash=str(payload.get("request_identity_hash") or ""),
            )
    elif repeatability_blocked_live:
        log(
            f"[ai_cache] store_skipped request_id={str(payload.get('id') or '')} "
            "reason=repeatability_non_authoritative_live_decision"
        )
    return final_decision


def score_setup_live(
    payload: Dict[str, Any],
    *,
    frozen_request: FrozenAIRequest | None = None,
) -> Decision:
    return _synchronize_decision_authority_fields(
        _score_setup_impl(payload, frozen_request=frozen_request)
    )


def score_setup(
    payload: Dict[str, Any],
    *,
    frozen_request: FrozenAIRequest | None = None,
) -> Decision:
    return score_setup_live(payload, frozen_request=frozen_request)


def _batch_output_dir() -> Path:
    return resolve_project_path(AI_CONFIG.batch_output_dir)


def _batch_request_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    compact = _compact_model_payload(payload)
    instructions = (
        "You are a PO3/FVG research auditor. Assess each historical candidate independently "
        "using candidate_id, candidate_hash, rule_score, llm_quality_score, "
        "llm_self_reported_confidence, decision_state, veto fields, and target identity. "
        "Do not emit ambiguous score/confidence authority and do not fabricate calibrated "
        "probability or expected R. Delayed batch results are DEGRADED_NON_TRADING research "
        "artifacts and must never trigger live execution."
    )
    request_kwargs: Dict[str, Any] = {
        "model": AI_CONFIG.model,
        "instructions": instructions,
        "input": [{
            "role": "user",
            "content": [{"type": "input_text", "text": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))}],
        }],
        "max_output_tokens": AI_CONFIG.max_output_tokens,
        "store": False,
        "truncation": "auto",
    }
    reasoning = _reasoning_config_for_model(AI_CONFIG.model)
    if reasoning:
        request_kwargs["reasoning"] = reasoning
    _apply_prompt_cache_kwargs(request_kwargs)
    _apply_service_tier_kwargs(request_kwargs, payload)
    return request_kwargs


def score_setup_batch_research(payloads: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Submit non-live research/backtest setup scoring through OpenAI Batch.

    The function writes a JSONL request file and, when the OpenAI SDK supports it,
    submits a Batch job. It deliberately returns job metadata only; delayed Batch
    outputs are never converted into immediate trade approvals.
    """
    if not AI_CONFIG.use_batch_api:
        return {"batch_used": False, "reason": "batch_api_disabled"}
    if not payloads:
        return {"batch_used": False, "reason": "empty_payloads"}
    # Live entry approval is never a Batch workload. Check this before
    # provider capability/configuration so no unrelated startup error can
    # obscure or weaken the unconditional live prohibition.
    live_ids = [str(p.get("id") or "") for p in payloads if _is_live_payload(p)]
    if live_ids:
        log("[ai_gate] batch_api_disabled_for_live")
        return {"batch_used": False, "reason": "batch_api_disabled_for_live", "live_request_ids": live_ids}
    if _provider().provider_mode != PROVIDER_MODE_REMOTE:
        return {
            "batch_used": False,
            "reason": "batch_api_not_supported_by_selected_provider",
            "provider_mode": _provider().provider_mode,
            "cross_provider_fallback": False,
        }
    allowed_modes = {"backtest", "replay", "research", "analytics"}
    disallowed = [str(p.get("id") or "") for p in payloads if _payload_workload_mode(p) not in allowed_modes]
    if disallowed:
        return {"batch_used": False, "reason": "batch_api_requires_non_live_mode", "request_ids": disallowed}
    if len(payloads) > AI_CONFIG.batch_max_pending:
        return {"batch_used": False, "reason": "batch_max_pending_exceeded", "count": len(payloads)}

    out_dir = _batch_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    jsonl_path = out_dir / f"batch_requests_{stamp}.jsonl"
    rows = []
    for payload in payloads:
        req_id = str(payload.get("id") or f"payload_{len(rows)}")
        rows.append({
            "custom_id": req_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": _batch_request_body(payload),
        })
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    manifest: Dict[str, Any] = {
        "batch_used": True,
        "status": "jsonl_written",
        "jsonl_path": str(jsonl_path),
        "request_count": len(rows),
        "created_at": stamp,
        "batch_id": "",
        "output_file_id": "",
        "error": "",
    }
    try:
        client = _remote_batch_client(AI_CONFIG.openai_timeout_sec)
        with jsonl_path.open("rb") as batch_file:
            upload = client.files.create(file=batch_file, purpose="batch")
        batch = client.batches.create(
            input_file_id=upload.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"source": "po3_ai_gate_research", "created_by": "score_setup_batch_research"},
        )
        manifest["status"] = str(getattr(batch, "status", "submitted") or "submitted")
        manifest["batch_id"] = str(getattr(batch, "id", "") or "")
        manifest["input_file_id"] = str(getattr(upload, "id", "") or "")
    except Exception as exc:
        manifest["status"] = "jsonl_written_submit_failed"
        manifest["error"] = str(exc)
        log(f"[ai_gate] batch_submit_failed error={exc}")
    manifest_path = out_dir / f"batch_manifest_{stamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
# ---------- File bus helpers ----------

def atomic_write_json(path: Path, obj: Dict[str, Any], encoding: str = "utf-8") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding=encoding)
    tmp.replace(path)


def write_debug_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, obj, encoding="utf-8")


def _try_move_to_stale(file_path: Path, stale_dir: Path) -> tuple[bool, str]:
    stale_dir.mkdir(parents=True, exist_ok=True)
    dst = stale_dir / file_path.name
    try:
        shutil.move(str(file_path), str(dst))
        return True, "moved"
    except FileNotFoundError:
        return False, "missing"
    except PermissionError:
        return False, "permission_denied"
    except Exception as e:
        try:
            shutil.copy2(str(file_path), str(dst))
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass
            return True, "copied"
        except FileNotFoundError:
            return False, "missing"
        except PermissionError:
            return False, "permission_denied"
        except Exception as inner:
            return False, str(inner if inner else e)


def move_to_stale(file_path: Path, stale_dir: Path) -> None:
    ok, reason = _try_move_to_stale(file_path, stale_dir)
    if not ok and reason not in {"missing", "permission_denied"}:
        raise RuntimeError(f"move_to_stale_failed:{reason}")


def _job_int(job: Dict[str, Any], key: str, default: int) -> int:
    try:
        value = job.get(key, default)
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _job_float(job: Dict[str, Any], key: str, default: float) -> float:
    try:
        value = job.get(key, default)
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _env_int_any(names: List[str], default: int) -> int:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            try:
                return int(float(raw))
            except Exception:
                return default
    return default


def _env_float_any(names: List[str], default: float) -> float:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            try:
                return float(raw)
            except Exception:
                return default
    return default


def _policy_job_int(job: Dict[str, Any], key: str, env_names: List[str], default: int) -> int:
    env_value = _env_int_any(env_names, default)
    if any(os.getenv(name, "").strip() for name in env_names):
        return env_value
    return _job_int(job, key, default)


def _policy_job_float(job: Dict[str, Any], key: str, env_names: List[str], default: float) -> float:
    env_value = _env_float_any(env_names, default)
    if any(os.getenv(name, "").strip() for name in env_names):
        return env_value
    return _job_float(job, key, default)


def process_analytics_job(job_path: Path, bus: Path, stale_dir: Path) -> None:
    if run_analytics_suite is None:
        raise RuntimeError(f"analytics_import_failed:{ANALYTICS_IMPORT_ERROR}")
    job = read_json_any_encoding(job_path)
    auto_activate = _runtime_bool(job.get("auto_activate"), ANALYTICS_AUTO_ACTIVATE)
    policy_shadow_mode = _runtime_bool(
        job.get("policy_shadow_mode"),
        os.getenv("POLICY_SHADOW_MODE", "true").strip().lower() in {"1", "true", "yes", "on"},
    )
    policy_config = {
        "auto_activate": auto_activate,
        "policy_shadow_mode": policy_shadow_mode,
        "policy_min_total_closed_trades": max(1, _policy_job_int(job, "policy_min_total_closed_trades", ["EXPECTANCY_MIN_TRADES_BEFORE_ACTION", "POLICY_MIN_TOTAL_CLOSED_TRADES"], 200)),
        "policy_min_bucket_trades": max(1, _policy_job_int(job, "policy_min_bucket_trades", ["EXPECTANCY_MIN_BUCKET_TRADES_BEFORE_ACTION", "POLICY_MIN_BUCKET_TRADES"], 50)),
        "policy_min_profit_factor_after_costs": _policy_job_float(job, "policy_min_profit_factor_after_costs", ["POLICY_MIN_PROFIT_FACTOR_AFTER_COSTS"], 1.15),
        "policy_max_train_test_gap_r": _policy_job_float(job, "policy_max_train_test_gap_r", ["POLICY_MAX_TRAIN_TEST_GAP_R"], 0.25),
        "policy_min_positive_fold_rate": _policy_job_float(job, "policy_min_positive_fold_rate", ["POLICY_MIN_POSITIVE_FOLD_RATE"], 0.60),
        "policy_max_drawdown_r": _policy_job_float(job, "policy_max_drawdown_r", ["POLICY_MAX_DRAWDOWN_R"], 12.0),
    }
    suite = run_analytics_suite(
        logs_dir=bus / "logs" / "trade_results",
        analytics_root=bus / "logs" / "analytics",
        policy_root=bus / "logs" / "policies",
        auto_activate=policy_config["auto_activate"],
        min_bucket_samples=policy_config["policy_min_bucket_trades"],
        min_subtype_samples=policy_config["policy_min_bucket_trades"],
        evidence_min_trades=policy_config["policy_min_total_closed_trades"],
        policy_shadow_mode=policy_config["policy_shadow_mode"],
        policy_min_bucket_trades=policy_config["policy_min_bucket_trades"],
        policy_min_profit_factor_after_costs=policy_config["policy_min_profit_factor_after_costs"],
        policy_max_train_test_gap_r=policy_config["policy_max_train_test_gap_r"],
        policy_min_positive_fold_rate=policy_config["policy_min_positive_fold_rate"],
        policy_max_drawdown_r=policy_config["policy_max_drawdown_r"],
    )
    job_result = {
        "job": job,
        "policy_config": policy_config,
        "processed_at": int(time.time()),
        "governance": suite.get("governance", {}),
        "active_policy": suite.get("active_policy", {}),
        "activation": suite.get("activation"),
        "snapshot_path": suite.get("snapshot_path", ""),
    }
    write_debug_json(bus / "logs" / "analytics" / "last_job.json", job_result)
    move_to_stale(job_path, stale_dir / "analytics_jobs")


def _claim_lock_path(lock_dir: Path, req_path: Path) -> Path:
    return lock_dir / f"{req_path.name}.lock"


def _acquire_request_claim(lock_dir: Path, req_path: Path) -> Path | None:
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _claim_lock_path(lock_dir, req_path)
    now = time.time()

    for attempt in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "process_id": os.getpid(),
                            "hostname": socket.gethostname(),
                            "worker_id": (
                                FILE_BUS_LIFECYCLE.session_id
                                if FILE_BUS_LIFECYCLE is not None
                                else f"python_{os.getpid()}"
                            ),
                            "claimed_at": now,
                            "heartbeat_at": now,
                            "expected_max_provider_duration_sec": (
                                EXPECTED_MAX_PROVIDER_DURATION_SEC
                            ),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            return lock_path
        except FileExistsError:
            try:
                stale = False
                if not req_path.exists():
                    stale = True
                else:
                    age = now - lock_path.stat().st_mtime
                    stale = age > REQUEST_LOCK_STALE_SEC
                if stale:
                    lock_path.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            return None
    return None


def _release_request_claim(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass

def _normalize_request_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload.get("0"), dict) and any(k in payload["0"] for k in ("plan", "po3", "candidates", "mkt")):
        inner = payload["0"]
        merged = dict(payload)
        merged.pop("0", None)
        merged.update(inner)
        return merged
    return payload

def _stable_input_status(path: Path) -> tuple[bool, str]:
    lowered = path.name.lower()
    if lowered.endswith((".tmp", ".partial", ".lock")):
        return False, "partial_extension"
    try:
        stat1 = path.stat()
        size1 = stat1.st_size
        if size1 <= 0:
            return False, "empty"
        time.sleep(REQUEST_STABLE_MS / 1000.0)
        stat2 = path.stat()
        if (
            size1 != stat2.st_size
            or stat1.st_mtime_ns != stat2.st_mtime_ns
            or stat2.st_size <= 0
        ):
            return False, "producer_write_in_progress"
        read_json_any_encoding(path)
        return True, "stable_complete_json"
    except FileNotFoundError:
        return False, "missing"
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return False, f"invalid_json:{type(exc).__name__}"
    except Exception as exc:
        return False, f"stability_check_error:{type(exc).__name__}"


def _is_stable_input_file(path: Path) -> bool:
    stable, _ = _stable_input_status(path)
    return stable


def _quarantine_unstable_input_if_terminal(path: Path, reason: str) -> bool:
    key = str(path.resolve())
    now = time.time()
    state = _UNSTABLE_FILE_STATE.setdefault(
        key,
        {"first_seen": now, "attempts": 0.0},
    )
    state["attempts"] += 1.0
    if now - state["first_seen"] < PRODUCER_LOCK_TIMEOUT_SEC:
        return False
    moved, move_reason = _archive_request_terminal(
        path,
        "quarantined",
        f"producer_input_never_stabilized:{reason}",
    )
    log(
        "[response_quarantined]"
        f" request_file={path.name}"
        f" reason=producer_input_never_stabilized"
        f" detail={reason}"
        f" attempts={int(state['attempts'])}"
        f" moved={str(moved).lower()}"
        f" move_reason={move_reason}"
        " provider_call=false"
    )
    if moved:
        _UNSTABLE_FILE_STATE.pop(key, None)
    return moved


def _handle_claim_deferred(
    req_path: Path,
    exc: FileBusClaimDeferredError,
) -> bool:
    key = str(req_path.resolve())
    now = time.time()
    state = _CLAIM_DEFERRED_STATE.setdefault(
        key,
        {"first_seen": now, "attempts": 0.0},
    )
    state["attempts"] += 1.0
    elapsed = now - state["first_seen"]
    log(
        f"[file_bus] claim_deferred request={req_path.name}"
        f" reason=producer_file_lock retries={exc.attempts}"
        f" attempts_total={int(state['attempts'])}"
        f" elapsed_sec={elapsed:.3f}"
    )
    if elapsed < PRODUCER_LOCK_TIMEOUT_SEC:
        return False
    moved, move_reason = _archive_request_terminal(
        req_path,
        "quarantined",
        "producer_file_lock_timeout_no_provider_call",
    )
    log(
        "[response_quarantined]"
        f" request_file={req_path.name}"
        " reason=producer_file_lock_timeout"
        f" moved={str(moved).lower()}"
        f" move_reason={move_reason}"
        " provider_call=false"
    )
    if moved:
        _CLAIM_DEFERRED_STATE.pop(key, None)
    return moved

def _write_error_response(req_id: str, resp_dir: Path, reason: str, payload_summary: Dict[str, Any] | None = None) -> None:
    resp_dir.mkdir(parents=True, exist_ok=True)
    reason_text = _ascii_compact(f"bridge_error={reason}")
    resp: Dict[str, Any] = {
        "id": req_id,
        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "decision_quality_tier": DECISION_QUALITY_DEGRADED_NON_TRADING,
        "response_quality": DECISION_QUALITY_DEGRADED_NON_TRADING,
        "decision_state": DECISION_REJECT,
        "mandatory_fields_complete": False,
        "missing_mandatory_fields": ["full_structured_ai_response"],
        "invalid_mandatory_fields": [],
        "selected_candidate_id": "",
        "selected_candidate_hash": "",
        "assessed_execution_fingerprint": "",
        "candidate_assessments": [],
        "allow": False,
        "raw_allow": False,
        "rule_score": 0.0,
        "llm_quality_score": 0.0,
        "blended_legacy_score": 0.0,
        "legacy_agreement_confidence": 0.0,
        "llm_self_reported_confidence": 0.0,
        "calibrated_win_probability": None,
        "expected_net_r": None,
        "oos_predicted_probability": None,
        "calibration_available": False,
        "score": 0.0,
        "chosen_index": 0,
        "confidence": 0.0,
        "decision_source": "bridge_error",
        "reasons": reason_text,
        "decision_id": f"{req_id}:bridge_error:{int(time.time())}",
        "rejection_codes": ["bridge_error"],
        "narrative_state": "bridge_error",
        "invalidation_risks": ["ai_bridge_error"],
        "missing_confirmations": ["ai_structured_audit"],
        "suggested_risk_multiplier": 0.0,
        "model_version": AI_GATE_MODEL_VERSION,
    }
    atomic_write_json(resp_dir / f"{req_id}.json", resp, encoding=RESP_ENCODING)
    debug_obj: Dict[str, Any] = {
        "id": req_id,
        "response": resp,
        "payload_summary": payload_summary or {},
    }
    write_debug_json(resp_dir.parent / "response_debug" / f"{req_id}.json", debug_obj)

def _tester_workflow_source(payload: Dict[str, Any]) -> str:
    runtime_inputs = _runtime_inputs(payload)
    raw_mode = _get_any(runtime_inputs, ["tester_ai_mode", "inp_tester_ai_mode"], payload.get("tester_ai_mode"))
    mode_name = _norm_text(_get_any(runtime_inputs, ["tester_ai_mode_name", "tester_mode_name"], payload.get("tester_ai_mode_name")))
    try:
        mode = int(float(raw_mode))
    except Exception:
        mode = -1
    if mode == 0 or mode_name == "tester_ai_record_only":
        return "record_only"
    if mode == 1 or mode_name == "tester_ai_cache_only":
        return "cache_only"
    if mode == 2 or mode_name == "tester_ai_live_wait_debug":
        return "live_wait_debug"
    return "tester"

def _tester_cache_identity(payload: Dict[str, Any]) -> tuple[str, str, str]:
    signature = str(payload.get("tester_cache_signature") or "").strip()
    key = str(payload.get("tester_cache_key") or "").strip()
    if not signature:
        return "", "", "missing_tester_cache_signature"
    if not key:
        return "", "", "missing_tester_cache_key"
    safe_key = "".join(ch for ch in key if ch.isalnum() or ch in {"_", "-"})
    if safe_key != key or not safe_key:
        return "", "", "invalid_tester_cache_key"
    return signature, safe_key, ""

def _mql_tester_cache_skip_reason(resp: Dict[str, Any]) -> str:
    if str(resp.get("request_identity_version") or "") != AI_REQUEST_IDENTITY_VERSION:
        return "legacy_request_identity"
    request_id = str(resp.get("id") or "")
    request_identity_hash = str(resp.get("request_identity_hash") or "")
    if not request_id:
        return "missing_request_id"
    if not request_identity_hash:
        return "missing_request_identity_hash"
    ordered_identities = resp.get("ordered_candidate_identities")
    assessments = resp.get("candidate_assessments")
    if not isinstance(ordered_identities, list) or not ordered_identities:
        return "missing_ordered_candidate_identities"
    try:
        candidate_count = int(resp.get("candidate_count"))
    except (TypeError, ValueError):
        return "invalid_candidate_count"
    if candidate_count != len(ordered_identities):
        return "candidate_count_identity_mismatch"
    if str(resp.get("decision_schema_version") or "") != AI_DECISION_SCHEMA_VERSION:
        return "legacy_decision_schema"
    if str(resp.get("target_arbitration_schema_version") or "") != AI_TARGET_ARBITRATION_SCHEMA_VERSION:
        return "legacy_target_arbitration_schema"
    if str(resp.get("prompt_contract_version") or "") != AI_PROMPT_CONTRACT_VERSION:
        return "legacy_prompt_contract"
    if str(resp.get("provider_contract_version") or "") != PROVIDER_CONTRACT_VERSION:
        return "legacy_provider_contract"
    if str(resp.get("evidence_envelope_version") or "") != EVIDENCE_ENVELOPE_VERSION:
        return "legacy_evidence_envelope"
    if str(resp.get("family_profile_version") or "") != FAMILY_PROFILE_VERSION:
        return "legacy_family_profile"
    if str(resp.get("memory_schema_version") or "") != TRADE_MEMORY_SCHEMA_VERSION:
        return "legacy_memory_schema"
    if str(resp.get("retrieval_policy_version") or "") != RETRIEVAL_POLICY_VERSION:
        return "legacy_retrieval_policy"
    if str(resp.get("role_contract_version") or "") != ROLE_CONTRACT_VERSION:
        return "legacy_role_contract"
    if str(resp.get("consensus_resolver_version") or "") != CONSENSUS_RESOLVER_VERSION:
        return "legacy_consensus_resolver"
    if str(resp.get("provider_mode") or "") not in {PROVIDER_MODE_REMOTE, PROVIDER_MODE_LOCAL}:
        return "invalid_provider_mode"
    for field_name in (
        "provider_id",
        "endpoint_identity_hash",
        "configured_models_hash",
        "actual_model_id",
        "model_fingerprint",
        "generation_settings_hash",
        "input_fingerprint",
        "analyst_response_fingerprint",
        "critic_response_fingerprint",
    ):
        if not str(resp.get(field_name) or "").strip():
            return f"missing_{field_name}"
    tier = str(resp.get("decision_quality_tier") or "")
    if tier not in {
        DECISION_QUALITY_FULL_STRUCTURED,
        DECISION_QUALITY_CACHE_FULL_STRUCTURED,
    }:
        return "degraded_decision_quality_tier"
    if resp.get("response_quality") is not None and str(resp.get("response_quality")) != tier:
        return "decision_quality_alias_conflict"
    if resp.get("mandatory_fields_complete") is not True:
        return "incomplete_mandatory_fields"
    if not isinstance(assessments, list) or not assessments:
        return "missing_candidate_assessments"
    if len(assessments) != candidate_count:
        return "candidate_assessment_count_mismatch"
    seen_hashes: set[str] = set()
    for index, assessment in enumerate(assessments):
        if not isinstance(assessment, dict):
            return "invalid_candidate_assessment"
        validation = validate_candidate_assessment(assessment)
        if not validation.valid:
            return "invalid_candidate_assessment"
        if str(assessment.get("request_id") or "") != request_id:
            return "candidate_request_id_mismatch"
        if str(assessment.get("request_identity_hash") or "") != request_identity_hash:
            return "candidate_request_identity_mismatch"
        if str(assessment.get("provider_id") or "") != str(resp.get("provider_id") or ""):
            return "candidate_provider_identity_mismatch"
        if str(assessment.get("model_id") or "") != str(resp.get("actual_model_id") or ""):
            return "candidate_model_identity_mismatch"
        if str(assessment.get("role_schema_version") or "") != ROLE_CONTRACT_VERSION:
            return "candidate_role_schema_mismatch"
        identity = ordered_identities[index]
        if not isinstance(identity, dict):
            return "invalid_ordered_candidate_identity"
        for identity_field in (
            "candidate_index",
            "candidate_id",
            "candidate_hash",
            "request_execution_fingerprint",
        ):
            if assessment.get(identity_field) != identity.get(identity_field):
                return f"ordered_candidate_identity_mismatch_{identity_field}"
        assessment_hash = str(assessment.get("candidate_hash") or "")
        if not assessment_hash or assessment_hash in seen_hashes:
            return "duplicate_candidate_assessment"
        seen_hashes.add(assessment_hash)
    if str(resp.get("selected_candidate_hash") or "") not in seen_hashes:
        return "selected_candidate_not_assessed"
    source = str(resp.get("decision_source") or "").lower()
    codes = {str(code).lower() for code in resp.get("rejection_codes") or []}
    if source == "bridge_error" or "bridge_error" in codes:
        return "bridge_error"
    skip_tokens = (
        "ai_failure",
        "transport",
        "timeout",
        "malformed",
        "invalid_json",
        "parse_error",
        "parser_error",
        "placeholder",
        "unavailable",
    )
    for token in skip_tokens:
        if token in source or token in codes:
            return token
    if "invalid_ai_target_arbitration_response" in codes:
        return "invalid_schema_decision"
    return ""

def _mql_tester_cache_contract_current(resp: Dict[str, Any]) -> bool:
    return (
        str(resp.get("decision_schema_version") or "") == AI_DECISION_SCHEMA_VERSION
        and str(resp.get("decision_quality_tier") or "") in {
            DECISION_QUALITY_FULL_STRUCTURED,
            DECISION_QUALITY_CACHE_FULL_STRUCTURED,
        }
        and resp.get("mandatory_fields_complete") is True
        and str(resp.get("target_arbitration_schema_version") or "") == AI_TARGET_ARBITRATION_SCHEMA_VERSION
        and str(resp.get("prompt_contract_version") or "") == AI_PROMPT_CONTRACT_VERSION
        and str(resp.get("provider_contract_version") or "") == PROVIDER_CONTRACT_VERSION
        and str(resp.get("evidence_envelope_version") or "") == EVIDENCE_ENVELOPE_VERSION
        and str(resp.get("family_profile_version") or "") == FAMILY_PROFILE_VERSION
        and str(resp.get("memory_schema_version") or "") == TRADE_MEMORY_SCHEMA_VERSION
        and str(resp.get("retrieval_policy_version") or "") == RETRIEVAL_POLICY_VERSION
        and str(resp.get("role_contract_version") or "") == ROLE_CONTRACT_VERSION
        and str(resp.get("consensus_resolver_version") or "") == CONSENSUS_RESOLVER_VERSION
    )

def _mql_tester_replay_cache_path(payload: Dict[str, Any], bus: Path) -> tuple[Path | None, str, str, str]:
    signature, key, identity_error = _tester_cache_identity(payload)
    if identity_error:
        return None, signature, key, identity_error
    return bus / "logs" / "tester_ai_cache" / f"{key}.json", signature, key, ""

def _export_mql_tester_replay_cache(payload: Dict[str, Any], resp: Dict[str, Any], bus: Path) -> str:
    # This is the MQL Strategy Tester replay cache. It is intentionally separate
    # from Python's AI_DECISION_CACHE_FILE signature cache; MQL provides the key.
    workflow_source = _tester_workflow_source(payload)
    if workflow_source == "live_wait_debug":
        log(
            "[tester_cache_export] skipped"
            " reason=live_wait_debug_not_replay_authoritative"
            f" request_id={str(payload.get('id') or '')}"
        )
        return "skipped:live_wait_debug_not_replay_authoritative"
    cache_path, signature, key, identity_error = _mql_tester_replay_cache_path(payload, bus)
    if identity_error:
        log(f"[tester_cache_export] skipped reason={identity_error}")
        return f"skipped:{identity_error}"
    skip_reason = _mql_tester_cache_skip_reason(resp)
    if skip_reason:
        log(f"[tester_cache_export] skipped reason={skip_reason} key={key} signature={signature}")
        return f"skipped:{skip_reason}"
    assert cache_path is not None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_obj = dict(resp)
    cache_obj["id"] = str(resp.get("id") or key)
    cache_obj["cache_signature"] = signature
    cache_obj["decision_quality_tier"] = DECISION_QUALITY_CACHE_FULL_STRUCTURED
    cache_obj["response_quality"] = DECISION_QUALITY_CACHE_FULL_STRUCTURED
    # The quality tier is part of both bindings. Recompute after promotion to a
    # replay-cache record; retaining the live-response hashes would make the
    # cache internally inconsistent and MQL must reject it.
    cache_obj["response_binding_hash"] = response_binding_hash(cache_obj)
    cache_obj.pop("response_fingerprint_contract", None)
    cache_contract = response_fingerprint(cache_obj)
    cache_obj["response_fingerprint"] = cache_contract["response_fingerprint"]
    cache_obj["full_structured_response_hash"] = cache_contract["full_structured_response_hash"]
    cache_obj["response_fingerprint_contract"] = cache_contract
    if cache_path.exists():
        try:
            existing = read_json_any_encoding(cache_path)
            if (
                existing.get("cache_signature") == signature
                and not _mql_tester_cache_skip_reason(existing)
                and _mql_tester_cache_contract_current(existing)
                and str(existing.get("request_identity_hash") or "") == str(cache_obj.get("request_identity_hash") or "")
                and str(existing.get("provider_mode") or "") == str(cache_obj.get("provider_mode") or "")
                and str(existing.get("provider_id") or "") == str(cache_obj.get("provider_id") or "")
                and str(existing.get("model_fingerprint") or "") == str(cache_obj.get("model_fingerprint") or "")
                and str(existing.get("generation_settings_hash") or "") == str(cache_obj.get("generation_settings_hash") or "")
            ):
                log(f"[tester_cache_export] existing=true key={key} signature={signature}")
                return "existing"
            quarantine_dir = cache_path.parent / "quarantined"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            quarantine_path = quarantine_dir / (
                f"{cache_path.stem}__collision_{int(time.time() * 1000)}.json"
            )
            os.replace(cache_path, quarantine_path)
            log(
                "[cache_quarantine]"
                " reason=cache_identity_collision"
                f" key={key} signature={signature}"
                f" old_request_identity={str(existing.get('request_identity_hash') or '')[:16]}"
                f" new_request_identity={str(cache_obj.get('request_identity_hash') or '')[:16]}"
                f" quarantine={quarantine_path.name}"
            )
            return "skipped:cache_identity_collision"
        except Exception as exc:
            log(
                "[tester_cache_export] skipped"
                " reason=cache_existing_read_or_quarantine_failed"
                f" key={key} signature={signature} error={type(exc).__name__}"
            )
            return "skipped:cache_existing_read_or_quarantine_failed"
    atomic_write_json(cache_path, cache_obj, encoding=RESP_ENCODING)
    log(f"[tester_cache_export] written=true key={key} signature={signature} source={workflow_source}")
    return "written"

def _repair_mql_tester_replay_cache_from_existing_response(req_path: Path, resp_path: Path, bus: Path) -> str:
    try:
        payload = _normalize_request_payload(read_json_any_encoding(req_path))
    except Exception as exc:
        log(f"[tester_cache_export] skipped reason=request_read_error request={req_path.name} error={exc}")
        return "skipped:request_read_error"
    cache_path, signature, key, identity_error = _mql_tester_replay_cache_path(payload, bus)
    if identity_error:
        log(f"[tester_cache_export] skipped reason={identity_error} request={req_path.name}")
        return f"skipped:{identity_error}"
    assert cache_path is not None
    try:
        resp = read_json_any_encoding(resp_path)
    except Exception as exc:
        log(f"[tester_cache_export] skipped reason=response_read_error key={key} signature={signature} error={exc}")
        return "skipped:response_read_error"
    status = _export_mql_tester_replay_cache(payload, resp, bus)
    if status == "written":
        log(f"[tester_cache_export] repaired_from_existing_response=true key={key} signature={signature} response={resp_path.name}")
    return status


def process_one(
    req_path: Path,
    resp_dir: Path,
    stale_dir: Path,
    *,
    lock_path: Path | None = None,
    idempotency_ledger: RequestIdempotencyLedger | None = None,
) -> None:
    _inc_counter("requests_processed")
    log(f"[ai_gate] Processing {req_path.name}")

    last_err: Exception | None = None
    payload = None
    for _ in range(6):
        try:
            payload = read_json_any_encoding(req_path)
            last_err = None
            break
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.05)

    if payload is None:
        raise last_err if last_err else RuntimeError("Unreadable request file")

    payload = _normalize_request_payload(payload)

    req_id = payload.get("id") or req_path.stem
    missing_transport = [
        field
        for field in ("session_id", "request_nonce")
        if not str(payload.get(field) or "").strip()
    ]
    if missing_transport:
        raise ValueError("file_bus_transport_contract_missing:" + ",".join(missing_transport))

    prior_artifact = _load_live_bucket_priors()
    provider_identity = _provider().generation_identity(
        "analyst",
        _provider_request_metadata(payload),
    )
    schema_preflight = strict_structured_schema(ModelAIGateOutput)
    if not schema_preflight.valid:
        raise ValueError(
            "structured_schema_preflight_failed:"
            + "|".join(schema_preflight.errors)
        )
    frozen_request = freeze_ai_request(
        payload,
        provider_identity=provider_identity,
        schema_fingerprint=schema_preflight.schema_fingerprint,
        family_profile_version=FAMILY_PROFILE_VERSION,
        retrieval_policy_version=RETRIEVAL_POLICY_VERSION,
        candidate_cap=12,
    )
    payload = frozen_request.thaw_payload()
    _attach_frozen_identity(payload, frozen_request)
    request_identity = frozen_request.identity
    log(
        "[request_created]"
        f" request_id={req_id}"
        f" request_identity_hash={request_identity['request_identity_hash'][:16]}"
        f" symbol={payload.get('symbol', '')}"
        f" provider={provider_identity.get('provider_id', '')}"
        f" model={provider_identity.get('model_id', '')}"
        f" schema_fingerprint={schema_preflight.schema_fingerprint[:16]}"
        f" identity_schema_version={AI_REQUEST_IDENTITY_VERSION}"
        f" canonicalization_version={AI_IDENTITY_CANONICALIZATION_VERSION}"
    )
    ledger = idempotency_ledger or REQUEST_IDEMPOTENCY_LEDGER
    lifecycle_disposition = None
    if ledger is not None:
        lifecycle_disposition = ledger.begin(
            request_id=str(req_id),
            request_identity_hash=frozen_request.request_identity_hash,
            provider_id=str(provider_identity.get("provider_id") or ""),
            model_id=str(provider_identity.get("model_id") or ""),
            prompt_contract_version=AI_PROMPT_CONTRACT_VERSION,
            schema_fingerprint=schema_preflight.schema_fingerprint,
            response_path=resp_dir / f"{req_id}.json",
            stale_after_sec=REQUEST_LOCK_STALE_SEC,
        )
        if lifecycle_disposition.action == "REUSE":
            assert lifecycle_disposition.response is not None
            atomic_write_json(
                resp_dir / f"{req_id}.json",
                lifecycle_disposition.response,
                encoding=RESP_ENCODING,
            )
            _export_mql_tester_replay_cache(
                payload,
                lifecycle_disposition.response,
                resp_dir.parent,
            )
            log(
                "[idempotency_hit]"
                f" request_id={req_id}"
                f" request_identity_hash={frozen_request.request_identity_hash[:16]}"
                f" reason={lifecycle_disposition.reason}"
                " provider_call=false"
            )
            moved, _ = _archive_request_terminal(
                req_path,
                "completed",
                "idempotent_response_reused",
            )
            ledger.transition(
                str(req_id),
                "ARCHIVED" if moved else "COMPLETED",
                extra={"archive_reason": "idempotent_response_reused"},
            )
            return
        if lifecycle_disposition.action == "COLLISION":
            log(
                "[duplicate_request_blocked]"
                f" request_id={req_id}"
                f" request_identity_hash={frozen_request.request_identity_hash[:16]}"
                f" reason={lifecycle_disposition.reason}"
            )
            raise ValueError(lifecycle_disposition.reason)
        if lifecycle_disposition.action == "ACTIVE":
            log(
                "[duplicate_request_blocked]"
                f" request_id={req_id}"
                f" request_identity_hash={frozen_request.request_identity_hash[:16]}"
                " reason=duplicate_request_already_running"
            )
            raise ValueError("duplicate_request_already_running")
        log(
            "[request_claimed]"
            f" request_id={req_id}"
            f" request_identity_hash={frozen_request.request_identity_hash[:16]}"
            f" worker_id={ledger.worker_id}"
            f" lifecycle_version={REQUEST_LIFECYCLE_VERSION}"
        )
    request_contract = request_fingerprint(
        payload,
        model=str(provider_identity.get("model_id") or AI_CONFIG.model),
        reasoning_effort=AI_CONFIG.reasoning_effort,
        decision_quality_tier="FULL_STRUCTURED_REQUIRED",
        prompt_contract_version=AI_PROMPT_CONTRACT_VERSION,
        decision_schema_version=AI_DECISION_SCHEMA_VERSION,
        target_schema_version=AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        prior_artifact_hash=str(prior_artifact.get("artifact_hash") or ""),
        prior_artifact_version=str(prior_artifact.get("prior_version") or HIERARCHICAL_PRIOR_SCHEMA_VERSION),
        provider_mode=str(provider_identity.get("provider_mode") or "UNAVAILABLE"),
        provider_id=str(provider_identity.get("provider_id") or "unavailable"),
        model_fingerprint=str(provider_identity.get("model_fingerprint") or "unavailable"),
        family_profile_version=FAMILY_PROFILE_VERSION,
        retrieval_policy_version=RETRIEVAL_POLICY_VERSION,
        generation_settings_hash=str(provider_identity.get("generation_settings_hash") or ""),
    )
    payload["request_fingerprint"] = request_contract["request_fingerprint"]
    payload["request_fingerprint_contract"] = request_contract
    payload["hierarchical_prior_artifact_hash"] = request_contract["prior_artifact_hash"]
    payload["hierarchical_prior_schema_version"] = HIERARCHICAL_PRIOR_SCHEMA_VERSION

    frozen_request.assert_unchanged(payload)
    if ledger is not None and lock_path is not None:
        with RequestHeartbeat(
            ledger=ledger,
            request_id=str(req_id),
            lock_path=lock_path,
            expected_max_provider_duration_sec=(
                EXPECTED_MAX_PROVIDER_DURATION_SEC
            ),
        ):
            log(
                "[provider_call_started]"
                f" request_id={req_id}"
                f" request_identity_hash={frozen_request.request_identity_hash[:16]}"
                f" provider={provider_identity.get('provider_id', '')}"
                f" model={provider_identity.get('model_id', '')}"
            )
            dec = score_setup(payload, frozen_request=frozen_request)
    else:
        dec = score_setup(payload, frozen_request=frozen_request)
    frozen_request.assert_unchanged(payload)

    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    regime = _as_dict(payload.get("regime"))
    snaps = _as_dict(payload.get("snapshots"))
    cands = payload.get("candidates") or []
    setup_family = _setup_family_from_payload(payload, plan, cands)
    requires_sweep_story = _family_requires_sweep_story(setup_family, payload)
    source_story = _as_dict(payload.get("story")) or _as_dict(payload.get("source_story"))

    t_sweep = (
        _get_any(po3, ["t_sweep", "sweep_time"])
        or _get_any(payload, ["t_sweep"])
        or _get_any(source_story, ["t_sweep", "source_t_sweep"])
        or _get_any(payload, ["source_t_sweep"])
    )
    t_disp = _get_any(po3, ["t_disp", "t_displacement", "disp_time"]) or _get_any(payload, ["t_disp"])
    t_bos = _get_any(po3, ["t_bos", "bos_time"]) or _get_any(payload, ["t_bos"])
    dr_high = (
        _get_any(po3, ["dr_high", "dr_hi"])
        or _get_any(payload, ["dr_high", "dr_hi"])
        or _get_any(source_story, ["dr_high", "source_dr_high"])
        or _get_any(payload, ["source_dr_high"])
    )
    dr_low = (
        _get_any(po3, ["dr_low", "dr_lo"])
        or _get_any(payload, ["dr_low", "dr_lo"])
        or _get_any(source_story, ["dr_low", "source_dr_low"])
        or _get_any(payload, ["source_dr_low"])
    )
    session_name = _get_any(po3, ["session_name"]) or _get_any(payload, ["session_name"])
    in_killzone = _get_any(po3, ["in_killzone"]) or _get_any(payload, ["in_killzone"])
    liquidity_target = _get_any(po3, ["liquidity_target"]) or _get_any(payload, ["liquidity_target"])

    entry_est = _get_any(plan, ["entry_est", "entry"]) or _get_any(payload, ["entry_est", "entry"])
    sl = _get_any(plan, ["sl", "stop_loss", "stop"]) or _get_any(payload, ["sl", "stop_loss", "stop"])
    tp2 = _get_any(plan, ["tp2", "tp", "final_tp"]) or _get_any(payload, ["tp2", "tp", "final_tp"])

    missing_fields = []
    if requires_sweep_story and not t_sweep:
        missing_fields.append("po3.t_sweep")
    if (po3.get("has_displacement") or po3.get("has_disp")) and not t_disp:
        missing_fields.append("po3.t_disp")
    if po3.get("has_bos") and not t_bos:
        missing_fields.append("po3.t_bos")
    if requires_sweep_story and (dr_high in (None, 0) or dr_low in (None, 0)):
        missing_fields.append("po3.dr_high/dr_low")
    if entry_est in (None, 0) or sl in (None, 0) or tp2 in (None, 0):
        missing_fields.append("plan.entry_est/sl/tp2")
    if not isinstance(cands, list) or len(cands) == 0:
        missing_fields.append("candidates[]")
    if missing_fields:
        dec.allow = False
        if dec.rejection_codes is None:
            dec.rejection_codes = []
        for field in missing_fields:
            code = "missing_" + "".join(ch if ch.isalnum() else "_" for ch in field.lower()).strip("_")
            if code not in dec.rejection_codes:
                dec.rejection_codes.append(code)
        if dec.missing_confirmations is None:
            dec.missing_confirmations = []
        for field in missing_fields:
            if field not in dec.missing_confirmations:
                dec.missing_confirmations.append(field)

    def _snapshot_exists(path_str: Any) -> bool:
        try:
            return bool(path_str) and Path(str(path_str)).exists()
        except Exception:
            return False

    snapshot_summary = {
        "htf_path": snaps.get("htf_path", ""),
        "ltf_path": snaps.get("ltf_path", ""),
        "htf_exists": _snapshot_exists(snaps.get("htf_path")),
        "ltf_exists": _snapshot_exists(snaps.get("ltf_path")),
    }

    payload_summary = {
        "keys": sorted(list(payload.keys())),
        "runtime_input_hash": payload.get("runtime_input_hash"),
        "runtime_inputs": _runtime_inputs(payload),
        "po3": {
            "has_sweep": po3.get("has_sweep"),
            "po3_scope": po3.get("po3_scope"),
            "setup_family": setup_family,
            "requires_sweep_story": requires_sweep_story,
            "t_sweep": t_sweep,
            "t_disp": t_disp,
            "t_bos": t_bos,
            "dr_high": dr_high,
            "dr_low": dr_low,
            "session_name": session_name,
            "in_killzone": in_killzone,
            "liquidity_target": liquidity_target,
        },
        "source_story": source_story or {
            key: payload.get(key)
            for key in (
                "source_t_sweep",
                "source_t_disp",
                "source_t_bos",
                "source_context_tier",
                "source_sweep_side",
                "source_liquidity_target",
                "source_liquidity_kind",
            )
            if key in payload
        },
        "plan": {
            "entry_est": entry_est,
            "entry_model": plan.get("entry_model"),
            "sl": sl,
            "tp1": plan.get("tp1"),
            "tp2": tp2,
            "rr2": plan.get("rr2"),
            "liquidity_rr": plan.get("liquidity_rr"),
            "diagnostic_legacy_setup_score": plan.get("setup_score"),
        },
        "target_candidates": _compact_target_candidates(_target_candidates(payload, plan)),
        "regime": {
            "atr_pct": _get_any(regime, ["atr_pct"], _get_any(payload, ["atr_pct"])),
            "trend_strength": _get_any(regime, ["trend_strength"], _get_any(payload, ["trend_strength"])),
            "trend_slope_pct": _get_any(regime, ["trend_slope_pct"], _get_any(payload, ["trend_slope_pct"])),
            "adx_value": _get_any(regime, ["adx_value"], _get_any(payload, ["adx_value"])),
            "adr_pct": _get_any(regime, ["adr_pct"], _get_any(payload, ["adr_pct"])),
            "session_vol_ratio": _get_any(regime, ["session_vol_ratio"], _get_any(payload, ["session_vol_ratio"])),
            "vwap_dist_atr": _get_any(regime, ["vwap_dist_atr"], _get_any(payload, ["vwap_dist_atr"])),
            "compression_score": _get_any(regime, ["compression_score"], _get_any(payload, ["compression_score"])),
            "expansion_score": _get_any(regime, ["expansion_score"], _get_any(payload, ["expansion_score"])),
            "news_risk": _get_any(regime, ["news_risk"], _get_any(payload, ["news_risk"])),
        },
        "snapshots": snapshot_summary,
        "cand_count": len(cands) if isinstance(cands, list) else 0,
        "candidate_indexes": [
            cand.get("candidate_index")
            for cand in cands[: min(5, len(cands))]
            if isinstance(cand, dict)
        ] if isinstance(cands, list) else [],
    }

    reason_text = _decision_reason_text(dec.reasons)
    resolved_risk = dec.suggested_risk_multiplier
    decision_repeatability = {}
    if isinstance(dec.reasons, dict) and isinstance(dec.reasons.get("repeatability_authority"), dict):
        decision_repeatability = dict(dec.reasons["repeatability_authority"])
    if not decision_repeatability:
        decision_repeatability = _repeatability_authority_for(
            str(dec.model_version or AI_CONFIG.model),
            str(dec.decision_quality_tier or DECISION_QUALITY_FULL_STRUCTURED),
            provider_mode=dec.provider_mode,
            provider_id=dec.provider_id,
            model_fingerprint=dec.model_fingerprint,
            generation_settings_hash=dec.generation_settings_hash,
        )
    trade_authorized_by_contract = response_can_trade(
        dec.decision_quality_tier,
        dec.decision_state,
        resolved_risk,
    ) and dec.mandatory_fields_complete
    dec = _synchronize_decision_authority_fields(dec)
    python_final_allow = bool(dec.python_final_allow and trade_authorized_by_contract and not missing_fields)
    dec.python_final_allow = python_final_allow
    dec.allow = python_final_allow
    forward_contract = live_forward_behavior_contract()
    resp = {
        "id": req_id,
        "session_id": str(payload.get("session_id") or ""),
        "request_nonce": str(payload.get("request_nonce") or ""),
        "request_identity_version": AI_REQUEST_IDENTITY_VERSION,
        "identity_schema_version": AI_REQUEST_IDENTITY_VERSION,
        "canonicalization_version": AI_IDENTITY_CANONICALIZATION_VERSION,
        "request_identity_hash": str(payload.get("request_identity_hash") or ""),
        "request_created_sim_time": int(payload.get("request_created_sim_time") or 0),
        "request_created_wall_time": int(payload.get("request_created_wall_time") or 0),
        "candidate_count": int(payload.get("candidate_count") or len(cands)),
        "ordered_candidate_identities": list(
            payload.get("ordered_candidate_identities") or []
        ),
        # Preserve the exact canonical tester mode in the transport envelope;
        # _payload_workload_mode intentionally collapses tester modes only for
        # OpenAI service-tier/batch routing.
        "workload_mode": canonical_workload_mode(payload),
        "live_forward_contract_version": LIVE_FORWARD_CONTRACT_VERSION,
        "behavior_contract_hash": forward_contract["behavior_contract_hash"],
        "architecture_contract_version": ARCHITECTURE_CONTRACT_VERSION,
        "semantic_cache_schema_version": SEMANTIC_CACHE_SCHEMA_VERSION,
        "cohort_schema_version": COHORT_SCHEMA_VERSION,
        "hierarchical_outcome_model_version": HIERARCHICAL_OUTCOME_MODEL_VERSION,
        "response_id": str(dec.decision_id or f"{req_id}:{dec.decision_source or 'decision'}"),
        "provider_contract_version": str(dec.provider_contract_version or PROVIDER_CONTRACT_VERSION),
        "provider_mode": str(dec.provider_mode or provider_identity.get("provider_mode") or "UNAVAILABLE"),
        "provider_id": str(dec.provider_id or provider_identity.get("provider_id") or "unavailable"),
        "endpoint_class": str(dec.endpoint_class or provider_identity.get("endpoint_class") or "invalid"),
        "endpoint_identity_hash": str(
            dec.endpoint_identity_hash or provider_identity.get("endpoint_identity_hash") or ""
        ),
        "configured_models_hash": str(
            dec.configured_models_hash or provider_identity.get("configured_models_hash") or ""
        ),
        "model_returned": str(dec.actual_model_id or dec.model_version or AI_CONFIG.model),
        "actual_model_id": str(dec.actual_model_id or dec.model_version or AI_CONFIG.model),
        "fallback_model": str(dec.fallback_model or ""),
        "model_fingerprint": str(dec.model_fingerprint or "unavailable"),
        "reasoning_configuration": (
            str(AI_CONFIG.reasoning_effort)
            if dec.provider_mode == PROVIDER_MODE_REMOTE
            else (
                f"temperature={AI_CONFIG.local_temperature:.4f};top_p={AI_CONFIG.local_top_p:.4f};"
                f"seed={AI_CONFIG.local_seed};thinking={str(AI_CONFIG.local_enable_thinking).lower()}"
            )
        ),
        "evidence_envelope_version": str(dec.evidence_envelope_version or EVIDENCE_ENVELOPE_VERSION),
        "family_profile_version": str(dec.family_profile_version or FAMILY_PROFILE_VERSION),
        "memory_schema_version": str(dec.memory_schema_version or TRADE_MEMORY_SCHEMA_VERSION),
        "retrieval_policy_version": str(dec.retrieval_policy_version or RETRIEVAL_POLICY_VERSION),
        "role_contract_version": str(dec.role_contract_version or ROLE_CONTRACT_VERSION),
        "consensus_resolver_version": str(dec.consensus_resolver_version or CONSENSUS_RESOLVER_VERSION),
        "generation_settings_hash": str(dec.generation_settings_hash or provider_identity.get("generation_settings_hash") or ""),
        "input_fingerprint": str(dec.input_fingerprint or ""),
        "retrieved_analogue_ids": list(dec.retrieved_analogue_ids or []),
        "historical_evidence_state": str(dec.historical_evidence_state or "INSUFFICIENT_SAMPLE"),
        "analyst_response_fingerprint": str(dec.analyst_response_fingerprint or ""),
        "critic_response_fingerprint": str(dec.critic_response_fingerprint or ""),
        "adjudicator_response_fingerprint": str(dec.adjudicator_response_fingerprint or ""),
        "final_resolver_reason": str(dec.final_resolver_reason or ""),
        "provider_health_state": str(dec.provider_health_state or "unavailable"),
        "role_latencies": dict(dec.role_latencies or {}),
        "provider_retry_counts": dict(dec.provider_retry_counts or {}),
        "provider_usage": dict(dec.provider_usage or {}),
        "estimated_context_tokens": dec.estimated_context_tokens,
        "unsupported_generation_parameters": list(
            dec.unsupported_generation_parameters or []
        ),
        "analyst_output": dict(dec.analyst_output or {}),
        "critic_output": dict(dec.critic_output or {}),
        "adjudicator_output": dict(dec.adjudicator_output or {}),
        "bucket_prior_hash": request_contract["prior_artifact_hash"],
        "calibration_artifact_id": "",
        "request_fingerprint": request_contract["request_fingerprint"],
        "request_fingerprint_contract": request_contract,
        "hierarchical_prior_artifact_hash": request_contract["prior_artifact_hash"],
        "hierarchical_prior_schema_version": HIERARCHICAL_PRIOR_SCHEMA_VERSION,
        "repeatability_schema_version": REPEATABILITY_SCHEMA_VERSION,
        "repeatability_status": str(decision_repeatability.get("status") or UNAVAILABLE),
        "repeatability_required_live": bool(
            AI_CONFIG.require_repeatability_live and _is_live_payload(payload)
        ),
        "repeatability_artifact_state": str(
            decision_repeatability.get("artifact_state") or "unknown"
        ),
        "repeatability_rejection_code": str(decision_repeatability.get("reason") or ""),
        "repeatability_score_threshold_authority": bool(
            decision_repeatability.get("score_threshold_authority", False)
        ),
        "repeatability_trading_eligible": bool(decision_repeatability.get("trading_eligible", False)),
        "repeatability_group_key": str(decision_repeatability.get("group_key") or ""),
        "repeatability_authority_hash": str(decision_repeatability.get("artifact_hash") or ""),
        "decision_schema_version": str(dec.decision_schema_version or AI_DECISION_SCHEMA_VERSION),
        "decision_quality_tier": str(dec.decision_quality_tier or DECISION_QUALITY_DEGRADED_NON_TRADING),
        "response_quality": str(dec.decision_quality_tier or DECISION_QUALITY_DEGRADED_NON_TRADING),
        "mandatory_fields_complete": bool(dec.mandatory_fields_complete),
        "missing_mandatory_fields": list(dec.missing_mandatory_fields or []),
        "invalid_mandatory_fields": list(dec.invalid_mandatory_fields or []),
        "decision_state": str(dec.decision_state or DECISION_REJECT),
        "selected_candidate_id": str(dec.selected_candidate_id or ""),
        "selected_candidate_hash": str(dec.selected_candidate_hash or ""),
        "request_execution_fingerprint": str(dec.request_execution_fingerprint or ""),
        "assessed_execution_fingerprint": str(dec.assessed_execution_fingerprint or ""),
        "selected_target_identity": str(dec.selected_target_identity or ""),
        "selected_target_price": float(dec.selected_target_price or 0.0),
        "assessed_entry": float(dec.assessed_entry or 0.0),
        "assessed_sl": float(dec.assessed_sl or 0.0),
        "assessed_tp1": float(dec.assessed_tp1 or 0.0),
        "assessed_tp2": float(dec.assessed_tp2 or 0.0),
        "candidate_assessments": list(dec.candidate_assessments or []),
        "allow": python_final_allow,
        "raw_allow": bool(dec.model_raw_allow),
        "model_raw_allow": bool(dec.model_raw_allow),
        "python_final_allow": python_final_allow,
        "mql_final_allow": None,
        "decision_field_authority": decision_field_authority_manifest(),
        "rule_score": float(dec.rule_score),
        "llm_quality_score": float(dec.llm_quality_score),
        "blended_legacy_score": float(dec.blended_legacy_score),
        "legacy_agreement_confidence": float(dec.legacy_agreement_confidence),
        "llm_self_reported_confidence": float(dec.llm_self_reported_confidence),
        "calibrated_win_probability": None,
        "expected_net_r": None,
        "oos_predicted_probability": None,
        "calibration_bucket": str(dec.calibration_bucket or ""),
        "calibration_sample_size": int(dec.calibration_sample_size),
        "calibration_lower_bound": None,
        "calibration_upper_bound": None,
        "calibration_model_version": str(dec.calibration_model_version or ""),
        "calibration_data_window_start": str(dec.calibration_data_window_start or ""),
        "calibration_data_window_end": str(dec.calibration_data_window_end or ""),
        "calibration_available": False,
        # Explicit migration aliases. Neither has independent trade authority.
        "score": float(dec.llm_quality_score),
        "chosen_index": int(dec.chosen_index),
        "confidence": float(dec.llm_self_reported_confidence),
        "migration_aliases": {
            "score": "llm_quality_score",
            "confidence": "llm_self_reported_confidence",
        },
        "decision_source": str(dec.decision_source or ""),
        "reasons": reason_text,
        "decision_id": str(dec.decision_id or f"{req_id}:{dec.decision_source or 'decision'}:{int(time.time())}"),
        "rejection_codes": list(dec.rejection_codes or []),
        "narrative_state": str(dec.narrative_state or "audited"),
        "invalidation_risks": list(dec.invalidation_risks or []),
        "missing_confirmations": list(dec.missing_confirmations or []),
        "suggested_risk_multiplier": float(resolved_risk if resolved_risk is not None else 0.0),
        "model_version": str(dec.model_version or AI_GATE_MODEL_VERSION),
        "setup_family": str(setup_family or ""),
        "setup_class": str(_get_any(plan, ["setup_class"], "")),
        "entry_branch": str(_get_any(plan, ["entry_branch", "entry_model"], "")),
        "llm_quality_score_threshold": float(dec.llm_quality_score_threshold),
        "llm_quality_threshold_source": str(dec.llm_quality_threshold_source or ""),
        "global_llm_quality_as_hard_floor": bool(dec.global_llm_quality_as_hard_floor),
        "llm_quality_threshold_passed": bool(dec.llm_quality_threshold_passed),
        "llm_quality_reject_reason": str(dec.llm_quality_reject_reason or ""),
        "structure_quality_score": float(dec.structure_quality_score),
        "entry_timing_score": float(dec.entry_timing_score),
        "follow_through_probability": float(dec.follow_through_probability),
        "invalidation_risk": float(dec.invalidation_risk),
        "chop_risk": float(dec.chop_risk),
        "cost_risk": float(dec.cost_risk),
        "symbol_bucket_risk": float(dec.symbol_bucket_risk),
        "session_bucket_risk": float(dec.session_bucket_risk),
        "post_entry_failure_risk": float(dec.post_entry_failure_risk),
        "final_trade_expectancy_score": float(dec.final_trade_expectancy_score),
        "llm_numeric_diagnostics_authority": "uncalibrated_diagnostic_only_no_direct_trade_authority",
        "veto_enabled": bool(dec.veto_enabled),
        "veto_code": str(dec.veto_code or ""),
        "veto_evidence_fields": list(dec.veto_evidence_fields or []),
        "veto_reason": str(dec.veto_reason or ""),
        "veto": {
            "enabled": bool(dec.veto_enabled),
            "code": str(dec.veto_code or ""),
            "evidence_fields": list(dec.veto_evidence_fields or []),
            "reason": str(dec.veto_reason or ""),
        },
        "bucket_prior_override_justification": str(dec.bucket_prior_override_justification or ""),
        "target_arbitration": dec.target_arbitration or {},
        "chosen_target_model": str(dec.chosen_target_model or ""),
        "chosen_tp1": float(dec.chosen_tp1 or 0.0),
        "chosen_tp2": float(dec.chosen_tp2 or 0.0),
        "chosen_rr1": float(dec.chosen_rr1 or 0.0),
        "chosen_rr2": float(dec.chosen_rr2 or 0.0),
        "rejected_target_models": list(dec.rejected_target_models or []),
        "target_blocker_kind": str(dec.target_blocker_kind or ""),
        "target_blocker_severity": float(dec.target_blocker_severity if dec.target_blocker_severity is not None else -1.0),
        "target_blocker_class": str(dec.target_blocker_class or ""),
        "target_blocker_is_trade_killer": bool(dec.target_blocker_is_trade_killer),
        "target_decision_reason": str(dec.target_decision_reason or ""),
        "target_blocker_severity_present": bool(dec.target_blocker_severity_present),
        "target_blocker_class_present": bool(dec.target_blocker_class_present),
        "target_blocker_is_trade_killer_present": bool(dec.target_blocker_is_trade_killer_present),
        "target_decision_reason_present": bool(dec.target_decision_reason_present),
        "why_not_liquidity_target": str(dec.why_not_liquidity_target or ""),
        "why_not_partial_before_obstacle": str(dec.why_not_partial_before_obstacle or ""),
        "why_not_capped_before_obstacle": str(dec.why_not_capped_before_obstacle or ""),
        "why_not_synthetic_fallback": str(dec.why_not_synthetic_fallback or ""),
        "target_arbitration_schema_version": str(dec.target_arbitration_schema_version or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
        "prompt_contract_version": str(dec.prompt_contract_version or AI_PROMPT_CONTRACT_VERSION),
        "target_comparison": _json_object_from_text(dec.target_comparison_json),
    }

    resp["response_binding_hash"] = response_binding_hash(resp)

    response_contract = response_fingerprint(resp)
    resp["response_fingerprint"] = response_contract["response_fingerprint"]
    resp["full_structured_response_hash"] = response_contract["full_structured_response_hash"]
    resp["response_fingerprint_contract"] = response_contract
    _append_fingerprint_record(
        {
            "recorded_at": int(time.time()),
            "request_id": str(req_id),
            "session_id": str(payload.get("session_id") or ""),
            "request": request_contract,
            "response": response_contract,
            "runtime_governance_versions": runtime_governance_versions(),
        }
    )

    resp_path = resp_dir / f"{req_id}.json"
    resp_dir.mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        ledger.transition(
            str(req_id),
            "RESPONSE_VALIDATED",
            extra={
                "validated_response": resp,
                "decision_quality_tier": str(
                    resp.get("decision_quality_tier") or ""
                ),
                "identity_valid": (
                    str(resp.get("decision_source") or "")
                    != "request_identity_mismatch"
                ),
            },
        )
    atomic_write_json(resp_path, resp, encoding=RESP_ENCODING)
    if ledger is not None:
        ledger.transition(
            str(req_id),
            "RESPONSE_WRITTEN",
            extra={"response_path": str(resp_path)},
        )
    log(
        "[response_written]"
        f" request_id={req_id}"
        f" request_identity_hash={str(resp.get('request_identity_hash') or '')[:16]}"
        f" quality_tier={resp.get('decision_quality_tier', '')}"
        f" path={resp_path.name}"
    )
    _export_mql_tester_replay_cache(payload, resp, resp_dir.parent)
    quality_tier = str(resp.get("decision_quality_tier") or "")
    if (
        quality_tier
        in {
            DECISION_QUALITY_FULL_STRUCTURED,
            DECISION_QUALITY_CACHE_FULL_STRUCTURED,
        }
        and bool(resp.get("mandatory_fields_complete"))
    ):
        _inc_counter("schema_valid_responses")
    else:
        _inc_counter("schema_invalid_responses")
        if quality_tier not in {
            DECISION_QUALITY_FULL_STRUCTURED,
            DECISION_QUALITY_CACHE_FULL_STRUCTURED,
        }:
            _inc_counter("degraded_responses")
    response_rejection_codes = {
        str(code) for code in (resp.get("rejection_codes") or [])
    }
    if response_rejection_codes.intersection(
        {
            "request_identity_mismatch",
            "candidate_hash_mismatch",
            "execution_fingerprint_mismatch",
        }
    ):
        _inc_counter("identity_mismatches")
    decision_state = str(resp.get("decision_state") or "").upper()
    if bool(resp.get("python_final_allow")):
        _inc_counter("ai_approvals")
    elif decision_state == DECISION_ABSTAIN:
        _inc_counter("ai_abstentions")
    else:
        _inc_counter("ai_rejections")
    write_debug_json(
        resp_dir.parent / "response_debug" / f"{req_id}.json",
        {
            "id": req_id,
            "response": resp,
            "missing_fields": missing_fields,
            "payload_summary": payload_summary,
            "reasons_raw": dec.reasons,
        },
    )
    log(
        f"[ai_gate] OK {req_path.name} -> {resp_path.name} "
        f"allow={resp['allow']} llm_quality_score={resp['llm_quality_score']} "
        f"llm_self_reported_confidence={resp['llm_self_reported_confidence']} chosen={resp['chosen_index']} "
        f"source={resp['decision_source']} "
        f"cand={payload_summary['cand_count']} missing={len(missing_fields)}"
    )

    if missing_fields:
        log(f"[ai_gate] {req_path.name} missing critical fields: {', '.join(missing_fields)}")
    if payload_summary["cand_count"] == 0:
        log(f"[ai_gate] {req_path.name} had no candidates")

    terminal_state = "completed" if python_final_allow else "rejected"
    terminal_reason = "python_final_allow_true" if python_final_allow else (
        str(dec.decision_state or "REJECT").lower()
        + ":"
        + ";".join(dec.rejection_codes or [dec.decision_source or "python_reject"])
    )
    moved, move_reason = _archive_request_terminal(req_path, terminal_state, terminal_reason)
    if ledger is not None:
        ledger.transition(
            str(req_id),
            "COMPLETED",
            extra={
                "terminal_state": terminal_state,
                "terminal_reason": terminal_reason,
            },
        )
        if moved:
            ledger.transition(
                str(req_id),
                "ARCHIVED",
                extra={"archive_state": terminal_state},
            )
    log(
        "[request_completed]"
        f" request_id={req_id}"
        f" request_identity_hash={str(resp.get('request_identity_hash') or '')[:16]}"
        f" terminal_state={terminal_state}"
        f" archived={str(moved).lower()}"
    )
    if not moved and move_reason == "permission_denied":
        log(f"[ai_gate] request cleanup deferred {req_path.name}: permission_denied")
    elif not moved and move_reason != "missing":
        log(f"[ai_gate] request cleanup deferred {req_path.name}: {move_reason}")


def _process_claimed_request(
    req_path: Path,
    resp_dir: Path,
    stale_dir: Path,
    lock_path: Path,
) -> None:
    req_id = req_path.stem
    try:
        process_one(
            req_path,
            resp_dir,
            stale_dir,
            lock_path=lock_path,
            idempotency_ledger=REQUEST_IDEMPOTENCY_LEDGER,
        )
    except Exception as exc:
        log(f"[ai_gate] ERROR {req_path.name}: {exc}")
        payload_summary: Dict[str, Any] = {"bridge_error": str(exc)}
        try:
            raw_payload = read_json_any_encoding(req_path)
            raw_payload = _normalize_request_payload(raw_payload)
            req_id = raw_payload.get("id") or req_id
            payload_summary["keys"] = sorted(list(raw_payload.keys()))
        except Exception as inner:
            payload_summary["request_read_error"] = str(inner)
        try:
            resp_path = resp_dir / f"{req_id}.json"
            if not resp_path.exists():
                _write_error_response(req_id, resp_dir, str(exc), payload_summary)
                log(f"[ai_gate] Wrote explicit error response for {req_id}")
            else:
                log(f"[ai_gate] Preserved existing response for {req_id} after error")
        except Exception as inner:
            log(f"[ai_gate] Failed to write error response for {req_path.name}: {inner}")
        moved, move_reason = _archive_request_terminal(req_path, "quarantined", f"processing_error:{exc}")
        if not moved and move_reason not in {"missing", "permission_denied"}:
            log(f"[ai_gate] request cleanup deferred {req_path.name}: {move_reason}")
    finally:
        _release_request_claim(lock_path)

def fill_tester_cache_once(bus: Path) -> Dict[str, int]:
    req_dir = bus / "requests"
    resp_dir = bus / "responses"
    stale_dir = bus / "stale"
    lock_dir = bus / "locks"
    summary = {
        "requests_seen": 0,
        "processed_new": 0,
        "repaired_existing_response": 0,
        "cache_written": 0,
        "cache_existing": 0,
        "skipped": 0,
        "errors": 0,
        "moved_to_stale": 0,
    }

    for req_path in sorted(req_dir.glob("*.json")):
        stable, stability_reason = _stable_input_status(req_path)
        if not stable:
            _quarantine_unstable_input_if_terminal(req_path, stability_reason)
            continue
        _UNSTABLE_FILE_STATE.pop(str(req_path.resolve()), None)
        summary["requests_seen"] += 1
        req_id = req_path.stem
        resp_path = resp_dir / f"{req_id}.json"

        try:
            lock_path = _acquire_request_claim(lock_dir, req_path)
            if lock_path is None:
                summary["skipped"] += 1
                continue

            before_cache_path: Path | None = None
            before_cache_exists = False
            try:
                payload_for_identity = _normalize_request_payload(read_json_any_encoding(req_path))
                before_cache_path, _signature, _key, identity_error = _mql_tester_replay_cache_path(payload_for_identity, bus)
                if identity_error:
                    before_cache_path = None
                before_cache_exists = bool(before_cache_path and before_cache_path.exists())
            except Exception:
                before_cache_path = None

            try:
                processing_path = _move_claimed_request_to_processing(req_path)
            except FileBusClaimDeferredError as exc:
                _release_request_claim(lock_path)
                _handle_claim_deferred(req_path, exc)
                summary["skipped"] += 1
                continue
            _CLAIM_DEFERRED_STATE.pop(str(req_path.resolve()), None)
            _process_claimed_request(processing_path, resp_dir, stale_dir, lock_path)
            summary["processed_new"] += 1
            if not req_path.exists():
                summary["moved_to_stale"] += 1
            if before_cache_path and before_cache_path.exists():
                if before_cache_exists:
                    summary["cache_existing"] += 1
                else:
                    summary["cache_written"] += 1
            else:
                summary["skipped"] += 1
        except Exception as exc:
            summary["errors"] += 1
            log(f"[tester_cache_export] fill_once_error request={req_path.name} error={exc}")

    log(
        "[tester_cache_export_summary] mode=fill_once "
        + " ".join(f"{key}={value}" for key, value in summary.items())
    )
    return summary


def _ledger_status_for_policy_manifest(bus: Path) -> str:
    candidates = (
        bus / "logs" / "analytics" / "ledger_integrity_report.json",
        resolve_project_path("data/ledger_integrity_report.json"),
        resolve_project_path("data/ledger_audit.json"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = read_json_any_encoding(path)
        except Exception:
            continue
        status = str(
            payload.get("global_status")
            or payload.get("ledger_integrity_status")
            or payload.get("status")
            or ""
        ).upper()
        clean_count = int(payload.get("clean_count") or payload.get("clean") or 0)
        if status in {"CLEAN", "VERIFIED_CLEAN", "RECONCILED_CLEAN"} and clean_count > 0:
            return "CLEAN"
        return status or "QUARANTINED"
    return "UNKNOWN"


def _write_startup_policy_manifest(bus: Path) -> Dict[str, Any]:
    policy_dir = bus / "logs" / "policies"
    config_dir = bus / "config"
    ledger_status = _ledger_status_for_policy_manifest(bus)
    management_path = bus / "logs" / "analytics" / "management_policy.json"
    calibration_path = resolve_project_path("data/calibration_artifact.json")
    analytics_policy_enabled = bool(ANALYTICS_AUTO_ACTIVATE)
    hierarchical_prior_enabled = bool(AI_CONFIG.require_live_bucket_priors)
    repeatability_enabled = bool(
        AI_CONFIG.require_repeatability_live or AI_CONFIG.shadow_repeat_enable
    )
    # Python starts before it has an EA request and therefore cannot prove the
    # active runtime-input contract.  An empty expected hash would incorrectly
    # look compatible; active policy authority remains blocked at this stage.
    ea_runtime_required = "__EA_RUNTIME_INPUT_HASH_REQUIRED__"
    specs = [
        PolicySpec("active", "active_policy", policy_dir / "active_policy.json", analytics_policy_enabled, "active", expected_runtime_input_hash=ea_runtime_required),
        PolicySpec("context", "context_policy", policy_dir / "context_policy.ndjson", analytics_policy_enabled, "active", expected_runtime_input_hash=ea_runtime_required),
        PolicySpec("subtype", "subtype_policy", policy_dir / "subtype_policy.ndjson", analytics_policy_enabled, "active", expected_runtime_input_hash=ea_runtime_required),
        PolicySpec("session", "session_weekday_policy", policy_dir / "session_weekday_policy.ndjson", analytics_policy_enabled, "active", expected_runtime_input_hash=ea_runtime_required),
        PolicySpec("hierarchical_priors", "hierarchical_priors", AI_CONFIG.live_bucket_priors_file, hierarchical_prior_enabled, "active", HIERARCHICAL_PRIOR_SCHEMA_VERSION, ea_runtime_required, AI_DECISION_SCHEMA_VERSION, SETUP_TAXONOMY_VERSION),
        PolicySpec("risk_factors", "risk_factor_policy", config_dir / "risk_factor_policy.v1.json", True, "active", RISK_FACTOR_SCHEMA_VERSION, ea_runtime_required, AI_DECISION_SCHEMA_VERSION, SETUP_TAXONOMY_VERSION),
        PolicySpec("invalidation", "invalidation_policy", config_dir / "invalidation_policy.v1.json", True, "active", INVALIDATION_POLICY_SCHEMA_VERSION, ea_runtime_required, AI_DECISION_SCHEMA_VERSION, SETUP_TAXONOMY_VERSION),
        PolicySpec("normalized_fvg", "normalized_fvg_policy", config_dir / "normalized_fvg_policy.v2.json", True, "shadow", NORMALIZED_FVG_SCHEMA_VERSION),
        PolicySpec("management", "management_policy", management_path, management_path.is_file(), "shadow", MANAGEMENT_SCHEMA_VERSION),
        PolicySpec("calibration", "calibration_artifact", calibration_path, calibration_path.is_file(), "shadow", CALIBRATION_CONTRACT_VERSION),
        PolicySpec("repeatability", "repeatability_artifact", AI_CONFIG.shadow_repeat_artifact_file, repeatability_enabled, "shadow", REPEATABILITY_SCHEMA_VERSION),
    ]
    manifest = build_startup_policy_manifest(
        specs,
        ledger_integrity_status=ledger_status,
        runtime_input_hash="python_startup_no_ea_payload",
    )
    manifest["component_scope"] = "python_pre_request"
    manifest["runtime_authority_available"] = False
    manifest["runtime_authority_reason"] = "ea_runtime_payload_not_available_at_python_startup"
    manifest.pop("manifest_hash", None)
    manifest["manifest_hash"] = canonical_hash(manifest)
    governance_atomic_write_json(bus / "logs" / "startup_policy_manifest.json", manifest)
    governance_atomic_write_json(resolve_project_path("data/startup_policy_manifest_latest.json"), manifest)
    for row in manifest["policies"]:
        log(
            "[startup_policy_manifest]"
            f" policy_type={row['policy_type']} policy_id={row['policy_id']}"
            f" enabled={str(bool(row['enabled'])).lower()}"
            f" status={row['status']} authority={row['authority']}"
            f" reason={';'.join(row['rejection_reasons']) or 'none'}"
        )
    return manifest


def _log_file_bus_summary() -> None:
    if FILE_BUS_LIFECYCLE is None:
        return
    summary = FILE_BUS_LIFECYCLE.summary()
    log("[file_bus_final_summary] " + json.dumps(summary, sort_keys=True, separators=(",", ":")))
    with AI_GATE_COUNTERS_LOCK:
        ai_summary = dict(AI_GATE_COUNTERS)
    log(
        "[ai_gate_final_summary] "
        + json.dumps(ai_summary, sort_keys=True, separators=(",", ":"))
    )


def _audit_runtime_caches(bus: Path, *, quarantine_incompatible: bool) -> Dict[str, Any]:
    tester_dir = bus / "logs" / "tester_ai_cache"
    tester_quarantine = tester_dir / "quarantined"
    tester_valid = 0
    tester_invalid = 0
    tester_quarantined = 0
    tester_reasons: Dict[str, int] = {}
    if tester_dir.is_dir():
        for path in sorted(tester_dir.glob("*.json")):
            reason = ""
            try:
                row = read_json_any_encoding(path)
                reason = _mql_tester_cache_skip_reason(row)
                if not reason:
                    stored_binding = str(row.get("response_binding_hash") or "")
                    expected_binding = response_binding_hash(row)
                    if not stored_binding or stored_binding != expected_binding:
                        reason = "response_binding_hash_mismatch"
            except Exception as exc:
                reason = "cache_json_invalid_" + type(exc).__name__
            if not reason:
                tester_valid += 1
                continue
            tester_invalid += 1
            tester_reasons[reason] = tester_reasons.get(reason, 0) + 1
            log(
                "[cache_quarantine]"
                f" pending={str(quarantine_incompatible).lower()}"
                f" cache=tester key={path.stem} reason={reason}"
            )
            if quarantine_incompatible:
                tester_quarantine.mkdir(parents=True, exist_ok=True)
                target = tester_quarantine / (
                    f"{path.stem}__{reason}_{int(time.time() * 1000)}.json"
                )
                os.replace(path, target)
                tester_quarantined += 1

    python_path = AI_CONFIG.decision_cache_file
    python_valid = 0
    python_invalid = 0
    python_quarantined = 0
    python_reasons: Dict[str, int] = {}
    retained_lines: list[str] = []
    rejected_lines: list[str] = []
    if python_path.is_file():
        for raw_line in python_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            reason = ""
            try:
                row = strict_json_loads(raw_line)
                if not isinstance(row, dict):
                    reason = "cache_row_not_object"
                else:
                    decision_row = row.get("decision")
                    if not isinstance(decision_row, dict):
                        reason = "cache_decision_missing"
                    else:
                        reason = _cached_decision_schema_miss_reason(decision_row)
            except Exception as exc:
                reason = "cache_json_invalid_" + type(exc).__name__
            if reason:
                python_invalid += 1
                python_reasons[reason] = python_reasons.get(reason, 0) + 1
                rejected_lines.append(raw_line)
            else:
                python_valid += 1
                retained_lines.append(raw_line)
        if quarantine_incompatible and rejected_lines:
            quarantine_dir = python_path.parent / "cache_quarantine"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            quarantine_path = quarantine_dir / (
                f"{python_path.stem}__incompatible_{int(time.time() * 1000)}.jsonl"
            )
            quarantine_path.write_text(
                "\n".join(rejected_lines) + "\n",
                encoding="utf-8",
            )
            replacement = "\n".join(retained_lines)
            if replacement:
                replacement += "\n"
            temp_path = python_path.with_name(
                f".{python_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
            )
            try:
                with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(replacement)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, python_path)
            finally:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
            python_quarantined = len(rejected_lines)

    summary = {
        "tester_cache": {
            "valid": tester_valid,
            "invalid": tester_invalid,
            "quarantined": tester_quarantined,
            "reasons": tester_reasons,
        },
        "python_decision_cache": {
            "valid": python_valid,
            "invalid": python_invalid,
            "quarantined": python_quarantined,
            "reasons": python_reasons,
        },
    }
    log("[cache_audit] " + json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return summary


def _runtime_repeatability_block_reason(
    required_live: bool,
    authority: Mapping[str, Any],
) -> str:
    if not required_live or authority.get("status") == REPEATABLE:
        return ""
    return str(
        authority.get("reason")
        or "repeatability_authority_unavailable"
    )


def _validate_runtime_authority(bus: Path) -> tuple[bool, Dict[str, Any]]:
    preflight = _run_structured_schema_preflight()
    health = _refresh_provider_health(force=True)
    policy_manifest = _write_startup_policy_manifest(bus)
    cache_summary = _audit_runtime_caches(bus, quarantine_incompatible=False)
    repeatability = _load_repeatability_artifact()
    primary_identity = _provider().identity("analyst")
    repeatability_authority = _repeatability_authority_for(
        AI_CONFIG.model,
        DECISION_QUALITY_FULL_STRUCTURED,
        provider_mode=str(primary_identity.get("provider_mode") or ""),
        provider_id=str(primary_identity.get("provider_id") or ""),
        model_fingerprint=str(primary_identity.get("model_fingerprint") or ""),
        generation_settings_hash=str(
            primary_identity.get("generation_settings_hash") or ""
        ),
    )
    prior = _load_live_bucket_priors()
    bus_paths = {
        name: (bus / name).is_dir()
        for name in (
            "requests",
            "responses",
            "processing",
            "completed",
            "rejected",
            "timed_out",
            "stale",
            "quarantined",
        )
    }
    blocking: list[str] = []
    if not AI_CONFIG.provider_config_valid:
        blocking.append("provider_configuration_invalid")
    if not preflight.get("valid"):
        blocking.append("structured_schema_preflight_failed")
    if not health.get("healthy"):
        blocking.append("provider_health_failed")
    if not all(bus_paths.values()):
        blocking.append("file_bus_directory_missing")
    repeatability_block = _runtime_repeatability_block_reason(
        AI_CONFIG.require_repeatability_live,
        repeatability_authority,
    )
    if repeatability_block:
        blocking.append(repeatability_block)
    if AI_CONFIG.require_live_bucket_priors and not str(prior.get("artifact_hash") or ""):
        blocking.append("hierarchical_priors_unavailable")
    report = {
        "valid_for_live_authority": not blocking,
        "blocking_reasons": blocking,
        "provider": AI_CONFIG.safe_log_dict(),
        "provider_health": health,
        "structured_schemas": preflight,
        "bus_paths": bus_paths,
        "policy_manifest_hash": policy_manifest.get("manifest_hash"),
        "policy_authorities": {
            str(row.get("policy_type")): str(row.get("authority"))
            for row in policy_manifest.get("policies") or []
            if isinstance(row, Mapping)
        },
        "cache": cache_summary,
        "ledger_integrity_status": _ledger_status_for_policy_manifest(bus),
        "repeatability_status": str(repeatability.get("_load_status") or "unknown"),
        "repeatability_groups": len(repeatability.get("groups") or {}),
        "repeatability_authority": repeatability_authority,
        "hierarchical_prior_status": str(prior.get("_load_status") or "unknown"),
    }
    governance_atomic_write_json(bus / "logs" / "runtime_validation_latest.json", report)
    log("[runtime_validation] " + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return not blocking, report

# ---------- Main loop ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "command",
        nargs="?",
        choices=("run", "validate-runtime", "cache-audit", "cache-clear-incompatible"),
        default="run",
        help="Run the bridge or execute one maintenance command.",
    )
    ap.add_argument("--common-files-dir", type=str, default="",
                    help="Path to MT5 Common/Files folder. If omitted, uses COMMON_FILES_DIR env var.")
    ap.add_argument("--bus-root", type=str, default=DEFAULT_BUS_ROOT,
                    help="Bus root folder name under Common/Files (default: PO3_AI_BUS).")
    ap.add_argument("--poll-ms", type=int, default=250, help="Polling interval (ms).")
    ap.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("AI_GATE_WORKERS", "4")),
        help="Maximum AI requests processed concurrently (default: 4).",
    )
    ap.add_argument(
        "--fill-tester-cache-once",
        action="store_true",
        help="Process current RECORD_ONLY tester requests into the MQL tester replay cache, then exit.",
    )
    args = ap.parse_args()

    # You may provide either:
    #  - the Common\Files folder, OR
    #  - the full bus folder (...\Common\Files\PO3_AI_BUS)
    appdata = os.getenv("APPDATA", "")
    if appdata:
        default_common_or_bus = str(Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files")
    else:
        default_common_or_bus = str(Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files")
    common_files_dir = (
        args.common_files_dir
        or os.getenv("COMMON_FILES_DIR", "").strip()
        or default_common_or_bus
    )
    if not common_files_dir:
        raise SystemExit(
            "Please provide --common-files-dir (the MT5 'Common\\Files' path) "
            "or set COMMON_FILES_DIR env var."
        )

    common_path = Path(common_files_dir)

    # If user provided the full bus dir already, don't append bus_root again
    if common_path.name.lower() == args.bus_root.lower():
        bus = common_path
    else:
        bus = common_path / args.bus_root

    req_dir = bus / "requests"
    resp_dir = bus / "responses"
    stale_dir = bus / "stale"
    lock_dir = bus / "locks"
    analytics_jobs_dir = bus / "logs" / "analytics_jobs"

    global LOG_FILE, FILE_BUS_LIFECYCLE, REQUEST_IDEMPOTENCY_LEDGER
    LOG_FILE = bus / "logs" / "ai_gate.log"
    set_ai_usage_bus(bus)

    FILE_BUS_LIFECYCLE = FileBusLifecycle(
        bus,
        session_id=f"python_{os.getpid()}_{int(time.time())}",
    )
    FILE_BUS_LIFECYCLE.ensure()
    REQUEST_IDEMPOTENCY_LEDGER = RequestIdempotencyLedger(
        bus / "request_ledger",
        worker_id=FILE_BUS_LIFECYCLE.session_id,
    )
    recovered = FILE_BUS_LIFECYCLE.recover_processing(stale_after_sec=REQUEST_LOCK_STALE_SEC)

    req_dir.mkdir(parents=True, exist_ok=True)
    resp_dir.mkdir(parents=True, exist_ok=True)
    stale_dir.mkdir(parents=True, exist_ok=True)
    lock_dir.mkdir(parents=True, exist_ok=True)
    analytics_jobs_dir.mkdir(parents=True, exist_ok=True)

    poll_s = max(0.05, args.poll_ms / 1000.0)
    if args.command == "cache-audit":
        summary = _audit_runtime_caches(bus, quarantine_incompatible=False)
        invalid = (
            int(summary["tester_cache"]["invalid"])
            + int(summary["python_decision_cache"]["invalid"])
        )
        raise SystemExit(1 if invalid else 0)
    if args.command == "cache-clear-incompatible":
        _audit_runtime_caches(bus, quarantine_incompatible=True)
        raise SystemExit(0)
    if args.command == "validate-runtime":
        valid, _ = _validate_runtime_authority(bus)
        raise SystemExit(0 if valid else 2)

    selected_provider = _provider()
    if set_expectancy_ai_provider is not None:
        set_expectancy_ai_provider(selected_provider)
    worker_count = max(1, min(16, args.workers))
    if selected_provider.provider_mode == PROVIDER_MODE_LOCAL:
        worker_count = 1
    request_pool = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ai-gate")

    log(f"[ai_gate] Bus root: {bus}")
    log(f"[ai_gate] Watching requests:  {req_dir}")
    log(f"[ai_gate] Writing responses: {resp_dir}")
    log(f"[ai_gate] Stale/bin:         {stale_dir}")
    log(
        "[ai_gate] Selected provider:"
        f" mode={selected_provider.provider_mode} id={selected_provider.provider_id}"
        f" endpoint_class={selected_provider.endpoint_class}"
        f" analyst_model={selected_provider.model_for_role('analyst')}"
        " cross_provider_fallback=false"
    )
    log(f"[ai_gate] Request workers:    {worker_count}")
    log(f"[ai_gate] Analytics jobs:    {analytics_jobs_dir}")
    log(
        f"[file_bus] lifecycle_version={FILE_BUS_LIFECYCLE_VERSION}"
        f" session_id={FILE_BUS_LIFECYCLE.session_id} recovered={len(recovered)}"
    )
    _log_ai_runtime_config_once()
    _run_structured_schema_preflight()
    _refresh_provider_health(force=True)
    _refresh_prior_startup_audit()
    _ensure_repeatability_artifact_container()
    _write_startup_policy_manifest(bus)
    repeatability_artifact = _load_repeatability_artifact()
    log(
        "[repeatability_startup_audit] "
        f"path={AI_CONFIG.shadow_repeat_artifact_file} exists={str(AI_CONFIG.shadow_repeat_artifact_file.is_file()).lower()} "
        f"schema={repeatability_artifact.get('schema_version')} groups={len(repeatability_artifact.get('groups') or {})} "
        f"shadow_enabled={str(AI_CONFIG.shadow_repeat_enable).lower()} sample_rate={AI_CONFIG.shadow_repeat_sample_rate:.4f} "
        f"repeat_count={AI_CONFIG.shadow_repeat_count}"
    )
    if args.fill_tester_cache_once:
        try:
            fill_tester_cache_once(bus)
        finally:
            request_pool.shutdown(wait=True)
            _log_file_bus_summary()
        return
    while True:
        try:
            for req_path in sorted(req_dir.glob("*.json")):
                stable, stability_reason = _stable_input_status(req_path)
                if not stable:
                    _quarantine_unstable_input_if_terminal(
                        req_path,
                        stability_reason,
                    )
                    continue
                _UNSTABLE_FILE_STATE.pop(str(req_path.resolve()), None)
                req_id = req_path.stem
                lock_path = _acquire_request_claim(lock_dir, req_path)
                if lock_path is None:
                    continue
                try:
                    processing_path = _move_claimed_request_to_processing(req_path)
                except FileBusClaimDeferredError as exc:
                    _release_request_claim(lock_path)
                    _handle_claim_deferred(req_path, exc)
                    continue
                except Exception as exc:
                    _release_request_claim(lock_path)
                    log(f"[file_bus] claim_failed request={req_path.name} error={exc}")
                    continue
                _CLAIM_DEFERRED_STATE.pop(str(req_path.resolve()), None)
                request_pool.submit(
                    _process_claimed_request,
                    processing_path,
                    resp_dir,
                    stale_dir,
                    lock_path,
                )
            for job_path in sorted(analytics_jobs_dir.glob("*.json")):
                if job_path.name.endswith(".tmp") or not _is_stable_input_file(job_path):
                    continue
                try:
                    process_analytics_job(job_path, bus, stale_dir)
                    log(f"[ai_gate] analytics refresh complete {job_path.name}")
                except Exception as exc:
                    log(f"[ai_gate] analytics job failed {job_path.name}: {exc}")
                    try:
                        write_debug_json(
                            bus / "logs" / "analytics" / "last_job_error.json",
                            {"job": job_path.name, "error": str(exc), "ts": int(time.time())},
                        )
                    except Exception:
                        pass
                    try:
                        move_to_stale(job_path, stale_dir / "analytics_jobs")
                    except Exception:
                        pass
            time.sleep(poll_s)
        except KeyboardInterrupt:
            print("\n[ai_gate] Stopped.")
            break
    request_pool.shutdown(wait=True)
    _log_file_bus_summary()

if __name__ == "__main__":
    main()
