# v-next missing implementation audit

Audit date: 2026-06-28

Status labels used:

- implemented
- partially implemented
- missing
- implemented but not enforced
- implemented in MQL only
- implemented in Python only
- documented but not coded

## MQL inputs

| Area | Status | Notes |
| --- | --- | --- |
| `InpTradeOnlyKillzones` | implemented | Input exists and is used by the MQL objective gate. |
| London killzone hour/minute inputs | implemented | Minute-level London start/end inputs exist and are included in runtime inputs. |
| New York killzone hour/minute inputs | implemented | Minute-level New York start/end inputs exist and are included in runtime inputs. |
| Optional Asia killzone inputs | implemented | `InpEnableAsiaKillzone` plus Asia start/end hour/minute inputs exist. |
| `InpRejectSyntheticFallbackAfterCrossedObstacle` | implemented | MQL blocks synthetic fallback targets crossing opposing imbalance. |
| `InpExecutionRejectCostR` | implemented | MQL objective execution-cost block exists. |
| `InpExecutionReduceRiskCostR` | implemented | MQL risk-reduction pressure exists. |
| `InpMicroScalpMaxCostFracOfPlannedR` | implemented | MQL blocks high cost micro/edge branches. |
| `InpSuppressMicroBisiSibiEdge` | implemented | MQL suppression gate exists. |
| `InpSuppressStaleFvgBranches` | implemented | MQL stale FVG suppression exists. |
| `InpSuppressTouchedContinuationUnlessRetested` | implemented | MQL continuation touched/retest suppression exists. |
| `InpSuppressContinuationTouchedFvg` | implemented | MQL continuation touched suppression exists. |
| `InpSuppressContinuationStaleFvg` | implemented | MQL continuation stale suppression exists. |

## EA runtime payload

| Required payload field group | Status | Notes |
| --- | --- | --- |
| `runtime_inputs` object | partially implemented | Present, but not all requested critical values were included before this follow-up. |
| `runtime_input_hash` | partially implemented | Present, but hash did not cover all critical runtime values. |
| Strategy mode | implemented | Included in runtime inputs. |
| Killzone inputs | implemented | Included in runtime inputs. |
| Suppression inputs | implemented | Included in runtime inputs. |
| Execution cost inputs | implemented | Included in runtime inputs. |
| Target fallback inputs | implemented | Included in runtime inputs. |
| AI thresholds | partially implemented | Runtime object included minimum confidence, but full AI threshold/control set was incomplete. |
| Risk/exposure inputs | partially implemented | Some values existed elsewhere in payload, but not as a complete runtime input contract. |
| Snapshot enabled/disabled state | partially implemented | Runtime object included snapshot flags; runtime inputs were incomplete. |
| Exclusive trading enabled state | implemented | Exclusive mode input is included. |
| Virtual ledger/backend PnL mode | missing | Needed explicit metadata in runtime inputs. |

## Python `ai_gate`

| Area | Status | Notes |
| --- | --- | --- |
| Reads `runtime_inputs` from actual payload | partially implemented | Existing hard gate uses payload runtime inputs for several checks. |
| Avoids static `.mqh` defaults for live decisions | partially implemented | Existing code did not parse `.mqh`, but missing runtime inputs could still leave Python with incomplete live context. |
| Rejects live requests if critical runtime inputs are missing | partially implemented | A small critical subset was checked; requested full contract was incomplete. |
| Applies hard pre-gates before OpenAI | partially implemented | Existing gate blocked several objective cases before OpenAI, but missing required objective cases and cost/log behavior. |
| Python AI config object | missing | No single validated runtime config object existed. |
| Batch API controls | missing | No Batch API behavior or live-disable guard existed. |
| Flex processing controls | missing | No live triple-confirm Flex guard existed. |
| Prompt cache controls | partially implemented | Prompt cache key was hardcoded-ish and retention fixed to `24h`. |
| Setup-signature decision cache | missing | No local decision cache existed. |
| AI cost report JSONL | missing | Usage logger existed, but not the requested per-request cost report schema. |

## Env/config files

| Area | Status | Notes |
| --- | --- | --- |
| `.env.example` | missing | No example file existed in the Python folder. |
| AI cost/safety variables documented | documented but not coded | Some prompt-cache related envs appeared elsewhere, but the requested complete control surface was absent. |
| Actual env parsing | partially implemented | Existing constants read some env vars directly without central validation or live-safety policy. |

## Tests/verification

| Test area | Status | Notes |
| --- | --- | --- |
| Killzone-only block | missing | No deterministic verification script existed. |
| Family suppression block | missing | No deterministic verification script existed. |
| Execution-cost block | missing | No deterministic verification script existed. |
| Synthetic fallback crossed obstacle block | missing | No deterministic verification script existed. |
| Missing runtime inputs live reject | missing | No deterministic verification script existed for the full contract. |
| AI call skipped for hard pre-gate | missing | No deterministic verification script existed. |
| Cache hit avoids OpenAI call | missing | No decision cache existed. |
| Batch not used for live | missing | No Batch API guard existed. |
| Flex not used for live unless explicitly allowed | missing | No Flex guard existed. |
| Ledger wrong-symbol/wrong-price-scale rejection | partially implemented | Repair tool existed but did not emit the requested outputs or fixture proof. |

## Follow-up scope

The follow-up implementation must complete the Python runtime config, live-safe Batch/Flex behavior, configurable prompt caching, local decision cache, expanded hard pre-gate, AI cost report, runtime-input contract, MQL final pre-order safety call, ledger repair outputs, verification script, and documentation updates.
