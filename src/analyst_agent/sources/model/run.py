"""The pipeline as four steps, each resumable from what the last one stored.

    connect    start the container, wait for /readyz
    cubes      read the catalog, select tables, emit cubes, install
    describe   descriptions, measures and ai_context (LLM), install, repair
    views      the view set (LLM), install, validate every measure across joins

They run minutes or days apart, so nothing may survive only in memory. Each step
writes what the next needs into `sources.db` through `state`: the catalog, the selected
schema, the cube bodies, the enrichment decisions. `views` cannot be rebuilt from the
YAML on disk — `views_prompt` needs the join graph and the cube decisions, and YAML has
neither — which is what the stored state is for.

Every trip into the container still goes through `publish.install`, so the
write-restart-validate cycle is written once. `generate` runs all four in order for the
CLI; the portal calls them one at a time.

Progress goes out through `report`, called with a stage name and a dict. The CLI prints
it; the web turns it into an event. Neither is assumed here.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...config import settings
from .. import docker, health
from ..registry import Source, scaffold_source_dirs
from ..store import STEPS, SourceStore
from . import build, decide, publish, read, selection
from .state import dump_enrichment, dump_schema, load_enrichment, load_schema
from .warehouse import Schema

Report = Callable[[str, dict], None]
"""Progress sink, synchronous. `report("describe", {"done": 7, "total": 12, ...})`.

The web's sink pushes onto an in-memory buffer and the CLI's prints; neither needs to
await, and keeping it sync means the steps read as straight procedures."""


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
    """Create the container from this source's row and wait for Cube to say it is up.

    Recreating rather than starting is deliberate: a container's environment is fixed
    at creation, so this is also how an edited password takes effect.
    """
    model_dir = scaffold_source_dirs(source)
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
    """Read every schema through a throwaway container and cache it on the source.

    Separate from `cubes` because the picker sits between them: you cannot choose
    tables before seeing them, and re-choosing must not boot another container.
    """
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
    """Emit and install the structural model for the selected tables.

    `tables=None` takes everything in the schema. A selection is closed over foreign
    keys first, so picking a fact table brings its dimensions with it.
    """
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
    """Descriptions, measures and ai_context, then install and repair what Cube refuses.

    One model call per table, sequentially — this is the step that takes minutes, and
    the only one where per-item progress means anything.
    """
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
    """Propose views over the join paths, install them, and probe across the joins.

    Reads the cube decisions step 3 stored: a view exposes members by the names that
    step chose, so without them the offered includes would not match the model.
    """
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
    """One step's stored record, as the panel renders it.

    The only writer of this shape. `errors` are per-member problems from a step that
    finished; `failure` is the step not finishing at all. Both make the row red, and
    keeping them distinct is why the panel can say "1 measure dropped" rather than
    calling a usable model broken.
    """
    return {
        "status": "error" if errors or failure else "done",
        "finished_at": time.time(),
        "summary": summary,
        "notes": list(notes or []),
        "errors": dict(errors or {}),
        "error": failure,
    }


ACTIONS = ("connect", "read", "cubes", "describe", "views")
"""Everything a job can run. `read` is the odd one: it produces the catalog the picker
needs, so it must be triggerable on its own, but it records no step — the four in
`store.STEPS` are the ones with a row in the panel."""


async def action(
    name: str,
    source: Source,
    store: SourceStore,
    *,
    schema: str = "public",
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    """Run one action by name, recording success or failure on the source.

    The single dispatcher. The CLI and the portal both decide *which* actions to run and
    in what order; neither knows how to call one, so they cannot drift on the arguments,
    on the order enforcement, or on what a failed step looks like in the database.
    """
    if name not in ACTIONS:
        raise StepFailed(f"unknown action {name!r} — one of {', '.join(ACTIONS)}")

    # A step may only run once the one before it has succeeded. Enforced here rather
    # than in the browser, which is advice, not a guarantee.
    if name in STEPS:
        index = STEPS.index(name)
        if (
            index
            and (await store.steps(source.name)).get(STEPS[index - 1], {}).get("status") != "done"
        ):
            raise StepFailed(f"run {STEPS[index - 1]} first")

    try:
        if name == "connect":
            return await connect(source, store, report)
        if name == "read":
            return StepResult(summary=await read_catalog(source, store, report))
        if name == "cubes":
            return await cubes(source, store, schema, tables, report)
        if name == "describe":
            return await describe(source, store, report)
        return await views(source, store, report)
    except StepFailed as exc:
        if name in STEPS:
            await store.save_step(source.name, name, _record({}, failure=str(exc)))
        raise


async def generate(
    source: Source,
    store: SourceStore,
    schema: str = "public",
    tables: list[str] | None = None,
    enrich: bool = False,
    report: Report | None = None,
) -> dict[str, StepResult]:
    """Every action in order. Stops at the first failure.

    The portal runs them one at a time instead, because between reading the catalog and
    choosing tables there is a person.
    """
    planned = ["connect", "read", "cubes", *(["describe", "views"] if enrich else [])]
    return {
        name: await action(name, source, store, schema=schema, tables=tables, report=report)
        for name in planned
    }
