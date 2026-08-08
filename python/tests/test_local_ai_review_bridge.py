"""Contract tests for the Local AI Review Bridge as PO3's sole AI transport.

The bridge is a loopback OpenAI-compatible *transport* in front of one browser
conversation.  Nothing about PO3's decision architecture changes; what changes
is that the model answer now arrives from a human-reviewed browser session over
``http://127.0.0.1:1234/v1``.

Two properties of that transport are different from a local GPU server and are
the reason these tests exist:

* it can legitimately take minutes to answer, so the absolute request deadline
  has to bound it exactly as it bounds the remote API.  Before the fix
  ``LocalOpenAICompatibleProvider.generate_structured`` ignored
  ``request_metadata["deadline"]`` entirely: it handed the SDK its *configured*
  180s timeout, never checked the remaining budget, and never emitted
  ``[provider_deadline_exceeded]``.  With a 180s terminal window and a 15s write
  margin that guaranteed the provider could outlive the whole MT5 wait and leave
  no time to write an authoritative response;
* it is backed by exactly one conversation, so two overlapping generations would
  interleave into the same chat.

The server used here is a stub that mirrors ``bridge/openai_compat.py``'s HTTP
contract (bearer auth, ``/models`` registry, the reserved non-trading capability
probe, strict JSON, JSON Schema enforcement, 429/504) so the *real*
``LocalOpenAICompatibleProvider`` runs against a real socket.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ai_gate  # noqa: E402
from ai_provider import (  # noqa: E402
    LOCAL_HEALTHCHECK_MAX_TIMEOUT_SEC,
    PROVIDER_CAPABILITY_PROBE_ID,
    PROVIDER_MODE_LOCAL,
    LocalOpenAICompatibleProvider,
    ProviderCallError,
    UnavailableProvider,
    endpoint_class,
)
from provider_deadline import DeadlinePolicy, RequestDeadline  # noqa: E402
from structured_models import StrictStructuredModel  # noqa: E402


BRIDGE_MODEL = "chatgpt-browser-review"
BRIDGE_KEY = "bridge-key-for-test-only-not-a-secret"


class _ProbeSchema(StrictStructuredModel):
    ok: bool


class _ReviewSchema(StrictStructuredModel):
    verdict: str


# ---------------------------------------------------------------------------
# Bridge-shaped stub server
# ---------------------------------------------------------------------------


class BridgeState:
    """Everything the stub bridge records or is scripted to do."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.api_key = BRIDGE_KEY
        self.models: list[str] = [BRIDGE_MODEL]
        # Raw text the "human reviewer" pastes back, per generation call.
        # ``None`` means "never answered" and exercises the deadline path.
        self.scripted: list[str | None] = []
        self.default_reply: str | None = '{"verdict":"PASS"}'
        self.review_latency_sec: float = 0.0
        self.max_pending_jobs = 3

        self.generation_calls: list[dict[str, Any]] = []
        self.probe_calls: list[dict[str, Any]] = []
        self.model_list_calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    def next_reply(self) -> str | None:
        with self.lock:
            if self.scripted:
                return self.scripted.pop(0)
        return self.default_reply

    def enter(self, record: dict[str, Any]) -> int:
        with self.lock:
            self.generation_calls.append(record)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            return self.in_flight

    def leave(self) -> None:
        with self.lock:
            self.in_flight -= 1


def _strict_json_object(text: str) -> dict[str, Any]:
    """Same strictness the real bridge applies before answering PO3."""

    duplicates: list[str] = []

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                duplicates.append(key)
            out[key] = value
        return out

    decoder = json.JSONDecoder(object_pairs_hook=_pairs)
    value, end = decoder.raw_decode(text.lstrip())
    if text.lstrip()[end:].strip():
        raise ValueError("trailing_json_content")
    if duplicates:
        raise ValueError("duplicate_json_keys")
    if not isinstance(value, dict):
        raise ValueError("root_not_object")
    return value


def _extract_request_id(messages: list[dict[str, Any]]) -> str:
    """Mirror of ``bridge.prompting.extract_request_id``."""

    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        try:
            obj = json.loads(str(message.get("content") or ""))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        identity = obj.get("identity")
        for value in (
            obj.get("request_id"),
            obj.get("id"),
            identity.get("request_id") if isinstance(identity, dict) else None,
        ):
            text = str(value or "").strip()
            if text:
                return text
    return ""


class _BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:  # keep test output clean
        return

    @property
    def state(self) -> BridgeState:
        return self.server.state  # type: ignore[attr-defined]

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        expected = f"Bearer {self.state.api_key}"
        if self.headers.get("Authorization") != expected:
            self._send(401, {"error": {"message": "invalid_bridge_api_key"}})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.endswith("/models"):
            self._send(404, {"error": {"message": "no_route"}})
            return
        if not self._authorised():
            return
        with self.state.lock:
            self.state.model_list_calls += 1
            models = list(self.state.models)
        self._send(
            200,
            {
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "owned_by": "local-ai-review-bridge"}
                    for name in models
                ],
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.endswith("/chat/completions"):
            self._send(404, {"error": {"message": "no_route"}})
            return
        if not self._authorised():
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:
            self._send(400, {"error": {"message": f"invalid_request_json:{exc}"}})
            return

        model = str(body.get("model") or "")
        with self.state.lock:
            known = model in self.state.models
        if not known:
            self._send(400, {"error": {"message": f"model_not_found:{model}"}})
            return

        messages = list(body.get("messages") or [])
        request_id = _extract_request_id(messages)
        schema = (
            ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")
            if isinstance(body.get("response_format"), dict)
            else None
        )

        # The reserved non-trading probe is answered locally.  It must never
        # occupy a browser review slot or reach the conversation.
        if request_id == PROVIDER_CAPABILITY_PROBE_ID:
            with self.state.lock:
                self.state.probe_calls.append({"model": model, "request_id": request_id})
            self._reply(model, json.dumps({"ok": True}), probe=True)
            return

        with self.state.lock:
            pending = self.state.in_flight
            limit = self.state.max_pending_jobs
        if pending >= limit:
            self._send(429, {"error": {"message": "bridge_queue_full"}})
            return

        self.state.enter(
            {
                "model": model,
                "request_id": request_id,
                "schema_name": (
                    ((body.get("response_format") or {}).get("json_schema") or {}).get("name", "")
                    if isinstance(body.get("response_format"), dict)
                    else ""
                ),
                "started_at": time.monotonic(),
            }
        )
        try:
            reply = self.state.next_reply()
            latency = self.state.review_latency_sec
            if latency > 0:
                time.sleep(latency)
            if reply is None:
                # The reviewer never answered: hold the connection until the
                # caller's own deadline closes it, exactly like the real bridge
                # waiting on its 180s job deadline.
                time.sleep(30.0)
                self._send(504, {"error": {"message": "review_deadline_exceeded"}})
                return
            try:
                obj = _strict_json_object(reply)
                if isinstance(schema, dict):
                    _validate_against_schema(obj, schema)
            except Exception as exc:
                self._send(400, {"error": {"message": f"structured_response_invalid:{exc}"}})
                return
            self._reply(model, json.dumps(obj, separators=(",", ":")))
        finally:
            self.state.leave()

    def _reply(self, model: str, content: str, *, probe: bool = False) -> None:
        self._send(
            200,
            {
                "id": "chatcmpl-probe" if probe else "chatcmpl-review",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "system_fingerprint": "local-human-reviewed-browser-bridge-v1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


def _validate_against_schema(obj: dict[str, Any], schema: dict[str, Any]) -> None:
    """Minimal strict-object check standing in for Draft202012Validator.

    Only the parts PO3 relies on are enforced: required keys must be present and
    additional keys must be refused when ``additionalProperties`` is false.
    """

    required = schema.get("required")
    if isinstance(required, list):
        missing = [str(key) for key in required if str(key) not in obj]
        if missing:
            raise ValueError("missing_required:" + ",".join(missing))
    properties = schema.get("properties")
    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        extra = [key for key in obj if key not in properties]
        if extra:
            raise ValueError("additional_properties:" + ",".join(sorted(extra)))


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], state: BridgeState) -> None:
        super().__init__(addr, _BridgeHandler)
        self.state = state


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# Shared test scaffolding
# ---------------------------------------------------------------------------


def bridge_env() -> dict[str, str]:
    """The documented Local AI Review Bridge deployment settings."""

    return {
        "AI_USE_REMOTE_API": "false",
        "LOCAL_AI_BASE_URL": "http://127.0.0.1:1234/v1",
        "LOCAL_AI_API_KEY": BRIDGE_KEY,
        "LOCAL_AI_MODEL": BRIDGE_MODEL,
        "LOCAL_AI_ANALYST_MODEL": BRIDGE_MODEL,
        "LOCAL_AI_CRITIC_MODEL": BRIDGE_MODEL,
        "LOCAL_AI_ADJUDICATOR_MODEL": BRIDGE_MODEL,
        "LOCAL_AI_FALLBACK_MODELS": "",
        "LOCAL_AI_HEALTHCHECK_PATH": "/models",
        "LOCAL_AI_REQUIRE_JSON_SCHEMA": "true",
        "LOCAL_AI_PARALLELISM": "1",
        "LOCAL_AI_MAX_RETRIES": "0",
        "LOCAL_AI_TIMEOUT_SEC": "180",
        "AI_MT5_TERMINAL_TIMEOUT_SEC": "180",
        "AI_RESPONSE_WRITE_MARGIN_SEC": "15",
        "AI_MIN_PROVIDER_ATTEMPT_SEC": "10",
        "AI_LIVE_CANDIDATE_BUDGET": "3",
        "AI_ENABLE_SNAPSHOTS": "false",
        "AI_HARD_PRE_GATE_BEFORE_OPENAI": "true",
        "AI_REQUIRE_RUNTIME_INPUTS_LIVE": "true",
        "AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE": "true",
        "AI_SHADOW_COMPARE_PROVIDERS": "false",
    }


class BridgeTestCase(unittest.TestCase):
    """One stub bridge on a real loopback port per test."""

    def setUp(self) -> None:
        self.state = BridgeState()
        self.port = _free_port()
        self.server = _BridgeServer(("127.0.0.1", self.port), self.state)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.logs: list[str] = []

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def provider(
        self,
        *,
        api_key: str = BRIDGE_KEY,
        model: str = BRIDGE_MODEL,
        timeout_sec: float = 180.0,
        max_retries: int = 0,
        parallelism: int = 1,
    ) -> LocalOpenAICompatibleProvider:
        return LocalOpenAICompatibleProvider(
            base_url=self.base_url(),
            api_key=api_key,
            analyst_model=model,
            critic_model=model,
            adjudicator_model=model,
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            max_output_tokens=4096,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=False,
            require_json_schema=True,
            parallelism=parallelism,
            context_budget_tokens=131072,
            circuit_failure_threshold=99,
            circuit_cooldown_sec=0.0,
            log=self.logs.append,
        )

    def call(
        self,
        provider: LocalOpenAICompatibleProvider,
        *,
        role: str = "critic",
        deadline: RequestDeadline | None = None,
        request_id: str = "bridge-request-1",
        schema: type = _ReviewSchema,
    ):
        metadata: dict[str, Any] = {
            "request_id": request_id,
            "request_identity_hash": "a" * 64,
            "decision_schema_version": "test",
            "prompt_contract_version": "test",
        }
        if deadline is not None:
            metadata["deadline"] = deadline
        return provider.generate_structured(
            role=role,
            system_prompt="Return strict JSON only.",
            evidence={"request_id": request_id, "candidate": "A"},
            response_schema=schema,
            request_metadata=metadata,
        )

    def assertLogged(self, marker: str) -> None:
        self.assertTrue(
            any(marker in line for line in self.logs),
            f"expected {marker!r} in provider logs, got: {self.logs}",
        )

    def assertNotLogged(self, marker: str) -> None:
        self.assertFalse(
            any(marker in line for line in self.logs),
            f"unexpected {marker!r} in provider logs",
        )


def _deadline(*, elapsed_sec: float = 0.0, terminal_sec: float = 180.0) -> RequestDeadline:
    """A deterministic deadline that has already burned ``elapsed_sec``."""

    policy = DeadlinePolicy.derive(
        mt5_terminal_timeout_sec=terminal_sec,
        response_write_margin_sec=15.0,
        min_attempt_sec=10.0,
    )
    return RequestDeadline.start("bridge-request-1", policy, now=time.monotonic() - elapsed_sec)


# ---------------------------------------------------------------------------
# A / C / D  -- startup health
# ---------------------------------------------------------------------------


class BridgeHealthTests(BridgeTestCase):
    def test_a_healthy_bridge_reports_healthy_and_probe_costs_no_review(self) -> None:
        provider = self.provider()
        health = provider.healthcheck(probe_structured=True)

        self.assertTrue(health.healthy, health.reason)
        self.assertTrue(health.model_available)
        self.assertTrue(health.structured_output_available)
        self.assertEqual(health.model_id, BRIDGE_MODEL)
        self.assertEqual(health.provider_mode, PROVIDER_MODE_LOCAL)
        self.assertEqual(health.endpoint_class, "loopback")
        # The probe was answered by the bridge itself and never became a
        # queued browser review.
        self.assertEqual(len(self.state.probe_calls), 1)
        self.assertEqual(self.state.generation_calls, [])

    def test_a_probe_identifies_itself_inside_the_wire_payload(self) -> None:
        """``request_metadata`` never crosses the wire; the evidence must.

        Before the fix the probe was indistinguishable from a real decision
        request at the HTTP boundary, so the bridge queued it for a human and
        startup blocked until the job deadline expired.
        """

        provider = self.provider()
        provider.healthcheck(probe_structured=True)
        self.assertEqual(
            self.state.probe_calls[0]["request_id"], PROVIDER_CAPABILITY_PROBE_ID
        )

    def test_b_bridge_unavailable_fails_closed(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        provider = self.provider()
        health = provider.healthcheck(probe_structured=True)

        self.assertFalse(health.healthy)
        self.assertFalse(health.model_available)
        self.assertFalse(health.structured_output_available)
        self.assertTrue(health.reason.startswith("local_healthcheck_failed:"))
        self.assertIn("unreachable", health.reason)

    def test_b_generation_against_a_dead_bridge_raises_and_never_approves(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        provider = self.provider(timeout_sec=10.0)
        with self.assertRaises(ProviderCallError) as ctx:
            self.call(provider)
        self.assertIn(
            ctx.exception.category,
            {"PROVIDER_TRANSPORT_ERROR", "STRUCTURED_RESPONSE_INVALID"},
        )

    def test_c_wrong_bridge_key_reports_authentication_failure(self) -> None:
        provider = self.provider(api_key="wrong-key")
        health = provider.healthcheck(probe_structured=True)

        self.assertFalse(health.healthy)
        self.assertIn("authentication_failed", health.reason)
        self.assertIn("http_401", health.reason)

    def test_c_wrong_bridge_key_never_leaks_the_key_into_the_reason(self) -> None:
        provider = self.provider(api_key="wrong-key")
        health = provider.healthcheck(probe_structured=True)
        self.assertNotIn("wrong-key", health.reason)
        self.assertNotIn(BRIDGE_KEY, health.reason)
        for line in self.logs:
            self.assertNotIn(BRIDGE_KEY, line)
            self.assertNotIn("wrong-key", line)

    def test_d_missing_model_fails_closed_without_a_generation_call(self) -> None:
        self.state.models = ["some-other-conversation"]
        provider = self.provider()
        health = provider.healthcheck(probe_structured=True)

        self.assertFalse(health.healthy)
        self.assertEqual(health.reason, "configured_local_model_not_available")
        self.assertEqual(self.state.generation_calls, [])
        self.assertEqual(self.state.probe_calls, [])

    def test_health_timeout_is_bounded_below_the_generation_timeout(self) -> None:
        """A model listing is not a generation and must not be able to burn one.

        ``healthcheck`` re-runs while requests are in flight, so binding it to
        the 180s browser-generation timeout let a single unresponsive bridge
        consume an entire MT5 terminal window.
        """

        provider = self.provider(timeout_sec=180.0)
        self.assertLessEqual(
            provider._health_timeout_sec(), LOCAL_HEALTHCHECK_MAX_TIMEOUT_SEC
        )
        self.assertLess(provider._health_timeout_sec(), provider.timeout_sec)

    def test_unhealthy_provider_blocks_live_trading_authority(self) -> None:
        """Requirement 11: no silent fallback when startup health fails."""

        provider = UnavailableProvider("bridge_unreachable")
        health = provider.healthcheck()
        self.assertFalse(health.healthy)
        self.assertFalse(provider.identity()["configuration_valid"])
        with self.assertRaises(RuntimeError):
            provider.generate_structured(role="analyst")


# ---------------------------------------------------------------------------
# E / F -- valid and invalid model output
# ---------------------------------------------------------------------------


class BridgeStructuredOutputTests(BridgeTestCase):
    def test_e_valid_structured_reply_round_trips_through_the_real_provider(self) -> None:
        self.state.scripted = ['{"verdict":"PASS"}']
        provider = self.provider()
        result = self.call(provider)

        self.assertEqual(result.parsed.verdict, "PASS")
        self.assertEqual(result.provider_mode, PROVIDER_MODE_LOCAL)
        self.assertEqual(result.provider_id, "local_openai_compatible")
        self.assertEqual(result.endpoint_class, "loopback")
        self.assertEqual(result.actual_model, BRIDGE_MODEL)
        self.assertEqual(result.transport_retry_count, 0)
        self.assertEqual(result.schema_retry_count, 0)
        self.assertLogged("[provider_call_completed]")
        self.assertLogged("quality_tier=FULL_STRUCTURED")

    def test_e_strict_json_schema_is_sent_to_the_bridge(self) -> None:
        """The bridge is a transport; PO3 keeps sending its own strict schema."""

        captured: dict[str, Any] = {}

        class _Completions:
            def create(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {
                    "model": kwargs["model"],
                    "choices": [{"message": {"content": '{"verdict":"PASS"}'}}],
                    "usage": {},
                }

        provider = self.provider()
        provider._client = type(
            "_C", (), {"chat": type("_Chat", (), {"completions": _Completions()})()}
        )()
        self.call(provider)

        response_format = captured["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(response_format["json_schema"]["name"], "_ReviewSchema")
        self.assertIn("properties", response_format["json_schema"]["schema"])

    def test_f_trailing_prose_after_the_json_is_rejected(self) -> None:
        self.state.scripted = ['{"verdict":"PASS"} hope this helps!']
        provider = self.provider()
        with self.assertRaises(ProviderCallError) as ctx:
            self.call(provider)
        self.assertEqual(ctx.exception.category, "PROVIDER_TRANSPORT_ERROR")
        self.assertIn("structured_response_invalid", str(ctx.exception))

    def test_f_duplicate_json_keys_are_rejected(self) -> None:
        self.state.scripted = ['{"verdict":"PASS","verdict":"BLOCK"}']
        provider = self.provider()
        with self.assertRaises(ProviderCallError):
            self.call(provider)

    def test_f_non_json_content_is_rejected_by_the_provider_itself(self) -> None:
        """A bridge that let prose through must still fail closed in PO3."""

        class _Completions:
            def create(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "model": BRIDGE_MODEL,
                    "choices": [{"message": {"content": "Sure! Here is my analysis."}}],
                    "usage": {},
                }

        provider = self.provider()
        provider._client = type(
            "_C", (), {"chat": type("_Chat", (), {"completions": _Completions()})()}
        )()
        with self.assertRaises(ProviderCallError) as ctx:
            self.call(provider)
        self.assertEqual(ctx.exception.category, "STRUCTURED_RESPONSE_INVALID")

    def test_f_duplicate_keys_from_the_bridge_are_rejected_by_the_provider(self) -> None:
        class _Completions:
            def create(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "model": BRIDGE_MODEL,
                    "choices": [
                        {"message": {"content": '{"verdict":"PASS","verdict":"BLOCK"}'}}
                    ],
                    "usage": {},
                }

        provider = self.provider()
        provider._client = type(
            "_C", (), {"chat": type("_Chat", (), {"completions": _Completions()})()}
        )()
        with self.assertRaises(ProviderCallError) as ctx:
            self.call(provider)
        self.assertEqual(ctx.exception.category, "STRUCTURED_RESPONSE_INVALID")

    def test_f_no_authoritative_result_is_produced_for_invalid_output(self) -> None:
        self.state.scripted = ["not json at all"]
        provider = self.provider()
        with self.assertRaises(ProviderCallError):
            self.call(provider)
        self.assertNotLogged("[provider_call_completed]")


# ---------------------------------------------------------------------------
# K -- the absolute deadline governs the browser transport
# ---------------------------------------------------------------------------


class BridgeDeadlineTests(BridgeTestCase):
    def test_k_sdk_receives_the_remaining_budget_not_the_configured_timeout(self) -> None:
        """The regression this whole change exists for.

        A 180s configured browser timeout inside a 165s Python response
        deadline must be narrowed to what is actually left, or the provider
        outlives the MT5 terminal window and no response can be written.
        """

        seen: list[dict[str, Any]] = []

        class _Client:
            def __init__(self) -> None:
                self.chat = type(
                    "_Chat",
                    (),
                    {
                        "completions": type(
                            "_Completions",
                            (),
                            {
                                "create": staticmethod(
                                    lambda **kwargs: {
                                        "model": kwargs["model"],
                                        "choices": [
                                            {"message": {"content": '{"verdict":"PASS"}'}}
                                        ],
                                        "usage": {},
                                    }
                                )
                            },
                        )()
                    },
                )()

            def with_options(self, **options: Any) -> "_Client":
                seen.append(options)
                return self

        provider = self.provider(timeout_sec=180.0)
        provider._client = _Client()
        self.call(provider, deadline=_deadline(elapsed_sec=60.0))

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["max_retries"], 0)
        # 180s terminal - 15s write margin = 165s python budget, 60s spent.
        self.assertLessEqual(seen[0]["timeout"], 105.0)
        self.assertGreater(seen[0]["timeout"], 100.0)
        self.assertLess(seen[0]["timeout"], 180.0)

    def test_k_full_budget_is_still_capped_by_the_python_response_deadline(self) -> None:
        seen: list[dict[str, Any]] = []

        class _Client:
            chat = type(
                "_Chat",
                (),
                {
                    "completions": type(
                        "_Completions",
                        (),
                        {
                            "create": staticmethod(
                                lambda **kwargs: {
                                    "model": kwargs["model"],
                                    "choices": [
                                        {"message": {"content": '{"verdict":"PASS"}'}}
                                    ],
                                    "usage": {},
                                }
                            )
                        },
                    )()
                },
            )()

            def with_options(self, **options: Any) -> "_Client":
                seen.append(options)
                return self

        provider = self.provider(timeout_sec=180.0)
        provider._client = _Client()
        self.call(provider, deadline=_deadline(elapsed_sec=0.0))

        self.assertLessEqual(
            seen[0]["timeout"],
            165.0,
            "the write margin must never be handed to the provider",
        )

    def test_k_exhausted_budget_refuses_to_start_a_browser_call(self) -> None:
        provider = self.provider()
        with self.assertRaises(ProviderCallError) as ctx:
            self.call(provider, deadline=_deadline(elapsed_sec=160.0))

        self.assertEqual(ctx.exception.category, "PROVIDER_DEADLINE_EXCEEDED")
        self.assertEqual(
            self.state.generation_calls,
            [],
            "no browser job may be created once the budget is spent",
        )
        self.assertLogged("[provider_deadline_exceeded]")
        self.assertLogged("late_result_action=quarantine")

    def test_k_deadline_contract_is_logged_before_the_call(self) -> None:
        self.state.scripted = ['{"verdict":"PASS"}']
        provider = self.provider()
        self.call(provider, deadline=_deadline())
        self.assertLogged("[provider_deadline]")
        self.assertLogged("python_deadline_ms=165000")
        self.assertLogged("write_margin_ms=15000")

    def test_k_late_arrival_cannot_become_authoritative(self) -> None:
        """The gate quarantines a result that lands after the deadline."""

        deadline = _deadline(elapsed_sec=0.0)
        self.assertFalse(deadline.expired())
        expired = _deadline(elapsed_sec=170.0)
        self.assertTrue(expired.expired())
        self.assertFalse(expired.can_start_attempt())

    def test_k_no_deadline_metadata_preserves_the_configured_timeout(self) -> None:
        """Offline record/cache callers pass no deadline and must not regress."""

        seen: list[dict[str, Any]] = []

        class _Client:
            chat = type(
                "_Chat",
                (),
                {
                    "completions": type(
                        "_Completions",
                        (),
                        {
                            "create": staticmethod(
                                lambda **kwargs: {
                                    "model": kwargs["model"],
                                    "choices": [
                                        {"message": {"content": '{"verdict":"PASS"}'}}
                                    ],
                                    "usage": {},
                                }
                            )
                        },
                    )()
                },
            )()

            def with_options(self, **options: Any) -> "_Client":
                seen.append(options)
                return self

        provider = self.provider(timeout_sec=180.0)
        provider._client = _Client()
        self.call(provider, deadline=None)
        self.assertEqual(seen[0]["timeout"], 180.0)
        self.assertNotLogged("[provider_deadline_exceeded]")


# ---------------------------------------------------------------------------
# L / M / N / O -- idempotency, isolation, serialization, candidate ceiling
# ---------------------------------------------------------------------------


class BridgeIdempotencyTests(BridgeTestCase):
    def test_l_zero_retries_means_one_browser_submission_per_call(self) -> None:
        """A transport retry would be a *second* message in the same chat."""

        self.state.scripted = ["not json", '{"verdict":"PASS"}']
        provider = self.provider(max_retries=0)
        with self.assertRaises(ProviderCallError):
            self.call(provider)
        self.assertEqual(
            len(self.state.generation_calls),
            1,
            "LOCAL_AI_MAX_RETRIES=0 must produce exactly one browser submission",
        )

    def test_l_configuration_clamps_local_retries_to_zero(self) -> None:
        env = bridge_env()
        config = ai_gate.AIGateRuntimeConfig.from_env(env)
        self.assertEqual(config.local_max_retries, 0)

    def test_l_sdk_never_adds_hidden_retries(self) -> None:
        captured: dict[str, Any] = {}

        def factory(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return object()

        provider = LocalOpenAICompatibleProvider(
            base_url=self.base_url(),
            api_key=BRIDGE_KEY,
            analyst_model=BRIDGE_MODEL,
            critic_model=BRIDGE_MODEL,
            adjudicator_model=BRIDGE_MODEL,
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=180.0,
            max_retries=0,
            max_output_tokens=4096,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=False,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=131072,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=60.0,
            log=self.logs.append,
            client_factory=factory,
        )
        provider._client_instance()
        self.assertEqual(captured["max_retries"], 0)
        self.assertEqual(captured["base_url"], self.base_url())

    def test_l_bridge_sees_the_po3_request_id_for_deduplication(self) -> None:
        self.state.scripted = ['{"verdict":"PASS"}']
        provider = self.provider()
        self.call(provider, request_id="po3-request-abc")
        self.assertEqual(self.state.generation_calls[0]["request_id"], "po3-request-abc")


class BridgeProviderIsolationTests(BridgeTestCase):
    def test_m_bridge_mode_builds_only_the_local_provider(self) -> None:
        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        self.assertIs(config.use_remote_api, False)
        self.assertTrue(config.provider_config_valid, config.provider_config_errors)

        with patch("ai_gate.RemoteAPIProvider") as remote_ctor, patch(
            "ai_gate.LocalOpenAICompatibleProvider"
        ) as local_ctor:
            ai_gate._build_ai_provider(config)
        remote_ctor.assert_not_called()
        local_ctor.assert_called_once()

    def test_m_remote_api_key_is_not_required_and_not_read(self) -> None:
        env = bridge_env()
        self.assertNotIn("OPENAI_API_KEY", env)
        config = ai_gate.AIGateRuntimeConfig.from_env(env)
        self.assertTrue(config.provider_config_valid, config.provider_config_errors)
        self.assertNotIn(
            "OPENAI_API_KEY=missing_remote", list(config.provider_config_errors)
        )

    def test_m_a_present_remote_key_is_still_ignored_in_bridge_mode(self) -> None:
        env = bridge_env()
        env["OPENAI_API_KEY"] = "sk-should-never-be-used"
        env["AI_GATE_MODEL"] = "gpt-5.4-nano"
        config = ai_gate.AIGateRuntimeConfig.from_env(env)

        self.assertIs(config.use_remote_api, False)
        self.assertEqual(config.model, BRIDGE_MODEL)
        self.assertEqual(config.expectancy_model, BRIDGE_MODEL)
        self.assertEqual(config.fallback_models, [])
        with patch("ai_gate.RemoteAPIProvider") as remote_ctor:
            provider = ai_gate._build_ai_provider(config)
        remote_ctor.assert_not_called()
        self.assertEqual(provider.provider_mode, PROVIDER_MODE_LOCAL)

    def test_m_no_cross_provider_fallback_model_is_configured(self) -> None:
        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        provider = ai_gate._build_ai_provider(config)
        self.assertEqual(provider.configured_models("analyst"), (BRIDGE_MODEL,))
        self.assertEqual(provider.configured_models("critic"), (BRIDGE_MODEL,))
        self.assertEqual(provider.configured_models("adjudicator"), (BRIDGE_MODEL,))

    def test_m_shadow_comparison_cannot_open_a_second_transport(self) -> None:
        """A local-authoritative run must never build a remote shadow."""

        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        with patch.object(ai_gate, "AI_CONFIG", config), patch.object(
            ai_gate, "AI_PROVIDER", ai_gate._build_ai_provider(config)
        ), patch.object(ai_gate, "SHADOW_AI_PROVIDER", None):
            shadow = ai_gate._build_non_selected_shadow_provider()
        self.assertIsInstance(shadow, UnavailableProvider)
        self.assertIn("local_privacy_contract", shadow.reason)

    def test_m_bridge_endpoint_must_be_loopback(self) -> None:
        self.assertEqual(endpoint_class("http://127.0.0.1:1234/v1"), "loopback")
        env = bridge_env()
        env["LOCAL_AI_BASE_URL"] = "http://198.51.100.7:1234/v1"
        config = ai_gate.AIGateRuntimeConfig.from_env(env)
        self.assertFalse(config.provider_config_valid)
        self.assertIn("LOCAL_AI_BASE_URL=non_loopback_without_ack", config.provider_config_errors)
        self.assertIsInstance(ai_gate._build_ai_provider(config), UnavailableProvider)

    def test_m_snapshots_stay_disabled(self) -> None:
        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        self.assertFalse(config.enable_snapshots)


class BridgeSerializationTests(BridgeTestCase):
    def test_n_one_browser_conversation_sees_one_generation_at_a_time(self) -> None:
        self.state.default_reply = '{"verdict":"PASS"}'
        self.state.review_latency_sec = 0.08
        provider = self.provider()

        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                self.call(provider, request_id=f"bridge-request-{index}")
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [], f"concurrent calls failed: {errors}")
        self.assertEqual(len(self.state.generation_calls), 4)
        self.assertEqual(
            self.state.max_in_flight,
            1,
            "two generations overlapped inside the single browser conversation",
        )

    def test_n_configuration_pins_provider_parallelism_to_one(self) -> None:
        env = bridge_env()
        env["LOCAL_AI_PARALLELISM"] = "4"
        config = ai_gate.AIGateRuntimeConfig.from_env(env)
        self.assertEqual(
            config.local_parallelism,
            1,
            "LOCAL_AI_PARALLELISM is hard-clamped; a browser chat cannot interleave",
        )

    def test_n_all_three_roles_share_the_one_serialized_transport(self) -> None:
        self.state.default_reply = '{"verdict":"PASS"}'
        self.state.review_latency_sec = 0.05
        provider = self.provider()

        errors: list[BaseException] = []

        def worker(role: str) -> None:
            try:
                self.call(provider, role=role, request_id=f"bridge-{role}")
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(role,))
            for role in ("analyst", "critic", "adjudicator")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(self.state.max_in_flight, 1)
        # Roles are preserved: each still made its own call with its own schema.
        self.assertEqual(len(self.state.generation_calls), 3)

    def test_n_queued_role_call_respects_the_shared_absolute_deadline(self) -> None:
        """Time spent queueing on the semaphore is deadline time already spent."""

        provider = self.provider()
        with self.assertRaises(ProviderCallError) as ctx:
            self.call(provider, role="adjudicator", deadline=_deadline(elapsed_sec=161.0))
        self.assertEqual(ctx.exception.category, "PROVIDER_DEADLINE_EXCEEDED")
        self.assertEqual(self.state.generation_calls, [])


class BridgeCandidateBudgetTests(unittest.TestCase):
    def test_o_live_candidate_ceiling_is_three(self) -> None:
        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        self.assertEqual(config.live_candidate_budget, 3)

    def test_o_candidates_beyond_the_ceiling_are_deferred_not_parallelised(self) -> None:
        candidates = [
            {"candidate_index": index, "setup_family": f"FAM{index % 3}"}
            for index in range(6)
        ]
        enriched = [{"rule_score": 10.0 - index} for index in range(6)]
        chosen, deferred, reason = ai_gate.select_live_candidate_cohort(
            candidates, enriched, budget=3
        )
        self.assertEqual(len(chosen), 3)
        self.assertEqual(len(deferred), 3)
        self.assertEqual(reason, "live_latency_budget_rule_score_family_diverse")
        self.assertEqual(sorted(chosen + deferred), list(range(6)))

    def test_o_candidate_budget_is_not_a_transport_concurrency_setting(self) -> None:
        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        self.assertEqual(config.live_candidate_budget, 3)
        self.assertEqual(config.local_parallelism, 1)


# ---------------------------------------------------------------------------
# Response-folder ownership
# ---------------------------------------------------------------------------


class ResponseFolderOwnershipTests(unittest.TestCase):
    """The bridge must never write PO3's authoritative responses."""

    BRIDGE_ROOT = REPO_ROOT.parent / "MT5 AI Review Bridge"

    def _bridge_sources(self) -> list[Path]:
        if not self.BRIDGE_ROOT.is_dir():
            self.skipTest("bridge package not present in this checkout")
        return sorted((self.BRIDGE_ROOT / "bridge").glob("*.py"))

    def test_no_bridge_source_references_the_po3_bus(self) -> None:
        offenders = [
            path.name
            for path in self._bridge_sources()
            if "PO3_AI_BUS" in path.read_text(encoding="utf-8")
            and "must_not_target_po3_bus" not in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            offenders,
            [],
            "only the boundary guard may name the PO3 bus",
        )

    def test_bridge_config_refuses_a_po3_bus_folder_target(self) -> None:
        sources = self._bridge_sources()
        config = next((p for p in sources if p.name == "config.py"), None)
        self.assertIsNotNone(config, "bridge/config.py is missing")
        text = config.read_text(encoding="utf-8")
        self.assertIn("assert_not_po3_bus", text)
        self.assertIn("PO3BusBoundaryError", text)

    def test_ai_gate_is_the_only_authoritative_response_writer(self) -> None:
        text = (REPO_ROOT / "ai_gate.py").read_text(encoding="utf-8", errors="replace")
        # One atomic write of ``resp_dir / f"{req_id}.json"`` on the success
        # path, one explicit error-response writer, and one idempotent reuse.
        self.assertIn("atomic_write_json(resp_path, resp, encoding=RESP_ENCODING)", text)
        self.assertIn("[response_written]", text)
        self.assertIn("claim_terminal", text)


# ---------------------------------------------------------------------------
# Deployment settings
# ---------------------------------------------------------------------------


class BridgeDeploymentSettingsTests(unittest.TestCase):
    def test_deadline_arithmetic_matches_the_documented_deployment(self) -> None:
        config = ai_gate.AIGateRuntimeConfig.from_env(bridge_env())
        policy = DeadlinePolicy.derive(
            mt5_terminal_timeout_sec=config.mt5_terminal_timeout_sec,
            response_write_margin_sec=config.response_write_margin_sec,
            min_attempt_sec=config.min_provider_attempt_sec,
        )
        self.assertEqual(policy.mt5_terminal_timeout_ms, 180_000)
        self.assertEqual(policy.response_write_margin_ms, 15_000)
        self.assertEqual(policy.python_deadline_ms, 165_000)
        self.assertLess(
            policy.python_deadline_ms / 1000.0,
            config.local_timeout_sec,
            "the configured browser timeout must be narrowed by the deadline",
        )

    def test_runtime_env_file_selects_the_bridge(self) -> None:
        env_path = REPO_ROOT / ".env"
        if not env_path.is_file():
            self.skipTest("no private runtime .env in this checkout")
        values = {}
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        self.assertEqual(values.get("AI_USE_REMOTE_API"), "false")
        self.assertEqual(values.get("LOCAL_AI_BASE_URL"), "http://127.0.0.1:1234/v1")
        self.assertEqual(values.get("LOCAL_AI_ANALYST_MODEL"), BRIDGE_MODEL)
        self.assertEqual(values.get("LOCAL_AI_MAX_RETRIES"), "0")
        self.assertEqual(values.get("LOCAL_AI_PARALLELISM"), "1")
        self.assertEqual(values.get("AI_LIVE_CANDIDATE_BUDGET"), "3")
        self.assertEqual(values.get("AI_ENABLE_SNAPSHOTS"), "false")
        self.assertEqual(values.get("AI_MT5_TERMINAL_TIMEOUT_SEC"), "180")
        self.assertEqual(values.get("AI_RESPONSE_WRITE_MARGIN_SEC"), "15")
        self.assertEqual(
            endpoint_class(values.get("LOCAL_AI_BASE_URL", "")), "loopback"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
