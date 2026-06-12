"""Complaint app package (FastAPI-driven)."""

try:
    from utils.lang_util.conversation_router import register_agent
except Exception:  # pragma: no cover - tooling runtime compatibility
    register_agent = None

# 代理标识
AGENT_ID = "complaint_agent"

# 注册 agent 配置（仅用于会话列表查询，创建和发送消息使用各自的接口）
# complaint_agent 无 LangGraph 图
if callable(register_agent):
    register_agent(agent_id=AGENT_ID)

__all__: list[str] = ['AGENT_ID']
