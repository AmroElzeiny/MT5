"""AI review gate: fewer unnecessary provider calls, never fewer useful ones.

Covers the acceptance list of the call-efficiency task (A..Q) offline, with
deterministic clocks and scripted providers.  The live-provider check is a
separate script (tools/ai_review_gate_live_check.py) and never part of the suite.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PY_ROOT = Path(__file__).resolve().parents[1]
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

import ai_review_gate as gate  # noqa: E402
from openai_usage_logger import traffic_class_for  # noqa: E402


def _mql_root() -> Path:
    override = os.environ.get("PO3_MQL_INCLUDE_ROOT", "").strip()
    if override:
        return Path(override)
    return PY_ROOT.parent / "MT5_PO3_Codex Include"


MUSE = {"provider_id": "opencode_go_responses", "model_id": "muse-spark-1.3-contributor"}
T0 = 1_789_000_000


def _candidate(**over) -> dict:
    row = {
        "candidate_index": 0,
        "candidate_id": "EURUSD|100|200|300|breaker_retest|1.10050|breaker_retest|0",
        "setup_id": "EURUSD|100|200|300|breaker_retest|1.10050",
        "entry_branch": "breaker_retest",
        "setup_taxonomy_enum": "MICRO_BREAKER_RETEST",
        "setup_family": "micro_bisi_sibi_edge",
        "po3_state": "PO3_ENTRY_WAITING",
        "fvg_mitigation_state": "mid_mitigated",
        "fvg_execution_class": "mid_mitigated_fvg",
        "obstacle_kind": "crossed_opposing_imbalance",
        "tp_model": "prev_day_low",
        "target_source": "prev_day_low",
        "volatility_profile": "balanced",
        "policy_bucket": "balanced_trend|ASIA|x",
        "entry_est": 1.10000,
        "sl": 1.09900,
        "tp1": 1.10100,
        "tp2": 1.10200,
        # Continuous evidence that moves every scan and is NOT a fingerprint input.
        "retest_quality_score": 9.0,
        "freshness_score": 10.0,
    }
    row.update(over)
    return row


def _payload(t: int, *, symbol="EURUSD", account="903871", session="ASIA", killzone="NK",
             candidates=None, bos=True, n=1, context=True) -> dict:
    payload = {
        "id": f"{account}_1788999394_24185000_{t}_{symbol}_{n}",
        "symbol": symbol,
        "workload_mode": "LIVE_FORWARD",
        "request_created_sim_time": t,
        "is_buy": True,
        "runtime_input_hash": "rih",
        "decision_input_hash": "dih",
        "contract_manifest_hash": "1068509530",
        "po3": {"has_bos": bos, "has_sweep": True, "po3_state": "PO3_ENTRY_WAITING", "t_sweep": 100},
        "regime": {"news_risk": 0.0, "expansion_score": 1.4 + (t % 7) * 0.01},
        "candidates": candidates if candidates is not None else [_candidate()],
    }
    if context:
        payload["ai_review_context"] = {
            "context_version": gate.REVIEW_CONTEXT_VERSION,
            "server_time": t,
            "session_code": session,
            "killzone_code": killzone,
            "scan_interval_min": 10,
        }
    return payload


def _decision(state="REJECT", quality=3.0, allow=False, model="muse-spark-1.3-contributor",
              provider="opencode_go_responses", tier="FULL_STRUCTURED", source=None, codes=()) -> dict:
    return {
        "decision_quality_tier": tier,
        "decision_source": source or {"APPROVE": "ai_approved", "REJECT": "ai_rejected", "ABSTAIN": "ai_abstained"}[state],
        "mandatory_fields_complete": True,
        "rejection_codes": list(codes),
        "decision_state": state,
        "python_final_allow": allow,
        "provider_id": provider,
        "actual_model_id": model,
        "candidate_assessments": [
            {
                "candidate_index": 0,
                "candidate_id": _candidate()["candidate_id"],
                "decision_state": state,
                "llm_quality_score": quality,
            }
        ],
    }


def _gate(tmp: str, **env) -> gate.ReviewGate:
    values = {"AI_REVIEW_GATE_MODE": "enforce", "AI_REVIEW_STATE_FILE": str(Path(tmp) / "state.json"), **env}
    return gate.ReviewGate(gate.ReviewGateConfig.from_env(values))


def _call_and_record(g: gate.ReviewGate, payload: dict, decision: dict) -> gate.ReviewVerdict:
    verdict = g.evaluate(payload, python_identity=MUSE)
    if verdict.action == gate.ACTION_CALL:
        g.record(verdict, payload, decision, family_threshold=lambda index: 7.2)
    return verdict


class SessionCadenceTests(unittest.TestCase):
    """A: cadence per MQL session code; TTL = cadence x multiplier, capped."""

    def test_defaults_match_requested_cadence(self) -> None:
        config = gate.ReviewGateConfig.from_env({})
        self.assertEqual(config.cadence_minutes["ASIA"], 30.0)
        self.assertEqual(config.cadence_minutes["LON"], 15.0)
        self.assertEqual(config.cadence_minutes["NY"], 7.0)
        # Enforced by default only because the replay acceptance passed.
        self.assertEqual(config.mode, gate.MODE_ENFORCE)
        self.assertEqual(config.decisive_margin, 2.0)
        self.assertEqual(config.decisive_ttl_cadences, 1.0)
        self.assertFalse(config.defer_borderline)

    def test_unchanged_decisive_state_is_reviewed_once_per_session_cadence(self) -> None:
        # 10-minute scans of an unchanged decisive state; count provider calls.
        for session, expected_calls in (("ASIA", 3), ("LON", 5), ("NY", 9), ("OFF", 3)):
            with tempfile.TemporaryDirectory() as tmp:
                g = _gate(tmp)
                calls = 0
                for step in range(9):  # 0..80 minutes
                    verdict = _call_and_record(g, _payload(T0 + step * 600, session=session), _decision())
                    calls += verdict.action == gate.ACTION_CALL
                self.assertEqual(calls, expected_calls, session)

    def test_cadence_counts_follow_authoritative_session_durations(self) -> None:
        """Nominal scheduled reviews/day = session minutes / cadence (not hardcoded 14/28/48)."""

        from tools.ai_call_efficiency_replay import historical_session_code

        config = gate.ReviewGateConfig.from_env({})
        day_start = 1_789_452_000  # 2026-09-15 06:00 server = 03:00 UTC (a Tuesday)
        minutes = {code: 0 for code in gate.SESSION_CODES}
        for minute in range(24 * 60):
            minutes[historical_session_code(day_start + minute * 60, 3)] += 1
        self.assertEqual(sum(minutes.values()), 1440)
        nominal = {code: minutes[code] / config.cadence_minutes[code] for code in minutes}
        # Summer DST with the preset hours: ASIA 00-10 UTC, LONDON 10-16 UTC,
        # OFF 16-20 UTC, NEW_YORK 20-24 UTC (ASIA wins the 00-04 overlap).
        self.assertEqual(minutes["ASIA"], 600)
        self.assertEqual(minutes["LON"], 360)
        self.assertEqual(minutes["OFF"], 240)
        self.assertEqual(minutes["NY"], 240)
        self.assertAlmostEqual(nominal["ASIA"], 20.0)
        self.assertAlmostEqual(nominal["LON"], 24.0)

    def test_dst_moves_repository_sessions(self) -> None:
        from tools.ai_call_efficiency_replay import historical_session_code

        import calendar

        # 20:30 UTC is inside NEW_YORK 16:00-24:00 New York time only under EDT.
        summer_2030 = calendar.timegm((2026, 9, 15, 20, 30, 0))
        winter_2030 = calendar.timegm((2026, 11, 25, 20, 30, 0))
        winter_2200 = calendar.timegm((2026, 11, 25, 22, 0, 0))
        self.assertEqual(historical_session_code(summer_2030 + 3 * 3600, 3), "NY")
        self.assertEqual(historical_session_code(winter_2030 + 2 * 3600, 2), "OFF")
        self.assertEqual(historical_session_code(winter_2200 + 2 * 3600, 2), "NY")

    def test_session_transition_forces_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            _call_and_record(g, _payload(T0, session="ASIA"), _decision())
            verdict = g.evaluate(_payload(T0 + 600, session="LON"), python_identity=MUSE)
            self.assertEqual(verdict.action, gate.ACTION_CALL)
            self.assertIn("session_transition", verdict.events)

    def test_invalid_or_missing_context_always_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            _call_and_record(g, _payload(T0), _decision())
            missing = g.evaluate(_payload(T0 + 600, context=False), python_identity=MUSE)
            self.assertEqual((missing.action, missing.reason), (gate.ACTION_CALL, "review_context_missing"))
            bad = _payload(T0 + 600)
            bad["ai_review_context"]["server_time"] = T0 - 3600
            self.assertEqual(g.evaluate(bad, python_identity=MUSE).reason, "review_context_time_mismatch")
            bad2 = _payload(T0 + 600)
            bad2["ai_review_context"]["session_code"] = "TOKYO"
            self.assertEqual(g.evaluate(bad2, python_identity=MUSE).reason, "review_context_session_invalid")


class ChangeDetectionTests(unittest.TestCase):
    def _primed(self, tmp: str) -> gate.ReviewGate:
        g = _gate(tmp)
        _call_and_record(g, _payload(T0), _decision())
        return g

    def test_b_structure_event_overrides_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = self._primed(tmp)
            verdict = g.evaluate(_payload(T0 + 600, bos=False), python_identity=MUSE)
            self.assertEqual(verdict.action, gate.ACTION_CALL)
            self.assertTrue(any(e.startswith("request_state:") and "has_bos" in e for e in verdict.events))
            new_candidate = [_candidate(), _candidate(candidate_index=1, candidate_id="EURUSD|100|200|300|fvg_mid|1.10050|fvg_mid|0", entry_branch="fvg_mid", setup_taxonomy_enum="MICRO_FVG_MID_REVERSAL")]
            verdict = g.evaluate(_payload(T0 + 600, candidates=new_candidate), python_identity=MUSE)
            self.assertIn("new_candidate", verdict.events)

    def test_c_noise_is_not_an_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = self._primed(tmp)
            noisy = [_candidate(entry_est=1.10001, sl=1.09901, retest_quality_score=7.3, freshness_score=8.1)]
            verdict = g.evaluate(_payload(T0 + 600, candidates=noisy), python_identity=MUSE)
            self.assertEqual((verdict.action, verdict.reason), (gate.ACTION_REUSE, "unchanged_decisive_non_approval_within_ttl"))

    def test_d_material_evidence_change_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = self._primed(tmp)
            for over, marker in (
                ({"fvg_mitigation_state": "fully_filled"}, "candidate_state"),
                ({"obstacle_kind": ""}, "candidate_state"),
                ({"volatility_profile": "expanding"}, "regime_label"),
                ({"entry_est": 1.10030}, "price_drift"),
            ):
                verdict = g.evaluate(_payload(T0 + 600, candidates=[_candidate(**over)]), python_identity=MUSE)
                self.assertEqual(verdict.action, gate.ACTION_CALL, over)
                self.assertTrue(any(e.startswith(marker) for e in verdict.events), (over, verdict.events))
            killzone = g.evaluate(_payload(T0 + 600, killzone="K"), python_identity=MUSE)
            self.assertIn("killzone_transition", killzone.events)

    def test_e_unchanged_state_one_decision_then_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            actions = [_call_and_record(g, _payload(T0 + i * 600), _decision()).action for i in range(3)]
            self.assertEqual(actions, [gate.ACTION_CALL, gate.ACTION_REUSE, gate.ACTION_REUSE])

    def test_f_expired_decision_always_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = self._primed(tmp)
            verdict = g.evaluate(_payload(T0 + 30 * 60), python_identity=MUSE)
            self.assertEqual((verdict.action, verdict.reason), (gate.ACTION_CALL, "decisive_prior_ttl_expired"))

    def test_h_borderline_or_approving_priors_are_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            _call_and_record(g, _payload(T0), _decision(state="ABSTAIN", quality=6.2))
            verdict = g.evaluate(_payload(T0 + 600), python_identity=MUSE)
            self.assertEqual((verdict.action, verdict.reason), (gate.ACTION_CALL, "borderline_prior_requires_fresh_review"))
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            _call_and_record(g, _payload(T0), _decision(state="APPROVE", quality=7.8, allow=True))
            verdict = g.evaluate(_payload(T0 + 600), python_identity=MUSE)
            self.assertEqual(verdict.reason, "prior_decision_approved_never_reused")

    def test_fallback_answer_never_counts_as_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            _call_and_record(g, _payload(T0), _decision(model="gpt-5.6-luna", provider="openai_remote_api"))
            verdict = g.evaluate(_payload(T0 + 600), python_identity=MUSE)
            self.assertEqual(verdict.action, gate.ACTION_CALL)
            self.assertTrue(any("python.model_id" in e for e in verdict.events))

    def test_l_non_authoritative_decisions_never_become_priors(self) -> None:
        for decision in (
            _decision(tier="DEGRADED_NON_TRADING"),
            _decision(source="provider_transport_error"),
            _decision(codes=["candidate_hash_mismatch"]),
            {**_decision(), "mandatory_fields_complete": False},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                g = _gate(tmp)
                verdict = g.evaluate(_payload(T0), python_identity=MUSE)
                stored, _reason = g.record(verdict, _payload(T0), decision, family_threshold=lambda i: 7.2)
                self.assertFalse(stored)
                self.assertEqual(g.evaluate(_payload(T0 + 600), python_identity=MUSE).action, gate.ACTION_CALL)

    def test_shadow_mode_never_suppresses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp, AI_REVIEW_GATE_MODE="shadow")
            _call_and_record(g, _payload(T0), _decision())
            verdict = g.evaluate(_payload(T0 + 600), python_identity=MUSE)
            self.assertTrue(verdict.would_reuse)
            self.assertFalse(verdict.enforced_reuse)
            self.assertTrue(gate.shadow_outcome_equivalent(verdict, {"python_final_allow": False}))
            self.assertFalse(gate.shadow_outcome_equivalent(verdict, {"python_final_allow": True}))

    def test_invalid_mode_is_off(self) -> None:
        config = gate.ReviewGateConfig.from_env({"AI_REVIEW_GATE_MODE": "maybe"})
        self.assertEqual(config.mode, gate.MODE_OFF)


class RestartAndConcurrencyTests(unittest.TestCase):
    def test_m_restart_keeps_valid_state_and_ignores_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = _gate(tmp)
            _call_and_record(first, _payload(T0), _decision())
            restarted = _gate(tmp)
            self.assertEqual(restarted.evaluate(_payload(T0 + 600), python_identity=MUSE).action, gate.ACTION_REUSE)
            # TTL is server-time based: a restart never extends validity.
            self.assertEqual(restarted.evaluate(_payload(T0 + 1800), python_identity=MUSE).action, gate.ACTION_CALL)
            Path(tmp, "state.json").write_text("{not json", encoding="utf-8")
            corrupt = _gate(tmp)
            self.assertEqual(corrupt.evaluate(_payload(T0 + 600), python_identity=MUSE).reason, "no_prior_authoritative_decision")

    def test_late_older_decision_never_overwrites_newer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            newer = _payload(T0 + 600)
            _call_and_record(g, newer, _decision(state="APPROVE", quality=8.0, allow=True))
            older = _payload(T0)
            verdict = g.evaluate(older, python_identity=MUSE)
            stored, reason = g.record(verdict, older, _decision(), family_threshold=lambda i: 7.2)
            self.assertFalse(stored)
            self.assertEqual(reason, "older_than_existing_record")

    def test_n_scopes_are_isolated_and_thread_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = _gate(tmp)
            _call_and_record(g, _payload(T0, symbol="EURUSD"), _decision())
            self.assertEqual(g.evaluate(_payload(T0 + 600, symbol="GBPUSD"), python_identity=MUSE).reason, "no_prior_authoritative_decision")
            self.assertEqual(g.evaluate(_payload(T0 + 600, account="111"), python_identity=MUSE).reason, "no_prior_authoritative_decision")
            symbols = [f"SYM{i}" for i in range(20)]

            def work(symbol: str) -> None:
                _call_and_record(g, _payload(T0, symbol=symbol), _decision())

            threads = [threading.Thread(target=work, args=(s,)) for s in symbols]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            document = json.loads(Path(tmp, "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(document["records"]), 21)


class AiGateIntegrationTests(unittest.TestCase):
    """G/H/L: the hard gates keep precedence and a reuse never reaches a provider."""

    @classmethod
    def setUpClass(cls) -> None:
        import ai_gate

        cls.ai_gate = ai_gate

    def _run(self, verdict: gate.ReviewVerdict, hard=None):
        ai_gate = self.ai_gate
        provider_calls = []
        with patch.object(ai_gate, "_enrich_candidate_taxonomy_before_freeze", return_value=[]), \
             patch.object(ai_gate, "_rule_score", return_value=(5.0, "")), \
             patch.object(ai_gate, "_mandatory_live_prior_rejection", return_value=None), \
             patch.object(ai_gate, "_hard_pretrade_decision", return_value=hard), \
             patch.object(ai_gate, "_snapshot_integrity_rejections", return_value=([], [], [])), \
             patch.object(ai_gate, "_evaluate_ai_review_gate", return_value=verdict) as evaluate, \
             patch.object(ai_gate, "_write_ai_cost_report"), \
             patch.object(ai_gate, "_refresh_provider_health", side_effect=RuntimeError("provider path reached")), \
             patch.object(ai_gate, "_score_setup_ai", side_effect=lambda *a, **k: provider_calls.append(1)):
            try:
                decision = ai_gate._score_setup_impl(_payload(T0))
            except RuntimeError as exc:
                decision = exc
        return decision, provider_calls, evaluate

    def test_g_hard_gate_rejection_needs_no_provider_and_precedes_review_gate(self) -> None:
        hard = self.ai_gate.Decision(allow=False, score=0.0, decision_source="hard_pre_gate", rejection_codes=["x"])
        decision, calls, evaluate = self._run(gate.ReviewVerdict(mode="enforce", action="CALL", category="", reason="x"), hard=hard)
        self.assertIs(decision, hard)
        self.assertEqual(calls, [])
        evaluate.assert_not_called()

    def test_enforced_reuse_returns_non_trading_envelope_without_provider(self) -> None:
        verdict = gate.ReviewVerdict(mode="enforce", action="REUSE", category="UNCHANGED_STATE",
                                     reason="unchanged_decisive_non_approval_within_ttl", prior_decision_state="REJECT")
        decision, calls, _ = self._run(verdict)
        self.assertEqual(calls, [])
        self.assertFalse(decision.allow)
        self.assertFalse(decision.python_final_allow)
        self.assertEqual(decision.decision_quality_tier, "RULE_ONLY_NON_TRADING")
        self.assertEqual(decision.decision_source, gate.REUSE_DECISION_SOURCE)
        self.assertEqual(decision.rejection_codes, [gate.REUSE_REJECTION_CODE])
        self.assertEqual(decision.suggested_risk_multiplier, 0.0)
        self.assertFalse(decision.candidate_assessments)

    def test_h_call_verdict_reaches_provider_path(self) -> None:
        decision, _calls, _ = self._run(gate.ReviewVerdict(mode="enforce", action="CALL", category="", reason="meaningful_change"))
        self.assertIsInstance(decision, RuntimeError)
        self.assertIn("provider path reached", str(decision))

    def test_shadow_would_reuse_still_calls(self) -> None:
        verdict = gate.ReviewVerdict(mode="shadow", action="REUSE", category="UNCHANGED_STATE", reason="r")
        decision, _calls, _ = self._run(verdict)
        self.assertIsInstance(decision, RuntimeError)

    def test_evidence_envelope_never_contains_review_context(self) -> None:
        from decision_evidence import build_decision_evidence_envelope

        envelope = build_decision_evidence_envelope(_payload(T0)).envelope
        self.assertNotIn("ai_review_context", json.dumps(envelope))

    def test_o_gate_configuration_cannot_touch_provider_settings(self) -> None:
        source = (PY_ROOT / "ai_review_gate.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'"(AI_[A-Z_]+)"', source))
        self.assertTrue(keys)
        self.assertTrue(all(key.startswith("AI_REVIEW_") for key in keys), keys)
        self.assertNotIn("OPENCODE_", source)
        ai_gate_source = (PY_ROOT / "ai_gate.py").read_text(encoding="utf-8")
        self.assertIn("review_verdict = _evaluate_ai_review_gate(payload)", ai_gate_source)
        order = [ai_gate_source.index(marker) for marker in (
            "hard_pre_decision = _hard_pretrade_decision(payload, best_index)",
            "fatal_integrity_codes = _fatal_snapshot_integrity_codes(integrity_codes)",
            "review_verdict = _evaluate_ai_review_gate(payload)",
            "health = _refresh_provider_health(force=False)",
            "dec = _score_setup_ai(payload, frozen_request=frozen_request)",
        )]
        self.assertEqual(order, sorted(order))


class AdjudicationTests(unittest.TestCase):
    """I/J/K: the adjudicator is skipped only where the outcome is invariant."""

    def _consensus(self, analyst_state: str, critic: str, live: bool, expectancy: float = 6.5):
        from decision_pipeline import run_qualitative_consensus
        from tests.test_provider_neutral_ai import (
            _ScriptedProvider,
            _adjudicator,
            _analyst_assessment,
            _consensus_catalog,
            _consensus_evidence,
            _critic,
        )

        provider = _ScriptedProvider({"critic": [_critic(critic)], "adjudicator": [_adjudicator("UPHOLD_BLOCK")]})
        assessment = {**_analyst_assessment(), "decision_state": analyst_state}
        result = run_qualitative_consensus(
            provider=provider,
            evidence=_consensus_evidence(),
            analyst_assessment=assessment,
            evidence_catalog=_consensus_catalog(),
            request_metadata={
                "request_id": "request-A",
                "request_identity_hash": "REQUESTIDENTITYCONSENSUS123",
                "adjudication_skip_enable": True,
                "live_workload": live,
                "analyst_can_approve": analyst_state == "APPROVE",
                "adjudication_skip_floor": 4.8,
                "analyst_expectancy_score": expectancy,
            },
        )
        return result, [role for role, _ in provider.calls]

    def test_i_live_analyst_reject_skips_adjudicator_with_identical_outcome(self) -> None:
        skipped, roles = self._consensus("REJECT", "BLOCK", live=True)
        self.assertEqual(roles, ["critic"])
        self.assertTrue(skipped.adjudication_skipped)
        adjudicated, roles_nonlive = self._consensus("REJECT", "BLOCK", live=False)
        self.assertEqual(roles_nonlive, ["critic", "adjudicator"])
        self.assertEqual((skipped.decision_state, skipped.python_allow), (adjudicated.decision_state, adjudicated.python_allow))
        self.assertEqual(skipped.decision_state, "REJECT")

    def test_j_borderline_abstain_and_disputed_approve_still_adjudicate(self) -> None:
        _abstain, roles = self._consensus("ABSTAIN", "BLOCK", live=True, expectancy=6.5)
        self.assertEqual(roles, ["critic", "adjudicator"])
        _approve, roles = self._consensus("APPROVE", "BLOCK", live=True, expectancy=7.5)
        self.assertEqual(roles, ["critic", "adjudicator"])

    def test_k_critic_runs_for_every_analyst_state(self) -> None:
        for state in ("APPROVE", "REJECT", "ABSTAIN"):
            _result, roles = self._consensus(state, "PASS", live=True, expectancy=2.0)
            self.assertEqual(roles[0], "critic", state)


class TrafficClassTests(unittest.TestCase):
    def test_every_provider_row_is_classified(self) -> None:
        self.assertEqual(traffic_class_for("trade_gate.provider_neutral_analyst", "r"), "TRADING_DECISION")
        self.assertEqual(traffic_class_for("trade_gate.provider_neutral_critic", "r"), "TRADING_CRITIC")
        self.assertEqual(traffic_class_for("trade_gate.provider_neutral_adjudicator", "r"), "TRADING_ADJUDICATOR")
        self.assertEqual(traffic_class_for("trade_gate.provider_neutral_analyst_evidence_repair", "r"), "REPAIR")
        self.assertEqual(traffic_class_for("trade_gate.provider_neutral_critic", "r__shadow_repeat_1"), "SHADOW")
        self.assertEqual(traffic_class_for("trade_gate.provider_neutral_analyst", "r", {"non_trading_shadow": True}), "SHADOW")
        with patch.dict(os.environ, {"AI_TRAFFIC_CLASS": "benchmark"}):
            self.assertEqual(traffic_class_for("trade_gate.provider_neutral_analyst", "r"), "BENCHMARK")
        with patch.dict(os.environ, {"AI_TRAFFIC_CLASS": "TRADING_DECISION"}):
            # An override can never relabel production traffic.
            self.assertEqual(traffic_class_for("trade_gate.provider_neutral_critic", "r"), "TRADING_CRITIC")


class MqlContractTests(unittest.TestCase):
    """P/Q: MQL owns session authority; the trade path is untouched."""

    @classmethod
    def setUpClass(cls) -> None:
        root = _mql_root()
        cls.config = (root / "Config.mqh").read_text(encoding="utf-8", errors="replace")
        cls.bridge = (root / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="replace")
        cls.engine = (root / "TradeEngine.mqh").read_text(encoding="utf-8", errors="replace")

    def test_context_version_is_synchronized(self) -> None:
        match = re.search(r'AI_REVIEW_CONTEXT_VERSION = "([^"]+)"', self.config)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), gate.REVIEW_CONTEXT_VERSION)
        match = re.search(r'AI_REVIEW_GATE_REUSE_SOURCE = "([^"]+)"', self.config)
        self.assertEqual(match.group(1), gate.REUSE_DECISION_SOURCE)
        self.assertNotIn("AI_REVIEW_CONTEXT_VERSION", self.config.split("string PO3ContractManifestMaterial()")[1].split("}")[0])

    def test_request_carries_mql_session_authority_on_every_send(self) -> None:
        self.assertIn('"\\"ai_review_context\\":" + review_context_json', self.bridge)
        sends = re.findall(r"m_ai\.SendRequestCandidates\(([^;]*)\);", self.engine)
        self.assertTrue(sends)
        self.assertTrue(all("_AiReviewContextJson(" in call for call in sends), sends)
        helper = self.engine.split("string _AiReviewContextJson(")[1].split("\n   }")[0]
        for marker in ("m_po3.SessionCodeAt(t)", "m_po3.KillzoneCodeAt(t)", "AI_REVIEW_CONTEXT_VERSION", "server_time"):
            self.assertIn(marker, helper)

    def test_reuse_is_labelled_but_stays_non_trading(self) -> None:
        self.assertIn("dec.decision_source == AI_REVIEW_GATE_REUSE_SOURCE", self.engine)
        self.assertIn('"ai_review_reused_prior_non_approval"', self.engine)
        # The approval predicate itself is unchanged: a reuse can never be full_ai_approval.
        self.assertIn("bool full_ai_approval = (dec.python_final_allow && dec.model_raw_allow && state_approve);", self.engine)

    def test_predetermined_sweep_gate_uses_the_watchlist_rule_before_sending(self) -> None:
        body = self.engine.split("bool _QueueCandidateGroup(TradePlan &cands[]) {")[1].split("\n   void _RemovePendingGroup")[0]
        gate_pos = body.index("predetermined_sweep_already_consumed")
        send_pos = body.index("m_ai.SendRequestCandidates(")
        self.assertLess(gate_pos, send_pos)
        self.assertIn("InpMaxTradesPerSweep == 1", body[:gate_pos])
        self.assertIn("_SweepAlreadyConsumed(cands[i])", body[:gate_pos])
        watch = self.engine.split("bool _AddToWatchlist(const TradePlan &p) {")[1]
        self.assertIn("_SweepTradeCapReached(staged, true, sweep_reason)", watch)


if __name__ == "__main__":
    unittest.main()
