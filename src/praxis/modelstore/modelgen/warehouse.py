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
    def ambiguous_targets(self) -> set[str]:
        """Tables more than one foreign key here points at — `from_store_id` and `to_store_id`
        both reaching `dim_stores`. Cube names a join after the cube it reaches, so the two would
        be one join name twice, and the direction each id carries would be lost with it."""
        targets = [fk.ref_table for fk in self.foreign_keys if fk.ref_table != self.name]
        return {target for target in targets if targets.count(target) > 1}

    @property
    def joined_keys(self) -> list[ForeignKey]:
        """The foreign keys that become joins. An ambiguous one does not, so its column stays a
        dimension — the same rule as a key to a table the selection left out."""
        return [fk for fk in self.foreign_keys if fk.ref_table not in self.ambiguous_targets]

    @property
    def key_columns(self) -> set[str]:
        """A single-column primary key and every joined foreign key — join plumbing, not slicing
        dimensions. A composite key's other parts are not plumbing: `sale_date` in
        `(sale_date, store_id, item_id)` is the grain of the row, and the synthesised key replaces
        the key, not the date."""
        single = set(self.primary_key) if len(self.primary_key) == 1 else set()
        joined = {column for foreign_key in self.joined_keys for column in foreign_key.columns}
        return single | joined

    def column(self, name: str) -> Column | None:
        return next((column for column in self.columns if column.name == name), None)


@dataclass
class Schema:
    tables: list[Table] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((table for table in self.tables if table.name == name), None)

    @property
    def exposed_keys(self) -> set[str]:
        """Tables whose primary key is public. An ambiguous foreign key arrives as a bare id, and
        an id with nothing to match it against says nothing."""
        return {target for table in self.tables for target in table.ambiguous_targets}


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
