//+------------------------------------------------------------------+
//| Config.mqh - central settings                                    |
//+------------------------------------------------------------------+
#ifndef __PO3_AIGATE_CONFIG_MQH__
#define __PO3_AIGATE_CONFIG_MQH__

const string ENGINE_VERSION = "5.5-version-z-canonical-request-20260724-v8";
const string ENGINE_INPUT_SCHEMA = "po3-fvg-ai-provider-version-z-20260724-v7";
const string AI_DECISION_SCHEMA_VERSION = "20260724_canonical_frozen_request_v10";
const string AI_TARGET_ARBITRATION_SCHEMA_VERSION = "20260717_target_fingerprint_authority_v6";
const string AI_PROMPT_CONTRACT_VERSION = "20260724_canonical_frozen_request_v12";
const string AI_ROLE_CONTRACT_VERSION = "20260724_python_bound_roles_v3";
const string AI_PROVIDER_CONTRACT_VERSION = "20260723_provider_neutral_transport_v2";
const string AI_REQUEST_IDENTITY_VERSION = "20260724_ai_request_identity_v3";
const string AI_EVIDENCE_ENVELOPE_VERSION = "20260718_decision_evidence_v1";
const string AI_FAMILY_PROFILE_VERSION = "20260718_family_context_v1";
const string AI_TRADE_MEMORY_SCHEMA_VERSION = "20260718_trade_memory_v1";
const string AI_RETRIEVAL_POLICY_VERSION = "20260718_hybrid_analogue_retrieval_v1";
const string AI_CONSENSUS_RESOLVER_VERSION = "20260718_deterministic_consensus_v1";
const string TRADE_LEDGER_SCHEMA_VERSION = "20260718_trade_ledger_provider_identity_v8";
const string SETUP_TAXONOMY_VERSION = "20260716_setup_taxonomy_v1";
const string FEATURE_LINEAGE_VERSION = "20260718_tick_path_evidence_v3";
const string RISK_MODEL_VERSION = "20260717_original_initial_risk_v3";
const string REPEATABILITY_SCHEMA_VERSION = "20260718_provider_neutral_repeatability_v3";
const string HIERARCHICAL_PRIOR_SCHEMA_VERSION = "20260717_hierarchical_prior_v1";
const string RISK_FACTOR_SCHEMA_VERSION = "20260717_risk_factor_v1";
const string COMMISSION_MODEL_SCHEMA_VERSION = "20260717_broker_cost_v1";
const string MANAGEMENT_SCHEMA_VERSION = "20260718_management_action_lifecycle_v4";
const string MANAGEMENT_EXPERIMENT_SCHEMA_VERSION = "20260717_management_experiment_v1";
const string MANAGEMENT_COUNTERFACTUAL_SCHEMA_VERSION = "20260717_management_counterfactual_v2";
const string INVALIDATION_POLICY_SCHEMA_VERSION = "20260717_invalidation_asset_class_v1";
const string SHADOW_CANDIDATE_SCHEMA_VERSION = "20260717_shadow_candidate_v3";
const string NORMALIZED_FVG_SCHEMA_VERSION = "20260717_normalized_fvg_v2";
const string ARCHITECTURE_CONTRACT_VERSION = "20260718_provider_neutral_architecture_v4";
const string LIVE_FORWARD_CONTRACT_VERSION = "20260717_live_forward_v1";
const string SEMANTIC_CACHE_SCHEMA_VERSION = "20260717_semantic_cache_v1";
const string POLICY_MANIFEST_SCHEMA_VERSION = "20260717_policy_manifest_v1";
const string COHORT_SCHEMA_VERSION = "20260717_homogeneous_cohort_v1";
const string HIERARCHICAL_OUTCOME_MODEL_VERSION = "20260717_hierarchical_outcome_shadow_v2";
const string ENTRY_MODEL_VERSION = "20260717_entry_path_shadow_v2";
const string MANAGEMENT_MODEL_VERSION = "20260717_management_alpha_shadow_v2";
const string SHADOW_OUTCOME_SCHEMA_VERSION = "20260717_shadow_outcome_complete_v2";
const string FILE_BUS_LIFECYCLE_VERSION = "20260724_file_bus_lifecycle_v3";
const string REQUEST_LIFECYCLE_VERSION = "20260724_exactly_once_request_v1";
const string CONTRACT_MANIFEST_VERSION = "20260724_contract_compatibility_v1";
const string CALIBRATION_CONTRACT_VERSION = "20260716_oos_calibration_v1";
const string DEPLOYMENT_MANIFEST_SCHEMA_VERSION = "20260717_deployment_manifest_v1";
const double SHADOW_ADVERSE_THRESHOLD_R = 0.50;

uint PO3ContractFnv1a(const string value)
{
   uint h = 2166136261;
   for(int i=0; i<StringLen(value); i++)
      h = (h ^ (uint)StringGetCharacter(value, i)) * 16777619;
   return h;
}

string PO3ContractManifestMaterial()
{
   string material = "contract_manifest_version=" + CONTRACT_MANIFEST_VERSION;
   material += "|engine_version=" + ENGINE_VERSION;
   material += "|engine_input_schema=" + ENGINE_INPUT_SCHEMA;
   material += "|decision_schema_version=" + AI_DECISION_SCHEMA_VERSION;
   material += "|target_arbitration_schema_version=" + AI_TARGET_ARBITRATION_SCHEMA_VERSION;
   material += "|prompt_contract_version=" + AI_PROMPT_CONTRACT_VERSION;
   material += "|role_contract_version=" + AI_ROLE_CONTRACT_VERSION;
   material += "|provider_contract_version=" + AI_PROVIDER_CONTRACT_VERSION;
   material += "|file_bus_lifecycle_version=" + FILE_BUS_LIFECYCLE_VERSION;
   material += "|request_lifecycle_version=" + REQUEST_LIFECYCLE_VERSION;
   material += "|request_identity_version=" + AI_REQUEST_IDENTITY_VERSION;
   material += "|setup_taxonomy_version=" + SETUP_TAXONOMY_VERSION;
   material += "|family_profile_version=" + AI_FAMILY_PROFILE_VERSION;
   material += "|retrieval_policy_version=" + AI_RETRIEVAL_POLICY_VERSION;
   material += "|consensus_resolver_version=" + AI_CONSENSUS_RESOLVER_VERSION;
   material += "|evidence_envelope_version=" + AI_EVIDENCE_ENVELOPE_VERSION;
   material += "|trade_memory_schema_version=" + AI_TRADE_MEMORY_SCHEMA_VERSION;
   material += "|repeatability_schema_version=" + REPEATABILITY_SCHEMA_VERSION;
   return material;
}

string PO3ContractManifestHash()
{
   return IntegerToString((int)(PO3ContractFnv1a(PO3ContractManifestMaterial()) % 2147483647));
}

enum ENUM_PO3_STOP_MODEL
{
   STOP_FVG_EDGE = 0,
   STOP_STRUCTURAL_SWEEP = 1,
   STOP_STRUCTURAL_SWING = 2
};

enum ENUM_STRATEGY_MODE
{
   STRATEGY_FULL_PO3 = 0,
   STRATEGY_MICRO_PO3 = 1,
   STRATEGY_SCALP_CONTINUATION = 2,
   STRATEGY_HYBRID = 3
};

enum TesterAiMode
{
   TESTER_AI_RECORD_ONLY = 0,
   TESTER_AI_CACHE_ONLY = 1,
   TESTER_AI_LIVE_WAIT_DEBUG = 2
};

enum ENUM_NETTING_POSITION_POLICY
{
   NETTING_REJECT_STARTUP = 0,
   NETTING_FORCE_ONE_MANAGED_POSITION_PER_SYMBOL = 1
};

enum ENUM_NORMALIZED_FVG_MODE
{
   NORMALIZED_FVG_OFF = 0,
   NORMALIZED_FVG_SHADOW = 1,
   NORMALIZED_FVG_ENFORCE = 2
};

enum ENUM_INVALIDATION_CONFIRMATION_MODE
{
   INVALIDATION_CLOSED_M1_BAR = 0,
   INVALIDATION_CLOSED_ENTRY_TF_BAR = 1,
   INVALIDATION_N_SECOND_PERSISTENCE = 2,
   INVALIDATION_PRICE_SPREAD_BUFFER = 3,
   INVALIDATION_TICK = 4
};

enum ENUM_THESIS_INVALIDATION_POLICY
{
   THESIS_INVALIDATION_PARTIAL_EXIT = 0,
   THESIS_INVALIDATION_FULL_EXIT = 1,
   THESIS_INVALIDATION_ORIGINAL_SL_TP_ONLY = 2
};

// --- Scan / scheduling ---
input ENUM_STRATEGY_MODE InpStrategyMode = STRATEGY_HYBRID;
input string InpStrategyPreset       = "micro_intraday";
input int   InpScanIntervalMinutes   = 1;
input int   InpTimerTickSeconds      = 1;
input int   InpMaxSymbolsPerTick     = 80;
input bool  InpScanAllMarketWatch    = true;
input string InpTesterSymbols        = "";
input bool  InpPauseScanWhilePendingAI = false;
input int   InpMaxPendingAiRequests  = 12;
input int   InpWatchlistMaxBars      = 90;
input int   InpWatchlistMaxMinutes   = 90;
input int   InpPendingAiTimeoutMin   = 6;
input bool  InpAiWaitInTester        = true;
input int   InpAiWaitPollMs          = 250;
input int   InpAiWaitSliceSeconds    = 15;
input int   InpAiWaitTimeoutRealMin  = 2;
input int   InpTesterPersistIntervalMin = 15;
input bool  InpTesterRejectStaleAiResults = true;
input int   InpTesterMaxAiResultAgeSimMinutes = 15;
input bool  InpTesterFreezeAiExecutionSnapshot = true;
input TesterAiMode InpTesterAiMode = TESTER_AI_RECORD_ONLY;
input bool  InpTesterAllowLiveWaitDebugTrading = false;
input int   InpTesterLiveWaitDebugMaxDurationHours = 6;
input bool  InpVerboseJournal        = true;
input bool   InpJournalTesterOnly     = false;
input bool   InpRolloverProtectionEnable = true;
input string InpTradingFreezeStartServerTime = "23:54";
input string InpTradingFreezeEndServerTime = "01:05";
input string InpServerMarketCloseTime = "00:00";
input int    InpCloseManagedTradesBeforeMarketCloseMin = 6;

// --- Timeframes ---
input ENUM_TIMEFRAMES InpHTF         = PERIOD_M15;
input ENUM_TIMEFRAMES InpEntryTF     = PERIOD_M1;
input ENUM_TIMEFRAMES InpConfirmTF   = PERIOD_M1;
input ENUM_TIMEFRAMES InpSessionMapTF = PERIOD_M5;
input bool InpPresetOverridesTimeframes = false;

// --- PO3 / dealing range ---
input int    InpDRLookbackBars       = 5;
input double InpDRMaxPct             = 3.0;
input bool   InpPreferRunningSweep   = false;
input double InpRunningStrengthMult  = 0.5;
input bool   InpAllowDevelopingBosContext = true;
input double InpDevelopingContextSelectionPenalty = 1.5;
input double InpDevelopingContextExecutionPenalty = 3.5;
input bool   InpRequireConfirmedPO3ForExecution = false;
input int    InpMaxContextAgeBars    = 24;
input int    InpContextAgeGraceBars  = 4;
input double InpContextAgePenaltyPerBar = 0.2;
input int    InpMinSweepStrengthTicks = 1;
input bool   InpUseAutoServerUtcOffset = true;
input int    InpServerUtcOffsetHours = 0;
input int    InpAsiaSessionStartHour = 0;
input int    InpAsiaSessionEndHour   = 10;
input int    InpLondonSessionStartHour = 10;
input int    InpLondonSessionEndHour   = 17;
input int    InpNewYorkSessionStartHour = 16;
input int    InpNewYorkSessionEndHour   = 0;
input int    InpLondonKillzoneStartHour = 10;
input int    InpLondonKillzoneEndHour   = 11;
input int    InpNewYorkKillzoneStartHour = 16;
input int    InpNewYorkKillzoneEndHour   = 17;
input bool   InpTradeOnlyKillzones = false;
input int    InpLondonKillzoneStartMinute = 0;
input int    InpLondonKillzoneEndMinute = 0;
input int    InpNewYorkKillzoneStartMinute = 0;
input int    InpNewYorkKillzoneEndMinute = 0;
input bool   InpEnableAsiaKillzone = false;
input int    InpAsiaKillzoneStartHour = 0;
input int    InpAsiaKillzoneStartMinute = 0;
input int    InpAsiaKillzoneEndHour = 2;
input int    InpAsiaKillzoneEndMinute = 0;
input int    InpLiquidityLookbackBars = 120;
input int    InpLiquiditySwingSpan    = 2;
input double InpLiquidityEqualTolAtrFrac = 0.15;
input int    InpLiquidityClusterMinCount = 2;

// --- Displacement / BOS ---
input bool   InpRequireDisplacement  = true;
input int    InpDispSearchBars       = 60;
input int    InpBOSLookbackBars      = 16;
input double InpDispBodyATRMin       = 0.14;
input double InpDispRangeATRMin      = 0.24;
input double InpDispBodyFracMin      = 0.14;
input double InpDispRangeVsRecentMult = 0.65;
input double InpDispCloseNearExtremeMax = 0.65;
input bool   InpDispRequireImbalance = false;
input double InpDispVolumeVsRecentMult = 0.6;
input double InpDispMinQuality       = 3.0;
input int    InpStructureSwingSpan   = 1;
input int    InpInternalStructureSpan = 1;
input bool   InpStructureRejectWickOnly = false;
input bool   InpStructureRequireFollowThrough = false;
input double InpStructureBodyBreakMinFrac = 0.03;
input bool   InpRequireLtfStructure  = false;

// --- OTE confluence ---
input bool   InpRequireOTE           = false;
input double InpOTELow               = 0.618;
input double InpOTEHigh              = 0.79;
input double InpRetraceTouchLow      = 0.48;
input double InpRetraceTouchHigh     = 0.62;

// --- FVG / entry ---
input int    InpLookbackLtfBars      = 360;
input int    InpMinLtfBars           = 40;
input int    InpMinFvgWidthTicks     = 1;
input ENUM_NORMALIZED_FVG_MODE InpNormalizedFvgMode = NORMALIZED_FVG_SHADOW;
input double InpNormalizedFvgSpreadMult = 1.0;
input double InpNormalizedFvgAtrFrac = 0.01;
input double InpNormalizedFvgSessionNoiseFrac = 0.10;
input int    InpNormalizedFvgSessionNoiseBars = 120;
input int    InpNormalizedFvgMinAssetClassSamples = 100;
input string InpNormalizedFvgAssetClassPolicyFile = "PO3_AI_BUS\\config\\normalized_fvg_policy.v2.json";
input double InpStopMaxFracOfPrice   = 0.05;
input int    InpSLBufferPts          = 4;
input double InpSLBufferAtrFrac      = 0.018;
input int    InpMidTouchLookbackBars = 6;
input int    InpMaxFvgCandidatesPerSymbol = 6;

input bool   InpRequireFvgAfterManip = true;
input bool   InpRequireFvgAfterDisp  = false;

input bool   InpRequireFvgMidMitigation = false;
input double InpFvgMidToleranceFrac  = 0.45;
input bool   InpRequireHtfFvgOverlap = false;
input int    InpHTFFvgLookbackBars   = 180;

// --- Setup-family kill switches ---
input bool   InpEnableFvgMid               = true;
input bool   InpEnableFvgEdge              = true;
input bool   InpEnableBreakerRetest        = true;
input bool   InpEnableOteInsideFvg         = true;
input bool   InpEnableNestedFvg            = true;
input bool   InpEnableSessionReentry       = true;
input bool   InpEnableRangeReentry         = true;
input bool   InpEnableContinuationReentry  = true;
input bool   InpEnableMPCTrading = true;
input bool   InpEnableBucketRiskPolicy = true;
input string InpBucketRiskPolicyFile = "PO3_AI_BUS\\config\\bucket_risk_policy.json";
input bool   InpOnlyBreakerRetestVirginStrongOrigin = false; // Exclusive mode: only breaker_retest + virgin_fvg + strong_origin may trade
input double InpStrongOriginMinScore = 7.0; // Minimum FVG origin_score required for strong_origin in exclusive mode
input bool   InpExclusiveModelFilterBeforeAI = true; // Block non-exclusive candidates before AI requests
input bool   InpExclusiveModelFilterLogRejected = true; // Log each candidate rejected by the exclusive model filter
input bool   InpSuppressMicroBisiSibiEdge = false;
input bool   InpSuppressStaleFvgBranches = true;
input bool   InpSuppressTouchedContinuationUnlessRetested = true;
input bool   InpSuppressContinuationTouchedFvg = false;
input bool   InpSuppressContinuationStaleFvg = true;

// --- Confirmation / watchlist ---
input bool   InpWaitB50OnM1          = false;
input bool   InpWaitCandleConfirm    = true;
input double InpConfirmBodyFracMin   = 0.45;
input double InpConfirmCloseNearExt  = 0.5;
input bool   InpAllowSameStoryNewEntry = false;
input int    InpMaxSameStoryEntryAttempts = 1;

// --- Risk / exposure ---
input ulong  InpMagicNumber          = 5303001;
input int    InpMaxOpenPositions     = 55;
input int    InpMaxTradesPerScan     = 6;
input int    InpMaxTradesPerSweep    = 1;

input bool   InpSkipIfAnyPositionOpen= false;
input bool   InpSkipIfSymbolOpen     = false;

input double InpRiskPerTradePct      = 0.5;
input double InpRiskPerTradeMoney    = 0.0;
input double InpMaxRiskPerTradePct   = 2.5;
input int    InpMaxSlippagePts       = 6;
input int    InpMaxSpreadTicks       = 100;
input double InpMaxSpreadRiskFrac    = 0.22;
input int    InpMinStopTicks         = 25;
input double InpMinStopAtrFrac       = 0.07;
input double InpMinStopEntryAtrFrac  = 0.16;
input double InpMinStopSpreadMult    = 6.0;
input double InpMaxEntryDriftR       = 0.4;
input double InpMarketEntryToleranceR = 0.55;
input double InpEntryZoneToleranceR  = 0.35;
input int    InpPendingEntryRelaxAfterBars = 3;
input double InpMinLiveRR2           = 0.85;
input double InpMaxPlanRR2           = 5.0;
input double InpMaxTargetAdrFrac     = 0.80;
input double InpMaxTargetAtrMult     = 5.00;
input double InpMinTargetAtrMult     = 0.08;
input double InpMinTargetAdrFrac     = 0.006;
input double InpMinTP1SpreadMult     = 4.0;
input int    InpPendingOrderExpiryMin = 45;
input int    InpPendingExpiryH1Minutes = 240;
input int    InpPendingExpiryH4Minutes = 720;
input int    InpPendingExpiryD1Minutes = 2880;
input int    InpBrokerStopBufferPts  = 5;
input double InpCommissionPerLotRoundTurn = 0.0;
input bool   InpBrokerCostHistoryEnable = true;
input int    InpBrokerCostMinSamples = 20;
input double InpBrokerCostStressedPercentile = 0.95;
input int    InpBrokerCostHistoryDays = 90;
input double InpCommissionFallbackPerLotRoundTurn = 0.01;
input int    InpSessionFillPenaltyPtsActive = 2;
input int    InpSessionFillPenaltyPtsOffHours = 8;
input int    InpSessionFillPenaltyPtsShock = 12;
input double InpObstacleMinStopMult = 0.2;
input int    InpObstacleBufferPts    = 2;
input bool   InpRejectAgainstHtfImbalance = false;
input bool   InpBlockSyntheticTargetThroughOpposingImbalance = false;
input bool   InpAllowSyntheticRRTarget = true;
input bool   InpRejectSyntheticFallbackAfterCrossedObstacle = true;
input bool   InpRequireAITargetArbitrationOnObstacle = true;
input bool   InpHardRejectCrossedObstacleTarget = false;
input bool   InpAllowAIToUseLiquidityTargetBehindMinorBlocker = true;
input bool   InpAllowPartialBeforeObstacle = true;
input double InpBlockerKillSeverity = 8.0;
input double InpBlockerMajorSeverity = 6.5;
input double InpBlockerMinorMaxSeverity = 3.5;
input double InpObstacleRejectR = 0.70;
input double InpFallbackRR2          = 1.05;
input double InpFallbackRRBufferR     = 0.05;
input bool   InpMaxPortfolioClusterEnable = false;
input double InpMaxCorrelatedRiskFrac = 0.45;
input double InpMaxUsdConcentrationFrac = 0.55;
input double InpMaxClusterRiskFrac   = 0.45;
input double InpMaxSessionExposureFrac = 0.65;
input double InpPortfolioSchedulerEvWeight = 1.4;
input double InpPortfolioSchedulerCorrPenalty = 0.85;
input double InpPortfolioSchedulerUsdPenalty = 0.65;
input double InpPortfolioSchedulerSessionPenalty = 0.45;
input double InpPortfolioSchedulerDiversityBonus = 0.4;
input double InpRunnerLiquidityRRFloor = 0.9;
input double InpRunnerSequenceQualityFloor = 4.6;
input double InpRunnerHtfAlignmentFloor = 4.2;
input double InpRunnerCostRCeiling = 0.3;
input double InpRunnerAdverseContextCeiling = 4.8;
input bool   InpAllowRunnerDowngrade = true;
input double InpStandardTradeLiquidityRRFloor = 1.10;
input double InpStandardTradeCostRCeiling = 0.24;
input double InpExecutionRejectCostR = 0.10;
input double InpExecutionReduceRiskCostR = 0.07;
input double InpMicroScalpMaxCostFracOfPlannedR = 0.10;
input double InpMinNetExpectedR = 0.05;
input double InpMicroEntryZoneToleranceR = 0.35;
input double InpMicroMarketEntryToleranceR = 0.55;
input int    InpMicroPendingExpiryMin = 45;
input int    InpStopAuditNoiseBars  = 12;
input double InpStopAuditNoiseAtrFrac = 0.12;
input double InpOteSoftnessFrac     = 0.12;
input int    InpStopAfterConsecutiveLosses = 0;
input int    InpStopAfterBadSlippageCount = 0;
input double InpBadSlippageRFloor   = 0.18;
input int    InpBehaviorStopCooldownMin = 180;

input bool   InpMaxTotalRiskEnable   = false;
input double InpMaxTotalRiskMoney    = 30.0;
input double InpMaxTotalRiskPct      = 3.0;
input bool   InpRequireAggregateRiskCapLive = false;
input bool   InpRiskFactorGateEnable = false;
input bool   InpRiskFactorPolicyRequiredLive = false;
input string InpRiskFactorPolicyFile = "PO3_AI_BUS\\config\\risk_factor_policy.v1.json";
input double InpDailyLossCapPct      = 98.151;
input double InpDailyLossCapMoney    = 0.0;
input bool   InpAnalyticsEnable      = true;
input int    InpAnalyticsHistoryDays = 45;
input double InpAnalyticsVirtualBalance = 100000.0;
input ENUM_NETTING_POSITION_POLICY InpNettingPositionPolicy = NETTING_REJECT_STARTUP;
input double InpLedgerRReconciliationTolerance = 0.02;
input double InpLedgerMoneyReconciliationTolerance = 0.02;
input double InpLedgerVolumeReconciliationTolerance = 0.00000001;
input double InpLedgerMaxMfeR = 50.0;
input double InpLedgerMaxAbsMaeR = 50.0;
input int    InpLedgerTimestampToleranceSec = 10;
input double InpLedgerPriceTickTolerance = 2.0;
input bool   InpUseSnapshotAI        = false;
input bool   InpRequireSnapshots     = false;
input int    InpSnapshotWidth        = 1600;
input int    InpSnapshotHeight       = 900;
input int    InpSnapshotDelayMs      = 100;
input int    InpSnapshotReadyTimeoutMs = 3000;
input int    InpSnapshotChartReadyTimeoutMs = 3000;
input bool   InpAutoAnalyticsRefresh = true;
input bool   InpAnalyticsAutoActivate = false;
input int    InpPolicyReloadMinutes  = 5;
input bool   InpPolicyShadowMode     = true;
input int    InpPolicyMinTotalClosedTrades = 100;
input int    InpPolicyMinBucketTrades = 50;
input double InpPolicyMinProfitFactorAfterCosts = 1.0;
input double InpPolicyMaxTrainTestGapR = 0.25;
input double InpPolicyMinPositiveFoldRate = 0.6;
input double InpPolicyMaxDrawdownR = 12.0;

// --- Regime gates (ATR% and simple trend strength) ---
input bool   InpRegimeGateEnable     = true;
input double InpAtrMinPct            = 0.0003;
input double InpAtrMaxPct            = 0.15;
input double InpTrendStrengthMin     = 0.14;
input double InpAdxMin               = 5.0;
input double InpAdrMinPct            = 0.0005;
input double InpSessionVolMinAdrFrac = 0.015;
input double InpExpansionRatioMin    = 0.3;
input double InpExpansionRatioMax    = 4.5;
input double InpVwapMaxDistAtr       = 5.5;

// --- TP / BE ---
input bool   InpBEEnable             = false;
input double InpBEOffsetPts          = 0.0;
input bool   InpBEOnTP1              = false;
input int    InpMinMinutesBeforeBE   = 45;
input double InpBETriggerR           = 1.25;

input bool   InpTPUseFibExtension    = true;
input double InpTPFibExtension       = 1.35;
input bool   InpTPPreferLiquidityTarget = true;

input bool   InpTP1UseFibExtension   = false;
input double InpTP1FibExtension      = 1.61;
input double InpTP1PartialPct        = 0.2;

// --- Deterministic setup floors ---
input double InpSetupFloorReversal = 30.0;
input double InpSetupFloorBreaker = 29.0;
input double InpSetupFloorContinuation = 28.0;
input double InpSetupFloorSession = 28.0;
input double InpSetupFloorRange = 28.0;
input double InpSetupFloorTierBExtra = 0.5;
input double InpSetupFloorTierCExtra = 99.0;

// --- Family-specific gates ---
input double InpAiScoreFullPO3 = 6.8;
input double InpAiScoreMicroPO3 = 7.2;
input double InpAiScoreContinuation = 7.3;
input double InpAiScoreRange = 7.0;
input double InpAiScoreFailedBreakout = 7.2;
input bool   InpGlobalAiScoreAsHardFloor = false;

input double InpSetupFloorFullPO3 = 30.0;
input double InpSetupFloorMicroPO3 = 30.0;
input double InpSetupFloorMicroBisiSibi = 29.0;
input double InpSetupFloorContinuationFamily = 28.0;
input double InpSetupFloorRangeFamily = 28.0;
input double InpSetupFloorFailedBreakout = 29.0;

input double InpMinRRFullPO3 = 0.85;
input double InpMinRRMicroPO3 = 0.85;
input double InpMinRRContinuation = 0.85;
input double InpMinRRRange = 0.8;
input double InpMinRRFailedBreakout = 0.85;

input int InpTargetTradesPerDayMin = 8;
input int InpTargetTradesPerDayMax = 16;

// --- Penalty (strikes/cooldowns) ---
input int    InpPenaltyCooldownMin   = 20;
input int    InpManagementActionRetryCooldownSec = 30;
input int    InpManagementActionMaxRetries = 20;
input int    InpPenaltyCloseStrikes  = 6;
input int    InpPenaltyCutStrikes    = 3;
input int    InpMinMinutesBeforePenaltyCuts = 20;

input double InpPenaltyMaeTriggerR   = -1.05;
input double InpPenaltyGivebackTrigMfeR = 1.8;
input double InpPenaltyGivebackFloorR   = 0.6;
input int    InpPenaltyStuckMinutes  = 120;
input double InpPenaltyStuckMinMfeR  = 0.35;

input double InpPenaltyDrInvalidCutPct  = 0.35;
input double InpPenaltyFvgInvalidCutPct = 0.15;
input int    InpPenaltyInvalidEpsBps    = 15;
input ENUM_THESIS_INVALIDATION_POLICY InpThesisInvalidationPolicy = THESIS_INVALIDATION_PARTIAL_EXIT;
input ENUM_INVALIDATION_CONFIRMATION_MODE InpInvalidationConfirmationMode = INVALIDATION_CLOSED_M1_BAR;
input int    InpInvalidationPersistenceSeconds = 15;
input double InpInvalidationSpreadBufferMult = 1.0;
input bool   InpInvalidationAssetClassPolicyEnable = false;
input string InpInvalidationAssetClassPolicyFile = "PO3_AI_BUS\\config\\invalidation_policy.v1.json";
input bool   InpClosingVolumeAllowCloseAllBelowMinimum = false;
input int    InpCounterfactualHorizonMinutes = 1440;

// --- Broker symbol sessions / pre-close governance ---
input bool   InpUseBrokerSymbolSessions = true;
input int    InpSymbolNoEntryBeforeCloseMin = 15;
input int    InpSymbolFlattenBeforeCloseMin = 6;
input int    InpSymbolFlattenRetrySeconds = 30;

// --- Shadow candidate research (never trading authority) ---
input bool   InpShadowCandidateLedgerEnable = true;
input int    InpShadowCandidateHorizonMinutes = 1440;

input ENUM_PO3_STOP_MODEL InpStopModel = STOP_STRUCTURAL_SWEEP;

// --- AI gate ---
input bool   InpUseAI                = true;
input bool   InpAiStrict             = true;
input double InpMinAiScoreTrend      = 7.0;
input bool   InpLiveFailClosedOnAIFailure = true;
input double InpFallbackRiskMultiplier = 0.0;
input bool   InpAllowRuleOnlyLive    = false;
input bool   InpAiVetoEnable = true;
input double InpAiMinFollowThroughProb = 0.58;
input double InpAiMaxInvalidationRisk = 0.62;
input double InpAiMaxChopRisk = 0.65;
input double InpAiMaxPostEntryFailureRisk = 0.62;
input double InpAiMinFinalExpectancyScore = 6.80;
input int    InpAiRetryBackoffMin    = 25;
input bool   InpTesterAiCache        = true;
input bool InpUseAdrTargetFloorForMicro = true;
// --- Global kill-switch ---
input bool   InpStopTradingEnable    = false;
input double InpStopAtEquityBelow    = 0.0;
input double InpStopAtBalanceBelow   = 0.0;
input bool InpAllowRuleOnlyFallback = false;
// --- Common folder bus ---
input string InpBusRoot              = "PO3_AI_BUS";

bool PO3PresetMicroIntraday() {
   string preset = InpStrategyPreset;
   StringToLower(preset);
   return (preset == "micro_intraday" || preset == "micro-po3" || preset == "micro_po3");
}

bool StrategyAllowsFullPO3() {
   return (InpStrategyMode == STRATEGY_FULL_PO3 ||
           InpStrategyMode == STRATEGY_MICRO_PO3 ||
           InpStrategyMode == STRATEGY_HYBRID);
}

bool StrategyAllowsScalpContinuation() {
   return (InpStrategyMode == STRATEGY_SCALP_CONTINUATION ||
           InpStrategyMode == STRATEGY_HYBRID);
}

ENUM_TIMEFRAMES PO3EffectiveHTF() {
   return (PO3PresetMicroIntraday() && InpPresetOverridesTimeframes ? PERIOD_M15 : InpHTF);
}

ENUM_TIMEFRAMES PO3EffectiveEntryTF() {
   return (PO3PresetMicroIntraday() && InpPresetOverridesTimeframes ? PERIOD_M1 : InpEntryTF);
}

ENUM_TIMEFRAMES PO3EffectiveConfirmTF() {
   return (PO3PresetMicroIntraday() && InpPresetOverridesTimeframes ? PERIOD_M1 : InpConfirmTF);
}

int PO3EffectiveMaxContextAgeBars() {
   return InpMaxContextAgeBars;
}

int PO3EffectiveMinFvgWidthTicks() {
   return MathMax(1, InpMinFvgWidthTicks);
}

double PO3EffectiveDispBodyATRMin() {
   return InpDispBodyATRMin;
}

double PO3EffectiveDispRangeATRMin() {
   return InpDispRangeATRMin;
}

double PO3EffectiveDispBodyFracMin() {
   return InpDispBodyFracMin;
}

double PO3EffectiveDispRangeVsRecentMult() {
   return InpDispRangeVsRecentMult;
}

double PO3EffectiveDispMinQuality() {
   return InpDispMinQuality;
}

string PO3ScopeLabel(const ENUM_TIMEFRAMES tf) {
   if(tf == PERIOD_M1 || tf == PERIOD_M2 || tf == PERIOD_M3 || tf == PERIOD_M4 || tf == PERIOD_M5)
      return "micro_po3";
   if(tf == PERIOD_M6 || tf == PERIOD_M10 || tf == PERIOD_M12 || tf == PERIOD_M15 ||
      tf == PERIOD_M20 || tf == PERIOD_M30 || tf == PERIOD_H1 || tf == PERIOD_H2)
      return "intraday_po3";
   return "institutional_po3";
}

#endif

