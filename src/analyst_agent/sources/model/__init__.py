"""Model generation: read the database, build cubes, ask the model, let Cube judge.

    warehouse   the vocabulary — Column, Table, Schema
    read        stage 1 — the probe container, into a Schema
    build       stage 2 — cube bodies and the menu of choices (pure, no LLM)
    decide      stage 3 — the LLM's descriptions, measures and views, guarded
    publish     stage 4 — YAML out, then write/restart/validate/restore
    run         the whole sequence, in order

`generate` is the entry point; the CLI only prints its Report.
"""

from . import build, decide, publish, read, warehouse
from .publish import Validation, restart, restore
from .run import Report, generate

__all__ = [
    "Report",
    "Validation",
    "build",
    "decide",
    "generate",
    "publish",
    "read",
    "restart",
    "restore",
    "warehouse",
]
