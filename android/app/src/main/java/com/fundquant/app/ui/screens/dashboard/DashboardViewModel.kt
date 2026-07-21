package com.fundquant.app.ui.screens.dashboard

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fundquant.app.data.model.*
import com.fundquant.app.data.repository.FundRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class DashboardState(
    val loading: Boolean = true,
    val error: String? = null,
    val summary: MarketSummary? = null,
    val latestAnalysis: List<AnalysisResult> = emptyList(),
    val funds: List<Fund> = emptyList(),
    val selectedFund: AnalysisResult? = null,
    val refreshing: Boolean = false
)

@HiltViewModel
class DashboardViewModel @Inject constructor(
    private val repository: FundRepository
) : ViewModel() {

    private val _state = MutableStateFlow(DashboardState())
    val state: StateFlow<DashboardState> = _state.asStateFlow()

    init {
        loadData()
    }

    fun loadData() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            try {
                val summaryRes = repository.getMarketSummary()
                val analysisRes = repository.getLatestAnalysis()
                val fundsRes = repository.getFunds("active")

                val summary = if (summaryRes.isSuccess) summaryRes.data else null
                val analysis = if (analysisRes.isSuccess) analysisRes.data ?: emptyList() else emptyList()
                val funds = if (fundsRes.isSuccess) fundsRes.data ?: emptyList() else emptyList()

                _state.update {
                    it.copy(
                        loading = false,
                        summary = summary,
                        latestAnalysis = analysis,
                        funds = funds
                    )
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(loading = false, error = e.localizedMessage ?: "加载失败")
                }
            }
        }
    }

    fun refreshMarket() {
        viewModelScope.launch {
            _state.update { it.copy(refreshing = true) }
            try {
                repository.refreshSummary()
                loadData()
            } catch (_: Exception) {
                _state.update { it.copy(refreshing = false) }
            }
        }
    }

    fun selectFund(result: AnalysisResult) {
        _state.update { it.copy(selectedFund = result) }
    }

    fun triggerAnalysis(fundIds: List<Int>? = null) {
        viewModelScope.launch {
            try {
                repository.triggerAnalysis(TriggerRequest(fundIds))
            } catch (_: Exception) { }
        }
    }
}
