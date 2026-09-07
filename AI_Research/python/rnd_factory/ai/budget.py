from __future__ import annotations
from dataclasses import dataclass

from ..config import FactoryConfig


def estimate_tokens_from_text(text: str) -> int:
    return max(1, (len(text) + 3) // 4)

@dataclass
class BudgetState:
    calls: int = 0
    estimated_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

class BudgetExceeded(RuntimeError): pass

class AIBudget:
    def __init__(self, config: FactoryConfig): self.config=config; self.state=BudgetState()
    def preflight(self, input_tokens: int, expected_output_tokens: int) -> float:
        if self.state.calls >= self.config.max_ai_calls:
            raise BudgetExceeded("maximum_ai_calls_reached")
        if input_tokens > self.config.max_context_tokens:
            raise BudgetExceeded("context_token_budget_exceeded")
        expected_output_tokens = min(expected_output_tokens, self.config.max_output_tokens)
        cost = (input_tokens*self.config.input_usd_per_million + expected_output_tokens*self.config.output_usd_per_million)/1_000_000.0
        if self.config.ai_mode == "remote" and self.state.estimated_cost_usd + cost > self.config.max_cost_usd:
            raise BudgetExceeded("estimated_cost_budget_exceeded")
        return cost
    def record(self, *, input_tokens:int, output_tokens:int, cached_input_tokens:int=0, actual_cost:float|None=None) -> None:
        billable=max(0,input_tokens-cached_input_tokens)
        cost=(billable*self.config.input_usd_per_million+cached_input_tokens*self.config.cached_input_usd_per_million+output_tokens*self.config.output_usd_per_million)/1_000_000.0
        self.state.calls += 1; self.state.input_tokens += input_tokens; self.state.output_tokens += output_tokens; self.state.estimated_cost_usd += actual_cost if actual_cost is not None else cost
