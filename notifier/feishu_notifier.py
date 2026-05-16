"""
飞书推送模块 - feishu_notifier.py
消息格式：总结卡片 + 高优先级基金单独卡片
支持：富文本、颜色标记（红涨绿跌）、签名验证
"""
import hashlib
import hmac
import base64
import time
import json
import logging
import requests
from typing import List, Optional
from datetime import datetime
from core.factor_engine import ComprehensiveFactorResult

logger = logging.getLogger(__name__)


# ============================================================
# 颜色与样式常量（飞书卡片规范）
# ============================================================
COLOR_MAP = {
    "strong_buy": "red",      # 强买：红色（涨）
    "buy":        "orange",   # 加仓：橙色
    "hold":       "grey",     # 观望：灰色
    "reduce":     "green",    # 减仓：绿色（跌）
    "sell":       "green",    # 清仓：深绿
}

HEADER_COLOR_MAP = {
    "strong_buy": "red",
    "buy":        "orange",
    "hold":       "grey",
    "reduce":     "green",
    "sell":       "green",
}

SIGNAL_EMOJI = {
    "strong_buy": "🔥",
    "buy":        "✅",
    "hold":       "⏸️",
    "reduce":     "⚠️",
    "sell":       "🔴",
}


def pct_color(pct: float) -> str:
    """涨跌着色：正数红色，负数绿色（A股惯例）"""
    return "red" if pct >= 0 else "green"

def pct_str(pct: float) -> str:
    """格式化百分比字符串"""
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


# ============================================================
# 飞书通知器
# ============================================================

class FeishuNotifier:
    """飞书Webhook消息推送器"""

    def __init__(self, config: dict):
        self.webhook_url = config.get("webhook_url", "")
        self.secret      = config.get("secret", "")
        self.retry_times = config.get("retry_times", 3)
        self.highlight_threshold = config.get("highlight_score_threshold", 3.5)

        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK_URL 未配置，推送功能将不可用")

    # ----------------------------------------------------------
    # 主推送入口
    # ----------------------------------------------------------
    def send_analysis_report(
        self,
        results: List[ComprehensiveFactorResult],
        run_time: Optional[str] = None,
        is_close: bool = False,  # True=收盘报告，False=盘中报告
    ) -> bool:
        """
        发送完整分析报告
        1. 先发总结卡片
        2. 再发各高优先级基金的详情卡片
        """
        if not self.webhook_url:
            logger.error("飞书Webhook未配置，跳过推送")
            return False

        if run_time is None:
            run_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        period_label = "收盘分析" if is_close else "盘中分析"
        all_ok = True

        # Step 1: 发送总结卡片
        summary_card = self._build_summary_card(results, run_time, period_label)
        ok1 = self._send_card(summary_card)
        if not ok1:
            logger.error("总结卡片发送失败")
            all_ok = False

        # Step 2: 发送高优先级基金详情卡片
        highlight_funds = [r for r in results if r.final_score >= self.highlight_threshold]
        for fund_result in highlight_funds[:5]:  # 最多发5张详情卡片
            detail_card = self._build_fund_detail_card(fund_result, run_time)
            ok2 = self._send_card(detail_card)
            if not ok2:
                logger.warning(f"[{fund_result.fund_code}] 详情卡片发送失败")
            time.sleep(0.3)  # 避免频率限制

        return all_ok

    # ----------------------------------------------------------
    # 总结卡片
    # ----------------------------------------------------------
    def _build_summary_card(
        self,
        results: List[ComprehensiveFactorResult],
        run_time: str,
        period_label: str,
    ) -> dict:
        """构建总结卡片（一览所有基金信号）"""

        # 统计信号分布
        signal_counts = {"strong_buy": 0, "buy": 0, "hold": 0, "reduce": 0, "sell": 0}
        for r in results:
            signal_counts[r.signal] = signal_counts.get(r.signal, 0) + 1

        # 卡片头部
        header = {
            "template": "blue",
            "title": {
                "tag":     "plain_text",
                "content": f"📊 基金量化监控 {period_label} · {run_time}",
            }
        }

        # 信号分布概览
        overview_text = (
            f"🔥 强买{signal_counts['strong_buy']} · "
            f"✅ 加仓{signal_counts['buy']} · "
            f"⏸️ 观望{signal_counts['hold']} · "
            f"⚠️ 减仓{signal_counts['reduce']} · "
            f"🔴 清仓{signal_counts['sell']}"
        )

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag":     "lark_md",
                    "content": f"**信号分布** | {overview_text}",
                }
            },
            {"tag": "hr"},
        ]

        # 每只基金一行
        for r in results:
            score_bar  = self._score_to_bar(r.final_score)
            signal_txt = f"{SIGNAL_EMOJI.get(r.signal, '')} {r.signal_label}"
            drawdown   = f"回撤{pct_str(r.fund_layer.drawdown_pct)}"
            momentum   = f"60D{pct_str(r.fund_layer.momentum_60d_pct)}"
            today_est  = r.market_layer.details.get("realtime_growth_pct")
            today_str  = f"今日估算{pct_str(today_est)}" if today_est is not None else ""

            # 组合成单行文本
            line = (
                f"**{r.fund_name}**（{r.fund_code}）\n"
                f"{score_bar} **{r.final_score:.2f}分** | {signal_txt} | {r.position_suggestion}\n"
                f"{drawdown} · {momentum}"
                + (f" · {today_str}" if today_str else "")
            )

            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": line}
            })
            elements.append({"tag": "hr"})

        # 底部说明
        elements.append({
            "tag": "note",
            "elements": [{
                "tag":     "plain_text",
                "content": "⚡ 评分0-5分 | 分档加仓策略 | 仅供参考，不构成投资建议",
            }]
        })

        return {
            "msg_type": "interactive",
            "card": {
                "header":   header,
                "elements": elements,
            }
        }

    # ----------------------------------------------------------
    # 单基金详情卡片
    # ----------------------------------------------------------
    def _build_fund_detail_card(
        self,
        r: ComprehensiveFactorResult,
        run_time: str,
    ) -> dict:
        """构建高优先级基金详情卡片"""

        header_color = HEADER_COLOR_MAP.get(r.signal, "blue")
        header = {
            "template": header_color,
            "title": {
                "tag":     "plain_text",
                "content": f"{SIGNAL_EMOJI.get(r.signal, '')} {r.fund_name}（{r.fund_code}） · {r.signal_label}",
            }
        }

        ml  = r.market_layer
        fl  = r.fund_layer

        elements = [
            # ---- 综合评分 ----
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": f"**综合评分**\n{r.final_score:.2f} / 5.00",
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": f"**操作建议**\n{r.position_suggestion}（置信度{r.confidence}）",
                        }
                    },
                ],
            },
            {"tag": "hr"},

            # ---- 市场层因子 ----
            {
                "tag": "div",
                "text": {
                    "tag":     "lark_md",
                    "content": "**📈 市场对比因子**（市场时机判断）",
                }
            },
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**PE百分位**\n"
                                f"{ml.pe_percentile:.0f}%（得分{ml.pe_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**股债性价比ERP**\n"
                                f"{ml.erp:.2f}%（得分{ml.erp_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**市场动量**\n"
                                f"MA20偏离{ml.details.get('deviation_ma20', 0):.1f}%（得分{ml.momentum_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**市场广度**\n"
                                f"上涨占比{ml.details.get('breadth_pct', 50):.0f}%（得分{ml.breadth_score:.1f}）"
                            ),
                        }
                    },
                ],
            },
            {"tag": "hr"},

            # ---- 基金层因子 ----
            {
                "tag": "div",
                "text": {
                    "tag":     "lark_md",
                    "content": "**🏦 基金自身因子**（基金当前状态）",
                }
            },
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**距高点回撤**\n"
                                f"{pct_str(fl.drawdown_pct)}（得分{fl.drawdown_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**近60日涨幅**\n"
                                f"{pct_str(fl.momentum_60d_pct)}（得分{fl.momentum_60d_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**近20日涨幅**\n"
                                f"{pct_str(fl.momentum_20d_pct)}（得分{fl.momentum_20d_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**90日夏普**\n"
                                f"{fl.sharpe_90d:.2f}（得分{fl.sharpe_score:.1f}）"
                            ),
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag":     "lark_md",
                            "content": (
                                f"**相对基准超额**\n"
                                f"{pct_str(fl.relative_strength)}（得分{fl.relative_score:.1f}）"
                            ),
                        }
                    },
                ],
            },
            {"tag": "hr"},

            # ---- 买入理由 ----
            {
                "tag": "div",
                "text": {
                    "tag":     "lark_md",
                    "content": "**📌 主要理由**\n" + "\n".join(f"· {reason}" for reason in r.top_reasons),
                }
            },
        ]

        # 风险提示（如果有）
        if r.risk_warnings:
            elements.append({
                "tag": "div",
                "text": {
                    "tag":     "lark_md",
                    "content": "**🚨 风险提示**\n" + "\n".join(r.risk_warnings),
                }
            })

        elements.append({
            "tag": "note",
            "elements": [{
                "tag":     "plain_text",
                "content": f"计算时间: {r.calc_time} | 仅供参考",
            }]
        })

        return {
            "msg_type": "interactive",
            "card": {
                "header":   header,
                "elements": elements,
            }
        }

    # ----------------------------------------------------------
    # 低层发送
    # ----------------------------------------------------------
    def _send_card(self, card: dict) -> bool:
        """带重试的卡片发送"""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        url = self.webhook_url

        # 签名（若配置了 secret）
        if self.secret:
            timestamp = str(int(time.time()))
            sign      = self._gen_sign(timestamp)
            card["timestamp"] = timestamp
            card["sign"]      = sign

        for attempt in range(self.retry_times):
            try:
                resp = requests.post(
                    url,
                    data=json.dumps(card, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                if result.get("code") == 0 or result.get("StatusCode") == 0:
                    logger.info("飞书卡片发送成功")
                    return True
                else:
                    logger.warning(f"飞书返回错误: {result}")
            except Exception as e:
                logger.warning(f"飞书发送失败（第{attempt+1}次）: {e}")
                if attempt < self.retry_times - 1:
                    time.sleep(1.5)
        return False

    def _gen_sign(self, timestamp: str) -> str:
        """飞书签名计算"""
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    @staticmethod
    def _score_to_bar(score: float, max_score: float = 5.0, width: int = 5) -> str:
        """将评分转为emoji进度条"""
        filled = round(score / max_score * width)
        return "🟩" * filled + "⬜" * (width - filled)
