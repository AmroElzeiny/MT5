# Model metadata rules

The local `opencode models opencode-go` output is the authority for availability.

Never invent:
- a model ID;
- an effort/reasoning variant;
- a context limit;
- an image capability.

`run-model.ps1` intentionally allows only the normal cost-controlled model set.
The orchestration system does not automatically route to expensive models.

If a preferred model disappears:
1. use another model from the normal set with the required capability;
2. preserve reviewer-family independence when practical;
3. record the substitution;
4. escalate if the normal set is insufficient.
