import logging

from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware

from ..chat.events import Event, emit

log = logging.getLogger("analyst.audit")


class AuditLog(AgentMiddleware):
    """Records every tool call. Swap the log sink for your warehouse table."""

    def __init__(self, user: str = "anonymous"):
        super().__init__()
        self.user = user

    def _record(self, request) -> None:
        log.info(
            "tool=%s user=%s args=%s",
            request.tool_call["name"],
            self.user,
            request.tool_call.get("args"),
        )

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        self._record(request)
        return result

    async def awrap_tool_call(self, request, handler):
        """Separate from the sync path — `ainvoke` never falls back to it."""
        result = await handler(request)
        self._record(request)
        return result


def _notice(state, result) -> None:
    """`before_model` returns None unless it actually summarized — exact, not a guess."""
    if result is None:
        return
    emit(
        Event(
            type="compacted",
            data={
                "before": len(state["messages"]),
                # The rewritten list opens with a RemoveMessage sentinel.
                "after": max(len(result.get("messages", [])) - 1, 0),
            },
        )
    )


class CompactionNotice(SummarizationMiddleware):
    """Marks the point in the transcript where history was rewritten.

    Without the marker the browser and the model diverge in silence: the user scrolls a
    full conversation the agent can no longer see, then asks for "the same thing for
    last year" about a turn that has been summarized away.
    """

    def before_model(self, state, runtime):
        result = super().before_model(state, runtime)
        _notice(state, result)
        return result

    async def abefore_model(self, state, runtime):
        """Separate from the sync path — the async graph never falls back to it."""
        result = await super().abefore_model(state, runtime)
        _notice(state, result)
        return result
