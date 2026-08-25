"""What the browser actually receives, byte for byte, snapshotted.

`test_surface.py` records the route *table* and each route's declared schema. That is not enough
for the migration in `mvc-migration.md`: Phase 2 gives every route a `response_model`, which
changes the declared schema **on purpose** — so the one artefact that could have caught a payload
changing at the same time is the one artefact guaranteed to be full of expected noise.

This is the missing half. It calls the routes and records the JSON, so "the shape is unchanged" is
an assertion rather than a hope. A `response_model` that drops a key, renames one, coerces a type
or starts emitting `null` where the dict emitted nothing fails here with a diff of the payload.

Volatile fields — ids, timestamps, minted tokens — are replaced before comparison, because they
change per run and would make the snapshot worthless. Everything else is compared exactly. Keeping
that list short is deliberate: each entry is a field this file has stopped watching.

Regenerate deliberately: `UPDATE_PAYLOADS=1 uv run pytest tests/test_payloads.py`, then read the
diff. Anything in it during Phases 1-3 is a bug in the migration, not a new baseline.
"""

import json
import os
import pathlib
import re

from analyst_agent.modelstore.client import Ref
from analyst_agent.modelstore.editing import workspace
from analyst_agent.web import model as model_routes

SNAPSHOT = pathlib.Path(__file__).with_name("payloads.json")

# Volatility is recognised by the *value*, not by the key. Redacting every field called `id`
# would blank `orders.count` and `orders.status` — member ids and column ids are the content here,
# and a snapshot that cannot see them would miss a `response_model` renaming one.
GENERATED_ID = re.compile(r"^[0-9a-f]{32}$")
EPOCH = 1_000_000_000  # any float this large in a payload is a unix timestamp

# The exceptions, which no value-shaped rule can catch: a minted token and the two config blocks
# it is pasted into. Each entry is a field this file has stopped watching, so the list stays short.
VOLATILE = {"token", "desktop", "cli", "duration_ms"}

CHINOOK = {
    "driver": "postgres",
    "description": "Chinook",
    "values": {
        "CUBEJS_DB_HOST": "host.docker.internal",
        "CUBEJS_DB_PORT": "5433",
        "CUBEJS_DB_NAME": "chinook",
        "CUBEJS_DB_USER": "analyst",
        "CUBEJS_DB_PASS": "chinook",
    },
}


def stable(value):
    """The payload with its per-run values blanked, so two runs of the same code agree."""
    if isinstance(value, dict):
        return {
            key: "<minted>" if key in VOLATILE else stable(inner) for key, inner in value.items()
        }
    if isinstance(value, list):
        return [stable(item) for item in value]
    if isinstance(value, str) and GENERATED_ID.match(value):
        return "<id>"
    # `True` is an int in Python, and `isinstance(True, int)` is why bool is excluded first.
    if isinstance(value, float | int) and not isinstance(value, bool) and value >= EPOCH:
        return "<time>"
    return value


class Listing:
    """A store that answers the three read calls the ref and file routes make.

    Its own class rather than `conftest.FakeStore` because that one keeps its refs in an attribute
    literally called `refs`, so giving it the client's `refs()` *method* clobbers the dict and
    every later `files()` call fails. Not a problem in the fake — nothing else needs both — but
    this collector reads refs and files in the same pass.
    """

    async def refs(self) -> list[Ref]:
        return [Ref("main", "a", 1, False), Ref("feature", "b", 2, False)]

    async def diff(self, ref: str) -> dict:
        return {"changed": ["cubes/orders.yml"], "patch": "diff --git a b"}

    async def rev(self, ref: str = "main") -> int:
        return 1

    async def files(self, ref: str = "main", *, refresh: bool = False) -> dict[str, str]:
        return {
            "cubes/orders.yml": "cubes: []",
            "views/revenue.yml": "views: []",
            # Filtered out by `workspace.resolve`, which is what the route lists through — so its
            # presence here is the assertion that the filter is still applied.
            "README.txt": "not a model file",
        }


async def collect(api, monkeypatch, catalog, model_store, opening) -> dict:
    """Every payload this can reach without a container, keyed by what it is.

    Read-shaped routes and the two writes whose *answer* is the thing the browser renders. The
    streaming routes are absent on purpose: their frames are the event contract and
    `test_transcript.py` owns that.
    """
    out: dict = {}

    out["chats: none yet"] = (await api.get("/api/chats")).json()
    chat = (await api.post("/api/chats", json={"title": "Revenue"})).json()
    out["chats: one created"] = chat

    # Every connection route answers the same body — `evidence.public` plus four keys — so this
    # one payload is the contract for five routes. See the plan's Part 1 note about who owns it.
    out["connection: stored"] = (await api.post("/api/connection", json=CHINOOK)).json()
    out["connection: read back"] = (await api.get("/api/connection")).json()

    out["dashboards: none yet"] = (await api.get("/api/dashboards")).json()
    board = (await api.post("/api/dashboards", json={"title": "Sales"})).json()
    out["dashboards: created"] = board
    out["dashboard: no tiles, never refreshed"] = (
        await api.get(f"/api/dashboards/{board['id']}")
    ).json()

    out["pipeline: nothing read yet"] = (await api.get("/api/pipeline/tables")).json()
    out["pipeline: no job running"] = (await api.get("/api/pipeline/job")).json()

    # With a catalog cached, so the per-table row and the schema wrapper are actually in the
    # snapshot. Without this the `schemas` list is empty in every payload here and the key the
    # picker reads each schema by is never compared — which is how an alias silently changes.
    await api.stores[2].save_state(
        {
            "introspection": {
                "public": {
                    "tables": [
                        {
                            "name": "invoice",
                            "schema": "public",
                            "columns": [{"name": "id", "data_type": "integer"}],
                            "primary_key": ["id"],
                            "foreign_keys": [],
                        },
                        {
                            "name": "invoice_line",
                            "schema": "public",
                            "columns": [{"name": "invoice_id", "data_type": "integer"}],
                            "primary_key": [],
                            "foreign_keys": [
                                {
                                    "columns": ["invoice_id"],
                                    "ref_table": "invoice",
                                    "ref_columns": ["id"],
                                }
                            ],
                        },
                    ]
                }
            }
        }
    )
    out["pipeline: a schema was read"] = (await api.get("/api/pipeline/tables")).json()
    out["pipeline: closing over one table"] = (
        await api.post(
            "/api/pipeline/tables/closure",
            json={"schema_name": "public", "tables": ["invoice_line"]},
        )
    ).json()

    columns = [
        {
            "id": "orders.status",
            "label": "Status",
            "kind": "dimension",
            "type": "string",
            "format": "",
            "align": "left",
        }
    ]
    # Against the chat, not the board: `results` has a foreign key to `chats`, and
    # `PRAGMA foreign_keys` is on for every connection `core.db.connect` opens.
    result_id = await api.stores[0].save_result(
        chat["id"], "turn-1", columns, [{"orders.status": "paid"}]
    )
    out["result: one page"] = (await api.get(f"/api/results/{result_id}")).json()

    # Set here rather than in the `api` fixture: only the real lifespan populates it, and it
    # reads the database, which the fixture deliberately does not run.
    from analyst_agent.core.access import Access
    from analyst_agent.web import server

    server.app.state.mcp = Access(enabled=True, token="tok-for-the-snapshot")
    server.app.state.mcp_max_rows = 100
    out["agent access: enabled"] = (await api.get("/api/mcp")).json()

    async def files(ref="main"):
        return {"cubes/everything.yml": "cubes:\n  - name: orders\n  - name: customers\n"}

    async def served(*, ref="main"):
        return catalog

    monkeypatch.setattr(model_routes, "model_files", files)
    monkeypatch.setattr(model_routes, "served_catalog", served)
    out["model: served"] = (await api.get("/api/model")).json()

    monkeypatch.setattr(model_routes, "open_store", opening(Listing()))
    out["model: refs"] = (await api.get("/api/model/refs")).json()
    out["model: files on a ref"] = (await api.get("/api/model/files?ref=feature")).json()

    async def verify(store, ref, write, delete):
        return workspace.Verdict(
            ok=False,
            kind="rejected",
            reason="it does not compile",
            errors=["views/revenue.yml: no such member orders.nope"],
            warnings=["a tile reads this view"],
            compile_error="Member not found: orders.nope",
        )

    monkeypatch.setattr(workspace, "verify", verify)
    out["model: a change set Cube refuses"] = (
        await api.post("/api/model/check", json={"ref": "feature", "write": {}})
    ).json()

    return out


async def test_no_payload_changed(api, monkeypatch, catalog, model_store, opening):
    """Every reachable payload against the committed snapshot.

    A failure means the wire format moved. During the routes/controllers/DTO migration that is a
    bug by definition — Phases 1-3 are supposed to change where code lives and nothing else.
    """
    found = stable(await collect(api, monkeypatch, catalog, model_store, opening))
    if os.environ.get("UPDATE_PAYLOADS"):
        SNAPSHOT.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n")
        return

    expected = json.loads(SNAPSHOT.read_text())
    assert sorted(found) == sorted(expected), "a payload was added or dropped from the snapshot"
    for name in sorted(expected):
        assert found[name] == expected[name], f"the payload of {name!r} changed"


async def test_the_snapshot_covers_what_it_claims_to(
    api, monkeypatch, catalog, model_store, opening
):
    """A floor on the snapshot, so it cannot quietly shrink to nothing.

    A payload that stops being collected — because a route was renamed and the call now 404s —
    would otherwise pass as "no payload changed" once the snapshot was regenerated.
    """
    found = await collect(api, monkeypatch, catalog, model_store, opening)
    assert len(found) >= 15, f"only {len(found)} payloads collected"
    empty = [name for name, body in found.items() if body in ({}, [], None)]
    assert empty == ["chats: none yet", "dashboards: none yet"], f"unexpectedly empty: {empty}"
