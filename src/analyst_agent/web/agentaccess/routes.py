"""The three access routes.

All three read `app.state.mcp` — the in-memory `Access` the request guard also tests — and hand it
to the controller. Read here rather than in the controller because it is request state, and a
controller that reached into `app.state` would be a controller that needs an app to be tested.
"""

from fastapi import APIRouter, Request

from . import controller
from .dto import AccessStatus, Enabled, RotatedToken, Toggle

router = APIRouter(prefix="/api/mcp")


@router.get("", response_model=AccessStatus)
async def read_access(request: Request) -> AccessStatus:
    """The switch, the token, the tool list, and a ready-made config for each kind of client.
    Refuses nothing — whether access is enabled is a field on the answer, not a status code."""
    return await controller.panel(
        request.app.state.mcp, request.app.state.mcp_server, str(request.base_url)
    )


@router.post("/enabled", response_model=Enabled)
async def set_access_enabled(body: Toggle, request: Request) -> Enabled:
    """Turn the MCP surface on or off, and store it.
    Takes effect on the next request: the guard reads the same in-memory copy this writes."""
    return await controller.set_enabled(request.app.state.mcp, body.enabled)


@router.post("/rotate", response_model=RotatedToken)
async def rotate_access_token(request: Request) -> RotatedToken:
    """Mint a new token and return it with both configs rebuilt around it.
    Invalidates every config already pasted anywhere — that is the point of it, not a side
    effect."""
    return await controller.rotate(request.app.state.mcp, str(request.base_url))
