"""Getting from nothing to a deployment that answers questions.

Four conditions, and conflating them is the mistake `CONTEXT.md` exists to prevent: **deployed**
(an address is stored) → **onboarded** (both are, plus a model) → *reachable* (something answers
and it is the right thing) → *serving* (Cube is compiling a model). Only the first two are
properties of the row; the other two cost a request.

`reachable.py` is here because this is the only thing that asks it. It speaks HTTP itself rather
than borrowing either service's client, since it is asked about an address that is not on the row
yet and a service that may not be there — see `docs/adr/0001`.
"""

from .routes import pipeline, router

__all__ = ["pipeline", "router"]
