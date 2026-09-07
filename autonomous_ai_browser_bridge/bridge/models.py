from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ChatProfile:
    alias: str
    model_id: str
    conversation_url: str
    desired_model: str
    desired_effort: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
