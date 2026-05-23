"""飞书 Webhook 推送"""

import json
import logging
from typing import Optional

import httpx

from backend.push.base import BasePush

logger = logging.getLogger(__name__)


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
        """发送 HTTP POST 请求到飞书 Webhook"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                result = response.json()

                if result.get("code") == 0 or result.get("StatusCode") == 0:
                    logger.info("飞书推送成功")
                    return True
                else:
                    logger.error(f"飞书推送失败: {result}")
                    return False
        except httpx.TimeoutException:
            logger.error("飞书推送超时")
            return False
        except Exception as e:
            logger.error(f"飞书推送异常: {e}")
            return False
