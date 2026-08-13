"""The vocabulary of a data source, and the rules for naming and porting one."""

import re
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from .drivers import Driver, get, missing_fields

MODEL_ROOT = "cube/sources"
"""The only thing this tool writes into the working directory. Everything else it needs —
sources.db, the job lock, the catalog probe's throwaway model — is machine-local under.
"""
GROUNDING_ROOT = "grounding"
"""Inside the source's own directory, which is bind-mounted as Cube's model. Cube compiles every
`*.yml` anywhere under that mount, not just under `cubes/` and `views/` — one written as YAML.
"""
ARTEFACT_DIRS = ("rules", "skills", "certified_queries")
"""The directories `Grounding` reads. Scaffolding a different set would create folders
nothing loads, so both sides take this list."""
FIRST_PORT = 4001


def project_root() -> Path:
    """Nearest ancestor of the cwd holding pyproject.toml. Model paths are relative to it."""
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


@dataclass
class Source:
    name: str
    description: str
    driver: str
    port: int
    mounts: dict[str, str] = field(default_factory=dict)
    """container path -> host path, for drivers that need a key file."""
    credentials: dict[str, str] = field(default_factory=dict)
    """CUBEJS_DB_* -> value. Lives in sources.db and reaches the container as `-e`.
    Never returned by an HTTP endpoint — see `store.public`."""

    @property
    def service(self) -> str:
        """The container name. `cube-` prefixed so `docker ps` reads as ours."""
        return f"cube-{self.name}"

    @property
    def spec(self) -> Driver:
        return get(self.driver)

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def is_active(self) -> bool:
        """Whether the agent queries this source. `CUBE_URL` decides, not the registry."""
        return self.url.rstrip("/") == settings.cube_url.rstrip("/")

    def model_dir(self, root: Path | None = None) -> Path:
        return (root or project_root()) / MODEL_ROOT / self.name

    def grounding_dir(self, root: Path | None = None) -> Path:
        """This source's rules, skills and certified queries. Per source and not global, because
        grounding names members — "revenue" means `revenue.net_revenue` is nonsense against a.
        """
        return self.model_dir(root) / GROUNDING_ROOT


def next_port(taken: Iterable[int] = ()) -> int:
    """First free port at or above 4001, skipping anything already listening."""
    assigned = set(taken)
    port = FIRST_PORT
    while port in assigned or port_in_use(port):
        port += 1
    return port


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


NAME_PATTERN = r"^[a-z][a-z0-9-]*$"
"""Shipped to the form by `/api/drivers` so both sides test the same rule. ASCII explicitly:
`str.isalpha` and `str.isalnum` accept `ωmega` and `café`, and the name becomes a container
name and a directory name."""


def validate_name(name: str, taken: Iterable[str] = ()) -> str | None:
    """Returns an error message, or None when the name is usable."""
    if not name:
        return "a name is required"
    if not re.match(NAME_PATTERN, name):
        return "use lowercase letters, digits and hyphens, starting with a letter"
    if name in set(taken):
        return f"source {name!r} already exists"
    return None


def validate_new(
    name: str, driver: str, port: int, values: dict[str, str], existing: list[Source]
) -> str | None:
    """Everything that makes a proposed source invalid, or None. The first error wins."""
    if error := validate_name(name, [s.name for s in existing]):
        return error
    try:
        spec = get(driver)
    except KeyError as exc:
        return str(exc)
    if missing := missing_fields(spec, values):
        return f"missing required fields: {', '.join(missing)}"
    if any(s.port == port for s in existing):
        return f"port {port} is already assigned to another source"
    return None


def scaffold_source_dirs(source: Source, root: Path | None = None) -> Path:
    """Create everything a source owns on disk, and return the directory to bind-mount."""
    base = source.model_dir(root)
    grounding = (f"{GROUNDING_ROOT}/{artefact}" for artefact in ARTEFACT_DIRS)
    for sub in ("cubes", "views", *grounding):
        (base / sub).mkdir(parents=True, exist_ok=True)
        (base / sub / ".gitkeep").touch()
    return base
