"""Pydantic Schema 导出"""

from backend.schemas.common import ApiResponse, PaginatedData, PaginatedResponse
from backend.schemas.fund import FundCreate, FundUpdate, FundOut
from backend.schemas.factor import FactorCreate, FactorUpdate, FactorOut
from backend.schemas.push_channel import PushChannelCreate, PushChannelUpdate, PushChannelOut
from backend.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleOut
from backend.schemas.report_config import ReportConfigOut, ReportConfigUpdate
from backend.schemas.analysis import FactorScore, AnalysisResultOut
from backend.schemas.ai import ChatMessage, ChatResponse
from backend.schemas.system_config import AIConfigUpdate, AIConfigOut

__all__ = [
    # Common
    "ApiResponse",
    "PaginatedData",
    "PaginatedResponse",
    # Fund
    "FundCreate",
    "FundUpdate",
    "FundOut",
    # Factor
    "FactorCreate",
    "FactorUpdate",
    "FactorOut",
    # PushChannel
    "PushChannelCreate",
    "PushChannelUpdate",
    "PushChannelOut",
    # Schedule
    "ScheduleCreate",
    "ScheduleUpdate",
    "ScheduleOut",
    # ReportConfig
    "ReportConfigOut",
    "ReportConfigUpdate",
    # Analysis
    "FactorScore",
    "AnalysisResultOut",
    # AI
    "ChatMessage",
    "ChatResponse",
    # SystemConfig
    "AIConfigUpdate",
    "AIConfigOut",
]
