# MT5 project safety and runtime contract

Existing repository instructions remain authoritative.

## Default mode

`Offline`

Offline means:
- repository analysis;
- unit/integration tests;
- deterministic fixtures/replays;
- MQL compilation where safe;
- no trade placement.

## Demo mode

Demo-forward trading activity is allowed only when:
- the user explicitly requests it;
- the mission contains `Demo authorization: YES`;
- delegation uses `-RuntimeMode Demo`;
- the worker proves the target account/environment is demo before any trading mutation.

Never force an approval or trade.

## Real-money/live accounts

Automated agent mutation of real-money/live trading is not supported by this orchestrator.

## Contract integrity

For changes touching the trade path, inspect as relevant:
- market/setup state;
- PO3/FVG logic;
- plan/candidate identity;
- Python/MQL serialization;
- file-bus lifecycle;
- provider call;
- response schema/identity;
- final allow state;
- freshness/watchlist/entry;
- risk sizing;
- order submission;
- broker result;
- trade ledger;
- retry/restart/late-result behavior.

Do not claim a deployment/runtime pass from repository tests alone.
