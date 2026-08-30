/**
 * AI Skill API — 分析技能包管理
 */

import apiClient from './client';
import type { ApiResponse, AISkill, AISkillImportResult } from '../types';

const BASE = '/api/ai/skills';

export interface AISkillPayload {
  name: string;
  description?: string | null;
  system_prompt: string;
  enabled?: boolean;
}

export const aiSkillApi = {
  list: () =>
    apiClient.get<ApiResponse<AISkill[]>>(BASE).then((r) => r.data),

  create: (data: AISkillPayload) =>
    apiClient.post<ApiResponse<AISkill>>(BASE, data).then((r) => r.data),

  /** 批量导入 JSON 数组，按 name upsert */
  import: (items: AISkillPayload[]) =>
    apiClient.post<ApiResponse<AISkillImportResult>>(`${BASE}/import`, items).then((r) => r.data),

  update: (id: number, data: Partial<AISkillPayload>) =>
    apiClient.put<ApiResponse<AISkill>>(`${BASE}/${id}`, data).then((r) => r.data),

  toggle: (id: number, enabled: boolean) =>
    apiClient.patch<ApiResponse<AISkill>>(`${BASE}/${id}/toggle`, { enabled }).then((r) => r.data),

  remove: (id: number) =>
    apiClient.delete<ApiResponse<null>>(`${BASE}/${id}`).then((r) => r.data),
};

/** 导入示例模板（前端"填入示例"按钮） */
export const SKILL_EXAMPLE = JSON.stringify([
  {
    name: "深度基本面分析",
    description: "从持仓/风格/风险维度深度分析基金，输出结构化结论",
    system_prompt: "你将扮演一名资深基金分析师。请对给出的基金进行深度分析：\n1. 结合因子评分拆解收益来源与风险点\n2. 关注回撤修复度与动量加速度的背离信号\n3. 输出结构：核心结论（3 条）→ 关键数据支撑 → 风险提示\n\n{{fund_pool}}\n\n{{market_regime}}",
    enabled: true,
  },
  {
    name: "个股持仓透视",
    description: "对单只基金的 top10 持仓做行业集中度与相关性分析",
    system_prompt: "基于单基金上下文，分析该基金前十大持仓：\n- 行业集中度与抱团风险\n- 持仓个股近期涨跌对净值的传导\n- 给出 1-10 的分散度评分并说明理由\n\n{{fund:1}}",
    enabled: false,
  },
], null, 2);
