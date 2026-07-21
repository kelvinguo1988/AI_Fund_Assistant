package com.fundquant.app.ui.screens.settings

import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.fundquant.app.data.local.ServerConfig
import com.fundquant.app.ui.components.LoadingView
import com.fundquant.app.ui.theme.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerConfigScreen(
    viewModel: ServerConfigViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsState()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // 标题区
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = DarkCard),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    "服务器配置",
                    style = MaterialTheme.typography.titleLarge,
                    color = TextPrimary
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    "设置后端 API 服务器地址。支持局域网 IP 或域名。",
                    color = TextSecondary,
                    fontSize = 13.sp
                )
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        // URL 输入区
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = DarkCard),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    "服务器地址",
                    color = TextSecondary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium
                )
                Spacer(modifier = Modifier.height(8.dp))

                OutlinedTextField(
                    value = state.url,
                    onValueChange = { viewModel.updateUrl(it) },
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = {
                        Text(
                            "http://192.168.1.100:8000/api",
                            color = TextMuted
                        )
                    },
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                        focusedBorderColor = PrimaryBlue,
                        unfocusedBorderColor = DarkDivider,
                        cursorColor = PrimaryBlue
                    ),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Go
                    ),
                    keyboardActions = KeyboardActions(
                        onGo = { viewModel.testConnection() }
                    ),
                    shape = RoundedCornerShape(8.dp)
                )

                Spacer(modifier = Modifier.height(4.dp))

                // 当前使用的地址
                Text(
                    "当前: ${state.currentBaseUrl}",
                    color = TextMuted,
                    fontSize = 11.sp
                )
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        // 快捷预设
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = DarkCard),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    "快捷地址",
                    color = TextSecondary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium
                )
                Spacer(modifier = Modifier.height(8.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    val presets = listOf(
                        "模拟器" to "http://10.0.2.2:8000/api",
                        "本地" to "http://localhost:8000/api",
                        "局域网" to "http://192.168.1.100:8000/api"
                    )
                    presets.forEach { (label, url) ->
                        FilterChip(
                            selected = state.url == url,
                            onClick = { viewModel.updateUrl(url) },
                            label = { Text(label, fontSize = 12.sp) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = PrimaryBlue.copy(alpha = 0.3f),
                                selectedLabelColor = PrimaryBlue
                            )
                        )
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        // 连接状态
        if (state.testing) {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(12.dp)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = PrimaryBlue,
                        strokeWidth = 2.dp
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    Text("正在测试连接…", color = TextSecondary, fontSize = 14.sp)
                }
            }
        }

        if (state.connectionResult != null) {
            val (success, message) = state.connectionResult!!
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = if (success) ColorSellBg else ColorBuyBg
                ),
                shape = RoundedCornerShape(12.dp)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        if (success) Icons.Default.CheckCircle else Icons.Default.Error,
                        contentDescription = null,
                        tint = if (success) ColorSell else ColorBuy,
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    Text(
                        message,
                        color = if (success) ColorSell else ColorBuy,
                        fontSize = 14.sp
                    )
                }
            }
        }

        Spacer(modifier = Modifier.weight(1f))

        // 底部按钮
        Button(
            onClick = { viewModel.testConnection() },
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
            shape = RoundedCornerShape(8.dp),
            enabled = state.url.isNotBlank() && !state.testing
        ) {
            Text("测试连接", fontSize = 16.sp)
        }

        Spacer(modifier = Modifier.height(12.dp))

        OutlinedButton(
            onClick = {
                viewModel.saveUrl()
                Toast.makeText(context, "服务器地址已保存", Toast.LENGTH_SHORT).show()
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp),
            enabled = state.url.isNotBlank()
        ) {
            Text("保存并应用", fontSize = 16.sp, color = PrimaryBlue)
        }
    }
}
