from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware

from ..dashboards.tools import DashboardContext, build_dashboard_tools
from ..sources.model.tools import EditContext, build_editor_tools
from .middleware import AuditLog, CompactionNotice
from .model import build_model
from .prompts import build_editor_prompt, build_system_prompt
from .tools import Session, build_tools


@dataclass
class SecurityContext:
    """Who is asking, for the audit trail. Signed into the JWT Cube requires."""

    user_id: str = "anonymous"

    def as_claims(self) -> dict:
        return {"user_id": self.user_id}


# The summarizing model runs inside the graph like any other call, so its tokens show
# up in the message stream. Tagged here, skipped there — otherwise a compaction would
# render into the transcript as if it were the agent's answer.
COMPACTION_TAG = "compaction"


def build_agent(
    session: Session,
    ctx: SecurityContext,
    dashboards: DashboardContext,
    checkpointer=None,
):
    """The agent behind a chat turn: the query tools, the dashboard tools, and the audit log."""
    model = build_model()
    return create_agent(
        model=model,
        tools=[*build_tools(session), *build_dashboard_tools(dashboards)],
        system_prompt=build_system_prompt(
            always_rules=session.grounding.always_rules,
            skill_catalog=session.grounding.catalog_lines(),
            dashboards=True,
        ),
        middleware=[
            AuditLog(user=ctx.user_id),
            # run_limit, not thread_limit: a thread is a whole conversation once chats
            # persist, and a thread limit would silently end one mid-chat.
            ModelCallLimitMiddleware(run_limit=20, exit_behavior="end"),
            CompactionNotice(model=model.with_config(tags=[COMPACTION_TAG])),
        ],
        checkpointer=checkpointer,
    )


def build_editor_agent(ctx: EditContext, source: str, branch: str, checkpointer=None):
    """The model editor. Its own agent and not a mode of the query one: they share no tools,
    and the only thing the editor may do to the warehouse is restart the container in front of
    it."""
    model = build_model()
    return create_agent(
        model=model,
        tools=build_editor_tools(ctx),
        system_prompt=build_editor_prompt(source, branch),
        middleware=[
            AuditLog(user="local"),
            ModelCallLimitMiddleware(run_limit=20, exit_behavior="end"),
            CompactionNotice(model=model.with_config(tags=[COMPACTION_TAG])),
        ],
        checkpointer=checkpointer,
    )
