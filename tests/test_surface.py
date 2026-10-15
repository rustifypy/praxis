"""The HTTP surface as a snapshot, so a restructure cannot move it by accident.

This exists for one job: `web/` is being split into routes, controllers and DTOs, and roughly half
of its routes have no test that calls them. A refactor that silently renames a path or drops a
route would otherwise be found in the browser, days later, by someone clicking.

Two snapshots, both in `tests/surface.json`:

- **the route table** — every method and path the app serves, with its handler's name. This is what
  catches a route that moved, vanished or gained a path parameter.
- **the response schema of each route**, from the app's own OpenAPI document. Most routes answer a
  bare `dict` today and so have no schema, which is exactly what the migration is fixing — so this
  snapshot's *diff* is the record of which payloads became typed, and the only reliable check that
  adding `response_model=` did not change a shape while it was at it.

Regenerate deliberately, never incidentally: `UPDATE_SURFACE=1 uv run pytest tests/test_surface.py`,
then read the diff before committing it. That diff is the changelog of the wire format.
"""

import json
import os
import pathlib
import re

from fastapi.routing import APIRoute

from praxis.api import server

SNAPSHOT = pathlib.Path(__file__).with_name("surface.json")

# Starlette's own additions and FastAPI's docs pages. Neither is this project's surface, and they
# appear or disappear with a framework upgrade.
NOT_OURS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def declared() -> list[APIRoute]:
    """Every `APIRoute` the app serves, in registration order.

    Registration order is the reason this walks the app instead of reading the OpenAPI document,
    which is alphabetical: order is what decides which of two matching paths wins, and
    `test_every_route_is_reachable_and_unshadowed` is an assertion about precisely that.

    FastAPI 0.141 does not flatten an included router into `app.routes` — it appends a wrapper
    holding the real router — so this descends through anything carrying an `original_router`.
    Written to accept both layouts rather than the current one, because the attribute is not part
    of FastAPI's public surface; `test_the_walk_sees_every_route_the_document_does` is what stops
    a future change to it turning this file into a test of nothing.
    """
    found: list[APIRoute] = []

    def walk(routes) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            elif isinstance(route, APIRoute) and route.path not in NOT_OURS:
                found.append(route)

    walk(server.app.routes)
    return found


def plain(path: str) -> str:
    """A path as the OpenAPI document spells it: converters dropped.

    OpenAPI has no notion of Starlette's converters, so `{path:path}` is published as `{path}`.
    Looking a route's schema up by its real path therefore misses for every greedy route — which
    silently reported four of them as answering nothing at all, `response_model` or not.
    """
    return re.sub(r"\{(\w+):\w+\}", r"{\1}", path)


def surface() -> dict:
    """Every route this app serves, and what each one answers with."""
    document = server.app.openapi()
    routes = []
    for route in declared():
        published = plain(route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            # The 200 schema by reference name only. The full definition lives in the document's
            # components, and asserting it here would make every unrelated field rename fail in
            # this file rather than in the noun's own tests.
            answers = (
                document["paths"]
                .get(published, {})
                .get(method.lower(), {})
                .get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            routes.append(
                {
                    "method": method,
                    "path": route.path,
                    "name": route.name,
                    "answers": answers.get("$ref", "").rpartition("/")[2]
                    or answers.get("type", ""),
                }
            )
    routes.sort(key=lambda entry: (entry["path"], entry["method"]))
    return {"routes": routes}


def test_the_walk_sees_every_route_the_document_does():
    """The guard on `declared()`: what the walk found, against FastAPI's own public document.

    Without this, a framework change to how included routers are stored would leave the walk
    returning two routes and every assertion below passing vacuously — which is how a safety net
    becomes a test that only proves it can run.
    """
    document = server.app.openapi()
    published = {
        (method.upper(), path)
        for path, methods in document["paths"].items()
        if path not in NOT_OURS
        for method in methods
    }
    walked = {
        (method, plain(route.path))
        for route in declared()
        for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    }
    assert walked == published, "the router walk and the OpenAPI document disagree"
    # A floor, so an empty document cannot make the comparison above trivially true.
    assert len(walked) > 40, f"only {len(walked)} routes found — the walk is not descending"


def test_the_surface_is_what_was_last_agreed():
    """The route table and every route's response schema, against the committed snapshot.

    A failure is not necessarily a bug — a deliberate rename fails it too. It means: read the diff,
    decide whether that is what you meant, and regenerate with `UPDATE_SURFACE=1`.
    """
    found = surface()
    if os.environ.get("UPDATE_SURFACE"):
        SNAPSHOT.write_text(json.dumps(found, indent=2) + "\n")
        return

    expected = json.loads(SNAPSHOT.read_text())
    listed = {(entry["method"], entry["path"]) for entry in expected["routes"]}
    serving = {(entry["method"], entry["path"]) for entry in found["routes"]}
    assert serving - listed == set(), f"routes appeared: {sorted(serving - listed)}"
    assert listed - serving == set(), f"routes vanished: {sorted(listed - serving)}"
    # Compared last, so a moved route is reported as a moved route rather than as a schema diff.
    assert found == expected, "a handler name or a response schema changed — read the diff"


def test_every_route_is_reachable_and_unshadowed():
    """No two routes share a method and path, and no catch-all is registered above its siblings.

    Both are silent failures: a duplicate means whichever router was included first answers, and a
    `{param}` registered before a fixed-segment sibling swallows it. `results.py` and `pipeline.py`
    each already carry a comment about this exact bug, one layer down.
    """
    order = [
        (method, route.path)
        for route in declared()
        for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    ]

    seen: set[tuple[str, str]] = set()
    for key in order:
        assert key not in seen, f"{key} is registered twice — the first one included answers"
        seen.add(key)

    for index, (method, path) in enumerate(order):
        head, _, tail = path.rpartition("/")
        # `{path:path}` matches across segments, so it shadows anything under its parent, not only
        # a sibling of the same depth.
        greedy = tail.endswith(":path}")
        if not tail.startswith("{"):
            continue
        for later_method, later in order[index + 1 :]:
            if later_method != method:
                continue
            later_head, _, later_tail = later.rpartition("/")
            shadowed = (
                later.startswith(f"{head}/") if greedy else later_head == head
            ) and not later_tail.startswith("{")
            assert not shadowed, (
                f"{method} {later} is unreachable: {path} is registered before it and its "
                f"parameter matches that segment"
            )
