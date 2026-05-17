/**
 * 因子雷达图 — ECharts
 */

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { FactorScore } from '../types';

interface FactorRadarChartProps {
  factorScores: FactorScore[];
  height?: number;
}

const FactorRadarChart: React.FC<FactorRadarChartProps> = ({
  factorScores,
  height = 300,
}) => {
  const option = useMemo(() => {
    const indicators = factorScores.map((fs) => ({
      name: fs.factor_name,
      max: 1,
      min: -1,
    }));

    const values = factorScores.map((fs) => fs.score);

    return {
      tooltip: {
        trigger: 'item',
      },
      radar: {
        indicator: indicators,
        shape: 'polygon' as const,
        splitNumber: 4,
        axisName: {
          color: '#666',
          fontSize: 12,
        },
        splitArea: {
          areaStyle: {
            color: ['rgba(25, 118, 210, 0.02)', 'rgba(25, 118, 210, 0.05)'],
          },
        },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: values,
              name: '因子评分',
              areaStyle: {
                color: 'rgba(25, 118, 210, 0.2)',
              },
              lineStyle: {
                color: '#1976D2',
                width: 2,
              },
              itemStyle: {
                color: '#1976D2',
              },
            },
          ],
        },
      ],
    };
  }, [factorScores]);

  if (!factorScores.length) {
    return <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999' }}>暂无因子数据</div>;
  }

  return <ReactECharts option={option} style={{ height }} />;
};

export default FactorRadarChart;
