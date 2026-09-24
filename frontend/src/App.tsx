/**
 * 路由配置 + 布局框架
 * T04: 7 个页面组件全部实现
 */

import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import {
  ThemeProvider,
  createTheme,
  CssBaseline,
  Box,
  AppBar,
  Toolbar,
  Typography,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  IconButton,
  Switch,
  LinearProgress,
} from '@mui/material';
import {
  Dashboard as DashboardIcon,
  AccountBalanceWallet as FundIcon,
  Assessment as DetailIcon,
  Tune as FactorIcon,
  Send as PushIcon,
  Description as ReportIcon,
  Schedule as ScheduleIcon,
  History as HistoryIcon,
  Menu as MenuIcon,
  SmartToy as AIIcon,
  Tune as ScoringIcon,
  Security as QualityIcon,
  Settings as SettingsIcon,
  Science as BacktestIcon,

  Insights as ReviewIcon,

  TravelExplore as ScanIcon,
  AccountBalance as PositionsIcon,
  AutoAwesome as WorkbenchIcon,
} from '@mui/icons-material';
import { useAppStore } from './store';
import AIChatWidget from './components/AIChatWidget';

import ErrorBell from './components/ErrorBell';

// 页面级懒加载：进入路由才拉取对应 chunk，缩小首屏体积（仪表盘优先可见）
const Dashboard = lazy(() => import('./pages/Dashboard'));
const FundPool = lazy(() => import('./pages/FundPool'));
const FactorManagement = lazy(() => import('./pages/FactorManagement'));
const PushConfig = lazy(() => import('./pages/PushConfig'));
const ReportConfig = lazy(() => import('./pages/ReportConfig'));
const SchedulePlan = lazy(() => import('./pages/SchedulePlan'));
const HistoryReports = lazy(() => import('./pages/HistoryReports'));
const ScoringConfig = lazy(() => import('./pages/ScoringConfig'));
const QualityConfig = lazy(() => import('./pages/QualityConfig'));
const SystemPage = lazy(() => import('./pages/System'));
const FundDetailPage = lazy(() => import('./pages/FundDetailPage'));
const SignalBacktest = lazy(() => import('./pages/SignalBacktest'));
const ReviewPage = lazy(() => import('./pages/ReviewPage'));
const ETFScanPage = lazy(() => import('./pages/ETFScanPage'));
const PositionsPage = lazy(() => import('./pages/PositionsPage'));
const AiWorkbench = lazy(() => import('./pages/AiWorkbench'));

/* ── MUI 主题（红涨绿跌） ─────────────────────────────────────────── */
const theme = createTheme({
  palette: {
    primary: { main: '#1976D2' },
    secondary: { main: '#9C27B0' },
    background: { default: '#F5F5F5' },
  },
  typography: {
    fontFamily: [
      '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto',
      '"Helvetica Neue"', 'Arial', 'sans-serif',
    ].join(','),
  },
});

/* ── 导航项配置 ───────────────────────────────────────────────────── */
const NAV_ITEMS = [
  { key: 'dashboard', label: '仪表盘', icon: <DashboardIcon />, path: '/dashboard' },
  { key: 'funds', label: '基金池', icon: <FundIcon />, path: '/funds' },
  { key: 'fund-detail', label: '基金详情', icon: <DetailIcon />, path: '/fund-details' },
  { key: 'factors', label: '因子管理', icon: <FactorIcon />, path: '/factors' },
  { key: 'push', label: '推送配置', icon: <PushIcon />, path: '/push' },
  { key: 'report', label: '报告配置', icon: <ReportIcon />, path: '/report' },
  { key: 'schedule', label: '调度计划', icon: <ScheduleIcon />, path: '/schedule' },
  { key: 'scoring', label: '评分配置', icon: <ScoringIcon />, path: '/scoring' },
  { key: 'quality', label: '质量过滤', icon: <QualityIcon />, path: '/quality-config' },
  { key: 'history', label: '历史报告', icon: <HistoryIcon />, path: '/history' },
  { key: 'backtest', label: '信号回测', icon: <BacktestIcon />, path: '/backtest' },
  { key: 'etf-scan', label: 'ETF 扫描', icon: <ScanIcon />, path: '/etf-scan' },
  { key: 'review', label: '投资复盘', icon: <ReviewIcon />, path: '/review' },
  { key: 'positions', label: '我的持仓', icon: <PositionsIcon />, path: '/positions' },
  { key: 'ai-workbench', label: 'AI 工作台', icon: <WorkbenchIcon />, path: '/ai-workbench' },
  { key: 'system', label: '系统设置', icon: <SettingsIcon />, path: '/system' },
];

const DRAWER_WIDTH = 220;

/* ── 顶栏时钟：秒级重渲染限定在本组件，避免整个布局每秒 re-render ──── */
const HeaderClock: React.FC = () => {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <Typography component="span" variant="body2" sx={{ ml: 2, opacity: 0.75 }}>
      {now.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
    </Typography>
  );
};

/* ── 侧边栏导航组件 ───────────────────────────────────────────────── */
const SidebarNav: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { setPageTitle } = useAppStore();

  return (
    <List>
      {NAV_ITEMS.map((item) => (
        <ListItemButton
          key={item.key}
          selected={location.pathname === item.path}
          onClick={() => {
            setPageTitle(item.label);
            navigate(item.path);
          }}
        >
          <ListItemIcon>{item.icon}</ListItemIcon>
          <ListItemText primary={item.label} />
        </ListItemButton>
      ))}
    </List>
  );
};

/* ── 主布局组件 ───────────────────────────────────────────────────── */
const AppLayout: React.FC = () => {
  const { sidebarOpen, aiEnabled, toggleSidebar, setAiEnabled } = useAppStore();

  // 开关状态以后端为准：store 默认 true，不回填则刷新后恒显示"开"，
  // 而后端 ai_enabled=false 时 AI 工作台/每日简报会报"未启用"
  useEffect(() => {
    import('./api/system')
      .then(({ systemApi }) => systemApi.getConfig())
      .then((res) => setAiEnabled(Boolean(res.data?.ai_enabled)))
      .catch(() => { /* 读取失败保持现状 */ });
  }, [setAiEnabled]);

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      {/* ── 顶栏 ── */}
      <AppBar
        position="fixed"
        sx={{ zIndex: (t) => t.zIndex.drawer + 1 }}
      >
        <Toolbar>
          <IconButton
            color="inherit"
            edge="start"
            onClick={toggleSidebar}
            sx={{ mr: 2 }}
          >
            <MenuIcon />
          </IconButton>
          <Typography variant="h6" noWrap sx={{ flexGrow: 1 }}>
            基金量化交易系统
            <HeaderClock />
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <AIIcon fontSize="small" />
            <Typography variant="body2">AI</Typography>
            <Switch
              checked={aiEnabled}
              onChange={async (e) => {
                const val = e.target.checked;
                setAiEnabled(val);
                try {
                  const { systemApi } = await import('./api/system');
                  await systemApi.updateConfig({ ai_enabled: val });
                } catch { /* 静默失败 */ }
              }}
              color="secondary"
              size="small"
            />
            <ErrorBell />
          </Box>
        </Toolbar>
      </AppBar>

      {/* ── 侧边栏 ── */}
      <Drawer
        variant="persistent"
        anchor="left"
        open={sidebarOpen}
        sx={{
          width: sidebarOpen ? DRAWER_WIDTH : 0,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: DRAWER_WIDTH,
            boxSizing: 'border-box',
          },
        }}
      >
        <Toolbar />
        <SidebarNav />
      </Drawer>

      {/* ── 主内容区 ── */}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 0,
          transition: 'margin 0.2s',
          marginLeft: sidebarOpen ? 0 : `-${DRAWER_WIDTH}px`,
        }}
      >
        <Toolbar />
        <Suspense
          fallback={
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}>
              <LinearProgress sx={{ width: 240 }} />
            </Box>
          }
        >
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/funds" element={<FundPool />} />
          <Route path="/fund-details" element={<FundDetailPage />} />
          <Route path="/factors" element={<FactorManagement />} />
          <Route path="/push" element={<PushConfig />} />
          <Route path="/report" element={<ReportConfig />} />
          <Route path="/schedule" element={<SchedulePlan />} />
          <Route path="/scoring" element={<ScoringConfig />} />
          <Route path="/quality-config" element={<QualityConfig />} />
          <Route path="/history" element={<HistoryReports />} />
          <Route path="/backtest" element={<SignalBacktest />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/etf-scan" element={<ETFScanPage />} />
          <Route path="/positions" element={<PositionsPage />} />
          <Route path="/ai-workbench" element={<AiWorkbench />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
        </Routes>
        </Suspense>
      </Box>
      <AIChatWidget />
    </Box>
  );
};

/* ── App 根组件 ───────────────────────────────────────────────────── */
const App: React.FC = () => {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <AppLayout />
      </BrowserRouter>
    </ThemeProvider>
  );
};

export default App;
