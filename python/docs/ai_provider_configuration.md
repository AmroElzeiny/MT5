# Provider-neutral AI configuration

Version Z selects exactly one AI transport at Python startup. Provider choice
changes transport, privacy, latency, and model identity; it never changes the
decision, candidate-integrity, repeatability, target, risk, or MQL execution
contracts.

## Required switch

Set exactly one explicit value in the private `.env`:

```env
AI_USE_REMOTE_API=true
```

or:

```env
AI_USE_REMOTE_API=false
```

Missing, malformed, or ambiguous values create an `UnavailableProvider`.
`LIVE_FORWARD` requests then return non-trading `ABSTAIN/NO_TRADE`; no rule-only
approval is possible.

The bridge reads the switch before loading provider secrets. In local mode it
excludes and removes `OPENAI_API_KEY` and `OPENAI_BASE_URL` from the process. In
remote mode it excludes local API keys and local model paths unless the explicit
research-only local shadow comparison is enabled.

## Remote mode

```env
AI_USE_REMOTE_API=true
OPENAI_API_KEY=
OPENAI_BASE_URL=
AI_GATE_MODEL=gpt-5.4-mini
AI_GATE_FALLBACK_MODELS=gpt-5.4-nano
AI_GATE_REASONING_EFFORT=high
AI_OPENAI_TIMEOUT_SEC=180
```

An empty `OPENAI_BASE_URL` uses the official SDK endpoint. Remote fallback
models remain inside `RemoteAPIProvider`. No local endpoint is contacted after
a remote failure.

Migration aliases remain accepted for existing private configurations:

- `OPENAI_MODEL` -> `AI_GATE_MODEL`
- `OPENAI_FALLBACK_MODELS` -> `AI_GATE_FALLBACK_MODELS`
- `AI_REASONING_EFFORT` -> `AI_GATE_REASONING_EFFORT`

The recommended names above take precedence. Existing model values are not
rewritten automatically.

Start the bridge from the Python directory:

```powershell
.\.venv\Scripts\python.exe ai_gate.py
```

## Local mode

The local runtime must expose an OpenAI-compatible HTTP API. Python does not
load GGUF weights itself and does not automate LM Studio, Bionic, or any GUI.

```env
AI_USE_REMOTE_API=false
LOCAL_AI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_AI_API_KEY=local
LOCAL_AI_MODEL=qwen3.5-9b
LOCAL_AI_ANALYST_MODEL=qwen3.5-9b
LOCAL_AI_CRITIC_MODEL=qwen3.5-9b
LOCAL_AI_ADJUDICATOR_MODEL=qwen3.5-9b
LOCAL_AI_FALLBACK_MODELS=
LOCAL_AI_MODEL_PATH=
LOCAL_AI_HEALTHCHECK_PATH=/models
LOCAL_AI_TIMEOUT_SEC=180
LOCAL_AI_MAX_RETRIES=1
LOCAL_AI_MAX_OUTPUT_TOKENS=4096
LOCAL_AI_TEMPERATURE=0.15
LOCAL_AI_TOP_P=0.85
LOCAL_AI_SEED=42
LOCAL_AI_ENABLE_THINKING=true
LOCAL_AI_REQUIRE_JSON_SCHEMA=true
LOCAL_AI_PARALLELISM=1
LOCAL_AI_CONTEXT_BUDGET_TOKENS=7000
LOCAL_AI_RETRIEVAL_TOP_K=7
LOCAL_AI_ALLOW_NON_LOOPBACK_ACK=false
```

`LOCAL_AI_MODEL_PATH` is optional audit metadata. The value is never logged and
does not cause Python to load a second model copy.

### Local server example

Load Qwen3.5-9B GGUF in the external runtime and expose port `1234`. For a
llama.cpp-compatible server, an illustrative command is:

```powershell
llama-server.exe -m C:\models\qwen3.5-9b.gguf --host 127.0.0.1 --port 1234 -c 8192 --parallel 1
```

LM Studio or a Bionic-managed runtime is compatible only when it exposes the
same OpenAI-compatible routes. Configure `LOCAL_AI_MODEL` to the exact model ID
returned by:

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

Then start the Python bridge normally:

```powershell
.\.venv\Scripts\python.exe ai_gate.py
```

At startup, local mode validates the URL, requires loopback by default, queries
the configured model endpoint, verifies the model ID, and performs a minimal
strict structured-output probe. Live authority remains disabled when any probe
fails.

Use a non-loopback endpoint only after explicitly acknowledging the privacy and
network boundary:

```env
LOCAL_AI_ALLOW_NON_LOOPBACK_ACK=true
```

## Decision architecture

Every selected provider receives the same versioned pipeline:

```text
MQL deterministic candidate
  -> request/candidate/fingerprint validation
  -> canonical evidence envelope and lineage checks
  -> deterministic setup taxonomy and one family profile
  -> clean historical analogue retrieval
  -> independent Analyst
  -> independent Critic
  -> conditional Adjudicator
  -> deterministic Python consensus and repeatability authority
  -> MQL candidate, fingerprint, risk, session, broker, and execution authority
```

The Critic does not receive the Analyst verdict. Adjudication runs only for a
dispute, abstention, low confidence band, missing qualitative evidence, or a
near-boundary case. It cannot override deterministic blockers, target
feasibility, risk, broker constraints, or repeatability.

The LLM does not own prices, SL/TP construction, position size, calibrated
probability, empirical expected R, portfolio risk, or broker execution.

## Historical memory

`AI_TRADE_MEMORY_FILE` points to a versioned SQLite store. Pending decisions
record immutable pre-entry evidence and role outputs. A completed result enters
retrieval only after the completed ledger marks exact execution identity,
candidate binding, execution-fingerprint binding, learning eligibility, and
ledger integrity as `CLEAN`.

Retrieval excludes the current request, same lineage, same candidate, and every
quarantined or unreconciled outcome. It prefers exact taxonomy, then family and
branch, direction, asset class, session/killzone, regime, target model, and
normalized numeric similarity. No match is reported as
`INSUFFICIENT_SAMPLE`; no prior is fabricated.

## Retry and circuit breaker

- Local transport: at most `LOCAL_AI_MAX_RETRIES=1` per model.
- Remote transport: one bounded schema/transport retry plus existing bounded
  Flex handling when Flex is explicitly active.
- Local fallback model IDs stay local; remote fallback model IDs stay remote.
- Repeated failures open the selected provider circuit using
  `AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD` and
  `AI_PROVIDER_CIRCUIT_COOLDOWN_SEC`.
- While open, requests fail closed without a cross-provider attempt.

Oversized local evidence is rejected before constructing the provider client.
Local calls are serialized with `LOCAL_AI_PARALLELISM=1`.

## Cache and repeatability isolation

Decision-cache and repeatability identities include:

- provider mode and provider ID;
- endpoint identity hash;
- configured model set;
- actual/model fingerprint where available;
- prompt and decision contracts;
- family profile and retrieval policy;
- retrieval result hash;
- generation settings that affect behavior;
- candidate and execution identities.

A remote approval cannot become a local approval, or vice versa. Old cache rows
without the provider-neutral strict contract are non-trading. Tester workflow
flags remain outside the economic decision signature, so a response recorded in
`RECORD_ONLY` can replay in `CACHE_ONLY` only when all decision-relevant and
provider contracts still match.

## Optional shadow comparison

```env
AI_SHADOW_COMPARE_PROVIDERS=false
```

When enabled, comparison is non-authoritative and is skipped in
`LIVE_FORWARD`. A remote-selected research run may compare a configured local
server. A local-selected run never reads a remote secret or invokes a remote
shadow provider. Shadow rows have `trading_authority=false` and
`outcome_attribution=false`; they cannot enter the trading decision cache or
duplicate trade outcomes.

Provider promotion remains an offline, versioned, out-of-sample decision. This
implementation makes no claim that local Qwen is better than the configured
remote model.

## Safe observability

Startup and decision records include provider mode/ID, endpoint class, model
identity, contract versions, generation-settings hash, input and role response
fingerprints, retrieved analogue IDs, resolver reason, latency, retries, health,
and reported token usage. API keys, authorization headers, full prompts, account
credentials, and local model paths are never logged.

Remote cost rows remain available. Local rows record zero remote cost and only
token/performance fields actually reported by the server.

## Verification

```powershell
.\.venv\Scripts\python.exe -m compileall .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe tools\verify_v_next_controls.py --skip-mql-compile
.\.venv\Scripts\python.exe tests\run_golden_validators.py
```

The final deployment check must also compile the active EA in MetaEditor. Unit
tests mock provider transports and never require paid API access.
