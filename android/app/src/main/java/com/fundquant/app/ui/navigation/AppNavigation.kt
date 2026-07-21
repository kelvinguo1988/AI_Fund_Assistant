package com.fundquant.app.ui.navigation

import androidx.compose.animation.*
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.fundquant.app.ui.screens.dashboard.*
import com.fundquant.app.ui.screens.fundpool.*
import com.fundquant.app.ui.screens.funddetail.*
import com.fundquant.app.ui.screens.factors.*
import com.fundquant.app.ui.screens.settings.*
import com.fundquant.app.ui.screens.system.*
import com.fundquant.app.ui.theme.*
import kotlinx.coroutines.launch

// ==================== 导航图标映射 ====================

private fun iconFor(screen: Screen): ImageVector = when (screen) {
    Screen.Dashboard -> Icons.Outlined.Dashboard
    Screen.FundPool -> Icons.Outlined.AccountBalance
    Screen.FundDetail -> Icons.Outlined.Analytics
    Screen.Factors -> Icons.Outlined.Tune
    Screen.Push -> Icons.Outlined.Send
    Screen.Report -> Icons.Outlined.Description
    Screen.Schedule -> Icons.Outlined.Schedule
    Screen.Scoring -> Icons.Outlined.Score
    Screen.Quality -> Icons.Outlined.FilterAlt
    Screen.History -> Icons.Outlined.History
    Screen.Backtest -> Icons.Outlined.ShowChart
    Screen.System -> Icons.Outlined.Settings
    Screen.ServerConfig -> Icons.Outlined.Dns
    Screen.AIChat -> Icons.Outlined.SmartToy
}

// ==================== 主导航 ====================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppNavigation() {
    val navController = rememberNavController()
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val currentRoute = navController.currentBackStackEntryAsState().value?.destination?.route

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet(
                modifier = Modifier.width(280.dp),
                drawerContainerColor = DarkSurface
            ) {
                // 标题
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(20.dp)
                ) {
                    Text(
                        "基金量化助手",
                        style = MaterialTheme.typography.headlineMedium,
                        color = PrimaryBlue,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        "AI_Fund_Assistant",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextMuted
                    )
                }

                HorizontalDivider(color = DarkDivider)

                // 导航列表
                LazyColumn(
                    modifier = Modifier.padding(vertical = 8.dp)
                ) {
                    items(Screen.allScreens) { screen ->
                        val selected = currentRoute == screen.route
                        NavigationDrawerItem(
                            icon = {
                                Icon(
                                    iconFor(screen),
                                    contentDescription = screen.title,
                                    tint = if (selected) PrimaryBlue else TextSecondary
                                )
                            },
                            label = {
                                Text(
                                    screen.title,
                                    color = if (selected) PrimaryBlue else TextPrimary,
                                    fontSize = 15.sp
                                )
                            },
                            selected = selected,
                    onClick = {
                        navController.navigate(screen.route) {
                            popUpTo(Screen.Dashboard.route) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                        scope.launch { drawerState.close() }
                    },
                            modifier = Modifier.padding(horizontal = 12.dp),
                            colors = NavigationDrawerItemDefaults.colors(
                                unselectedContainerColor = DarkSurface,
                                selectedContainerColor = DarkCard,
                            )
                        )
                    }
                }
            }
        }
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = {
                        val title = Screen.allScreens.find { it.route == currentRoute }?.title ?: "仪表盘"
                        Text(title, color = TextPrimary)
                    },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Default.Menu, "菜单", tint = TextSecondary)
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = DarkBackground
                    )
                )
            },
            containerColor = DarkBackground
        ) { padding ->
            NavHost(
                navController = navController,
                startDestination = Screen.Dashboard.route,
                modifier = Modifier.padding(padding)
            ) {
                composable(Screen.Dashboard.route) { DashboardScreen() }
                composable(Screen.FundPool.route) { FundPoolScreen() }
                composable(Screen.FundDetail.route) { FundDetailScreen() }
                composable(Screen.Factors.route) { FactorScreen() }
                composable(Screen.ServerConfig.route) { ServerConfigScreen() }
                composable(Screen.System.route) { SystemScreen() }

                // 简化占位页面
                val placeholders = listOf(
                    Screen.Push, Screen.Report, Screen.Schedule,
                    Screen.Scoring, Screen.Quality, Screen.History,
                    Screen.Backtest
                )
                placeholders.forEach { screen ->
                    composable(screen.route) {
                        PlaceholderScreen(screen.title)
                    }
                }
            }
        }
    }
}

@Composable
fun PlaceholderScreen(title: String) {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                title,
                style = MaterialTheme.typography.headlineMedium,
                color = TextSecondary
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "功能开发中…",
                style = MaterialTheme.typography.bodyMedium,
                color = TextMuted
            )
        }
    }
}
