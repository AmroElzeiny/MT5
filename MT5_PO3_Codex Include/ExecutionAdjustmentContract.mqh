//+------------------------------------------------------------------+
//| ExecutionAdjustmentContract.mqh                                   |
//|                                                                   |
//| Separates the plan the AI assessed from the plan the broker is    |
//| asked to execute, and states -- deterministically, in one place -- |
//| exactly which values are allowed to differ between them.          |
//|                                                                   |
//| Why this exists                                                   |
//| --------------                                                    |
//| The eleventh zero-trade run approved a setup, admitted it to the  |
//| watchlist, completed confirmation, and then failed every one of   |
//| 91 execution attempts with:                                       |
//|                                                                   |
//|   [execution_fingerprint] match=false stage=market                |
//|   changed_components=target_source,target_model,obstacle_kind,    |
//|                      tp1,tp2                                      |
//|                                                                   |
//| Nothing about the market had invalidated the trade.  The engine    |
//| rebuilt the plan at the live price, re-derived the target from     |
//| scratch (resolving the liquidity level to 4220.90 instead of the   |
//| approved 4070.51), let the target sanitizer silently substitute    |
//| synthetic_rr_fallback for the AI's ai_selected_liquidity_target,   |
//| and then compared that materially different trade against the      |
//| immutable assessment fingerprint.  The integrity check was working |
//| correctly; it was being handed a different trade.                  |
//|                                                                    |
//| The contract below makes that impossible to express.  An approved  |
//| plan carries its target identity and price forward; only the       |
//| fields named here may move, only within the bounds named here.     |
//| Anything else is a semantic change, and a semantic change is not a |
//| retryable execution failure -- it terminally invalidates the plan  |
//| or requeues it for a fresh assessment.                             |
//+------------------------------------------------------------------+
#ifndef __PO3_EXECUTION_ADJUSTMENT_CONTRACT_MQH__
#define __PO3_EXECUTION_ADJUSTMENT_CONTRACT_MQH__

#define EXECUTION_ADJUSTMENT_CONTRACT_VERSION "20260731_assessed_vs_execution_authority_v1"

//--- How a target price is allowed to move when the entry moves ------
// PRESERVE_FIXED_PRICE: the target is a structural level (liquidity pool,
//   session range, opposing imbalance cap).  Its price is a property of the
//   market, not of the entry, so it does not move at all.  This is authority
//   rule 1: a fixed liquidity target keeps the same identity AND price.
// DETERMINISTIC_RR: the target is synthetic and defined as a multiple of
//   risk.  When the entry moves, the price must be recomputed by the exact
//   approved formula from the new entry, keeping the same model and source.
//   This is authority rule 2.
#define TARGET_RECALC_PRESERVE_FIXED_PRICE "preserve_fixed_price"
#define TARGET_RECALC_DETERMINISTIC_RR     "deterministic_rr_from_entry"

//--- Execution failure classification --------------------------------
// A failure class decides whether retrying can ever succeed.  Retrying a
// SEMANTIC_PLAN_CHANGED every tick produced 91 identical rejections and zero
// information; only the transient classes may retry.
#define EXEC_FAIL_NONE                      "NONE"
#define EXEC_FAIL_TRANSIENT_QUOTE           "TRANSIENT_QUOTE_FAILURE"
#define EXEC_FAIL_TRANSIENT_SPREAD          "TRANSIENT_SPREAD_FAILURE"
#define EXEC_FAIL_TRANSIENT_BROKER          "TRANSIENT_BROKER_FAILURE"
#define EXEC_FAIL_SEMANTIC_PLAN_CHANGED     "SEMANTIC_PLAN_CHANGED"
#define EXEC_FAIL_STRUCTURAL_INVALIDATION   "STRUCTURAL_INVALIDATION"
#define EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE "TARGET_NO_LONGER_FEASIBLE"
#define EXEC_FAIL_PERMANENT_BROKER          "PERMANENT_BROKER_CONSTRAINT"

//--- Terminal actions after a classified failure ----------------------
#define EXEC_ACTION_RETRY_BOUNDED       "retry_bounded_backoff"
#define EXEC_ACTION_TERMINAL_INVALIDATE "terminal_invalidate_or_requeue_ai"
#define EXEC_ACTION_SUPPRESS            "suppress_until_input_changes"

bool ExecFailureIsTransient(const string cls) {
   return (cls == EXEC_FAIL_TRANSIENT_QUOTE ||
           cls == EXEC_FAIL_TRANSIENT_SPREAD ||
           cls == EXEC_FAIL_TRANSIENT_BROKER);
}

bool ExecFailureIsTerminal(const string cls) {
   return (cls == EXEC_FAIL_SEMANTIC_PLAN_CHANGED ||
           cls == EXEC_FAIL_STRUCTURAL_INVALIDATION ||
           cls == EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE ||
           cls == EXEC_FAIL_PERMANENT_BROKER);
}

string ExecFailureAction(const string cls) {
   if(ExecFailureIsTerminal(cls)) return EXEC_ACTION_TERMINAL_INVALIDATE;
   if(ExecFailureIsTransient(cls)) return EXEC_ACTION_RETRY_BOUNDED;
   return EXEC_ACTION_SUPPRESS;
}

//+------------------------------------------------------------------+
//| The deterministic permission itself.                              |
//|                                                                   |
//| Every bound is expressed in R (multiples of the assessed stop     |
//| distance) or in ticks so it is scale free and identical on both   |
//| the Python and MQL sides.                                         |
//+------------------------------------------------------------------+
struct ExecutionAdjustmentContract {
   string contract_version;

   bool   entry_adjustment_allowed;
   double max_entry_drift_r;          // adverse entry movement, in R
   double max_entry_drift_ticks;      // absolute floor so tiny-R plans still move

   bool   sl_adjustment_allowed;
   double max_sl_drift_r;

   bool   tp1_adjustment_allowed;
   bool   tp2_adjustment_allowed;
   string target_recalculation;       // TARGET_RECALC_*

   bool   target_identity_preserved;  // selected target identity may not change
   bool   target_source_preserved;    // ai_selected_* source may not change
   bool   target_model_preserved;     // named model may not change
   bool   obstacle_revalidation_required;

   double min_resulting_rr;
   double max_target_distance;        // absolute price distance, live-derived
   double max_cost_deterioration_r;

   datetime expiry;

   void Reset() {
      contract_version              = EXECUTION_ADJUSTMENT_CONTRACT_VERSION;
      entry_adjustment_allowed      = true;
      max_entry_drift_r             = 0.0;
      max_entry_drift_ticks         = 0.0;
      sl_adjustment_allowed         = true;
      max_sl_drift_r                = 0.10;
      tp1_adjustment_allowed        = false;
      tp2_adjustment_allowed        = false;
      target_recalculation          = TARGET_RECALC_PRESERVE_FIXED_PRICE;
      target_identity_preserved     = true;
      target_source_preserved       = true;
      target_model_preserved        = true;
      obstacle_revalidation_required= true;
      min_resulting_rr              = 0.0;
      max_target_distance           = 0.0;
      max_cost_deterioration_r      = 0.02;
      expiry                        = 0;
   }

   string Describe() const {
      return "version=" + contract_version
           + " entry_adjust=" + (entry_adjustment_allowed ? "true" : "false")
           + " max_entry_drift_r=" + DoubleToString(max_entry_drift_r, 4)
           + " sl_adjust=" + (sl_adjustment_allowed ? "true" : "false")
           + " max_sl_drift_r=" + DoubleToString(max_sl_drift_r, 4)
           + " tp1_adjust=" + (tp1_adjustment_allowed ? "true" : "false")
           + " tp2_adjust=" + (tp2_adjustment_allowed ? "true" : "false")
           + " target_recalc=" + target_recalculation
           + " identity_preserved=" + (target_identity_preserved ? "true" : "false")
           + " min_rr=" + DoubleToString(min_resulting_rr, 4)
           + " max_target_distance=" + DoubleToString(max_target_distance, 8)
           + " max_cost_deterioration_r=" + DoubleToString(max_cost_deterioration_r, 4);
   }
};

//+------------------------------------------------------------------+
//| Result of comparing a live plan against its assessed plan.        |
//|                                                                   |
//| Three buckets, deliberately: the previous single                  |
//| "changed_components" string could not distinguish "the AI's own    |
//| approved target was applied" from "the market invalidated the      |
//| setup", so both produced the same opaque rejection.               |
//+------------------------------------------------------------------+
struct SemanticPlanMatchResult {
   bool   semantic_match;              // no immutable field changed
   bool   adjustment_valid;            // authorized changes stayed in bounds
   string immutable_fields_changed;
   string authorized_fields_changed;
   string unauthorized_fields_changed;
   string adjustment_bounds;
   string failure_class;
   string result;

   void Reset() {
      semantic_match              = true;
      adjustment_valid            = true;
      immutable_fields_changed    = "";
      authorized_fields_changed   = "";
      unauthorized_fields_changed = "";
      adjustment_bounds           = "";
      failure_class               = EXEC_FAIL_NONE;
      result                      = "pass";
   }

   bool Ok() const { return (semantic_match && adjustment_valid); }
};

//+------------------------------------------------------------------+
//| Canonical target feasibility verdict.                             |
//|                                                                   |
//| Every stage -- initial plan construction, AI candidate            |
//| construction, the sanitizer, the watchlist precheck, the live      |
//| rebuild, and final validation -- reports through this one struct.  |
//| The eleventh run logged "feasible=true reward=30.01                |
//| max_allowed=30.01" from one validator and                          |
//| "ai_chosen_target_exceeds_max_distance" from another for the same  |
//| plan, because each recomputed the cap independently and rounded    |
//| the comparison differently.                                        |
//+------------------------------------------------------------------+
struct TargetFeasibilityResult {
   bool   feasible;
   string reason;
   string authority;            // which stage computed it
   string model;
   double target_price;
   double reward;               // absolute price distance to target
   double risk;                 // absolute price distance to stop
   double rr;
   double max_allowed_distance;
   double min_required_distance;
   double min_required_rr;
   long   reward_ticks;
   long   max_allowed_ticks;
   bool   direction_valid;
   bool   target_reached;
   bool   rr_floor_pass;
   bool   max_distance_pass;
   bool   min_distance_pass;

   void Reset() {
      feasible              = false;
      reason                = "unevaluated";
      authority             = "";
      model                 = "";
      target_price          = 0.0;
      reward                = 0.0;
      risk                  = 0.0;
      rr                    = 0.0;
      max_allowed_distance  = 0.0;
      min_required_distance = 0.0;
      min_required_rr       = 0.0;
      reward_ticks          = 0;
      max_allowed_ticks     = 0;
      direction_valid       = false;
      target_reached        = false;
      rr_floor_pass         = false;
      max_distance_pass     = false;
      min_distance_pass     = false;
   }

   // Full precision, deliberately: the contradiction in the eleventh run was
   // invisible because both sides printed two decimals and looked equal.
   string Describe() const {
      return "model=" + model
           + " feasible=" + (feasible ? "true" : "false")
           + " reason=" + reason
           + " authority=" + authority
           + " tp=" + DoubleToString(target_price, 8)
           + " reward=" + DoubleToString(reward, 8)
           + " risk=" + DoubleToString(risk, 8)
           + " rr=" + DoubleToString(rr, 8)
           + " reward_ticks=" + IntegerToString(reward_ticks)
           + " max_allowed=" + DoubleToString(max_allowed_distance, 8)
           + " max_allowed_ticks=" + IntegerToString(max_allowed_ticks)
           + " min_required=" + DoubleToString(min_required_distance, 8)
           + " min_rr=" + DoubleToString(min_required_rr, 8)
           + " direction_valid=" + (direction_valid ? "true" : "false")
           + " target_reached=" + (target_reached ? "true" : "false")
           + " rr_floor_pass=" + (rr_floor_pass ? "true" : "false")
           + " max_distance_pass=" + (max_distance_pass ? "true" : "false")
           + " min_distance_pass=" + (min_distance_pass ? "true" : "false");
   }
};

//+------------------------------------------------------------------+
//| Integer-tick arithmetic.                                          |
//|                                                                   |
//| The max-distance comparison is decided in whole ticks so a target |
//| capped exactly to the maximum passes deterministically instead of |
//| depending on which side accumulated the last binary rounding      |
//| error.                                                            |
//+------------------------------------------------------------------+
long PriceDistanceToTicks(const double distance, const double tick_size) {
   if(tick_size <= 0.0) return 0;
   // Round to nearest whole tick.  A value that is a hair under the cap due to
   // floating point subtraction must not be reported as a hair over it.
   return (long)MathRound(distance / tick_size);
}

bool TicksWithinCap(const long reward_ticks, const long cap_ticks) {
   if(cap_ticks <= 0) return true;   // no cap configured
   return (reward_ticks <= cap_ticks);
}

#endif // __PO3_EXECUTION_ADJUSTMENT_CONTRACT_MQH__
