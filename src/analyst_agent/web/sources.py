"""The data source: what it is, what it is serving, and editing its connection."""

import secrets
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.deployment import Deployment
from ..cube.client import CubeError, served_catalog
from ..datamodel import definitions, grounding, modelgen, vcs
from ..datamodel.deployenv import render as deployment_env
from ..datamodel.deployenv import render_sandbox as sandbox_env
from ..datamodel.drivers import (
    credentials_for,
    get,
    merge_credentials,
    missing_fields,
    split_mounts,
)
from ..datamodel.modelgen import selection
from ..datamodel.registry import model_dir, sandbox_dir, semantic_dir
from ..modelstore import StoreError, model_files
from .state import find, read, view

router = APIRouter(prefix="/api")

COMPOSE_FILE = "docker-compose.yml"


class NewSource(BaseModel):
    description: str = ""
    driver: str
    values: dict[str, str] = Field(default_factory=dict)


class EditSource(BaseModel):
    description: str | None = None
    store_url: str | None = None
    sandbox_url: str | None = None
    """Where each deployment ended up. Arriving here rather than at connect time is what makes
    "run it wherever you like" the same flow as "it moved". There is no Cube address: Cube is
    reached through the store, and where it is, is the store's own configuration."""
    values: dict[str, str] = Field(default_factory=dict)
    """A key with an empty value keeps what is stored — the form is never sent the
    password it would otherwise overwrite with blank."""


class TableSelection(BaseModel):
    """Which tables the picker is asking about. Its own model rather than the one the cubes
    action takes: the shape matches today, the question does not."""

    schema_name: str = "public"
    tables: list[str] | None = None


@router.get("/source")
async def get_source(request: Request) -> dict | None:
    """The connection, or null before one has been made."""
    store = request.app.state.sources
    deployment = await store.get()
    if deployment is None:
        return None
    return await view(request, deployment, await store.steps())


@router.get("/source/env")
async def source_env(request: Request) -> dict:
    """The environment file the deployment is run with, and where to put it.

    **The one route that returns credential values.** Everywhere else the rule is names only —
    but handing these over is the entire purpose of this one, since nothing can run Cube
    without them. It is behind the same token as the rest of the portal, and the alternative is
    retyping a password into a file.
    """
    deployment = await find(request)
    return {
        # Two deployments, so two files. The sandbox is separate on purpose — its own
        # directory, its own compose file, no shared network or secret.
        "env": deployment_env(deployment),
        "directory": str(semantic_dir()),
        "sandbox_env": sandbox_env(deployment),
        "sandbox_directory": str(sandbox_dir()),
        "compose": COMPOSE_FILE,
        "deployed": deployment.deployed,
    }


@router.get("/source/compose")
async def source_compose() -> dict:
    """`semantic/docker-compose.yml`, verbatim. Served rather than generated: a generated copy
    is a second deployment that can drift from the one that was tested."""
    path = semantic_dir() / COMPOSE_FILE
    if not path.is_file():
        raise HTTPException(404, f"{path} is missing")
    return {"name": COMPOSE_FILE, "text": path.read_text()}


@router.get("/source/catalog")
async def source_catalog(request: Request) -> dict:
    """The cached catalog, for the picker. Never reads the database."""
    store = request.app.state.sources
    current = await store.state()
    catalog = {
        schema: modelgen.state.load_schema(raw)
        for schema, raw in (current.get("catalog") or {}).items()
    }
    return {
        "read": bool(catalog),
        "truncated": bool(current.get("catalog_truncated")),
        "notes": current.get("catalog_notes") or [],
        "selection": current.get("selection") or {},
        "schemas": [
            {"schema": schema_name, "tables": selection.describe(schema)}
            for schema_name, schema in sorted(catalog.items())
        ],
    }


@router.get("/source/model")
async def source_model(request: Request) -> dict:
    """What Cube has compiled, read live from its own `/v1/meta`."""
    # Which file defines each entity, so the panel that lists what Cube serves is also the
    # panel that edits it. Keyed by name and read from the store rather than assumed from
    # `cubes/<name>.yml`: a hand-edited model may put a cube anywhere.
    repo = model_dir()
    try:
        stored = await model_files()
    except StoreError:
        # The same reason a stopped Cube does not empty this panel: one of the two halves being
        # unreachable must not hide the other. Without the store nothing names the file an
        # entity came from, and Cube can still say what it is serving.
        stored = {}
    on_disk, _ = definitions.read(stored)
    branch = await vcs.current(repo) if vcs.is_repo(repo) else ""
    editing_state = {
        "branch": branch,
        "editable": bool(branch) and branch != vcs.MAIN,
        "dirty": await vcs.status(repo) if branch else [],
    }

    try:
        catalog = await served_catalog()
    except CubeError as exc:
        # A stopped container does not stop the editing. The files are on this machine, and
        # the branch is still what a restart would load.
        return {
            "serving": False,
            "error": str(exc)[:400],
            "entities": [],
            "unloaded": sorted({e.file for e in on_disk.values()}),
            **editing_state,
        }

    return {
        "serving": True,
        "error": "",
        **editing_state,
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


@router.get("/source/grounding")
async def source_grounding(request: Request) -> dict:
    """The markdown the agent is grounded with, exactly as it loads it — main, since that is
    the ref a question chat is pinned to."""
    deployment = await request.app.state.sources.get()
    root = deployment.store_url if deployment else ""
    try:
        loaded = await grounding.load()
    except StoreError:
        # A store that is not answering is the one case where this panel is worth reading, so it
        # reports empty against the address rather than 500ing the data page.
        return {"root": root, "exists": False, "artefacts": []}
    kinds = (
        ("rule", loaded.rules),
        ("skill", loaded.skills),
        ("certified", loaded.certified),
    )
    return {
        "root": root,
        "exists": True,
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


@router.post("/source/check")
async def check_now(request: Request) -> dict:
    """Probe now, ignoring `HEALTH_TTL_S`. Nothing else drops that entry without also
    starting or rebuilding something."""
    request.app.state.health = None
    return await read(request)


@router.post("/source/closure")
async def closure(body: TableSelection, request: Request) -> dict:
    """What a tick-list actually models, and what it gives up."""
    # Only the requested schema is rebuilt: this fires on every checkbox tick, and
    # `cached_catalog` would reconstruct every Column of every schema each time.
    raw = (await request.app.state.sources.state()).get("catalog") or {}
    if body.schema_name not in raw:
        raise HTTPException(404, f"schema {body.schema_name!r} has not been read")
    schema = modelgen.state.load_schema(raw[body.schema_name])
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


@router.post("/source")
async def connect(body: NewSource, request: Request) -> dict:
    """Store the credentials and mint the secrets. **Nothing is running after this.**

    The addresses arrive later, from whoever ran the compose file — which is the whole reason
    this is two steps rather than one. Steps and cached state reset with the row: they describe
    a database, and this is a different one.
    """
    if request.app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")
    try:
        spec = get(body.driver)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from None
    # Validated on what was typed: a `mount_to` field is required as a host path, and
    # rewriting it to the container path first would hide a blank one.
    if missing := missing_fields(spec, body.values):
        raise HTTPException(400, f"missing required fields: {', '.join(missing)}")
    values, mounts = _paths(spec, body.values)

    # Minted here and never again: they go into the environment file, and the deployment that
    # reads it has to keep matching the row. Rotating one means recreating the deployment.
    deployment = Deployment(
        api_secret=secrets.token_urlsafe(36),
        store_token=secrets.token_urlsafe(32),
        store_read_token=secrets.token_urlsafe(32),
        driver=body.driver,
        description=body.description,
        mounts=mounts,
        credentials=credentials_for(spec, values),
    )
    await request.app.state.sources.save(deployment)
    request.app.state.health = None
    return await view(request, deployment, steps={})


@router.patch("/source")
async def edit(body: EditSource, request: Request) -> dict:
    """Change the description, the credentials, or either address.

    This is also step two of connecting: `url` and `store_url` arrive here once the deployment
    is up. Editing credentials afterwards changes only the environment file — a container's
    environment cannot be edited, so a warehouse password change means re-running the compose
    with the new file, and the panel says so rather than this pretending to have applied it.
    """
    store = request.app.state.sources
    deployment = await find(request)
    merged = mounts = None
    if body.values:
        values, supplied = _paths(get(deployment.driver), body.values)
        merged = merge_credentials(deployment.credentials, values)
        # Only the paths actually retyped: blank means "unchanged" here too, and
        # replacing the map wholesale would drop a key file the form did not ask about.
        mounts = {**deployment.mounts, **supplied}
    await store.update(
        description=body.description,
        credentials=merged,
        mounts=mounts,
        store_url=body.store_url,
        sandbox_url=body.sandbox_url,
    )
    request.app.state.health = None
    return await read(request)


@router.delete("/source")
async def remove(request: Request) -> dict:
    """Forget the connection. Nothing is stopped, because nothing here was started — the
    deployment and its model outlive this, which is the point of it running elsewhere."""
    await find(request)
    if request.app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")

    await request.app.state.sources.remove()
    request.app.state.health = None
    return {"removed": True}
