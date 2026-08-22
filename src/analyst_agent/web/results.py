"""One query's rows, kept beside the transcript so a table has something to address.

The event carries only a head of them; this is where the rest is read from, and where a CSV is
built. Labels in the header, not member ids: a person reads a spreadsheet, and `export_result`
writes the ids instead because a script refers to `sales.invoice_total`.
"""

import csv
import io

from fastapi import APIRouter, HTTPException, Response

from .. import transcript
from ..cube.shape import Column, export_value

router = APIRouter(prefix="/api/results")


@router.get("/{result_id}")
async def result(result_id: str, offset: int = 0, limit: int = 200) -> dict:
    record = await transcript.result(result_id)
    if record is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    rows = record["rows"]
    return {
        "id": record["id"],
        "columns": record["columns"],
        "rows": rows[offset : offset + limit],
        "row_count": record["row_count"],
        "offset": offset,
    }


# A segment, not `{result_id}.csv`: a bare path parameter matches "abc.csv" as the id itself and
# shadows the route above it — the same lesson as `/pipeline/{stage}`, one layer down.
@router.get("/{result_id}/csv")
async def result_csv(result_id: str) -> Response:
    record = await transcript.result(result_id)
    if record is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    buffer = io.StringIO()
    columns = [Column(**raw) for raw in record["columns"]]
    writer = csv.writer(buffer)
    writer.writerow([column.label for column in columns])
    for row in record["rows"]:
        writer.writerow([export_value(row.get(column.id), column) for column in columns])
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="result-{result_id[:8]}.csv"'},
    )
