package com.fundquant.app.data.local

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "server_config")

data class ServerConfig(
    val baseUrl: String = DEFAULT_BASE_URL,
    val isConfigured: Boolean = false
) {
    companion object {
        const val DEFAULT_BASE_URL = "http://10.0.2.2:8000/api"
    }
}

@Singleton
class ServerConfigManager @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private object Keys {
        val BASE_URL = stringPreferencesKey("base_url")
        val IS_CONFIGURED = booleanPreferencesKey("is_configured")
    }

    val configFlow: Flow<ServerConfig> = context.dataStore.data.map { prefs ->
        ServerConfig(
            baseUrl = prefs[Keys.BASE_URL] ?: ServerConfig.DEFAULT_BASE_URL,
            isConfigured = prefs[Keys.IS_CONFIGURED] ?: false
        )
    }

    suspend fun saveBaseUrl(url: String) {
        context.dataStore.edit { prefs ->
            prefs[Keys.BASE_URL] = url
            prefs[Keys.IS_CONFIGURED] = true
        }
    }
}
