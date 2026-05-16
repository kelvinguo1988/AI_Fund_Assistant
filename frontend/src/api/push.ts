/**
 * 推送渠道 API
 */

import apiClient from './client';
import type { ApiResponse, PushChannelCreate, PushChannelUpdate, PushChannelOut } from '../types';

const BASE = '/api/push-channels';

export const pushApi = {
  list: () =>
    apiClient.get<ApiResponse<PushChannelOut[]>>(BASE).then((r) => r.data),

  create: (data: PushChannelCreate) =>
    apiClient.post<ApiResponse<PushChannelOut>>(BASE, data).then((r) => r.data),

  update: (id: number, data: PushChannelUpdate) =>
    apiClient.put<ApiResponse<PushChannelOut>>(`${BASE}/${id}`, data).then((r) => r.data),

  delete: (id: number) =>
    apiClient.delete<ApiResponse<null>>(`${BASE}/${id}`).then((r) => r.data),

  test: (id: number) =>
    apiClient.post<ApiResponse<null>>(`${BASE}/${id}/test`).then((r) => r.data),
};
