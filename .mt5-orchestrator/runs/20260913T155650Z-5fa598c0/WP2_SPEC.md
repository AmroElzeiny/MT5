# WP2 SPEC -- opencode_token_attribution tool (stage A, measurement only)

RuntimeMode: Offline. Read-only with respect to the live bus and all product files EXCEPT the
single new file `python/tools/opencode_token_attribution.py`.

## Hard safety (SAF-001)
- Do NOT read `python/.env*` or any credential.
- Do NOT write to the live bus `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`.
  Only open bus files for read. Never create/modify bus files.
- Do NOT edit any product file other than creating `python/tools/opencode_token_attribution.py`.
  In particular do not touch `ai_gate.py`, `ai_provider.py`, `decision_evidence.py`,
  `evidence_catalog.py`, `decision_pipeline.py`, `structured_models.py`, any schema, any test.
- No network calls at all.
- Never start/stop/signal `ai_gate.py` or MT5.

## Deliverables
1. `python/tools/opencode_token_attribution.py` (new, stdlib only; may import repo modules).
2. Raw outputs under `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/wp2/`:
   - `sample_manifest.json` (>=30 request ids + stratum: candidates 1/2/3+, symbol class, role)
   - `input_attribution.json` + `input_attribution.csv` (per-subtree tokens: avg, p50, p95, % of input,
     static/dynamic, duplicated yes/no)
   - `output_attribution.json` + `output_attribution.csv`
   - `duplication.json` (DUP-001 classes + tokens per class)
   - `precision.json` (PREC-001)
   - `cache_layout.json` (WIRE-001 static prefix tokens, first volatile byte offset, sort_keys interleave)
   - `calibration.json` (proxy vs provider counts, per-request factors)
   - `consumer_table.json` (OUT-001 field -> consumer file:function, authority class)
   - `tool_stdout.txt` (the exact command run + summary)

## Data sources
- Bus root: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`
- `logs/openai_usage.ndjson` (1 row per successful provider response; fields include
  `request_id`, `operation`, `model`, `input_tokens`, `cached_input_tokens`, `output_tokens`,
  `reasoning_output_tokens`, `raw_usage`, `extra.role`, `extra.symbol`).
- `logs/ai_gate.log` (filter only; never load whole file into memory).
- `response_debug/*.json` (full authoritative response envelope; keys include `analyst_output`,
  `critic_output`, `adjudicator_output`, `candidate_assessments`, `provider_usage`).
- `completed/python_*.json` and `rejected/python_*.json` = archived frozen MQL request payloads.
  Filename form: `python_<...>__<request_id>.json`; encoding may be UTF-16LE (BOM) or UTF-8.
  Use the repo helper `ai_gate.read_json_any_encoding(path)` to load.
- Muse model id: `muse-spark-1.3-contributor`.

## VALIDATED capture recipe (already proven by the supervisor -- reuse it)
Driving the REAL code path offline with an injected recording provider captures the exact wire kwargs.

```python
import ai_gate
from ai_provider import OpenCodeResponsesProvider
from provider_deadline import RequestDeadline

class _Stop(Exception): pass

class Recorder:
    def __init__(self): self.calls=[]
    def factory(self, **kwargs):
        outer=self
        class _Resp:
            def create(self, **kw):
                outer.calls.append(dict(kw)); raise _Stop("captured")
        class _Client:
            responses=_Resp()
            @staticmethod
            def with_options(**o): return _Client
        return _Client

leg = OpenCodeResponsesProvider(
    base_url="https://opencode.ai/zen/go/v1", api_key="x", model="muse-spark-1.3-contributor",
    reasoning_effort="high", timeout_sec=60.0, max_output_tokens=25000,
    circuit_failure_threshold=10_000, circuit_cooldown_sec=1.0,
    log=lambda _m: None, client_factory=Recorder().factory)

# Neutralize side effects (in-process only; no product edit):
ai_gate.LOG_FILE = None                     # log() writes nowhere
ai_gate._provider = lambda: leg             # _score_setup_ai/_score_setup_impl use this
ai_gate._write_ai_cost_report = lambda *a, **k: None
RequestDeadline.can_start_attempt = lambda self, now=None: True      # archived deadlines are in the past
RequestDeadline.provider_timeout_sec = lambda self, configured, now=None: float(configured)

ai_gate._score_setup_impl(payload)          # raises/handles _Stop after one captured call
kw = recorder.calls[0]
# kw keys: model, instructions, input, text, reasoning, max_output_tokens, truncation, extra_headers
```
Notes:
- `kw["input"]` is a list; `kw["input"][0]["content"][0]["text"]` is the evidence JSON string.
- `kw["text"]["format"]["schema"]` is the strict response schema; `kw["text"]["format"]["name"]` is the schema name.
- `kw["extra_headers"]` carries `x-opencode-session` (Phase-1 stable prefix id) and deadline headers.
- Prefer calling `ai_gate._score_setup_ai(payload, provider_override=leg)` directly if
  `_score_setup_impl` early-returns before the provider call for some payloads; fall back to
  `build_decision_evidence_envelope` + `build_evidence_catalog` + `ai_gate._compact_model_evidence_payload`
  and report the approximation.
- Some archived payloads trip hard pre-gates and never reach the provider. Skip them and record why.
- Run each payload in a fresh subprocess (or reset globals) so provider circuits/registries do not leak.

## Critic / adjudicator capture
- `decision_pipeline.run_qualitative_consensus(provider=..., evidence=<compact envelope>,
  analyst_assessment=<dict>, request_metadata=<dict>, evidence_catalog=<EvidenceCatalog>)`.
- Rebuild `evidence` via `build_decision_evidence_envelope(payload)` -> `.envelope`, then
  `build_evidence_catalog(envelope)`, then `ai_gate._compact_model_evidence_payload(envelope, catalog)`.
  Build a synthetic `analyst_assessment` from the archived `response_debug` `candidate_assessments[i]`
  (candidate_index, decision_state, confidence_band, missing_required_evidence) plus candidate_id/hash
  from `envelope["entry_and_invalidation"]["candidates"][i]`.
- Use a capture provider whose `generate_structured(**kwargs)` records kwargs and returns a canned
  result object with `.parsed` (a valid `ModelCriticDecision` / `ModelAdjudicatorDecision`),
  `.provider_id`, `.actual_model`. For the critic return verdict PASS (consensus returns without
  adjudicator). To capture the adjudicator, return a critic verdict BLOCK with one valid
  `ModelCriticObjection` (code from `structured_models.QUALITATIVE_VETO_CODES`, evidence_ref_ids from
  `kwargs["evidence"]["allowed_evidence_ref_ids"]`), then return an ABSTAIN adjudicator result.
  Record which of critic/adjudicator was reached.

## Token proxy (tiktoken is NOT installed anywhere; do not attempt network install)
Implement a documented deterministic proxy, `raw_units(text)`:
- Split with `re.compile(r"'s|'t|'re|'ve|'m|'ll|'d|[A-Za-z]+|[0-9]+|[^\sA-Za-z0-9]+|\s+")`.
- Skip whitespace-only pieces. For alpha pieces add `ceil(len_utf8/4)`; for digit pieces add
  `ceil(len_utf8/3)`; for punctuation pieces add `ceil(len_utf8/1.5)`; minimum 1.
Then calibrate:
- Per request, `factor = provider_input_tokens / raw_units(whole_wire_text)`, where
  `whole_wire_text = instructions + schema_json + input_text`.
- Static prefix anchor: the analyst static prefix is exactly 6,513 provider tokens and the critic
  static prefix exactly 1,265 (observed `cached_input_tokens`). Compute `static_factor` from the
  static prefix only and compare; report both.
- Emit per-subtree `tokens = raw_units(subtree) * factor` (state the factor per request).
- Report proxy error: raw (uncalibrated) total vs provider input_tokens per request; calibrated
  error; and whether any exceeds the 15% escalation threshold. If `tiktoken` unexpectedly imports,
  use `o200k_base` and say so.

## Required measurement content
- IN-001: per-subtree tokens for at least: instructions per role; `text.format` schema per role;
  every top-level envelope section; `evidence_catalog.items` split into `id`/`p`/`v`/`c` and JSON
  punctuation; `allowed_evidence_ref_ids`; per-candidate rows and their largest subtrees
  (target candidates, authoritative numbers, family contract, bucket prior, shadow historical
  evidence, analogues); market/regime/po3/story blocks; timestamps; ids; labels; repeated key-name
  tokens; image parts if any. Include critic/adjudicator payloads.
- DUP-001: classify every catalog `v` and other repeated value as (a) same canonical fact
  serialized twice (prove by path identity, not value equality), (b) distinct facts equal value,
  (c) alias, (d) catalog material, (e) contract-required. Tokens per class.
- PREC-001: numeric fields whose serialized digits exceed source precision; example, source
  precision, proven-safe canonical form or "not provable"; token effect.
- WIRE-001 cache layout: static prefix tokens per role; first volatile byte position; whether
  `sort_keys=True` interleaves static and dynamic keys.
- OUT-001: reasoning vs visible tokens per role; visible tokens per output field (tokenize stored
  `analyst_output`/`critic_output`/`adjudicator_output`); for EVERY output schema field, consumer
  (file:function), whether it alters a trading/risk decision, persisted, telemetry-only, or never
  read; echoed inputs and unconsumed prose; reasoning-reserve vs used.

## Sample
- >=30 real archived requests stratified by candidate count 1 / 2 / 3+, symbol class
  (FX-major / FX-cross / metal / index / crypto), and role (analyst / critic / adjudicator).
- Prefer request ids that exist both in `openai_usage.ndjson` (for calibration) and as an archived
  payload. Include the known ids AUDJPY_32618 and USDCHF_19925 if present.
- Record in `sample_manifest.json` each row's request_id, archive path, stratum, and which roles
  were captured.

## Acceptance / evidence to return
- `python -m py_compile python/tools/opencode_token_attribution.py` exit 0.
- The one command used to run the tool, its exit code, wall time, and the list of output files with sizes.
- `sample_manifest.json` contents summary (>=30 rows).
- Calibration numbers (per-request factor min/median/max; calibrated error; 6,513/1,265 checks).
- Top-10 input subtrees by tokens (median) and top-10 output fields by visible tokens.
- Anything you could not measure, with the exact error.
