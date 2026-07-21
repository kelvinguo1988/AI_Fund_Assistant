package com.fundquant.app.ui.screens.system

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fundquant.app.data.model.*
import com.fundquant.app.data.repository.FundRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SystemState(
    val loading: Boolean = true,
    val error: String? = null,
    val aiEnabled: Boolean = false,
    val selectedModel: String = "",
    val apiKey: String = "",
    val baseUrl: String = "",
    val presets: List<AIModelPreset> = emptyList(),
    val saving: Boolean = false,
    val testingConnectivity: Boolean = false,
    val connectivityResult: ConnectivityResult? = null
)

@HiltViewModel
class SystemViewModel @Inject constructor(
    private val repository: FundRepository
) : ViewModel() {

    private val _state = MutableStateFlow(SystemState())
    val state: StateFlow<SystemState> = _state.asStateFlow()

    init { loadConfig() }

    fun loadConfig() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true) }
            try {
                val res = repository.getSystemConfig()
                if (res.isSuccess && res.data != null) {
                    val config = res.data!!
                    _state.update {
                        it.copy(
                            loading = false,
                            aiEnabled = config.aiEnabled,
                            selectedModel = config.aiModel ?: "",
                            baseUrl = config.aiBaseUrl ?: "",
                            presets = config.presets
                        )
                    }
                } else {
                    _state.update { it.copy(loading = false, error = res.message) }
                }
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = e.localizedMessage) }
            }
        }
    }

    fun toggleAI(enabled: Boolean) {
        _state.update { it.copy(aiEnabled = enabled) }
    }

    fun selectModel(key: String) {
        val preset = _state.value.presets.find { it.key == key }
        _state.update {
            it.copy(
                selectedModel = key,
                baseUrl = preset?.baseUrl ?: it.baseUrl
            )
        }
    }

    fun updateApiKey(key: String) {
        _state.update { it.copy(apiKey = key) }
    }

    fun updateBaseUrl(url: String) {
        _state.update { it.copy(baseUrl = url) }
    }

    fun saveConfig() {
        viewModelScope.launch {
            _state.update { it.copy(saving = true) }
            try {
                repository.updateSystemConfig(
                    AIConfigUpdate(
                        aiEnabled = _state.value.aiEnabled,
                        aiModel = _state.value.selectedModel,
                        aiApiKey = _state.value.apiKey,
                        aiBaseUrl = _state.value.baseUrl
                    )
                )
            } catch (_: Exception) { }
            _state.update { it.copy(saving = false) }
        }
    }

    fun testConnectivity() {
        viewModelScope.launch {
            _state.update { it.copy(testingConnectivity = true, connectivityResult = null) }
            try {
                val res = repository.testConnectivity()
                if (res.isSuccess && res.data != null) {
                    _state.update { it.copy(testingConnectivity = false, connectivityResult = res.data) }
                }
            } catch (_: Exception) { }
            _state.update { it.copy(testingConnectivity = false) }
        }
    }
}
