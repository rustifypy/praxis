"""The pipeline as four steps, each resumable from what the last one stored."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.deployment import Deployment, DeploymentStore
from ...modelstore import model_files
from .. import definitions, evidence
from ..evidence import STAGES, STEPS
from . import build, decide, publish, read, selection
from .state import dump_schema, load_schema
from .warehouse import Schema

Report = Callable[[str, dict], object]
"""Progress sink, synchronous. `report("describe", {"done": 7, "total": 12, ...})`. The return
is `object` because the web's sink returns the `Event` it emitted; `_say` discards it, and
awaiting it would kill the job on its first progress call."""


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


# --- the catalog, read once and cached ---------------------------------------


async def read_catalog(store: DeploymentStore, report: Report | None = None) -> dict:
    """Read every schema through a throwaway container and cache it on the source row."""
    _say(report, "cubes", {"message": "introspecting — one throwaway Cube container"})
    catalog = await read.read_catalog(await _connection(store))
    if not catalog.schemas:
        raise StepFailed("the connection exposes no schemas")

    await store.save_state(
        {
            "catalog": {name: dump_schema(s) for name, s in catalog.schemas.items()},
            "catalog_notes": catalog.notes,
            "catalog_truncated": catalog.truncated,
        },
    )
    return {"schemas": catalog.summary(), "notes": catalog.notes, "truncated": catalog.truncated}


async def cached_catalog(store: DeploymentStore) -> dict[str, Schema]:
    state = await store.state()
    return {schema: load_schema(raw) for schema, raw in (state.get("catalog") or {}).items()}


# --- step 2: cubes -----------------------------------------------------------


async def cubes(
    store: DeploymentStore,
    schema_name: str,
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    """Emit and install the structural model for the selected tables."""
    # Reading is its own action so the picker has something to show before this runs,
    # but a first run with nothing cached should not fail with "read it first". One
    # place decides that, rather than each caller.
    if not await cached_catalog(store):
        await read_catalog(store, report)

    catalog = await cached_catalog(store)
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
    notes = list((await store.state()).get("catalog_notes") or [])
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
    validation = await publish.install(
        publish.build_files(emitted.bodies),
        members=False,
        message=f"cubes — {len(emitted.bodies)} cubes from {schema_name}",
    )
    if not validation.compiled:
        raise StepFailed(_rejected("the model", validation))

    await store.save_state(
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
    await store.save_step("cubes", _record(summary, notes))
    dropped = evidence.invalidated_by(
        evidence.of(definitions.read(await model_files())[0], await store.state()), "cubes"
    )
    await store.drop_steps(dropped)
    if dropped:
        notes.append(
            f"discarded {' and '.join(dropped)} — every model file was rewritten, and the "
            "commit before this one is what those steps had produced"
        )
    return StepResult(summary={**summary, "invalidated": dropped}, notes=notes)


# --- step 3: describe --------------------------------------------------------


async def describe(store: DeploymentStore, report: Report | None = None) -> StepResult:
    """Descriptions, measures and ai_context, then install and repair what Cube refuses."""
    schema, bodies = await _built(store)

    _say(report, "describe", {"message": "reading sample values"})
    menu = await build.build_menu(schema)

    total = len(schema.tables)
    enrichment = decide.Enrichment()
    for index, table in enumerate(schema.tables, 1):
        _say(report, "describe", {"done": index - 1, "total": total, "table": table.name})
        await decide.describe_table(table, menu, enrichment)
    _say(report, "describe", {"done": total, "total": total})

    _say(report, "describe", {"message": "installing, validating every measure"})
    validation = await publish.install(
        publish.build_files(bodies, enrichment),
        every_measure=True,
        enrichment=enrichment,
        message=f"describe — {len(bodies)} cubes",
    )
    if not validation.compiled:
        raise StepFailed(_rejected("the described model", validation))

    if validation.member_errors:
        _say(
            report,
            "describe",
            {"message": f"{len(validation.member_errors)} members failed — repairing"},
        )
        await decide.repair(enrichment, schema, validation.member_errors)
        validation = await publish.install(
            publish.build_files(bodies, enrichment),
            every_measure=True,
            enrichment=enrichment,
            message=f"describe — repaired {len(enrichment.repaired)} members",
        )

    await store.save_state({"enrichment": enrichment.model_dump()})
    summary = {
        **enrichment.counts,
        "repaired": len(enrichment.repaired),
        "dropped": len(enrichment.dropped),
    }
    await store.save_step(
        "describe",
        _record(summary, enrichment.rejected, validation.member_errors),
    )
    dropped = evidence.invalidated_by(
        evidence.of(definitions.read(await model_files())[0], await store.state()), "describe"
    )
    await store.drop_steps(dropped)
    return StepResult(
        summary={**summary, "invalidated": dropped},
        notes=enrichment.rejected,
        errors=validation.member_errors,
    )


# --- step 4: views -----------------------------------------------------------


async def views(store: DeploymentStore, report: Report | None = None) -> StepResult:
    """Propose views over the join paths, install them, and probe across the joins."""
    schema, bodies = await _built(store)
    state = await store.state()
    if "enrichment" not in state:
        raise StepFailed("run the describe step first — views are built from its decisions")
    enrichment = decide.Enrichment.model_validate(state["enrichment"])

    _say(report, "views", {"message": "choosing views over the join paths"})
    roots = build.view_roots(schema)
    await decide.propose_views(roots, enrichment)

    _say(
        report,
        "views",
        {"message": f"installing {len(enrichment.views)} views, probing across joins"},
    )
    validation = await publish.install(
        publish.build_files(bodies, enrichment),
        every_measure=True,
        enrichment=enrichment,
        message=f"views — {len(enrichment.views)} views",
    )
    if not validation.compiled:
        raise StepFailed(_rejected("the views", validation))

    await store.save_state({"enrichment": enrichment.model_dump()})
    summary = {
        "views": len(enrichment.views),
        "names": [v.name for v in enrichment.views],
        "entities": len(validation.entities),
    }
    await store.save_step("views", _record(summary, enrichment.rejected, validation.member_errors))
    return StepResult(summary=summary, notes=enrichment.rejected, errors=validation.member_errors)


# --- shared -------------------------------------------------------------------


async def _connection(store: DeploymentStore) -> Deployment:
    """The stored deployment, or the message that says to make one."""
    deployment = await store.get()
    if deployment is None:
        raise StepFailed("no database is connected yet")
    return deployment


def _rejected(what: str, validation: publish.Validation) -> str:
    """Cube's refusal, and where the model it refused is still readable. The served model has
    not moved — that is the point of the candidate — so the message has to say where to look."""
    where = (
        f"\n\nIt is still readable at the {validation.candidate!r} ref; the served model has "
        "not changed."
        if validation.candidate
        else ""
    )
    return f"Cube rejected {what}:\n{validation.compile_error}{where}"


async def _built(store: DeploymentStore) -> tuple[Schema, dict[str, dict]]:
    """The schema and cube bodies step 2 stored, or a message saying to run it."""
    state = await store.state()
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


async def action(
    name: str,
    store: DeploymentStore,
    *,
    schema: str = "public",
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    """Run one action by name, recording success or failure on the source row.

    Nothing here commits: `publish.install` is the only thing that writes the model, and it
    commits to the candidate whether Cube accepts it or not. A step that fails leaves the
    served model exactly as it was, which is why there is no rollback to arrange.
    """
    if name not in STAGES:
        raise StepFailed(f"unknown action {name!r} — one of {', '.join(STAGES)}")

    # A step may only run once the one before it has succeeded. Enforced here rather than in
    # the browser, which is advice — and shipped to it, so the advice cannot say otherwise.
    found = evidence.of(definitions.read(await model_files())[0], await store.state())
    if blocker := found.blocked(name):
        raise StepFailed(f"run {blocker} first")

    try:
        if name == "read":
            result = StepResult(summary=await read_catalog(store, report))
        elif name == "cubes":
            result = await cubes(store, schema, tables, report)
        elif name == "describe":
            result = await describe(store, report)
        elif name == "views":
            result = await views(store, report)
        else:
            # The membership check above accepts anything in STAGES, so a name added there
            # without a branch here has to be refused rather than fall through to `views`.
            raise StepFailed(f"{name!r} is a stage with no branch in `action`")
    except StepFailed as exc:
        if name in STEPS:
            await store.save_step(name, _record({}, failure=str(exc)))
        raise
    return result


async def generate(
    store: DeploymentStore,
    schema: str = "public",
    tables: list[str] | None = None,
    enrich: bool = False,
    report: Report | None = None,
) -> dict[str, StepResult]:
    """Every action in order. Stops at the first failure."""
    planned = ["read", "cubes", *(["describe", "views"] if enrich else [])]
    return {
        name: await action(name, store, schema=schema, tables=tables, report=report)
        for name in planned
    }
