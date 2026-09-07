"""Python-owned evidence catalog: bounded IDs instead of free-form paths.

The analyst used to return ``evidence_refs: list[str]`` and Python resolved each
string against a deeply nested canonical envelope.  For a three-candidate
request that envelope exposes ~1,600 leaf paths and for six candidates ~3,200,
none of which the model is ever shown as a vocabulary.  Asking a model to spell
exact internal paths out of a namespace that large is a guessing game, and every
miss became ``decision_evidence_reference_reject`` -- a full downgrade of an
otherwise valid FULL_STRUCTURED response.

This module makes evidence references deterministic:

* Python enumerates a bounded, curated catalog before the provider call.
* Each item gets a small integer ``evidence_id``, a canonical path, the observed
  value, a value hash, and an authority label -- all Python-owned.
* The provider schema returns ``evidence_ref_ids: list[int]`` only.
* Python validates existence, candidate scope, and duplicates, then maps IDs
  back to canonical paths and hashes for the authoritative envelope.

The model never creates canonical paths, hashes, authority labels, or identity.
A legacy string parser exists for migration of previously cached responses only;
it resolves to real catalog items and never invents evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence


EVIDENCE_CATALOG_VERSION = "20260730_python_owned_evidence_catalog_v1"

AUTHORITY_DETERMINISTIC = "deterministic"
AUTHORITY_DIAGNOSTIC = "diagnostic_only"

# Global (request-scoped) sections the analyst may legitimately cite.  Each entry
# is (section, tuple-of-leaf-names) -- an empty tuple means "every scalar leaf".
_GLOBAL_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sequence", ()),
    ("liquidity", ()),
    ("setup_taxonomy", ("taxonomy_version", "family_profile_version")),
    ("validation", ("missing_fields", "invalid_fields", "hard_blockers")),
    ("instrument", ()),
    ("market_regime", ()),
    ("htf_context", ()),
    ("ltf_execution", ()),
    ("risk", ("deterministic_only",)),
)

# Per-candidate scalar fields worth citing, beyond the numeric evidence block.
_CANDIDATE_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "candidate_hash",
    "symbol",
    "direction",
    "is_buy",
    "setup_code",
    "setup_family",
    "setup_class",
    "setup_taxonomy_enum",
    "entry_branch",
    "entry_model",
    "session_name",
    "session_code",
    "killzone_code",
    "asset_class",
    "regime_profile",
    "volatility_profile",
    "target_source",
    "target_model",
    "obstacle_kind",
    "obstacle_tf",
    "fvg_lower",
    "fvg_upper",
    "fvg_mid",
    "fvg_fresh",
    "fvg_touched",
    "fvg_retested",
    "structure_state",
    "source_t_sweep",
    "source_t_disp",
    "source_t_bos",
    "po3_state",
    "po3_state_reason",
    "po3_scope",
    "source_context_tier",
    "structure_type",
    "final_setup_class",
    "fvg_execution_class",
    "fvg_mitigation_state",
    "fvg_invalidation_reason",
    "fvg_continuation",
    "fvg_reversal",
    "fvg_context_type",
    "fvg_mid_mitigated",
    "fvg_fully_filled",
    "fvg_invalidated",
    "fvg_entry_invalid",
    "fvg_structure_invalidated",
    "fvg_score",
    "origin_score",
    "cleanliness_score",
    "freshness_score",
    "retest_quality_score",
    "continuation_score",
    "reversal_score",
    "displacement_candle_score",
    "opposing_obstruction_score",
    "stop_model",
    "configured_stop_model",
    "stop_quality_score",
    "target_arbitration_required",
    "liquidity_target_valid_structurally",
    "liquidity_target_blocked_by_obstacle",
    "historical_evidence_state",
    "retrieved_analogue_ids",
    "rule_score",
    "family_scope",
    "full_po3_sequence_required",
    "htf_bos_required",
    "htf_bos_observed",
    "htf_bos_absence_classification",
    "follow_through_required",
    "follow_through_observed",
    "follow_through_absence_classification",
)

_MAX_SCALAR_CHARS = 160


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: int
    candidate_index: int | None
    canonical_path: str
    value: Any
    value_hash: str
    authority: str

    def as_catalog_row(self) -> dict[str, Any]:
        """Compact provider-facing row.

        Short keys deliberately: this block is repeated once per item and the
        six-candidate payload is already the dominant latency cost.
        """

        row: dict[str, Any] = {"id": self.evidence_id, "p": self.canonical_path, "v": self.value}
        if self.candidate_index is not None:
            row["c"] = self.candidate_index
        return row

    def as_envelope_row(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "candidate_index": self.candidate_index,
            "canonical_path": self.canonical_path,
            "value_hash": self.value_hash,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class EvidenceResolution:
    valid: bool
    resolved: tuple[EvidenceItem, ...]
    unknown_ids: tuple[Any, ...]
    cross_candidate_ids: tuple[int, ...]
    duplicate_ids: tuple[int, ...]

    @property
    def resolved_paths(self) -> tuple[str, ...]:
        return tuple(item.canonical_path for item in self.resolved)


def _value_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_SCALAR_CHARS:
        return value[:_MAX_SCALAR_CHARS]
    return value


@dataclass(frozen=True)
class EvidenceCatalog:
    """Deterministic, bounded evidence surface for one frozen request."""

    catalog_version: str
    items: tuple[EvidenceItem, ...]
    catalog_hash: str
    _by_id: Mapping[int, EvidenceItem] = field(repr=False, default_factory=dict)
    _by_path: Mapping[str, EvidenceItem] = field(repr=False, default_factory=dict)

    def __len__(self) -> int:
        return len(self.items)

    def get(self, evidence_id: Any) -> EvidenceItem | None:
        if isinstance(evidence_id, bool) or not isinstance(evidence_id, int):
            return None
        return self._by_id.get(evidence_id)

    def by_path(self, canonical_path: str) -> EvidenceItem | None:
        return self._by_path.get(str(canonical_path))

    def provider_rows(self) -> list[dict[str, Any]]:
        return [item.as_catalog_row() for item in self.items]

    def resolve(
        self,
        evidence_ref_ids: Iterable[Any],
        *,
        candidate_index: int | None,
    ) -> EvidenceResolution:
        """Validate returned IDs and map them to canonical evidence.

        Fails closed on unknown IDs and on candidate-scoped references that
        belong to a different candidate.  Global items are citable by every
        candidate; a candidate may never cite another candidate's evidence.
        """

        resolved: list[EvidenceItem] = []
        unknown: list[Any] = []
        cross: list[int] = []
        duplicates: list[int] = []
        seen: set[int] = set()

        for raw in evidence_ref_ids or ():
            item = self.get(raw)
            if item is None:
                unknown.append(raw)
                continue
            if item.evidence_id in seen:
                duplicates.append(item.evidence_id)
                continue
            if (
                item.candidate_index is not None
                and candidate_index is not None
                and item.candidate_index != candidate_index
            ):
                cross.append(item.evidence_id)
                continue
            seen.add(item.evidence_id)
            resolved.append(item)

        return EvidenceResolution(
            valid=not unknown and not cross and not duplicates and bool(resolved),
            resolved=tuple(resolved),
            unknown_ids=tuple(unknown),
            cross_candidate_ids=tuple(cross),
            duplicate_ids=tuple(duplicates),
        )


def _append(
    items: list[EvidenceItem],
    *,
    candidate_index: int | None,
    canonical_path: str,
    value: Any,
    authority: str = AUTHORITY_DETERMINISTIC,
) -> None:
    compact = _compact_scalar(value)
    items.append(
        EvidenceItem(
            evidence_id=len(items),
            candidate_index=candidate_index,
            canonical_path=canonical_path,
            value=compact,
            value_hash=_value_hash(value),
            authority=authority,
        )
    )


def build_evidence_catalog(envelope: Mapping[str, Any]) -> EvidenceCatalog:
    """Enumerate the citable evidence surface for a canonical envelope.

    Deterministic in both content and ordering, so the same frozen request
    always produces the same IDs and the same catalog hash.
    """

    items: list[EvidenceItem] = []

    for section, allowed in _GLOBAL_SECTIONS:
        body = envelope.get(section)
        if not isinstance(body, Mapping):
            continue
        for key in sorted(body):
            if allowed and key not in allowed:
                continue
            value = body[key]
            if _is_scalar(value):
                _append(items, candidate_index=None, canonical_path=f"{section}.{key}", value=value)
            elif isinstance(value, list) and all(_is_scalar(entry) for entry in value):
                _append(items, candidate_index=None, canonical_path=f"{section}.{key}", value=list(value))

    candidates = envelope.get("entry_and_invalidation")
    rows = candidates.get("candidates") if isinstance(candidates, Mapping) else None
    if isinstance(rows, list):
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            index = int(row.get("candidate_index", position))
            base = f"entry_and_invalidation.candidates.{position}"
            for name in _CANDIDATE_FIELDS:
                if name not in row:
                    continue
                value = row[name]
                if _is_scalar(value):
                    _append(
                        items,
                        candidate_index=index,
                        canonical_path=f"{base}.{name}",
                        value=value,
                    )
            numbers = row.get("authoritative_numbers")
            if isinstance(numbers, Mapping):
                for name in sorted(numbers):
                    body = numbers[name]
                    if not isinstance(body, Mapping):
                        continue
                    _append(
                        items,
                        candidate_index=index,
                        canonical_path=f"{base}.authoritative_numbers.{name}.value",
                        value=body.get("value"),
                    )
            family_requirements = row.get("family_requirement_contract")
            if isinstance(family_requirements, Mapping):
                for name in (
                    "context_version",
                    "family_scope",
                    "full_po3_sequence_required",
                    "htf_bos_required",
                    "htf_bos_observed",
                    "htf_bos_absence_classification",
                    "follow_through_required",
                    "follow_through_observed",
                    "follow_through_absence_classification",
                    "required_event_sequence",
                ):
                    if name not in family_requirements:
                        continue
                    value = family_requirements[name]
                    if _is_scalar(value) or (
                        isinstance(value, list) and all(_is_scalar(entry) for entry in value)
                    ):
                        _append(
                            items,
                            candidate_index=index,
                            canonical_path=f"{base}.family_requirement_contract.{name}",
                            value=value,
                        )

    catalog_hash = sha256(
        json.dumps(
            [item.as_envelope_row() for item in items],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()

    return EvidenceCatalog(
        catalog_version=EVIDENCE_CATALOG_VERSION,
        items=tuple(items),
        catalog_hash=catalog_hash,
        _by_id={item.evidence_id: item for item in items},
        _by_path={item.canonical_path: item for item in items},
    )


# ---------------------------------------------------------------------------
# Legacy migration only.
# ---------------------------------------------------------------------------

_BRACKET_QUOTED = re.compile(r'\[\s*["\']([^"\']+)["\']\s*\]')
_BRACKET_INDEX = re.compile(r"\[\s*(\d+)\s*\]")


def normalize_legacy_reference(raw: Any) -> str:
    """Normalize a legacy free-form reference to dotted canonical form.

    Supports the historical shapes ``a.b.0.c``, ``a.b[0].c``, ``$.a.b[0].c``
    and ``a["b"][0]["c"]``.  Normalization alone proves nothing: the caller must
    still resolve the result against a real catalog item.
    """

    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("$."):
        text = text[2:]
    elif text == "$":
        return ""
    text = _BRACKET_QUOTED.sub(r".\1", text)
    text = _BRACKET_INDEX.sub(r".\1", text)
    text = text.replace("..", ".")
    return text.strip(".")


def resolve_legacy_references(
    catalog: EvidenceCatalog,
    references: Sequence[Any],
    *,
    candidate_index: int | None,
) -> tuple[EvidenceResolution, dict[str, Any]]:
    """Migrate legacy path strings to catalog IDs, fail-closed.

    Returns the resolution plus diagnostics naming the first token that could
    not be matched, so an operator can see exactly where a legacy reference
    diverged from the catalog instead of a generic rejection.
    """

    ids: list[Any] = []
    diagnostics: dict[str, Any] = {"raw_references": [], "normalized_references": []}
    unresolved: list[dict[str, Any]] = []

    for raw in references or ():
        normalized = normalize_legacy_reference(raw)
        diagnostics["raw_references"].append(str(raw))
        diagnostics["normalized_references"].append(normalized)
        item = catalog.by_path(normalized) if normalized else None
        if item is not None:
            ids.append(item.evidence_id)
            continue
        unresolved.append(
            {
                "raw_reference": str(raw),
                "normalized_reference": normalized,
                "first_missing_token": _first_missing_token(catalog, normalized),
                "nearest_valid_paths": _nearest_paths(catalog, normalized),
            }
        )

    resolution = catalog.resolve(ids, candidate_index=candidate_index)
    if unresolved:
        resolution = EvidenceResolution(
            valid=False,
            resolved=resolution.resolved,
            unknown_ids=tuple(entry["raw_reference"] for entry in unresolved),
            cross_candidate_ids=resolution.cross_candidate_ids,
            duplicate_ids=resolution.duplicate_ids,
        )
    diagnostics["unresolved"] = unresolved
    return resolution, diagnostics


def _first_missing_token(catalog: EvidenceCatalog, normalized: str) -> str:
    """The first path token that no catalog entry shares as a prefix."""

    if not normalized:
        return ""
    prefixes = {""}
    for item in catalog.items:
        parts = item.canonical_path.split(".")
        for depth in range(1, len(parts) + 1):
            prefixes.add(".".join(parts[:depth]))
    walked: list[str] = []
    for token in normalized.split("."):
        candidate = ".".join(walked + [token])
        if candidate not in prefixes:
            return token
        walked.append(token)
    return ""


def _nearest_paths(catalog: EvidenceCatalog, normalized: str, limit: int = 3) -> list[str]:
    """A few catalog paths sharing the longest prefix with the failed one."""

    if not normalized:
        return []
    tokens = normalized.split(".")
    scored: list[tuple[int, str]] = []
    for item in catalog.items:
        parts = item.canonical_path.split(".")
        shared = 0
        for left, right in zip(tokens, parts):
            if left != right:
                break
            shared += 1
        if shared:
            scored.append((shared, item.canonical_path))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [path for _shared, path in scored[:limit]]
