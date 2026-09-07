from __future__ import annotations

FACTORY_VERSION = "1.0.0"
FACTORY_SCHEMA_VERSION = "20260810_rnd_factory_v1"
REQUEST_SCHEMA_VERSION = "20260810_rnd_ai_request_v1"
RESPONSE_SCHEMA_VERSION = "20260810_rnd_ai_response_v1"
DB_SCHEMA_VERSION = 1

AI_MODES = {"none", "remote", "local"}
RUN_MODES = {"no-ai", "quick", "standard", "deep"}
RECOMMENDATIONS = {
    "NO_ACTION",
    "MORE_DATA_REQUIRED",
    "RESEARCH_EXPERIMENT",
    "SHADOW_TEST_RECOMMENDED",
    "HUMAN_REVIEW_REQUIRED",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
HYPOTHESIS_STATUSES = {
    "proposed",
    "insufficient_evidence",
    "rejected_before_test",
    "approved_for_experiment",
    "experiment_running",
    "experiment_failed",
    "experiment_passed",
    "shadow_candidate",
    "rejected_after_validation",
    "research_complete",
}
PROTECTED_PRODUCTION_PATTERNS = (
    "MT5_PO3_Codex Include",
    "MT5_PO3_Codex Experts",
    "active_policy.json",
    ".set",
)
