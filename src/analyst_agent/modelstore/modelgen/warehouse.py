"""What the warehouse says about itself: tables, columns, keys, statistics."""

from dataclasses import asdict, dataclass, field

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
        if any(low.startswith(prefix) for prefix in TIME_PREFIXES):
            return "time"
        if any(low.startswith(table) for table in NUMBER_TYPES):
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
        return set(self.primary_key) | {
            column for foreign_key in self.foreign_keys for column in foreign_key.columns
        }

    def column(self, name: str) -> Column | None:
        return next((column for column in self.columns if column.name == name), None)


@dataclass
class Schema:
    tables: list[Table] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((table for table in self.tables if table.name == name), None)


# These two stay each other's inverse without anyone maintaining it: `asdict` emits every field a
# dataclass has and `cls(**raw)` accepts exactly those, so an added field round-trips with no edit
# here and an unknown key raises rather than being dropped on a resume days later.


def dump_schema(schema: Schema) -> dict:
    return asdict(schema)


def load_schema(raw: dict) -> Schema:
    return Schema(
        tables=[
            Table(
                **{
                    **table,
                    "columns": [Column(**column) for column in table["columns"]],
                    "foreign_keys": [ForeignKey(**key) for key in table["foreign_keys"]],
                }
            )
            for table in raw["tables"]
        ]
    )
