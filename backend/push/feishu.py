"""飞书 Webhook 推送"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx

from backend.push.base import BasePush

logger = logging.getLogger(__name__)


def _gen_sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人「加签」校验值

    飞书约定：以 f"{timestamp}\n{secret}" 为 key、空串为消息体做 HMAC-SHA256 后 base64。
    """
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FeishuPush(BasePush):
    """飞书 Webhook 推送实现

    支持发送文本消息和卡片消息。
    """

    def __init__(self, webhook_url: str, secret: Optional[str] = None) -> None:
        self.webhook_url = webhook_url
        self.secret = secret

    async def send(self, content: str, title: Optional[str] = None) -> bool:
        """发送飞书卡片消息

        Args:
            content: 消息内容（Markdown 格式）
            title: 消息标题

        Returns:
            是否发送成功
        """
        if not self.webhook_url:
            logger.warning("飞书 Webhook URL 为空，跳过推送")
            return False

        card_content = self._build_card(content, title)
        return await self._post(card_content)

    async def send_test(self) -> bool:
        """发送测试消息"""
        return await self.send(
            content="这是一条测试消息，来自基金量化交易系统。",
            title="推送测试",
        )

    async def send_market_overview(self, report_markdown: str) -> bool:
        """发送市场概况卡片

        Args:
            report_markdown: 市场概况 Markdown

        Returns:
            是否发送成功
        """
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "📊 市场全景概览",
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": self._simplify_markdown(report_markdown),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": "基金量化交易系统自动推送，仅供参考",
                            }
                        ],
                    },
                ],
            },
        }
        return await self._post(card)

    async def send_analysis_report(
        self,
        fund_name: str,
        fund_code: str,
        signal_direction: str,
        weighted_score: float,
        report_markdown: str,
    ) -> bool:
        """发送分析报告卡片

        Args:
            fund_name: 基金名称
            fund_code: 基金代码
            signal_direction: 信号方向
            weighted_score: 加权评分
            report_markdown: 报告 Markdown 内容

        Returns:
            是否发送成功
        """
        # 信号颜色
        color_map = {
            "buy": "red",
            "sell": "green",
            "hold": "grey",
        }
        signal_label_map = {
            "buy": "买入🔴",
            "sell": "卖出🟢",
            "hold": "观望⚪",
        }

        signal_color = color_map.get(signal_direction, "grey")
        signal_label = signal_label_map.get(signal_direction, signal_direction)

        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📊 {fund_name}({fund_code}) 量化分析",
                    },
                    "template": signal_color,
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**信号方向**: {signal_label}\n**综合评分**: {weighted_score}（-6.0 ~ +6.0）",
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": self._simplify_markdown(report_markdown),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": "基金量化交易系统自动推送，仅供参考",
                            }
                        ],
                    },
                ],
            },
        }

        return await self._post(card)

    def _build_card(self, content: str, title: Optional[str] = None) -> dict:
        """构建简单卡片消息"""
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": content,
                },
            }
        ]

        card: dict = {
            "msg_type": "interactive",
            "card": {
                "elements": elements,
            },
        }

        if title:
            card["card"]["header"] = {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
            }

        return card

    def _simplify_markdown(self, md: str) -> str:
        """简化 Markdown 以适配飞书 lark_md 格式"""
        # 飞书 lark_md 不支持 # 标题，用加粗代替
        content = md.replace("### ", "**").replace("## ", "**").replace("# ", "**")
        # 移除表格分隔行
        content = "\n".join(
            line for line in content.split("\n")
            if not line.strip().startswith("|--") and not line.strip().startswith("| ---")
        )
        # 限制长度
        if len(content) > 3000:
            content = content[:3000] + "\n...（内容过长，已截断）"
        return content

    async def _post(self, payload: dict) -> bool:
        """发送 HTTP POST 请求到飞书 Webhook

        失败重试 1 次（间隔 1s，飞书机器人限频 100/min，瞬时抖动居多）；
        最终失败写 error_logs 埋点，便于在系统页排查断供原因。
        """
        from backend.services.error_log_service import log_source_failure

        last_err = ""
        for attempt in (1, 2):
            try:
                body = payload
                if self.secret:
                    # 配置了签名密钥就必须带签名，否则飞书端返回 19021 校验失败（此前静默不生效）
                    ts = int(time.time())
                    body = {
                        **payload,
                        "timestamp": str(ts),
                        "sign": _gen_sign(self.secret, ts),
                    }
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        self.webhook_url,
                        json=body,
                        headers={"Content-Type": "application/json"},
                    )
                    result = response.json()

                if result.get("code") == 0 or result.get("StatusCode") == 0:
                    logger.info("飞书推送成功" if attempt == 1 else "飞书推送成功（重试后）")
                    return True
                last_err = f"飞书推送失败: {result}"
                logger.warning(f"{last_err}（第 {attempt} 次）")
            except httpx.TimeoutException:
                last_err = "飞书推送超时"
                logger.warning(f"{last_err}（第 {attempt} 次）")
            except Exception as e:
                last_err = f"飞书推送异常: {type(e).__name__}: {e}"
                logger.warning(f"{last_err}（第 {attempt} 次）", exc_info=True)
            if attempt == 1:
                await asyncio.sleep(1.0)

        logger.error(last_err)
        log_source_failure(
            module="push.feishu", message=last_err[:300], category="network"
        )
        return False
