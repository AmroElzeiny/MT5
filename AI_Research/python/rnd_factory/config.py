from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .constants import AI_MODES
from .utils import ensure_dir


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path, *, override: bool = True) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and (override or key not in os.environ):
            os.environ[key] = _strip_quotes(value)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid_boolean:{name}={raw}")


def _int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    value = int(raw) if raw else default
    if minimum is not None and value < minimum:
        raise ValueError(f"value_below_minimum:{name}<{minimum}")
    return value


def _float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    value = float(raw) if raw else default
    if minimum is not None and value < minimum:
        raise ValueError(f"value_below_minimum:{name}<{minimum}")
    return value


def _path(name: str, default: Path, base: Path) -> Path:
    raw = os.getenv(name, "").strip()
    value = Path(raw) if raw else default
    if not value.is_absolute():
        value = (base / value).resolve()
    return value


def _csv(name: str, default: Iterable[str]) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    return tuple(x.strip() for x in raw.split(",") if x.strip()) if raw else tuple(default)


@dataclass(frozen=True)
class FactoryConfig:
    package_root: Path
    python_root: Path
    repo_root: Path
    enabled: bool
    ai_mode: str
    remote_base_url: str
    remote_api_key: str
    remote_model: str
    remote_critic_model: str
    remote_synthesis_model: str
    remote_api_style: str
    remote_reasoning_effort: str
    remote_timeout_sec: float
    remote_max_retries: int
    max_ai_calls: int
    max_context_tokens: int
    max_output_tokens: int
    max_cost_usd: float
    input_usd_per_million: float
    cached_input_usd_per_million: float
    output_usd_per_million: float
    require_known_pricing: bool
    local_request_dir: Path
    local_response_dir: Path
    local_processing_dir: Path
    local_archive_dir: Path
    local_quarantine_dir: Path
    local_timeout_sec: float
    local_poll_interval_ms: int
    local_max_request_bytes: int
    local_max_evidence_files: int
    database_path: Path
    reports_dir: Path
    source_index_path: Path
    experiment_pending_dir: Path
    experiment_results_dir: Path
    trade_memory_path: Path
    common_files_dir: Path
    bus_root: str
    max_comparable_cases: int
    max_code_snippets: int
    max_code_snippet_lines: int
    max_log_evidence: int
    max_scan_file_bytes: int
    bootstrap_samples: int
    min_moderate_sample: int
    min_strong_sample: int
    redact_secrets: bool
    source_extensions: tuple[str, ...]
    source_exclude_dirs: tuple[str, ...]
    compat_usage_log_enable: bool
    existing_experiment_registry_path: Path

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.ai_mode not in AI_MODES:
            errors.append(f"RND_AI_MODE must be one of {sorted(AI_MODES)}")
        if self.ai_mode == "remote":
            if not self.remote_api_key:
                errors.append("RND_REMOTE_API_KEY is required in remote mode")
            if not self.remote_model:
                errors.append("RND_REMOTE_MODEL is required in remote mode")
            if self.remote_api_style not in {"responses", "chat_completions"}:
                errors.append("RND_REMOTE_API_STYLE must be responses or chat_completions")
            if self.require_known_pricing and self.input_usd_per_million <= 0 and self.output_usd_per_million <= 0:
                errors.append("Remote pricing must be configured when RND_REQUIRE_KNOWN_PRICING=true")
        if self.max_ai_calls < 0:
            errors.append("RND_MAX_AI_CALLS cannot be negative")
        if self.max_context_tokens < 1000:
            errors.append("RND_MAX_CONTEXT_TOKENS must be >= 1000")
        if self.max_output_tokens < 128:
            errors.append("RND_MAX_OUTPUT_TOKENS must be >= 128")
        if self.min_moderate_sample > self.min_strong_sample:
            errors.append("RND_MIN_MODERATE_SAMPLE cannot exceed RND_MIN_STRONG_SAMPLE")
        protected = [self.repo_root / "MT5_PO3_Codex Include", self.repo_root / "MT5_PO3_Codex Experts"]
        writable = [self.local_request_dir,self.local_response_dir,self.local_processing_dir,self.local_archive_dir,self.local_quarantine_dir,self.database_path,self.reports_dir,self.source_index_path,self.experiment_pending_dir,self.experiment_results_dir]
        for target in writable:
            resolved = target.resolve()
            for base in protected:
                try:
                    resolved.relative_to(base.resolve())
                    errors.append(f"R&D writable path must not be inside production source directory: {resolved}")
                except ValueError:
                    pass
        return errors

    def ensure_directories(self) -> None:
        for path in (
            self.local_request_dir, self.local_response_dir, self.local_processing_dir,
            self.local_archive_dir, self.local_quarantine_dir, self.reports_dir,
            self.experiment_pending_dir, self.experiment_results_dir, self.database_path.parent,
            self.source_index_path.parent,
        ):
            ensure_dir(path)


def _default_common_files() -> Path:
    appdata = os.getenv("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
    return Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"


def load_config(env_path: str | Path | None = None) -> FactoryConfig:
    package_root = Path(__file__).resolve().parent
    python_root = package_root.parent
    repo_root = python_root.parent
    selected = Path(env_path) if env_path else Path(os.getenv("RND_DOTENV_FILE", package_root / ".env"))
    if not selected.is_absolute():
        selected = (package_root / selected).resolve()
    load_env_file(selected, override=True)
    data = package_root / "data"
    common = _path("RND_COMMON_FILES_DIR", Path(os.getenv("COMMON_FILES_DIR", "").strip() or _default_common_files()), package_root)
    config = FactoryConfig(
        package_root=package_root,
        python_root=python_root,
        repo_root=_path("RND_REPO_ROOT", repo_root, package_root),
        enabled=_bool("RND_ENABLE", True),
        ai_mode=os.getenv("RND_AI_MODE", "none").strip().lower() or "none",
        remote_base_url=os.getenv("RND_REMOTE_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        remote_api_key=os.getenv("RND_REMOTE_API_KEY", "").strip(),
        remote_model=os.getenv("RND_REMOTE_MODEL", "").strip(),
        remote_critic_model=os.getenv("RND_REMOTE_CRITIC_MODEL", "").strip(),
        remote_synthesis_model=os.getenv("RND_REMOTE_SYNTHESIS_MODEL", "").strip(),
        remote_api_style=os.getenv("RND_REMOTE_API_STYLE", "responses").strip().lower(),
        remote_reasoning_effort=os.getenv("RND_REMOTE_REASONING_EFFORT", "medium").strip().lower(),
        remote_timeout_sec=_float("RND_REMOTE_TIMEOUT_SEC", 180.0, 1.0),
        remote_max_retries=_int("RND_REMOTE_MAX_RETRIES", 1, 0),
        max_ai_calls=_int("RND_MAX_AI_CALLS", 3, 0),
        max_context_tokens=_int("RND_MAX_CONTEXT_TOKENS", 12000, 1000),
        max_output_tokens=_int("RND_MAX_OUTPUT_TOKENS", 3500, 128),
        max_cost_usd=_float("RND_MAX_COST_USD", 0.25, 0.0),
        input_usd_per_million=_float("RND_REMOTE_INPUT_USD_PER_MILLION", 0.0, 0.0),
        cached_input_usd_per_million=_float("RND_REMOTE_CACHED_INPUT_USD_PER_MILLION", 0.0, 0.0),
        output_usd_per_million=_float("RND_REMOTE_OUTPUT_USD_PER_MILLION", 0.0, 0.0),
        require_known_pricing=_bool("RND_REQUIRE_KNOWN_PRICING", True),
        local_request_dir=_path("RND_LOCAL_REQUEST_DIR", data / "local_ai_bridge" / "requests", package_root),
        local_response_dir=_path("RND_LOCAL_RESPONSE_DIR", data / "local_ai_bridge" / "responses", package_root),
        local_processing_dir=_path("RND_LOCAL_PROCESSING_DIR", data / "local_ai_bridge" / "processing", package_root),
        local_archive_dir=_path("RND_LOCAL_ARCHIVE_DIR", data / "local_ai_bridge" / "archive", package_root),
        local_quarantine_dir=_path("RND_LOCAL_QUARANTINE_DIR", data / "local_ai_bridge" / "quarantine", package_root),
        local_timeout_sec=_float("RND_LOCAL_TIMEOUT_SEC", 180.0, 1.0),
        local_poll_interval_ms=_int("RND_LOCAL_POLL_INTERVAL_MS", 500, 50),
        local_max_request_bytes=_int("RND_LOCAL_MAX_REQUEST_BYTES", 2_000_000, 10_000),
        local_max_evidence_files=_int("RND_LOCAL_MAX_EVIDENCE_FILES", 8, 1),
        database_path=_path("RND_DATABASE_PATH", data / "rnd_factory.sqlite3", package_root),
        reports_dir=_path("RND_REPORTS_DIR", data / "reports", package_root),
        source_index_path=_path("RND_SOURCE_INDEX_PATH", data / "source_index.json", package_root),
        experiment_pending_dir=_path("RND_EXPERIMENT_PENDING_DIR", data / "experiment_jobs" / "pending", package_root),
        experiment_results_dir=_path("RND_EXPERIMENT_RESULTS_DIR", data / "experiment_jobs" / "results", package_root),
        trade_memory_path=_path("RND_TRADE_MEMORY_PATH", python_root / "data" / "ai_trade_memory.sqlite3", package_root),
        common_files_dir=common,
        bus_root=os.getenv("RND_BUS_ROOT", os.getenv("PO3_AI_BUS_ROOT", os.getenv("AI_BUS_ROOT", "PO3_AI_BUS"))).strip() or "PO3_AI_BUS",
        max_comparable_cases=_int("RND_MAX_COMPARABLE_CASES", 20, 1),
        max_code_snippets=_int("RND_MAX_CODE_SNIPPETS", 8, 0),
        max_code_snippet_lines=_int("RND_MAX_CODE_SNIPPET_LINES", 120, 20),
        max_log_evidence=_int("RND_MAX_LOG_EVIDENCE", 50, 0),
        max_scan_file_bytes=_int("RND_MAX_SCAN_FILE_BYTES", 20_000_000, 100_000),
        bootstrap_samples=_int("RND_BOOTSTRAP_SAMPLES", 1200, 100),
        min_moderate_sample=_int("RND_MIN_MODERATE_SAMPLE", 30, 5),
        min_strong_sample=_int("RND_MIN_STRONG_SAMPLE", 100, 10),
        redact_secrets=_bool("RND_REDACT_SECRETS", True),
        source_extensions=_csv("RND_SOURCE_EXTENSIONS", (".py", ".mqh", ".mq5", ".ps1", ".bat")),
        source_exclude_dirs=_csv("RND_SOURCE_EXCLUDE_DIRS", (".git", ".venv", "__pycache__", "data", "logs", "ledger_repair_output", "rnd_factory")),
        compat_usage_log_enable=_bool("RND_COMPAT_USAGE_LOG_ENABLE", False),
        existing_experiment_registry_path=_path("RND_EXISTING_EXPERIMENT_REGISTRY_PATH", python_root / "data" / "experiment_registry.ndjson", package_root),
    )
    errors = config.validate()
    if errors:
        raise ValueError("invalid_rnd_configuration:" + " | ".join(errors))
    config.ensure_directories()
    return config
