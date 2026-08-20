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
from .tools.query import build_tools

COMPACTION_TAG = "compaction"


async def build_agent(
    session: Session,
    dashboards: DashboardContext,
    checkpointer=None,
):
    """The agent behind a chat turn: the query tools, the dashboard tools, the sandbox, and the
    audit log.

    One agent, so `run_query` and a Python interpreter are in the same hands. What keeps the gate
    load-bearing is not a second agent but that **the sandbox holds no credential**: it cannot
    sign a Cube token and has no warehouse password, so there is no route through it to a number
    that did not come through the four tiers. What it adds is the arithmetic a `CubeQuery` cannot
    express — a ratio, a period delta, a share of a total — which was otherwise done in prose.
    `export_result` is the only way rows cross over.

    Async because the uploads it puts in the prompt are read from the sandbox service, which is
    the only reason building an agent touches the network at all.
    """
    model = build_model()
    return create_agent(
        model=model,
        tools=[
            *build_tools(session),
            *build_dashboard_tools(dashboards),
            *build_code_tools(session),
        ],
        system_prompt=build_system_prompt(
            always_rules=session.grounding.always_rules,
            skill_catalog=session.grounding.catalog_lines(),
            dashboards=True,
            sandbox=True,
            files="\n\n".join(p.as_prompt() for p in await profile.saved(session.chat_id))
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


def build_editor_agent(ctx: EditContext, branch: str, checkpointer=None):
    """The model editor. Its own agent and not a mode of the query one: they share no tools,
    and the only thing the editor may do to the warehouse is restart the container in front of
    it."""
    model = build_model()
    return create_agent(
        model=model,
        tools=build_editor_tools(ctx),
        system_prompt=build_editor_prompt(branch),
        middleware=[
            AuditLog(user="local"),
            ModelCallLimitMiddleware(run_limit=20, exit_behavior="end"),
            CompactionNotice(model=model.with_config(tags=[COMPACTION_TAG])),
        ],
        checkpointer=checkpointer,
    )
