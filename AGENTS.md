# BEGIN DUAL-OCG-ORCHESTRATION

## Codex front-end orchestration

When Codex is the active front-end, Codex is the architect and final judge. OpenCode Go is the default implementation/test/review workforce.

Codex must read `CLAUDE.md` as product context even though `AGENTS.md` is its native project instruction file.


### Personal low-cost dual orchestration

This block controls the personal engineering workflow only. Existing repository product rules remain authoritative for product behavior.

#### Default ownership

1. The active front-end is the architect, scope owner, risk classifier and final judge.
2. OpenCode Go is the default implementation/test/review workforce.
3. The front-end MUST NOT implement product-file changes before delegation unless Direct mode or a valid file-scoped takeover is active.
4. The front-end MAY read/search, inspect diffs/history, run read-only diagnostics, create `.mt5-orchestrator/current/MISSION.md`, `.mt5-orchestrator/current/VISUAL_CONTRACT.md`, `.mt5-orchestrator/current/MIGRATION_CONTRACT.md`, and review final evidence.
5. Do not silently fall back to front-end implementation when OpenCode is unavailable.
6. Do not use native Claude/Codex subagents as a second hidden implementation workforce unless the user explicitly requests it.

#### Required workflow

Before delegation:
1. Read repository instructions and relevant tests/contracts.
2. Refresh live OpenCode Go models if snapshot is older than 24h.
3. Read:
   - `.mt5-orchestrator/policy/ROUTING_POLICY.md`
   - `.mt5-orchestrator/policy/BUDGET_POLICY.md`
   - `.mt5-orchestrator/models/ROLE_MODEL_MAP.md`
   - `.mt5-orchestrator/models/LIVE_MODELS.md` when present
4. Create `.mt5-orchestrator/current/MISSION.md`.
5. Use 1-6 outcome-based work packages. Avoid unnecessary microtasks.
6. Give every user requirement a stable requirement ID.
7. Use the smallest context sufficient for each worker.

Delegate as the active front-end:
- Claude: `powershell -ExecutionPolicy Bypass -File .\tools\mt5-orchestrator\delegate.ps1 -MissionFile .\.mt5-orchestrator\current\MISSION.md -FrontEnd Claude -Tier Standard -RuntimeMode Offline`
- Codex: `powershell -ExecutionPolicy Bypass -File .\tools\mt5-orchestrator\delegate.ps1 -MissionFile .\.mt5-orchestrator\current\MISSION.md -FrontEnd Codex -Tier Standard -RuntimeMode Offline`

Use Deep only for material architecture/state/security/execution risk or repeated meaningful failure.

#### Model routing

Normal defaults:
- Standard supervisor: `opencode-go/minimax-m3`
- Deep supervisor: `opencode-go/deepseek-v4.1-flash`
- Explorer: `opencode-go/deepseek-v4.1-flash`
- Implementation: `opencode-go/qwen3.8-flash`
- Test/debug: `opencode-go/qwen3.8-flash`
- Logic review: `opencode-go/minimax-m3`
- Adversarial review: `opencode-go/deepseek-v4.1-flash`
- Vision: only when actual image/screenshot verification is required.

Do not use expensive models merely because a task is large, important, or cross-file.
Do not use the vision model for non-visual tasks.
Do not run a full repository explorer pass unless ownership is materially unclear.

#### Repair and budget discipline

- Maximum two meaningful attempts with the same model/approach.
- Standard target: <= 90 minutes and <= 8 material model invocations.
- Deep target: <= 180 minutes and <= 12 material model invocations.
- If the budget is likely to be exceeded, escalate instead of looping.
- Never repeatedly attach full transcripts or rediscover already-established repository state.

#### Direct mode

If the user wants the active front-end to code directly, Direct mode must be started by the user with `tools/mt5-orchestrator/start-direct.ps1`.
A prompt alone does not bypass hooks.

Direct mode unlocks product Edit/Write/apply_patch only. It does not unlock protected governance files, shell file mutation, destructive git, publishing, or unsafe live-trading actions.

#### Evidence

A delegated run is not complete without:
- `.mt5-orchestrator/runs/<run>/SUPERVISOR_REPORT.json`
- `.mt5-orchestrator/runs/<run>/SUPERVISOR_REPORT.md`
- final diff audit;
- requirement coverage;
- test-integrity audit;
- actual model IDs;
- independent review;
- explicit unresolved uncertainty.

Claude/Codex must independently inspect the highest-risk evidence and final diff rather than replaying the full worker transcript.

#### Escalation

If the supervisor returns `ESCALATE_TO_FRONTEND`, a file-scoped `ESCALATION.json` is required.
The active front-end may then use `grant-takeover.ps1 -Actor Claude` or `-Actor Codex`, edit only authorized files, revoke the takeover, and re-delegate verification.


### MT5-specific authority

Existing `CLAUDE.md` and `python/CLAUDE.md` remain the product-behavior authority.

Before changing an MT5 workflow:
- trace Python + MQL5/MQH + file-bus contracts end to end;
- identify the earliest incorrect authoritative state;
- keep schema/version/identity/risk/execution contracts synchronized;
- do not force trades or weaken fail-closed controls;
- default delegated runtime mode is Offline;
- Demo-forward mutation requires `Demo authorization: YES` in the mission and `-RuntimeMode Demo`.

The front-end must distinguish repository source from deployed terminal runtime and must not claim deployment/live verification without evidence.


# END DUAL-OCG-ORCHESTRATION

