package com.fundquant.app.data.repository

import com.fundquant.app.data.api.FundQuantApi
import com.fundquant.app.data.model.ApiResponse
import com.fundquant.app.data.model.*
import okhttp3.ResponseBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 基金量化系统数据仓库 — 封装所有 API 调用
 */
@Singleton
class FundRepository @Inject constructor(
    private val api: FundQuantApi
) {
    // ========== 健康检查 ==========
    suspend fun healthCheck() = api.healthCheck()

    // ========== 基金 ==========
    suspend fun getFunds(status: String? = null) = api.getFunds(status)
    suspend fun createFund(fund: FundCreate) = api.createFund(fund)
    suspend fun updateFund(id: Int, update: FundUpdate) = api.updateFund(id, update)
    suspend fun deleteFund(id: Int) = api.deleteFund(id)
    suspend fun getFundHoldings(id: Int) = api.getFundHoldings(id)
    suspend fun getFundManager(id: Int) = api.getFundManager(id)
    suspend fun refreshThemes(id: Int) = api.refreshThemes(id)
    suspend fun getFundDetail() = api.getFundDetail()
    suspend fun getFundDetailStatus() = api.getFundDetailStatus()
    suspend fun getExtendedDetail() = api.getExtendedDetail()
    suspend fun getChangeSummary() = api.getChangeSummary()
    suspend fun refreshDetails() = api.refreshDetails()
    suspend fun getRefreshDetailsStatus() = api.getRefreshDetailsStatus()
    suspend fun batchAction(action: BatchAction) = api.batchAction(action)
    suspend fun importFunds(import: FundImport) = api.importFunds(import)

    // ========== 因子 ==========
    suspend fun getFactors() = api.getFactors()
    suspend fun createFactor(factor: FactorCreate) = api.createFactor(factor)
    suspend fun updateFactor(id: Int, update: FactorUpdate) = api.updateFactor(id, update)
    suspend fun deleteFactor(id: Int) = api.deleteFactor(id)
    suspend fun importFactors(overwrite: Boolean, payload: Map<String, Any>) = api.importFactors(overwrite, payload)

    // ========== 分析 ==========
    suspend fun getAnalysis(date: String? = null, fundId: Int? = null) = api.getAnalysis(date, fundId)
    suspend fun getLatestAnalysis() = api.getLatestAnalysis()
    suspend fun getMarketSummary() = api.getMarketSummary()
    suspend fun triggerAnalysis(request: TriggerRequest) = api.triggerAnalysis(request)
    suspend fun refreshSummary() = api.refreshSummary()

    // ========== AI ==========
    suspend fun chat(request: ChatRequest) = api.chat(request)
    suspend fun getConversations(conversationId: String) = api.getConversations(conversationId)

    // ========== 系统 ==========
    suspend fun getSystemConfig() = api.getSystemConfig()
    suspend fun updateSystemConfig(config: AIConfigUpdate) = api.updateSystemConfig(config)
    suspend fun getScoringConfig() = api.getScoringConfig()
    suspend fun updateScoringConfig(config: ScoringConfigUpdate) = api.updateScoringConfig(config)
    suspend fun getQualityConfig() = api.getQualityConfig()
    suspend fun updateQualityConfig(config: QualityConfigUpdate) = api.updateQualityConfig(config)
    suspend fun testConnectivity() = api.testConnectivity()

    // ========== 推送 ==========
    suspend fun getPushChannels() = api.getPushChannels()
    suspend fun createPushChannel(channel: PushChannelCreate) = api.createPushChannel(channel)
    suspend fun updatePushChannel(id: Int, update: PushChannelUpdate) = api.updatePushChannel(id, update)
    suspend fun deletePushChannel(id: Int) = api.deletePushChannel(id)
    suspend fun testPushChannel(id: Int) = api.testPushChannel(id)

    // ========== 调度 ==========
    suspend fun getSchedules() = api.getSchedules()
    suspend fun createSchedule(schedule: ScheduleCreate) = api.createSchedule(schedule)
    suspend fun updateSchedule(id: Int, update: ScheduleUpdate) = api.updateSchedule(id, update)
    suspend fun deleteSchedule(id: Int) = api.deleteSchedule(id)

    // ========== 报告 ==========
    suspend fun getReportConfig() = api.getReportConfig()
    suspend fun updateReportConfig(configs: List<ReportConfigUpdate>) = api.updateReportConfig(configs)

    // ========== 回测 ==========
    suspend fun runBacktest(fundId: Int, period: Int = 365, window: Int = 5) =
        api.runBacktest(fundId, period, window)
}
