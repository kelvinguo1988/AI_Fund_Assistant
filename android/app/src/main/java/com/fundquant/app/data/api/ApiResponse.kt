package com.fundquant.app.data.model

import com.google.gson.annotations.SerializedName

/**
 * 统一 API 响应包装
 */
data class ApiResponse<T>(
    val code: Int = 0,
    val data: T? = null,
    val message: String = "success"
) {
    val isSuccess: Boolean get() = code == 0
}

/**
 * 错误响应（HTTP 非 200）
 */
data class ApiError(
    val detail: String = "未知错误"
)
