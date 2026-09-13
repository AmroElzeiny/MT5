"""Pending-AI restart-recovery lifecycle acceptance tests (REC-001..REC-006,
OBS-001, RET-001/RET-002, SHD-001/SHD-002).

Three surfaces, in the style of ``test_shadow_outcome_lifecycle``:

*   ``PendingAiRecoveryMqlSourceTests`` pins the durable-state contract in the
    REPO ``MT5_PO3_Codex Include`` tree (never the deployed terminal tree).
    Properties such as "a live shutdown preserves pending groups" or "startup
    reconciliation never recalls the provider" live in ``TradeEngine.mqh`` and
    have no Python object that can violate them, so the source -- scoped to the
    single owning function via ``_function_body`` -- is the honest place.
*   ``PendingAiRecoveryFalsificationTests`` runs the same eyes against the
    pre-change tree via ``git show HEAD:...`` and proves the source assertions
    would have failed before the change (they pin new behaviour, not prose).
*   ``FileBusRecoveryScenarioTests`` exercises the REAL production
    ``request_lifecycle.RequestIdempotencyLedger`` offline in a temp directory:
    the response-present-before-startup reuse, the fresh-heartbeat await, the
    stale-claim recovery, the identity collision, and the exact bus layout the
    MQL reconciler inspects.

Naming note: the mission refers to the pending-AI enqueue as
``_QueuePendingAi``; that symbol does not exist in the repository (neither in
the working tree nor at HEAD).  The two real enqueue sites that the mission's
context lists -- "the ``_QueuePendingAi`` enqueue" and "the tester-cache-hit
enqueue" -- are ``_QueueCandidateGroup`` (TradeEngine.mqh live path) and
``_QueueTesterCachedDecision`` (tester cache-hit path), so REC-001 is scoped to
those actual owners rather than renaming production code to fit a spec token.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from request_lifecycle import RequestIdempotencyLedger
from test_governance_contracts import _function_body


# --------------------------------------------------------------------------
# Repo source resolution -- deliberately NOT the deployed terminal tree.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]   # ...\MT5
REPO_INCLUDE = REPO_ROOT / "MT5_PO3_Codex Include"


def _resolve_include_root() -> Path:
    override = os.environ.get("PO3_MQL_INCLUDE_ROOT", "").strip()
    if override:
        candidate = Path(override)
        if (candidate / "TradeEngine.mqh").is_file():
            return candidate
    return REPO_INCLUDE


INCLUDE_ROOT = _resolve_include_root()
TRADE_ENGINE_PATH = INCLUDE_ROOT / "TradeEngine.mqh"
STATE_STORE_PATH = INCLUDE_ROOT / "StateStore.mqh"
CONFIG_PATH = INCLUDE_ROOT / "Config.mqh"
PRESET_PATH = REPO_ROOT / "scalp_v2_full.set"


def _read_source(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


TRADE_ENGINE = _read_source(TRADE_ENGINE_PATH)
STATE_STORE = _read_source(STATE_STORE_PATH)
CONFIG = _read_source(CONFIG_PATH)
PRESET = _read_source(PRESET_PATH)

SKIP_MSG = (
    f"repo MQL source not found: {TRADE_ENGINE_PATH} is missing; "
    "set PO3_MQL_INCLUDE_ROOT to the repository's 'MT5_PO3_Codex Include' "
    "directory (the deployed terminal tree is intentionally not used here)"
)


def _require(source: str | None, path: Path) -> str:
    """Guard for sources other than TradeEngine.mqh that share the include tree."""
    if source is None:
        raise unittest.SkipTest(f"source file not found: {path}")
    return source


def _config_default(source: str, name: str, value: int) -> bool:
    """True when ``input int <name> = <value>;`` -- whitespace-tolerant.

    Config.mqh column-aligns its input declarations, so the spacing between a
    switch name, its ``=`` and its value is formatting, not semantics.  The
    invariant under test is the DEFAULT VALUE, which a plain substring check
    would tie to an accidental number of spaces (e.g.
    ``InpPendingAiTimeoutMin   = 45;`` uses three).
    """

    return re.search(rf"{re.escape(name)}\s*=\s*{value}\s*;", source) is not None


# --------------------------------------------------------------------------
# 2) MQL source contract
# --------------------------------------------------------------------------


@unittest.skipIf(TRADE_ENGINE is None, SKIP_MSG)
class PendingAiRecoveryMqlSourceTests(unittest.TestCase):
    """The pending-AI group survives, reconciles and re-gates across restarts."""

    # -- REC-001 ---------------------------------------------------------------

    def test_rec_001_pending_ai_transitions_checkpoint_durable_state(self) -> None:
        """Enqueue, tester-cache enqueue and group removal each checkpoint now.

        The mission's ``_QueuePendingAi`` does not exist; the real enqueue
        owners are ``_QueueCandidateGroup`` (live) and ``_QueueTesterCachedDecision``
        (tester cache hit) -- both are scoped here.
        """
        for name in ("_QueueCandidateGroup", "_QueueTesterCachedDecision", "_RemovePendingGroup"):
            with self.subTest(fn=name):
                self.assertIn("_PersistPendingAiState", _function_body(TRADE_ENGINE, name))
        # Enqueue x2, removal, ProcessPendingAI drop loop, Deinit, reconcile-start
        # and reconcile-end -- every material transition checkpoints, far beyond
        # the four the requirement demands.
        self.assertGreaterEqual(TRADE_ENGINE.count("_PersistPendingAiState();"), 4)
        self.assertIn("_PersistPendingAiState", _function_body(TRADE_ENGINE, "ProcessPendingAI"))
        self.assertIn("_PersistPendingAiState", _function_body(TRADE_ENGINE, "Deinit"))
        self.assertIn("_PersistPendingAiState", _function_body(TRADE_ENGINE, "_ReconcileRestoredPendingAi"))

    # -- REC-002 ---------------------------------------------------------------

    def test_rec_002_deinit_preserves_live_pending_and_still_fails_tester_closed(self) -> None:
        deinit = _function_body(TRADE_ENGINE, "Deinit")
        self.assertIn("tester_runtime", deinit)
        self.assertIn("preserved_for_restart_recovery", deinit)
        # The clear-to-empty resize belongs to the tester branch alone; a live
        # Deinit must keep the restored groups for REC-003 reconciliation.
        self.assertEqual(deinit.count("ArrayResize(m_pending_ai, 0)"), 1)
        self.assertIn("archived_shutdown_and_failed_closed", deinit)

    # -- REC-003 ---------------------------------------------------------------

    def test_rec_003_startup_reconciliation_reads_bus_evidence_never_the_provider(self) -> None:
        recon = _function_body(TRADE_ENGINE, "_ReconcileRestoredPendingAi")
        for token in (
            "ProcessingDir()",
            "_ReqPath(",
            "_RespPath(",
            "_ArchivePendingArtifacts",
            "recovered_request_deadline_expired",
            "recovered_request_response_and_processing_artifacts_missing",
            "recovered_pending_plan_invalid_contract",
            "consume_completed_response",
            "await_inflight_request",
        ):
            with self.subTest(token=token):
                self.assertIn(token, recon)
        # Reconciliation consumes durable artifacts; it must not recall the AI
        # provider for an identity that was already paid for.
        self.assertNotIn("SendRequestCandidates", recon)
        self.assertNotIn("SendRequest(", recon)

    def test_rec_003_init_wires_reconciliation_and_keeps_tester_shutdown_archival(self) -> None:
        init = _function_body(TRADE_ENGINE, "Init")
        self.assertIn("_ReconcileRestoredPendingAi", init)
        self.assertIn('_ArchivePendingArtifacts(req_id, "shutdown")', init)

    # -- REC-004 / REC-005 -------------------------------------------------------

    def test_rec_004_process_pending_ai_is_the_single_authority_path_into_the_watchlist(self) -> None:
        pp = _function_body(TRADE_ENGINE, "ProcessPendingAI")
        # One door into the watchlist, and the recovered gate stands in front of it.
        self.assertEqual(pp.count("_AddToWatchlist(selected)"), 1)
        self.assertIn("_IsRecoveredPendingReq(req_ids[r])", pp)
        self.assertIn("_RecoveredEntryWithinContract(selected, recovered_entry_reason)", pp)
        gate = pp.find("_IsRecoveredPendingReq(req_ids[r])")
        contract = pp.find("_RecoveredEntryWithinContract(selected, recovered_entry_reason)")
        add = pp.find("_AddToWatchlist(selected)")
        self.assertGreaterEqual(gate, 0)
        self.assertLess(gate, add)
        self.assertLess(contract, add)

    def test_rec_005_recovered_entry_gate_uses_the_execution_adjustment_contract(self) -> None:
        gate = _function_body(TRADE_ENGINE, "_RecoveredEntryWithinContract")
        for token in (
            "_BuildExecutionAdjustmentContract",
            "_ContractMaxEntryDrift",
            "recovered_entry_drift_exceeds_contract",
            "never_chase",
        ):
            with self.subTest(token=token):
                self.assertIn(token, gate)
        pp = _function_body(TRADE_ENGINE, "ProcessPendingAI")
        self.assertIn('_ArchivePendingArtifacts(req_ids[r], "rejected")', pp)
        # The rejection archive sits inside the recovered block: after the gate
        # opens and before the watchlist call the gate must clear.
        self.assertLess(
            pp.find("_RecoveredEntryWithinContract(selected, recovered_entry_reason)"),
            pp.find('_ArchivePendingArtifacts(req_ids[r], "rejected")'),
        )
        self.assertLess(
            pp.find('_ArchivePendingArtifacts(req_ids[r], "rejected")'),
            pp.find("_AddToWatchlist(selected)"),
        )

    def test_stale_response_falls_back_to_server_age_when_the_wall_anchor_is_lost(self) -> None:
        pp = _function_body(TRADE_ENGINE, "ProcessPendingAI")
        self.assertIn("wall_late", pp)
        self.assertIn("age_late", pp)
        self.assertIn("ai_response_late_wall_deadline", pp)
        self.assertIn("ai_response_late_server_age", pp)

    # -- REC-006 ---------------------------------------------------------------

    def test_rec_006_recovered_marks_follow_the_group_lifetime(self) -> None:
        self.assertIn("_ForgetRecoveredPendingReq", _function_body(TRADE_ENGINE, "_RemovePendingGroup"))
        # Pruner exists and is brace-scannable; reconciliation marks admissions.
        _function_body(TRADE_ENGINE, "_PruneRecoveredPendingReqIds")
        self.assertIn("_MarkRecoveredPendingReq", _function_body(TRADE_ENGINE, "_ReconcileRestoredPendingAi"))

    # -- OBS-001 ---------------------------------------------------------------

    def test_obs_001_recovery_surface_is_observable(self) -> None:
        for token in (
            "[pending_ai_recovery_summary]",
            "[pending_ai_recovery]",
            "[recovered_entry_gate]",
            "m_recovered_groups_total",
            "m_recovered_consume_response_total",
            "m_recovered_await_inflight_total",
            "m_recovered_missing_artifacts_total",
            "m_recovered_deadline_expired_total",
            "m_recovered_invalid_contract_total",
            "m_recovered_entry_gate_rejected_total",
        ):
            with self.subTest(token=token):
                self.assertIn(token, TRADE_ENGINE)

    # -- serialization ----------------------------------------------------------

    def test_wall_anchor_is_serialized_in_both_directions(self) -> None:
        state_store = _require(STATE_STORE, STATE_STORE_PATH)
        # A restart without ai_requested_wall_ms would silently disable the
        # wall-clock late-response check (REC: stale fallback) for recovered groups.
        self.assertGreaterEqual(state_store.count('"ai_requested_wall_ms"'), 2)
        self.assertIn("ai_requested_wall_ms", _function_body(state_store, "PlanToJson"))
        self.assertIn("ai_requested_wall_ms", _function_body(state_store, "PlanFromJson"))

    # -- RET-001 ------------------------------------------------------------------

    def test_ret_001_config_default_shadow_horizon_is_three_days(self) -> None:
        config = _require(CONFIG, CONFIG_PATH)
        self.assertTrue(_config_default(config, "InpShadowCandidateHorizonMinutes", 4320))

    def test_ret_001_preset_requests_the_three_day_horizon(self) -> None:
        preset = _require(PRESET, PRESET_PATH)
        self.assertIn("InpShadowCandidateHorizonMinutes=4320", preset)
        self.assertNotIn("InpShadowCandidateHorizonMinutes=480", preset)

    def test_distinct_horizon_and_expiry_concepts_remain_unchanged(self) -> None:
        """The 4320-retirement touches ONLY the shadow outcome horizon.

        Counterfactual horizon, pending-order expiry and pending-AI timeout are
        different clocks with different owners; a search-replace "fix" that
        conflates them is exactly what this pins against.
        """
        config = _require(CONFIG, CONFIG_PATH)
        self.assertTrue(_config_default(config, "InpCounterfactualHorizonMinutes", 1440))
        self.assertTrue(_config_default(config, "InpPendingOrderExpiryMin", 480))
        self.assertTrue(_config_default(config, "InpPendingAiTimeoutMin", 45))
        preset = _require(PRESET, PRESET_PATH)
        self.assertIn("InpCounterfactualHorizonMinutes=480", preset)
        self.assertIn("InpPendingOrderExpiryMin=120", preset)
        self.assertIn("InpShadowIdentityRetentionMinutes=4320", preset)

    # -- RET-002 ------------------------------------------------------------------

    def test_ret_002_restored_trackers_extend_to_the_canonical_horizon(self) -> None:
        mig = _function_body(TRADE_ENGINE, "_MigrateRestoredShadowHorizon")
        self.assertIn("canonical_horizon", mig)
        self.assertIn("session_capped", mig)
        # Never shorten: an already-later (explicit or session-capped) horizon wins.
        self.assertIn("if(p.shadow_horizon_at >= canonical_horizon) return false;", mig)

    def test_ret_002_restore_migrates_logs_saves_and_persists_the_index(self) -> None:
        rst = _function_body(TRADE_ENGINE, "_RestoreShadowPendingTrackers")
        for token in (
            "horizon_migrated",
            "[shadow_horizon_migration]",
            "m_state.SavePlans(m_state.ShadowPendingPath()",
            "_PersistShadowTrackerIndex()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, rst)
        # The migration must not touch the RESOLVED identity index: a terminal
        # outcome stays terminal across a restart (terminal-once).  The literal
        # pair "m_shadow_resolved_variants, p.shadow_candidate_variant_id" does
        # appear in restore -- pre-existing at HEAD and still -- inside the pure
        # membership read _ShadowIndexContains(...) that DROPS already-resolved
        # trackers.  The honest invariant is that no line may MUTATE the
        # resolved index, so every mention must be that read and no extend/add
        # helper may target it.
        mentions = [
            line for line in rst.splitlines()
            if "m_shadow_resolved_variants, p.shadow_candidate_variant_id" in line
        ]
        for line in mentions:
            self.assertIn("_ShadowIndexContains(", line, f"resolved index mutated on restore: {line.strip()}")
        self.assertNotIn("_ShadowIndexExtendExpiry(m_shadow_resolved_variants", rst)
        self.assertNotIn("_ShadowIndexAdd(m_shadow_resolved_variants", rst)

    def test_ret_002_identity_expiry_never_shortens_and_tracks_the_horizon(self) -> None:
        ext = _function_body(TRADE_ENGINE, "_ShadowIndexExtendExpiry")
        self.assertIn(">= expires_at", ext)  # an existing later expiry is kept
        expiry = _function_body(TRADE_ENGINE, "_ShadowIdentityExpiry")
        self.assertIn("horizon_sec", expiry)
        self.assertIn("retention_sec", expiry)

    # -- SHD-001 / SHD-002 ----------------------------------------------------------

    def test_shd_001_restore_preserves_accumulated_progress(self) -> None:
        """Resetting a restored tracker's progress is the fresh-observation writer's job."""
        rst = _function_body(TRADE_ENGINE, "_RestoreShadowPendingTrackers")
        for token in (
            "shadow_scan_cursor = 0",
            "shadow_progress_mask = 0",
            "shadow_mfe_r = 0",
            "shadow_entry_activated = false",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, rst)

    def test_shd_002_decision_attribution_does_not_reset_the_price_path(self) -> None:
        """Recording a decision must not rewind the path the tracker already walked."""
        att = _function_body(TRADE_ENGINE, "_WriteShadowDecisionUpdate")
        for token in (
            "shadow_scan_cursor =",
            "shadow_mfe_r =",
            "shadow_mae_r =",
            "shadow_assessed_entry =",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, att)


# --------------------------------------------------------------------------
# 3) Falsification against the pre-change tree
# --------------------------------------------------------------------------


class PendingAiRecoveryFalsificationTests(unittest.TestCase):
    """The source assertions above pin NEW behaviour, not incumbent prose.

    Every token asserted here absent was absent before the change; every value
    asserted present is the value the change replaced.  If this class goes red
    because a token suddenly appears at HEAD, the working-tree diff is no longer
    the change under test.
    """

    def _git_show(self, rev_path: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", rev_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise unittest.SkipTest("git unavailable")
        return proc.stdout

    def test_pre_change_trade_engine_lacks_the_recovery_surface(self) -> None:
        head = self._git_show("HEAD:MT5_PO3_Codex Include/TradeEngine.mqh")
        for token in (
            "_ReconcileRestoredPendingAi",
            "_RecoveredEntryWithinContract",
            "preserved_for_restart_recovery",
            "age_late",
            "_MigrateRestoredShadowHorizon",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, head)

    def test_pre_change_config_default_shadow_horizon_was_1440(self) -> None:
        head = self._git_show("HEAD:MT5_PO3_Codex Include/Config.mqh")
        self.assertIn("InpShadowCandidateHorizonMinutes = 1440", head)

    def test_pre_change_preset_shadow_horizon_was_480(self) -> None:
        head = self._git_show("HEAD:scalp_v2_full.set")
        self.assertIn("InpShadowCandidateHorizonMinutes=480", head)


# --------------------------------------------------------------------------
# 4) Real offline file-bus recovery scenario
# --------------------------------------------------------------------------


BUS_DIRS = ("requests", "processing", "responses", "stale", "timed_out", "quarantined")

RESPONSE_REUSE_REASONS = {
    "preexisting_response_reused",
    "completed_response_reused",
    "validated_response_reused",
}


def _identity(request_id: str, identity_hash: str) -> dict:
    """Keyword bundle for RequestIdempotencyLedger.begin() for one identity."""
    return {
        "request_id": request_id,
        "request_identity_hash": identity_hash,
        "provider_id": "opencode-go",
        "model_id": "qwen3.8-flash",
        "prompt_contract_version": "prompt_v12",
        "schema_fingerprint": "fp-test-0001",
    }


class FileBusRecoveryScenarioTests(unittest.TestCase):
    """The Python side of the same restart contract, executed for real offline.

    ``_ReconcileRestoredPendingAi`` (MQL) and ``RequestIdempotencyLedger``
    (Python worker) read the SAME bus layout and obey the SAME rule: durable
    evidence is consumed exactly once and the provider is never recalled for a
    completed or in-flight identity.  These tests execute the production ledger
    and therefore need no MQL source.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for name in BUS_DIRS:
            (self.root / name).mkdir()

    def _ledger(self) -> RequestIdempotencyLedger:
        return RequestIdempotencyLedger(self.root / "request_ledger", worker_id="w-recovery")

    def _write_response(self, request_id: str, identity_hash: str) -> Path:
        path = self.root / "responses" / f"{request_id}.json"
        path.write_text(
            json.dumps(
                {
                    "id": request_id,
                    "request_identity_hash": identity_hash,
                    "provider_id": "opencode-go",
                    "prompt_contract_version": "prompt_v12",
                    "decision_quality_tier": "FULL_STRUCTURED",
                    "mandatory_fields_complete": True,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_response_present_before_startup_is_reused_not_recalled(self) -> None:
        """The MQL ``consume_completed_response`` path, proven on the real ledger.

        A response that landed before the process (re)started is terminal
        evidence: the first ``begin`` for that identity must REUSE it exactly
        once and never dispatch a provider call.
        """
        req = "req-recovery-complete-0001"
        identity = "1" * 64
        response_path = self._write_response(req, identity)
        disposition = self._ledger().begin(
            response_path=response_path, stale_after_sec=60, **_identity(req, identity)
        )
        self.assertEqual(disposition.action, "REUSE")
        self.assertIsNotNone(disposition.response)
        self.assertIn(disposition.reason, RESPONSE_REUSE_REASONS)
        # The reused payload is the identity-bound response, not a placeholder.
        self.assertEqual(disposition.response["id"], req)
        self.assertEqual(disposition.response["request_identity_hash"], identity)

    def test_inflight_request_with_fresh_heartbeat_is_awaited(self) -> None:
        """The MQL ``await_inflight_request`` path: an active claim is never doubled."""
        req = "req-recovery-inflight-0001"
        identity = "2" * 64
        ledger = self._ledger()
        kwargs = _identity(req, identity)
        response_path = self.root / "responses" / f"{req}.json"
        first = ledger.begin(response_path=response_path, stale_after_sec=60, **kwargs)
        self.assertEqual(first.action, "PROCESS")
        second = ledger.begin(response_path=response_path, stale_after_sec=60, **kwargs)
        self.assertEqual(second.action, "ACTIVE")
        self.assertEqual(second.reason, "duplicate_request_already_running")

    def test_stale_claim_is_recovered_once(self) -> None:
        """A dead worker's claim is taken over -- but only after it goes stale.

        The ledger row is rewritten with a 10,000s-old heartbeat (far past the
        60s budget); the next ``begin`` re-dispatches with reason
        ``new_or_stale_claim``.  Exactly one takeover: a fresh second claim
        then reads ACTIVE again.
        """
        req = "req-recovery-stale-0001"
        identity = "3" * 64
        ledger = self._ledger()
        kwargs = _identity(req, identity)
        response_path = self.root / "responses" / f"{req}.json"
        self.assertEqual(ledger.begin(response_path=response_path, stale_after_sec=60, **kwargs).action, "PROCESS")

        row_path = ledger._path(req)
        row = json.loads(row_path.read_text(encoding="utf-8"))
        stale = time.time() - 10_000
        row["heartbeat_at"] = stale
        row["updated_at"] = stale
        row_path.write_text(json.dumps(row), encoding="utf-8")

        takeover = ledger.begin(response_path=response_path, stale_after_sec=60, **kwargs)
        self.assertEqual(takeover.action, "PROCESS")
        self.assertEqual(takeover.reason, "new_or_stale_claim")

        # The takeover owns a fresh claim now; a third begin must not fork the work.
        third = ledger.begin(response_path=response_path, stale_after_sec=60, **kwargs)
        self.assertEqual(third.action, "ACTIVE")

    def test_colliding_identity_fails_closed(self) -> None:
        """One request_id may not answer to two request identities."""
        req = "req-recovery-collision-0001"
        ledger = self._ledger()
        response_path = self.root / "responses" / f"{req}.json"
        first = ledger.begin(
            response_path=response_path, stale_after_sec=60, **_identity(req, "4" * 64)
        )
        self.assertEqual(first.action, "PROCESS")
        collision = ledger.begin(
            response_path=response_path, stale_after_sec=60, **_identity(req, "5" * 64)
        )
        self.assertEqual(collision.action, "COLLISION")
        self.assertEqual(collision.reason, "request_id_identity_collision")
        self.assertIsNone(collision.response)

    def test_bus_layout_classifies_response_processing_and_missing(self) -> None:
        """The sibling-artifact predicate the MQL reconciler runs.

        ``_ReconcileRestoredPendingAi`` evaluates ``has_response`` /
        ``has_processing`` / ``has_request`` over exactly these three sibling
        directories and acts on the combination (consume / await / drop with
        ``recovered_request_response_and_processing_artifacts_missing``).  This
        helper mirrors that read-side classification so the layout itself is
        pinned; it intentionally does NOT execute MQL.
        """

        def classify(req: str) -> str:
            # Response outranks processing outranks request, matching the
            # reconciler's terminal-evidence-first reading of the same siblings.
            for directory, label in (
                ("responses", "response"),
                ("processing", "processing"),
                ("requests", "request"),
            ):
                if (self.root / directory / f"{req}.json").is_file():
                    return label
            return "missing"

        for directory, req in (("requests", "A"), ("processing", "B"), ("responses", "C")):
            (self.root / directory / f"{req}.json").write_text(
                json.dumps({"id": req}), encoding="utf-8"
            )
        self.assertEqual(classify("A"), "request")
        self.assertEqual(classify("B"), "processing")
        self.assertEqual(classify("C"), "response")
        self.assertEqual(classify("D"), "missing")


if __name__ == "__main__":
    unittest.main()
