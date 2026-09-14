"""The system-wide concurrency ceiling is 25 on every path that has no stricter
structural limit (raised from 8 per transport / 16 gate-wide on 2026-09-13).

Every layer that bounds concurrency is pinned here, because a single layer left
behind silently caps the whole system below the configured value:

* the gate's request-pool clamp (``effective_worker_count`` / ``request_pool_size``)
* the per-transport parallelism settings for OpenCode and OpenRouter
* the in-process call semaphore of the Responses transport, which Muse, the
  secondary OpenCode leg and the Luna fallback all run on (it was 16)

The local browser-bridge path keeps its structural limit of 3 isolated lanes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
from ai_gate import AIGateRuntimeConfig  # noqa: E402
from ai_provider import (  # noqa: E402
    MAX_PROVIDER_PARALLELISM,
    OpenCodeResponsesProvider,
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_OPENCODE,
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_REMOTE,
    RemoteAPIProvider,
)


def _opencode_config(**overrides: str) -> AIGateRuntimeConfig:
    env = {
        "AI_PROVIDER_SELECT": "opencode",
        "OPENCODE_GO_API_KEY": "oc-test-key",
        "OPENAI_API_KEY": "sk-test-openai",
    }
    env.update(overrides)
    return AIGateRuntimeConfig.from_env(env)


def _openrouter_config(**overrides: str) -> AIGateRuntimeConfig:
    env = {"AI_PROVIDER_SELECT": "openrouter", "OPENROUTER_API_KEY": "or-test-key"}
    env.update(overrides)
    return AIGateRuntimeConfig.from_env(env)


def _remote_provider() -> RemoteAPIProvider:
    return RemoteAPIProvider(
        api_key="sk-test-openai",
        base_url="",
        primary_model="gpt-5.6-luna",
        fallback_models=[],
        analytics_model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_sec=60.0,
        max_output_tokens=2048,
        prompt_cache_enable=False,
        prompt_cache_key="",
        prompt_cache_retention="",
        service_tier="flex",
        flex_unavailable_retry_enable=False,
        flex_unavailable_max_retries=0,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=1.0,
        log=lambda _m: None,
    )


def _opencode_leg() -> OpenCodeResponsesProvider:
    return OpenCodeResponsesProvider(
        base_url="https://opencode.ai/zen/go/v1",
        api_key="oc-test-key",
        model="muse-spark-1.3-contributor",
        reasoning_effort="high",
        timeout_sec=60.0,
        max_output_tokens=2048,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=1.0,
        log=lambda _m: None,
    )


def _concurrent_slots(provider) -> int:
    """How many calls the transport admits at once, measured by acquiring."""

    taken = 0
    while taken <= MAX_PROVIDER_PARALLELISM + 5 and provider._semaphore.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        provider._semaphore.release()
    return taken


class WorkerCeilingTests(unittest.TestCase):
    def test_the_ceiling_is_25(self) -> None:
        self.assertEqual(MAX_PROVIDER_PARALLELISM, 25)
        self.assertIs(ai_gate.MAX_PROVIDER_PARALLELISM, MAX_PROVIDER_PARALLELISM)

    def test_the_gate_clamps_configured_workers_to_the_ceiling(self) -> None:
        self.assertEqual(ai_gate.effective_worker_count(25, "live_forward"), 25)
        self.assertEqual(ai_gate.effective_worker_count(26, "live_forward"), 25)
        self.assertEqual(ai_gate.effective_worker_count(0, "live_forward"), 1)
        # Live-wait debug stays serial whatever the ceiling.
        self.assertEqual(ai_gate.effective_worker_count(25, "live_wait_debug"), 1)

    def test_opencode_parallelism_accepts_25_and_clamps_above_it(self) -> None:
        ok = _opencode_config(OPENCODE_PARALLELISM="25")
        self.assertEqual(ok.opencode_parallelism, 25)
        self.assertNotIn("OPENCODE_PARALLELISM=above_max", ok.validation_warnings)
        high = _opencode_config(OPENCODE_PARALLELISM="40")
        self.assertEqual(high.opencode_parallelism, 25)
        self.assertIn("OPENCODE_PARALLELISM=above_max", high.validation_warnings)

    def test_openrouter_parallelism_accepts_25_and_clamps_above_it(self) -> None:
        ok = _openrouter_config(OPENROUTER_PARALLELISM="25")
        self.assertEqual(ok.openrouter_parallelism, 25)
        self.assertNotIn("OPENROUTER_PARALLELISM=above_max", ok.validation_warnings)
        high = _openrouter_config(OPENROUTER_PARALLELISM="40")
        self.assertEqual(high.openrouter_parallelism, 25)
        self.assertIn("OPENROUTER_PARALLELISM=above_max", high.validation_warnings)

    def test_request_pool_reaches_25_on_the_paid_transports(self) -> None:
        opencode = _opencode_config(OPENCODE_PARALLELISM="25")
        openrouter = _openrouter_config(OPENROUTER_PARALLELISM="25")
        self.assertEqual(ai_gate.request_pool_size(25, PROVIDER_MODE_OPENCODE, opencode), 25)
        self.assertEqual(ai_gate.request_pool_size(99, PROVIDER_MODE_OPENCODE, opencode), 25)
        self.assertEqual(ai_gate.request_pool_size(25, PROVIDER_MODE_OPENROUTER, openrouter), 25)
        self.assertEqual(ai_gate.request_pool_size(99, PROVIDER_MODE_REMOTE, opencode), 25)

    def test_the_stricter_per_transport_value_still_wins(self) -> None:
        opencode = _opencode_config(OPENCODE_PARALLELISM="8")
        self.assertEqual(ai_gate.request_pool_size(25, PROVIDER_MODE_OPENCODE, opencode), 8)
        self.assertEqual(ai_gate.request_pool_size(4, PROVIDER_MODE_OPENCODE, opencode), 4)

    def test_the_local_path_keeps_its_three_isolated_lanes(self) -> None:
        local = AIGateRuntimeConfig.from_env(
            {"AI_PROVIDER_SELECT": "local", "LOCAL_AI_PARALLELISM": "25"}
        )
        self.assertEqual(local.local_parallelism, 3)
        self.assertEqual(ai_gate.request_pool_size(25, PROVIDER_MODE_LOCAL, local), 3)

    def test_the_responses_transport_admits_25_concurrent_calls(self) -> None:
        """This semaphore was 16, so a 25-worker pool would have queued 9
        calls behind it on Muse, the secondary leg and the Luna fallback."""

        for name, provider in (("luna", _remote_provider()), ("opencode", _opencode_leg())):
            with self.subTest(transport=name):
                self.assertEqual(_concurrent_slots(provider), 25)


if __name__ == "__main__":
    unittest.main()
