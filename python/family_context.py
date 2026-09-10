"""Versioned family context selected by the deterministic setup taxonomy.

Profiles describe the evidence contract already produced by MQL.  They do not
classify setups and they do not add trading rules or thresholds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from governance_contracts import (
    BREAKER_RETEST_ENTRY_BRANCHES,
    CONTINUATION_ENTRY_BRANCHES,
    FAILED_BREAKOUT_ENTRY_BRANCHES,
    FULL_PO3_STRUCTURAL_ENTRY_BRANCHES,
    FVG_EDGE_ENTRY_BRANCHES,
    FVG_MID_ENTRY_BRANCHES,
    NESTED_CONTINUATION_ENTRY_BRANCHES,
    OTE_ENTRY_BRANCHES,
    RANGE_REENTRY_ENTRY_BRANCHES,
    SESSION_REENTRY_ENTRY_BRANCHES,
    SETUP_TAXONOMY_VERSION,
    SetupTaxonomy,
)


# v3: permitted_entry_branches now comes from classify_setup_taxonomy's own
# vocabulary.  v2 restated it by hand and drifted into family/state tokens the EA
# never emits as entry_branch ("full_po3_continuation", "continuation_fvg",
# "nested_continuation", "reclaim", "ote", ...), so the payload told the model that
# the very branch which produced the taxonomy was not permitted for it.  Every
# full-PO3 candidate in the 2026-08-18 run was vetoed on that contradiction.
FAMILY_PROFILE_VERSION = "20260908_family_context_v4"


@dataclass(frozen=True)
class FamilyContextProfile:
    taxonomy: str
    canonical_market_hypothesis: str
    required_event_sequence: tuple[str, ...]
    required_temporal_ordering: tuple[str, ...]
    permitted_entry_branches: tuple[str, ...]
    expected_regime: tuple[str, ...]
    htf_alignment_requirements: tuple[str, ...]
    ltf_confirmation_requirements: tuple[str, ...]
    liquidity_objective_logic: tuple[str, ...]
    entry_invalidation_conditions: tuple[str, ...]
    target_feasibility_rules: tuple[str, ...]
    known_failure_modes: tuple[str, ...]
    adverse_context_checks: tuple[str, ...]
    relevant_feature_whitelist: tuple[str, ...]
    historical_retrieval_dimensions: tuple[str, ...]
    family_specific_abstention_reasons: tuple[str, ...]
    deferred_execution_triggers: tuple[str, ...] = ()
    profile_version: str = FAMILY_PROFILE_VERSION
    taxonomy_version: str = SETUP_TAXONOMY_VERSION

    def as_payload(self) -> dict[str, object]:
        return asdict(self)


_COMMON_FEATURES = (
    "candidate_id",
    "candidate_hash",
    "request_execution_fingerprint",
    "setup_taxonomy_enum",
    "setup_family",
    "entry_branch",
    "direction",
    "source_t_sweep",
    "source_t_disp",
    "source_t_bos",
    "entry_est",
    "sl",
    "tp1",
    "tp2",
    "effective_rr2",
    "liquidity_rr",
    "sequence_quality",
    "htf_alignment_score",
    "adverse_context_score",
    "execution_cost_r",
    "spread_r",
    "target_candidates",
    "obstacle_kind",
    "session_name",
    "killzone_code",
    "asset_class",
    "regime_profile",
)


def _profile(
    taxonomy: SetupTaxonomy,
    hypothesis: str,
    sequence: tuple[str, ...],
    branches: tuple[str, ...],
    regimes: tuple[str, ...],
    failures: tuple[str, ...],
    deferred_execution_triggers: tuple[str, ...] = (),
) -> FamilyContextProfile:
    return FamilyContextProfile(
        taxonomy=taxonomy.value,
        canonical_market_hypothesis=hypothesis,
        required_event_sequence=sequence,
        required_temporal_ordering=("use MQL source timestamps; never infer absent events",),
        permitted_entry_branches=branches,
        expected_regime=regimes,
        htf_alignment_requirements=("audit supplied HTF alignment fields only",),
        ltf_confirmation_requirements=("audit supplied displacement, BOS, retest, and confirmation fields only",),
        liquidity_objective_logic=("compare only deterministic target candidates marked available and feasible",),
        entry_invalidation_conditions=("respect deterministic structure, FVG, entry, and stop validity flags",),
        target_feasibility_rules=("never invent target prices", "never select infeasible target candidates"),
        known_failure_modes=failures,
        adverse_context_checks=("obstacle evidence", "spread and execution cost", "session and correlation context"),
        relevant_feature_whitelist=_COMMON_FEATURES,
        historical_retrieval_dimensions=(
            "exact_taxonomy",
            "setup_family_entry_branch",
            "direction_semantics",
            "asset_class",
            "session_killzone",
            "volatility_trend_regime",
            "htf_ltf_alignment",
            "rr_cost_obstacle_liquidity_buckets",
            "stop_target_model",
            "data_quality_tier",
        ),
        family_specific_abstention_reasons=(
            "required sequence evidence unresolved",
            "target choice unstable",
            "historical evidence insufficient",
            "material structured evidence conflict",
        ),
        deferred_execution_triggers=deferred_execution_triggers,
    )


FAMILY_CONTEXT_REGISTRY: Mapping[str, FamilyContextProfile] = {
    SetupTaxonomy.MICRO_FVG_MID_REVERSAL.value: _profile(
        SetupTaxonomy.MICRO_FVG_MID_REVERSAL,
        "micro reversal from the deterministic FVG midpoint entry branch",
        ("declared source context", "deterministic displacement/FVG", "validated midpoint entry plan"),
        FVG_MID_ENTRY_BRANCHES,
        ("range", "reversal", "transition"),
        ("weak displacement", "late midpoint entry", "near opposing liquidity"),
        ("midpoint price touch",),
    ),
    SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL.value: _profile(
        SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL,
        "micro reversal from the deterministic FVG edge entry branch",
        ("declared source context", "deterministic displacement/FVG", "validated edge entry plan"),
        FVG_EDGE_ENTRY_BRANCHES,
        ("range", "reversal", "transition"),
        ("edge already mitigated", "weak origin", "late entry"),
        ("edge price touch",),
    ),
    SetupTaxonomy.MICRO_BREAKER_RETEST.value: _profile(
        SetupTaxonomy.MICRO_BREAKER_RETEST,
        "micro breaker retest using the deterministic breaker identity and retest state",
        ("declared source context", "validated breaker formation"),
        BREAKER_RETEST_ENTRY_BRANCHES,
        ("reversal", "transition", "trend pullback"),
        ("non-virgin breaker", "weak origin", "retest invalidation"),
        ("clean retest",),
    ),
    SetupTaxonomy.MICRO_OTE_REVERSAL.value: _profile(
        SetupTaxonomy.MICRO_OTE_REVERSAL,
        "micro reversal at the deterministic OTE branch",
        ("declared source context", "displacement", "validated OTE/FVG overlap"),
        OTE_ENTRY_BRANCHES,
        ("reversal", "trend pullback"),
        ("OTE reached without confirmation", "sequence conflict", "target obstruction"),
        ("OTE retracement",),
    ),
    SetupTaxonomy.MICRO_CONTINUATION_FVG.value: _profile(
        SetupTaxonomy.MICRO_CONTINUATION_FVG,
        "micro continuation through a deterministically classified continuation FVG",
        ("trend context", "continuation displacement", "validated continuation FVG plan"),
        CONTINUATION_ENTRY_BRANCHES + FVG_MID_ENTRY_BRANCHES + FVG_EDGE_ENTRY_BRANCHES,
        ("trend", "expansion"),
        ("stale FVG", "touched continuation without clean retest", "trend exhaustion"),
        ("continuation FVG entry touch",),
    ),
    SetupTaxonomy.MICRO_NESTED_CONTINUATION.value: _profile(
        SetupTaxonomy.MICRO_NESTED_CONTINUATION,
        "nested micro continuation inside deterministic higher-order context",
        ("parent context", "nested displacement", "validated nested entry plan"),
        NESTED_CONTINUATION_ENTRY_BRANCHES,
        ("trend", "expansion"),
        ("parent-child contradiction", "stale nested FVG", "overextended entry"),
        ("nested entry touch",),
    ),
    SetupTaxonomy.MICRO_RANGE_REENTRY.value: _profile(
        SetupTaxonomy.MICRO_RANGE_REENTRY,
        "micro reentry into a deterministic range after the declared range event",
        ("range identity", "range excursion", "validated range reentry plan"),
        RANGE_REENTRY_ENTRY_BRANCHES,
        ("range", "compression"),
        ("range expansion underway", "opposite confirmed structure", "poor target clearance"),
        ("range reentry price trigger",),
    ),
    SetupTaxonomy.MICRO_SESSION_REENTRY.value: _profile(
        SetupTaxonomy.MICRO_SESSION_REENTRY,
        "micro session reentry using deterministic session boundaries and timing",
        ("session range identity", "session excursion", "validated session reentry plan"),
        SESSION_REENTRY_ENTRY_BRANCHES,
        ("session transition", "range"),
        ("off-session timing", "session objective already reached", "stale session context"),
        ("session reentry price trigger",),
    ),
    SetupTaxonomy.FAILED_BREAKOUT_RECLAIM.value: _profile(
        SetupTaxonomy.FAILED_BREAKOUT_RECLAIM,
        "failed breakout followed by deterministic reclaim evidence",
        ("breakout", "failure", "reclaim", "entry confirmation"),
        FAILED_BREAKOUT_ENTRY_BRANCHES,
        ("range", "transition", "reversal"),
        ("no true reclaim", "continued breakout acceptance", "late reclaim"),
    ),
    SetupTaxonomy.FULL_PO3_REVERSAL.value: _profile(
        SetupTaxonomy.FULL_PO3_REVERSAL,
        "full PO3 reversal using the deterministic range, sweep, displacement, and BOS sequence",
        ("dealing range", "liquidity sweep", "displacement", "BOS", "entry"),
        FULL_PO3_STRUCTURAL_ENTRY_BRANCHES,
        ("reversal", "transition"),
        ("sequence contradiction", "superseded sweep", "target obstruction"),
    ),
    SetupTaxonomy.FULL_PO3_CONTINUATION.value: _profile(
        SetupTaxonomy.FULL_PO3_CONTINUATION,
        "full PO3 continuation using the deterministic continuation sequence",
        ("dealing range", "liquidity event", "continuation displacement", "BOS", "entry"),
        FULL_PO3_STRUCTURAL_ENTRY_BRANCHES + CONTINUATION_ENTRY_BRANCHES,
        ("trend", "expansion"),
        ("trend exhaustion", "stale continuation FVG", "opposite confirmed structure"),
    ),
}


def family_context_for(taxonomy: str) -> FamilyContextProfile:
    value = str(taxonomy or "").strip().upper()
    profile = FAMILY_CONTEXT_REGISTRY.get(value)
    if profile is None:
        raise ValueError("family_context_unknown_taxonomy:" + (value or "missing"))
    return profile
