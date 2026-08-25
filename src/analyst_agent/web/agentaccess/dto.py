"""What the access panel sends and receives.

`ClientConfig` is a base class rather than a nested field because the browser reads `url`,
`desktop` and `cli` as top-level keys on both answers — the panel shows the token beside the two
config blocks it appears inside, and nesting them would have been a wire change dressed as tidying.
"""

from pydantic import BaseModel


class Toggle(BaseModel):
    """The only request here: on or off."""

    enabled: bool


class ClientConfig(BaseModel):
    """Where an MCP client points, and the two ways of telling one so.

    Built per request from the address it arrived on, never from settings — see the controller.
    """

    url: str
    desktop: str
    cli: str


class ToolInfo(BaseModel):
    """One tool as the panel lists it. Read off the running server, never named a second time."""

    name: str
    description: str


class AccessStatus(ClientConfig):
    """The whole panel: the switch, the token, the tools, and where to point a client."""

    enabled: bool
    token: str
    tools: list[ToolInfo]


class RotatedToken(ClientConfig):
    """A fresh token, with the configs rebuilt around it.

    Both, so the panel can never show a new token beside a block still quoting the old one.
    """

    token: str


class Enabled(BaseModel):
    """The switch's own answer, echoed so the panel renders what was stored rather than what was
    clicked."""

    enabled: bool
