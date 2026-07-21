package com.fundquant.app.ui.screens.funddetail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fundquant.app.data.model.*
import com.fundquant.app.data.repository.FundRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class FundDetailState(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val error: String? = null,
    val detailStatus: FundDetailStatus? = null,
    val periodReturns: List<FundPeriodReturn> = emptyList(),
    val holdings: List<FundHolding> = emptyList(),
    val managers: List<FundManager> = emptyList(),
    val changeSummaries: List<FundChangeSummary> = emptyList(),
    val selectedFundId: Int? = null
)

@HiltViewModel
class FundDetailViewModel @Inject constructor(
    private val repository: FundRepository
) : ViewModel() {

    private val _state = MutableStateFlow(FundDetailState())
    val state: StateFlow<FundDetailState> = _state.asStateFlow()

    init { loadData() }

    fun loadData() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true) }
            try {
                // 并行加载
                val detailRes = repository.getFundDetail()
                val changeRes = repository.getChangeSummary()
                val statusRes = repository.getFundDetailStatus()

                _state.update {
                    it.copy(
                        loading = false,
                        detailStatus = if (statusRes.isSuccess) statusRes.data else null,
                        periodReturns = if (detailRes.isSuccess) detailRes.data?.funds ?: emptyList() else emptyList(),
                        changeSummaries = if (changeRes.isSuccess) changeRes.data ?: emptyList() else emptyList()
                    )
                }
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = e.localizedMessage) }
            }
        }
    }

    fun refreshDetails() {
        viewModelScope.launch {
            _state.update { it.copy(refreshing = true) }
            try {
                repository.refreshDetails()
                // 轮询等待完成
                var done = false
                repeat(30) {
                    kotlinx.coroutines.delay(2000)
                    val status = repository.getRefreshDetailsStatus()
                    if (status.isSuccess && status.data?.status == "completed") {
                        done = true
                        return@repeat
                    }
                }
                loadData()
            } catch (_: Exception) { }
            _state.update { it.copy(refreshing = false) }
        }
    }

    fun loadHoldings(fundId: Int) {
        _state.update { it.copy(selectedFundId = fundId) }
        viewModelScope.launch {
            try {
                val hRes = repository.getFundHoldings(fundId)
                val mRes = repository.getFundManager(fundId)
                _state.update {
                    it.copy(
                        holdings = if (hRes.isSuccess) hRes.data ?: emptyList() else emptyList(),
                        managers = if (mRes.isSuccess) mRes.data ?: emptyList() else emptyList()
                    )
                }
            } catch (_: Exception) { }
        }
    }
}
