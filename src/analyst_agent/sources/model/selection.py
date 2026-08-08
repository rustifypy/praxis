"""Choosing which tables to model, as a filter on the Schema.

Nothing downstream knows selection happened. `build.joins`, `build.join_paths` and
`build.view_roots` already tolerate a foreign key whose target is absent — they were
written that way for keys pointing into another schema — and a deselected table is
indistinguishable from that. So this filters the Schema and the rest of the pipeline
is untouched.

Selection matters because of what it saves further along. Reading the catalog costs
two queries however many tables there are, but `build.build_menu` samples values per
table and `decide.enrich` makes one model call per table. Four hundred tables is an
hour of sequential reasoning calls and a semantic layer too wide for the agent to
navigate — so the choice belongs here, after the read and before anything expensive.

The cost of choosing badly is silent: drop `invoice` and every join to it disappears
into a note. `closure` and `cut_joins` are what let the UI say so out loud.
"""

from collections.abc import Iterable

from .warehouse import Schema, Table


def closure(schema: Schema, tables: Iterable[str]) -> set[str]:
    """`tables` plus everything they reference, transitively.

    Picking a fact table should pull in its dimensions — that is what makes "model my
    invoices" one click rather than a puzzle about which nine tables it needs. Only
    targets with a primary key are followed: Cube refuses a join to a keyless cube, so
    adding one would widen the model without adding a join.
    """
    chosen = {t for t in tables if schema.table(t)}
    frontier = list(chosen)
    while frontier:
        table = schema.table(frontier.pop())
        if table is None:
            continue
        for fk in table.foreign_keys:
            target = schema.table(fk.ref_table)
            if target is None or not target.primary_key or target.name in chosen:
                continue
            chosen.add(target.name)
            frontier.append(target.name)
    return chosen


def cut_joins(schema: Schema, selected: Iterable[str]) -> list[str]:
    """The joins a selection gives up, as `from -> to` strings.

    A join to an unselected table does not fail, it vanishes: `build.joins` records
    "table not in this schema" and moves on. Without this the user has no way to know
    a view came out thin because of what they unticked.
    """
    keep = set(selected)
    return sorted(
        f"{table.name} -> {fk.ref_table}"
        for table in schema.tables
        if table.name in keep
        for fk in table.foreign_keys
        if fk.ref_table != table.name and fk.ref_table not in keep and schema.table(fk.ref_table)
    )


def filter_schema(schema: Schema, tables: Iterable[str]) -> Schema:
    """The selected tables, with foreign keys to unselected ones removed.

    Dropping the dead keys matters beyond tidiness: `Table.key_columns` hides every
    foreign-key column from the dimension list, so a key left pointing at a table that
    is no longer modelled would silently remove a perfectly good dimension.
    """
    keep = set(tables)
    return Schema(
        tables=[
            Table(
                name=table.name,
                schema=table.schema,
                columns=list(table.columns),
                primary_key=list(table.primary_key),
                foreign_keys=[fk for fk in table.foreign_keys if fk.ref_table in keep],
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
                {fk.ref_table for fk in table.foreign_keys if fk.ref_table != table.name}
            ),
            # Nothing points at it, so it is where additive facts live — the useful
            # thing to pick first, and worth marking in the list.
            "fact": table.name
            not in {
                fk.ref_table
                for t in schema.tables
                for fk in t.foreign_keys
                if fk.ref_table != t.name
            },
        }
        for table in schema.tables
    ]


def keyless(schema: Schema) -> list[str]:
    """Tables Cube will not let anything join to."""
    return sorted(t.name for t in schema.tables if not t.primary_key)
