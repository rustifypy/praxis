"""What onboarding sends and receives: the credentials, the addresses, and each stage.

Two of these mirror a shape another tier builds, and the reason is different in each case:

- `JobSnapshot` mirrors `core.jobs.Job.snapshot()`. It has to: `Job` carries an `asyncio.Event`, a
  `Task` and a set of watcher queues, none of which can be serialised, so `snapshot()` exists
  precisely to pick the seven fields that can. Typing that answer means naming those seven.
- `TableRow` mirrors `modelgen.selection.describe()`, which returns dicts rather than a dataclass.
  Given a dataclass there, this would name it the way `result/dto.py` names `Column`.

`StageRequest` is one model for both writes. The old code had two — identical fields, on the
argument that "the shape matches today, the question does not" — and the second was never used
differently.
"""

from pydantic import BaseModel, Field


class StageRequest(BaseModel):
    """Which schema, and which of its tables. `None` tables means every table in the schema."""

    schema_name: str = "public"
    tables: list[str] | None = None


class TableRow(BaseModel):
    """One table as the picker lists it: how big, what its key is, and what it points at."""

    name: str
    columns: int
    primary_key: list[str]
    references: list[str]
    # Nothing points at it, so it is where additive facts live — the useful thing to pick first.
    fact: bool


class SchemaTables(BaseModel):
    """One schema and its tables. A list and not a map, because the order is the server's."""

    schema_name: str = Field(serialization_alias="schema", validation_alias="schema")
    tables: list[TableRow]

    model_config = {"populate_by_name": True}


class TableCatalog(BaseModel):
    """What the `read` stage cached, for the picker. Never the warehouse itself.

    `read` is false before anything has been read, which is what the picker draws its empty state
    from — it is not the same as a schema with no tables.
    """

    read: bool
    truncated: bool
    notes: list[str]
    selection: dict
    schemas: list[SchemaTables]


class Closure(BaseModel):
    """The tables a selection drags in, and what is wrong with what is left.

    `added` is what the closure pulled in beyond what was ticked, so the picker can say why a box
    it did not tick is now checked.
    """

    tables: list[str]
    added: list[str]
    cut_joins: list[str]
    keyless: list[str]


class JobEvent(BaseModel):
    """One line of a job's narration, as `core.jobs.JobLine` records it."""

    type: str
    data: dict = Field(default_factory=dict)
    seq: int = 0
    at: float = 0.0


class JobSnapshot(BaseModel):
    """A running job and its buffered narration, so a page refresh re-attaches.

    Every field is optional but `id`, because "no job" is answered as `{"id": null}` rather than as
    a 404: nothing is wrong when nothing is running, and the browser polls this on arrival.
    """

    id: str | None = None
    steps: list[str] = Field(default_factory=list)
    current: str = ""
    started_at: float = 0.0
    done: bool = False
    error: str = ""
    events: list[JobEvent] = Field(default_factory=list)


class Cancelled(BaseModel):
    """Whether there was a job to cancel. False is not an error — the button may be late."""

    cancelled: bool


# --- connecting --------------------------------------------------------------


class NewConnection(BaseModel):
    description: str = ""
    driver: str
    values: dict[str, str] = Field(default_factory=dict)


class EditConnection(BaseModel):
    description: str | None = None
    values: dict[str, str] = Field(default_factory=dict)


class Addresses(BaseModel):
    store_url: str = ""
    sandbox_url: str = ""


# --- the body every route here answers with ----------------------------------
