"""Model generation: read the database, build cubes, ask the model, let Cube judge.

Changing a model afterwards is `..edit`, which depends on this. Nothing here depends on it.
"""

# The submodules callers reach as `model.read`, `model.run` and so on. Everything else is
# imported from its own module by name.
from . import decide, read, run, state, warehouse  # noqa: F401
