from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Tuple


# ---------------------------------------------------------------------------
# Provider selection.  This lives here, not in ``ai_gate``, because more than
# one module bootstraps the environment and they must agree on which provider
# is selected and therefore which secrets may exist in the process.  When
# ``expectancy_report`` resolved the selection independently -- off the legacy
# ``AI_USE_REMOTE_API`` boolean alone -- it disagreed with ``ai_gate`` whenever
# the modern ``AI_PROVIDER_SELECT`` key was the one actually configured, and
# since ``ai_gate`` imports ``expectancy_report`` *after* its own bootstrap the
# disagreeing module won: it re-loaded the whole env file (re-injecting the
# secrets the bootstrap had deliberately stripped) and then popped
# ``OPENAI_API_KEY``, which is the credential the selected provider needs.
# One resolver, one secret map, one bootstrap -- so the two cannot diverge.
# ---------------------------------------------------------------------------
PROVIDER_SELECT_OPENAI = "openai_remote"
PROVIDER_SELECT_LOCAL = "local"
PROVIDER_SELECT_OPENROUTER = "openrouter"
PROVIDER_SELECT_VALUES: Tuple[str, ...] = (
    PROVIDER_SELECT_OPENAI,
    PROVIDER_SELECT_LOCAL,
    PROVIDER_SELECT_OPENROUTER,
)

# Credentials owned by each selection.  Everything not owned by the selected
# provider is stripped from the environment before any provider is built, so a
# stale parent-process secret can never reach a transport that must not see it.
PROVIDER_SECRET_KEYS: Dict[str, Tuple[str, ...]] = {
    PROVIDER_SELECT_OPENAI: ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    PROVIDER_SELECT_LOCAL: ("LOCAL_AI_API_KEY", "LOCAL_AI_MODEL_PATH"),
    PROVIDER_SELECT_OPENROUTER: ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"),
}


def resolve_provider_select(
    select_raw: str | None,
    legacy_raw: str | None,
) -> Tuple[str | None, str]:
    """Return ``(selection, error)``; selection is None when unusable."""

    select_text = str(select_raw or "").strip().lower()
    legacy_text = str(legacy_raw or "").strip().lower()
    legacy_select: str | None = None
    if legacy_text == "true":
        legacy_select = PROVIDER_SELECT_OPENAI
    elif legacy_text == "false":
        legacy_select = PROVIDER_SELECT_LOCAL
    elif legacy_text:
        return None, "AI_USE_REMOTE_API=missing_or_invalid"

    if select_text:
        if select_text not in PROVIDER_SELECT_VALUES:
            return None, "AI_PROVIDER_SELECT=invalid"
        if legacy_select is not None and legacy_select != select_text:
            return None, (
                "AI_PROVIDER_SELECT_conflicts_AI_USE_REMOTE_API:"
                f"{select_text}!={legacy_select}"
            )
        return select_text, ""
    if legacy_select is not None:
        return legacy_select, ""
    return None, "AI_USE_REMOTE_API=missing_or_invalid"


def peek_provider_select() -> Tuple[str | None, str]:
    """Resolve the selection from the env file, falling back to the process env.

    Reads only the two non-secret selector keys, so it is safe to call before
    the private environment has been imported.
    """

    dotenv_select = peek_dotenv_value(None, "AI_PROVIDER_SELECT")
    dotenv_switch = peek_dotenv_value(None, "AI_USE_REMOTE_API")
    return resolve_provider_select(
        dotenv_select if dotenv_select is not None else os.environ.get("AI_PROVIDER_SELECT"),
        dotenv_switch if dotenv_switch is not None else os.environ.get("AI_USE_REMOTE_API"),
    )


def excluded_secret_keys(selection: str | None) -> Tuple[str, ...]:
    """Secrets that must not exist in the process for this selection.

    An unusable selector (``None``) keeps *every* provider secret out: the gate
    cannot call anything in that state, so nothing needs a credential.
    """

    return tuple(
        key
        for owner, keys in PROVIDER_SECRET_KEYS.items()
        if owner != selection
        for key in keys
    )


def bootstrap_provider_env() -> Tuple[str | None, str]:
    """Load the env file, keeping only the selected provider's secrets.

    Idempotent: importing it twice re-applies the same exclusions rather than
    re-injecting what a previous call stripped.  Returns ``(selection, error)``.
    """

    selection, error = peek_provider_select()
    excluded = excluded_secret_keys(selection)
    load_dotenv(override=True, exclude_keys=excluded)
    for key in excluded:
        os.environ.pop(key, None)
    return selection, error


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def default_dotenv_path() -> Path:
    """Resolve which env file to load.

    ``load_dotenv`` runs with ``override=True``, so the env file wins over the
    process environment.  That is the right precedence for production, but it
    means an integration run cannot be configured with ordinary environment
    variables -- the only way to point the gate at a local harness endpoint
    would be to edit the same ``.env`` that holds the production API key.
    Editing a live secrets file to run a test is not acceptable, so an operator
    may name an alternate file instead.

    ``PO3_DOTENV_FILE`` is read from the process environment only; an env file
    cannot redirect the loader to another env file.
    """

    override = str(os.environ.get("PO3_DOTENV_FILE") or "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parent / candidate
        return candidate
    return Path(__file__).resolve().with_name(".env")


def peek_dotenv_value(path: str | Path | None, key_name: str) -> str | None:
    """Read one non-secret selector without importing the full environment."""

    env_path = Path(path) if path else default_dotenv_path()
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == key_name:
            return _strip_quotes(value)
    return None


def load_dotenv(
    path: str | Path | None = None,
    *,
    override: bool = False,
    exclude_keys: Iterable[str] = (),
) -> Path:
    env_path = Path(path) if path else default_dotenv_path()
    if not env_path.exists():
        return env_path
    excluded = {str(key).strip() for key in exclude_keys if str(key).strip()}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in excluded:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value)
    if "OPENAI_BASE_URL" not in excluded and not os.environ.get("OPENAI_BASE_URL", "").strip():
        os.environ.pop("OPENAI_BASE_URL", None)
    return env_path
