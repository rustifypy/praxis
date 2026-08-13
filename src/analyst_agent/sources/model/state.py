"""What one step leaves behind for the next, as JSON.

These two are each other's inverse, and they are written so that staying inverses is not a
thing anyone has to maintain: `asdict` emits every field a dataclass has, and `cls(**raw)`
accepts exactly the fields it has. Add a field and it round-trips with no edit here. A key
that is not a field raises `TypeError` rather than being dropped on the next resume, days
later, with every test still passing — a field with a default compares equal on both sides of
a fixture that never sets it, so an enumerating loader can lose one silently.

`Enrichment` needs no pair: it is a pydantic model, so `model_dump()` and `model_validate()`
already are one.
"""

from dataclasses import asdict

from .warehouse import Column, ForeignKey, Schema, Table


def dump_schema(schema: Schema) -> dict:
    return asdict(schema)


def load_schema(raw: dict) -> Schema:
    return Schema(
        tables=[
            Table(
                **{
                    **table,
                    "columns": [Column(**c) for c in table["columns"]],
                    "foreign_keys": [ForeignKey(**fk) for fk in table["foreign_keys"]],
                }
            )
            for table in raw["tables"]
        ]
    )
