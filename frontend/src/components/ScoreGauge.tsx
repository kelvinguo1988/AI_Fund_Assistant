/**
 * 评分仪表盘 — ECharts
 * 使用 axisLine color stops 替代 progress，避免溢出
 */

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

interface ScoreGaugeProps {
  score: number;
  height?: number;
}

const ScoreGauge: React.FC<ScoreGaugeProps> = ({ score, height = 200 }) => {
  const option = useMemo(() => {
    // 总分钳位 ±8.5（与 active 因子总权重对齐，见后端 scoring_engine）
    const clampedScore = Math.max(-8.5, Math.min(8.5, score));

    // 根据评分确定颜色
    let color: string;
    if (clampedScore >= 1.5) {
      color = '#E74C3C'; // 红（偏多）
    } else if (clampedScore <= -1.5) {
      color = '#27AE60'; // 绿（偏空）
    } else {
      color = '#95A5A6'; // 灰（中性）
    }

    // 表盘刻度 -9~+9（略宽于钳位范围，刻度间距 3 更整洁）映射为 0~1
    const ratio = Math.max(0, (clampedScore + 9) / 18);

    return {
      series: [
        {
          type: 'gauge',
          startAngle: 210,
          endAngle: -30,
          min: -9,
          max: 9,
          splitNumber: 6,
          itemStyle: {
            color,
          },
          progress: { show: false },
          pointer: {
            show: true,
            length: '55%',
            width: 3,
            itemStyle: { color },
          },
          axisLine: {
            lineStyle: {
              width: 20,
              color: [
                [ratio, color],
                [1, '#e8e8e8'],
              ],
            },
          },
          axisTick: {
            distance: -28,
            lineStyle: { color: '#999', width: 1 },
            length: 6,
          },
          splitLine: {
            distance: -34,
            lineStyle: { color: '#999', width: 2 },
            length: 14,
          },
          axisLabel: {
            distance: -22,
            color: '#999',
            fontSize: 11,
          },
          detail: {
            valueAnimation: true,
            formatter: '{value}',
            fontSize: 22,
            fontWeight: 'bold',
            color,
            offsetCenter: [0, '72%'],
          },
          data: [{ value: clampedScore }],
        },
      ],
    };
  }, [score]);

  return <ReactECharts option={option} style={{ height }} />;
};

export default ScoreGauge;
