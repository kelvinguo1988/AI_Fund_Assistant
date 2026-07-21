package com.fundquant.app.ui.screens.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fundquant.app.data.local.ServerConfig
import com.fundquant.app.data.local.ServerConfigManager
import com.fundquant.app.data.repository.FundRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class ServerConfigState(
    val url: String = ServerConfig.DEFAULT_BASE_URL,
    val currentBaseUrl: String = ServerConfig.DEFAULT_BASE_URL,
    val testing: Boolean = false,
    val connectionResult: Pair<Boolean, String>? = null // (success, message)
)

@HiltViewModel
class ServerConfigViewModel @Inject constructor(
    private val configManager: ServerConfigManager,
    private val repository: FundRepository
) : ViewModel() {

    private val _state = MutableStateFlow(ServerConfigState())
    val state: StateFlow<ServerConfigState> = _state.asStateFlow()

    init {
        // 加载已保存的配置
        viewModelScope.launch {
            configManager.configFlow.collect { config ->
                _state.update {
                    it.copy(
                        url = config.baseUrl,
                        currentBaseUrl = config.baseUrl
                    )
                }
            }
        }
    }

    fun updateUrl(url: String) {
        _state.update { it.copy(url = url, connectionResult = null) }
    }

    fun testConnection() {
        val url = _state.value.url
        if (url.isBlank()) return

        _state.update { it.copy(testing = true, connectionResult = null) }

        viewModelScope.launch {
            try {
                // 临时保存 URL 以便测试
                configManager.saveBaseUrl(url)

                // 短暂延迟让 Retrofit 重建连接
                kotlinx.coroutines.delay(500)

                val response = repository.healthCheck()
                if (response.isSuccessful) {
                    _state.update {
                        it.copy(
                            testing = false,
                            connectionResult = true to "连接成功 ✅ — 服务器响应正常"
                        )
                    }
                } else {
                    _state.update {
                        it.copy(
                            testing = false,
                            connectionResult = false to "服务器返回错误: ${response.code()}"
                        )
                    }
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(
                        testing = false,
                        connectionResult = false to "连接失败 ❌ — ${e.localizedMessage ?: "未知错误"}"
                    )
                }
            }
        }
    }

    fun saveUrl() {
        viewModelScope.launch {
            configManager.saveBaseUrl(_state.value.url)
        }
    }
}
