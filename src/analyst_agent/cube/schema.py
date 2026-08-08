from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Granularity = Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]

Order = Literal["asc", "desc"]


class Filter(BaseModel):
    """Leaf filter. Cube requires all values as strings."""

    model_config = ConfigDict(extra="forbid")

    member: str
    operator: str
    values: list[str] | None = None

    @model_validator(mode="after")
    def check_values(self):
        if self.operator in ("set", "notSet"):
            if self.values:
                raise ValueError(f"'{self.operator}' takes no values")
        elif not self.values:
            raise ValueError(f"'{self.operator}' on {self.member} requires values")
        return self


class FilterGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    and_: list["FilterNode"] | None = Field(default=None, alias="and")
    or_: list["FilterNode"] | None = Field(default=None, alias="or")

    @model_validator(mode="after")
    def exactly_one(self):
        if bool(self.and_) == bool(self.or_):
            raise ValueError("filter group needs exactly one of 'and' / 'or'")
        return self

    @property
    def children(self) -> list["FilterNode"]:
        return self.and_ or self.or_ or []


FilterNode = Annotated[Filter | FilterGroup, Field(union_mode="left_to_right")]
FilterGroup.model_rebuild()


class TimeDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    dateRange: list[str] | str | None = None
    granularity: Granularity | None = None


class CubeQuery(BaseModel):
    """A Cube JSON query. `ungrouped` is deliberately absent: agents may not
    request raw un-aggregated rows."""

    model_config = ConfigDict(extra="forbid")

    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    timeDimensions: list[TimeDimension] = Field(default_factory=list)
    filters: list[FilterNode] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    order: dict[str, Order] | None = None
    # None means the agent did not ask for one, which is not the same as asking for
    # the default: the gate warns about a limit without an order, and with a default
    # here that warning fired on every query ever made.
    limit: int | None = Field(default=None, ge=1, le=50_000)
    offset: int = Field(default=0, ge=0)
    timezone: str | None = None

    @model_validator(mode="after")
    def must_request_something(self):
        if not (self.measures or self.dimensions or self.timeDimensions):
            raise ValueError("query must request at least one measure or dimension")
        return self

    def to_payload(self) -> dict:
        raw = self.model_dump(exclude_none=True, by_alias=True)
        return {k: v for k, v in raw.items() if v != [] and v != {}}

    def referenced_members(self) -> list[str]:
        out = [*self.measures, *self.dimensions, *self.segments]
        out += [td.dimension for td in self.timeDimensions]
        out += [f.member for f in self.iter_filters()]
        if self.order:
            out += list(self.order)
        return out

    def iter_filters(self, nodes: list[FilterNode] | None = None):
        for node in self.filters if nodes is None else nodes:
            if isinstance(node, Filter):
                yield node
            else:
                yield from self.iter_filters(node.children)
