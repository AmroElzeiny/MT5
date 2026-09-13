# Supervisor report contract

Required:
- `SUPERVISOR_REPORT.json`
- `SUPERVISOR_REPORT.md`

Wrapper-owned:
- `SUPERVISOR_STDOUT_RAW.txt`
- `SUPERVISOR_STDOUT.txt`

The supervisor must never write wrapper-owned transcript files.

## Required report content

- run ID;
- front-end (`Claude` or `Codex`);
- tier and `RuntimeMode`;
- baseline commit/branch/working tree;
- actual models used, by role;
- requirement status with evidence;
- work-package ledger;
- changed files and rationale;
- tests and exit codes;
- test-integrity audit;
- complete final diff audit;
- independent reviewer findings and closure;
- budget/time summary;
- live/demo action audit;
- unresolved uncertainty;
- final verdict.

For high-risk work, explicitly audit state, retry/idempotency, serialization/contracts, backward compatibility, security/secrets and provider/runtime boundaries.

Verdicts:
- `COMPLETE_VERIFIED`
- `COMPLETE_WITH_EXPLICIT_UNVERIFIED_ITEM`
- `ESCALATE_TO_FRONTEND`
- `BLOCKED`

Never claim hidden risk is zero.
