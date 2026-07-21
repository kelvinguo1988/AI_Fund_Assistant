package com.fundquant.app.ui.screens.fundpool

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fundquant.app.data.model.*
import com.fundquant.app.data.repository.FundRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class FundPoolState(
    val loading: Boolean = true,
    val error: String? = null,
    val funds: List<Fund> = emptyList()
)

@HiltViewModel
class FundPoolViewModel @Inject constructor(
    private val repository: FundRepository
) : ViewModel() {

    private val _state = MutableStateFlow(FundPoolState())
    val state: StateFlow<FundPoolState> = _state.asStateFlow()

    init { loadFunds() }

    fun loadFunds() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            try {
                val res = repository.getFunds()
                if (res.isSuccess) {
                    _state.update { it.copy(loading = false, funds = res.data ?: emptyList()) }
                } else {
                    _state.update { it.copy(loading = false, error = res.message) }
                }
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = e.localizedMessage ?: "加载失败") }
            }
        }
    }

    fun addFund(code: String, name: String, type: String, tags: String) {
        viewModelScope.launch {
            try {
                repository.createFund(FundCreate(code, name, type, tags))
                loadFunds()
            } catch (_: Exception) { }
        }
    }

    fun updateFund(id: Int, name: String, type: String, tags: String) {
        viewModelScope.launch {
            try {
                repository.updateFund(id, FundUpdate(name = name, fundType = type, tags = tags))
                loadFunds()
            } catch (_: Exception) { }
        }
    }

    fun deleteFund(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteFund(id)
                loadFunds()
            } catch (_: Exception) { }
        }
    }

    fun toggleStatus(fund: Fund) {
        viewModelScope.launch {
            val newStatus = if (fund.status == "active") "disabled" else "active"
            try {
                repository.updateFund(fund.id, FundUpdate(status = newStatus))
                loadFunds()
            } catch (_: Exception) { }
        }
    }
}
