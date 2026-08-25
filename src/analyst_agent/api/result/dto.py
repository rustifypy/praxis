"""What a result looks like on the wire.

`Column` is used directly rather than mirrored. It is a `cube.shape` dataclass of six primitive
fields and it is already the browser's `ResultColumn`, so a pydantic copy here would be a second
definition of one shape with nothing asserting the two agree — which is the drift this whole split
exists to remove. Pydantic validates the stored JSON into it on the way out.

There is no request model: both routes take their arguments in the path and the query string,
where FastAPI already types them.
"""

from pydantic import BaseModel

from ...modelstore.shape import Column


class ResultPage(BaseModel):
    """One slice of a stored result set, and the size of the whole.

    `row_count` is the count of the *set*, not of `rows` — the browser draws "showing 200 of
    4,812" from the pair, and a run past the retention depth keeps its count after its rows are
    pruned. `offset` is echoed because the request that asked for it is not the one rendering it.
    """

    id: str
    columns: list[Column]
    rows: list[dict]
    row_count: int
    offset: int
