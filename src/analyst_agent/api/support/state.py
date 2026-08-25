"""What `app.state` holds, and the accessors that are not plain attributes.

There is no store handle here. The stores became modules reading `settings.db_path`, so a route
imports the one it needs rather than being handed an instance that carried nothing but a path.
The Cube session stays because it carries an *address*: an edited URL has to replace it.

`idle` and `reloaded` are here rather than beside any one router because three of them need each,
and the busy refusal was written out five times before it was one function — the message a person
reads is part of the rule, not a string each route gets to spell for itself.
"""

from fastapi import HTTPException

from ...core import db, jobs
from ...core import deployment as connection
from ...modelstore.client import Bound, ModelStore


async def init_state(app) -> None:
    # One creator for the schema and the file mode, called once — every store used to call it,
    # which was forty reflection round trips to create nothing.
    await db.create_tables()
    app.state.jobs = jobs.Runner()
    # (monotonic time, probe), or None. See `connection.HEALTH_TTL_S`.
    app.state.health = None
    # Built on first use and rebuilt when the row's address or secret moves — see `cube`.
    app.state.cube = None
    app.state.cube_key = None


def idle(app) -> None:
    # One job at a time, machine-wide.
    if app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")


def reloaded(app) -> None:
    # Nothing to restart: Cube asks the store for the version of the ref it serves on every
    # request, so a commit is picked up on its own and only the cached probe is stale.
    app.state.health = None


async def cube(app) -> Bound:
    # A held client carries an address, so an edited URL has to replace it rather than be
    # ignored — hence the key. What is held is the session; what is handed out is a view of it
    # pinned to main, because a question chat and a dashboard are never on an edit ref.
    deployment = await connection.current()
    if deployment is None or not deployment.store_url:
        raise HTTPException(503, "no Cube deployment is configured")
    key = (deployment.store_url, deployment.store_token)
    if app.state.cube_key != key:
        if app.state.cube is not None:
            await app.state.cube.aclose()
        app.state.cube = ModelStore.for_deployment(deployment)
        app.state.cube_key = key
    return app.state.cube.at()
