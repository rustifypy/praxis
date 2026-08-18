"""The vocabulary of the data source: what it connects to, and where its model lives."""

from dataclasses import dataclass, field
from pathlib import Path

from .drivers import Driver, get

MODEL_ROOT = "cube/model"
"""The only thing this tool writes into the working directory. Everything else it needs —
analyst.db, the job lock, the catalog probe's throwaway model — is machine-local under
DATA_DIR. This one is not, because the model is the user's to version-control.
"""
GROUNDING_ROOT = "grounding"
"""Inside the model directory, which is bind-mounted as Cube's model. Cube compiles every
`*.yml` anywhere under that mount, not just under `cubes/` and `views/`, so grounding is
markdown — one written as YAML takes the whole model to zero cubes.
"""
ARTEFACT_DIRS = ("rules", "skills", "certified_queries")
"""The directories `Grounding` reads. Scaffolding a different set would create folders
nothing loads, so both sides take this list."""


def project_root() -> Path:
    """Nearest ancestor of the cwd holding pyproject.toml. Model paths are relative to it."""
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


def model_dir(root: Path | None = None) -> Path:
    """What the container bind-mounts as its model, and what `git` versions."""
    return (root or project_root()) / MODEL_ROOT


def grounding_dir(root: Path | None = None) -> Path:
    """The rules, skills and certified queries the agent believes before a question."""
    return model_dir(root) / GROUNDING_ROOT


def scaffold_dirs(root: Path | None = None) -> Path:
    """Create everything the model directory holds, and return it."""
    base = model_dir(root)
    grounding = (f"{GROUNDING_ROOT}/{artefact}" for artefact in ARTEFACT_DIRS)
    for sub in ("cubes", "views", *grounding):
        (base / sub).mkdir(parents=True, exist_ok=True)
        (base / sub / ".gitkeep").touch()
    return base


@dataclass
class Source:
    """What the Cube container connects to. One row in `source`, credentials included."""

    driver: str
    description: str = ""
    mounts: dict[str, str] = field(default_factory=dict)
    """container path -> host path, for drivers that need a key file."""
    credentials: dict[str, str] = field(default_factory=dict)
    """CUBEJS_DB_* -> value. Lives in analyst.db and reaches the container as `-e`.
    Never returned by an HTTP endpoint — see `store.public`."""

    @property
    def spec(self) -> Driver:
        return get(self.driver)

    @property
    def label(self) -> str:
        """What to call this connection on screen: the database it opens, or the driver."""
        return self.credentials.get("CUBEJS_DB_NAME") or self.spec.label
