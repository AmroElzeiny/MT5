"""Versioned family context selected by the deterministic setup taxonomy.

Profiles describe the evidence contract already produced by MQL.  They do not
classify setups and they do not add trading rules or thresholds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from governance_contracts import SETUP_TAXONOMY_VERSION, SetupTaxonomy


FAMILY_PROFILE_VERSION = "20260718_family_context_v1"


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
    )


FAMILY_CONTEXT_REGISTRY: Mapping[str, FamilyContextProfile] = {
    SetupTaxonomy.MICRO_FVG_MID_REVERSAL.value: _profile(
        SetupTaxonomy.MICRO_FVG_MID_REVERSAL,
        "micro reversal from the deterministic FVG midpoint entry branch",
        ("declared source context", "deterministic displacement/FVG", "midpoint entry"),
        ("fvg_mid", "micro_mid", "midpoint"),
        ("range", "reversal", "transition"),
        ("weak displacement", "late midpoint entry", "near opposing liquidity"),
    ),
    SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL.value: _profile(
        SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL,
        "micro reversal from the deterministic FVG edge entry branch",
        ("declared source context", "deterministic displacement/FVG", "edge entry"),
        ("fvg_edge", "micro_edge", "edge"),
        ("range", "reversal", "transition"),
        ("edge already mitigated", "weak origin", "late entry"),
    ),
    SetupTaxonomy.MICRO_BREAKER_RETEST.value: _profile(
        SetupTaxonomy.MICRO_BREAKER_RETEST,
        "micro breaker retest using the deterministic breaker identity and retest state",
        ("declared source context", "breaker formation", "clean retest"),
        ("breaker_retest", "micro_breaker"),
        ("reversal", "transition", "trend pullback"),
        ("non-virgin breaker", "weak origin", "retest invalidation"),
    ),
    SetupTaxonomy.MICRO_OTE_REVERSAL.value: _profile(
        SetupTaxonomy.MICRO_OTE_REVERSAL,
        "micro reversal at the deterministic OTE branch",
        ("declared source context", "displacement", "OTE retracement"),
        ("ote", "micro_ote"),
        ("reversal", "trend pullback"),
        ("OTE reached without confirmation", "sequence conflict", "target obstruction"),
    ),
    SetupTaxonomy.MICRO_CONTINUATION_FVG.value: _profile(
        SetupTaxonomy.MICRO_CONTINUATION_FVG,
        "micro continuation through a deterministically classified continuation FVG",
        ("trend context", "continuation displacement", "continuation FVG entry"),
        ("continuation_fvg", "fvg_mid", "fvg_edge"),
        ("trend", "expansion"),
        ("stale FVG", "touched continuation without clean retest", "trend exhaustion"),
    ),
    SetupTaxonomy.MICRO_NESTED_CONTINUATION.value: _profile(
        SetupTaxonomy.MICRO_NESTED_CONTINUATION,
        "nested micro continuation inside deterministic higher-order context",
        ("parent context", "nested displacement", "nested entry"),
        ("nested_continuation", "nested_fvg"),
        ("trend", "expansion"),
        ("parent-child contradiction", "stale nested FVG", "overextended entry"),
    ),
    SetupTaxonomy.MICRO_RANGE_REENTRY.value: _profile(
        SetupTaxonomy.MICRO_RANGE_REENTRY,
        "micro reentry into a deterministic range after the declared range event",
        ("range identity", "range excursion", "range reentry"),
        ("range_reentry", "micro_range"),
        ("range", "compression"),
        ("range expansion underway", "opposite confirmed structure", "poor target clearance"),
    ),
    SetupTaxonomy.MICRO_SESSION_REENTRY.value: _profile(
        SetupTaxonomy.MICRO_SESSION_REENTRY,
        "micro session reentry using deterministic session boundaries and timing",
        ("session range identity", "session excursion", "session reentry"),
        ("session_reentry", "micro_session"),
        ("session transition", "range"),
        ("off-session timing", "session objective already reached", "stale session context"),
    ),
    SetupTaxonomy.FAILED_BREAKOUT_RECLAIM.value: _profile(
        SetupTaxonomy.FAILED_BREAKOUT_RECLAIM,
        "failed breakout followed by deterministic reclaim evidence",
        ("breakout", "failure", "reclaim", "entry confirmation"),
        ("failed_breakout_reclaim", "reclaim"),
        ("range", "transition", "reversal"),
        ("no true reclaim", "continued breakout acceptance", "late reclaim"),
    ),
    SetupTaxonomy.FULL_PO3_REVERSAL.value: _profile(
        SetupTaxonomy.FULL_PO3_REVERSAL,
        "full PO3 reversal using the deterministic range, sweep, displacement, and BOS sequence",
        ("dealing range", "liquidity sweep", "displacement", "BOS", "entry"),
        ("full_po3", "po3_reversal", "fvg_mid", "fvg_edge"),
        ("reversal", "transition"),
        ("sequence contradiction", "superseded sweep", "target obstruction"),
    ),
    SetupTaxonomy.FULL_PO3_CONTINUATION.value: _profile(
        SetupTaxonomy.FULL_PO3_CONTINUATION,
        "full PO3 continuation using the deterministic continuation sequence",
        ("dealing range", "liquidity event", "continuation displacement", "BOS", "entry"),
        ("full_po3_continuation", "continuation_fvg"),
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

