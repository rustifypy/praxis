"""The two result routes. Paths, statuses, and the download's headers — nothing else.

Route order is load-bearing: `/{result_id}/csv` is a segment and not `{result_id}.csv` because a
bare path parameter matches "abc.csv" as the id itself, which is how the CSV route once became
unreachable. `test_surface.py` asserts that ordering for the whole app now.

`export_csv` and not `export_result`: that name belongs to the agent's sandbox tool, which writes
member ids where this writes labels. Handler names are global in `surface.json`, so two of them
meaning different things is a name taken twice.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from . import controller
from .dto import ResultPage

router = APIRouter(prefix="/api/results")


@router.get("/{result_id}", response_model=ResultPage)
async def read_result(result_id: str, offset: int = 0, limit: int = 200) -> ResultPage:
    """One page of a stored result set, plus the row count of the whole set.
    404 when the id is unknown, which a link in an old transcript will hit: rows are pruned on a
    retention pass and outlive nothing."""
    found = await controller.page(result_id, offset, limit)
    if found is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    return found


@router.get("/{result_id}/csv")
async def export_csv(result_id: str) -> Response:
    """The whole result set as a CSV attachment, with column labels as its header.
    No `response_model`: the body is text/csv, and the browser reaches this as a plain link
    authenticated by the token cookie rather than through `fetch`."""
    exported = await controller.as_csv(result_id)
    if exported is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    return Response(
        exported,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="result-{result_id[:8]}.csv"'},
    )
