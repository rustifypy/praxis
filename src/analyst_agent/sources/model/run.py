"""The pipeline as four steps, each resumable from what the last one stored."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...config import settings
from .. import docker, health
from ..registry import Source, scaffold_source_dirs
from ..store import STAGES, STEPS, SourceStore, blocked_by
from . import build, decide, publish, read, selection, vcs
from .state import dump_enrichment, dump_schema, load_enrichment, load_schema
from .warehouse import Schema

Report = Callable[[str, dict], None]
"""Progress sink, synchronous. `report("describe", {"done": 7, "total": 12, ...})`."""


class StepFailed(RuntimeError):
    """A step could not finish. The message is shown to the user verbatim."""


@dataclass
class StepResult:
    """What a step achieved, stored on the source and rendered as its summary line."""

    summary: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    """Per-member problems Cube reported. Present and non-fatal: a model that compiles
    with fewer measures beats one that breaks at query time."""

    @property
    def ok(self) -> bool:
        return not self.errors


def _say(report: Report | None, stage: str, payload: dict) -> None:
    if report is not None:
        report(stage, payload)


# --- step 1: connect ---------------------------------------------------------


async def connect(source: Source, store: SourceStore, report: Report | None = None) -> StepResult:
    """Create the container from this source's row and wait for Cube to say it is up."""
    model_dir = scaffold_source_dirs(source)
    await vcs.ensure(model_dir)
    _say(report, "connect", {"message": f"starting {source.service}"})

    created = await docker.create(
        source,
        model_dir,
        api_secret=settings.cube_api_secret,
        dev_mode=settings.cubejs_dev_mode,
    )
    if not created.ok:
        raise StepFailed(f"could not start {source.service}: {created.message}")

    _say(report, "connect", {"message": f"testing the connection via {source.url}/readyz"})
    result, attempts = await health.wait_until_ready(source)
    if not result.ok:
        # The container is left running on purpose. The logs are the diagnosis, and
        # throwing away what the user just typed to save them an `rm` is a bad trade.
        raise StepFailed(f"{source.name} is not ready: {result}")

    await store.save_step(
        source.name,
        "connect",
        _record({"status": result.status, "attempts": attempts, "driver": source.driver}),
    )
    return StepResult(summary={"status": result.status, "attempts": attempts})


# --- the catalog, read once and cached ---------------------------------------


async def read_catalog(source: Source, store: SourceStore, report: Report | None = None) -> dict:
    """Read every schema through a throwaway container and cache it on the source."""
    _say(report, "cubes", {"message": "introspecting — one throwaway Cube container"})
    catalog = await read.read_catalog(source)
    if not catalog.schemas:
        raise StepFailed("the connection exposes no schemas")

    await store.save_state(
        source.name,
        {
            "catalog": {name: dump_schema(s) for name, s in catalog.schemas.items()},
            "catalog_notes": catalog.notes,
            "catalog_truncated": catalog.truncated,
        },
    )
    return {"schemas": catalog.summary(), "notes": catalog.notes, "truncated": catalog.truncated}


async def cached_catalog(store: SourceStore, name: str) -> dict[str, Schema]:
    state = await store.state(name)
    return {schema: load_schema(raw) for schema, raw in (state.get("catalog") or {}).items()}


# --- step 2: cubes -----------------------------------------------------------


async def cubes(
    source: Source,
    store: SourceStore,
    schema_name: str,
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    """Emit and install the structural model for the selected tables."""
    # Reading is its own action so the picker has something to show before this runs,
    # but a first run with nothing cached should not fail with "read it first". One
    # place decides that, rather than each caller.
    if not await cached_catalog(store, source.name):
        await read_catalog(source, store, report)

    catalog = await cached_catalog(store, source.name)
    if schema_name not in catalog:
        available = ", ".join(sorted(catalog)) or "none"
        raise StepFailed(f"schema {schema_name!r} was not read — available: {available}")
    full = catalog[schema_name]

    chosen = (
        sorted(t.name for t in full.tables)
        if tables is None
        else sorted(selection.closure(full, tables))
    )
    if not chosen:
        # Installing nothing would delete a working model and report success.
        raise StepFailed(f"no tables selected from {schema_name!r}")

    schema = selection.filter_schema(full, chosen)
    notes = list((await store.state(source.name)).get("catalog_notes") or [])
    cut = selection.cut_joins(full, chosen)
    if cut:
        notes.append(f"joins dropped by the table selection: {', '.join(cut)}")
    if keyless := selection.keyless(schema):
        notes.append(
            f"no primary key on {', '.join(keyless)} — joins to these are omitted, "
            "since Cube rejects a joined cube without one"
        )

    emitted = build.emit_cubes(schema)
    notes += emitted.notes

    _say(report, "cubes", {"message": f"installing {len(emitted.bodies)} cubes"})
    # Descriptions rewrite and revalidate this model, so the per-member sweep waits
    # until then rather than running twice.
    validation = await publish.install(source, publish.build_files(emitted.bodies), members=False)
    if not validation.compiled:
        # The rejected model is left on disk rather than rolled back: the model directory
        # is version-controlled by whoever owns it, so `git checkout` is the way back and
        # it beats a one-deep copy. Leaving it also means the YAML Cube complained about
        # is still there to read.
        raise StepFailed(f"Cube rejected the model:\n{validation.compile_error}")

    await store.save_state(
        source.name,
        {
            "selection": {"schema": schema_name, "tables": chosen},
            "schema": dump_schema(schema),
            "bodies": emitted.bodies,
        },
    )
    summary = {
        "tables": len(chosen),
        "cubes": len(emitted.bodies),
        "keys": sum(1 for t in schema.tables if t.primary_key),
        "joins": sum(len(b.get("joins") or []) for b in emitted.bodies.values()),
        "entities": len(validation.entities),
        "schema": schema_name,
    }
    await store.save_step(source.name, "cubes", _record(summary, notes))
    dropped = await store.clear_steps_after(source.name, "cubes")
    if dropped:
        notes.append(
            f"discarded {' and '.join(dropped)} — every model file was rewritten, so "
            "`git diff` on the model directory is what those steps had produced"
        )
    return StepResult(summary={**summary, "invalidated": dropped}, notes=notes)


# --- step 3: describe --------------------------------------------------------


async def describe(source: Source, store: SourceStore, report: Report | None = None) -> StepResult:
    """Descriptions, measures and ai_context, then install and repair what Cube refuses."""
    schema, bodies = await _built(store, source.name)

    _say(report, "describe", {"message": "reading sample values"})
    menu = await build.build_menu(source, schema)

    total = len(schema.tables)
    enrichment = decide.Enrichment()
    for index, table in enumerate(schema.tables, 1):
        _say(report, "describe", {"done": index - 1, "total": total, "table": table.name})
        await decide.describe_table(table, menu, enrichment)
    _say(report, "describe", {"done": total, "total": total})

    _say(report, "describe", {"message": "installing, validating every measure"})
    validation = await publish.install(
        source,
        publish.build_files(bodies, enrichment),
        every_measure=True,
        enrichment=enrichment,
    )
    if not validation.compiled:
        raise StepFailed(f"Cube rejected the described model:\n{validation.compile_error}")

    if validation.member_errors:
        _say(
            report,
            "describe",
            {"message": f"{len(validation.member_errors)} members failed — repairing"},
        )
        await decide.repair(enrichment, schema, validation.member_errors)
        validation = await publish.install(
            source,
            publish.build_files(bodies, enrichment),
            every_measure=True,
            enrichment=enrichment,
        )

    await store.save_state(source.name, {"enrichment": dump_enrichment(enrichment)})
    summary = {
        **enrichment.counts,
        "repaired": len(enrichment.repaired),
        "dropped": len(enrichment.dropped),
    }
    await store.save_step(
        source.name,
        "describe",
        _record(summary, enrichment.rejected, validation.member_errors),
    )
    dropped = await store.clear_steps_after(source.name, "describe")
    return StepResult(
        summary={**summary, "invalidated": dropped},
        notes=enrichment.rejected,
        errors=validation.member_errors,
    )


# --- step 4: views -----------------------------------------------------------


async def views(source: Source, store: SourceStore, report: Report | None = None) -> StepResult:
    """Propose views over the join paths, install them, and probe across the joins."""
    schema, bodies = await _built(store, source.name)
    state = await store.state(source.name)
    if "enrichment" not in state:
        raise StepFailed("run the describe step first — views are built from its decisions")
    enrichment = load_enrichment(state["enrichment"])

    _say(report, "views", {"message": "choosing views over the join paths"})
    roots = build.view_roots(schema)
    await decide.propose_views(roots, enrichment)

    _say(
        report,
        "views",
        {"message": f"installing {len(enrichment.views)} views, probing across joins"},
    )
    validation = await publish.install(
        source,
        publish.build_files(bodies, enrichment),
        every_measure=True,
        enrichment=enrichment,
    )
    if not validation.compiled:
        raise StepFailed(f"Cube rejected the views:\n{validation.compile_error}")

    await store.save_state(source.name, {"enrichment": dump_enrichment(enrichment)})
    summary = {
        "views": len(enrichment.views),
        "names": [v.name for v in enrichment.views],
        "entities": len(validation.entities),
    }
    await store.save_step(
        source.name, "views", _record(summary, enrichment.rejected, validation.member_errors)
    )
    return StepResult(summary=summary, notes=enrichment.rejected, errors=validation.member_errors)


# --- shared -------------------------------------------------------------------


async def _built(store: SourceStore, name: str) -> tuple[Schema, dict[str, dict]]:
    """The schema and cube bodies step 2 stored, or a message saying to run it."""
    state = await store.state(name)
    if "schema" not in state or "bodies" not in state:
        raise StepFailed("run the cubes step first — nothing has been read from the database yet")
    return load_schema(state["schema"]), state["bodies"]


def _record(
    summary: dict,
    notes: list[str] | None = None,
    errors: dict | None = None,
    failure: str = "",
) -> dict:
    """One step's stored record, as the panel renders it."""
    return {
        "status": "error" if errors or failure else "done",
        "finished_at": time.time(),
        "summary": summary,
        "notes": list(notes or []),
        "errors": dict(errors or {}),
        "error": failure,
    }


WRITES_MODEL = ("cubes", "describe", "views")
"""The actions that rewrite the model directory, and so the ones that commit. Each commits pass
*or* fail: a model Cube rejected is left on disk deliberately, and an uncommitted tree is one
`git checkout` away from refusing to switch branches.
"""


async def action(
    name: str,
    source: Source,
    store: SourceStore,
    *,
    schema: str = "public",
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    """Run one action by name, recording success or failure on the source."""
    if name not in STAGES:
        raise StepFailed(f"unknown action {name!r} — one of {', '.join(STAGES)}")

    # A step may only run once the one before it has succeeded. Enforced here rather than in
    # the browser, which is advice — and shipped to it, so the advice cannot say otherwise.
    if blocker := blocked_by(await store.steps(source.name), name):
        raise StepFailed(f"run {blocker} first")

    model_dir = source.model_dir()
    if name in WRITES_MODEL:
        # Before the write, not after. A source built before any of this existed gets its
        # current model captured as the initial commit, so the rewrite about to happen is
        # recoverable — the hazard `publish.write` has carried all along.
        await vcs.ensure(model_dir)

    try:
        if name == "connect":
            result = await connect(source, store, report)
        elif name == "read":
            result = StepResult(summary=await read_catalog(source, store, report))
        elif name == "cubes":
            result = await cubes(source, store, schema, tables, report)
        elif name == "describe":
            result = await describe(source, store, report)
        else:
            result = await views(source, store, report)
    except StepFailed as exc:
        if name in STEPS:
            await store.save_step(source.name, name, _record({}, failure=str(exc)))
        if name in WRITES_MODEL:
            await vcs.commit(model_dir, f"{name} — failed: {exc}"[:200])
        raise

    # Both paths test `WRITES_MODEL`, rather than the success path relying on `connect` and
    # `read` having returned early — moving either return would have committed a catalog read.
    if name in WRITES_MODEL:
        detail = ", ".join(f"{k}={v}" for k, v in result.summary.items() if not isinstance(v, list))
        committed = await vcs.commit(model_dir, f"{name} — {detail}"[:200])
        # A failed commit must not fail a build that worked, but it must not be silent either:
        # someone who thinks they have history and does not is worse off than before.
        if not committed.ok:
            result.notes.append(f"not committed to the model repo: {committed.message}")
    return result


async def generate(
    source: Source,
    store: SourceStore,
    schema: str = "public",
    tables: list[str] | None = None,
    enrich: bool = False,
    report: Report | None = None,
) -> dict[str, StepResult]:
    """Every action in order. Stops at the first failure."""
    planned = ["connect", "read", "cubes", *(["describe", "views"] if enrich else [])]
    return {
        name: await action(name, source, store, schema=schema, tables=tables, report=report)
        for name in planned
    }
