from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware

from ..config import settings
from ..cube.catalog import Catalog
from ..cube.client import CubeClient, mint_token
from ..grounding import Grounding
from .content import text_of
from .middleware import AuditLog, CompactionNotice
from .model import build_model
from .prompts import build_system_prompt
from .tools import Session, build_tools


@dataclass
class SecurityContext:
    """Who is asking, for the audit trail. Signed into the JWT Cube requires."""

    user_id: str = "anonymous"

    def as_claims(self) -> dict:
        return {"user_id": self.user_id}


@dataclass
class Answer:
    text: str
    executed: list[dict]
    rejected: list[dict]


# The summarizing model runs inside the graph like any other call, so its tokens show
# up in the message stream. Tagged here, skipped there — otherwise a compaction would
# render into the transcript as if it were the agent's answer.
COMPACTION_TAG = "compaction"

_catalogs: dict[str, Catalog] = {}


async def load_catalog(client: CubeClient, refresh: bool = False) -> Catalog:
    """The catalog, cached per Cube.

    Cube Core does not hot-reload its model, so this only changes when a container
    restarts — which is exactly what the refresh flag is for. Refetching it per turn
    would put an HTTP round trip in front of every message in a chat.
    """
    key = settings.api_root
    if refresh or key not in _catalogs:
        _catalogs[key] = Catalog.from_meta(await client.meta())
    return _catalogs[key]


def forget_catalog() -> None:
    """Drop the cached catalog so the next turn refetches it.

    The cache lives for the life of the process, so a model rebuilt in the panel would
    otherwise stay invisible to the agent until a restart.
    """
    _catalogs.pop(settings.api_root, None)


def build_agent(session: Session, ctx: SecurityContext, checkpointer=None):
    """The agent, shared by `/ask`'s single shot and the web's streamed turn.

    Two construction sites would drift; middleware added for the browser would quietly
    not apply to the other. Pass a checkpointer to give the agent memory across turns —
    without one it behaves exactly as it always has.
    """
    model = build_model()
    return create_agent(
        model=model,
        tools=build_tools(session),
        system_prompt=build_system_prompt(
            always_rules=session.grounding.always_rules,
            skill_catalog=session.grounding.catalog_lines(),
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


async def ask(question: str, ctx: SecurityContext | None = None) -> Answer:
    """One question, no memory. The /ask endpoint."""
    ctx = ctx or SecurityContext()
    client = CubeClient(mint_token(ctx.as_claims()))
    try:
        session = Session(
            catalog=await load_catalog(client),
            client=client,
            grounding=Grounding(settings.grounding_dir),
            max_rows=settings.max_rows,
        )
        agent = build_agent(session, ctx)
        result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]})
        return Answer(
            text=text_of(result["messages"][-1].content),
            executed=[
                {
                    "query": r.query,
                    "sql": r.sql,
                    "rows": r.row_count,
                    "ms": r.duration_ms,
                    "warnings": r.warnings,
                }
                for r in session.executed
            ],
            rejected=session.rejected,
        )
    finally:
        await client.aclose()
