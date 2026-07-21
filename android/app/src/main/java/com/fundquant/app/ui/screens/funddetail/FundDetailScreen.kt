package com.fundquant.app.ui.screens.funddetail

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.fundquant.app.data.model.*
import com.fundquant.app.ui.components.*
import com.fundquant.app.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FundDetailScreen(
    viewModel: FundDetailViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsState()
    var selectedTab by remember { mutableIntStateOf(0) }

    Column(modifier = Modifier.fillMaxSize()) {
        // 刷新栏
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                if (state.detailStatus?.updatedAt != null)
                    "缓存: ${state.detailStatus!!.updatedAt}"
                else "暂无缓存",
                color = TextMuted,
                fontSize = 12.sp
            )
            Button(
                onClick = { viewModel.refreshDetails() },
                enabled = !state.refreshing,
                colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue)
            ) {
                Icon(Icons.Default.Refresh, null, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(4.dp))
                Text("刷新数据")
            }
        }

        if (state.refreshing) {
            LinearProgressIndicator(
                modifier = Modifier.fillMaxWidth(),
                color = PrimaryBlue
            )
        }

        // Tab 栏
        TabRow(
            selectedTabIndex = selectedTab,
            containerColor = DarkSurface,
            contentColor = PrimaryBlue
        ) {
            listOf("阶段涨幅", "持仓明细", "基金经理", "变更摘要").forEachIndexed { index, title ->
                Tab(
                    selected = selectedTab == index,
                    onClick = { selectedTab = index },
                    text = { Text(title, fontSize = 13.sp) }
                )
            }
        }

        // Tab 内容
        when (selectedTab) {
            0 -> PeriodReturnTab(state.periodReturns, state.loading)
            1 -> HoldingsTab(state.holdings, state.selectedFundId, viewModel, state.loading)
            2 -> ManagerTab(state.managers, state.selectedFundId, viewModel, state.loading)
            3 -> ChangeSummaryTab(state.changeSummaries, state.loading)
        }
    }
}

// ==================== 阶段涨幅 Tab ====================

@Composable
private fun PeriodReturnTab(returns: List<FundPeriodReturn>, loading: Boolean) {
    if (loading) { LoadingView(); return }
    if (returns.isEmpty()) { EmptyView("暂无阶段涨幅数据"); return }

    LazyColumn(
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        items(returns) { fund ->
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(10.dp)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(fund.code, color = TextMuted, fontSize = 11.sp)
                        Text(fund.name, color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ReturnCell("1月", fund.return1m)
                        ReturnCell("3月", fund.return3m)
                        ReturnCell("6月", fund.return6m)
                        ReturnCell("1年", fund.return1y)
                    }
                }
            }
        }
    }
}

@Composable
private fun ReturnCell(label: String, value: String?) {
    val numValue = value?.removeSuffix("%")?.toDoubleOrNull() ?: 0.0
    Column(horizontalAlignment = Alignment.End) {
        Text(label, color = TextMuted, fontSize = 10.sp)
        Text(
            value ?: "-",
            color = when {
                numValue > 0 -> ColorBuy
                numValue < 0 -> ColorSell
                else -> TextSecondary
            },
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium
        )
    }
}

// ==================== 持仓明细 Tab ====================

@Composable
private fun HoldingsTab(
    holdings: List<FundHolding>,
    fundId: Int?,
    viewModel: FundDetailViewModel,
    loading: Boolean
) {
    if (loading) { LoadingView(); return }
    if (holdings.isEmpty()) {
        EmptyView("点击基金池中的基金查看持仓明细\n或等待数据同步")
        return
    }

    LazyColumn(
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        items(holdings) { holding ->
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(10.dp)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(holding.stockCode, color = TextMuted, fontSize = 11.sp)
                        Text(holding.stockName, color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                        if (holding.quarterLabel.isNotBlank()) {
                            Text(holding.quarterLabel, color = TextMuted, fontSize = 10.sp)
                        }
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text("${"%.2f".format(holding.ratio)}%", color = PrimaryBlue, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                        Text("占净值", color = TextMuted, fontSize = 10.sp)
                    }
                }
            }
        }
    }
}

// ==================== 基金经理 Tab ====================

@Composable
private fun ManagerTab(
    managers: List<FundManager>,
    fundId: Int?,
    viewModel: FundDetailViewModel,
    loading: Boolean
) {
    if (loading) { LoadingView(); return }
    if (managers.isEmpty()) {
        EmptyView("点击基金池中的基金查看经理信息\n或等待数据同步")
        return
    }

    LazyColumn(
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        items(managers) { manager ->
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(10.dp)
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(manager.managerName, color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                        Text(manager.company, color = TextMuted, fontSize = 12.sp)
                    }
                    Spacer(modifier = Modifier.height(10.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceEvenly
                    ) {
                        ManagerStat("从业天数", "${manager.tenureDays}天")
                        ManagerStat("管理规模", "${"%.1f".format(manager.assetScale)}亿")
                        ManagerStat("最佳回报", "${"%.1f".format(manager.bestReturn)}%")
                    }
                }
            }
        }
    }
}

@Composable
private fun ManagerStat(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, color = TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        Text(label, color = TextMuted, fontSize = 10.sp)
    }
}

// ==================== 变更摘要 Tab ====================

@Composable
private fun ChangeSummaryTab(
    summaries: List<FundChangeSummary>,
    loading: Boolean
) {
    if (loading) { LoadingView(); return }
    if (summaries.isEmpty()) { EmptyView("暂无变更摘要"); return }

    LazyColumn(
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        items(summaries) { summary ->
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(10.dp)
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(
                        "${summary.fundCode} ${summary.fundName}",
                        color = TextPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )

                    // 持仓变更
                    summary.holdingChanges?.let { hc ->
                        Spacer(modifier = Modifier.height(8.dp))
                        Text("持仓调仓", color = TextSecondary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        Text("${hc.previousQuarter} → ${hc.latestQuarter}", color = TextMuted, fontSize = 10.sp)

                        if (hc.added.isNotEmpty()) {
                            Text("新增 ${hc.added.size} 只", color = ColorBuy, fontSize = 11.sp)
                            hc.added.take(3).forEach { item ->
                                Text("  +${item.stockName} (${"%.1f".format(item.ratio)}%)", color = ColorBuy, fontSize = 11.sp)
                            }
                        }
                        if (hc.removed.isNotEmpty()) {
                            Text("移除 ${hc.removed.size} 只", color = ColorSell, fontSize = 11.sp)
                            hc.removed.take(3).forEach { item ->
                                Text("  -${item.stockName} (${"%.1f".format(item.ratio)}%)", color = ColorSell, fontSize = 11.sp)
                            }
                        }
                    }

                    // 经理变更
                    summary.managerChanges?.let { mc ->
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            "经理${if (mc.changed) "已变更" else "未变更"}",
                            color = if (mc.changed) ColorBuy else ColorSell,
                            fontSize = 12.sp
                        )
                        mc.current.take(2).forEach { m ->
                            Text("  ${m.managerName} @ ${m.company}", color = TextSecondary, fontSize = 11.sp)
                        }
                    }
                }
            }
        }
    }
}
