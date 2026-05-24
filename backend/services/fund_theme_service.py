"""天天基金相关主题抓取服务

从 fund.eastmoney.com/{code}.html 页面提取"相关主题基金"区的主题名称。
"""

import logging
import re

import requests

logger = logging.getLogger(__name__)

# 主题名称提取正则：匹配 relatedThemeFund 区中的 data-id=BKxxxx><span>主题名</span>
_THEME_PATTERN = re.compile(r'data-id=BK\d+><span>([^<]+)</span>')


def fetch_related_themes(fund_code: str) -> list[str]:
    """获取基金在天天基金上的相关主题名称列表

    Args:
        fund_code: 6 位基金代码，如 "025209"

    Returns:
        主题名称列表，如 ["半导体", "存储芯片"]；失败时返回空列表
    """
    url = f"https://fund.eastmoney.com/{fund_code}.html"
    headers = {
        "Referer": "http://fund.eastmoney.com",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            logger.warning("获取基金 %s 页面失败, status=%d", fund_code, resp.status_code)
            return []

        themes = _THEME_PATTERN.findall(resp.text)
        if not themes:
            logger.info("基金 %s 未找到相关主题", fund_code)
        else:
            logger.info("基金 %s 相关主题: %s", fund_code, themes)
        return themes
    except Exception as e:
        logger.warning("获取基金 %s 相关主题异常: %s", fund_code, e)
        return []
