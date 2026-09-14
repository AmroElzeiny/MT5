"""OpenCode Go cost correctness: call counts, retry ownership, cacheable prefix,
JSON enforcement and consumption accounting.

None of these tests reduces, samples or gates a call.  They pin that the SAME
intended workload produces exactly the outbound requests it should, that the
reusable part of every request is byte-identical, and that every attempt is
accounted for at OpenCode Go's published rates.

Falsified against the pre-change tree (``ai_provider.py``/``decision_pipeline.py``
before 2026-09-13): the stable-session, critic/adjudicator prefix and accounting
tests fail there; the call-count and retry-ownership tests pass on both trees
because the transport already had the correct shape -- they pin it.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
import opencode_go_accounting as acct  # noqa: E402
from ai_provider import (  # noqa: E402
    OPENCODE_SECONDARY_PROVIDER_ID,
    OpenCodeChatCompletionsProvider,
    OpenCodeResponsesProvider,
    OpenCodeRoutedProvider,
    ProviderCallError,
    RemoteAPIProvider,
)
from opencode_routing import LIVE_FORWARD, OpenCodeRoutingPolicy  # noqa: E402
from structured_models import (  # noqa: E402
    ModelAIGateOutput,
    ModelCriticDecision,
    StrictStructuredModel,
)

MUSE = "muse-spark-1.3-contributor"
DEEPSEEK = "deepseek-v4.1-flash"
GLM = "glm-5.3-flash"
LUNA = "gpt-5.6-luna"
BASE_URL = "https://opencode.ai/zen/go/v1"
SECRET_OPENCODE = "oc-test-secret-key-DO-NOT-LOG"
SECRET_OPENAI = "sk-test-secret-key-DO-NOT-LOG"


class _Probe(StrictStructuredModel):
    decision_state: str
    llm_quality_score: float


VALID = '{"decision_state":"ABSTAIN","llm_quality_score":6.5}'
SCHEMA_INVALID = '{"decision_state":"ABSTAIN"}'
MALFORMED = '{"decision_state":"ABSTAIN",'


class _Double:
    """Responses transport double: records every create(), thread-safe."""

    def __init__(self, answer, *, usage=None) -> None:
        self.answer = answer
        self.calls: list[dict] = []
        self.factory_kwargs: list[dict] = []
        self.with_options_kwargs: list[dict] = []
        self._lock = threading.Lock()
        self.usage = usage or SimpleNamespace(
            input_tokens=37000,
            input_tokens_details=SimpleNamespace(cached_tokens=6513),
            output_tokens=12000,
            output_tokens_details=SimpleNamespace(reasoning_tokens=9000),
            total_tokens=49000,
        )

    def create(self, **kwargs):
        with self._lock:
            self.calls.append(dict(kwargs))
        answer = self.answer(kwargs) if callable(self.answer) else self.answer
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(
            id=f"resp_{len(self.calls)}",
            model=kwargs["model"],
            status="completed",
            output_text=answer,
            usage=self.usage,
        )

    def factory(self, **kwargs):
        self.factory_kwargs.append(dict(kwargs))
        outer = self

        class _Client:
            responses = outer

            @staticmethod
            def with_options(**options):
                outer.with_options_kwargs.append(dict(options))
                return _Client

        return _Client


def _opencode_leg(model: str, answer, *, provider_id=None, **extra) -> OpenCodeResponsesProvider:
    double = _Double(answer)
    kwargs = dict(
        base_url=BASE_URL,
        api_key=SECRET_OPENCODE,
        model=model,
        reasoning_effort="high",
        timeout_sec=60.0,
        max_output_tokens=2048,
        circuit_failure_threshold=10_000,
        circuit_cooldown_sec=1.0,
        log=lambda _m: None,
        client_factory=double.factory,
    )
    if provider_id:
        kwargs["provider_id"] = provider_id
    kwargs.update(extra)
    leg = OpenCodeResponsesProvider(**kwargs)
    leg.double = double
    return leg


def _luna(answer) -> RemoteAPIProvider:
    double = _Double(answer)
    leg = RemoteAPIProvider(
        api_key=SECRET_OPENAI,
        base_url="",
        primary_model=LUNA,
        fallback_models=[],
        analytics_model=LUNA,
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
        circuit_failure_threshold=10_000,
        circuit_cooldown_sec=1.0,
        log=lambda _m: None,
        client_factory=double.factory,
    )
    leg.double = double
    return leg


class _ChatDouble:
    """/chat/completions double for the GLM Muse-fallback leg."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def create(self, **kwargs):
        with self._lock:
            self.calls.append(dict(kwargs))
        answer = self.answer(kwargs) if callable(self.answer) else self.answer
        if isinstance(answer, BaseException):
            raise answer
        return {
            "id": f"chatcmpl_{len(self.calls)}",
            "model": kwargs["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 37000, "completion_tokens": 12000, "total_tokens": 49000},
        }

    def factory(self, **_kwargs):
        outer = self

        class _Client:
            chat = SimpleNamespace(completions=outer)

            @staticmethod
            def with_options(**_options):
                return _Client

        return _Client


def _glm_leg(answer, **extra) -> OpenCodeChatCompletionsProvider:
    double = _ChatDouble(answer)
    kwargs = dict(
        base_url=BASE_URL,
        api_key=SECRET_OPENCODE,
        model=GLM,
        reasoning_effort="high",
        timeout_sec=60.0,
        max_output_tokens=2048,
        circuit_failure_threshold=10_000,
        circuit_cooldown_sec=1.0,
        log=lambda _m: None,
        client_factory=double.factory,
    )
    kwargs.update(extra)
    leg = OpenCodeChatCompletionsProvider(**kwargs)
    leg.double = double
    return leg


def _routed(*, muse=VALID, muse_fallback=VALID, secondary=VALID, luna=VALID, secondary_enable=True, ledger=None):
    routed = OpenCodeRoutedProvider(
        muse=_opencode_leg(MUSE, muse),
        muse_fallback=_glm_leg(muse_fallback),
        secondary=_opencode_leg(MUSE, secondary, provider_id=OPENCODE_SECONDARY_PROVIDER_ID),
        fallback=_luna(luna),
        policy=OpenCodeRoutingPolicy(),
        log=lambda _m: None,
        fallback_service_tier="flex",
        secondary_fallback_enable=secondary_enable,
    )
    if ledger is not None:
        for leg in (routed._muse, routed._muse_fallback, routed._secondary, routed._fallback):
            leg.attempt_observer = ledger.append
        routed.call_observer = ledger.append
    return routed


def _call(routed, index: int = 0, *, role="analyst", evidence=None, system="Return JSON.", schema=_Probe):
    return routed.generate_structured(
        role=role,
        system_prompt=system,
        evidence=evidence if evidence is not None else {"candidates": [{"candidate_index": 0, "rule_score": 5.0}], "n": index},
        response_schema=schema,
        request_metadata={"request_id": f"req-{index}", "workload_mode": LIVE_FORWARD},
    )


def _counts(routed) -> tuple[int, int, int]:
    """(Muse, Muse fallback, Luna) -- the normal route.  The secondary
    (important route) must never be reached from it."""

    assert len(routed._secondary.double.calls) == 0, "secondary reached from the normal route"
    return (
        len(routed._muse.double.calls),
        len(routed._muse_fallback.double.calls),
        len(routed._fallback.double.calls),
    )


# ---------------------------------------------------------------------------
# 1. Call-count invariants
# ---------------------------------------------------------------------------


class CallCountInvariantTests(unittest.TestCase):
    N = 100

    def test_100_valid_muse_decisions_are_100_muse_requests_and_zero_fallbacks(self) -> None:
        ledger: list[dict] = []
        routed = _routed(ledger=ledger)
        for i in range(self.N):
            result = _call(routed, i)
            self.assertEqual(result.actual_model, MUSE)
        self.assertEqual(_counts(routed), (self.N, 0, 0))
        attempts = [r for r in ledger if r["event"] == acct.EVENT_ATTEMPT]
        logical = [r for r in ledger if r["event"] == acct.EVENT_LOGICAL_CALL]
        self.assertEqual(len(attempts), self.N)
        self.assertEqual(len(logical), self.N)
        self.assertEqual({r["model"] for r in attempts}, {MUSE})
        self.assertTrue(all(r["outcome"] == "ok" for r in attempts))
        self.assertEqual(len({r["logical_call_id"] for r in attempts}), self.N)

    def test_100_schema_invalid_muse_answers_are_100_muse_and_100_luna_requests(self) -> None:
        # Secondary stage disabled: the chain is exactly Muse -> Luna.
        routed = _routed(muse=SCHEMA_INVALID, secondary_enable=False)
        for i in range(self.N):
            self.assertEqual(_call(routed, i).actual_model, LUNA)
        self.assertEqual(_counts(routed), (self.N, 0, self.N))

    def test_100_malformed_muse_answers_never_repeat_muse(self) -> None:
        routed = _routed(muse=MALFORMED, secondary_enable=False)
        for i in range(self.N):
            _call(routed, i)
        muse, secondary, luna = _counts(routed)
        self.assertEqual(muse, self.N)  # not 200: no JSON repair re-ask
        self.assertEqual((secondary, luna), (0, self.N))

    def test_configured_secondary_stage_is_one_request_per_leg(self) -> None:
        routed = _routed(muse=SCHEMA_INVALID, muse_fallback=MALFORMED, secondary_enable=True)
        for i in range(self.N):
            self.assertEqual(_call(routed, i).actual_model, LUNA)
        self.assertEqual(_counts(routed), (self.N, self.N, self.N))

    def test_successful_muse_answer_never_touches_any_fallback_leg(self) -> None:
        routed = _routed(
            muse_fallback=AssertionError("muse fallback must not run"),
            secondary=AssertionError("secondary must not run"),
            luna=AssertionError("luna must not run"),
        )
        _call(routed)
        self.assertEqual(_counts(routed), (1, 0, 0))


# ---------------------------------------------------------------------------
# 2. Retry ownership
# ---------------------------------------------------------------------------


class _Http500(Exception):
    status_code = 500

    def __str__(self) -> str:
        return "Error code: 500 - upstream error"


class _Timeout(Exception):
    def __str__(self) -> str:
        return "Request timed out."


class _GoUsageLimit(Exception):
    status_code = 429
    body = {"type": "error", "error": {"type": "GoUsageLimitError", "message": "Weekly usage limit reached."}}

    def __str__(self) -> str:
        return "Error code: 429 - GoUsageLimitError Weekly usage limit reached."


class RetryOwnershipTests(unittest.TestCase):
    def test_sdk_retries_are_disabled_at_client_and_per_call(self) -> None:
        routed = _routed()
        _call(routed)
        leg = routed._muse
        self.assertEqual(leg.double.factory_kwargs[0]["max_retries"], 0)
        self.assertTrue(all(o.get("max_retries") == 0 for o in leg.double.with_options_kwargs))
        self.assertEqual(leg.max_retries, 0)
        self.assertEqual(leg.schema_repair_attempts, 0)
        self.assertFalse(leg.resubmit_ambiguous_transport_failures)

    def test_retryable_5xx_is_not_multiplied_by_any_layer(self) -> None:
        routed = _routed(muse=_Http500(), secondary_enable=False)
        _call(routed)
        self.assertEqual(_counts(routed), (1, 0, 1))

    def test_timeout_never_starts_a_second_identical_muse_call(self) -> None:
        ledger: list[dict] = []
        routed = _routed(muse=_Timeout(), secondary_enable=False, ledger=ledger)
        _call(routed)
        self.assertEqual(_counts(routed), (1, 0, 1))
        muse_rows = [r for r in ledger if r["event"] == acct.EVENT_ATTEMPT and r["model"] == MUSE]
        self.assertEqual(len(muse_rows), 1)
        # The abandoned attempt is tracked, not forgotten: it may still bill.
        self.assertEqual(muse_rows[0]["billing_state"], "unknown_possibly_billed")
        luna_rows = [r for r in ledger if r["event"] == acct.EVENT_ATTEMPT and r["model"] == LUNA]
        self.assertEqual(luna_rows[0]["route_stage"], 1)
        self.assertEqual(luna_rows[0]["logical_call_id"], muse_rows[0]["logical_call_id"])

    def test_go_usage_limit_429_is_not_resubmitted(self) -> None:
        routed = _routed(muse=_GoUsageLimit(), secondary_enable=False)
        _call(routed)
        self.assertEqual(_counts(routed), (1, 0, 1))


# ---------------------------------------------------------------------------
# 3. Cacheable prefix
# ---------------------------------------------------------------------------

ANALYST_SYSTEM = "You are the independent Analyst. " * 40


def _market_state(price: float, rsi: float, spread: float, ts: int, positions: list) -> dict:
    return {
        "request_id": f"591813800_{ts}_GBPNZD_13183",
        "created_at": ts,
        "candidates": [{"candidate_index": 0, "entry": price, "rsi": rsi, "spread": spread}],
        "positions": positions,
    }


class StablePrefixTests(unittest.TestCase):
    def _wire(self, evidence: dict, *, request_id: str, model: str = MUSE, schema=ModelAIGateOutput) -> dict:
        leg = _opencode_leg(model, VALID)
        with mock.patch("ai_provider._schema_validate", return_value=_Probe(decision_state="ABSTAIN", llm_quality_score=1.0)):
            leg.generate_structured(
                role="analyst",
                system_prompt=ANALYST_SYSTEM,
                evidence=evidence,
                response_schema=schema,
                request_metadata={"request_id": request_id, "deadline": None},
            )
        return leg.double.calls[0]

    def test_static_prefix_is_byte_identical_across_market_states(self) -> None:
        a = self._wire(_market_state(2.29954, 61.2, 0.8, 1786509126, []), request_id="req-A")
        b = self._wire(_market_state(2.31877, 38.9, 2.4, 1786512000, [{"ticket": 7}]), request_id="req-B")
        prefix_a = json.dumps(acct.stable_prefix_material(a), sort_keys=True, separators=(",", ":")).encode()
        prefix_b = json.dumps(acct.stable_prefix_material(b), sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(sha256(prefix_a).hexdigest(), sha256(prefix_b).hexdigest())
        # The session header is part of routing, and must be stable too.
        self.assertEqual(a["extra_headers"]["x-opencode-session"], b["extra_headers"]["x-opencode-session"])
        # The volatile suffix does differ.
        self.assertNotEqual(a["input"], b["input"])
        text = prefix_a.decode()
        for volatile in ("req-A", "1786509126", "2.29954", "61.2", "GBPNZD", "ticket", "created_at"):
            self.assertNotIn(volatile, text)
        self.assertNotIn("req-A", a["extra_headers"]["x-opencode-session"])

    def test_static_blocks_appear_exactly_once_on_the_wire(self) -> None:
        sent = self._wire(_market_state(1.0, 50.0, 1.0, 1, []), request_id="req-once")
        serialized = json.dumps(sent, sort_keys=True, default=str)
        self.assertEqual(serialized.count("You are the independent Analyst."), 40)  # the prompt's own 40 repeats, once
        self.assertEqual(sent["instructions"], ANALYST_SYSTEM)
        schema_json = json.dumps(sent["text"]["format"]["schema"], sort_keys=True)
        self.assertEqual(serialized.count(schema_json), 1)
        self.assertNotIn(ANALYST_SYSTEM[:60], json.dumps(sent["input"]))

    def test_sessions_separate_models_and_schemas(self) -> None:
        state = _market_state(1.0, 50.0, 1.0, 1, [])
        muse_analyst = self._wire(state, request_id="r1")["extra_headers"]["x-opencode-session"]
        muse_critic = self._wire(state, request_id="r1", schema=ModelCriticDecision)["extra_headers"]["x-opencode-session"]
        deepseek_analyst = self._wire(state, request_id="r1", model=DEEPSEEK)["extra_headers"]["x-opencode-session"]
        self.assertEqual(len({muse_analyst, muse_critic, deepseek_analyst}), 3)


class CriticPromptPrefixTests(unittest.TestCase):
    """The critic/adjudicator id lists are per-request; they must come last."""

    def _critic_prompt(self, candidate_index: int) -> str:
        from decision_pipeline import run_qualitative_consensus
        from test_harness_provider_integration import _catalog, _envelope

        captured: list[str] = []

        class _Stop(Exception):
            pass

        class _Capture:
            provider_id = "capture"

            def generate_structured(self, **kwargs):
                captured.append(kwargs["system_prompt"])
                raise _Stop()

        envelope = _envelope()
        candidate = envelope["entry_and_invalidation"]["candidates"][candidate_index]
        with self.assertRaises(_Stop):
            run_qualitative_consensus(
                provider=_Capture(),
                evidence=envelope,
                analyst_assessment={
                    "candidate_index": candidate_index,
                    "candidate_id": candidate["candidate_id"],
                    "candidate_hash": candidate["candidate_hash"],
                    "decision_state": "APPROVE",
                    "confidence_band": "HIGH",
                    "missing_required_evidence": [],
                },
                request_metadata={"request_id": f"req-{candidate_index}", "request_identity_hash": "c" * 32},
                evidence_catalog=_catalog(),
            )
        return captured[0]

    def test_critic_prompt_static_text_precedes_the_per_request_id_list(self) -> None:
        a = self._critic_prompt(0)
        b = self._critic_prompt(1)
        self.assertNotEqual(a, b)
        marker = " For this candidate the complete allowed id set is: "
        self.assertEqual(a.count(marker.strip()), 1)
        head_a, tail_a = a.split(marker)
        head_b, _tail_b = b.split(marker)
        self.assertEqual(head_a, head_b)
        self.assertTrue(tail_a.startswith("[") and tail_a.endswith("]."), tail_a[-40:])
        self.assertIn("Treat target_semantics and family_event_evidence", head_a)

    def test_adjudicator_prompt_places_its_id_list_last(self) -> None:
        source = (ROOT / "decision_pipeline.py").read_text(encoding="utf-8")
        start = source.index("adjudicator_prompt = (")
        block = source[start : source.index("\n    )\n", start)]
        self.assertTrue(
            block.rstrip().endswith('f" For this candidate the complete allowed id set is: {adjudicator_allowed_ids}."'),
            block[-200:],
        )


# ---------------------------------------------------------------------------
# 4. JSON enforcement
# ---------------------------------------------------------------------------


class JsonEnforcementTests(unittest.TestCase):
    def test_muse_request_uses_native_strict_json_schema(self) -> None:
        routed = _routed()
        _call(routed)
        fmt = routed._muse.double.calls[0]["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertEqual(fmt["name"], "_Probe")
        self.assertEqual(set(fmt["schema"]["required"]), {"decision_state", "llm_quality_score"})

    def test_invalid_output_can_never_reach_the_consumer(self) -> None:
        for bad in (SCHEMA_INVALID, MALFORMED, ""):
            routed = _routed(muse=bad, muse_fallback=bad, secondary=bad, luna=bad)
            with self.assertRaises(ProviderCallError):
                _call(routed)


# ---------------------------------------------------------------------------
# 5. Accounting
# ---------------------------------------------------------------------------


class AccountingTests(unittest.TestCase):
    def test_expected_muse_cost_uses_published_categories(self) -> None:
        categories = acct.usage_categories(
            {
                "usage": {
                    "input_tokens": 1_000_000,
                    "input_tokens_details": {"cached_tokens": 250_000},
                    "output_tokens": 500_000,
                    "output_tokens_details": {"reasoning_tokens": 400_000},
                }
            }
        )
        self.assertEqual(categories["uncached_input_tokens"], 750_000)
        usd = acct.expected_go_usage_usd(MUSE, categories)
        # 0.75*0.10 + 0.25*0.002 + 0.5*0.20 ; reasoning is inside output, never added
        self.assertAlmostEqual(usd["standard"], 0.075 + 0.0005 + 0.10, places=9)

    def test_deepseek_reports_both_published_variants(self) -> None:
        categories = acct.usage_categories({"usage": {"input_tokens": 1_000_000, "output_tokens": 0}})
        usd = acct.expected_go_usage_usd(DEEPSEEK, categories)
        self.assertEqual(set(usd), {"off_peak", "peak"})
        self.assertAlmostEqual(usd["off_peak"], 0.15)
        self.assertAlmostEqual(usd["peak"], 0.30)

    def test_unexposed_usage_is_not_a_fabricated_zero(self) -> None:
        self.assertIsNone(acct.expected_go_usage_usd(MUSE, acct.usage_categories(None)))

    def test_schema_invalid_attempt_is_recorded_with_its_billed_usage(self) -> None:
        ledger: list[dict] = []
        routed = _routed(muse=SCHEMA_INVALID, secondary_enable=False, ledger=ledger)
        _call(routed)
        muse_row = next(r for r in ledger if r.get("model") == MUSE)
        self.assertEqual(muse_row["outcome"], "schema_invalid")
        self.assertEqual(muse_row["billing_state"], "billed_usage_reported")
        self.assertEqual(muse_row["cached_read_tokens"], 6513)
        self.assertIsNotNone(muse_row["expected_go_usage_usd"])
        self.assertEqual(muse_row["reasoning_effort_sent"], "high")
        self.assertTrue(muse_row["schema_strict"])

    def test_records_never_contain_credentials(self) -> None:
        ledger: list[dict] = []
        routed = _routed(muse=_Http500(), secondary_enable=False, ledger=ledger)
        _call(routed)
        dumped = json.dumps(ledger)
        self.assertNotIn(SECRET_OPENCODE, dumped)
        self.assertNotIn(SECRET_OPENAI, dumped)
        self.assertNotIn("Authorization", dumped)
        self.assertNotIn("api_key", dumped)

    def test_a_failing_observer_cannot_affect_the_call(self) -> None:
        routed = _routed()

        def _boom(_record):
            raise RuntimeError("ledger down")

        for leg in (routed._muse, routed._muse_fallback, routed._secondary, routed._fallback):
            leg.attempt_observer = _boom
        routed.call_observer = _boom
        self.assertEqual(_call(routed).actual_model, MUSE)
        self.assertEqual(_counts(routed), (1, 0, 0))

    def test_unobserved_transports_send_byte_identical_requests(self) -> None:
        plain = _routed()
        observed = _routed(ledger=[])
        _call(plain, 3)
        _call(observed, 3)
        self.assertEqual(plain._muse.double.calls[0], observed._muse.double.calls[0])

    def test_summary_computes_token_weighted_cache_ratio(self) -> None:
        ledger: list[dict] = []
        routed = _routed(ledger=ledger)
        for i in range(4):
            _call(routed, i)
        summary = acct.summarize_attempts(ledger)
        muse = summary["models"][MUSE]
        self.assertEqual(summary["logical_calls"], 4)
        self.assertEqual(muse["http_attempts"], 4)
        self.assertAlmostEqual(muse["token_weighted_cache_read_ratio"], 6513 / 37000, places=6)
        self.assertEqual(muse["distinct_sessions"], 1)
        self.assertEqual(summary["luna_fallback_http_attempts"], 0)

    def test_cache_ratio_counts_cache_writes_as_uncached_input(self) -> None:
        rows = [
            {
                "ts_utc": "2026-09-13T12:00:00+00:00",
                "provider_mode": "REMOTE_API",
                "model": LUNA,
                "request_id": "r",
                "input_tokens": 67584,
                "cached_input_tokens": 4999,
                "output_tokens": 10,
                "raw_usage": {"input_tokens_details": {"cached_tokens": 4999, "cache_write_tokens": 62582}},
            }
        ]
        luna = acct.summarize_legacy_usage(rows)["models"][LUNA]
        self.assertAlmostEqual(luna["token_weighted_cache_read_ratio"], 4999 / 67584, places=4)

    def test_ledger_writer_swallows_io_failures(self) -> None:
        sink = acct.OpenCodeGoAttemptLedger(path=Path("Z:/definitely/not/writable/ledger.ndjson"))
        with mock.patch.dict("os.environ", {"AI_USAGE_LOG_ENABLE": "true"}):
            sink.record({"event": "x"})
        self.assertEqual(sink.write_failures, 1)


# ---------------------------------------------------------------------------
# 6. Gate wiring
# ---------------------------------------------------------------------------


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "AI_PROVIDER_SELECT": "opencode",
        "OPENCODE_GO_API_KEY": SECRET_OPENCODE,
        "OPENAI_API_KEY": SECRET_OPENAI,
    }
    base.update(overrides)
    return base


class GateWiringTests(unittest.TestCase):
    def test_default_session_scope_is_prompt_prefix(self) -> None:
        cfg = ai_gate.AIGateRuntimeConfig.from_env(_env())
        self.assertEqual(cfg.opencode_session_scope, "prompt_prefix")
        self.assertEqual(cfg.safe_log_dict()["opencode_session_scope"], "prompt_prefix")

    def test_invalid_session_scope_warns_and_uses_default(self) -> None:
        cfg = ai_gate.AIGateRuntimeConfig.from_env(_env(OPENCODE_SESSION_SCOPE="sticky"))
        self.assertEqual(cfg.opencode_session_scope, "prompt_prefix")
        self.assertIn("OPENCODE_SESSION_SCOPE=invalid", cfg.validation_warnings)

    def test_built_provider_instruments_every_leg_and_passes_the_scope(self) -> None:
        cfg = ai_gate.AIGateRuntimeConfig.from_env(_env(OPENCODE_SESSION_SCOPE="request"))
        with mock.patch.dict("os.environ", {"OPENCODE_GO_API_KEY": SECRET_OPENCODE, "OPENAI_API_KEY": SECRET_OPENAI}):
            provider = ai_gate._build_ai_provider(cfg)
        self.assertIsInstance(provider, OpenCodeRoutedProvider)
        self.assertIsInstance(provider.call_observer, acct.OpenCodeGoAttemptLedger)
        for leg in (provider._muse, provider._muse_fallback, provider._secondary, provider._fallback):
            self.assertIs(leg.attempt_observer, provider.call_observer)
        self.assertEqual(provider._muse.session_scope, "request")
        self.assertEqual(provider._muse_fallback.session_scope, "request")
        self.assertEqual(provider._secondary.session_scope, "request")

    def test_openai_selection_is_not_instrumented(self) -> None:
        cfg = ai_gate.AIGateRuntimeConfig.from_env(
            {"AI_PROVIDER_SELECT": "openai_remote", "OPENAI_API_KEY": SECRET_OPENAI}
        )
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": SECRET_OPENAI}):
            provider = ai_gate._build_ai_provider(cfg)
        self.assertIsInstance(provider, RemoteAPIProvider)
        self.assertIsNone(provider.attempt_observer)


if __name__ == "__main__":
    unittest.main()
