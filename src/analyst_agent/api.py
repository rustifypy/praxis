from fastapi import FastAPI, Header
from pydantic import BaseModel

from .agent.build import SecurityContext, ask
from .cube.catalog import Catalog
from .cube.client import CubeClient, mint_token

app = FastAPI(title="analyst-agent")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    executed: list[dict]
    rejected: list[dict]


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(
    body: AskRequest,
    x_user_id: str | None = Header(default=None),
) -> AskResponse:
    ctx = SecurityContext(user_id=x_user_id or "anonymous")
    result = await ask(body.question, ctx)
    return AskResponse(answer=result.text, executed=result.executed, rejected=result.rejected)


@app.get("/catalog")
async def catalog_endpoint() -> dict:
    client = CubeClient(mint_token(SecurityContext().as_claims()))
    try:
        catalog = Catalog.from_meta(await client.meta())
        return {
            "entities": [
                {"name": e.name, "view": e.is_view, "members": len(e.members)}
                for e in catalog.entities
            ]
        }
    finally:
        await client.aclose()


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
