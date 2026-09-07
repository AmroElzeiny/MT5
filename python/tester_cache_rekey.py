"""Re-key a tester replay cohort after a decision-identity fingerprint correction.

Why this exists
---------------
``_TesterAiCacheSignature`` is

    AI_DECISION_SCHEMA_VERSION | AI_TARGET_ARBITRATION_SCHEMA_VERSION
    | AI_PROMPT_CONTRACT_VERSION | DecisionHash() | _GroupSignature(plans) | ...

so component 4, ``DecisionInputHash()``, is shared by every artifact recorded in
one cohort.  It mixes the engine inputs with a content hash of the normalized-FVG
asset-class policy file, and that content hash was produced by a reader that
opened the file as UTF-16 text and concatenated ``FileReadString`` results.  Both
policy files are odd-length ASCII JSON, and under scan-time file I/O load that
read did not return the same string twice: the 2026.09.06 replay logged the same
unchanged 1,067-byte file hashing to 49736647, 1990245157, 1989935653 and
1984261413 within one run.

Correcting the reader to hash raw bytes changes ``DecisionInputHash()`` even
though **no engine input and no policy file changed**.  Every recorded decision
is still exactly the decision those inputs produced; only our fingerprint of them
was wrong.  Re-recording the cohort would mean paying for provider calls to
reproduce answers we already hold, so this module rewrites the key instead.

What it deliberately does not do
--------------------------------
* It never changes a decision, an assessment, a candidate hash, a request id, or
  any binding hash.  Only component 4 of ``cache_signature`` and the file name
  derived from it.
* It never touches an artifact recorded under a different cohort.  Those were
  keyed off a corrupted read and their provenance is not established here.
* It never overwrites an existing artifact whose signature differs.
* It is a no-op unless ``apply=True``; the default is a plan you can inspect.

WHY THIS MODULE IS DISARMED (2026-09-06)
----------------------------------------
The premise above is **wrong**, and the refutation is in the engine's own source.
``TradeEngine.mqh:2760``, inside ``_CandidateHash``::

    canonical += m_ai.DecisionHash() + "|" + ENGINE_INPUT_SCHEMA + "|" + AI_DECISION_SCHEMA_VERSION;

and again at ``:2784`` inside ``_ExecutionFingerprint``.  ``DecisionInputHash()``
is therefore not merely component 4 of the signature -- it is mixed into **every
candidate hash and every execution fingerprint**, and those hashes are themselves
embedded in the per-candidate section of the same signature.  Correcting the
decision identity moves all of them at once.

Measured, not argued.  The cohort was re-keyed 671051197 -> 82474443 (1,346
artifacts, 0 conflicts, every original backed up) and the 5-day replay that
followed reported::

    [tester_ai_cache_cohort] decision_input_hash=82474443
        recorded_cohorts=82474443x182,1098270737x18 dominant=82474443
        match=true verdict=replayable
    [final_summary] ai_cache_hits_total=0 ai_cache_misses_total=1365
        ai_cache_miss_no_artifact_total=1365

The cohort was addressable and every single lookup still missed.  Diffing a
signature the new build computed against the closest stored one showed the whole
plan section identical and the divergence starting exactly at the candidate hash::

    [ 0] new=EFD9EAF5BB681E01   stored=47F47916193BD592   <-- DIFFERS
    [ 1..14] setup_family, setup_class, taxonomy, entry_branch, target,
             obstacle_kind, tp2, tp1, entry, ...            identical
    [15] new=F063E2A364128017   stored=D1A83930CE2CD8C4   <-- DIFFERS

Making a re-key work would mean recomputing every candidate hash, execution
fingerprint and response binding hash in Python.  That is not renaming a key; it
is rewriting the identity a provider response was validated against, which
``python/CLAUDE.md`` forbids outright ("no hash recomputation from a different
representation", "never silently reinterpret incompatible authoritative
schemas").  A subtle error there produces bindings that look self-consistent and
are wrong, to save provider spend on a test cohort.

So a cohort recorded under a superseded decision identity is **not re-keyable**.
The supported way to obtain a replayable cohort after an identity correction is
the workflow ``python/CLAUDE.md`` already prescribes: RECORD_ONLY -> process the
cohort -> export -> CACHE_ONLY.

``plan_rekey`` now refuses.  The code is kept because the port of
``_TesterAiCacheKey`` below is verified correct (it reproduces the file name of
every one of the 74 artifacts this module never touched), and because it becomes
usable the day ``DecisionHash()`` leaves ``_CandidateHash``.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

TESTER_CACHE_REKEY_VERSION = "20260906_decision_identity_rekey_v1"

# Component index of DecisionInputHash() inside a cache signature.
DECISION_HASH_COMPONENT_INDEX = 3

REKEY_DISARMED_REASON = (
    "tester_cache_rekey_is_unsound: DecisionInputHash() is mixed into "
    "_CandidateHash (TradeEngine.mqh:2760) and _ExecutionFingerprint "
    "(TradeEngine.mqh:2784), so it also moves every candidate hash embedded in "
    "the signature. Re-keying component 4 alone was measured to yield "
    "cohort match=true with ai_cache_hits_total=0 across 1365 lookups. "
    "Re-record the cohort (RECORD_ONLY -> export -> CACHE_ONLY) instead."
)


class RekeyUnsound(RuntimeError):
    """Raised instead of producing a cohort that is addressable but never hit."""


def tester_cache_key(signature: str) -> str:
    """Port of ``CTradeEngine::_TesterAiCacheKey``.

    Note this is the raw signed reinterpretation of the 32-bit FNV-1a value, not
    the ``% 2147483647`` form used by the input hashes themselves.
    """

    h = 2166136261
    for ch in signature:
        h = (h ^ ord(ch)) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    signed = h - 0x100000000 if h >= 0x80000000 else h
    return str(signed).replace("-", "n")


def decision_hash_component(signature: str) -> str:
    parts = signature.split("|")
    if len(parts) <= DECISION_HASH_COMPONENT_INDEX:
        return ""
    return parts[DECISION_HASH_COMPONENT_INDEX]


def rekey_signature(signature: str, new_decision_hash: str) -> str:
    parts = signature.split("|")
    if len(parts) <= DECISION_HASH_COMPONENT_INDEX:
        raise ValueError("signature_has_no_decision_hash_component")
    if not new_decision_hash or "|" in new_decision_hash:
        raise ValueError("invalid_decision_hash")
    parts[DECISION_HASH_COMPONENT_INDEX] = new_decision_hash
    return "|".join(parts)


def _read_json(path: Path) -> dict:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    raise ValueError(f"unreadable_artifact:{path}")


@dataclass
class RekeyEntry:
    source: Path
    target: Path
    old_signature: str
    new_signature: str


@dataclass
class RekeyPlan:
    old_hash: str
    new_hash: str
    entries: list[RekeyEntry] = field(default_factory=list)
    skipped_other_cohort: int = 0
    skipped_unreadable: int = 0
    skipped_no_signature: int = 0
    conflicts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"version={TESTER_CACHE_REKEY_VERSION}"
            f" old_hash={self.old_hash} new_hash={self.new_hash}"
            f" rekey={len(self.entries)}"
            f" skipped_other_cohort={self.skipped_other_cohort}"
            f" skipped_unreadable={self.skipped_unreadable}"
            f" skipped_no_signature={self.skipped_no_signature}"
            f" conflicts={len(self.conflicts)}"
        )


def plan_rekey(
    cache_dir: Path,
    old_hash: str,
    new_hash: str,
    _candidate_hash_is_identity_free: bool = False,
) -> RekeyPlan:
    """Work out every rename without touching anything.

    Refuses unless the caller can assert that ``DecisionInputHash()`` no longer
    participates in ``_CandidateHash`` -- see REKEY_DISARMED_REASON.  The flag is
    private on purpose: it exists so the accompanying test can still exercise the
    planning logic, not so a caller can wave the refutation away.
    """

    if not _candidate_hash_is_identity_free:
        raise RekeyUnsound(REKEY_DISARMED_REASON)
    if not old_hash or not new_hash:
        raise ValueError("old_hash and new_hash are required")
    if old_hash == new_hash:
        raise ValueError("old_hash equals new_hash; nothing to re-key")

    plan = RekeyPlan(old_hash=old_hash, new_hash=new_hash)
    planned_targets: dict[Path, Path] = {}

    for path in sorted(cache_dir.glob("*.json")):
        try:
            obj = _read_json(path)
        except ValueError:
            plan.skipped_unreadable += 1
            continue
        signature = str(obj.get("cache_signature") or "")
        if not signature:
            plan.skipped_no_signature += 1
            continue
        if decision_hash_component(signature) != old_hash:
            plan.skipped_other_cohort += 1
            continue

        new_signature = rekey_signature(signature, new_hash)
        target = cache_dir / f"{tester_cache_key(new_signature)}.json"

        if target in planned_targets:
            plan.conflicts.append(
                f"two sources map to {target.name}: {planned_targets[target].name} and {path.name}"
            )
            continue
        if target.exists() and target != path:
            try:
                existing = _read_json(target)
            except ValueError:
                plan.conflicts.append(f"{target.name} exists and is unreadable")
                continue
            if str(existing.get("cache_signature") or "") != new_signature:
                plan.conflicts.append(
                    f"{target.name} already exists with a different signature"
                )
                continue

        planned_targets[target] = path
        plan.entries.append(
            RekeyEntry(
                source=path,
                target=target,
                old_signature=signature,
                new_signature=new_signature,
            )
        )

    return plan


@dataclass
class RekeyResult:
    written: int = 0
    backed_up: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


def apply_rekey(plan: RekeyPlan, backup_dir: Path) -> RekeyResult:
    """Write the re-keyed artifacts, keeping a copy of every original.

    Refuses outright if the plan reported a conflict: a partially applied re-key
    would leave the cohort split across two keys, which is the failure this whole
    module exists to end.
    """

    result = RekeyResult()
    if plan.conflicts:
        result.errors.append(f"refusing to apply: {len(plan.conflicts)} conflict(s)")
        return result

    backup_dir.mkdir(parents=True, exist_ok=True)
    for entry in plan.entries:
        try:
            shutil.copy2(entry.source, backup_dir / entry.source.name)
            result.backed_up += 1
            obj = _read_json(entry.source)
            obj["cache_signature"] = entry.new_signature
            entry.target.write_text(
                json.dumps(obj, ensure_ascii=False), encoding="utf-8"
            )
            result.written += 1
            if entry.source != entry.target:
                entry.source.unlink()
                result.removed += 1
        except Exception as exc:  # pragma: no cover - filesystem failure path
            result.errors.append(f"{entry.source.name}: {exc}")
    return result


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache_dir", type=Path)
    parser.add_argument("old_hash")
    parser.add_argument("new_hash")
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    try:
        plan = plan_rekey(args.cache_dir, args.old_hash, args.new_hash)
    except RekeyUnsound as exc:
        print(f"refused: {exc}")
        return 2
    print(plan.summary())
    for conflict in plan.conflicts[:10]:
        print(f"  conflict: {conflict}")
    if not args.apply:
        print("dry run; pass --apply to write")
        return 1 if plan.conflicts else 0

    backup = args.backup_dir or (args.cache_dir.parent / "tester_ai_cache_rekey_backup")
    result = apply_rekey(plan, backup)
    print(
        f"applied written={result.written} backed_up={result.backed_up}"
        f" removed={result.removed} errors={len(result.errors)} backup={backup}"
    )
    for err in result.errors[:10]:
        print(f"  error: {err}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
