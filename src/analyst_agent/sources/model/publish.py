"""Stage 4 — write the model, and let Cube be the judge.

Writing our own YAML checker would be a second, weaker opinion about what Cube
accepts. So the candidate model goes to the source's own container and Cube says
what is wrong, in errors precise enough to repair from, e.g.
`primary key for 'album' is required when join is defined`.

    restart          load the new files (Cube Core does not hot-reload)
    GET  /v1/meta    compile errors, and the members that survived
    POST /v1/sql     per member: compiles SQL without executing it, so no
                     warehouse cost and no rows read

`install` is the whole cycle — write, restart, ask — and is the only way the model
reaches the container.
"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from .. import docker
from ..registry import Source, project_root
from .decide import CubeDecision, Enrichment, MeasureDecision, ViewDecision, exposed_members

RESTART_TIMEOUT_S = 90.0
NO_MATCH = "__analyst_probe_no_match__"
"""Filter value nothing can equal, so an executed probe aggregates almost no rows."""


@dataclass
class Validation:
    compiled: bool = False
    compile_error: str = ""
    entities: list[str] = field(default_factory=list)
    member_errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.compiled and not self.member_errors


# --- decisions to YAML ------------------------------------------------------


def dump(document: dict) -> str:
    """sort_keys=False keeps member order stable across regenerations."""
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=100)


def measure_body(decision: MeasureDecision) -> dict:
    body: dict = {"name": decision.name, "type": decision.aggregation}
    # A bare column stays bare, as Cube's own generator writes it — Cube resolves a
    # single identifier against this cube. An expression arrives from `validate_cube`
    # already qualified, because Cube passes multi-column SQL through verbatim.
    body["sql"] = decision.expression or decision.column
    if decision.description:
        body["description"] = decision.description
    if decision.ai_context:
        body["meta"] = {"ai_context": decision.ai_context}
    return body


def apply_cube(body: dict, decision: CubeDecision | None) -> dict:
    """Layer descriptions and measures onto a deterministically emitted cube."""
    if decision is None:
        return body
    if decision.description:
        body["description"] = decision.description

    docs = {m.name: m for m in decision.members}
    for dimension in body.get("dimensions", []):
        doc = docs.get(dimension["name"])
        if not doc:
            continue
        if doc.description:
            dimension["description"] = doc.description
        if doc.ai_context:
            dimension["meta"] = {"ai_context": doc.ai_context}

    # A measure named after its own column supersedes that dimension: Cube rejects two
    # members with one name, and a raw numeric left queryable as a dimension is the
    # ambiguity the generated ai_context ends up warning about. The key is exempt.
    taken = {m.name for m in decision.measures}
    body["dimensions"] = [
        d for d in body.get("dimensions", []) if d["name"] not in taken or d.get("primary_key")
    ]

    existing = {m["name"] for m in body.get("measures", [])}
    for measure in decision.measures:
        if measure.name not in existing:
            body["measures"].append(measure_body(measure))
    return body


def view_body(view: ViewDecision) -> dict:
    body: dict = {"name": view.name}
    if view.description:
        body["description"] = view.description
    if view.ai_context:
        body["meta"] = {"ai_context": view.ai_context}
    aliases = {(path, member): alias for path, member, _, alias in exposed_members(view) if alias}
    body["cubes"] = [
        {
            "join_path": entry.join_path,
            **({"prefix": True} if entry.prefix else {}),
            "includes": [
                {"name": member, "alias": aliases[(entry.join_path, member)]}
                if (entry.join_path, member) in aliases
                else member
                for member in entry.includes
            ],
        }
        for entry in view.cubes
    ]
    if view.folders:
        body["folders"] = [{"name": f.name, "includes": f.includes} for f in view.folders]
    return body


def build_files(bodies: dict[str, dict], enrichment: Enrichment | None = None) -> dict[str, str]:
    """The files to write. Without an enrichment this is the deterministic model.

    Takes the bodies `emit_cubes` produced — the structural rules, including dropping
    joins that have no primary key, live there and only there. Copies before layering,
    so the same bodies can be enriched again after a repair.
    """
    files = {
        f"cubes/{name}.yml": dump(
            {"cubes": [apply_cube(deepcopy(body), enrichment and enrichment.cubes.get(name))]}
        )
        for name, body in bodies.items()
    }
    for view in enrichment.views if enrichment else []:
        files[f"views/{view.name}.yml"] = dump({"views": [view_body(view)]})
    return files


# --- getting it into the container -----------------------------------------


def write(source: Source, files: dict[str, str], root: Path | None = None) -> None:
    """Replace the model. Every `*.yml` goes first, so a cube that is no longer emitted
    does not linger and keep compiling.

    Destructive on purpose and with no undo of its own: the model directory is the
    user's, and version control is what rolls it back. A rebuild that discards views is
    a diff they can read, which is more than a one-deep snapshot ever told them.
    """
    model_dir = source.model_dir(root or project_root())
    for existing in model_dir.rglob("*.yml"):
        existing.unlink()
    for relative, body in files.items():
        target = model_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)


async def restart(source: Source) -> bool:
    """Cube Core does not hot-reload model files; only a restart loads what was written.

    Async because this runs inside the web server: six seconds of blocking here would
    freeze the SSE stream of any chat in progress.
    """
    return (await docker.restart(source.service)).ok


async def install(
    source: Source,
    files: dict[str, str],
    *,
    members: bool = True,
    every_measure: bool = False,
    enrichment: Enrichment | None = None,
) -> Validation:
    """Write the model, restart Cube, and report what Cube makes of it."""
    write(source, files)
    if not await restart(source):
        return Validation(compile_error=f"could not restart {source.service}")
    return await check(source, members=members, every_measure=every_measure, enrichment=enrichment)


async def check(
    source: Source,
    timeout: float = RESTART_TIMEOUT_S,
    every_measure: bool = False,
    members: bool = True,
    enrichment: Enrichment | None = None,
) -> Validation:
    """Read compile errors from /v1/meta, then compile queries through /v1/sql.

    `every_measure` tests each measure individually — a bad `sql:` expression only
    fails when that measure is used, so a per-cube count would miss it.

    `enrichment` adds a second probe per view measure, pairing it with a dimension
    from a different join path. A measure queried on its own never joins, so tested
    that way it can ship with an ambiguous column reference and fail on first real use.

    `members=False` stops after the compile check. Used before enrichment, where the
    model is about to be rewritten and revalidated anyway.
    """
    out = Validation()

    # Poll /v1/meta until it serves a compiled model. Startup races look like errors
    # too, so only a compile error is terminal — anything else is worth retrying.
    meta: dict = {}
    deadline = asyncio.get_running_loop().time() + timeout
    last = "no response"
    async with httpx.AsyncClient(timeout=15) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                r = await client.get(f"{source.url}/cubejs-api/v1/meta")
                json_response = r.headers.get("content-type", "").startswith("application")
                body = r.json() if json_response else {}
                if r.status_code == 200 and "cubes" in body:
                    meta = body
                    break
                if isinstance(body, dict) and body.get("error"):
                    error = str(body["error"])
                    if "Compile errors" in error or "Error: " in error:
                        out.compile_error = error
                        return out
                    last = error[:300]
                else:
                    last = r.text[:300]
            except httpx.RequestError as exc:
                last = type(exc).__name__
            await asyncio.sleep(2)
        else:
            out.compile_error = f"no compiled model within {timeout:.0f}s — {last}"
            return out

    out.compiled = True
    out.entities = [c["name"] for c in meta.get("cubes", [])]
    if not members:
        return out

    # (member, context, endpoint, query). Compile probes come first, so when a measure
    # fails both ways the plain compile error is the one reported.
    probes: list[tuple[str, str, str, dict]] = []
    for entity in meta.get("cubes", []):
        measures = [m["name"] for m in entity.get("measures", [])]
        # Take the names meta reports rather than assuming `<entity>.count` exists — a
        # cube always has one, but a view has it only if it includes one.
        if every_measure:
            chosen = measures
        else:
            smoke = next((m for m in measures if m.endswith(".count")), None)
            chosen = [smoke or measures[0]] if measures else []
        probes += [(m, "", "sql", {"measures": [m], "limit": 1}) for m in chosen]

    if every_measure and enrichment:
        # Which underlying cube each exposed view member came from, so a measure can be
        # paired with a dimension that forces Cube to join.
        origins = {
            view.name: {name: path for path, _, name, _ in exposed_members(view)}
            for view in enrichment.views
        }
        for entity in meta.get("cubes", []):
            paths = origins.get(entity["name"])
            if not paths:
                continue
            # A string dimension, so the impossible filter below cannot fail on a cast.
            elsewhere = {
                paths.get(d["name"].split(".", 1)[-1]): d["name"]
                for d in entity.get("dimensions", [])
                if d.get("type") == "string"
            }
            for measure in (m["name"] for m in entity.get("measures", [])):
                home = paths.get(measure.split(".", 1)[-1])
                partner = next(
                    (d for path, d in elsewhere.items() if path not in (None, home)), None
                )
                if partner:
                    probes.append(
                        (
                            measure,
                            f"joined with {partner}: ",
                            "load",
                            {
                                "measures": [measure],
                                "dimensions": [partner],
                                # The database resolves column references while planning, so
                                # a filter nothing can match still catches an ambiguous
                                # reference while aggregating almost no rows.
                                "filters": [
                                    {"member": partner, "operator": "equals", "values": [NO_MATCH]}
                                ],
                                "limit": 1,
                            },
                        )
                    )

    async with httpx.AsyncClient(timeout=60) as client:
        results = await asyncio.gather(*(_probe(client, source, *probe) for probe in probes))
    for member, context, error in results:
        if error and member not in out.member_errors:
            out.member_errors[member] = context + error
    return out


async def _probe(
    client: httpx.AsyncClient, source: Source, member: str, context: str, endpoint: str, query: dict
) -> tuple[str, str, str]:
    """One coroutine per probe, so `check` can gather them.

    `sql` compiles and runs nothing. `load` executes — needed because Cube compiles an
    ambiguous column reference happily and only the database objects to it.
    """
    try:
        r = await client.post(f"{source.url}/cubejs-api/v1/{endpoint}", json={"query": query})
    except httpx.RequestError as exc:
        return member, context, type(exc).__name__
    body = {}
    try:
        body = r.json()
    except ValueError:
        return member, context, r.text[:300] if r.status_code != 200 else ""
    # /v1/load answers 200 with an `error` key for some failures, so status is not enough.
    if r.status_code != 200 or "error" in body:
        return member, context, str(body.get("error", r.text))[:300]
    return member, context, ""
