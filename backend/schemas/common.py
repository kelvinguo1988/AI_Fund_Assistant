"""通用响应 Schema"""

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应格式"""
    code: int = 0          # 0=成功, 非0=错误码
    data: Optional[T] = None
    message: str = "success"


class PaginatedData(BaseModel, Generic[T]):
    """分页数据"""
    items: List[T]
    total: int
    page: int
    page_size: int


class PaginatedResponse(ApiResponse[PaginatedData[T]]):
    """分页响应"""
    pass
