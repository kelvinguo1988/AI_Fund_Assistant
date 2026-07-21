package com.fundquant.app.data.api

import com.fundquant.app.data.model.*
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.*

interface FundQuantApi {

    // ==================== 健康检查 ====================

    @GET("/health")
    suspend fun healthCheck(): Response<Map<String, String>>

    // ==================== 基金管理 ====================

    @GET("funds")
    suspend fun getFunds(@Query("status") status: String? = null): ApiResponse<List<Fund>>

    @POST("funds")
    suspend fun createFund(@Body fund: FundCreate): ApiResponse<Fund>

    @PUT("funds/{id}")
    suspend fun updateFund(@Path("id") id: Int, @Body update: FundUpdate): ApiResponse<Fund>

    @DELETE("funds/{id}")
    suspend fun deleteFund(@Path("id") id: Int): ApiResponse<Unit>

    @GET("funds/export")
    suspend fun exportFunds(): ResponseBody

    @POST("funds/import")
    suspend fun importFunds(@Body import: FundImport): ApiResponse<Map<String, Any>>

    @PATCH("funds/batch")
    suspend fun batchAction(@Body action: BatchAction): ApiResponse<Unit>

    @GET("funds/{id}/holdings")
    suspend fun getFundHoldings(@Path("id") id: Int): ApiResponse<List<FundHolding>>

    @GET("funds/{id}/manager")
    suspend fun getFundManager(@Path("id") id: Int): ApiResponse<List<FundManager>>

    @POST("funds/{id}/refresh-themes")
    suspend fun refreshThemes(@Path("id") id: Int): ApiResponse<Fund>

    @GET("funds/detail")
    suspend fun getFundDetail(): ApiResponse<FundDetailResponse>

    @GET("funds/detail/status")
    suspend fun getFundDetailStatus(): ApiResponse<FundDetailStatus>

    @GET("funds/extended-detail")
    suspend fun getExtendedDetail(): ApiResponse<Map<String, Any>>

    @GET("funds/change-summary")
    suspend fun getChangeSummary(): ApiResponse<List<FundChangeSummary>>

    @POST("funds/refresh-details")
    suspend fun refreshDetails(): ApiResponse<RefreshStatus>

    @GET("funds/refresh-details/status")
    suspend fun getRefreshDetailsStatus(): ApiResponse<RefreshStatus>

    // ==================== 因子管理 ====================

    @GET("factors")
    suspend fun getFactors(): ApiResponse<List<Factor>>

    @POST("factors")
    suspend fun createFactor(@Body factor: FactorCreate): ApiResponse<Factor>

    @PUT("factors/{id}")
    suspend fun updateFactor(@Path("id") id: Int, @Body update: FactorUpdate): ApiResponse<Factor>

    @DELETE("factors/{id}")
    suspend fun deleteFactor(@Path("id") id: Int): ApiResponse<Unit>

    @GET("factors/export")
    suspend fun exportFactors(): ResponseBody

    @POST("factors/import")
    suspend fun importFactors(
        @Query("overwrite") overwrite: Boolean = false,
        @Body payload: Map<String, Any>
    ): ApiResponse<Map<String, Any>>

    // ==================== 分析结果 ====================

    @GET("analysis")
    suspend fun getAnalysis(
        @Query("date") date: String? = null,
        @Query("fund_id") fundId: Int? = null
    ): ApiResponse<List<AnalysisResult>>

    @GET("analysis/latest")
    suspend fun getLatestAnalysis(): ApiResponse<List<AnalysisResult>>

    @GET("analysis/summary")
    suspend fun getMarketSummary(): ApiResponse<MarketSummary>

    @POST("analysis/trigger")
    suspend fun triggerAnalysis(@Body request: TriggerRequest): ApiResponse<List<AnalysisResult>>

    @POST("analysis/refresh-summary")
    suspend fun refreshSummary(): ApiResponse<Map<String, String>>

    @GET("analysis/export")
    suspend fun exportAnalysis(): ResponseBody

    @POST("analysis/import")
    suspend fun importAnalysis(
        @Query("overwrite") overwrite: Boolean = false,
        @Body payload: Map<String, Any>
    ): ApiResponse<Map<String, Any>>

    // ==================== AI 对话 ====================

    @POST("ai/chat")
    suspend fun chat(@Body request: ChatRequest): ApiResponse<ChatResponse>

    @GET("ai/conversations")
    suspend fun getConversations(@Query("conversation_id") conversationId: String): ApiResponse<List<ConversationMessage>>

    // ==================== 系统配置 ====================

    @GET("system")
    suspend fun getSystemConfig(): ApiResponse<AIConfig>

    @PUT("system")
    suspend fun updateSystemConfig(@Body config: AIConfigUpdate): ApiResponse<AIConfig>

    @GET("system/scoring-config")
    suspend fun getScoringConfig(): ApiResponse<ScoringConfig>

    @PUT("system/scoring-config")
    suspend fun updateScoringConfig(@Body config: ScoringConfigUpdate): ApiResponse<ScoringConfig>

    @GET("system/quality-config")
    suspend fun getQualityConfig(): ApiResponse<QualityConfig>

    @PUT("system/quality-config")
    suspend fun updateQualityConfig(@Body config: QualityConfigUpdate): ApiResponse<QualityConfig>

    @GET("system/connectivity")
    suspend fun testConnectivity(): ApiResponse<ConnectivityResult>

    // ==================== 推送渠道 ====================

    @GET("push-channels")
    suspend fun getPushChannels(): ApiResponse<List<PushChannel>>

    @POST("push-channels")
    suspend fun createPushChannel(@Body channel: PushChannelCreate): ApiResponse<PushChannel>

    @PUT("push-channels/{id}")
    suspend fun updatePushChannel(@Path("id") id: Int, @Body update: PushChannelUpdate): ApiResponse<PushChannel>

    @DELETE("push-channels/{id}")
    suspend fun deletePushChannel(@Path("id") id: Int): ApiResponse<Unit>

    @POST("push-channels/{id}/test")
    suspend fun testPushChannel(@Path("id") id: Int): ApiResponse<Unit>

    // ==================== 调度计划 ====================

    @GET("schedules")
    suspend fun getSchedules(): ApiResponse<List<Schedule>>

    @POST("schedules")
    suspend fun createSchedule(@Body schedule: ScheduleCreate): ApiResponse<Schedule>

    @PUT("schedules/{id}")
    suspend fun updateSchedule(@Path("id") id: Int, @Body update: ScheduleUpdate): ApiResponse<Schedule>

    @DELETE("schedules/{id}")
    suspend fun deleteSchedule(@Path("id") id: Int): ApiResponse<Unit>

    // ==================== 报告配置 ====================

    @GET("report-config")
    suspend fun getReportConfig(): ApiResponse<List<ReportConfig>>

    @PUT("report-config")
    suspend fun updateReportConfig(@Body configs: List<ReportConfigUpdate>): ApiResponse<List<ReportConfig>>

    // ==================== 回测 ====================

    @GET("backtest/{fundId}")
    suspend fun runBacktest(
        @Path("fundId") fundId: Int,
        @Query("period") period: Int = 365,
        @Query("effectiveness_window") effectivenessWindow: Int = 5
    ): ApiResponse<BacktestSummary>
}
