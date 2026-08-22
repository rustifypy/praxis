from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

from ..core.llm import build_model
from ..sandbox import profile
from .middleware import AuditLog, CompactionNotice
from .prompts import build_editor_prompt, build_system_prompt
from .session import Session
from .tools.code import build_code_tools
from .tools.dashboards import DashboardContext, build_dashboard_tools
from .tools.editor import EditContext, build_editor_tools
from .tools.query import build_query_tools

COMPACTION_TAG = "compaction"


async def build_agent(
    session: Session,
    dashboards: DashboardContext,
    checkpointer=None,
):
    model = build_model()
    return create_agent(
        model=model,
        tools=[
            *build_query_tools(session),
            *build_dashboard_tools(dashboards),
            *build_code_tools(session),
        ],
        system_prompt=build_system_prompt(
            always_rules=session.grounding.always_rules,
            skill_catalog=session.grounding.catalog_lines(),
            dashboards=True,
            sandbox=True,
            files="\n\n".join(shape.as_prompt() for shape in await profile.saved(session.chat_id))
            if session.chat_id
            else "",
        ),
        middleware=[
            AuditLog(),
            ModelCallLimitMiddleware(run_limit=40, exit_behavior="end"),
            ToolCallLimitMiddleware(tool_name="run_python", run_limit=15, exit_behavior="continue"),
            CompactionNotice(model=model.with_config(tags=[COMPACTION_TAG])),
        ],
        checkpointer=checkpointer,
    )


def build_editor_agent(context: EditContext, ref: str, checkpointer=None):
    # Its own agent and not a mode of the query one: they share no tools.
    model = build_model()
    return create_agent(
        model=model,
        tools=build_editor_tools(context),
        system_prompt=build_editor_prompt(ref),
        middleware=[
            AuditLog(user="local"),
            ModelCallLimitMiddleware(run_limit=20, exit_behavior="end"),
            CompactionNotice(model=model.with_config(tags=[COMPACTION_TAG])),
        ],
        checkpointer=checkpointer,
    )
