/**
 * 信号回测 API
 */

import apiClient from './client';
import type { ApiResponse, BacktestSummary } from '../types';

const BASE = '/api/backtest';

export const backtestApi = {
  /** 运行信号回测 */
  run: (fundId: number, period = 365, effectivenessWindow = 5) =>
    apiClient
      .get<ApiResponse<BacktestSummary>>(`${BASE}/${fundId}`, {
        params: { period, effectiveness_window: effectivenessWindow },
        timeout: 180000,
      })
      .then((r) => r.data),
};
