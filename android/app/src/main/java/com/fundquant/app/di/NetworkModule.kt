package com.fundquant.app.di

import com.fundquant.app.data.api.BaseUrlProvider
import com.fundquant.app.data.api.DynamicBaseUrlInterceptor
import com.fundquant.app.data.api.FundQuantApi
import com.fundquant.app.data.local.ServerConfigManager
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides
    @Singleton
    fun provideOkHttpClient(
        baseUrlInterceptor: DynamicBaseUrlInterceptor
    ): OkHttpClient {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }
        return OkHttpClient.Builder()
            .addInterceptor(baseUrlInterceptor)
            .addInterceptor(logging)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)  // 回测/分析接口需要较长时间
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    @Provides
    @Singleton
    fun provideRetrofit(client: OkHttpClient): Retrofit {
        // 初始 Base URL — 会被 DynamicBaseUrlInterceptor 覆盖 host/port。
        // 必须以 /api/ 结尾，这样 Retrofit 把 "funds" 解析为 "/api/funds"，
        // 拦截器才能识别 /api 前缀并正确替换服务器地址；
        // 而 "/health"（带前导斜杠）会解析为根路径 /health，匹配后端根路由。
        return Retrofit.Builder()
            .baseUrl("http://localhost:8000/api/")
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    @Provides
    @Singleton
    fun provideFundQuantApi(retrofit: Retrofit): FundQuantApi {
        return retrofit.create(FundQuantApi::class.java)
    }

    @Provides
    @Singleton
    fun provideBaseUrlProvider(serverConfigManager: ServerConfigManager): BaseUrlProvider {
        return BaseUrlProvider(serverConfigManager)
    }
}
