# Android 自签名 App — 基金量化助手

## 架构总览

```
android/
├── build.gradle.kts              # 项目级 Gradle（AGP 8.5 + Kotlin 1.9.24 + Hilt 2.51.1）
├── settings.gradle.kts           # 模块配置
├── gradle.properties             # JVM 参数
├── app/
│   ├── build.gradle.kts          # 应用级依赖（Compose, Retrofit, Hilt, Vico 等）
│   ├── proguard-rules.pro        # 混淆规则
│   └── src/main/
│       ├── AndroidManifest.xml   # 权限 + 应用声明
│       ├── res/                  # 主题、字符串、网络安全配置
│       └── java/com/fundquant/app/
│           ├── FundQuantApp.kt           # Hilt Application
│           ├── MainActivity.kt           # 单 Activity 入口
│           ├── di/NetworkModule.kt       # Hilt 网络模块
│           ├── data/
│           │   ├── api/
│           │   │   ├── ApiResponse.kt            # 统一响应模型
│           │   │   ├── FundQuantApi.kt           # Retrofit API（40端点）
│           │   │   └── DynamicBaseUrlInterceptor.kt  # 动态 Base URL
│           │   ├── model/Models.kt               # 全部数据类
│           │   ├── repository/FundRepository.kt  # 数据仓库
│           │   └── local/ServerConfigManager.kt  # 服务器地址 DataStore
│           └── ui/
│               ├── theme/        # 深色金融主题 + 红涨绿跌色板
│               ├── navigation/   # 抽屉导航 + 路由定义
│               ├── components/   # 信号灯/卡片/Chip 等共享组件
│               └── screens/
│                   ├── dashboard/    # 仪表盘（市场概况+基金分析）
│                   ├── fundpool/     # 基金池 CRUD
│                   ├── funddetail/   # 基金详情（4Tab）
│                   ├── factors/      # 因子管理 + 权重滑块
│                   ├── settings/     # ★ 服务器地址配置
│                   └── system/       # AI配置+连通性测试
```

## 技术栈

| 层 | 技术 |
|----|------|
| UI | Jetpack Compose + Material 3 |
| DI | Hilt |
| 网络 | Retrofit 2.11 + OkHttp 4.12 |
| 状态 | ViewModel + StateFlow |
| 本地存储 | DataStore Preferences |
| 导航 | Navigation Compose + ModalNavigationDrawer |
| 序列化 | Gson |

## ★ 核心功能：服务器地址配置

1. **首次启动**：使用默认地址 `http://10.0.2.2:8000/api`
2. **进入 App 后**：左侧抽屉 → "服务器配置"
3. **配置面板**：
   - 手动输入 IP 或域名
   - 快捷预设（模拟器/本地/局域网）
   - **测试连接**：调用 `/health` 验证连通性
   - **保存并应用**：DataStore 持久化，下次启动自动使用

## 已实现页面

| 页面 | 功能 |
|------|------|
| 仪表盘 | 信号概览/涨跌分布/成交额/大盘资金流/沪深港通/行业板块/基金分析列表/选中详情 |
| 基金池 | 列表/新增/编辑/删除/启用停用/标签 |
| 基金详情 | 4Tab：阶段涨幅/持仓明细/基金经理/变更摘要 |
| 因子管理 | 因子列表/权重滑块/方向标签/标准化方式 |
| 服务器配置 | URL 输入/快捷预设/连接测试/持久化保存 |
| 系统设置 | AI模型配置/连通性测试/应用信息 |
| 其他页面 | 占位页面（推送/报告/调度/评分/质量/历史/回测） |

## 自签名构建

使用 Android debug keystore 签名：
```bash
# 构建 debug APK
./gradlew assembleDebug

# 构建 release APK（自签名）
./gradlew assembleRelease
# 输出: app/build/outputs/apk/release/app-release.apk
```

## 数据流

```
ServerConfigScreen → DataStore ← DynamicBaseUrlInterceptor
                                      ↓
                                OkHttp Client
                                      ↓
                    Retrofit → FundQuantApi (40 endpoints)
                                      ↓
                              FundRepository
                                      ↓
                    ViewModel → StateFlow → Compose UI
```

## 与 Web 版的对应关系

| Web 功能 | Android 实现 |
|----------|-------------|
| React Router | Navigation Compose + 抽屉导航 |
| MUI Theme | Material 3 + 自定义深色金融主题 |
| ECharts 仪表盘 | 信号强度 Chip + 卡片数据展示 |
| ECharts 折线图 | 后续用 Vico 图表库 |
| Zustand 状态 | ViewModel + StateFlow |
| Axios 拦截器 | OkHttp Interceptor (动态 Base URL) |
| SSE 流式分析 | OkHttp SSE（后续实现） |
| .env 配置 | DataStore 持久化服务器地址 |
