"""Lossless provider-wire projection of evidence payloads (OpenCode Go Phase 2, part 4).

The canonical evidence envelope and its ``EvidenceCatalog`` remain the internal,
Python-authoritative contract.  Citation validation (``EvidenceCatalog.resolve``),
identity binding, persistence and every cache key keep using them unchanged.  This
module only changes how catalog rows are ENCODED on the provider wire, and only when
a projection other than ``canonical`` is selected.

compact_v1
    Canonical ``evidence_catalog.items`` is a list of ``{"id", "p", "v"[, "c"]}`` rows.
    Measured on real archived requests, the repeated row key names and the repeated
    ``entry_and_invalidation.candidates.<i>.`` path heads are about half of the
    catalog's serialized size, and the catalog is the largest section of the analyst
    payload.  compact_v1 groups rows by (candidate scope, first path segment), moves
    the longest dotted prefix the group shares into ``p_prefix``, and writes each row
    as ``[id, p_suffix, v]``.  No id, path, value or scope is dropped or rewritten.

    Lossless by construction AND by check: ``expand_evidence_from_wire`` restores the
    canonical payload, and ``project_evidence_for_wire`` returns the canonical payload
    unchanged unless the canonical JSON serialization of the restored payload is
    byte-identical to that of the input.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

WIRE_PROJECTION_ENV = "AI_PROVIDER_WIRE_PROJECTION"
WIRE_PROJECTION_METADATA_KEY = "provider_wire_projection"
WIRE_PROJECTION_CANONICAL = "canonical"
WIRE_PROJECTION_COMPACT_V1 = "compact_v1"
WIRE_PROJECTIONS = (WIRE_PROJECTION_CANONICAL, WIRE_PROJECTION_COMPACT_V1)

COMPACT_V1_WIRE_FORMAT = (
    "compact_v1: items is a list of groups. Each group has p_prefix and rows, and c when "
    "every item in the group belongs to that candidate index; a group without c is global. "
    "Each row is [id, p, v]: the integer evidence id, the canonical path suffix, and the "
    "observed value. The item's full canonical path is the group's p_prefix followed by p."
)

# The analyst prompt describes the catalog encoding, so the sentence must match the
# projection actually sent.  The canonical sentence is the Phase-1 text, byte for byte.
ANALYST_CATALOG_DESCRIPTION = {
    WIRE_PROJECTION_CANONICAL: (
        "The payload contains evidence_catalog.items, where each item has id, p (the "
        "Python-owned canonical path), v (the observed value), and optionally c (the "
        "candidate index it belongs to). Items without c are global."
    ),
    WIRE_PROJECTION_COMPACT_V1: (
        "The payload contains evidence_catalog.items as groups: each group has p_prefix, "
        "rows, and optionally c (the candidate index every item in the group belongs to). "
        "Each row is [id, p, v]: the integer id, the Python-owned canonical path suffix (the "
        "full path is the group's p_prefix followed by p), and v (the observed value). "
        "Groups without c are global."
    ),
}

_REQUIRED_ROW_KEYS = frozenset({"id", "p", "v"})
_ALLOWED_ROW_KEYS = frozenset({"id", "p", "v", "c"})


def normalize_wire_projection(value: Any) -> str | None:
    """Return the projection name, ``canonical`` for empty, or ``None`` if unknown."""

    text = str(value or "").strip().lower()
    if not text:
        return WIRE_PROJECTION_CANONICAL
    return text if text in WIRE_PROJECTIONS else None


def analyst_catalog_description(projection: Any) -> str:
    return ANALYST_CATALOG_DESCRIPTION[
        normalize_wire_projection(projection) or WIRE_PROJECTION_CANONICAL
    ]


def canonical_wire_json(value: Any) -> str:
    """The serialization ``build_provider_exchange_contract`` freezes."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _shared_dotted_prefix(paths: list[str]) -> str:
    segments = [path.split(".") for path in paths]
    shared: list[str] = []
    for parts in zip(*segments):
        if all(part == parts[0] for part in parts):
            shared.append(parts[0])
        else:
            break
    # Every row keeps at least one segment of its own.
    while shared and any(len(parts) <= len(shared) for parts in segments):
        shared.pop()
    return ".".join(shared) + "." if shared else ""


def _compact_catalog_rows(items: Any) -> list[dict[str, Any]] | None:
    """Group canonical rows; ``None`` when the rows are not in the canonical shape."""

    if not isinstance(items, list):
        return None
    previous_id: int | None = None
    order: list[tuple[bool, Any, str]] = []
    grouped: dict[tuple[bool, Any, str], list[Mapping[str, Any]]] = {}
    for row in items:
        if not isinstance(row, Mapping):
            return None
        keys = set(row)
        if not _REQUIRED_ROW_KEYS <= keys or not keys <= _ALLOWED_ROW_KEYS:
            return None
        evidence_id, path = row["id"], row["p"]
        if isinstance(evidence_id, bool) or not isinstance(evidence_id, int) or not isinstance(path, str):
            return None
        # Expansion restores order by id, so ids must already be strictly ascending.
        if previous_id is not None and evidence_id <= previous_id:
            return None
        previous_id = evidence_id
        scoped = "c" in row
        scope = row.get("c")
        if scoped and (isinstance(scope, bool) or not isinstance(scope, int)):
            return None
        key = (scoped, scope, path.split(".", 1)[0])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)

    groups: list[dict[str, Any]] = []
    for key in order:
        rows = grouped[key]
        prefix = _shared_dotted_prefix([row["p"] for row in rows])
        group: dict[str, Any] = {
            "p_prefix": prefix,
            "rows": [[row["id"], row["p"][len(prefix):], row["v"]] for row in rows],
        }
        if key[0]:
            group["c"] = key[1]
        groups.append(group)
    return groups


def _expand_catalog_groups(groups: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        prefix = group["p_prefix"]
        for evidence_id, suffix, value in group["rows"]:
            item: dict[str, Any] = {"id": evidence_id, "p": prefix + suffix, "v": value}
            if "c" in group:
                item["c"] = group["c"]
            rows.append(item)
    rows.sort(key=lambda item: item["id"])
    return rows


def expand_evidence_from_wire(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Inverse of ``project_evidence_for_wire``; a canonical payload passes through."""

    restored = dict(evidence)
    catalog = restored.get("evidence_catalog")
    if isinstance(catalog, Mapping) and catalog.get("wire_format") == COMPACT_V1_WIRE_FORMAT:
        restored_catalog = {key: value for key, value in catalog.items() if key != "wire_format"}
        restored_catalog["items"] = _expand_catalog_groups(catalog.get("items") or [])
        restored["evidence_catalog"] = restored_catalog
    return restored


def project_evidence_for_wire(evidence: Any, projection: Any) -> Any:
    """Encode ``evidence`` for the provider wire.

    ``canonical`` (and any unknown value) returns the very same object, so the
    Phase-1 wire is reproduced byte for byte.  ``compact_v1`` returns a new payload
    whose only difference is the encoding of ``evidence_catalog.items`` plus the
    self-describing ``wire_format`` marker -- or the input itself whenever the rows
    are not canonical or the exact round trip does not hold.
    """

    if normalize_wire_projection(projection) != WIRE_PROJECTION_COMPACT_V1:
        return evidence
    if not isinstance(evidence, Mapping):
        return evidence
    catalog = evidence.get("evidence_catalog")
    if not isinstance(catalog, Mapping) or "wire_format" in catalog:
        return evidence
    groups = _compact_catalog_rows(catalog.get("items"))
    if groups is None:
        return evidence
    projected_catalog = dict(catalog)
    projected_catalog["items"] = groups
    projected_catalog["wire_format"] = COMPACT_V1_WIRE_FORMAT
    projected = dict(evidence)
    projected["evidence_catalog"] = projected_catalog
    try:
        lossless = canonical_wire_json(expand_evidence_from_wire(projected)) == canonical_wire_json(evidence)
    except (TypeError, ValueError):
        return evidence
    return projected if lossless else evidence
