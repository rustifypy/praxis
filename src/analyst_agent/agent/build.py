from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, SummarizationMiddleware

from ..config import settings
from ..cube.catalog import Catalog
from ..cube.client import CubeClient, mint_token
from ..grounding import Grounding
from .middleware import AuditLog
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


async def ask(question: str, ctx: SecurityContext | None = None) -> Answer:
    ctx = ctx or SecurityContext()
    client = CubeClient(mint_token(ctx.as_claims()))
    try:
        catalog = Catalog.from_meta(await client.meta())
        grounding = Grounding(settings.grounding_dir)
        session = Session(
            catalog=catalog,
            client=client,
            grounding=grounding,
            max_rows=settings.max_rows,
        )
        model = build_model()
        agent = create_agent(
            model=model,
            tools=build_tools(session),
            system_prompt=build_system_prompt(
                always_rules=grounding.always_rules,
                skill_catalog=grounding.catalog_lines(),
            ),
            middleware=[
                AuditLog(user=ctx.user_id),
                ModelCallLimitMiddleware(thread_limit=20, exit_behavior="end"),
                SummarizationMiddleware(model=model),
            ],
        )
        result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]})
        return Answer(
            text=result["messages"][-1].content,
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
