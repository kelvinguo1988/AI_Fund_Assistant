/**
 * 通用格式化助手 — 原 Dashboard/ReviewPage/ETFScanPage/FundDetailPage 各自复制
 */

/** 涨跌百分比（+x.xx% / -x.xx% / —） */
export const pct = (v?: number | null, digits = 2): string =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

/** 红涨绿跌色号（中国市场惯例）— 全仓唯一定义点
 *
 *  原来 Dashboard / FundDetailPanel / ScoreGauge / SignalBacktest 各写一套
 *  （#f44336/#E74C3C 两种红、#4caf50/#27AE60 两种绿），同一页面相邻区块
 *  的"涨"颜色并不一致。改动配色只需改这三行。
 */
export const GROWTH_UP = '#f44336';
export const GROWTH_DOWN = '#4caf50';
export const GROWTH_FLAT = '#999999';

/** 红涨绿跌（中国市场惯例）；null / 0 → inherit */
export const growthColor = (v?: number | null): string =>
  v == null ? 'inherit' : v > 0 ? GROWTH_UP : v < 0 ? GROWTH_DOWN : 'inherit';

/** 同上，但 0 / 空值给中性灰（资金流为 0 时不想跟正文同色的场合） */
export const growthColorOrFlat = (v?: number | null): string =>
  v == null || v === 0 ? GROWTH_FLAT : v > 0 ? GROWTH_UP : GROWTH_DOWN;

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

/** 截面样本数低于此值时，z 分/分位数的抽样误差大于因子区分度（与后端 THIN_POOL_SIZE 同值） */
export const THIN_POOL_SIZE = 20;

/**
 * 评分口径标注（Q5）— 文案与后端 `scoring_engine.score_caliber_note` 对齐
 *
 * weighted_score 由 6 个截面 z 因子加权而来，当日池内均值恒为 0：它只回答"这只在当天
 * 这批基金里排第几"，不回答"这只基金好不好"。池子构成一变分数就变，所以同一个 2.5 分
 * 在 57 只池和 8 只池不是一回事，也不能跨日比较。全仓只在这里定义一次措辞。
 */
export const scoreCaliberNote = (poolSize?: number | null): string => {
  if (!poolSize) return '池内相对分（当日截面样本数未记录，不能跨期/跨池比较）';
  const thin = poolSize < THIN_POOL_SIZE ? '，池子偏薄、截面标准化本身不稳定' : '';
  return `池内相对分（当日 ${poolSize} 只参与截面标准化${thin}）`;
};
