"""Focused offline tests for ``python/tools/opencode_reasoning_benchmark.py`` (WP1).

BEN-003/BEN-005/BEN-007 focused coverage.  FULLY OFFLINE: every provider
interaction runs through the tool's own dry capture fake (records the wire
kwargs of ``responses.create`` and raises a non-retryable sentinel), so no
network, no credentials, and no live bus/data/log writes are possible.  The
fake-provider and payload conventions are copied verbatim from
``tests/test_provider_neutral_ai.py`` (the mocked local response e2e) and
``tests/test_opencode_go_cost_correctness.py`` (the wire-capture recipe).
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai_gate
import ai_provider
from ai_provider import ProviderCallError
from compatibility_manifest import compatibility_manifest_hash
from provider_deadline import RequestDeadline
from structured_models import ModelAIGateOutput
from tests.test_decision_integrity import candidate as integrity_candidate

import tools.opencode_reasoning_benchmark as bench


class _RetrievalStore:
    """Copy of the existing test convention (test_provider_neutral_ai)."""

    def __init__(self, retrieval_hash: str) -> None:
        self.retrieval_hash = retrieval_hash

    def retrieve_analogues(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(retrieval_hash=self.retrieval_hash)

    def record_pending_decision(self, **kwargs: object) -> bool:
        return True


def _e2e_payload() -> dict:
    """Copy of the proven mocked-local e2e payload recipe from
    test_provider_neutral_ai.test_mocked_local_response_uses_real_evidence_schema_and_consensus_path."""
    candidate = integrity_candidate()
    return {
        "id": "bench-dry-request",
        "session_id": "bench-dry-session",
        "request_nonce": "bench-dry-nonce",
        "contract_manifest_hash": compatibility_manifest_hash(),
        "request_identity_version": ai_gate.AI_REQUEST_IDENTITY_VERSION,
        "request_identity_hash": "REQUESTIDENTITYBENCH1234567890",
        "request_created_sim_time": 1780000000,
        "request_created_wall_time": 1780000000000,
        "symbol": "GOLD",
        "asset_class": "metal",
        "is_buy": True,
        "workload_mode": "RESEARCH",
        "engine_version": "engine-e2e-v1",
        "input_schema_version": "input-e2e-v1",
        "runtime": {"require_snapshots": False},
        "runtime_inputs": {"runtime_input_hash": "runtime-e2e"},
        "runtime_input_hash": "runtime-e2e",
        "plan": copy.deepcopy(candidate),
        "candidates": [candidate],
        "candidate_count": 1,
        "ordered_candidate_identities": [
            {
                "candidate_index": candidate["candidate_index"],
                "candidate_id": candidate["candidate_id"],
                "candidate_hash": candidate["candidate_hash"],
                "request_execution_fingerprint": candidate["request_execution_fingerprint"],
            }
        ],
        "po3": {
            "has_sweep": True,
            "has_displacement": True,
            "has_bos": True,
            "t_sweep": 100,
            "t_disp": 101,
            "t_bos": 102,
            "session_name": "LON",
            "in_killzone": True,
        },
        "validation": {"missing_fields": [], "hard_blockers": []},
    }


def _score_setup_patches(memory: _RetrievalStore) -> list:
    """Same neutralizations the e2e convention proves are sufficient."""
    return [
        patch("ai_gate._trade_memory_store", return_value=memory),
        patch("ai_gate.log_ai_usage", return_value={}),
        patch("ai_gate._write_ai_cost_report", return_value=None),
        patch("ai_gate._load_live_bucket_priors", return_value={}),
        patch.object(RequestDeadline, "can_start_attempt", lambda self, now=None: True),
        patch.object(
            RequestDeadline, "provider_timeout_sec", lambda self, configured, now=None: float(configured)
        ),
    ]


class _PatchGroup:
    def __init__(self, patches: list) -> None:
        self._patches = patches

    def __enter__(self) -> None:
        for handle in self._patches:
            handle.start()

    def __exit__(self, *_exc) -> bool:
        for handle in self._patches:
            handle.stop()
        return False


class ArmEffortReachesWireTests(unittest.TestCase):
    """WP1 test 1 / BEN-003: each arm's effort reaches kwargs["reasoning"]["effort"]
    of the production Responses transport when driven through the pipeline."""

    MUSE_BY_EFFORT = {
        "minimal": "muse_minimal",
        "low": "muse_low",
        "medium": "muse_medium",
        "high": "muse_high_a",
    }

    def test_muse_arm_effort_reaches_the_wire(self) -> None:
        for effort, arm in self.MUSE_BY_EFFORT.items():
            with self.subTest(arm=arm, effort=effort):
                fake = bench.DryFakeClient()
                leg = bench.build_arm_leg(arm, ai_gate.AI_CONFIG, mode="dry", client_factory=fake.factory)
                memory = _RetrievalStore("retrieval-bench")
                memory.retrieve_analogues = lambda *args, **kwargs: SimpleNamespace(
                    state="INSUFFICIENT_SAMPLE",
                    analogue_ids=(),
                    analogues=(),
                    retrieval_hash="retrieval-bench",
                )
                with _PatchGroup(_score_setup_patches(memory)):
                    try:
                        ai_gate._score_setup_ai(_e2e_payload(), provider_override=leg)
                    except ProviderCallError:
                        pass  # the capture fake raises at responses.create by design
                self.assertTrue(fake.calls, f"{arm}: the wire was never reached")
                self.assertEqual(fake.calls[0].get("reasoning", {}).get("effort"), effort)
                self.assertEqual(
                    fake.calls[0].get("model"), str(ai_gate.AI_CONFIG.opencode_muse_model)
                )

    def test_luna_arm_sends_low_effort_and_flex_tier(self) -> None:
        fake = bench.DryFakeClient()
        leg = bench.build_arm_leg("luna_low_flex", ai_gate.AI_CONFIG, mode="dry", client_factory=fake.factory)
        with self.assertRaises(ProviderCallError):
            leg.generate_structured(
                role="analyst",
                system_prompt="benchmark probe",
                evidence={"probe": True},
                response_schema=ModelAIGateOutput,
                request_metadata={"request_id": "luna-wire-probe", "deadline": None},
            )
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["reasoning"], {"effort": "low"})
        self.assertEqual(fake.calls[0].get("service_tier"), "flex")


class MuseFailureNoFallbackTests(unittest.TestCase):
    """WP1 test 2: a Muse failure is recorded as a failure of THAT arm with no
    fallback -- exactly one provider call, one model, one provider id, and the
    result document records an error (or provider-failure decision)."""

    def test_single_leg_failure_records_no_second_provider(self) -> None:
        fake = bench.DryFakeClient()
        attempts: list[dict] = []
        leg = bench.build_arm_leg("muse_medium", ai_gate.AI_CONFIG, mode="dry", client_factory=fake.factory)
        leg.attempt_observer = lambda record: attempts.append(dict(record))
        self.assertEqual(list(leg._models_for_role("analyst")), [str(ai_gate.AI_CONFIG.opencode_muse_model)])
        with self.assertRaises(ProviderCallError) as raised:
            leg.generate_structured(
                role="analyst",
                system_prompt="benchmark probe",
                evidence={"probe": True},
                response_schema=ModelAIGateOutput,
                request_metadata={"request_id": "muse-failure-probe", "deadline": None},
            )
        self.assertEqual(len(fake.calls), 1, "no second call of any kind")
        self.assertEqual(
            {call["model"] for call in fake.calls}, {str(ai_gate.AI_CONFIG.opencode_muse_model)}
        )
        self.assertIn("PROVIDER_TRANSPORT_ERROR", str(raised.exception))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["provider_id"], "opencode_go_responses")
        self.assertEqual(attempts[0]["outcome"], "transport_error")
        # BEN-003: the effort proof also exists INSIDE the attempt record.
        self.assertEqual(attempts[0]["reasoning_effort_sent"], "medium")
        document = bench.build_result_document(
            request_id="muse-failure-probe",
            arm="muse_medium",
            mode="dry",  # required by the mode-aware result schema (BEN-007)
            started_utc=bench._now_utc(),
            elapsed_sec=0.1,
            config=bench.arm_config_block("muse_medium", leg),
            archive_candidates=1,
            sealed_candidates=1,
            decision=None,
            attempts=attempts,
            error=raised.exception,
        )
        self.assertEqual(document["status"], "error")
        self.assertEqual(document["error"]["type"], "ProviderCallError")
        self.assertEqual(document["decision"], None)
        self.assertFalse(any(a.get("fallback_reason") for a in document["attempts"]))


def _mk_decision(
    *,
    state: str,
    allow: bool,
    candidate_id: str,
    score: float,
    veto: bool = False,
    veto_code: str = "",
    tier: str = bench.SCHEMA_FULL_STRUCTURED,
    source: str = "ai_approved",
) -> dict:
    return {
        "decision_state": state,
        "python_final_allow": allow,
        "raw_allow": allow,
        "allow": allow,
        "chosen_index": 0,
        "selected_candidate_id": candidate_id,
        "selected_candidate_hash": "HASH-" + candidate_id,
        "decision_quality_tier": tier,
        "decision_source": source,
        "rejection_codes": [],
        "veto_enabled": veto,
        "veto_code": veto_code,
        "veto_reason": "",
        "llm_quality_score": score,
        "llm_self_reported_confidence": 0.7,
        "suggested_risk_multiplier": 1.0 if allow else 0.0,
        "mandatory_fields_complete": True,
        "missing_mandatory_fields": [],
        "invalid_mandatory_fields": [],
        "provider_mode": "OPENCODE_API",
        "provider_id": "opencode_go_responses",
        "actual_model_id": bench.MUSE_MODEL_DEFAULT,
        "model_fingerprint": "fp",
        "final_resolver_reason": "",
        "role_latencies": {"analyst": 1.0},
        "provider_retry_counts": {},
        "provider_usage": {},
        "analyst_output": {},
        "critic_output": {},
        "adjudicator_output": {},
        "candidate_assessments": [
            {
                "candidate_id": candidate_id,
                "decision_state": state,
                "veto": {"enabled": veto, "code": veto_code or None},
            }
        ],
    }


def _mk_result(request_id: str, arm: str, decision: dict, *, effort: str = "high") -> dict:
    attempt = {
        "role": "analyst",
        "provider_id": "opencode_go_responses",
        "model": bench.MUSE_MODEL_DEFAULT,
        "outcome": "ok",
        "http_status": 200,
        "latency_sec": 1.0,
        "input_tokens": 1000,
        "cached_read_tokens": 500,
        "reasoning_tokens": 100,
        "output_tokens": 200,
        "expected_go_usage_usd": {"standard": 0.00014},
        "reasoning_effort_sent": effort,
        "fallback_reason": "",
        "route_stage": None,
    }
    return {
        "request_id": request_id,
        "arm": arm,
        "status": "ok",
        "started_utc": "2026-09-13T00:00:00Z",
        "elapsed_sec": 1.5,
        "error": None,
        "config": {"effort": effort, "model": bench.MUSE_MODEL_DEFAULT,
                   "provider_mode": "OPENCODE_API", "provider_id": "opencode_go_responses"},
        "wire_payload": {"archive_candidates": 1, "sealed_candidates": 1},
        "decision": decision,
        "attempts": [attempt],
    }


class AgreementSafetyWilsonTests(unittest.TestCase):
    """WP1 test 3: agreement / safety / Wilson computation on a hand-built fixture."""

    def _manifest(self, request_ids: list[str]) -> dict:
        return {
            "request_count": len(request_ids),
            "seed": bench.DEFAULT_SEED,
            "rows": [{"request_id": rid} for rid in request_ids],
        }

    def test_wilson_interval_matches_the_frozen_formula(self) -> None:
        lo, hi = bench.wilson_interval(1, 4)
        # p=0.25, z=1.959964, n=4 -> center 0.372473, half 0.326885 (hand-computed)
        self.assertAlmostEqual(lo, 0.045588, places=4)
        self.assertAlmostEqual(hi, 0.699358, places=4)
        self.assertIsNone(bench.wilson_interval(0, 0))

    def test_agreement_s1_s2_s3_and_mad(self) -> None:
        results = [
            # R1: everything approves and allows -> agreement, no safety hits.
            _mk_result("R1", "muse_high_a", _mk_decision(state="APPROVE", allow=True, candidate_id="A", score=8.0)),
            _mk_result("R1", "muse_high_b", _mk_decision(state="APPROVE", allow=True, candidate_id="A", score=7.0)),
            _mk_result("R1", "muse_low", _mk_decision(state="APPROVE", allow=True, candidate_id="A", score=6.0), effort="low"),
            # R2: both HIGH veto-REJECT candidate B; the arm allows B -> S1.
            _mk_result("R2", "muse_high_a", _mk_decision(state="REJECT", allow=False, candidate_id="B", score=2.0, veto=True, veto_code="missing_required_evidence", source="ai_reject")),
            _mk_result("R2", "muse_high_b", _mk_decision(state="REJECT", allow=False, candidate_id="B", score=2.0, veto=True, veto_code="missing_required_evidence", source="ai_reject")),
            _mk_result("R2", "muse_low", _mk_decision(state="APPROVE", allow=True, candidate_id="B", score=6.5), effort="low"),
            # R3: both HIGH approve; the arm abstains -> S3 (missed trade).
            _mk_result("R3", "muse_high_a", _mk_decision(state="APPROVE", allow=True, candidate_id="C", score=7.5)),
            _mk_result("R3", "muse_high_b", _mk_decision(state="APPROVE", allow=True, candidate_id="C", score=7.5)),
            _mk_result("R3", "muse_low", _mk_decision(state="ABSTAIN", allow=False, candidate_id="C", score=4.0, source="ai_abstain"), effort="low"),
        ]
        summary = bench.aggregate_results(self._manifest(["R1", "R2", "R3"]), results)
        low = summary["muse_low"]
        safety = low["safety"]
        self.assertEqual(safety["S1"], 1)
        # R2 counts as S2 too: the arm allows where BOTH HIGH runs refused.
        self.assertEqual(safety["S2"], 1)
        self.assertEqual(safety["S3"], 1)
        self.assertEqual(safety["n_requests_with_both_high_completed"], 3)
        self.assertEqual(safety["S1_detail"], [{"request_id": "R2", "candidate": "B"}])
        agreement = low["agreement_vs_high_a"]
        self.assertEqual(agreement["n"], 3)
        self.assertEqual(agreement["decision_state_agreement"]["numerator"], 1)
        self.assertEqual(agreement["decision_state_agreement"]["denominator"], 3)
        self.assertEqual(agreement["decision_state_agreement"]["wilson_95_ci"],
                         bench.wilson_interval(1, 3))
        # R1 T==T; R2 arm T vs high F; R3 arm F vs high T -> 1/3.
        self.assertEqual(agreement["final_allow_agreement"]["numerator"], 1)
        self.assertEqual(agreement["selected_candidate_agreement"]["numerator"], 3)
        self.assertEqual(agreement["veto_code_agreement"]["numerator"], 2)
        # MAD: R1 |6-8| + R2 |6.5-2| + R3 |4-7.5| -> (2+4.5+3.5)/3
        self.assertAlmostEqual(agreement["quality_score_mad"], round(10.0 / 3, 6), places=5)
        noise = summary["_noise_high_a_vs_high_b"]
        self.assertEqual(noise["decision_state_agreement"]["numerator"], 3)
        # BEN-005: S1>0 and agreement below the noise floor -> not non-inferior.
        ben = low["ben005"]["conditions"]
        self.assertFalse(ben["a_S1_zero"])
        self.assertFalse(ben["b_S2_within_high_vs_high_noise"])  # S2=1 > noise 0
        self.assertFalse(ben["d_state_agreement_ge_noise_minus_5pp"])
        self.assertIs(low["ben005"]["non_inferior"], False)
        self.assertEqual(low["schema_valid_rate"]["numerator"], 3)
        self.assertEqual(low["semantic_valid_rate"]["numerator"], 3)
        self.assertEqual(low["infra_failure_rate"]["numerator"], 0)
        cost = low["roles"]["analyst"]["cost_usd_total"]
        self.assertAlmostEqual(cost, 0.00042, places=8)


class ResumabilityTests(unittest.TestCase):
    """WP1 test 4: a pre-existing status=="ok" result file is never re-spawned."""

    def test_plan_skips_completed_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            results_dir = Path(raw) / "results"
            results_dir.mkdir()
            request_id = "REQ-A"
            done = bench.result_path(results_dir, request_id, "muse_high_a")
            done.write_text(
                json.dumps(
                    {"request_id": request_id, "arm": "muse_high_a", "status": "ok", "mode": "dry"}
                ),
                encoding="utf-8",
            )
            corrupt = bench.result_path(results_dir, request_id, "muse_low")
            corrupt.write_text("{not json", encoding="utf-8")
            todo, skipped = bench.build_pair_plan(
                [request_id], list(bench.ARM_ORDER), results_dir, seed=bench.DEFAULT_SEED, mode="dry"
            )
            self.assertIn((request_id, "muse_high_a"), skipped)
            todo_arms = {arm for _rid, arm in todo}
            self.assertNotIn("muse_high_a", todo_arms)
            self.assertIn("muse_low", todo_arms)  # corrupt file must be re-run
            self.assertEqual(len(todo) + len(skipped), len(bench.ARM_ORDER))

    def test_cmd_run_spawns_only_todo_pairs(self) -> None:
        spawned: list[tuple[str, str]] = []

        def fake_launch(pair, repo_python, child_timeout_sec):
            spawned.append((pair["request_id"], pair["arm"]))
            bench.write_json(Path(pair["out"]), _mk_result(
                pair["request_id"], pair["arm"],
                _mk_decision(state="APPROVE", allow=True, candidate_id="A", score=7.0),
                effort=bench.ARMS[pair["arm"]]["effort"],
            ))
            return 0

        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "run"
            run_dir.mkdir()
            bus = Path(raw) / "empty-bus"
            bus.mkdir()
            bench.write_json(run_dir / "benchmark_manifest.json", {
                "seed": bench.DEFAULT_SEED,
                "bus": str(bus),
                "request_count": 1,
                "rows": [{
                    "request_id": "REQ-B",
                    "archive_path": str(bus / "payload.json"),
                    "historical_decision": {},
                }],
            })
            results_dir = run_dir / "results"
            results_dir.mkdir()
            bench.write_json(bench.result_path(results_dir, "REQ-B", "muse_high_a"), {
                "request_id": "REQ-B", "arm": "muse_high_a", "status": "ok", "mode": "dry",
                "config": {}, "decision": {}, "attempts": [],
            })
            args = Namespace(
                run_dir=str(run_dir), arms="muse_high_a,muse_medium", requests="",
                concurrency=2, mode="dry", dry_requests=5, child_timeout_sec=60.0,
                bus=str(bus), keep_scratch=False,
            )
            with patch.object(bench, "launch_child", side_effect=fake_launch) as launch:
                code = bench.cmd_run(args)
            self.assertEqual(code, 0)
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(spawned, [("REQ-B", "muse_medium")])
            selfcheck = json.loads((run_dir / "selfcheck.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [list(pair) for pair in selfcheck["pairs_skipped_ok"]], [["REQ-B", "muse_high_a"]]
            )
            self.assertTrue(selfcheck["isolation"]["unchanged"])


class TakeoverHardeningTests(unittest.TestCase):
    """2026-09-13 takeover: usage-limit stop/resume, .env-proof isolation, A3 clean agreement."""

    @staticmethod
    def _limit_result(request_id: str = "REQ-L", arm: str = "muse_low") -> dict:
        return {
            "request_id": request_id, "arm": arm, "status": "ok", "mode": "live",
            "decision": {"decision_state": "REJECT", "decision_quality_tier": "DEGRADED_NON_TRADING",
                         "decision_source": "provider_transport_error"},
            "attempts": [{
                "role": "analyst", "outcome": "transport_error", "http_status": 429,
                "error_category": "RateLimitError", "billing_state": "not_billed_admission_refused",
                "retry_kind": "none", "retry_counts": {"admission_retries": 0},
            }],
        }

    def test_weekly_limit_refusal_stops_the_run_and_is_rerun_on_resume(self) -> None:
        result = self._limit_result()
        self.assertTrue(bench.go_limit_hit(result))
        with tempfile.TemporaryDirectory() as raw:
            results_dir = Path(raw)
            bench.write_json(bench.result_path(results_dir, "REQ-L", "muse_low"), result)
            self.assertFalse(bench.completed_ok(results_dir, "REQ-L", "muse_low", mode="live"))

    def test_congestion_429_recovered_by_admission_retry_is_not_a_limit(self) -> None:
        result = self._limit_result()
        result["attempts"].append({
            "role": "analyst", "outcome": "ok", "http_status": 200, "billing_state": "billed_usage_reported",
            "retry_kind": "admission", "retry_counts": {"admission_retries": 1},
        })
        result["decision"] = {"decision_state": "ABSTAIN", "decision_quality_tier": "FULL_STRUCTURED"}
        self.assertFalse(bench.usage_limit_refused(result))
        self.assertFalse(bench.go_limit_hit(result))

    def test_isolation_survives_an_env_file_that_overrides_the_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {}, clear=False):
            scratch = Path(raw) / "pair"
            bench.configure_child_env(scratch, mode="dry", bus=Path(raw))
            clean = ai_gate.AIGateRuntimeConfig.from_env(dict(os.environ))
            self.assertEqual(bench.child_isolation_violations(clean, scratch), [])
            # What po3_env.bootstrap_provider_env(override=True) did on 2026-09-13.
            os.environ["AI_DECISION_CACHE_FILE"] = str(bench.REPO_PYTHON / "data" / "ai_decision_cache.jsonl")
            os.environ["AI_SHADOW_REPEAT_ENABLE"] = "true"
            leaked = ai_gate.AIGateRuntimeConfig.from_env(dict(os.environ))
            violations = bench.child_isolation_violations(leaked, scratch)
            self.assertIn("decision_cache_file", violations)
            self.assertIn("shadow_repeat_enable=True", violations)
            bench.configure_child_env(scratch, mode="dry", bus=Path(raw))
            restored = ai_gate.AIGateRuntimeConfig.from_env(dict(os.environ))
            self.assertEqual(bench.child_isolation_violations(restored, scratch), [])

    def test_live_child_reapplies_isolation_after_the_credential_load(self) -> None:
        source = Path(bench.__file__).read_text(encoding="utf-8")
        body = source[source.index("def run_child("):]
        bootstrap = body.index("po3_env.bootstrap_provider_env()")
        self.assertGreater(body.index("configure_child_env(scratch, mode=mode, bus=bus)", bootstrap), bootstrap)
        self.assertLess(bootstrap, body.index("child_isolation_violations(ai_gate.AI_CONFIG, scratch)"))

    def test_clean_agreement_ignores_shared_infrastructure_rejects(self) -> None:
        high = _mk_decision(state="ABSTAIN", allow=False, candidate_id="A", score=6.0)
        manifest = {"rows": [{"request_id": f"R{i}"} for i in range(4)]}
        results = []
        for i in range(4):
            for arm in ("muse_high_a", "muse_high_b"):
                results.append(_mk_result(f"R{i}", arm, copy.deepcopy(high)))
            if i < 2:
                arm_result = _mk_result(f"R{i}", "muse_low", _mk_decision(state="REJECT", allow=False, candidate_id="A", score=4.0), effort="low")
            else:
                arm_result = _mk_result(f"R{i}", "muse_low", copy.deepcopy(high), effort="low")
            results.append(arm_result)
        # Two requests where BOTH sides were refused by the usage limit.
        for i in range(4, 6):
            manifest["rows"].append({"request_id": f"R{i}"})
            for arm in ("muse_high_a", "muse_high_b", "muse_low"):
                results.append(self._limit_result(f"R{i}", arm))
        summary = bench.aggregate_results(manifest, results)
        literal = summary["muse_low"]["agreement_vs_high_a"]["decision_state_agreement"]
        clean = summary["muse_low"]["agreement_vs_high_a_clean"]["decision_state_agreement"]
        self.assertEqual((literal["numerator"], literal["denominator"]), (4, 6))
        self.assertEqual((clean["numerator"], clean["denominator"]), (2, 4))
        a3 = summary["muse_low"]["ben005"]["conditions"]["addendum_a3"]
        self.assertFalse(a3["no_usage_limit_refusals_left"])
        self.assertFalse(a3["n_clean_pairs_ge_min"])
        self.assertFalse(summary["muse_low"]["ben005"]["non_inferior_a3"])


if __name__ == "__main__":
    unittest.main()
