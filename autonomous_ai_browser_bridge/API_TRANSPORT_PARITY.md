# PO3 local-browser / official-API parity

Both transports now freeze one `20260811_provider_exchange_v1` semantic
exchange before they build provider-specific wire payloads.

| Contract item | Local browser (`chat.completions`) | Official API (`responses`) |
|---|---|---|
| System instructions | `messages[system].content` | `instructions` |
| Evidence | Canonical sorted compact JSON | The same canonical JSON in `input_text` |
| Output contract | Strict JSON Schema | The same strict JSON Schema |
| Identity | Request ID + request identity hash | The same values |
| Deadline | One absolute deadline, sent to the bridge | The same absolute budget and trace headers |
| Retries | No transport retry | No transport retry |
| Schema correction | One bridge correction turn | One API schema-only correction turn |
| Accepted response | Strict JSON object + Pydantic validation | The same validation |
| Trading authority | Common Python decision/binding pipeline | The same pipeline |

The HTTP formats are intentionally different because the official API uses the
Responses API while the local bridge exposes an OpenAI-compatible Chat
Completions endpoint. Their transport-neutral `exchange_contract_hash` must be
identical for the same request.

Regression coverage is in
`python/tests/test_provider_transport_parity.py`. It compares the captured wire
requests, strict schema, normalized response, retry count, and exchange hash.
