package com.fundquant.app.ui.screens.factors

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
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
import com.fundquant.app.data.model.Factor
import com.fundquant.app.ui.components.*
import com.fundquant.app.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FactorScreen(
    viewModel: FactorViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsState()

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Spacer(modifier = Modifier.weight(1f))
            IconButton(onClick = { viewModel.loadFactors() }) {
                Icon(Icons.Default.Refresh, "刷新", tint = TextSecondary)
            }
        }

        if (state.loading) { LoadingView() }
        else if (state.error != null) { ErrorView(message = state.error!!, onRetry = { viewModel.loadFactors() }) }
        else if (state.factors.isEmpty()) { EmptyView("暂无因子数据") }
        else {
            LazyColumn(
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(state.factors) { factor ->
                    FactorCard(factor) { newWeight ->
                        viewModel.updateWeight(factor.id, newWeight)
                    }
                }
                item { Spacer(modifier = Modifier.height(80.dp)) }
            }
        }
    }
}

@Composable
private fun FactorCard(factor: Factor, onWeightChange: (Double) -> Unit) {
    var sliderValue by remember(factor.id) { mutableFloatStateOf(factor.weight.toFloat()) }

    Card(
        colors = CardDefaults.cardColors(containerColor = DarkCard),
        shape = RoundedCornerShape(10.dp)
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(factor.name, color = TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                    Text(factor.code, color = TextMuted, fontSize = 11.sp)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "权重: ${"%.1f".format(factor.weight)}",
                        color = PrimaryBlue,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        "${"%.0f".format(factor.weightPercentage)}%",
                        color = TextMuted,
                        fontSize = 12.sp
                    )
                }
            }

            Spacer(modifier = Modifier.height(6.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Surface(
                    shape = RoundedCornerShape(4.dp),
                    color = if (factor.direction == "positive") ColorBuyBg else ColorSellBg
                ) {
                    Text(
                        if (factor.direction == "positive") "正向" else "反向",
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        fontSize = 10.sp,
                        color = if (factor.direction == "positive") ColorBuy else ColorSell
                    )
                }
                Surface(
                    shape = RoundedCornerShape(4.dp),
                    color = ColorHoldBg
                ) {
                    Text(
                        factor.normalization.ifBlank { "无标准化" },
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        fontSize = 10.sp,
                        color = ColorHold
                    )
                }
                Surface(
                    shape = RoundedCornerShape(4.dp),
                    color = if (factor.status == "active") ColorSellBg else ColorHoldBg
                ) {
                    Text(
                        if (factor.status == "active") "启用" else "停用",
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        fontSize = 10.sp,
                        color = if (factor.status == "active") ColorSell else ColorHold
                    )
                }
            }

            // 权重滑块
            Spacer(modifier = Modifier.height(8.dp))
            Slider(
                value = sliderValue,
                onValueChange = { sliderValue = it },
                onValueChangeFinished = { onWeightChange(sliderValue.toDouble()) },
                valueRange = 0f..3f,
                colors = SliderDefaults.colors(
                    thumbColor = PrimaryBlue,
                    activeTrackColor = PrimaryBlue
                )
            )
        }
    }
}
