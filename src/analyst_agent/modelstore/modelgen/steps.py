import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...core import deployment
from .. import definitions, evidence
from ..client import model_files
from ..evidence import STAGES, STEPS
from . import build, decide, publish, read, selection
from .warehouse import Schema, dump_schema, load_schema

# Synchronous progress sink. Returns `object` because the web's sink returns the Event it
# emitted; `_say` discards it, and awaiting it would kill the job on its first progress call.
Report = Callable[[str, dict], object]


class StepFailed(RuntimeError):
    # The message is shown to the user verbatim.
    pass


@dataclass
class StepResult:
    summary: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Per-member problems Cube reported, which are non-fatal: a model that compiles with fewer
    # measures beats one that breaks at query time.
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _say(report: Report | None, stage: str, payload: dict) -> None:
    if report is not None:
        report(stage, payload)


# --- the catalog, read once and cached ---------------------------------------


async def read_catalog(report: Report | None = None) -> dict:
    _say(report, "cubes", {"message": "introspecting — probing the connected deployment"})
    connected = await deployment.current()
    if connected is None:
        raise StepFailed("no database is connected yet")
    catalog = await read.read_catalog(connected)
    if not catalog.schemas:
        raise StepFailed("the connection exposes no schemas")

    await deployment.save_state(
        {
            "catalog": {name: dump_schema(schema) for name, schema in catalog.schemas.items()},
            "catalog_notes": catalog.notes,
            "catalog_truncated": catalog.truncated,
        },
    )
    return {"schemas": catalog.summary(), "notes": catalog.notes, "truncated": catalog.truncated}


async def cached_catalog() -> dict[str, Schema]:
    state = await deployment.state()
    return {schema: load_schema(raw) for schema, raw in (state.get("catalog") or {}).items()}


# --- step 2: cubes -----------------------------------------------------------


async def cubes(
    schema_name: str,
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    # Reading is its own action so the picker has something to show before this runs, but a
    # first run with nothing cached should not fail with "read it first". Decided here rather
    # than by each caller.
    if not await cached_catalog():
        await read_catalog(report)

    catalog = await cached_catalog()
    if schema_name not in catalog:
        available = ", ".join(sorted(catalog)) or "none"
        raise StepFailed(f"schema {schema_name!r} was not read — available: {available}")
    full = catalog[schema_name]

    chosen = (
        sorted(table.name for table in full.tables)
        if tables is None
        else sorted(selection.closure(full, tables))
    )
    if not chosen:
        # Installing nothing would delete a working model and report success.
        raise StepFailed(f"no tables selected from {schema_name!r}")

    schema = selection.filter_schema(full, chosen)
    notes = list((await deployment.state()).get("catalog_notes") or [])
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
    # Descriptions rewrite and revalidate this model, so the per-member sweep waits until then
    # rather than running twice.
    validation = await publish.install(
        publish.build_files(emitted.bodies),
        members=False,
        message=f"cubes — {len(emitted.bodies)} cubes from {schema_name}",
    )
    if not validation.compiled:
        raise StepFailed(_rejected("the model", validation))

    await deployment.save_state(
        {
            "selection": {"schema": schema_name, "tables": chosen},
            "schema": dump_schema(schema),
            "bodies": emitted.bodies,
        },
    )
    summary = {
        "tables": len(chosen),
        "cubes": len(emitted.bodies),
        "keys": sum(1 for table in schema.tables if table.primary_key),
        "joins": sum(len(body.get("joins") or []) for body in emitted.bodies.values()),
        "entities": len(validation.entities),
        "schema": schema_name,
    }
    await deployment.save_step("cubes", _record(summary, notes))
    # Nothing is dropped from `steps` here any more. A stage is no longer run on its own: the
    # wizard runs cubes → describe → views in order, and a rebuild runs all of them again, so
    # there is no state in which a later step's record outlives the model it described.
    return StepResult(summary=summary, notes=notes)


# --- step 3: describe --------------------------------------------------------


async def describe(report: Report | None = None) -> StepResult:
    schema, bodies = await _built()

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

    await deployment.save_state({"enrichment": enrichment.model_dump()})
    summary = {
        **enrichment.counts,
        "repaired": len(enrichment.repaired),
        "dropped": len(enrichment.dropped),
    }
    await deployment.save_step(
        "describe",
        _record(summary, enrichment.rejected, validation.member_errors),
    )
    return StepResult(
        summary=summary,
        notes=enrichment.rejected,
        errors=validation.member_errors,
    )


# --- step 4: views -----------------------------------------------------------


async def views(report: Report | None = None) -> StepResult:
    schema, bodies = await _built()
    state = await deployment.state()
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

    await deployment.save_state({"enrichment": enrichment.model_dump()})
    summary = {
        "views": len(enrichment.views),
        "names": [view.name for view in enrichment.views],
        "entities": len(validation.entities),
    }
    await deployment.save_step(
        "views", _record(summary, enrichment.rejected, validation.member_errors)
    )
    return StepResult(summary=summary, notes=enrichment.rejected, errors=validation.member_errors)


# --- shared -------------------------------------------------------------------


def _rejected(what: str, validation: publish.Validation) -> str:
    # The served model has not moved — that is the point of the candidate — so the message has
    # to say where the refused YAML is still readable.
    where = (
        f"\n\nIt is still readable at the {validation.candidate!r} ref; the served model has "
        "not changed."
        if validation.candidate
        else ""
    )
    return f"Cube rejected {what}:\n{validation.compile_error}{where}"


async def _built() -> tuple[Schema, dict[str, dict]]:
    state = await deployment.state()
    if "schema" not in state or "bodies" not in state:
        raise StepFailed("run the cubes step first — nothing has been read from the database yet")
    return load_schema(state["schema"]), state["bodies"]


def _record(
    summary: dict,
    notes: list[str] | None = None,
    errors: dict | None = None,
    failure: str = "",
) -> dict:
    # One step's stored record, as the panel renders it. Written only from `action`, so a
    # failed step cannot be recorded two different ways.
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
    *,
    schema: str = "public",
    tables: list[str] | None = None,
    report: Report | None = None,
) -> StepResult:
    # Nothing here commits: `publish.install` is the only thing that writes the model, and it
    # commits to the candidate whether Cube accepts it or not. A step that fails leaves the
    # served model exactly as it was, so there is no rollback to arrange.
    if name not in STAGES:
        raise StepFailed(f"unknown action {name!r} — one of {', '.join(STAGES)}")

    # A step may only run once the one before it has succeeded. Enforced here rather than in
    # the browser, which is advice — and shipped to it, so the advice cannot say otherwise.
    found = evidence.of(definitions.read(await model_files())[0], await deployment.state())
    if blocker := found.blocked(name):
        raise StepFailed(f"run {blocker} first")

    try:
        if name == "read":
            result = StepResult(summary=await read_catalog(report))
        elif name == "cubes":
            result = await cubes(schema, tables, report)
        elif name == "describe":
            result = await describe(report)
        elif name == "views":
            result = await views(report)
        else:
            # The check above accepts anything in STAGES, so a name added there without a
            # branch here must be refused rather than fall through to `views`.
            raise StepFailed(f"{name!r} is a stage with no branch in `action`")
    except StepFailed as exc:
        if name in STEPS:
            await deployment.save_step(name, _record({}, failure=str(exc)))
        raise
    return result


async def generate(
    schema: str = "public",
    tables: list[str] | None = None,
    enrich: bool = False,
    report: Report | None = None,
) -> dict[str, StepResult]:
    # Every action in order, stopping at the first failure.
    planned = ["read", "cubes", *(["describe", "views"] if enrich else [])]
    return {
        name: await action(name, schema=schema, tables=tables, report=report) for name in planned
    }
