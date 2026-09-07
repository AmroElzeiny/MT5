from __future__ import annotations

import json
from typing import Any


def strict_json_object(text: str) -> dict[str, Any]:
    duplicates: list[str] = []

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                duplicates.append(k)
            d[k] = v
        return d

    stripped = text.strip()
    if not stripped:
        raise ValueError("empty_response")
    decoder = json.JSONDecoder(
        object_pairs_hook=pairs_hook,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non_finite_json:{value}")),
    )
    value, end = decoder.raw_decode(stripped)
    if stripped[end:].strip():
        raise ValueError("trailing_json_content")
    if duplicates:
        raise ValueError("duplicate_json_keys:" + ",".join(sorted(set(duplicates))))
    if not isinstance(value, dict):
        raise ValueError("response_root_not_object")
    return value
