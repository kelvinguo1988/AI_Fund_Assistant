package com.fundquant.app.ui.screens.fundpool

import androidx.compose.foundation.clickable
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
import com.fundquant.app.data.model.Fund
import com.fundquant.app.ui.components.*
import com.fundquant.app.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FundPoolScreen(
    viewModel: FundPoolViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsState()
    var showAddDialog by remember { mutableStateOf(false) }
    var showDeleteConfirm by remember { mutableStateOf<Fund?>(null) }
    var showImportDialog by remember { mutableStateOf(false) }
    var editFund by remember { mutableStateOf<Fund?>(null) }

    Column(modifier = Modifier.fillMaxSize()) {
        // 操作栏
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = { showAddDialog = true },
                colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue)
            ) {
                Icon(Icons.Default.Add, null, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(4.dp))
                Text("新增")
            }
            OutlinedButton(onClick = { showImportDialog = true }) {
                Text("导入")
            }
            Spacer(modifier = Modifier.weight(1f))
            IconButton(onClick = { viewModel.loadFunds() }) {
                Icon(Icons.Default.Refresh, "刷新", tint = TextSecondary)
            }
        }

        if (state.loading) {
            LoadingView()
        } else if (state.error != null) {
            ErrorView(message = state.error!!, onRetry = { viewModel.loadFunds() })
        } else if (state.funds.isEmpty()) {
            EmptyView("暂无基金数据")
        } else {
            LazyColumn(
                contentPadding = PaddingValues(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                items(state.funds, key = { it.id }) { fund ->
                    FundCard(
                        fund = fund,
                        onEdit = { editFund = fund },
                        onDelete = { showDeleteConfirm = fund },
                        onToggle = { viewModel.toggleStatus(fund) }
                    )
                }
                item { Spacer(modifier = Modifier.height(80.dp)) }
            }
        }
    }

    // ========== 新增/编辑弹窗 ==========
    if (showAddDialog || editFund != null) {
        FundFormDialog(
            fund = editFund,
            onDismiss = {
                showAddDialog = false
                editFund = null
            },
            onSave = { code, name, type, tags ->
                if (editFund != null) {
                    viewModel.updateFund(editFund!!.id, name, type, tags)
                } else {
                    viewModel.addFund(code, name, type, tags)
                }
                showAddDialog = false
                editFund = null
            }
        )
    }

    // ========== 删除确认 ==========
    showDeleteConfirm?.let { fund ->
        AlertDialog(
            onDismissRequest = { showDeleteConfirm = null },
            title = { Text("确认删除") },
            text = { Text("确定要删除 ${fund.code} ${fund.name} 吗？") },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.deleteFund(fund.id)
                        showDeleteConfirm = null
                    },
                    colors = ButtonDefaults.textButtonColors(contentColor = ColorBuy)
                ) { Text("删除") }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteConfirm = null }) { Text("取消") }
            }
        )
    }
}

@Composable
private fun FundCard(
    fund: Fund,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    onToggle: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = DarkCard),
        shape = RoundedCornerShape(10.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(fund.code, color = TextMuted, fontSize = 12.sp)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(fund.name, color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                }
                Spacer(modifier = Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Surface(
                        shape = RoundedCornerShape(4.dp),
                        color = if (fund.fundType == "etf") ColorBuyBg else PrimaryBlue.copy(alpha = 0.2f)
                    ) {
                        Text(
                            if (fund.fundType == "etf") "ETF" else "场外",
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp),
                            fontSize = 10.sp,
                            color = if (fund.fundType == "etf") ColorBuy else PrimaryBlue
                        )
                    }
                    fund.tags.split(",").filter { it.isNotBlank() }.forEach { tag ->
                        TagChip(tag)
                    }
                }
            }

            // 状态开关
            Switch(
                checked = fund.status == "active",
                onCheckedChange = { onToggle() },
                colors = SwitchDefaults.colors(
                    checkedThumbColor = PrimaryBlue,
                    checkedTrackColor = PrimaryBlue.copy(alpha = 0.3f)
                )
            )

            // 操作按钮
            IconButton(onClick = onEdit, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.Edit, "编辑", tint = TextSecondary, modifier = Modifier.size(16.dp))
            }
            IconButton(onClick = onDelete, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.Delete, "删除", tint = ColorBuy, modifier = Modifier.size(16.dp))
            }
        }
    }
}

// ==================== 基金表单弹窗 ====================

@Composable
private fun FundFormDialog(
    fund: Fund?,
    onDismiss: () -> Unit,
    onSave: (code: String, name: String, type: String, tags: String) -> Unit
) {
    var code by remember { mutableStateOf(fund?.code ?: "") }
    var name by remember { mutableStateOf(fund?.name ?: "") }
    var type by remember { mutableStateOf(fund?.fundType ?: "etf") }
    var tags by remember { mutableStateOf(fund?.tags ?: "") }
    val isEdit = fund != null

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (isEdit) "编辑基金" else "新增基金") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it },
                    label = { Text("基金代码") },
                    singleLine = true,
                    enabled = !isEdit,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("基金名称") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("类型: ", color = TextSecondary, fontSize = 14.sp)
                    FilterChip(
                        selected = type == "etf",
                        onClick = { type = "etf" },
                        label = { Text("ETF") }
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    FilterChip(
                        selected = type == "otc",
                        onClick = { type = "otc" },
                        label = { Text("场外") }
                    )
                }
                OutlinedTextField(
                    value = tags,
                    onValueChange = { tags = it },
                    label = { Text("标签（逗号分隔）") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(code, name, type, tags) },
                enabled = code.isNotBlank() && name.isNotBlank()
            ) { Text("保存") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}
