# Local AI File Bridge Contract

The R&D Factory is the request owner. The separate local AI machine is the response producer.

## Request lifecycle

1. Factory creates a unique immutable `request_id`.
2. It builds deterministic findings and bounded evidence.
3. It writes a temporary request bundle and atomically publishes it as `requests/<request_id>/`.
4. `READY` is written before publication and is visible only with the completed bundle.
5. The local AI machine reads `request.json` and the listed evidence files.
6. It writes exactly one JSON response to `responses/<request_id>.json` atomically.
7. Factory verifies stable file state, freshness, request ID, research run ID, request hash, evidence-file integrity, schema version, enum values and additional-field restrictions.
8. Success moves request+response to `archive/<request_id>/`.
9. Invalid/stale/mismatched responses and their request bundle are moved to `quarantine/<request_id>/` with a reason.
10. Timeout quarantines the request and fails closed.

The external machine must never return trading instructions. The allowed production recommendations are limited to research/human-review states embedded in the response schema.
