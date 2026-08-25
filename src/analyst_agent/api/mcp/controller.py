"""Reading the access panel, flipping the switch, and rotating the token.

Every function takes the in-memory `Access` rather than loading the row: it is held on
`app.state` and written through, so the request guard tests one copy per request instead of
opening the database on every tool call. Which means these mutate that object *and* persist it,
and a caller that forgets the second half leaves a process disagreeing with its own database.

No `HTTPException` here. There is nothing to refuse — the panel is reachable whenever the portal
is, and whether access is *enabled* is a field on the answer rather than a status code.
"""

import json
import secrets

from ...core.access import Access, save
from .dto import AccessStatus, ClientConfig, Enabled, RotatedToken, ToolInfo


def config_for(access: Access, base_url: str) -> ClientConfig:
    """Where a client points, and the two ways of telling one so.

    `base_url` is the address this request arrived on and not `settings.web_host`: reached over a
    tunnel or a LAN address, the settings pair still says 127.0.0.1, which is a config that cannot
    work from wherever the person actually is.

    `mcp-remote` is the shim because Claude Desktop's own connectors expect OAuth while this takes
    a bearer token.
    """
    url = f"{base_url.rstrip('/')}/mcp"
    header = f"Authorization: Bearer {access.token}"
    desktop = {
        "mcpServers": {
            "analyst": {"command": "npx", "args": ["-y", "mcp-remote", url, "--header", header]}
        }
    }
    return ClientConfig(
        url=url,
        desktop=json.dumps(desktop, indent=2),
        cli=f'claude mcp add --transport http analyst {url} --header "{header}"',
    )


async def panel(access: Access, server, base_url: str) -> AccessStatus:
    """The whole panel. The tool list is read off the running server, never named again here."""
    tools = await server.list_tools()
    return AccessStatus(
        enabled=access.enabled,
        token=access.token,
        tools=[ToolInfo(name=tool.name, description=tool.description or "") for tool in tools],
        **config_for(access, base_url).model_dump(),
    )


async def set_enabled(access: Access, enabled: bool) -> Enabled:
    """Flip the switch and persist it. Off is the meaningful direction: on opens the warehouse to
    whatever holds the token."""
    access.enabled = enabled
    await save(access)
    return Enabled(enabled=access.enabled)


async def rotate(access: Access, base_url: str) -> RotatedToken:
    """Mint a new token and persist it.

    **Invalidates every config already pasted anywhere**, which is the point — it is how access is
    taken back, and the panel says so before the button is pressed.
    """
    access.token = secrets.token_urlsafe(24)
    await save(access)
    return RotatedToken(token=access.token, **config_for(access, base_url).model_dump())
