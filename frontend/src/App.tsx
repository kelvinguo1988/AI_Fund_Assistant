/**
 * 路由配置 + 布局框架
 * T04: 7 个页面组件全部实现
 */

import React from 'react';
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
  Settings as SettingsIcon,
} from '@mui/icons-material';
import { useAppStore } from './store';
import AIChatWidget from './components/AIChatWidget';

// 页面组件
import Dashboard from './pages/Dashboard';
import FundPool from './pages/FundPool';
import FactorManagement from './pages/FactorManagement';
import PushConfig from './pages/PushConfig';
import ReportConfig from './pages/ReportConfig';
import SchedulePlan from './pages/SchedulePlan';
import HistoryReports from './pages/HistoryReports';
import ScoringConfig from './pages/ScoringConfig';
import SystemPage from './pages/System';
import FundDetailPage from './pages/FundDetailPage';

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
  { key: 'history', label: '历史报告', icon: <HistoryIcon />, path: '/history' },
  { key: 'system', label: '系统设置', icon: <SettingsIcon />, path: '/system' },
];

const DRAWER_WIDTH = 220;

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
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/funds" element={<FundPool />} />
          <Route path="/fund-details" element={<FundDetailPage />} />
          <Route path="/factors" element={<FactorManagement />} />
          <Route path="/push" element={<PushConfig />} />
          <Route path="/report" element={<ReportConfig />} />
          <Route path="/schedule" element={<SchedulePlan />} />
          <Route path="/scoring" element={<ScoringConfig />} />
          <Route path="/history" element={<HistoryReports />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
        </Routes>
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
