from dataclasses import dataclass
from typing import Literal, get_args

from ...core.deployment import Deployment
from ..definitions import Definition

# In order. Connecting is not a step: a step records that something once succeeded, while being
# reachable is a condition checked now.
Step = Literal["cubes", "describe", "views"]
STEPS: tuple[Step, ...] = get_args(Step)

# Every action a person can run, in order — the one definition of the workflow. `read` records no
# step row, so its state is whether a catalog is cached.
Stage = Literal["read", "cubes", "describe", "views"]
STAGES: tuple[Stage, ...] = get_args(Stage)


@dataclass
class Evidence:
    cubes: tuple[str, ...] = ()
    described: tuple[str, ...] = ()
    views: tuple[str, ...] = ()
    introspected: bool = False
    bodies: bool = False
    enrichment: bool = False

    def done(self, step: str) -> bool:
        if step == "read":
            return self.introspected
        if step == "cubes":
            return bool(self.cubes)
        if step == "describe":
            return bool(self.described)
        if step == "views":
            return bool(self.views)
        return False

    def blocked(self, step: str) -> str:
        # Keyed on the state a step *reads* rather than on a recorded tick, and on presence
        # rather than truth: a describe step that proposed nothing stores an empty enrichment,
        # and blocking on its emptiness would refuse a step whose inputs are there.
        if step == "describe" and not self.bodies:
            return "cubes"
        if step == "views" and not self.enrichment:
            return "describe"
        return ""


def of(definitions: dict[str, Definition], state: dict) -> Evidence:
    """What the model store holds, plus what the steps left in the row.

    It no longer takes what Cube is *serving*. That existed only to spot a model this portal did
    not build — a store that arrived with one already in it — and there is no such case now: a
    deployment is created by onboarding, which builds the model as its last step.
    """
    cubes = sorted(
        definition.name for definition in definitions.values() if definition.kind == "cube"
    )
    return Evidence(
        cubes=tuple(cubes),
        described=tuple(name for name in cubes if definitions[name].body.get("description")),
        views=tuple(
            sorted(
                definition.name for definition in definitions.values() if definition.kind == "view"
            )
        ),
        introspected=bool(state.get("introspection")),
        bodies="bodies" in state,
        enrichment="enrichment" in state,
    )


# There is no `stages()` here any more, and no `stages`/`next_stage` on the body below. They fed
# the data page's four-stage rail, which is gone: the pipeline runs inside onboarding and inside
# Rebuild, and both of those follow the *job* rather than a rail of recorded state. `blocked` stays
# because `steps.action` enforces the ordering rule with it.


def onboarded(deployment: Deployment, found: Evidence) -> bool:
    """Whether setup is finished: both addresses stored, and a model in the store.

    Computed rather than stored, because `core.db.SCHEMA` cannot gain a column —
    `CREATE TABLE IF NOT EXISTS` does not diff, so a new column silently fails to appear on an
    existing `analyst.db` and there is no migration mechanism to add one with.

    Read off the cubes the store holds and not off a recorded step, the same way every other
    question here is: a step record says what this portal once did, and the model is what is
    true now. The wizard is owed until this is true, so getting it from `steps` would let a
    model someone deleted read as finished setup.

    Views and descriptions are deliberately not part of it. Enrichment is optional in the
    wizard and reachable afterwards through the editor chat, so demanding it here would trap
    someone in setup over work they chose to skip.
    """
    return bool(deployment.store_url and deployment.sandbox_url) and found.done("cubes")
