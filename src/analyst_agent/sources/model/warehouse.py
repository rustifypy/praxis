"""What the warehouse says about itself: tables, columns, keys, statistics.

The vocabulary the rest of the pipeline speaks. Keys come from the database
catalog, never from column names — Cube's own scaffolder guesses the primary key
from naming and drops the column when the guess misses, which is what produces
`primary key for 'x' is required when join is defined`.
"""

from dataclasses import dataclass, field

NUMBER_TYPES = (
    "smallint",
    "integer",
    "int",
    "bigint",
    "decimal",
    "numeric",
    "real",
    "double",
    "float",
    "money",
)
TIME_PREFIXES = ("timestamp", "datetime", "date", "time")
BOOLEAN_TYPES = ("boolean", "bool")


@dataclass
class Column:
    name: str
    data_type: str
    distinct: float | None = None
    """pg_stats n_distinct: >0 is a count, <0 is a fraction of rows, -1 is unique."""
    null_fraction: float | None = None

    @property
    def type(self) -> str:
        low = self.data_type.lower().strip()

        if low in BOOLEAN_TYPES:
            return "boolean"
        if any(low.startswith(p) for p in TIME_PREFIXES):
            return "time"
        if any(low.startswith(t) for t in NUMBER_TYPES):
            return "number"
        return "string"


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    schema: str = "public"
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def has_composite_key(self) -> bool:
        return len(self.primary_key) > 1

    @property
    def key_columns(self) -> set[str]:
        """Primary and foreign key columns — join plumbing, not slicing dimensions."""
        return set(self.primary_key) | {c for fk in self.foreign_keys for c in fk.columns}

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Schema:
    tables: list[Table] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)
