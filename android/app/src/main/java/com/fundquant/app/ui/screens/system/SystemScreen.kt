package com.fundquant.app.ui.screens.system

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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.fundquant.app.ui.components.*
import com.fundquant.app.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SystemScreen(
    viewModel: SystemViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsState()

    if (state.loading) { LoadingView(); return }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // ========== AI 模型配置 ==========
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(12.dp)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("AI 模型配置", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    Spacer(modifier = Modifier.height(12.dp))

                    // AI 开关
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("AI 功能", color = TextSecondary, fontSize = 14.sp)
                        Switch(
                            checked = state.aiEnabled,
                            onCheckedChange = { viewModel.toggleAI(it) },
                            colors = SwitchDefaults.colors(
                                checkedThumbColor = PrimaryBlue,
                                checkedTrackColor = PrimaryBlue.copy(alpha = 0.3f)
                            )
                        )
                    }

                    Spacer(modifier = Modifier.height(12.dp))

                    // 模型供应商
                    Text("模型供应商", color = TextMuted, fontSize = 11.sp)
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        state.presets.forEach { preset ->
                            FilterChip(
                                selected = state.selectedModel == preset.key,
                                onClick = { viewModel.selectModel(preset.key) },
                                label = { Text(preset.label, fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = PrimaryBlue.copy(alpha = 0.3f),
                                    selectedLabelColor = PrimaryBlue
                                )
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(12.dp))

                    // API Key
                    OutlinedTextField(
                        value = state.apiKey,
                        onValueChange = { viewModel.updateApiKey(it) },
                        label = { Text("API Key") },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedBorderColor = PrimaryBlue,
                            unfocusedBorderColor = DarkDivider,
                            cursorColor = PrimaryBlue
                        )
                    )

                    Spacer(modifier = Modifier.height(10.dp))

                    // Base URL
                    OutlinedTextField(
                        value = state.baseUrl,
                        onValueChange = { viewModel.updateBaseUrl(it) },
                        label = { Text("Base URL") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedBorderColor = PrimaryBlue,
                            unfocusedBorderColor = DarkDivider,
                            cursorColor = PrimaryBlue
                        )
                    )

                    Spacer(modifier = Modifier.height(12.dp))

                    Button(
                        onClick = { viewModel.saveConfig() },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
                        enabled = !state.saving
                    ) { Text("保存配置") }
                }
            }
        }

        // ========== 数据源连通性测试 ==========
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(12.dp)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("数据源连通性", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    Spacer(modifier = Modifier.height(12.dp))

                    Button(
                        onClick = { viewModel.testConnectivity() },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
                        enabled = !state.testingConnectivity
                    ) {
                        if (state.testingConnectivity) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                color = TextPrimary,
                                strokeWidth = 2.dp
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                        }
                        Text(if (state.testingConnectivity) "测试中…" else "开始测试")
                    }

                    // 结果
                    state.connectivityResult?.let { result ->
                        Spacer(modifier = Modifier.height(12.dp))

                        // 总体状态
                        val (statusColor, statusText) = when (result.status) {
                            "ok" -> ColorSell to "全部可达"
                            "partial" -> ColorBuy to "部分可达"
                            else -> ColorBuy to "连接失败"
                        }
                        Surface(
                            shape = RoundedCornerShape(6.dp),
                            color = statusColor.copy(alpha = 0.2f)
                        ) {
                            Text(
                                statusText,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                                color = statusColor,
                                fontSize = 13.sp
                            )
                        }

                        Spacer(modifier = Modifier.height(8.dp))

                        result.results.forEach { item ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 3.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(item.name, color = TextSecondary, fontSize = 12.sp)
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(
                                        if (item.reachable) Icons.Default.CheckCircle else Icons.Default.Error,
                                        null,
                                        tint = if (item.reachable) ColorSell else ColorBuy,
                                        modifier = Modifier.size(14.dp)
                                    )
                                    Spacer(modifier = Modifier.width(4.dp))
                                    Text(
                                        if (item.reachable) "${"%.0f".format(item.latencyMs)}ms" else (item.error ?: "不可达"),
                                        color = if (item.reachable) ColorSell else ColorBuy,
                                        fontSize = 11.sp
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        // ========== 应用信息 ==========
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(12.dp)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("关于", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("基金量化助手 v1.0.0", color = TextSecondary, fontSize = 13.sp)
                    Text("Android 自签名版本", color = TextMuted, fontSize = 11.sp)
                    Text("后端: FastAPI + SQLite + AKShare", color = TextMuted, fontSize = 11.sp)
                }
            }
        }

        item { Spacer(modifier = Modifier.height(16.dp)) }
    }
}
