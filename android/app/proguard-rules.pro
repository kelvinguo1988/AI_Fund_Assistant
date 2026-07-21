# Retrofit
-keepattributes Signature
-keepattributes *Annotation*
-keep class retrofit2.** { *; }
-keepclasseswithmembers class * { @retrofit2.http.* <methods>; }

# Gson
-keep class com.fundquant.app.data.model.** { *; }
-keepclassmembers class com.fundquant.app.data.model.** { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Coroutines
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
-keep class kotlinx.coroutines.android.AndroidDispatcherFactory { *; }

# Hilt / Dagger
-keep class dagger.hilt.** { *; }
-keep class javax.inject.** { *; }
-keep class * extends dagger.hilt.android.internal.managers.ViewComponentManager$FragmentContextWrapper
-dontwarn dagger.hilt.**
-dontwarn javax.inject.**

# Hilt ViewModels (keep @HiltViewModel classes)
-keep @dagger.hilt.android.lifecycle.HiltViewModel class * { *; }
-keep class * extends androidx.lifecycle.ViewModel { *; }

# Vico charts
-keep class com.patrykandpatrick.vico.** { *; }
-dontwarn com.patrykandpatrick.vico.**

# DataStore / Proto
-dontwarn androidx.datastore.**

# Keep generic serializable model classes used by Gson
-keepattributes Signature
-keepattributes EnclosingMethod
-keepattributes InnerClasses
-keep class com.fundquant.app.data.api.** { *; }
-keep class com.fundquant.app.data.model.** { *; }
