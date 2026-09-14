# OpenCode Go usage-limit discrepancy — evidence pack

Prepared 2026-09-13 (UTC) for an OpenCode support ticket. Contains no credentials.
The workspace id is shortened here. The full id is in the 429 response bodies
quoted in section 6, and in the OpenCode console.

## 1. Summary

A single automated client used `muse-spark-1.3-contributor` on
`https://opencode.ai/zen/go/v1/responses`. It received `429 GoUsageLimitError`
"Weekly usage limit reached" on 2026-09-13 at about 12:28 UTC.

That model's usage between 2026-09-07 00:00 UTC and the moment of the 429 was
repriced at OpenCode's published Go rates, from the provider's own `usage` block on
every successful response. The total is **USD 11.15**. The published weekly limit
for this model is **USD 30.00** (50% of the USD 60 monthly limit). That is
**37% of the limit**, which leaves **USD 18.85 (63%) unexplained** by the usage the
API reported.

The client also received 429 `GoUsageLimitError` on Muse from 2026-09-11 16:50 UTC.
Our logs cut off the `limitName` of those responses, so that window is shown
separately in section 5.

## 2. Published pricing used

Source: https://opencode.ai/docs/go/ (page "Last updated Sep 11, 2026"), retrieved 2026-09-13.

| Model | Endpoint | Input /1M | Output /1M | Cached read /1M | Cached write /1M | Monthly limit |
|---|---|---:|---:|---:|---:|---:|
| `muse-spark-1.3-contributor` | `/zen/go/v1/responses` | 0.10 | 0.20 | 0.002 | — | 60 |
| `deepseek-v4.1-flash` (off-peak) | docs list `/chat/completions`; client uses `/responses` | 0.15 | 0.60 | 0.003 | — | 15 |
| `deepseek-v4.1-flash` (peak) | as above | 0.30 | 1.20 | 0.006 | — | 15 |

Published limit windows: 5-hour = 20%, weekly = 50%, monthly = 100% of the monthly limit.

Accounting rules applied, in the order a reviewer would check them:

- **Input:** uncached input = `usage.input_tokens − input_tokens_details.cached_tokens`.
- **Cache writes:** no cache-write tokens were ever reported, and no cache-write price is published.
- **Output:** output = `usage.output_tokens`.
- **Reasoning:** `output_tokens_details.reasoning_tokens` is **included** in
  `output_tokens` on every one of 2,516 responses (reasoning ≤ output on all of them).
  It is therefore not added a second time. If Go meters reasoning *in addition* to
  output, the Muse week total becomes USD 14.35. That is still 48% of the limit.

## 3. Time window and client

- **Client:** one Python process (the "ai_gate") on one Windows host. It uses the
  OpenAI Responses wire format with `x-opencode-session`, `user-agent: opencode/1.0.0`,
  `store=false`, `truncation=disabled`, `reasoning.effort=high`, a strict
  `text.format` json_schema, and `max_output_tokens` = schema budget + 24,000.
- **First Muse call:** 2026-09-09 16:50 UTC.
- **Last successful Muse call:** 2026-09-13 12:31 UTC.
- **Weekly window:** assumed Monday 00:00 UTC. The 429 at about 12:28 said "Resets in
  11hr 32min", which points to 2026-09-14 00:00 UTC.

## 4. Logical calls vs. outbound requests (duplicate calls ruled out)

Counted from the client's own per-attempt log lines. Each HTTP attempt writes
`[provider_call_started]` immediately before the request is sent.

| Metric | Value |
|---|---:|
| Logical Muse calls (distinct request id + role) | 3,003 |
| Outbound Muse HTTP attempts | 3,012 |
| Attempts per logical call | 1.003 |
| Extra attempts | 9, all deliberate one-shot semantic correction passes (evidence-id scope), each a separate full request |
| SDK / transport retries on Muse | 0 (`max_retries=0` at client and per call; every attempt logged `transport_retry=0`) |
| Successful Muse responses (usage reported) | 2,472 (ledger rows: 2,474) |
| Muse attempts answered `429 GoUsageLimitError` | 475 (refused, assumed not billed) |
| Muse attempts that failed after running upstream, usage unknown | 60 (20 × HTTP 403 after a median 51 s, 23 × HTTP 500 after a median 94 s, 16 × connection error after a median 19 s, 1 other) |

Even if all 60 unknown attempts had been billed as full analyst calls (about USD
0.006 each), the week would rise by about USD 0.36. That does not close an USD
18.85 gap.

## 5. Provider-reported usage, repriced at the published rates

| Window (UTC) | Muse responses | Uncached input | Cached read | Output (incl. reasoning) | Reasoning | Expected Go usage | Applicable limit |
|---|---:|---:|---:|---:|---:|---:|---:|
| 09-07 00:00 → 09-13 12:27 (weekly 429) | 2,454 | 70,763,331 | 260,468 | 20,381,594 | 16,015,176 | **USD 11.153** | weekly USD 30 |
| 09-07 00:00 → 09-11 16:50 (first Muse 429, limit name not captured) | 1,795 | 47,348,463 | 174,157 | 15,277,719 | 12,399,790 | USD 7.791 | weekly USD 30 |
| 09-11 11:50 → 16:50 (5 h before that 429) | 141 | 3,841,469 | 15,172 | 1,245,332 | 1,011,243 | USD 0.633 | 5-hour USD 12 |
| 09-13 07:27 → 12:27 (5 h before weekly 429) | 658 | 23,369,930 | 86,311 | 5,083,686 | 3,598,833 | USD 3.354 | 5-hour USD 12 |

Daily totals (Muse, successful responses):

| Day | Responses | Input | Cached read | Output | Reasoning | Expected USD |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-09 | 371 | 9,473,853 | 77,659 | 3,031,343 | 2,448,479 | 1.546 |
| 2026-09-10 | 996 | 26,492,723 | 72,140 | 8,543,815 | 6,935,142 | 4.351 |
| 2026-09-11 | 429 | 11,600,982 | 24,358 | 3,722,750 | 3,032,722 | 1.902 |
| 2026-09-13 | 678 | 24,235,903 | 86,311 | 5,249,155 | 3,711,828 | 3.465 |

There was no Muse traffic from this client on 09-07, 09-08 or 09-12.

`deepseek-v4.1-flash` from this client, 09-13 only: 42 responses, 1,162,204 uncached
input, 128,512 cached read, 609,835 output. That repriced to USD 0.54 (off-peak) or
USD 1.08 (peak). This model also returned "Weekly usage limit reached" on 09-13
against a USD 7.50 weekly limit.

## 6. Representative requests

OpenCode response ids (analyst role; token counts as reported by the API):

| UTC | Response id | input | cached | output | reasoning |
|---|---|---:|---:|---:|---:|
| 2026-09-10 00:02:41 | `resp_6aa1f3a0e69f45cc59d54b14` | 19,041 | 0 | 8,683 | 7,646 |
| 2026-09-10 06:10:32 | `resp_6aa249d76bde8d95d6084388` | 37,857 | 0 | 10,967 | 7,580 |
| 2026-09-10 13:44:09 | `resp_6aa2b426ad59fc30120b48f5` | 38,674 | 0 | 14,592 | 11,512 |
| 2026-09-11 00:00:00 | `resp_6aa3447f4645faddc48c4170` | 45,091 | 0 | 14,698 | 11,158 |
| 2026-09-11 02:08:14 | `resp_6aa3628d9a3242ea6cc34f62` | 38,892 | 0 | 10,579 | 7,289 |
| 2026-09-11 14:44:51 | `resp_6aa413e2619c700ebd4f4fea` | 38,801 | 0 | 12,308 | 9,532 |
| 2026-09-13 10:30:37 | `resp_6aa67b4c46d79aced0cf4f5e` | 86,066 | 0 | 17,776 | 9,844 |
| 2026-09-13 11:26:41 | `resp_6aa688706eff38a7491c4bd3` | 101,843 | 0 | 18,385 | 10,431 |
| 2026-09-13 12:09:01 | `resp_6aa6925bd5fe3dd05ce74a18` | 89,238 | 0 | 18,180 | 9,649 |

The 429 body received (credential-free):

```
Error code: 429 - {'type': 'error', 'error': {'type': 'GoUsageLimitError',
'message': 'Weekly usage limit reached. Resets in 11hr 32min. To continue using this
model now, enable usage from your available balance: https://opencode.ai/workspace/wrk_01M2…ZPJ37/go'},
'metadata': {'workspace': 'wrk_01M2…ZPJ37', 'limitName': 'weekly'}}
```

## 7. Prompt-cache evidence

Every response exposes `input_tokens_details.cached_tokens`, and cached reads were
billed-category data. Over the week, Muse's token-weighted cache-read ratio was
**0.37%** (260,468 of 71,023,799 input tokens). The rare hits were exactly the
static prefix of our requests: 6,513 tokens on analyst calls (19 times) and 1,265 on
critic calls. The cache works, but it was almost never reused.

The client sent a new `x-opencode-session` for every request. Your docs ask for "a
stable session ID … so we can optimize routing and prompt caching". Since
2026-09-13 the client sends one stable session per model and response schema.
Caching can only make our reconstruction *cheaper*, so it cannot explain the
limit being reached early.

## 8. Questions for OpenCode

1. What dollar amount did the Go meter record for `muse-spark-1.3-contributor` in
   this workspace for the week starting 2026-09-07 00:00 UTC? At which request did it
   cross USD 30?
2. Is Muse metered at the published USD 0.10 / 0.20 / 0.002 rates, or at a different
   (for example undiscounted) rate?
3. Are reasoning tokens metered in addition to `output_tokens`?
4. Are failed requests metered? That covers HTTP 500 after about 90 s, HTTP 403 after
   about 50 s, and client-side disconnects.
5. Is the weekly window fixed (Monday 00:00 UTC) or rolling? What was the `limitName`
   of the 429s this workspace received from 2026-09-11 16:50 UTC?

## 9. Must-check before sending (on the customer side)

- **Other clients on the same workspace or key.** Any other process using this Go key
  counts against the same limit: another `ai_gate` instance (for example a VPS or
  Linux deployment), an OpenCode CLI or TUI session selecting Muse, or ad-hoc probes.
  This pack covers only the one Windows host's ledger. The OpenCode console's
  per-key usage view settles this.
- Confirm "Use balance" was not toggled during the week.

## 10. How the numbers were produced (reproducible)

- **Provider-reported usage:** `<bus>\logs\openai_usage.ndjson`, rows with
  `provider_mode=OPENCODE_API` (successful responses only).
- **Attempt counts:** `<bus>\logs\ai_gate.log`, lines `[provider_call_started]` and
  `[opencode_fallback]`.
- **Repricing:** `python/opencode_go_accounting.py` (`summarize_legacy_usage`), with
  the table in section 2.
- **From 2026-09-13:** every outbound attempt, including failures and late results, is
  also written to `<bus>\logs\opencode_go_attempts.ndjson`. It carries the provider
  response id, HTTP status, session id, all token categories and the repriced cost.
  Summarize a window with:
  `python opencode_go_accounting.py report --since 2026-09-14T00:00:00`
