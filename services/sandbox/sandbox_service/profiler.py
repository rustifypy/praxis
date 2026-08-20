"""The profiler, as it runs *inside* the sandbox. Never imported by this process.

`profile.of` copies this file into a chat's workspace and runs it there, so the shape of an
uploaded file is read by the same interpreter the agent's own scripts get — duckdb rather than
pandas, because a 100 MB CSV read whole into a dataframe would put the shared container's memory
limit at risk, and an OOM kill takes every other chat's sandbox with it.

A single line of JSON on stdout, always, whatever happened: a file this cannot read is a note the
agent can act on, not a failed upload.
"""

import json
import sys

import duckdb

MAX_COLUMNS = 60
"""Past this, the shape is listed and the statistics are not. Every number below costs a pass
over the file, and sixty columns is already more than a prompt should carry."""
HEAD = 3
CELL = 60


def cell(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= CELL else text[:CELL] + "…"


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def view(con: duckdb.DuckDBPyConnection, path: str) -> None:
    """`t` over whatever this file is. The path is written as an escaped literal rather than a
    parameter so that it works the same on any duckdb version — it is this chat's own upload, in a
    container with no network."""
    literal = "'" + path.replace("'", "''") + "'"
    low = path.lower()
    if low.endswith((".parquet", ".pq")):
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet({literal})")
    elif low.endswith((".json", ".jsonl", ".ndjson")):
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_json_auto({literal})")
    elif low.endswith((".xlsx", ".xls")):
        # The only format duckdb cannot read without an extension it has no network to fetch.
        # A sheet is bounded at a million rows, so pandas is affordable here and nowhere else.
        import pandas

        frame = pandas.read_excel(path)  # noqa: F841 — duckdb reads it by name below
        con.execute("CREATE VIEW t AS SELECT * FROM frame")
    elif low.endswith((".csv", ".tsv", ".txt", ".csv.gz", ".tsv.gz")):
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_csv_auto({literal})")
    else:
        # Named formats only. `read_csv_auto` will happily read a PDF as one column of nonsense
        # and report it as a shape, which is worse than saying there is none.
        raise ValueError(f"{path} is not a tabular file this can read")


def main(path: str) -> dict:
    out: dict = {"rows": 0, "columns": [], "head": [], "note": ""}
    con = duckdb.connect()
    # duckdb defaults its limit to most of the machine's memory, which inside a container is the
    # container's whole cap — and that cap is shared by every chat.
    con.execute("SET memory_limit='512MB'")
    con.execute("SET temp_directory='/tmp'")

    view(con, path)
    stats = con.execute("SUMMARIZE t")
    names = [d[0] for d in stats.description]
    summary = [dict(zip(names, row, strict=False)) for row in stats.fetchall()]
    out["rows"] = con.execute("SELECT count(*) FROM t").fetchone()[0]

    listed = summary[:MAX_COLUMNS]
    if len(summary) > MAX_COLUMNS:
        out["note"] = f"{len(summary)} columns; statistics for the first {MAX_COLUMNS}"
    # Exact distinct counts in one pass, rather than SUMMARIZE's approximate ones: "this column
    # is unique" is what an analyst joins on, and an approximation cannot say it.
    counts = []
    if listed:
        selected = ", ".join(f"count(DISTINCT {quoted(c['column_name'])})" for c in listed)
        counts = list(con.execute(f"SELECT {selected} FROM t").fetchone())

    for column, distinct in zip(listed, counts, strict=False):
        nulls = float(column.get("null_percentage") or 0)
        out["columns"].append(
            {
                "name": column["column_name"],
                "type": column["column_type"],
                "distinct": int(distinct),
                "nulls": round(nulls / 100, 4),
                "min": cell(column.get("min")),
                "max": cell(column.get("max")),
                "unique": bool(distinct == out["rows"] and not nulls),
            }
        )

    rows = con.execute(f"SELECT * FROM t LIMIT {HEAD}")
    header = [d[0] for d in rows.description]
    out["head"] = [
        dict(zip(header, [cell(v) for v in row], strict=False)) for row in rows.fetchall()
    ]
    return out


if __name__ == "__main__":
    try:
        result = main(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 — the note is the answer when a file will not read
        result = {
            "rows": 0,
            "columns": [],
            "head": [],
            "note": f"{type(exc).__name__}: {exc}"[:300],
        }
    # One line, so the host can take the last line of stdout and ignore anything a library said.
    print(json.dumps(result, default=str))
