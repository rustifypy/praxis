from dataclasses import dataclass, field
from typing import Literal, get_args

from ..core.deployment import Deployment
from .definitions import Definition
from .registry import label_of

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
    catalog: bool = False
    served: int = 0
    state: dict = field(default_factory=dict)

    @property
    def adopted(self) -> bool:
        # Cube is serving a model nothing here produced — an empty rail over working cubes is
        # the whole reason this module exists.
        return not self.cubes and not self.views and self.served > 0

    def done(self, step: str) -> bool:
        if step == "read":
            return self.catalog
        if step == "cubes":
            return bool(self.cubes)
        if step == "describe":
            return bool(self.described)
        if step == "views":
            return bool(self.views)
        return False

    def note(self, step: str) -> str:
        # Nobody re-describes a whole model to finish the last two, so "9 of 11" is honest and
        # still counts as run.
        if step != "describe" or not self.cubes:
            return ""
        if len(self.described) == len(self.cubes):
            return f"all {len(self.cubes)} described"
        return f"{len(self.described)} of {len(self.cubes)} described"

    def blocked(self, step: str) -> str:
        # Keyed on the state a step *reads* rather than on a recorded tick, and on presence
        # rather than truth: a describe step that proposed nothing stores an empty enrichment,
        # and blocking on its emptiness would refuse a step whose inputs are there.
        if step == "describe" and "bodies" not in self.state:
            return "cubes"
        if step == "views" and "enrichment" not in self.state:
            return "describe"
        return ""


def of(definitions: dict[str, Definition], state: dict, served: int = 0) -> Evidence:
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
        catalog=bool(state.get("catalog")),
        served=served,
        state=state,
    )


def stages(steps: dict, found: Evidence) -> list[dict]:
    # A recorded failure wins over the artefacts it left behind: it is the newer information,
    # and a half-written model must not hide the error.
    rail = []
    for stage in STAGES:
        if steps.get(stage, {}).get("status") == "error":
            state = "error"
        else:
            state = "done" if found.done(stage) else "todo"
        rail.append(
            {
                "name": stage,
                "state": state,
                "blocked_by": found.blocked(stage),
                "note": found.note(stage),
            }
        )
    return rail


def invalidated_by(found: Evidence, step: str) -> list[str]:
    # Read off the artefacts and not off `steps`: for an adopted model `steps` is empty while
    # the views a rebuild deletes are right there, so a warning from `steps` would promise
    # nothing is lost.
    if step not in STEPS:
        return []
    return [later for later in STEPS[STEPS.index(step) + 1 :] if found.done(later)]


def public(
    deployment: Deployment,
    steps: dict,
    health: dict,
    found: Evidence,
) -> dict:
    # Every field except the secrets.
    rail = stages(steps, found)
    return {
        "label": label_of(deployment),
        "description": deployment.description,
        "driver": deployment.driver,
        "store_url": deployment.store_url,
        # Not part of `deployed`: a deployment without a sandbox answers questions perfectly
        # well and just cannot run Python.
        "sandbox_url": deployment.sandbox_url,
        "deployed": deployment.deployed,
        "onboarded": deployment.onboarded,
        # Names only, never values, so the edit form can show which fields are set.
        "credential_keys": sorted(deployment.credentials),
        "steps": steps,
        "health": health,
        "adopted": found.adopted,
        "cubes": list(found.cubes),
        "views": list(found.views),
        "invalidates": {step: invalidated_by(found, step) for step in STEPS},
        "stages": rail,
        # One answer, so a sidebar and a page cannot name different next steps.
        "next_stage": next((stage["name"] for stage in rail if stage["state"] != "done"), None),
    }
