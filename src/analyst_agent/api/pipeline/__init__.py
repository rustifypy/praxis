"""Building the model: the four stages, the job running one, and picking the tables.

`/pipeline/tables` is the *warehouse's* tables, cached by the `read` stage — not
`cube.catalog.Catalog`, which is the served model and close to the opposite thing.

A job is subscribed to rather than awaited: a three-minute describe must not hold a request open,
and a page refresh re-attaches to the narration instead of losing it. That is why starting a stage
answers with a snapshot immediately and the progress arrives on `/job/stream`.
"""

from .routes import router

__all__ = ["router"]
