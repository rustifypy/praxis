import logging

from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware

from ..core.events import Event, emit

log = logging.getLogger("analyst.audit")


class AuditLog(AgentMiddleware):
    def _record(self, request) -> None:
        log.info("tool=%s args=%s", request.tool_call["name"], request.tool_call.get("args"))

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        self._record(request)
        return result

    async def awrap_tool_call(self, request, handler):
        # Separate from the sync path: `ainvoke` never falls back to it.
        result = await handler(request)
        self._record(request)
        return result


def _notice(state, result) -> None:
    # `before_model` returns None unless it actually summarized, so this is exact.
    if result is None:
        return
    emit(
        Event(
            type="compacted",
            data={
                "before": len(state["messages"]),
                "after": max(len(result.get("messages", [])) - 1, 0),
            },
        )
    )


class CompactionNotice(SummarizationMiddleware):
    # Marks where history was rewritten: without it the reader scrolls a conversation the agent
    # can no longer see, then asks for "the same for last year" about a summarized turn.

    def before_model(self, state, runtime):
        result = super().before_model(state, runtime)
        _notice(state, result)
        return result

    async def abefore_model(self, state, runtime):
        # Separate from the sync path: the async graph never falls back to it.
        result = await super().abefore_model(state, runtime)
        _notice(state, result)
        return result
