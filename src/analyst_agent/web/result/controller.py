"""Reading a stored result, and turning one into a CSV.

**Neither function raises `HTTPException`, and neither knows a status code.** An unknown id comes
back as `None` and the route decides that is a 404 — because whether a missing record is a 404, an
empty page or a redirect is an HTTP question, and answering it here would put the decision in the
one layer that cannot see the request.
"""

import csv
import io

from ... import transcript
from ...modelstore.shape import Column, export_value
from .dto import ResultPage


async def page(result_id: str, offset: int, limit: int) -> ResultPage | None:
    """One page of a result set, or `None` when no result has that id.

    Sliced here rather than in SQL: the rows are stored as a single JSON blob, so there is no
    query that could return page two on its own. Which means `limit` bounds what is *serialised*,
    not what is read — the whole set is in memory either way.
    """
    record = await transcript.result(result_id)
    if record is None:
        return None
    return ResultPage(
        id=record["id"],
        columns=record["columns"],
        rows=record["rows"][offset : offset + limit],
        row_count=record["row_count"],
        offset=offset,
    )


async def as_csv(result_id: str) -> str | None:
    """The whole result set as CSV text, or `None` when no result has that id.

    Labels in the header and not member ids, which is the one thing this does not share with the
    agent's `export_result` — that writes ids, because a script refers to `sales.invoice_total`
    and a person reads "Invoice total".

    Never paged: a spreadsheet of the first 200 rows silently answers a different question than
    the one that was asked.
    """
    record = await transcript.result(result_id)
    if record is None:
        return None
    columns = [Column(**raw) for raw in record["columns"]]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([column.label for column in columns])
    for row in record["rows"]:
        writer.writerow([export_value(row.get(column.id), column) for column in columns])
    return buffer.getvalue()
