/**
 * 分析数据 Hook
 */

import { useState, useCallback } from 'react';
import { analysisApi } from '../api/analysis';
import type { AnalysisResultOut } from '../types';

interface UseAnalysisReturn {
  results: AnalysisResultOut[];
  loading: boolean;
  error: string | null;
  loadLatest: () => Promise<void>;
  triggerAnalysis: (fundIds?: number[]) => Promise<void>;
  queryByDate: (date: string) => Promise<void>;
}

export function useAnalysis(): UseAnalysisReturn {
  const [results, setResults] = useState<AnalysisResultOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLatest = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await analysisApi.latest();
      if (res.data) {
        setResults(res.data);
      }
    } catch (err: any) {
      setError(err.message || '加载分析数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerAnalysis = useCallback(async (fundIds?: number[]) => {
    setLoading(true);
    setError(null);
    try {
      const res = await analysisApi.trigger(fundIds);
      if (res.data) {
        setResults(res.data);
      }
    } catch (err: any) {
      setError(err.message || '触发分析失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const queryByDate = useCallback(async (date: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await analysisApi.query({ date });
      if (res.data) {
        setResults(res.data);
      }
    } catch (err: any) {
      setError(err.message || '查询分析数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    results,
    loading,
    error,
    loadLatest,
    triggerAnalysis,
    queryByDate,
  };
}
