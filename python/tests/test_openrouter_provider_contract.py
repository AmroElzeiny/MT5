"""OpenRouter provider mode: selection, wire shape, and Python/MQL agreement.

Every test here fails against the pre-change tree.  The two that matter most are
``test_mql_accepts_every_python_trading_provider_mode`` (a mode Python can emit
but MQL rejects turns a healthy decision into an infrastructure rejection) and
``test_reasoning_is_disabled_explicitly_not_omitted`` (the measured defect: a
routed reasoning model spent 10,798 of a 10,800-token budget thinking and
returned no content at all).
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_governance_contracts import MQL_STAGE  # noqa: E402  (shared resolver)

import ai_gate  # noqa: E402
from ai_gate import (  # noqa: E402
    AIGateRuntimeConfig,
    PROVIDER_SELECT_LOCAL,
    PROVIDER_SELECT_OPENAI,
    PROVIDER_SELECT_OPENROUTER,
    resolve_provider_select,
)
from ai_provider import (  # noqa: E402
    OpenRouterProvider,
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_REMOTE,
    PROVIDER_MODES_TRADING,
)


def _config(**env: str) -> AIGateRuntimeConfig:
    base = {
        "AI_PROVIDER_SELECT": "openrouter",
        "OPENROUTER_API_KEY": "test-key",
        "OPENROUTER_MODEL": "z-ai/glm-5.3-flash",
    }
    base.update(env)
    return AIGateRuntimeConfig.from_env(base)


def _provider(**kwargs) -> OpenRouterProvider:
    defaults = dict(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        analyst_model="z-ai/glm-5.3-flash",
        critic_model="z-ai/glm-5.3-flash",
        adjudicator_model="z-ai/glm-5.3-flash",
        fallback_models=(),
        healthcheck_path="/models",
        timeout_sec=900.0,
        max_retries=2,
        max_output_tokens=25000,
        temperature=0.15,
        top_p=0.85,
        seed=42,
        enable_thinking=False,
        reasoning_effort="low",
        reasoning_token_reserve=24000,
        require_json_schema=True,
        require_structured_provider=True,
        allowed_providers=(),
        parallelism=2,
        context_budget_tokens=131072,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=60.0,
        log=lambda _msg: None,
    )
    defaults.update(kwargs)
    return OpenRouterProvider(**defaults)


class ProviderSelectionTests(unittest.TestCase):
    def test_legacy_switch_still_selects_the_same_providers(self):
        """Existing private .env files must keep working untouched."""

        self.assertEqual(resolve_provider_select(None, "true"), (PROVIDER_SELECT_OPENAI, ""))
        self.assertEqual(resolve_provider_select(None, "false"), (PROVIDER_SELECT_LOCAL, ""))

    def test_missing_selector_fails_closed(self):
        selection, error = resolve_provider_select(None, None)
        self.assertIsNone(selection)
        self.assertTrue(error)

    def test_invalid_selector_fails_closed(self):
        selection, error = resolve_provider_select("gemini", None)
        self.assertIsNone(selection)
        self.assertEqual(error, "AI_PROVIDER_SELECT=invalid")

    def test_contradiction_fails_closed_rather_than_preferring_one(self):
        selection, error = resolve_provider_select("openrouter", "true")
        self.assertIsNone(selection)
        self.assertIn("conflicts", error)

    def test_agreeing_pair_is_accepted(self):
        self.assertEqual(
            resolve_provider_select("openai_remote", "true"), (PROVIDER_SELECT_OPENAI, "")
        )

    def test_openrouter_is_not_the_openai_remote_api(self):
        """use_remote_api gates OpenAI-only behaviour (Responses API, service
        tiers, Batch, prompt caching). OpenRouter must never satisfy it."""

        cfg = _config()
        self.assertEqual(cfg.provider_select, PROVIDER_SELECT_OPENROUTER)
        self.assertFalse(cfg.use_remote_api)
        self.assertTrue(cfg.is_openrouter_provider)
        self.assertFalse(cfg.is_local_provider)
        self.assertEqual(cfg.provider_mode, PROVIDER_MODE_OPENROUTER)

    def test_missing_key_fails_closed(self):
        cfg = _config(OPENROUTER_API_KEY="")
        self.assertFalse(cfg.provider_config_valid)
        self.assertIn("OPENROUTER_API_KEY=missing", cfg.provider_config_errors)

    def test_json_schema_cannot_be_switched_off(self):
        """Downgrading the strict schema to best-effort JSON is an authority
        change, so it is a configuration error rather than a warning."""

        cfg = _config(OPENROUTER_REQUIRE_JSON_SCHEMA="false")
        self.assertFalse(cfg.provider_config_valid)
        self.assertIn("OPENROUTER_REQUIRE_JSON_SCHEMA=must_be_true", cfg.provider_config_errors)

    def test_selected_model_comes_from_the_openrouter_category(self):
        cfg = _config(OPENROUTER_ANALYST_MODEL="z-ai/glm-5.2:free")
        self.assertEqual(cfg.model, "z-ai/glm-5.2:free")
        self.assertEqual(cfg.expectancy_model, "z-ai/glm-5.2:free")

    def test_secret_isolation_map_covers_every_selection(self):
        for selection in (PROVIDER_SELECT_OPENAI, PROVIDER_SELECT_LOCAL, PROVIDER_SELECT_OPENROUTER):
            self.assertIn(selection, ai_gate._PROVIDER_SECRET_KEYS)
        self.assertIn("OPENROUTER_API_KEY", ai_gate._PROVIDER_SECRET_KEYS[PROVIDER_SELECT_OPENROUTER])

    def test_log_dict_never_contains_the_key(self):
        cfg = _config(OPENROUTER_API_KEY="sk-or-v1-secret-value")
        rendered = repr(cfg.safe_log_dict())
        self.assertNotIn("sk-or-v1-secret-value", rendered)
        self.assertTrue(cfg.safe_log_dict()["openrouter_api_key_configured"])


class OpenRouterWireContractTests(unittest.TestCase):
    def test_identity_is_its_own_mode_not_local(self):
        provider = _provider()
        self.assertEqual(provider.provider_mode, PROVIDER_MODE_OPENROUTER)
        self.assertEqual(provider.provider_id, "openrouter_api")
        self.assertNotEqual(provider.provider_mode, PROVIDER_MODE_LOCAL)

    def test_reasoning_is_disabled_explicitly_not_omitted(self):
        """Measured defect: omitting the field leaves a routed reasoning model
        reasoning by default, which consumed the entire schema-sized budget
        (10,798 of 10,800 tokens) and returned empty content."""

        body = _provider(enable_thinking=False)._wire_extra_body()
        self.assertEqual(body["reasoning"], {"enabled": False})

    def test_thinking_on_sends_effort_and_widens_the_budget(self):
        provider = _provider(enable_thinking=True, reasoning_effort="high", reasoning_token_reserve=24000)
        self.assertEqual(provider._wire_extra_body()["reasoning"], {"enabled": True, "effort": "high"})
        self.assertEqual(provider._wire_max_tokens(10800), 34800)

    def test_thinking_off_never_widens_the_budget(self):
        self.assertEqual(_provider(enable_thinking=False)._wire_max_tokens(10800), 10800)

    def test_structured_requirement_is_stated_to_the_router(self):
        """Support is per endpoint, not per model. Without require_parameters the
        router may pick an endpoint that treats the schema as a hint."""

        self.assertTrue(_provider()._wire_extra_body()["provider"]["require_parameters"])

    def test_provider_pin_disables_fallback(self):
        body = _provider(allowed_providers=("Fireworks", "Together"))._wire_extra_body()
        self.assertEqual(body["provider"]["order"], ["Fireworks", "Together"])
        self.assertFalse(body["provider"]["allow_fallbacks"])

    def test_local_provider_wire_shape_is_unchanged(self):
        """The hooks must not alter the local transport they were factored out of."""

        from ai_provider import LocalOpenAICompatibleProvider

        local = LocalOpenAICompatibleProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="local",
            analyst_model="m",
            critic_model="m",
            adjudicator_model="m",
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=60.0,
            max_retries=1,
            max_output_tokens=4096,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=True,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=7000,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=60.0,
            log=lambda _m: None,
        )
        self.assertEqual(local.provider_mode, PROVIDER_MODE_LOCAL)
        self.assertEqual(
            local._wire_extra_body(), {"chat_template_kwargs": {"enable_thinking": True}}
        )
        self.assertEqual(local._wire_max_tokens(10800), 10800)

    def test_generation_settings_hash_separates_thinking_modes(self):
        """Repeatability binds to generation settings; two different reasoning
        configurations must not share one identity."""

        on = _provider(enable_thinking=True).generation_identity("analyst", {})
        off = _provider(enable_thinking=False).generation_identity("analyst", {})
        self.assertNotEqual(on["generation_settings_hash"], off["generation_settings_hash"])

    def test_attribution_headers_only_when_configured(self):
        self.assertEqual(_provider(app_url="", app_title="")._client_default_headers(), {})
        headers = _provider(app_url="https://example.invalid", app_title="PO3")._client_default_headers()
        self.assertEqual(headers, {"HTTP-Referer": "https://example.invalid", "X-Title": "PO3"})


class PythonMqlProviderModeAgreementTests(unittest.TestCase):
    """A mode Python can emit but MQL rejects is an infrastructure rejection on
    healthy infrastructure, which the project contract forbids."""

    def _config_source(self) -> str:
        return (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="ignore")

    def test_mql_accepts_every_python_trading_provider_mode(self):
        source = self._config_source()
        match = re.search(
            r"bool\s+AiProviderModeIsTradeable\s*\([^)]*\)\s*\{(.*?)\n\s*\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "AiProviderModeIsTradeable not found in Config.mqh")
        body = match.group(1)
        declared = dict(
            re.findall(r'const\s+string\s+(AI_PROVIDER_MODE_\w+)\s*=\s*"([^"]+)"', source)
        )
        accepted = {declared[name] for name in re.findall(r"AI_PROVIDER_MODE_\w+", body) if name in declared}
        self.assertEqual(
            accepted,
            set(PROVIDER_MODES_TRADING),
            "Config.mqh and ai_provider.PROVIDER_MODES_TRADING disagree",
        )

    def test_every_mql_validator_uses_the_shared_helper(self):
        """Three separate files validated provider_mode against their own copy of
        the literals; a fourth copy is how one side silently drifts again."""

        for name in ("AIGateBridge.mqh", "StateStore.mqh", "TradeEngine.mqh"):
            source = (MQL_STAGE / name).read_text(encoding="utf-8", errors="ignore")
            self.assertIn("AiProviderModeIsTradeable", source, f"{name} lost the shared check")
            stray = re.findall(r'provider_mode\s*[!=]=\s*"(?:REMOTE_API|LOCAL_OPENAI_COMPATIBLE|OPENROUTER_API)"', source)
            self.assertEqual(stray, [], f"{name} still compares provider_mode to a literal: {stray}")

    def test_python_response_validator_uses_the_shared_set(self):
        source = (ROOT / "ai_gate.py").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("not in set(PROVIDER_MODES_TRADING)", source)
        self.assertNotIn("{PROVIDER_MODE_REMOTE, PROVIDER_MODE_LOCAL}", source)

    def test_remote_only_features_stay_bound_to_the_openai_mode(self):
        """Batch, flex service tiers and prompt caching are OpenAI-only. They key
        off PROVIDER_MODE_REMOTE, which OpenRouter must never equal."""

        self.assertNotEqual(PROVIDER_MODE_OPENROUTER, PROVIDER_MODE_REMOTE)
        source = (ROOT / "ai_gate.py").read_text(encoding="utf-8", errors="ignore")
        self.assertIn(
            'if _provider().provider_mode != PROVIDER_MODE_REMOTE',
            source,
            "the Batch-API guard must still be OpenAI-only",
        )


if __name__ == "__main__":
    unittest.main()
