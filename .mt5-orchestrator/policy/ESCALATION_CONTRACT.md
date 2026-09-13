# Escalation contract

A supervisor escalation writes `ESCALATION.json` in the run directory.

Required fields:

```json
{
  "run_id": "...",
  "status": "ESCALATE_TO_FRONTEND",
  "reason": "...",
  "needs_frontend_implementation": true,
  "allowed_files": ["relative/path"],
  "evidence": ["..."],
  "decision_needed": "..."
}
```

Rules:
- keep `allowed_files` minimal;
- governance/orchestrator files cannot be unlocked;
- live real-money trading is never unlocked by escalation;
- if only a user/business decision is needed, set `needs_frontend_implementation` to false.
