/**
 * 通用格式化助手 — 原 Dashboard/ReviewPage/ETFScanPage/FundDetailPage 各自复制
 */

/** 涨跌百分比（+x.xx% / -x.xx% / —） */
export const pct = (v?: number | null, digits = 2): string =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

/** 红涨绿跌（中国市场惯例） */
export const growthColor = (v?: number | null): string =>
  v == null ? 'inherit' : v > 0 ? '#f44336' : v < 0 ? '#4caf50' : 'inherit';

/** 更新时间统一北京时间展示（无时区标记=北京墙钟直接展示；带标记=换算） */
export const formatBeijingTime = (iso: string | null): string => {
  if (!iso) return '暂无';
  try {
    if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso)) {
      const d = new Date(iso);
      if (!isNaN(d.getTime())) {
        return new Intl.DateTimeFormat('zh-CN', {
          timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
          day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
        }).format(d).split('/').join('-');
      }
    }
    const m = iso.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return m ? `${m[1]} ${m[2]} (北京时间)` : iso;
  } catch {
    return iso;
  }
};

/** 信号强度中文标签（全仓唯一定义，含 light_buy/light_sell 五档以上强度） */
export const STRENGTH_LABELS: Record<string, string> = {
  heavy_buy: '强烈买入',
  moderate_buy: '适度买入',
  light_buy: '轻仓买入',
  hold: '观望',
  light_sell: '轻仓减仓',
  moderate_sell: '适度减仓',
  heavy_sell: '强烈减仓',
};

/** 信号强度 → MUI Chip 颜色（买=error 红 / 卖=success 绿，红涨绿跌惯例） */
export const STRENGTH_CHIP_COLOR: Record<string, 'error' | 'success' | 'default'> = {
  heavy_buy: 'error',
  moderate_buy: 'error',
  light_buy: 'error',
  hold: 'default',
  light_sell: 'success',
  moderate_sell: 'success',
  heavy_sell: 'success',
};
