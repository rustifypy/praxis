"""Choosing which tables to model, as a filter on the Schema."""

from collections.abc import Iterable

from .warehouse import Schema, Table


def closure(schema: Schema, tables: Iterable[str]) -> set[str]:
    """`tables` plus everything they reference, transitively."""
    chosen = {table for table in tables if schema.table(table)}
    frontier = list(chosen)
    while frontier:
        table = schema.table(frontier.pop())
        if table is None:
            continue
        for foreign_key in table.foreign_keys:
            target = schema.table(foreign_key.ref_table)
            if target is None or not target.primary_key or target.name in chosen:
                continue
            chosen.add(target.name)
            frontier.append(target.name)
    return chosen


def cut_joins(schema: Schema, selected: Iterable[str]) -> list[str]:
    """The joins a selection gives up, as `from -> to` strings."""
    keep = set(selected)
    return sorted(
        f"{table.name} -> {foreign_key.ref_table}"
        for table in schema.tables
        if table.name in keep
        for foreign_key in table.foreign_keys
        if foreign_key.ref_table != table.name
        and foreign_key.ref_table not in keep
        and schema.table(foreign_key.ref_table)
    )


def filter_schema(schema: Schema, tables: Iterable[str]) -> Schema:
    """The selected tables, with foreign keys to unselected ones removed."""
    keep = set(tables)
    return Schema(
        tables=[
            Table(
                name=table.name,
                schema=table.schema,
                columns=list(table.columns),
                primary_key=list(table.primary_key),
                foreign_keys=[
                    foreign_key
                    for foreign_key in table.foreign_keys
                    if foreign_key.ref_table in keep
                ],
            )
            for table in schema.tables
            if table.name in keep
        ]
    )


def describe(schema: Schema) -> list[dict]:
    """One row per table for the picker: size, key, and what it points at."""
    return [
        {
            "name": table.name,
            "columns": len(table.columns),
            "primary_key": list(table.primary_key),
            "references": sorted(
                {
                    foreign_key.ref_table
                    for foreign_key in table.foreign_keys
                    if foreign_key.ref_table != table.name
                }
            ),
            # Nothing points at it, so it is where additive facts live — the useful
            # thing to pick first, and worth marking in the list.
            "fact": table.name
            not in {
                foreign_key.ref_table
                for table in schema.tables
                for foreign_key in table.foreign_keys
                if foreign_key.ref_table != table.name
            },
        }
        for table in schema.tables
    ]


def keyless(schema: Schema) -> list[str]:
    """Tables Cube will not let anything join to."""
    return sorted(table.name for table in schema.tables if not table.primary_key)
