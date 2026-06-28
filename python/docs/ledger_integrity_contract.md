# Ledger integrity contract

Closed-trade analytics should use clean ledger rows by default. Suspicious rows
are excluded unless forensic mode is explicitly requested.

## Repair tool

Run:

```powershell
python tools\repair_trade_ledger.py
```

Optional:

```powershell
python tools\repair_trade_ledger.py --logs-dir <trade_results_dir> --out-dir ledger_repair_output
```

Outputs:

- `repaired_system_trade_history.json`
- `rejected_deals.json`
- `ledger_integrity_report.md`
- `ledger_integrity_report.json`

## Rejection checks

The repair tool detects:

- `symbol_mismatch`
- `price_scale_mismatch`
- `magic_mismatch`
- `comment_mismatch`
- `position_id_reused`
- `volume_overmatched`
- `account_context_mismatch`
- `missing_trade_key`
- `missing_trade_id`
- `legacy_nonexclusive_history`
- missing/zero PnL and invalid planned entry/SL/TP direction

The fixture under
`tests/fixtures/ledger_wrong_symbol_price_scale/trade_results` proves that an
XAUUSD trade with a EURUSD-priced exit is rejected from clean analytics.

## Analytics default

`expectancy_report.py` excludes suspicious rows by default. Use
`--include-suspicious` or `EXPECTANCY_INCLUDE_SUSPICIOUS=true` only for
forensic investigations.
