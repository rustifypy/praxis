"""Flows as something you build and something you run — two surfaces over one folder.

Read `flows/__init__.py` first: everything here is HTTP over that package, and the rules that
matter (what a definition file is, what a **Gate** permits, what fails a **Phase**) are its rules,
asked rather than restated. Three of them are worth knowing before touching a route:

**A Run is not a request.** `advance` streams while Phases run and returns at the next Gate, so
the browser calls it again to continue. `run.json` is the Run throughout — a closed tab loses a
stream, never a Run. The only thing that cannot survive that is an **approval**, which waits on an
in-memory queue inside a Phase; losing it fails that Phase, which is then re-run.

**Editing an artifact is narrow on purpose.** Reads are open across a Run because reading is how
someone understands what happened. Writes are permitted only in the output folder of the Phase the
Run is paused at, because that is the moment before the next Phase reads it — `controller.artifact`
is where both halves of that are decided.

**Two routers, and their registration order is load-bearing.** `/{flow}` would answer for
`/chats`, so `chats` is registered first in `server.py` — the same collision `onboarding` runs a
second router for.
"""

from .routes import chats, router

__all__ = ["chats", "router"]
