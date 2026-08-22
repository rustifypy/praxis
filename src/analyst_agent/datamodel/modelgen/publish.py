import asyncio
from copy import deepcopy
from dataclasses import dataclass, field

import yaml

from ...core.deployment import MAIN
from ...cube.client import CubeClient, CubeError, open_client
from ...cube.schema import CubeQuery
from ...modelstore import StoreError, open_store
from .decide import CubeDecision, Enrichment, MeasureDecision, ViewDecision, exposed_members

COMPILE_TIMEOUT_S = 90.0

# Where a rebuild lands before anything is asked about it. One name and not one per run: a
# rejected model is worth keeping until the next attempt and no longer.
CANDIDATE = "candidate"

# A filter value nothing can equal, so an executed probe aggregates almost no rows.
NO_MATCH = "__analyst_probe_no_match__"


@dataclass
class Validation:
    compiled: bool = False
    compile_error: str = ""
    entities: list[str] = field(default_factory=list)
    member_errors: dict[str, str] = field(default_factory=dict)
    # The ref a rejected model was left at, so the YAML Cube complained about can be read.
    candidate: str = ""

    @property
    def ok(self) -> bool:
        return self.compiled and not self.member_errors


# --- decisions to YAML ------------------------------------------------------


def dump(document: dict) -> str:
    # sort_keys=False keeps member order stable across regenerations.
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=100)


def measure_body(decision: MeasureDecision) -> dict:
    body: dict = {"name": decision.name, "type": decision.aggregation}
    # A bare column stays bare, as Cube's own generator writes it. An expression arrives from
    # `validate_cube` already qualified, since Cube passes multi-column SQL through verbatim.
    body["sql"] = decision.expression or decision.column
    if decision.description:
        body["description"] = decision.description
    if decision.ai_context:
        body["meta"] = {"ai_context": decision.ai_context}
    return body


def apply_cube(body: dict, decision: CubeDecision | None) -> dict:
    if decision is None:
        return body
    if decision.description:
        body["description"] = decision.description

    docs = {member.name: member for member in decision.members}
    for dimension in body.get("dimensions", []):
        doc = docs.get(dimension["name"])
        if not doc:
            continue
        if doc.description:
            dimension["description"] = doc.description
        if doc.ai_context:
            dimension["meta"] = {"ai_context": doc.ai_context}

    # A measure named after its own column supersedes that dimension: Cube rejects two members
    # with one name. The primary key is exempt.
    taken = {measure.name for measure in decision.measures}
    body["dimensions"] = [
        dimension
        for dimension in body.get("dimensions", [])
        if dimension["name"] not in taken or dimension.get("primary_key")
    ]

    existing = {measure["name"] for measure in body.get("measures", [])}
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
        body["folders"] = [
            {"name": folder.name, "includes": folder.includes} for folder in view.folders
        ]
    return body


def build_files(bodies: dict[str, dict], enrichment: Enrichment | None = None) -> dict[str, str]:
    # Without an enrichment this is the deterministic model.
    files = {
        f"cubes/{name}.yml": dump(
            {"cubes": [apply_cube(deepcopy(body), enrichment and enrichment.cubes.get(name))]}
        )
        for name, body in bodies.items()
    }
    for view in enrichment.views if enrichment else []:
        files[f"views/{view.name}.yml"] = dump({"views": [view_body(view)]})
    return files


# --- getting it in front of Cube --------------------------------------------


async def install(
    files: dict[str, str],
    *,
    members: bool = True,
    every_measure: bool = False,
    enrichment: Enrichment | None = None,
    ref: str = MAIN,
    message: str = "rebuild",
) -> Validation:
    # What makes a rebuild non-destructive: one Cube serves every ref at once, so a candidate
    # is validated without taking anything down, and a model Cube refuses is a ref left behind
    # to read rather than YAML that has already replaced a working model.
    async with await open_store() as store:
        try:
            # Deleted and recreated rather than written on top of: a candidate that has
            # diverged from `ref` would make the merge below a real three-way one, which is
            # how a stale side comes quietly back.
            await store.delete_ref(CANDIDATE)
        except StoreError as exc:
            if not exc.missing:
                raise
        await store.create_ref(CANDIDATE, ref)
        await store.replace(CANDIDATE, files, f"{message} — candidate")

        validation = await check(
            every_measure=every_measure, members=members, enrichment=enrichment, ref=CANDIDATE
        )
        # Member errors are not fatal, so compilation alone decides whether this lands: a
        # model that compiles with fewer measures beats one that breaks at query time.
        if not validation.compiled:
            validation.candidate = CANDIDATE
            return validation

        await store.merge(CANDIDATE, ref, message)
        try:
            await store.delete_ref(CANDIDATE)
        except StoreError:
            # The model has landed, so raising here would report a rebuild that worked as a
            # step that failed.
            pass
    return validation


async def check(
    timeout: float = COMPILE_TIMEOUT_S,
    every_measure: bool = False,
    members: bool = True,
    enrichment: Enrichment | None = None,
    ref: str = MAIN,
) -> Validation:
    # Compile errors from /v1/meta, then queries through /v1/sql.
    try:
        validation = Validation()

        async with await open_client(ref=ref) as cube:
            # Startup races look like errors too, so the session retries anything but a genuine
            # compile error, which it raises rather than waiting out the deadline.
            catalog = await cube.wait_until_compiled(timeout=timeout)

            validation.compiled = True
            validation.entities = [entity.name for entity in catalog.entities]
            if not members:
                return validation

            # (member, context, executed, query). Compile probes come first, so when a measure
            # fails both ways the plain compile error is the one reported.
            probes: list[tuple[str, str, bool, dict]] = []
            for entity in catalog.entities:
                measures = [member.id for member in entity.members if member.kind == "measure"]
                # The names meta reports, rather than assuming `<entity>.count` exists: a cube
                # always has one, a view only if it includes one.
                if every_measure:
                    chosen = measures
                else:
                    smoke = next((name for name in measures if name.endswith(".count")), None)
                    chosen = [smoke or measures[0]] if measures else []
                probes += [(name, "", False, {"measures": [name], "limit": 1}) for name in chosen]

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
                        paths.get(member.id.split(".", 1)[-1]): member.id
                        for member in entity.members
                        if member.kind == "dimension" and member.type == "string"
                    }
                    measures_here = (
                        member.id for member in entity.members if member.kind == "measure"
                    )
                    for measure in measures_here:
                        home = paths.get(measure.split(".", 1)[-1])
                        partner = next(
                            (
                                dimension
                                for path, dimension in elsewhere.items()
                                if path not in (None, home)
                            ),
                            None,
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
                                        # References are resolved while planning, so a filter
                                        # nothing can match still catches an ambiguous one.
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
            if error and member not in validation.member_errors:
                validation.member_errors[member] = context + error
        return validation
    except CubeError as exc:
        validation.compile_error = str(exc)
        return validation


async def _probe(
    cube: CubeClient, member: str, context: str, executed: bool, query: dict
) -> tuple[str, str, str]:
    # A compile probe reports Cube's refusal; an executed one is how an ambiguous column
    # reference surfaces at all.
    try:
        parsed = CubeQuery.model_validate(query)
        if executed:
            await cube.load(parsed)
        else:
            compiled = await cube.compile(parsed)
            if not compiled.ok:
                return member, context, (compiled.error or "")[:300]
        return member, context, ""
    except CubeError as exc:
        return member, context, str(exc)[:300]
