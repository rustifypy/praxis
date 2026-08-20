"""Where the pipeline stands, read off the model files rather than off `steps`.

The case that matters is a model this portal did not build — a clone, a colleague's branch, a
deployment already serving thirteen cubes. `steps` is empty for all of them, and a rail keyed on
it reports a working model as unstarted.
"""

from dataclasses import dataclass, field
from typing import Literal, get_args

from ..core.deployment import Deployment
from .definitions import Definition
from .registry import label_of

Step = Literal["cubes", "describe", "views"]
"""In order. Connecting is not one: a step records that something *once* succeeded, and a
deployment nobody has run makes such a record a lie — being reachable is a condition, checked
now."""
STEPS: tuple[Step, ...] = get_args(Step)

Stage = Literal["read", "cubes", "describe", "views"]
"""Every action a person can run, in order — the one definition of the workflow. `read` is the
odd one: it records no step row, so its state is whether a catalog is cached."""
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
        """Cube is serving a model nothing here produced. The empty rail over thirteen working
        cubes is the whole reason this module exists."""
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
        """Nobody re-describes a whole model to finish the last two, so "9 of 11" is the honest
        thing to say — and it still counts as run."""
        if step != "describe" or not self.cubes:
            return ""
        if len(self.described) == len(self.cubes):
            return f"all {len(self.cubes)} described"
        return f"{len(self.described)} of {len(self.cubes)} described"

    def blocked(self, step: str) -> str:
        """What is holding `step` up, keyed on the state it *reads* rather than on a recorded
        tick. `describe` reads the cube bodies `cubes` stored, so a model built elsewhere is
        honestly blocked however complete the rail looks."""
        # Presence, not truth: `steps.views` reads `state["enrichment"]`, and a model whose
        # describe step proposed nothing stores an empty one. Blocking on that would refuse a
        # step whose inputs are there.
        if step == "describe" and "bodies" not in self.state:
            return "cubes"
        if step == "views" and "enrichment" not in self.state:
            return "describe"
        return ""


def of(definitions: dict[str, Definition], state: dict, served: int = 0) -> Evidence:
    cubes = sorted(d.name for d in definitions.values() if d.kind == "cube")
    return Evidence(
        cubes=tuple(cubes),
        described=tuple(name for name in cubes if definitions[name].body.get("description")),
        views=tuple(sorted(d.name for d in definitions.values() if d.kind == "view")),
        catalog=bool(state.get("catalog")),
        served=served,
        state=state,
    )


def stages(steps: dict, found: Evidence) -> list[dict]:
    """The whole progress rail. A recorded failure wins over the artefacts it left behind: it is
    the newer information, and a half-written model must not hide the error."""
    out = []
    for stage in STAGES:
        if steps.get(stage, {}).get("status") == "error":
            state = "error"
        else:
            state = "done" if found.done(stage) else "todo"
        out.append(
            {
                "name": stage,
                "state": state,
                "blocked_by": found.blocked(stage),
                "note": found.note(stage),
            }
        )
    return out


def invalidated_by(found: Evidence, step: str) -> list[str]:
    """Which later steps re-running `step` would throw away. Read off the artefacts, not
    `steps` — for an adopted model `steps` is empty while the views a rebuild deletes are
    right there, so a warning from `steps` would promise nothing is lost."""
    if step not in STEPS:
        return []
    return [s for s in STEPS[STEPS.index(step) + 1 :] if found.done(s)]


def public(
    deployment: Deployment,
    steps: dict,
    health: dict,
    found: Evidence,
) -> dict:
    """The deployment as the browser may see it — every field except the secrets."""
    rail = stages(steps, found)
    return {
        "label": label_of(deployment),
        "description": deployment.description,
        "driver": deployment.driver,
        "store_url": deployment.store_url,
        # Optional, unlike the other two: a deployment without a sandbox answers questions and
        # just cannot run Python, so it is not part of `deployed`.
        "sandbox_url": deployment.sandbox_url,
        # Whether anything is running yet. The portal does not run it, so this is something it
        # is told rather than something it arranges — and the panel says so instead of drawing
        # a rail for a deployment that does not exist.
        "deployed": deployment.deployed,
        # Which connection fields are set, so the edit form can show them filled —
        # names only, never values.
        "credential_keys": sorted(deployment.credentials),
        "mounts": dict(deployment.mounts),
        "steps": steps,
        "health": health,
        "adopted": found.adopted,
        "cubes": list(found.cubes),
        "views": list(found.views),
        "invalidates": {step: invalidated_by(found, step) for step in STEPS},
        "stages": rail,
        # The primary action every screen offers. One answer, so a sidebar and a page cannot
        # name different next steps.
        "next_stage": next((s["name"] for s in rail if s["state"] != "done"), None),
    }
