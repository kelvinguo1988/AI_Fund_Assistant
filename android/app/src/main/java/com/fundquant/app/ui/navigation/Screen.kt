package com.fundquant.app.ui.navigation

/**
 * 所有页面路由定义
 */
sealed class Screen(val route: String, val title: String, val icon: String) {
    data object Dashboard : Screen("dashboard", "仪表盘", "dashboard")
    data object FundPool : Screen("fund_pool", "基金池", "account_balance")
    data object FundDetail : Screen("fund_detail", "基金详情", "analytics")
    data object Factors : Screen("factors", "因子管理", "tune")
    data object Push : Screen("push", "推送配置", "send")
    data object Report : Screen("report", "报告配置", "description")
    data object Schedule : Screen("schedule", "调度计划", "schedule")
    data object Scoring : Screen("scoring", "评分配置", "score")
    data object Quality : Screen("quality", "质量过滤", "filter_alt")
    data object History : Screen("history", "历史报告", "history")
    data object Backtest : Screen("backtest", "信号回测", "show_chart")
    data object System : Screen("system", "系统设置", "settings")
    data object ServerConfig : Screen("server_config", "服务器配置", "dns")
    data object AIChat : Screen("ai_chat", "AI助手", "smart_toy")

    companion object {
        val allScreens = listOf(
            Dashboard, FundPool, FundDetail, Factors, Push,
            Report, Schedule, Scoring, Quality, History,
            Backtest, System, ServerConfig
        )
    }
}
