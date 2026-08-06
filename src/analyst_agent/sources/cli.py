"""`analyst source` — add, list, remove and test data sources interactively.

Adding a source writes four things and starts one container:
  sources.yml                  the registry entry
  .env.sources/<name>.env      credentials, chmod 600, git-ignored
  cube/sources/<name>/         the model directory Cube mounts, initially empty
  docker-compose.sources.yml   regenerated from the registry
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from . import compose, health, model, registry
from .drivers import DRIVERS
from .registry import COMPOSE_FILE, Source

app = typer.Typer(add_completion=False, help="Manage data sources, one Cube container each.")
console = Console()


def _run(*args: str, quiet: bool = False) -> bool:
    result = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        cwd=registry.project_root(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not quiet:
        console.print(f"[red]docker compose {' '.join(args)} failed[/red]")
        console.print(f"[dim]{escape((result.stderr or result.stdout).strip()[:800])}[/dim]")
    return result.returncode == 0


@app.command("add")
def add(
    name: str = typer.Argument("", help="Source name. Prompted for when omitted."),
    start: bool = typer.Option(
        True, "--start/--no-start", help="Start the container after writing."
    ),
) -> None:
    """Add a data source, interactively."""
    console.print(Panel("Add a data source", style="bold", expand=False))

    while True:
        name = (name or Prompt.ask("Name")).strip().lower()
        error = registry.validate_name(name)
        if not error:
            break
        console.print(f"[red]{error}[/red]")
        name = ""

    description = Prompt.ask(
        "Description [dim](the agent reads this when routing questions)[/dim]"
    ).strip()
    drivers = Table("#", "driver", "id", box=None, pad_edge=False)
    keys = list(DRIVERS)
    for i, key in enumerate(keys, 1):
        # A driver can extract tables exactly when there is catalog SQL for it.
        extracts = key in model.read.DIALECTS
        note = "" if extracts else "  [dim](no table extraction yet)[/dim]"
        drivers.add_row(str(i), DRIVERS[key].label + note, key)
    console.print(drivers)
    choice = IntPrompt.ask("Driver", default=1, choices=[str(i) for i in range(1, len(keys) + 1)])
    spec = DRIVERS[keys[choice - 1]]

    # One prompt per connection field. A mount_to field holds a host path: the file is
    # mounted at that container path, and the env var carries the container path.
    values: dict[str, str] = {}
    mounts: dict[str, str] = {}
    for f in spec.fields:
        if f.fixed:
            values[f.env] = f.fixed
            continue
        if f.boolean:
            values[f.env] = str(Confirm.ask(f.prompt, default=f.default == "true")).lower()
            continue
        while True:
            answer = Prompt.ask(
                f.prompt, default=f.default or None, password=f.secret, show_default=not f.secret
            )
            answer = (answer or "").strip()
            if answer or not f.required:
                break
            console.print("[red]required[/red]")
        if f.mount_to and answer:
            host_path = Path(answer).expanduser()
            if not host_path.exists():
                console.print(f"[yellow]warning:[/yellow] {host_path} does not exist yet")
            mounts[f.mount_to] = str(host_path)
            values[f.env] = f.mount_to
        else:
            values[f.env] = answer

    existing = registry.load()
    port = IntPrompt.ask("Cube API port on this machine", default=registry.next_port(existing))
    if any(s.port == port for s in existing):
        console.print(f"[red]port {port} is already assigned to another source[/red]")
        raise typer.Exit(1)

    source = Source(name=name, description=description, driver=spec.key, port=port, mounts=mounts)

    plan = Table("writes", "", box=None, pad_edge=False)
    plan.add_row("sources.yml", f"{name} ({spec.key}, port {port})")
    plan.add_row(f".env.sources/{name}.env", f"{len(spec.fields) + 1} keys, chmod 600")
    plan.add_row(f"cube/sources/{name}/", "cubes/ views/ — empty until a model is generated")
    plan.add_row(COMPOSE_FILE, f"regenerated, {len(existing) + 1} sources")
    console.print(plan)
    if not Confirm.ask("Write these", default=True):
        console.print("[dim]nothing written[/dim]")
        raise typer.Exit(1)

    registry.write_env(source, values)
    registry.scaffold_model_dir(source)
    registry.save([*existing, source])
    compose.write()
    console.print(f"[green]wrote[/green] {name}")

    if not start:
        console.print(
            f"[dim]start it with: make sources-up (or docker compose up -d {source.service})[/dim]"
        )
        return

    if not shutil.which("docker"):
        console.print("[yellow]docker not found — skipping start[/yellow]")
        return

    console.print(f"starting {source.service}…")
    if not _run("up", "-d", source.service):
        _rollback(source, existing)
        raise typer.Exit(1)

    with console.status(f"testing the connection via {source.url}/readyz"):
        result, attempts = asyncio.run(health.wait_until_ready(source))

    if result.ok:
        console.print(f"[green]connected[/green] ({result.status} after {attempts} check(s))")
        console.print(f"[dim]next: generate a model for {name}[/dim]")
        return

    console.print(f"[red]connection failed:[/red] {escape(str(result))}")
    console.print(f"[dim]logs: docker compose logs {source.service}[/dim]")
    if Confirm.ask("Roll back this source", default=False):
        _rollback(source, existing)
    else:
        console.print(
            f"[dim]kept — fix .env.sources/{name}.env, then: analyst source test {name}[/dim]"
        )


def _rollback(source: Source, previous: list[Source]) -> None:
    _run("rm", "-sf", source.service, quiet=True)
    source.env_file().unlink(missing_ok=True)
    shutil.rmtree(source.model_dir(), ignore_errors=True)
    registry.save(previous)
    compose.write()
    console.print(f"[dim]rolled back {source.name}[/dim]")


@app.command("ls")
def ls(check: bool = typer.Option(False, "--check", help="Probe each source's /readyz.")) -> None:
    """List the configured sources."""
    sources = registry.load()
    if not sources:
        console.print("[dim]no sources — add one with: analyst source add[/dim]")
        return

    table = Table("name", "driver", "port", "model files", "description")
    results: list[health.Health] = []
    if check:
        table.add_column("health")

        async def probe_all() -> list[health.Health]:
            return await asyncio.gather(*(health.check(s) for s in sources))

        with console.status("probing /readyz"):
            results = asyncio.run(probe_all())
    for i, s in enumerate(sources):
        model_dir = s.model_dir()
        count = len([p for p in model_dir.rglob("*.yml")]) if model_dir.exists() else 0
        row = [
            s.name,
            s.driver,
            str(s.port),
            str(count) if count else "[yellow]0[/yellow]",
            s.description or "-",
        ]
        if check:
            r = results[i]
            row.append(f"[green]{r.status}[/green]" if r.ok else f"[red]{r}[/red]")
        table.add_row(*row)
    console.print(table)


@app.command("test")
def test(name: str) -> None:
    """Test a source's database connection through Cube's /readyz."""
    source = registry.find(name)
    if source is None:
        console.print(f"[red]no source named {name!r}[/red]")
        raise typer.Exit(1)
    with console.status(f"checking {source.url}/readyz"):
        result = asyncio.run(health.check(source))
    if result.ok:
        console.print(f"[green]{source.name}: {result.status}[/green]")
    else:
        console.print(f"[red]{source.name}: {escape(str(result))}[/red]")
        raise typer.Exit(1)


@app.command("rm")
def rm(
    name: str,
    keep_model: bool = typer.Option(
        False, "--keep-model", help="Leave cube/sources/<name>/ on disk."
    ),
) -> None:
    """Remove a data source and its container."""
    source = registry.find(name)
    if source is None:
        console.print(f"[red]no source named {name!r}[/red]")
        raise typer.Exit(1)

    model_dir = source.model_dir()
    models = sorted(p.relative_to(model_dir) for p in model_dir.rglob("*.yml"))
    console.print(f"removing [bold]{name}[/bold] ({source.driver}, port {source.port})")
    console.print(f"  container {source.service}, .env.sources/{name}.env")
    if models and not keep_model:
        console.print(
            f"  [yellow]{len(models)} model file(s)[/yellow]: {', '.join(map(str, models))}"
        )
    if not Confirm.ask("Proceed", default=False):
        console.print("[dim]nothing removed[/dim]")
        raise typer.Exit(1)

    _run("rm", "-sf", source.service, quiet=True)
    source.env_file().unlink(missing_ok=True)
    if not keep_model:
        if models and not Confirm.ask(f"Delete {len(models)} model file(s) too", default=False):
            console.print(f"[dim]kept {model_dir}[/dim]")
        else:
            shutil.rmtree(model_dir, ignore_errors=True)
    registry.save([s for s in registry.load() if s.name != name])
    compose.write()
    console.print(f"[green]removed[/green] {name}")


@app.command("sync")
def sync() -> None:
    """Regenerate docker-compose.sources.yml from sources.yml."""
    path = compose.write()
    console.print(f"[green]wrote[/green] {path.name} ({len(registry.load())} sources)")


@app.command("model")
def model_cmd(
    name: str,
    schema: str = typer.Option("public", "--schema", help="Schema to introspect."),
    yes: bool = typer.Option(False, "--yes", help="Keep the model without asking."),
    enrich: bool = typer.Option(
        False, "--enrich", help="Add descriptions, measures and views using the model."
    ),
) -> None:
    """Generate a Cube data model for a source by introspecting its tables."""
    source = registry.find(name)
    if source is None:
        console.print(f"[red]no source named {name!r}[/red]")
        raise typer.Exit(1)

    try:
        report = asyncio.run(
            model.generate(
                source,
                schema=schema,
                enrich=enrich,
                say=lambda step: console.print(f"[dim]{step}…[/dim]"),
            )
        )
    except RuntimeError as exc:
        # Only reading the database raises; every later stage reports on the Report.
        console.print(f"[red]could not generate a model:[/red] {escape(str(exc))}")
        raise typer.Exit(1) from None

    db = report.schema
    console.print(
        f"read [bold]{len(db.tables)}[/bold] tables, "
        f"[bold]{sum(1 for t in db.tables if t.primary_key)}[/bold] primary keys, "
        f"[bold]{sum(len(t.foreign_keys) for t in db.tables)}[/bold] foreign keys"
    )

    table = Table("cube", "key", "joins", "dimensions", box=None, pad_edge=False)
    for t in db.tables:
        key = ",".join(t.primary_key) or "[red]none[/red]"
        if t.has_composite_key:
            key += " [dim](concatenated)[/dim]"
        table.add_row(
            t.name,
            key,
            str(len([fk for fk in t.foreign_keys if fk.ref_table != t.name])),
            str(len(t.columns) - len(t.key_columns)),
        )
    console.print(table)
    for note in report.notes:
        console.print(f"[yellow]note:[/yellow] {escape(note)}")

    if report.enrichment:
        counts = report.enrichment.counts
        console.print(
            f"described [bold]{counts['described_cubes']}[/bold] cubes and "
            f"[bold]{counts['described_members']}[/bold] members, added "
            f"[bold]{counts['measures']}[/bold] measures, "
            f"[bold]{counts['ai_context']}[/bold] ai_context notes, "
            f"[bold]{counts['views']}[/bold] views"
        )
        for rejection in report.enrichment.rejected:
            console.print(f"[yellow]rejected:[/yellow] {escape(rejection)}")
        for member in report.enrichment.repaired:
            console.print(f"[green]repaired[/green] {escape(member)}")
        for member in report.enrichment.dropped:
            console.print(f"[dim]dropped {escape(member)}[/dim]")

    result = report.validation
    if not result.compiled:
        console.print(f"[red]Cube rejected the model:[/red]\n{escape(result.compile_error)}")
    else:
        views = [v.name for v in report.enrichment.views] if report.enrichment else []
        console.print(
            f"[green]compiled[/green] — {len(result.entities)} entities served"
            + (f", views: {', '.join(views)}" if views else "")
        )
        for member, error in result.member_errors.items():
            console.print(f"[red]{escape(member)}:[/red] {escape(error)}")

    if result.ok:
        console.print(f"[green]{name} is queryable[/green] — try: analyst catalog")
        return
    if yes or Confirm.ask("Keep this model anyway", default=False):
        console.print("[yellow]kept with errors[/yellow]")
        return
    model.restore(source, report.backup)
    model.restart(source)
    console.print("[dim]reverted to the previous model[/dim]")
