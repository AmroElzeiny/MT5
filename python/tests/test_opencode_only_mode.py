"""OpenCode-only mode: ``OPENCODE_FALLBACK_ENABLE=false`` removes the Luna leg.

Pinned:

1. Configuration no longer requires ``OPENAI_API_KEY`` and stays valid.
2. Directing cannot be enabled without Luna (the critical route exists only to reach it).
3. A failing Muse with every fallback off fails closed: no OpenAI call exists to make.
4. The default (fallback enabled) still requires the OpenAI credential.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_gate import AIGateRuntimeConfig  # noqa: E402
from ai_provider import OpenCodeRoutedProvider, ProviderCallError  # noqa: E402
from opencode_routing import OpenCodeRoutingPolicy  # noqa: E402
from test_opencode_muse_fallback_glm import _no_admission_retry_muse, _status_error  # noqa: E402
from test_opencode_provider_contract import VALID_ANSWER, _call, _glm, _secondary  # noqa: E402


class OpenCodeOnlyConfigTests(unittest.TestCase):
    def test_fallback_disabled_does_not_require_openai_key(self) -> None:
        cfg = AIGateRuntimeConfig.from_env(
            {
                "AI_PROVIDER_SELECT": "opencode",
                "OPENCODE_GO_API_KEY": "oc",
                "OPENCODE_FALLBACK_ENABLE": "false",
            }
        )
        self.assertNotIn("OPENAI_API_KEY=missing_opencode_fallback", cfg.provider_config_errors)
        self.assertFalse(cfg.opencode_fallback_enable)
        self.assertTrue(cfg.provider_config_valid, cfg.provider_config_errors)

    def test_directing_requires_the_fallback_leg(self) -> None:
        cfg = AIGateRuntimeConfig.from_env(
            {
                "AI_PROVIDER_SELECT": "opencode",
                "OPENCODE_GO_API_KEY": "oc",
                "OPENCODE_FALLBACK_ENABLE": "false",
                "OPENCODE_CALL_DIRECTING": "true",
            }
        )
        self.assertFalse(cfg.provider_config_valid)
        self.assertIn("OPENCODE_CALL_DIRECTING=requires_fallback", cfg.provider_config_errors)

    def test_default_still_requires_openai_key(self) -> None:
        cfg = AIGateRuntimeConfig.from_env(
            {"AI_PROVIDER_SELECT": "opencode", "OPENCODE_GO_API_KEY": "oc"}
        )
        self.assertIn("OPENAI_API_KEY=missing_opencode_fallback", cfg.provider_config_errors)


class OpenCodeOnlyRoutingTests(unittest.TestCase):
    def test_failing_muse_fails_closed_without_any_fallback(self) -> None:
        lines: list[str] = []
        routed = OpenCodeRoutedProvider(
            muse=_no_admission_retry_muse(_status_error(500, "upstream failure")),
            muse_fallback=_glm(VALID_ANSWER),
            secondary=_secondary(VALID_ANSWER, lines.append),
            fallback=None,
            policy=OpenCodeRoutingPolicy(),
            log=lines.append,
            fallback_service_tier="flex",
            secondary_fallback_enable=False,
        )
        with self.assertRaises(ProviderCallError):
            _call(routed)
        self.assertEqual(len(routed._muse_fallback.transport_double.calls), 0)
        self.assertEqual(len(routed._secondary.transport_double.calls), 0)
        self.assertTrue(any("reason=no_fallback_leg_configured" in line for line in lines))
        self.assertEqual(routed.identity()["opencode_legs"]["critical"], routed._muse.model_for_role("analyst"))

if __name__ == "__main__":
    unittest.main()
