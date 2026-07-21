package com.fundquant.app.data.api

import com.fundquant.app.data.local.ServerConfig
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import javax.inject.Inject

/**
 * 动态 Base URL 拦截器 — 从 DataStore 读取用户配置的服务器地址
 */
class DynamicBaseUrlInterceptor @Inject constructor(
    private val baseUrlProvider: BaseUrlProvider
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        var request = chain.request()
        val baseUrl = baseUrlProvider.getBaseUrl()

        // 如果路径以 /api 开头，使用配置的 baseUrl
        val url = request.url
        val fullUrl = if (url.encodedPath.startsWith("/api")) {
            // Retrofit 已拼接好，只替换 host:port 部分
            val base = baseUrl.toHttpUrlOrNull()
            if (base != null) {
                url.newBuilder()
                    .scheme(base.scheme)
                    .host(base.host)
                    .port(base.port)
                    .build()
            } else url
        } else {
            // /health 等无 /api 前缀的路径，也替换
            val base = baseUrl.removeSuffix("/api").toHttpUrlOrNull()
            if (base != null) {
                url.newBuilder()
                    .scheme(base.scheme)
                    .host(base.host)
                    .port(base.port)
                    .build()
            } else url
        }

        request = request.newBuilder().url(fullUrl).build()
        return chain.proceed(request)
    }
}

/**
 * Base URL 提供器 — 读取当前 DataStore 中的服务器地址
 */
class BaseUrlProvider @Inject constructor(
    private val serverConfigManager: com.fundquant.app.data.local.ServerConfigManager
) {
    fun getBaseUrl(): String {
        return runBlocking {
            serverConfigManager.configFlow.first().baseUrl
        }
    }
}
