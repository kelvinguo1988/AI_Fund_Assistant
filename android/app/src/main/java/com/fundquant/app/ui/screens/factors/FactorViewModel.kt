package com.fundquant.app.ui.screens.factors

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fundquant.app.data.model.Factor
import com.fundquant.app.data.model.FactorUpdate
import com.fundquant.app.data.repository.FundRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class FactorState(
    val loading: Boolean = true,
    val error: String? = null,
    val factors: List<Factor> = emptyList()
)

@HiltViewModel
class FactorViewModel @Inject constructor(
    private val repository: FundRepository
) : ViewModel() {

    private val _state = MutableStateFlow(FactorState())
    val state: StateFlow<FactorState> = _state.asStateFlow()

    init { loadFactors() }

    fun loadFactors() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            try {
                val res = repository.getFactors()
                if (res.isSuccess) {
                    _state.update { it.copy(loading = false, factors = res.data ?: emptyList()) }
                } else {
                    _state.update { it.copy(loading = false, error = res.message) }
                }
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = e.localizedMessage) }
            }
        }
    }

    fun updateWeight(id: Int, weight: Double) {
        viewModelScope.launch {
            try {
                repository.updateFactor(id, FactorUpdate(weight = weight))
                loadFactors()
            } catch (_: Exception) { }
        }
    }
}
