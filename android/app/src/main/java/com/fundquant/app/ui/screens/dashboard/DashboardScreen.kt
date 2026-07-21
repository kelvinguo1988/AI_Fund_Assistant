package com.fundquant.app.ui.screens.dashboard

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
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
fun DashboardScreen(
    viewModel: DashboardViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsState()

    if (state.loading) {
        LoadingView()
        return
    }

    if (state.error != null && state.summary == null) {
        ErrorView(message = state.error!!, onRetry = { viewModel.loadData() })
        return
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // ========== 操作栏 ==========
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Button(
                    onClick = { viewModel.refreshMarket() },
                    enabled = !state.refreshing,
                    colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Refresh, null, modifier = Modifier.size(18.dp))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("刷新行情")
                }
                OutlinedButton(
                    onClick = { viewModel.triggerAnalysis() },
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.PlayArrow, null, modifier = Modifier.size(18.dp))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("触发分析")
                }
            }
        }

        // ========== 信号概览 ==========
        state.summary?.signals?.let { signals ->
            item {
                Text("信号概览", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            }
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    StatCard(
                        title = "买入信号",
                        value = "${signals.buyCount}",
                        valueColor = ColorBuy,
                        modifier = Modifier.weight(1f)
                    )
                    StatCard(
                        title = "持有/观望",
                        value = "${signals.holdCount}",
                        valueColor = ColorHold,
                        modifier = Modifier.weight(1f)
                    )
                    StatCard(
                        title = "卖出信号",
                        value = "${signals.sellCount}",
                        valueColor = ColorSell,
                        modifier = Modifier.weight(1f)
                    )
                }
            }
        }

        // ========== 涨跌分布 & 成交额 ==========
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                state.summary?.advDecline?.let { ad ->
                    val upPct = if (ad.totalCount > 0) ad.upCount * 100.0 / ad.totalCount else 0.0
                    Card(
                        modifier = Modifier.weight(1f),
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(12.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(14.dp),
                            horizontalAlignment = Alignment.CenterHorizontally
                        ) {
                            Text("涨跌分布", color = TextSecondary, fontSize = 11.sp)
                            Spacer(modifier = Modifier.height(6.dp))
                            Row(verticalAlignment = Alignment.Bottom) {
                                Text("${ad.upCount}", color = ColorBuy, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                                Text("/${ad.downCount}", color = ColorSell, fontSize = 16.sp)
                            }
                            Text("上涨 ${"%.1f".format(upPct)}%", color = ColorBuy, fontSize = 11.sp)
                        }
                    }
                }

                state.summary?.turnover?.let { t ->
                    Card(
                        modifier = Modifier.weight(1f),
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(12.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(14.dp),
                            horizontalAlignment = Alignment.CenterHorizontally
                        ) {
                            Text("两市成交额", color = TextSecondary, fontSize = 11.sp)
                            Spacer(modifier = Modifier.height(6.dp))
                            Text(
                                "${"%.0f".format(t.totalAmount)}亿",
                                color = TextPrimary,
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold
                            )
                            PctText(t.changePct, modifier = Modifier)
                        }
                    }
                }
            }
        }

        // ========== 大盘资金流 ==========
        state.summary?.marketFlow?.let { flow ->
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkCard),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text("大盘指数", color = TextSecondary, fontSize = 12.sp)
                            flow.mainFlow?.let { mf ->
                                Text(
                                    "主力净流入: ${"%.2f".format(mf.netAmount)}亿",
                                    color = if (mf.netAmount >= 0) ColorBuy else ColorSell,
                                    fontSize = 12.sp
                                )
                            }
                        }
                        Spacer(modifier = Modifier.height(8.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Column {
                                Text("上证", color = TextMuted, fontSize = 11.sp)
                                Text("${flow.shIndex}", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                                PctText(flow.shChange)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text("深证", color = TextMuted, fontSize = 11.sp)
                                Text("${flow.szIndex}", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                                PctText(flow.szChange)
                            }
                        }
                        flow.mainFlow?.let { mf ->
                            Spacer(modifier = Modifier.height(10.dp))
                            Divider(color = DarkDivider)
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceEvenly
                            ) {
                                FlowItem("超大单", mf.superLargeNet)
                                FlowItem("大单", mf.largeNet)
                                FlowItem("中单", mf.mediumNet)
                                FlowItem("小单", mf.smallNet)
                            }
                        }
                    }
                }
            }
        }

        // ========== 沪深港通 ==========
        state.summary?.hsgtFlow?.let { hsgt ->
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkCard),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(14.dp),
                        horizontalArrangement = Arrangement.SpaceEvenly
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("北向资金", color = TextSecondary, fontSize = 11.sp)
                            AmountText(hsgt.northNetBuy)
                        }
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("南向资金", color = TextSecondary, fontSize = 11.sp)
                            AmountText(hsgt.southNetBuy)
                        }
                    }
                }
            }
        }

        // ========== 行业板块资金流 ==========
        state.summary?.sectorFlow?.firstOrNull()?.let { sectorGroup ->
            item {
                Text("行业板块资金流", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            }
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkCard),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        // 资金流入 TOP
                        Text("主力流入 TOP5", color = ColorBuy, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        Spacer(modifier = Modifier.height(6.dp))
                        sectorGroup.byInflow.take(5).forEach { item ->
                            SectorFlowRow(item, true)
                        }

                        Spacer(modifier = Modifier.height(10.dp))
                        HorizontalDivider(color = DarkDivider)
                        Spacer(modifier = Modifier.height(10.dp))

                        // 资金流出 TOP
                        Text("主力流出 TOP5", color = ColorSell, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        Spacer(modifier = Modifier.height(6.dp))
                        sectorGroup.byOutflow.take(5).forEach { item ->
                            SectorFlowRow(item, false)
                        }
                    }
                }
            }
        }

        // ========== 基金分析列表 ==========
        item {
            Text("基金分析", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
        }

        if (state.latestAnalysis.isNotEmpty()) {
            items(state.latestAnalysis) { result ->
                FundAnalysisCard(
                    result = result,
                    selected = state.selectedFund?.id == result.id,
                    onClick = { viewModel.selectFund(result) }
                )
            }
        } else {
            item { EmptyView("暂无分析数据") }
        }

        // ========== 选中基金详情 ==========
        state.selectedFund?.let { fund ->
            item {
                FundDetailCard(fund)
            }
        }

        // 底部留白
        item { Spacer(modifier = Modifier.height(16.dp)) }
    }
}

// ==================== 子组件 ====================

@Composable
private fun FlowItem(label: String, amount: Double) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, color = TextMuted, fontSize = 10.sp)
        Spacer(modifier = Modifier.height(2.dp))
        AmountText(amount, unit = "", modifier = Modifier)
    }
}

@Composable
private fun SectorFlowRow(item: SectorFlowItem, isInflow: Boolean) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(item.sectorName, color = TextPrimary, fontSize = 13.sp)
            Spacer(modifier = Modifier.width(6.dp))
            PctText(item.changePct)
        }
        Column(horizontalAlignment = Alignment.End) {
            AmountText(item.mainNetInflow, unit = "亿")
            Text("领涨: ${item.topStock}", color = TextMuted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun FundAnalysisCard(
    result: AnalysisResult,
    selected: Boolean,
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = if (selected) DarkCard.copy(alpha = 0.8f) else DarkCard
        ),
        shape = RoundedCornerShape(10.dp),
        border = if (selected) BorderStroke(1.dp, PrimaryBlue) else null
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(result.fundCode, color = TextMuted, fontSize = 12.sp)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(result.fundName, color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                }
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    "${"%.1f".format(result.weightedScore)}",
                    color = when {
                        result.weightedScore > 0 -> ColorBuy
                        result.weightedScore < 0 -> ColorSell
                        else -> ColorHold
                    },
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold
                )
                SignalStrengthChip(result.signalStrength)
            }
        }
    }
}

@Composable
private fun FundDetailCard(fund: AnalysisResult) {
    Card(
        colors = CardDefaults.cardColors(containerColor = DarkCard),
        shape = RoundedCornerShape(12.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                "${fund.fundCode} ${fund.fundName}",
                color = TextPrimary,
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold
            )

            Spacer(modifier = Modifier.height(12.dp))

            // 评分
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("综合评分: ", color = TextSecondary, fontSize = 13.sp)
                Text(
                    "${"%.1f".format(fund.weightedScore)}",
                    color = when {
                        fund.weightedScore > 0 -> ColorBuy
                        fund.weightedScore < 0 -> ColorSell
                        else -> ColorHold
                    },
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.width(12.dp))
                SignalStrengthChip(fund.signalStrength)
            }

            // 操作建议
            Spacer(modifier = Modifier.height(10.dp))
            Text(
                fund.operationAdvice,
                color = TextSecondary,
                fontSize = 13.sp,
                lineHeight = 18.sp
            )

            // 因子评分
            if (fund.factorScores.isNotEmpty()) {
                Spacer(modifier = Modifier.height(10.dp))
                HorizontalDivider(color = DarkDivider)
                Spacer(modifier = Modifier.height(8.dp))
                Text("因子评分", color = TextMuted, fontSize = 11.sp)
                Spacer(modifier = Modifier.height(4.dp))

                fund.factorScores.forEach { fs ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 2.dp),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(fs.factorName, color = TextSecondary, fontSize = 12.sp)
                        Text(
                            "${"%.2f".format(fs.score)}",
                            color = if (fs.score > 0) ColorBuy else if (fs.score < 0) ColorSell else ColorHold,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium
                        )
                    }
                }
            }
        }
    }
}
