from dataclasses import dataclass

from ..modelstore.client import ADMIN_TIMEOUT_S, StoreError, served_catalog


@dataclass
class Serving:
    cubes: int
    views: list[str]
    described: int


STATES = ("not deployed", "unreachable", "broken", "empty", "ok")


def state(deployed: bool, ok: bool, served: "Serving | None") -> str:
    # `deployed` and not `running`: the portal starts nothing, so it can never observe a stop.
    if not deployed:
        return "not deployed"
    if not ok:
        return "unreachable"
    if served is None:
        return "broken"
    return "ok" if served.cubes else "empty"


async def read(timeout: float = ADMIN_TIMEOUT_S) -> Serving | None:
    # Counted over cubes only: a view's members are the same members re-exported, so counting
    # every entity counts the model twice.
    try:
        catalog = await served_catalog(timeout=timeout)
        return Serving(
            cubes=sum(1 for entity in catalog.entities if not entity.is_view),
            views=[entity.name for entity in catalog.views],
            described=sum(1 for entity in catalog.entities if entity.description),
        )
    except StoreError:
        return None
