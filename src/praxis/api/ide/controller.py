"""What every editor route needs before it can answer.

`STATUS` is the one place a workspace refusal becomes a status code. The kinds are
`modelstore.editing.workspace`'s; the codes are HTTP's, and keeping the mapping here is what stops
two routes disagreeing about what a rejected edit is.
"""

from contextlib import asynccontextmanager

from fastapi import HTTPException

from ...core import boards
from ...modelstore import StoreError, served_catalog
from ...modelstore.client import open_store
from ...modelstore.editing import impact, workspace

STATUS = {"ref": 409, "path": 400, "missing": 404, "rejected": 400, "store": 502}


def editable(ref: str) -> str:
    # One rule, in `workspace`, so the routes and the editor's tools cannot disagree about what
    # is editable.
    if refusal := workspace.editable(ref):
        raise HTTPException(STATUS[refusal.kind], refusal.reason)
    return ref


@asynccontextmanager
async def store():
    try:
        async with await open_store() as store:
            yield store
    except StoreError as exc:
        raise HTTPException(502, str(exc)) from None


async def broken_by(ref: str) -> impact.Impact:
    """Which saved queries this ref would break. Not `impact` — that is the module it calls, and
    a function of that name shadows it from inside its own body."""
    try:
        return impact.check(await served_catalog(ref=ref), await boards.saved_queries())
    except StoreError as exc:
        raise HTTPException(409, f"cannot read the model to check impact: {exc}") from None


# --- what is served -----------------------------------------------------------
