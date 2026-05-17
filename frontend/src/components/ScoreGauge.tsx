/**
 * 评分仪表盘 — ECharts
 */

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

interface ScoreGaugeProps {
  score: number;
  height?: number;
}

const ScoreGauge: React.FC<ScoreGaugeProps> = ({ score, height = 200 }) => {
  const option = useMemo(() => {
    // 根据评分确定颜色（-6.0 ~ +6.0 范围）
    let color: string;
    if (score >= 1.5) {
      color = 'var(--signal-buy)'; // 红（偏多）
    } else if (score <= -1.5) {
      color = 'var(--signal-sell)'; // 绿（偏空）
    } else {
      color = 'var(--signal-hold)'; // 灰（中性）
    }

    // ECharts 不支持 CSS 变量，转成具体颜色值
    const colorMap: Record<string, string> = {
      'var(--signal-buy)': '#E74C3C',
      'var(--signal-sell)': '#27AE60',
      'var(--signal-hold)': '#95A5A6',
    };
    const realColor = colorMap[color] || '#95A5A6';

    return {
      series: [
        {
          type: 'gauge',
          startAngle: 200,
          endAngle: -20,
          min: -6,
          max: 6,
          splitNumber: 6,
          itemStyle: {
            color: realColor,
          },
          progress: {
            show: true,
            width: 18,
          },
          pointer: {
            show: true,
            length: '60%',
            width: 4,
            itemStyle: {
              color: realColor,
            },
          },
          axisLine: {
            lineStyle: {
              width: 18,
              color: [[1, '#e0e0e0']],
            },
          },
          axisTick: {
            distance: -25,
            lineStyle: {
              color: '#999',
              width: 1,
            },
          },
          splitLine: {
            distance: -30,
            lineStyle: {
              color: '#999',
              width: 2,
            },
          },
          axisLabel: {
            distance: -15,
            color: '#999',
            fontSize: 11,
          },
          detail: {
            valueAnimation: true,
            formatter: '{value}',
            fontSize: 24,
            fontWeight: 'bold',
            color: realColor,
            offsetCenter: [0, '70%'],
          },
          data: [{ value: score }],
        },
      ],
    };
  }, [score]);

  return <ReactECharts option={option} style={{ height }} />;
};

export default ScoreGauge;
