package com.fundquant.app.data.model

import com.google.gson.annotations.SerializedName

// ============= 基金 =============

data class Fund(
    val id: Int = 0,
    val code: String = "",
    val name: String = "",
    @SerializedName("fund_type") val fundType: String = "etf",
    val tags: String = "",
    val status: String = "active",
    @SerializedName("created_at") val createdAt: String? = null,
    @SerializedName("updated_at") val updatedAt: String? = null
)

data class FundCreate(
    val code: String,
    val name: String,
    @SerializedName("fund_type") val fundType: String = "etf",
    val tags: String = ""
)

data class FundUpdate(
    val name: String? = null,
    @SerializedName("fund_type") val fundType: String? = null,
    val tags: String? = null,
    val status: String? = null
)

data class BatchAction(
    val ids: List<Int>,
    val action: String  // "active" | "disabled"
)

data class FundImport(
    val items: List<FundImportItem>
)

data class FundImportItem(
    val code: String,
    val name: String,
    val tags: String? = null
)

// ============= 持仓 =============

data class FundHolding(
    @SerializedName("stock_code") val stockCode: String = "",
    @SerializedName("stock_name") val stockName: String = "",
    val ratio: Double = 0.0,        // 占净值比例%
    val shares: Double = 0.0,       // 持股数(万股)
    @SerializedName("market_value") val marketValue: Double = 0.0, // 持仓市值(万元)
    @SerializedName("quarter_label") val quarterLabel: String = ""
)

// ============= 基金经理 =============

data class FundManager(
    @SerializedName("manager_name") val managerName: String = "",
    val company: String = "",
    @SerializedName("tenure_days") val tenureDays: Int = 0,
    @SerializedName("asset_scale") val assetScale: Double = 0.0, // 管理规模(亿)
    @SerializedName("best_return") val bestReturn: Double = 0.0  // 最佳回报%
)

// ============= 阶段涨幅 =============

data class FundPeriodReturn(
    val code: String = "",
    val name: String = "",
    @SerializedName("return_1m") val return1m: String? = null,
    @SerializedName("return_3m") val return3m: String? = null,
    @SerializedName("return_6m") val return6m: String? = null,
    @SerializedName("return_1y") val return1y: String? = null
)

data class FundDetailResponse(
    val funds: List<FundPeriodReturn> = emptyList(),
    @SerializedName("updated_at") val updatedAt: String? = null
)

data class FundDetailStatus(
    @SerializedName("has_cache") val hasCache: Boolean = false,
    @SerializedName("updated_at") val updatedAt: String? = null,
    val refreshing: Boolean = false
)

// ============= 变更摘要 =============

data class FundChangeSummary(
    @SerializedName("fund_id") val fundId: Int = 0,
    @SerializedName("fund_code") val fundCode: String = "",
    @SerializedName("fund_name") val fundName: String = "",
    @SerializedName("holding_changes") val holdingChanges: HoldingChanges? = null,
    @SerializedName("manager_changes") val managerChanges: ManagerChanges? = null,
    val tags: List<String> = emptyList()
)

data class HoldingChanges(
    @SerializedName("latest_quarter") val latestQuarter: String = "",
    @SerializedName("previous_quarter") val previousQuarter: String = "",
    val added: List<HoldingItem> = emptyList(),
    val removed: List<HoldingItem> = emptyList()
)

data class HoldingItem(
    @SerializedName("stock_code") val stockCode: String = "",
    @SerializedName("stock_name") val stockName: String = "",
    val ratio: Double = 0.0
)

data class ManagerChanges(
    val current: List<ManagerChangeInfo> = emptyList(),
    val history: List<ManagerChangeInfo> = emptyList(),
    val changed: Boolean = false
)

data class ManagerChangeInfo(
    @SerializedName("manager_name") val managerName: String = "",
    val company: String = "",
    @SerializedName("tenure_days") val tenureDays: Int = 0
)

// ============= 因子 =============

data class Factor(
    val id: Int = 0,
    val name: String = "",
    val code: String = "",
    val weight: Double = 1.0,
    val direction: String = "positive",
    val formula: String = "",
    val window: Int = 20,
    @SerializedName("window_unit") val windowUnit: String = "day",
    @SerializedName("signal_rules") val signalRules: List<SignalRule>? = null,
    val normalization: String = "none",
    @SerializedName("normalization_config") val normalizationConfig: Map<String, Any>? = null,
    val status: String = "active",
    @SerializedName("sort_order") val sortOrder: Int = 0,
    @SerializedName("weight_percentage") val weightPercentage: Double = 0.0
)

data class SignalRule(
    val condition: String = "",
    val score: Double = 0.0
)

data class FactorCreate(
    val name: String,
    val code: String,
    @SerializedName("data_fields") val dataFields: List<String>? = null,
    val weight: Double = 1.0,
    val direction: String = "positive",
    val formula: String = "",
    val window: Int = 20,
    @SerializedName("window_unit") val windowUnit: String = "day",
    @SerializedName("signal_rules") val signalRules: List<SignalRule>? = null,
    val normalization: String = "none",
    @SerializedName("normalization_config") val normalizationConfig: Map<String, Any>? = null,
    @SerializedName("sort_order") val sortOrder: Int = 0
)

data class FactorUpdate(
    val name: String? = null,
    val weight: Double? = null,
    val direction: String? = null,
    val formula: String? = null,
    val window: Int? = null,
    @SerializedName("window_unit") val windowUnit: String? = null,
    @SerializedName("signal_rules") val signalRules: List<SignalRule>? = null,
    val normalization: String? = null,
    val status: String? = null,
    @SerializedName("sort_order") val sortOrder: Int? = null
)

// ============= 分析结果 =============

data class AnalysisResult(
    val id: Int = 0,
    @SerializedName("fund_id") val fundId: Int = 0,
    @SerializedName("fund_code") val fundCode: String = "",
    @SerializedName("fund_name") val fundName: String = "",
    @SerializedName("analysis_date") val analysisDate: String = "",
    @SerializedName("weighted_score") val weightedScore: Double = 0.0,
    @SerializedName("signal_direction") val signalDirection: String = "hold",
    @SerializedName("signal_strength") val signalStrength: String = "hold",
    @SerializedName("operation_advice") val operationAdvice: String = "",
    @SerializedName("equity_ratio") val equityRatio: Double = 0.5,
    @SerializedName("factor_scores") val factorScores: List<FactorScore> = emptyList(),
    @SerializedName("original_score") val originalScore: Double? = null,
    @SerializedName("dynamic_buy_threshold") val dynamicBuyThreshold: Double? = null,
    @SerializedName("quality_warnings") val qualityWarnings: List<String> = emptyList(),
    @SerializedName("created_at") val createdAt: String? = null
)

data class FactorScore(
    @SerializedName("factor_code") val factorCode: String = "",
    @SerializedName("factor_name") val factorName: String = "",
    @SerializedName("raw_value") val rawValue: Double = 0.0,
    val score: Double = 0.0,
    val direction: String = "positive"
)

// ============= 市场概况 =============

data class MarketSummary(
    val date: String = "",
    val signals: SignalSummary? = null,
    @SerializedName("market_flow") val marketFlow: MarketCapitalFlow? = null,
    @SerializedName("sector_flow") val sectorFlow: List<SectorFlowGroup> = emptyList(),
    @SerializedName("hsgt_flow") val hsgtFlow: HsgtFlow? = null,
    @SerializedName("adv_decline") val advDecline: AdvDecline? = null,
    val turnover: TurnoverData? = null,
    @SerializedName("updated_at") val updatedAt: String? = null
)

data class SignalSummary(
    val total: Int = 0,
    @SerializedName("buy_count") val buyCount: Int = 0,
    @SerializedName("sell_count") val sellCount: Int = 0,
    @SerializedName("hold_count") val holdCount: Int = 0,
    @SerializedName("top_buy") val topBuy: List<AnalysisResult> = emptyList(),
    @SerializedName("top_sell") val topSell: List<AnalysisResult> = emptyList()
)

data class MarketCapitalFlow(
    val date: String = "",
    @SerializedName("sh_index") val shIndex: Double = 0.0,
    @SerializedName("sh_change") val shChange: Double = 0.0,
    @SerializedName("sz_index") val szIndex: Double = 0.0,
    @SerializedName("sz_change") val szChange: Double = 0.0,
    @SerializedName("main_flow") val mainFlow: MainFlow? = null
)

data class MainFlow(
    @SerializedName("net_amount") val netAmount: Double = 0.0,
    @SerializedName("net_ratio") val netRatio: Double = 0.0,
    @SerializedName("super_large_net") val superLargeNet: Double = 0.0,
    @SerializedName("large_net") val largeNet: Double = 0.0,
    @SerializedName("medium_net") val mediumNet: Double = 0.0,
    @SerializedName("small_net") val smallNet: Double = 0.0
)

data class SectorFlowGroup(
    val timeframe: String = "",
    @SerializedName("by_inflow") val byInflow: List<SectorFlowItem> = emptyList(),
    @SerializedName("by_outflow") val byOutflow: List<SectorFlowItem> = emptyList()
)

data class SectorFlowItem(
    @SerializedName("sector_name") val sectorName: String = "",
    @SerializedName("change_pct") val changePct: Double = 0.0,
    @SerializedName("main_net_inflow") val mainNetInflow: Double = 0.0,
    @SerializedName("main_net_ratio") val mainNetRatio: Double = 0.0,
    @SerializedName("top_stock") val topStock: String = ""
)

data class HsgtFlow(
    @SerializedName("north_net_buy") val northNetBuy: Double = 0.0,
    @SerializedName("south_net_buy") val southNetBuy: Double = 0.0,
    val date: String = ""
)

data class AdvDecline(
    @SerializedName("up_count") val upCount: Int = 0,
    @SerializedName("down_count") val downCount: Int = 0,
    @SerializedName("total_count") val totalCount: Int = 0
)

data class TurnoverData(
    @SerializedName("sse_amount") val sseAmount: Double = 0.0,
    @SerializedName("szse_amount") val szseAmount: Double = 0.0,
    @SerializedName("total_amount") val totalAmount: Double = 0.0,
    @SerializedName("prev_total_amount") val prevTotalAmount: Double = 0.0,
    @SerializedName("change_pct") val changePct: Double = 0.0
)

// ============= 推送渠道 =============

data class PushChannel(
    val id: Int = 0,
    val name: String = "",
    @SerializedName("channel_type") val channelType: String = "feishu",
    @SerializedName("webhook_url") val webhookUrl: String? = null,
    val token: String? = null,
    val config: Map<String, Any>? = null,
    val enabled: Boolean = true,
    @SerializedName("created_at") val createdAt: String? = null,
    @SerializedName("updated_at") val updatedAt: String? = null
)

data class PushChannelCreate(
    val name: String,
    @SerializedName("channel_type") val channelType: String,
    @SerializedName("webhook_url") val webhookUrl: String? = null,
    val token: String? = null,
    val config: Map<String, Any>? = null,
    val enabled: Boolean = true
)

data class PushChannelUpdate(
    val name: String? = null,
    @SerializedName("webhook_url") val webhookUrl: String? = null,
    val token: String? = null,
    val config: Map<String, Any>? = null,
    val enabled: Boolean? = null
)

// ============= 调度 =============

data class Schedule(
    val id: Int = 0,
    val name: String = "",
    @SerializedName("cron_expr") val cronExpr: String? = null,
    @SerializedName("time_point") val timePoint: String? = null,
    @SerializedName("task_type") val taskType: String = "analysis_push",
    @SerializedName("channel_id") val channelId: Int? = null,
    val enabled: Boolean = true,
    @SerializedName("last_run_at") val lastRunAt: String? = null,
    @SerializedName("created_at") val createdAt: String? = null,
    @SerializedName("updated_at") val updatedAt: String? = null
)

data class ScheduleCreate(
    val name: String,
    @SerializedName("cron_expr") val cronExpr: String? = null,
    @SerializedName("time_point") val timePoint: String? = null,
    @SerializedName("task_type") val taskType: String = "analysis_push",
    @SerializedName("channel_id") val channelId: Int? = null,
    val enabled: Boolean = true
)

data class ScheduleUpdate(
    val name: String? = null,
    @SerializedName("cron_expr") val cronExpr: String? = null,
    @SerializedName("time_point") val timePoint: String? = null,
    @SerializedName("channel_id") val channelId: Int? = null,
    val enabled: Boolean? = null
)

// ============= 报告配置 =============

data class ReportConfig(
    val id: Int = 0,
    val name: String = "",
    @SerializedName("item_key") val itemKey: String = "",
    val enabled: Boolean = true,
    @SerializedName("sort_order") val sortOrder: Int = 0,
    @SerializedName("created_at") val createdAt: String? = null
)

data class ReportConfigUpdate(
    val id: Int,
    val enabled: Boolean? = null,
    @SerializedName("sort_order") val sortOrder: Int? = null
)

// ============= 评分配置 =============

data class ScoringTier(
    @SerializedName("min_score") val minScore: Double = 0.0,
    val label: String = "",
    @SerializedName("signal_direction") val signalDirection: String = "hold",
    @SerializedName("signal_strength") val signalStrength: String = "hold",
    @SerializedName("operation_advice") val operationAdvice: String = "",
    @SerializedName("equity_ratio") val equityRatio: Double = 0.5
)

data class ScoringConfig(
    @SerializedName("score_range_min") val scoreRangeMin: Double = -6.0,
    @SerializedName("score_range_max") val scoreRangeMax: Double = 6.0,
    val thresholds: List<ScoringTier> = emptyList()
)

data class ScoringConfigUpdate(
    val thresholds: List<ScoringTier>
)

// ============= 质量过滤 =============

data class QualityConfigParam(
    val key: String = "",
    val value: Double = 0.0,
    @SerializedName("default_value") val defaultValue: Double = 0.0,
    val description: String = "",
    val category: String = ""
)

data class QualityConfig(
    val parameters: List<QualityConfigParam> = emptyList(),
    @SerializedName("updated_at") val updatedAt: String? = null
)

data class QualityConfigUpdate(
    val parameters: List<QualityConfigParamUpdate>
)

data class QualityConfigParamUpdate(
    val key: String,
    val value: Double
)

// ============= 系统/AI 配置 =============

data class AIConfig(
    @SerializedName("ai_enabled") val aiEnabled: Boolean = false,
    @SerializedName("ai_model") val aiModel: String? = null,
    @SerializedName("ai_base_url") val aiBaseUrl: String? = null,
    val presets: List<AIModelPreset> = emptyList()
)

data class AIModelPreset(
    val key: String = "",
    val label: String = "",
    @SerializedName("base_url") val baseUrl: String = "",
    @SerializedName("model_name") val modelName: String = ""
)

data class AIConfigUpdate(
    @SerializedName("ai_enabled") val aiEnabled: Boolean? = null,
    @SerializedName("ai_model") val aiModel: String? = null,
    @SerializedName("ai_api_key") val aiApiKey: String? = null,
    @SerializedName("ai_base_url") val aiBaseUrl: String? = null
)

// ============= 连通性 =============

data class ConnectivityResult(
    val status: String = "",
    val results: List<ConnectivityItem> = emptyList(),
    val summary: ConnectivitySummary? = null
)

data class ConnectivityItem(
    val name: String = "",
    val reachable: Boolean = false,
    @SerializedName("latency_ms") val latencyMs: Double = 0.0,
    val error: String? = null
)

data class ConnectivitySummary(
    val total: Int = 0,
    val reachable: Int = 0,
    val unreachable: Int = 0
)

// ============= AI 对话 =============

data class ChatRequest(
    val content: String,
    @SerializedName("conversation_id") val conversationId: String? = null,
    @SerializedName("context_type") val contextType: String? = null,
    @SerializedName("fund_id") val fundId: Int? = null
)

data class ChatResponse(
    @SerializedName("conversation_id") val conversationId: String = "",
    val role: String = "assistant",
    val content: String = "",
    @SerializedName("model_name") val modelName: String? = null
)

data class ConversationMessage(
    val id: Int = 0,
    @SerializedName("conversation_id") val conversationId: String = "",
    val role: String = "",
    val content: String = "",
    @SerializedName("context_type") val contextType: String? = null,
    @SerializedName("fund_id") val fundId: Int? = null,
    @SerializedName("model_name") val modelName: String? = null,
    @SerializedName("created_at") val createdAt: String? = null
)

// ============= 回测 =============

data class BacktestSummary(
    @SerializedName("fund_code") val fundCode: String = "",
    @SerializedName("fund_name") val fundName: String = "",
    val period: Int = 0,
    @SerializedName("total_nav_return") val totalNavReturn: Double = 0.0,
    @SerializedName("total_strategy_return") val totalStrategyReturn: Double = 0.0,
    @SerializedName("excess_return") val excessReturn: Double = 0.0,
    @SerializedName("max_drawdown") val maxDrawdown: Double = 0.0,
    @SerializedName("signal_count") val signalCount: Int = 0,
    @SerializedName("total_days") val totalDays: Int = 0,
    @SerializedName("effectiveness_window") val effectivenessWindow: Int = 5,
    @SerializedName("avg_effectiveness") val avgEffectiveness: Double? = null,
    @SerializedName("buy_effectiveness") val buyEffectiveness: Double? = null,
    @SerializedName("sell_effectiveness") val sellEffectiveness: Double? = null,
    @SerializedName("effectiveness_rate") val effectivenessRate: Double? = null,
    val points: List<BacktestPoint> = emptyList()
)

data class BacktestPoint(
    val date: String = "",
    val nav: Double = 0.0,
    @SerializedName("nav_return") val navReturn: Double = 0.0,
    @SerializedName("strategy_return") val strategyReturn: Double = 0.0,
    @SerializedName("signal_direction") val signalDirection: String? = null,
    @SerializedName("signal_strength") val signalStrength: String? = null,
    @SerializedName("weighted_score") val weightedScore: Double? = null,
    @SerializedName("signal_effectiveness") val signalEffectiveness: Double? = null
)

// ============= 通用 =============

data class TriggerRequest(
    @SerializedName("fund_ids") val fundIds: List<Int>? = null
)

data class RefreshStatus(
    val status: String = "",
    val total: Int = 0,
    val done: Int = 0,
    @SerializedName("accepted") val accepted: Boolean? = null,
    @SerializedName("already_running") val alreadyRunning: Boolean? = null
)
