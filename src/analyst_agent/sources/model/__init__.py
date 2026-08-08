"""Model generation: read the database, build cubes, ask the model, let Cube judge.

    warehouse   the vocabulary — Column, Table, Schema
    read        stage 1 — the probe container, into a Catalog of every schema
    selection   which tables to model, as a filter on one Schema
    build       stage 2 — cube bodies and the menu of choices (pure, no LLM)
    decide      stage 3 — the LLM's descriptions, measures and views, guarded
    publish     stage 4 — YAML out, then write/restart/validate
    state       what one step leaves behind for the next, as JSON
    run         the actions, and `generate` for all of them in order

`run.action` is the one way to run a step: the CLI and the portal choose which, never
how, so they cannot drift on order enforcement or on what a failure records.
"""

from . import build, decide, publish, read, run, selection, state, warehouse
from .publish import Validation, restart
from .run import ACTIONS, StepFailed, StepResult, action, generate

__all__ = [
    "ACTIONS",
    "StepFailed",
    "StepResult",
    "Validation",
    "action",
    "build",
    "decide",
    "generate",
    "publish",
    "read",
    "restart",
    "run",
    "selection",
    "state",
    "warehouse",
]
