"""The sources surface: what exists, what is running, and one job at a time."""

import asyncio
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..cube.client import CubeError, catalog_of
from ..grounding import for_source
from ..sources import docker, health, jobs, model
from ..sources.drivers import (
    DRIVERS,
    LOCAL_HOSTS,
    LOCALHOST_NOTE,
    credentials_for,
    get,
    split_mounts,
)
from ..sources.model import edits, selection, vcs, workspace
from ..sources.model.run import StepFailed
from ..sources.registry import Source, next_port, scaffold_source_dirs, validate_new
from ..sources.store import STAGES, SourceStore, merge_credentials, public
from .stream import sse

router = APIRouter(prefix="/api")

HEALTH_TTL_S = 15.0
"""How long a /readyz result is reused. Each probe is an HTTP call per source, and a
status dot that lies is worse than one that is a few seconds stale."""
CONTAINER_ACTIONS = ("start", "stop", "restart")


class NewSource(BaseModel):
    name: str
    description: str = ""
    driver: str
    port: int = 0
    """0 asks for the next free port."""
    values: dict[str, str] = Field(default_factory=dict)
    """CUBEJS_DB_* as typed into the form."""


class EditSource(BaseModel):
    description: str | None = None
    values: dict[str, str] = Field(default_factory=dict)
    """A key with an empty value keeps what is stored — the form is never sent the
    password it would otherwise overwrite with blank."""


class RunStep(BaseModel):
    schema_name: str = "public"
    tables: list[str] | None = None
    """None models every table in the schema."""


async def init_state(app) -> None:
    """Called from the server's lifespan: open the store and the job runner."""
    store = SourceStore()
    await store.init()
    app.state.sources = store
    app.state.jobs = jobs.Runner()
    # name -> (monotonic time, probe). See HEALTH_TTL_S.
    app.state.health = {}


def store_of(request: Request) -> SourceStore:
    return request.app.state.sources


def runner_of(request: Request) -> jobs.Runner:
    return request.app.state.jobs


def invalidate(cache: dict, source: Source) -> None:
    """Forget the health probe for a source whose model just changed — nothing watches a
    container for changes. The served catalog needs no hook: it is cached on the Cube session
    for `CATALOG_TTL_S` and ages out on its own."""
    cache.pop(source.name, None)


# --- reading -----------------------------------------------------------------


async def _probe(cache: dict, source: Source, running: bool) -> dict:
    """Cached health and served entities: is it up, and what is it actually serving?"""
    if not running:
        return {"health": health.Health(False, "stopped").as_dict(), "serving": None}
    hit = cache.get(source.name)
    now = time.monotonic()
    if hit and now - hit[0] < HEALTH_TTL_S:
        return hit[1]

    result = await health.check(source, timeout=5.0)
    served = await health.serving(source) if result.ok else None
    probed = {"health": result.as_dict(), "serving": served and served.__dict__}
    cache[source.name] = (now, probed)
    return probed


async def _view(
    request: Request,
    source: Source,
    containers: dict[str, str],
    steps: dict,
    catalog_read: bool | None = None,
) -> dict:
    state = containers.get(source.service, "")
    running = state == "running"
    # Looked up unless the caller already has it. It used to default to False, which meant
    # every response but the list claimed `read` had not run — and now that the stepper
    # renders the shipped rail rather than deriving one, that default would erase a tick.
    if catalog_read is None:
        catalog_read = await store_of(request).catalog_read(source.name)
    probed = await _probe(request.app.state.health, source, running)
    body = public(
        source, steps=steps, running=running, health=probed["health"], catalog_read=catalog_read
    )
    body["serving"] = probed["serving"]
    body["container_state"] = state or "missing"
    # The checked-out branch is the model this source serves, so anywhere a query can be
    # issued has to be able to say which one. Empty until the directory is a repo.
    model_dir = source.model_dir()
    body["branch"] = await vcs.current(model_dir) if vcs.is_repo(model_dir) else ""
    return body


@router.get("/system")
async def system(request: Request) -> dict:
    """Whether this feature can work at all, in one call."""
    if not docker.installed():
        return {"ok": False, "reason": "docker is not installed on this machine"}
    info = await docker.daemon()
    if not info.ok:
        return {"ok": False, "reason": "Docker is not running — start Docker Desktop"}
    # Independent: each is a process spawn plus a daemon round trip, and the panel
    # refreshes this after every step.
    containers, cubestore, known = await asyncio.gather(
        docker.ours(), docker.cubestore_running(), store_of(request).names()
    )
    return {
        "ok": True,
        "docker": info.out.strip(),
        "cubestore": cubestore,
        "orphans": docker.orphans(containers, known),
        "cube_url": settings.cube_url,
        "dev_mode": settings.cubejs_dev_mode,
    }


@router.get("/drivers")
async def drivers() -> dict:
    """The connection form, described by the driver specs themselves."""
    return {
        "local_hosts": list(LOCAL_HOSTS),
        "localhost_note": LOCALHOST_NOTE,
        "drivers": [
            {
                "key": spec.key,
                "label": spec.label,
                "extracts_tables": key in model.read.DIALECTS,
                "fields": [
                    {
                        "env": f.env,
                        "label": f.prompt,
                        "default": f.default,
                        "secret": f.secret,
                        "required": f.required,
                        "boolean": f.boolean,
                        "mount_to": f.mount_to,
                        "help": f.comment,
                    }
                    for f in spec.fields
                    if not f.fixed
                ],
            }
            for key, spec in DRIVERS.items()
        ],
    }


@router.get("/sources")
async def list_sources(request: Request) -> dict:
    store = store_of(request)
    sources, steps, containers, read = await asyncio.gather(
        store.all(), store.all_steps(), docker.ours(), store.all_catalog_read()
    )
    # Concurrently: a cold cache costs each source a /readyz and a /v1/meta, and a
    # stopped-but-listening one can burn five seconds before the next even starts.
    views = await asyncio.gather(
        *(
            _view(request, s, containers, steps.get(s.name, {}), read.get(s.name, False))
            for s in sources
        )
    )
    # A blocking connect_ex per candidate port, off the event loop.
    port = await asyncio.to_thread(next_port, [s.port for s in sources])
    return {"sources": list(views), "next_port": port}


@router.get("/sources/{name}/logs")
async def source_logs(name: str, request: Request, tail: int = 200) -> dict:
    source = await _find(request, name)
    return {"container": source.service, "logs": await docker.logs(source.service, tail)}


@router.get("/sources/{name}/catalog")
async def source_catalog(name: str, request: Request) -> dict:
    """The cached catalog, for the picker. Never reads the database."""
    store = store_of(request)
    await _find(request, name)
    state = await store.state(name)
    catalog = {
        schema: model.state.load_schema(raw) for schema, raw in (state.get("catalog") or {}).items()
    }
    return {
        "read": bool(catalog),
        "truncated": bool(state.get("catalog_truncated")),
        "notes": state.get("catalog_notes") or [],
        "selection": state.get("selection") or {},
        "schemas": [
            {"schema": schema_name, "tables": selection.describe(schema)}
            for schema_name, schema in sorted(catalog.items())
        ],
    }


@router.get("/sources/{name}/model")
async def source_model(name: str, request: Request) -> dict:
    """What Cube has compiled, read live from this source's own `/v1/meta`."""
    source = await _find(request, name)
    # Which file defines each entity, so the panel that lists what Cube serves is also the
    # panel that edits it. Keyed by name and read from the working tree rather than assumed
    # from `cubes/<name>.yml`: a hand-edited model may put a cube anywhere.
    model_dir = source.model_dir()
    on_disk, _ = edits.read(workspace.files(source))
    branch = await vcs.current(model_dir) if vcs.is_repo(model_dir) else ""
    state = {
        "branch": branch,
        "editable": bool(branch) and branch != vcs.MAIN,
        "dirty": await vcs.status(model_dir) if branch else [],
    }

    try:
        catalog = await catalog_of(source)
    except CubeError as exc:
        # A stopped container does not stop the editing. The files are on this machine, and
        # the branch is still what a restart would load.
        return {
            "serving": False,
            "error": str(exc)[:400],
            "entities": [],
            "unloaded": sorted({e.file for e in on_disk.values()}),
            **state,
        }

    return {
        "serving": True,
        "error": "",
        **state,
        # Shipped rather than recomputed in the browser: "over cubes only, because a view's
        # members are the cube's re-exported" is a fact about the model, not about the panel.
        "counts": asdict(catalog.counts()),
        # Defined on disk but absent from what Cube serves. With the file watcher on this is
        # a file that failed to compile; with dev mode off it is simply one not loaded yet.
        "unloaded": sorted(
            {e.file for e in on_disk.values() if e.name not in {c.name for c in catalog.entities}}
        ),
        "entities": [
            {
                "name": e.name,
                "file": on_disk[e.name].file if e.name in on_disk else "",
                "is_view": e.is_view,
                "title": e.title,
                "description": e.description,
                "ai_context": e.ai_context,
                "folders": [asdict(f) for f in e.folders],
                "draws_from": catalog.draws_from(e),
                "exposed_by": catalog.exposed_by(e),
                "members": [
                    {
                        "id": m.id,
                        "name": m.id.rpartition(".")[2],
                        "kind": m.kind,
                        "type": m.type,
                        "title": m.title,
                        "description": m.description,
                        "ai_context": m.ai_context,
                        "granularities": m.granularities,
                        "operators": m.valid_operators,
                        "alias": m.alias_member,
                        "exposed_by": [] if e.is_view else catalog.exposure.get(m.id, []),
                    }
                    for m in e.members
                ],
            }
            for e in catalog.entities
        ],
    }


@router.get("/sources/{name}/grounding")
async def source_grounding(name: str, request: Request) -> dict:
    """The markdown this source grounds the agent with, exactly as the agent loads it."""
    source = await _find(request, name)
    loaded = for_source(source)
    kinds = (
        ("rule", loaded.rules),
        ("skill", loaded.skills),
        ("certified", loaded.certified),
    )
    return {
        "root": str(source.grounding_dir()),
        "exists": source.grounding_dir().is_dir(),
        "artefacts": [
            {
                "kind": kind,
                "name": artefact.name,
                "file": artefact.file,
                "description": artefact.description,
                # Rules only, and defaulted the way `always_rules` defaults it: an
                # untyped rule loads every turn, which is the expensive half.
                "loading": artefact.meta.get("type", "always") if kind == "rule" else "",
                "user_request": artefact.meta.get("user_request", ""),
                "body": artefact.body,
            }
            for kind, artefacts in kinds
            for artefact in artefacts
        ],
    }


@router.post("/sources/{name}/check")
async def check_now(name: str, request: Request) -> dict:
    """Probe now, ignoring `HEALTH_TTL_S`. Nothing else drops that entry without also
    starting or rebuilding something."""
    source = await _find(request, name)
    request.app.state.health.pop(name, None)
    steps = await store_of(request).steps(name)
    return await _view(request, source, await docker.ours(), steps)


@router.post("/sources/{name}/closure")
async def closure(name: str, body: RunStep, request: Request) -> dict:
    """What a tick-list actually models, and what it gives up."""
    # Only the requested schema is rebuilt: this fires on every checkbox tick, and
    # `cached_catalog` would reconstruct every Column of every schema each time.
    raw = (await store_of(request).state(name)).get("catalog") or {}
    if body.schema_name not in raw:
        raise HTTPException(404, f"schema {body.schema_name!r} has not been read")
    schema = model.state.load_schema(raw[body.schema_name])
    picked = body.tables or []
    chosen = sorted(selection.closure(schema, picked))
    return {
        "tables": chosen,
        "added": [t for t in chosen if t not in picked],
        "cut_joins": selection.cut_joins(schema, chosen),
        "keyless": [t for t in selection.keyless(schema) if t in chosen],
    }


# --- writing -----------------------------------------------------------------


def _paths(spec, values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Split host paths out of a form, refusing one that is not there."""
    kept, mounts = split_mounts(spec, values)
    for container_path, host_path in mounts.items():
        if not Path(host_path).is_file():
            field = next(f for f in spec.fields if f.mount_to == container_path)
            raise HTTPException(400, f"{field.prompt}: no file at {host_path}")
    return kept, mounts


@router.post("/sources")
async def create(body: NewSource, request: Request) -> dict:
    store = store_of(request)
    existing = await store.all()
    port = body.port or await asyncio.to_thread(next_port, [s.port for s in existing])

    # Validated on what was typed: a `mount_to` field is required as a host path, and
    # rewriting it to the container path first would hide a blank one.
    if error := validate_new(body.name, body.driver, port, body.values, existing):
        raise HTTPException(400, error)
    spec = get(body.driver)
    values, mounts = _paths(spec, body.values)

    source = Source(
        name=body.name,
        description=body.description,
        driver=body.driver,
        port=port,
        mounts=mounts,
        credentials=credentials_for(spec, values),
    )
    await store.add(source)
    scaffold_source_dirs(source)
    return await _view(request, source, await docker.ours(), steps={})


@router.patch("/sources/{name}")
async def edit(name: str, body: EditSource, request: Request) -> dict:
    """Change the description or the credentials."""
    store = store_of(request)
    source = await _find(request, name)
    merged = mounts = None
    if body.values:
        values, supplied = _paths(source.spec, body.values)
        merged = merge_credentials(source.credentials, values)
        # Only the paths actually retyped: blank means "unchanged" here too, and
        # replacing the map wholesale would drop a key file the form did not ask about.
        mounts = {**source.mounts, **supplied}
    await store.update(name, description=body.description, credentials=merged, mounts=mounts)
    request.app.state.health.pop(name, None)
    updated = await store.get(name)
    return await _view(request, updated, await docker.ours(), await store.steps(name))


@router.delete("/sources/{name}")
async def remove(name: str, request: Request, keep_model: bool = False) -> dict:
    """Remove the row, the container, and unless kept the model files."""
    source = await _find(request, name)
    if source.is_active:
        raise HTTPException(
            409,
            f"{name} is the source this portal queries (CUBE_URL={settings.cube_url}) — "
            "point CUBE_URL elsewhere and restart before removing it",
        )
    if runner_of(request).busy:
        raise HTTPException(409, "a job is running — wait for it to finish")

    await docker.remove(source.service)
    if not keep_model:
        shutil.rmtree(source.model_dir(), ignore_errors=True)
    await store_of(request).remove(name)
    request.app.state.health.pop(name, None)
    return {"removed": name}


@router.post("/sources/{name}/container/{action}")
async def control(name: str, action: str, request: Request) -> dict:
    """start / stop / restart the container, without touching the model."""
    if action not in CONTAINER_ACTIONS:
        raise HTTPException(
            404, f"no such action {action!r} — one of {', '.join(CONTAINER_ACTIONS)}"
        )
    source = await _find(request, name)
    result = await getattr(docker, action)(source.service)
    request.app.state.health.pop(name, None)
    if not result.ok:
        raise HTTPException(400, result.message or f"could not {action} {source.service}")
    steps = await store_of(request).steps(name)
    return await _view(request, source, await docker.ours(), steps)


@router.delete("/containers/{container}")
async def remove_orphan(container: str, request: Request) -> dict:
    """Delete a `cube-*` container that has no source row."""
    known = await store_of(request).names()
    if container not in docker.orphans(await docker.ours(), known):
        raise HTTPException(400, f"{container} is not an orphaned source container")
    result = await docker.remove(container)
    if not result.ok:
        raise HTTPException(400, result.message)
    return {"removed": container}


# --- the job -----------------------------------------------------------------


@router.get("/job")
async def current_job(request: Request) -> dict:
    """The running job and its buffered events, so a refresh re-attaches."""
    job = runner_of(request).job
    return job.snapshot() if job else {"id": None}


@router.post("/job/cancel")
async def cancel_job(request: Request) -> dict:
    return {"cancelled": runner_of(request).cancel()}


@router.get("/job/stream")
async def job_stream(request: Request) -> StreamingResponse:
    """SSE. Native EventSource on the browser side: a job is subscribed to, not posted."""
    return sse(runner_of(request).watch())


@router.post("/sources/{name}/actions/{name_of_action}")
async def run_action(name: str, name_of_action: str, body: RunStep, request: Request) -> dict:
    """Start a job that runs one action, and answer as soon as it exists."""
    if name_of_action not in STAGES:
        raise HTTPException(404, f"no such action {name_of_action!r} — one of {', '.join(STAGES)}")
    source = await _find(request, name)
    runner = runner_of(request)
    store = store_of(request)
    # Bound out here so the closure does not hold the finished request — and with it the
    # whole ASGI scope — alive for the minutes a describe takes.
    cache = request.app.state.health

    async def work(job: jobs.Job) -> None:
        job.current = name_of_action
        runner.emit("step_start", step=name_of_action)
        try:
            result = await model.run.action(
                name_of_action,
                source,
                store,
                schema=body.schema_name,
                tables=body.tables,
                report=lambda stage, payload: runner.emit("progress", step=stage, **payload),
            )
        except StepFailed as exc:
            runner.emit("step_end", step=name_of_action, ok=False, error=str(exc))
            # The container is left as it is — its logs are the diagnosis.
            runner.emit("log", message=await docker.logs(source.service, 40))
            raise
        finally:
            invalidate(cache, source)
            job.current = ""
        runner.emit(
            "step_end",
            step=name_of_action,
            ok=result.ok,
            summary=result.summary,
            notes=result.notes,
            errors=result.errors,
        )

    try:
        job = await runner.start(source.name, [name_of_action], work)
    except jobs.Busy as exc:
        raise HTTPException(409, f"a job is already running — {exc}") from None
    return job.snapshot()


# --- shared ------------------------------------------------------------------


async def _find(request: Request, name: str) -> Source:
    source = await store_of(request).get(name)
    if source is None:
        raise HTTPException(404, f"no source named {name!r}")
    return source
