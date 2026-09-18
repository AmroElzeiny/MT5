# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

Make Python's assessed/request execution fingerprint canonicalization produce exactly the same price
strings as the MQL5 verifier, so approved AI decisions stop being rejected by the EA with
`reject_stage=decision_integrity reject_reason=execution_fingerprint_mismatch`.

Python-only change. Do NOT touch any MQL file, `.set`, `.env`, bus file, or runtime artifact.
Do not revert, reformat or commit unrelated working-tree modifications (the tree is dirty).

## Confirmed evidence (front-end verified, do not rediscover)

Live defect, recurring: 9 approvals lost in 3 days (EA journals 20260914-16), e.g. NZDUSD 2026-09-16 16:20:
```
expected_assessed_execution_fingerprint=5828C283D2EAB737 (MQL)
received_assessed_execution_fingerprint=4DC677D454EA1CF8 (Python)
```
Reproduced byte-exactly. Inputs: request_execution_fingerprint=2FD20D358AC2F4C9,
candidate_hash=7F68A47094513C0C, selected_target_identity=keep_current, entry=0.574785, sl=0.57326,
tp1=0.5760135, tp2=0.57654, selected_target_price=0.57654, symbol_digits=5, symbol_tick_size=1e-05.

Root cause: a half-tick price. `0.574785 / 1e-05` in IEEE double is `57478.49999999999`.
- MQL (`MT5_PO3_Codex Include\TradeEngine.mqh:4692` `_CanonicalPrice`):
  `MathRound(price / tick) * tick` -> `NormalizeDouble(rounded, digits)` -> `DoubleToString(.., digits)`
  gives `0.57478`.
- Python (`python/decision_integrity.py:1016` `_mql_canonical_price`) uses
  `Decimal(str(float(value)))` / Decimal tick with `ROUND_HALF_UP`, which sees the exact decimal
  57478.5 and gives `0.57479`.
With MQL-style rounding the hash is 5828C283D2EAB737; with the current Python rounding it is
4DC677D454EA1CF8. Across 389 recent assessments, 26 diverge (8 of them approvals).

MQL is the verifier and owns the prices, so Python must emulate MQL double arithmetic, not the reverse.
`_mql_canonical_price` is also used by `canonical_execution_components` (~946-958) and the candidate
hash material (~1072-1082); all call sites must share the one corrected function.

## Requirements

- R1: `_mql_canonical_price(value, candidate)` must reproduce MQL `_CanonicalPrice` exactly using
  IEEE double (Python float) arithmetic: `q = float(value or 0.0) / float(tick)`; round half AWAY
  from zero on the double `q` (MQL `MathRound`), implemented without the `floor(q + 0.5)` precision
  trap (e.g. compare `abs(q) - floor(abs(q)) >= 0.5`); multiply back by the float tick; format with
  `digits` decimals. Keep existing digits/tick defaulting semantics (digits default 5, tick default
  10**-digits when missing or <= 0). Negative zero must format as `0.00000`-style, not `-0.00000`.
- R2: Keep function name/signature; all existing callers use it unchanged. Remove the now-unused
  import only if nothing else uses it (`ROUND_HALF_UP` is still used at line ~217 — keep it).
- R3: Add ONE focused regression test in `python/tests/test_decision_integrity.py` that asserts, with
  the literal inputs above: `_mql_canonical_price(0.574785, {"symbol_digits":5,"symbol_tick_size":1e-05}) == "0.57478"`,
  `0.5760135 -> "0.57601"`, and that `assessed_execution_fingerprint(candidate, assessment)` equals
  `5828C283D2EAB737` for the NZDUSD inputs (candidate carries request_execution_fingerprint,
  candidate_hash, symbol_digits, symbol_tick_size; assessment carries selected_target_identity, entry,
  sl, tp1, tp2, selected_target_price). Also assert a non-tie value is unchanged (0.57326 -> "0.57326")
  and an index-style tick works (value 21457.13, digits 2, tick 0.25 -> "21457.25").
- R4: Run only `python -m pytest python/tests/test_decision_integrity.py -q` plus the full suite ONCE
  at the end (`python -m pytest python/tests -q` from the `python` directory). Report exact counts.
  Existing tests that encoded the old Decimal half-up tie behaviour must be reported, not silently
  rewritten; update them only if they assert the old tie rounding, and say so explicitly.

## Out of scope

MQL changes, recompiles, gate restarts, bus edits, cache re-keying.
