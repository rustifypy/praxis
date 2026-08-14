"""Stage 4 — write the model, and let Cube be the judge."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ...cube.client import CubeClient, CubeError
from ...cube.schema import CubeQuery
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
    """The files to write. Without an enrichment this is the deterministic model."""
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
    """Replace the model. Every `*.yml` goes first, so a cube that is no longer emitted does not
    linger and keep compiling.
    """
    model_dir = source.model_dir(root or project_root())
    for existing in model_dir.rglob("*.yml"):
        existing.unlink()
    for relative, body in files.items():
        target = model_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)


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
    # A restart rather than trusting the file watcher: a whole model is rewritten here, and
    # `check` must report on that model rather than on a half-seen one.
    if not (await docker.restart(source.service)).ok:
        return Validation(compile_error=f"could not restart {source.service}")
    return await check(source, members=members, every_measure=every_measure, enrichment=enrichment)


async def check(
    source: Source,
    timeout: float = RESTART_TIMEOUT_S,
    every_measure: bool = False,
    members: bool = True,
    enrichment: Enrichment | None = None,
) -> Validation:
    """Read compile errors from /v1/meta, then compile queries through /v1/sql."""
    out = Validation()

    async with CubeClient.for_source(source) as cube:
        try:
            # Startup races look like errors too, so the session retries anything but a
            # genuine compile error, which it raises rather than waiting out the deadline.
            catalog = await cube.wait_until_compiled(timeout=timeout)
        except CubeError as exc:
            out.compile_error = str(exc)
            return out

        out.compiled = True
        out.entities = [e.name for e in catalog.entities]
        if not members:
            return out

        # (member, context, executed, query). Compile probes come first, so when a measure
        # fails both ways the plain compile error is the one reported.
        probes: list[tuple[str, str, bool, dict]] = []
        for entity in catalog.entities:
            measures = [m.id for m in entity.members if m.kind == "measure"]
            # Take the names meta reports rather than assuming `<entity>.count` exists — a
            # cube always has one, but a view has it only if it includes one.
            if every_measure:
                chosen = measures
            else:
                smoke = next((m for m in measures if m.endswith(".count")), None)
                chosen = [smoke or measures[0]] if measures else []
            probes += [(m, "", False, {"measures": [m], "limit": 1}) for m in chosen]

        if every_measure and enrichment:
            # Which underlying cube each exposed view member came from, so a measure can be
            # paired with a dimension that forces Cube to join.
            origins = {
                view.name: {name: path for path, _, name, _ in exposed_members(view)}
                for view in enrichment.views
            }
            for entity in catalog.entities:
                paths = origins.get(entity.name)
                if not paths:
                    continue
                # A string dimension, so the impossible filter below cannot fail on a cast.
                elsewhere = {
                    paths.get(m.id.split(".", 1)[-1]): m.id
                    for m in entity.members
                    if m.kind == "dimension" and m.type == "string"
                }
                for measure in (m.id for m in entity.members if m.kind == "measure"):
                    home = paths.get(measure.split(".", 1)[-1])
                    partner = next(
                        (d for path, d in elsewhere.items() if path not in (None, home)), None
                    )
                    if partner:
                        probes.append(
                            (
                                measure,
                                f"joined with {partner}: ",
                                True,
                                {
                                    "measures": [measure],
                                    "dimensions": [partner],
                                    # The database resolves column references while planning,
                                    # so a filter nothing can match still catches an ambiguous
                                    # reference while aggregating almost no rows.
                                    "filters": [
                                        {
                                            "member": partner,
                                            "operator": "equals",
                                            "values": [NO_MATCH],
                                        }
                                    ],
                                    "limit": 1,
                                },
                            )
                        )

        results = await asyncio.gather(*(_probe(cube, *probe) for probe in probes))
    for member, context, error in results:
        if error and member not in out.member_errors:
            out.member_errors[member] = context + error
    return out


async def _probe(
    cube: CubeClient, member: str, context: str, executed: bool, query: dict
) -> tuple[str, str, str]:
    """One coroutine per probe, so `check` can gather them. A compile probe reports Cube's
    refusal; an executed one is how an ambiguous column reference surfaces at all."""
    try:
        parsed = CubeQuery.model_validate(query)
        if executed:
            await cube.load(parsed)
        else:
            compiled = await cube.compile(parsed)
            if not compiled.ok:
                return member, context, (compiled.error or "")[:300]
    except CubeError as exc:
        return member, context, str(exc)[:300]
    return member, context, ""
