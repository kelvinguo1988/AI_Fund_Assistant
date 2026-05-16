"""
主调度脚本 - fund_monitor_main.py
功能：
  1. 交易日判断（自动跳过节假日）
  2. 盘中/收盘定时触发分析
  3. 串联数据→因子→信号→飞书推送全流程
  4. 完整日志记录

用法：
  直接运行：python fund_monitor_main.py
  Docker：CMD ["python", "fund_monitor_main.py"]
  单次立即执行：python fund_monitor_main.py --now
  收盘报告：python fund_monitor_main.py --close
"""
import sys
import os
import time
import logging
import argparse
from datetime import datetime, date

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import (
    TARGET_FUNDS,
    DATA_CONFIG,
    FEISHU_CONFIG,
    SCHEDULER_CONFIG,
    SIGNAL_THRESHOLDS,
    POSITION_TIERS,
    MARKET_FACTOR_WEIGHTS,
    FUND_FACTOR_WEIGHTS,
    LAYER_WEIGHTS,
    LOG_DIR,
)
from core.signal_generator import SignalGenerator
from notifier.feishu_notifier import FeishuNotifier


# ============================================================
# 日志配置
# ============================================================
os.makedirs(LOG_DIR, exist_ok=True)
log_filename = os.path.join(LOG_DIR, f"fund_monitor_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_filename, encoding="utf-8"),
    ],
)
logger = logging.getLogger("FundMonitorMain")


# ============================================================
# 交易日判断
# ============================================================

def is_trading_day(target_date: date = None) -> bool:
    """
    判断是否为A股交易日（排除周末和法定节假日）
    优先使用AKShare节假日数据，降级使用简单周末判断
    """
    if target_date is None:
        target_date = date.today()

    # 先排除周末
    if target_date.weekday() >= 5:
        return False

    # 尝试从AKShare获取节假日列表
    try:
        import akshare as ak
        year = str(target_date.year)
        df = ak.tool_trade_date_hist_sina()
        if df is not None and len(df) > 0:
            trade_dates = set(df.iloc[:, 0].astype(str).tolist())
            return target_date.strftime("%Y-%m-%d") in trade_dates
    except Exception as e:
        logger.warning(f"节假日接口获取失败，降级为简单周末判断: {e}")

    # 降级：只排除周末
    return True


def is_trading_hours(now: datetime = None) -> bool:
    """判断当前是否在交易时间内"""
    if now is None:
        now = datetime.now()
    start_time = SCHEDULER_CONFIG.get("trading_hours_start", "09:15")
    end_time   = SCHEDULER_CONFIG.get("trading_hours_end",   "15:30")
    current    = now.strftime("%H:%M")
    return start_time <= current <= end_time


# ============================================================
# 核心执行逻辑
# ============================================================

def build_config() -> dict:
    """构建统一配置字典"""
    return {
        **DATA_CONFIG,
        "SIGNAL_THRESHOLDS":   SIGNAL_THRESHOLDS,
        "POSITION_TIERS":      POSITION_TIERS,
        "MARKET_FACTOR_WEIGHTS": MARKET_FACTOR_WEIGHTS,
        "FUND_FACTOR_WEIGHTS": FUND_FACTOR_WEIGHTS,
        "LAYER_WEIGHTS":       LAYER_WEIGHTS,
    }


def run_analysis(is_close: bool = False) -> bool:
    """
    执行一次完整的基金分析并推送
    is_close=True 表示收盘分析（更完整的历史数据）
    """
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    period   = "收盘分析" if is_close else "盘中分析"
    logger.info(f"===== 开始{period} {run_time} =====")

    try:
        # 1. 构建配置
        config = build_config()

        # 2. 执行信号分析
        generator = SignalGenerator(config)
        results   = generator.run_analysis(TARGET_FUNDS)

        if not results:
            logger.warning("未获取到任何分析结果，跳过推送")
            return False

        # 3. 打印文本摘要（日志）
        summary = SignalGenerator.format_text_summary(results)
        logger.info("\n" + summary)

        # 4. 飞书推送
        notifier = FeishuNotifier(FEISHU_CONFIG)
        success  = notifier.send_analysis_report(results, run_time=run_time, is_close=is_close)

        if success:
            logger.info(f"飞书推送成功！共{len(results)}只基金")
        else:
            logger.error("飞书推送失败")

        return success

    except Exception as e:
        logger.error(f"分析执行异常: {e}", exc_info=True)
        # 推送错误通知
        try:
            notifier = FeishuNotifier(FEISHU_CONFIG)
            error_msg = {
                "msg_type": "text",
                "content": {
                    "text": f"⚠️ 基金量化监控异常 {run_time}\n错误: {str(e)}\n请检查日志: {log_filename}"
                }
            }
            import requests
            requests.post(FEISHU_CONFIG.get("webhook_url", ""), json=error_msg, timeout=5)
        except Exception:
            pass
        return False


# ============================================================
# 定时调度主循环
# ============================================================

def run_scheduler():
    """
    盘中定时调度器
    在配置的时间点触发分析（交易日内）
    """
    push_times  = FEISHU_CONFIG.get("intraday_push_times", ["09:35", "11:30", "14:00", "15:05"])
    close_time  = SCHEDULER_CONFIG.get("close_analysis_time", "15:10")
    triggered_today = set()  # 记录今日已触发的时间点

    logger.info(f"🚀 基金量化监控启动，推送时间点: {push_times}")
    logger.info(f"   收盘分析时间: {close_time}")

    while True:
        now   = datetime.now()
        today = now.date()

        # 跨日重置
        if not hasattr(run_scheduler, "_last_date") or run_scheduler._last_date != today:
            triggered_today.clear()
            run_scheduler._last_date = today
            logger.info(f"--- 新交易日: {today} ---")

        # 检查交易日
        if not is_trading_day(today):
            next_check = 3600  # 非交易日1小时检查一次
            logger.debug(f"今日非交易日，{next_check}s后再检查")
            time.sleep(next_check)
            continue

        # 当前时间字符串（HH:MM）
        current_hm = now.strftime("%H:%M")

        # 收盘分析（优先级最高）
        if current_hm >= close_time and "CLOSE" not in triggered_today:
            triggered_today.add("CLOSE")
            logger.info(f"触发收盘分析 ({current_hm})")
            run_analysis(is_close=True)

        # 盘中分析
        elif is_trading_hours(now):
            for t in push_times:
                if current_hm >= t and t not in triggered_today:
                    triggered_today.add(t)
                    logger.info(f"触发盘中分析 ({t})")
                    run_analysis(is_close=False)
                    break  # 每次循环最多触发一个时间点

        # 等待30秒后再检查
        time.sleep(30)


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="基金量化监控系统")
    parser.add_argument("--now",   action="store_true", help="立即执行一次分析")
    parser.add_argument("--close", action="store_true", help="立即执行收盘分析")
    parser.add_argument("--daemon", action="store_true", help="以守护模式启动定时调度")
    args = parser.parse_args()

    if args.now:
        logger.info("手动触发盘中分析...")
        success = run_analysis(is_close=False)
        sys.exit(0 if success else 1)

    elif args.close:
        logger.info("手动触发收盘分析...")
        success = run_analysis(is_close=True)
        sys.exit(0 if success else 1)

    else:
        # 默认：启动定时调度器
        logger.info("启动定时调度模式（Ctrl+C 退出）")
        try:
            run_scheduler()
        except KeyboardInterrupt:
            logger.info("监控系统已停止")


if __name__ == "__main__":
    main()
