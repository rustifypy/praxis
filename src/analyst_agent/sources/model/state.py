"""What one step leaves behind for the next, as JSON.

The four steps run minutes or days apart, so nothing may survive only in memory:

    catalog      every schema and table the probe read, before any selection
    schema       the selected tables, as a Schema — what `emit_cubes` was given
    bodies       the emitted cube bodies, which enrichment layers onto
    enrichment   the model's decisions, which the views step and any repair need
    selection    the schema name and table names the user picked

`views` cannot be rebuilt from the YAML on disk — `views_prompt` needs the join graph
and the cube decisions, and YAML has neither. So the round-trip here is load-bearing:
if it loses a field, a re-run silently produces a different model. It has a test.

`Schema` is dataclasses and `Enrichment` holds pydantic models, which is why this is
hand-written rather than one `asdict`.
"""

from dataclasses import asdict

from .decide import CubeDecision, Enrichment, ViewDecision
from .warehouse import Column, ForeignKey, Schema, Table


def dump_schema(schema: Schema) -> dict:
    return asdict(schema)


def load_schema(raw: dict) -> Schema:
    return Schema(
        tables=[
            Table(
                name=t["name"],
                schema=t.get("schema", "public"),
                columns=[
                    Column(
                        name=c["name"],
                        data_type=c.get("data_type", ""),
                        distinct=c.get("distinct"),
                        null_fraction=c.get("null_fraction"),
                    )
                    for c in t.get("columns") or []
                ],
                primary_key=list(t.get("primary_key") or []),
                foreign_keys=[
                    ForeignKey(
                        columns=list(fk.get("columns") or []),
                        ref_table=fk["ref_table"],
                        ref_columns=list(fk.get("ref_columns") or []),
                    )
                    for fk in t.get("foreign_keys") or []
                ],
            )
            for t in raw.get("tables") or []
        ]
    )


def dump_enrichment(enrichment: Enrichment) -> dict:
    return {
        "cubes": {name: d.model_dump() for name, d in enrichment.cubes.items()},
        "views": [v.model_dump() for v in enrichment.views],
        "rejected": list(enrichment.rejected),
        "repaired": list(enrichment.repaired),
        "dropped": list(enrichment.dropped),
    }


def load_enrichment(raw: dict) -> Enrichment:
    return Enrichment(
        cubes={
            name: CubeDecision.model_validate(d) for name, d in (raw.get("cubes") or {}).items()
        },
        views=[ViewDecision.model_validate(v) for v in raw.get("views") or []],
        rejected=list(raw.get("rejected") or []),
        repaired=list(raw.get("repaired") or []),
        dropped=list(raw.get("dropped") or []),
    )
