"""The vocabulary of the data source: what it connects to, and where its model lives."""

from pathlib import Path

from ..core.deployment import Deployment
from .drivers import get

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


SERVICES_ROOT = "services"
"""Where the deployments live: one directory per service, each with its own compose file, `.env`
and address. Read from the working directory rather than shipped in this package, because each
one is a separate project with its own lock file and tests.
"""
SEMANTIC_ROOT = f"{SERVICES_ROOT}/semantic"
"""`cube.js`, the store's build context, and the compose file that runs Cube behind it."""


def project_root() -> Path:
    """Nearest ancestor of the cwd holding pyproject.toml. Model paths are relative to it."""
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


SANDBOX_ROOT = f"{SERVICES_ROOT}/sandbox"
"""The code sandbox: its Dockerfile and the compose file that runs it. A sibling of the semantic
layer rather than part of it, because it shares no network, volume or secret with it — and is
optional in a way the semantic layer is not."""


def sandbox_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / SANDBOX_ROOT


def semantic_dir(root: Path | None = None) -> Path:
    """Where `cube.js` and the store's Dockerfile are. One copy, used by the compose file on a
    server and by the containers this portal creates — a second would drift, and the two halves
    of the ref plumbing failing apart is a model served from the wrong branch."""
    return (root or project_root()) / SEMANTIC_ROOT


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


def label_of(deployment: Deployment) -> str:
    """What to call this connection on screen: the database it opens, or the driver."""
    return deployment.credentials.get("CUBEJS_DB_NAME") or get(deployment.driver).label
