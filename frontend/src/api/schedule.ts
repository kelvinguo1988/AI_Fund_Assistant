/**
 * 调度计划 API
 */

import apiClient from './client';
import type { ApiResponse, ScheduleCreate, ScheduleUpdate, ScheduleOut } from '../types';

const BASE = '/api/schedules';

export const scheduleApi = {
  list: () =>
    apiClient.get<ApiResponse<ScheduleOut[]>>(BASE).then((r) => r.data),

  create: (data: ScheduleCreate) =>
    apiClient.post<ApiResponse<ScheduleOut>>(BASE, data).then((r) => r.data),

  update: (id: number, data: ScheduleUpdate) =>
    apiClient.put<ApiResponse<ScheduleOut>>(`${BASE}/${id}`, data).then((r) => r.data),

  delete: (id: number) =>
    apiClient.delete<ApiResponse<null>>(`${BASE}/${id}`).then((r) => r.data),
};
