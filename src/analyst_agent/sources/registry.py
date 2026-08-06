"""sources.yml — the list of data sources, one Cube container each.

Credentials never live here. They go to .env.sources/<name>.env, which the
container reads via env_file and which git ignores.
"""

import socket
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .drivers import Driver, get

SOURCES_FILE = "sources.yml"
ENV_DIR = ".env.sources"
MODEL_ROOT = "cube/sources"
COMPOSE_FILE = "docker-compose.sources.yml"
SCRATCH_DIR = ".cube-probe"
"""Git-ignored working area. Each user gets its own subdirectory — the probe deletes
its own, and must never take a model backup with it."""
FIRST_PORT = 4001

HEADER = """\
# Named data sources. One Cube container each — see README.
# Credentials live in .env.sources/<name>.env and are not committed.
"""


def project_root() -> Path:
    """Nearest ancestor of the cwd holding pyproject.toml. Compose paths are relative to it."""
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

    @property
    def service(self) -> str:
        return f"cube-{self.name}"

    @property
    def spec(self) -> Driver:
        return get(self.driver)

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def env_file(self, root: Path | None = None) -> Path:
        return (root or project_root()) / ENV_DIR / f"{self.name}.env"

    def model_dir(self, root: Path | None = None) -> Path:
        return (root or project_root()) / MODEL_ROOT / self.name

    def as_yaml(self) -> dict:
        out = {
            "name": self.name,
            "description": self.description,
            "driver": self.driver,
            "port": self.port,
        }
        if self.mounts:
            out["mounts"] = dict(self.mounts)
        return out


def sources_path(root: Path | None = None) -> Path:
    return (root or project_root()) / SOURCES_FILE


def load(root: Path | None = None) -> list[Source]:
    path = sources_path(root)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    return [
        Source(
            name=s["name"],
            description=s.get("description", ""),
            driver=s["driver"],
            port=int(s["port"]),
            mounts=dict(s.get("mounts") or {}),
        )
        for s in raw.get("sources") or []
    ]


def save(sources: list[Source], root: Path | None = None) -> Path:
    path = sources_path(root)
    body = yaml.safe_dump(
        {"sources": [s.as_yaml() for s in sources]}, sort_keys=False, default_flow_style=False
    )
    path.write_text(HEADER + body)
    return path


def find(name: str, root: Path | None = None) -> Source | None:
    return next((s for s in load(root) if s.name == name), None)


def next_port(sources: list[Source] | None = None) -> int:
    taken = {s.port for s in (sources if sources is not None else load())}
    port = FIRST_PORT
    while port in taken or port_in_use(port):
        port += 1
    return port


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def write_env(source: Source, values: dict[str, str], root: Path | None = None) -> Path:
    """Write the credential file the container reads. Owner-only permissions."""
    spec = source.spec
    lines = [
        f"# {source.name} — {source.description}",
        f"# Passed to the {source.service} container verbatim. Not committed.",
    ]
    host_note = next((f.comment for f in spec.fields if f.env == "CUBEJS_DB_HOST"), "")
    if host_note:
        lines += ["#", f"# {host_note}"]
    lines += ["", f"CUBEJS_DB_TYPE={spec.key}"]
    for f in spec.fields:
        if f.comment and f.env != "CUBEJS_DB_HOST":
            lines.append(f"# {f.comment}")
        lines.append(f"{f.env}={values.get(f.env, f.fixed)}")

    path = source.env_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def read_env(source: Source, root: Path | None = None) -> dict[str, str]:
    path = source.env_file(root)
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def scaffold_model_dir(source: Source, root: Path | None = None) -> Path:
    """Cube reads /cube/conf/model. Empty until the model generator writes into it."""
    base = source.model_dir(root)
    for sub in ("cubes", "views"):
        (base / sub).mkdir(parents=True, exist_ok=True)
        (base / sub / ".gitkeep").touch()
    return base


def validate_name(name: str, root: Path | None = None) -> str | None:
    """Returns an error message, or None when the name is usable."""
    if not name:
        return "a name is required"
    if not name[0].isalpha() or not all(c.isalnum() or c == "-" for c in name):
        return "use lowercase letters, digits and hyphens, starting with a letter"
    if name != name.lower():
        return "use lowercase — the name becomes a container and directory name"
    if find(name, root):
        return f"source {name!r} already exists"
    return None
