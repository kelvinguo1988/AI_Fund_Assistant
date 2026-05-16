"""抽象推送接口"""

from abc import ABC, abstractmethod
from typing import Optional


class BasePush(ABC):
    """推送抽象基类"""

    @abstractmethod
    async def send(self, content: str, title: Optional[str] = None) -> bool:
        """发送消息

        Args:
            content: 消息内容
            title: 消息标题

        Returns:
            是否发送成功
        """
        ...

    @abstractmethod
    async def send_test(self) -> bool:
        """发送测试消息

        Returns:
            是否发送成功
        """
        ...
