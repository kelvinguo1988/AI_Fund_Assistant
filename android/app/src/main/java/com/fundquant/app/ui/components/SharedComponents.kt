package com.fundquant.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.fundquant.app.data.model.AnalysisResult
import com.fundquant.app.ui.theme.*

// ==================== 信号灯组件 ====================

@Composable
fun SignalIndicator(
    direction: String,
    strength: String? = null,
    modifier: Modifier = Modifier
) {
    val (color, label) = when (direction) {
        "buy" -> ColorBuy to "买入"
        "sell" -> ColorSell to "卖出"
        else -> ColorHold to "持有"
    }

    val strengthLabel = when (strength) {
        "heavy_buy" -> "强烈买入"
        "moderate_buy" -> "中度买入"
        "light_buy" -> "轻度买入"
        "heavy_sell" -> "强烈卖出"
        "moderate_sell" -> "中度卖出"
        "light_sell" -> "轻度卖出"
        else -> label
    }

    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(10.dp)
                .clip(CircleShape)
                .background(color)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            strengthLabel,
            color = color,
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium
        )
    }
}

// ==================== 信号强度标签 ====================

@Composable
fun SignalStrengthChip(strength: String) {
    val (bg, text, label) = when (strength) {
        "heavy_buy" -> Triple(ColorBuyBg, ColorBuy, "强烈买入")
        "moderate_buy" -> Triple(ColorBuyBg.copy(alpha = 0.15f), ColorBuy, "中度买入")
        "light_buy" -> Triple(ColorBuyBg.copy(alpha = 0.1f), ColorBuy, "轻度买入")
        "heavy_sell" -> Triple(ColorSellBg, ColorSell, "强烈卖出")
        "moderate_sell" -> Triple(ColorSellBg.copy(alpha = 0.15f), ColorSell, "中度卖出")
        "light_sell" -> Triple(ColorSellBg.copy(alpha = 0.1f), ColorSell, "轻度卖出")
        else -> Triple(ColorHoldBg, ColorHold, "持有")
    }

    Surface(
        shape = RoundedCornerShape(6.dp),
        color = bg,
    ) {
        Text(
            label,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
            color = text,
            fontSize = 11.sp,
            fontWeight = FontWeight.Medium
        )
    }
}

// ==================== 统计卡片 ====================

@Composable
fun StatCard(
    title: String,
    value: String,
    subtitle: String? = null,
    valueColor: Color = TextPrimary,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = DarkCard),
        shape = RoundedCornerShape(12.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                title,
                color = TextSecondary,
                fontSize = 12.sp
            )
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                value,
                color = valueColor,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold
            )
            if (subtitle != null) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    subtitle,
                    color = TextMuted,
                    fontSize = 11.sp
                )
            }
        }
    }
}

// ==================== 标签 Chip ====================

@Composable
fun TagChip(label: String, index: Int = label.hashCode()) {
    val color = TagColors[index.absoluteValue % TagColors.size]
    Surface(
        shape = RoundedCornerShape(4.dp),
        color = color.copy(alpha = 0.2f)
    ) {
        Text(
            label.trim(),
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            color = color,
            fontSize = 11.sp,
            fontWeight = FontWeight.Medium
        )
    }
}

// ==================== 加载中 ====================

@Composable
fun LoadingView(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        CircularProgressIndicator(color = PrimaryBlue)
    }
}

// ==================== 错误视图 ====================

@Composable
fun ErrorView(
    message: String,
    onRetry: (() -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("⚠️", fontSize = 40.sp)
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                message,
                color = TextSecondary,
                fontSize = 14.sp,
                textAlign = TextAlign.Center
            )
            if (onRetry != null) {
                Spacer(modifier = Modifier.height(16.dp))
                Button(
                    onClick = onRetry,
                    colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue)
                ) { Text("重试") }
            }
        }
    }
}

// ==================== 空数据 ====================

@Composable
fun EmptyView(message: String = "暂无数据", modifier: Modifier = Modifier) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Text(message, color = TextMuted, fontSize = 14.sp)
    }
}

// ==================== 涨跌百分比 ====================

@Composable
fun PctText(value: Double, modifier: Modifier = Modifier) {
    val color = when {
        value > 0 -> ColorBuy
        value < 0 -> ColorSell
        else -> TextSecondary
    }
    val prefix = if (value > 0) "+" else ""
    Text(
        "$prefix${"%.2f".format(value)}%",
        color = color,
        modifier = modifier,
        fontWeight = FontWeight.Medium
    )
}

// ==================== 金额显示 ====================

@Composable
fun AmountText(amount: Double, unit: String = "亿", modifier: Modifier = Modifier) {
    val color = when {
        amount > 0 -> ColorBuy
        amount < 0 -> ColorSell
        else -> TextSecondary
    }
    val prefix = if (amount > 0) "+" else ""
    Text(
        "$prefix${"%.2f".format(amount)}$unit",
        color = color,
        modifier = modifier,
        fontWeight = FontWeight.Medium
    )
}
