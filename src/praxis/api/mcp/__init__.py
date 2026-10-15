"""Agent access: what something else gets to call, and the switch that lets it.

The read-only half of the query agent's vocabulary — `search_model`, `run_query`, the served
model — behind a token of its own, so turning it off is one flag and rotating it does not touch
the portal's. Off by default: this opens the warehouse to whatever holds the token.

**The row lives in `core.access`.** It was here once, which made `web/` the only tier issuing
SQLAlchemy statements — so "what is stored about access" could only be answered by reading a
module of HTTP routes.

Four modules rather than three, because this noun has two surfaces. `routes`/`controller`/`dto` are
the panel the browser configures it from; `tools` is the MCP server itself, which is an entry point
and not a controller — nothing in the portal calls it, `server.py` mounts it.
"""

from .routes import router
from .tools import build

__all__ = ["build", "router"]
