/**
 * Zustand 全局状态管理
 */

import { create } from 'zustand';

interface AppState {
  /** AI 功能是否开启（由后端 system_config 决定） */
  aiEnabled: boolean;
  /** 侧边栏是否展开 */
  sidebarOpen: boolean;
  /** 当前页面标题 */
  pageTitle: string;

  /** Actions */
  setAiEnabled: (enabled: boolean) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setPageTitle: (title: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  aiEnabled: true,
  sidebarOpen: true,
  pageTitle: '仪表盘',

  setAiEnabled: (enabled) => set({ aiEnabled: enabled }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setPageTitle: (title) => set({ pageTitle: title }),
}));
