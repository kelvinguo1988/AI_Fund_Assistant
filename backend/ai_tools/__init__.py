"""导入即注册内置工具。

工具注册依赖 data_tools 的模块副作用；此前只有 ai_agent 路由在函数内惰性 import，
调度器（ai_daily_brief）不经该路由时会拿到空注册表，Agent 便在零工具下静默生成简报。
"""

from backend.ai_tools import data_tools  # noqa: F401  触发 @tool 注册
