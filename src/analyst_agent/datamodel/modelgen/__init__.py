"""Model generation: read the database, build cubes, ask the model, let Cube judge.

`modelgen` and not `model`, because in this codebase "model" is also the LLM — `core.llm`
builds one, and `decide.py` here calls it. Changing a generated model afterwards is
`..editing`, which depends on this; nothing here depends on it.
"""

# The submodules callers reach as `modelgen.read`, `modelgen.steps` and so on. Everything
# else is imported from its own module by name.
from . import decide, read, state, steps, warehouse  # noqa: F401
