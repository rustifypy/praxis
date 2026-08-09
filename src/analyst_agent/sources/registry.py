"""The vocabulary of a data source, and the rules for naming and porting one.

A source's data lives in `store.py` — this module is the shape it takes in memory, the
paths it owns on disk, and the two rules that must read the same whether the caller is
the CLI or an HTTP handler.

Credentials are part of the `Source` now. There is no registry file and no per-source
credential file; `docker.py` builds a container from this object and nothing else.
"""

import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from .drivers import Driver, get, missing_fields

MODEL_ROOT = "cube/sources"
"""The only thing this tool writes into the working directory. Everything else it needs
— sources.db, the job lock, the catalog probe's throwaway model — is machine-local under
`~/.analyst`, because the working directory belongs to whoever installed this."""
GROUNDING_ROOT = "grounding"
"""Grounding sits inside the source's own directory, which is bind-mounted as Cube's
model. Cube compiles every `*.yml` it finds anywhere under that mount, not just under
`cubes/` and `views/` — a grounding file written as YAML takes the whole model to zero
cubes and reports the compile error against the grounding file. Artefacts here are
markdown only, which is what `grounding._load_dir` globbing `*.md` enforces."""
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
        """Whether the agent queries this source. `CUBE_URL` decides, not the registry.

        A property rather than a check at each call site: the CLI's `rm`, the delete
        route and the panel all refuse or mark on this, and they must agree.
        """
        return self.url.rstrip("/") == settings.cube_url.rstrip("/")

    def model_dir(self, root: Path | None = None) -> Path:
        return (root or project_root()) / MODEL_ROOT / self.name

    def grounding_dir(self, root: Path | None = None) -> Path:
        """Where this source's rules, skills and certified queries live.

        Per source and not global, because grounding names members: a rule saying
        "revenue" means `revenue.net_revenue` is nonsense against a warehouse that has
        no revenue cube, and the agent was being told it for every source regardless.
        """
        return self.model_dir(root) / GROUNDING_ROOT


def next_port(taken: Iterable[int] = ()) -> int:
    """First free port at or above 4001, skipping anything already listening.

    Takes the assigned ports rather than reading them, for the same reason
    `validate_name` does — one rule, callable from the CLI or an HTTP handler.
    """
    assigned = set(taken)
    port = FIRST_PORT
    while port in assigned or port_in_use(port):
        port += 1
    return port


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def validate_name(name: str, taken: Iterable[str] = ()) -> str | None:
    """Returns an error message, or None when the name is usable."""
    if not name:
        return "a name is required"
    if not name[0].isalpha() or not all(c.isalnum() or c == "-" for c in name):
        return "use lowercase letters, digits and hyphens, starting with a letter"
    if name != name.lower():
        return "use lowercase — the name becomes a container and directory name"
    if name in set(taken):
        return f"source {name!r} already exists"
    return None


def validate_new(
    name: str, driver: str, port: int, values: dict[str, str], existing: list[Source]
) -> str | None:
    """Everything that makes a proposed source invalid, or None. The first error wins.

    Both the CLI and `POST /api/sources` ask this, so "what is a valid new source"
    cannot come to mean two things.
    """
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
    """Create everything a source owns on disk, and return the directory to bind-mount.

    Cube reads /cube/conf/model, which is `model_dir` — empty until the model generator
    writes into it. Made before the container starts: docker would otherwise create the
    bind-mount target as root, and Cube could not read the model written into it later.

    The grounding directories are made empty here for a different reason. `Grounding`
    tolerates them being absent, so this is purely so they can be found: nobody writes a
    rule into a folder they cannot see.
    """
    base = source.model_dir(root)
    grounding = (f"{GROUNDING_ROOT}/{artefact}" for artefact in ARTEFACT_DIRS)
    for sub in ("cubes", "views", *grounding):
        (base / sub).mkdir(parents=True, exist_ok=True)
        (base / sub / ".gitkeep").touch()
    return base
