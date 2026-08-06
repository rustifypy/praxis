import logging

from langchain.agents.middleware import AgentMiddleware

log = logging.getLogger("analyst.audit")


class AuditLog(AgentMiddleware):
    """Records every tool call. Swap the log sink for your warehouse table."""

    def __init__(self, user: str = "anonymous"):
        super().__init__()
        self.user = user

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        log.info(
            "tool=%s user=%s args=%s",
            request.tool_call["name"],
            self.user,
            request.tool_call.get("args"),
        )
        return result

    async def awrap_tool_call(self, request, handler):
        """Required separately from the sync path — `ainvoke` never falls back to it."""
        result = await handler(request)
        log.info(
            "tool=%s user=%s args=%s",
            request.tool_call["name"],
            self.user,
            request.tool_call.get("args"),
        )
        return result
