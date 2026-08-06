import asyncio
import json
import webbrowser

import typer
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table

from .agent.build import SecurityContext, ask
from .cube.catalog import Catalog
from .cube.client import CubeClient, mint_token
from .sources.cli import app as sources_app

app = typer.Typer(add_completion=False, help="Analyst agent over a Cube semantic layer.")
app.add_typer(sources_app, name="source")
console = Console()


def _client() -> CubeClient:
    return CubeClient(mint_token(SecurityContext(user_id="cli").as_claims()))


@app.command()
def catalog() -> None:
    """List the entities and members Cube is exposing."""

    async def run():
        client = _client()
        try:
            cat = Catalog.from_meta(await client.meta())
        finally:
            await client.aclose()
        table = Table("entity", "kind", "members", "description")
        for e in cat.entities:
            table.add_row(
                e.name, "view" if e.is_view else "cube", str(len(e.members)), e.description or "-"
            )
        console.print(table)

    asyncio.run(run())


@app.command(name="ask")
def ask_cmd(
    question: str,
    show_sql: bool = typer.Option(False, "--sql", help="Print the SQL Cube generated."),
) -> None:
    """Ask a question."""

    async def run():
        result = await ask(question)
        # Rich reads [...] as markup, and model answers contain brackets.
        console.print(escape(result.text))
        if not result.executed:
            console.print("[red]No query ran — this answer has no data behind it.[/red]")
        for record in result.executed:
            if show_sql:
                console.print(Syntax(record["sql"] or "", "sql", word_wrap=True))
            for w in record["warnings"]:
                console.print(f"[yellow]warning:[/yellow] {w}")
        if result.rejected:
            console.print(f"[dim]{len(result.rejected)} query attempts rejected by the gate[/dim]")

    asyncio.run(run())


@app.command()
def web(
    port: int = typer.Option(0, help="Port to serve on. Defaults to WEB_PORT."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
    data_dir: str = typer.Option(
        "",
        "--data-dir",
        help="Where chats are stored. Defaults to DATA_DIR (~/.analyst). "
        "Point this at a scratch directory when testing — never at real history.",
    ),
) -> None:
    """Open the chat portal in a browser."""
    import uvicorn

    from .config import settings
    from .web.server import TOKEN
    from .web.server import app as web_app

    if data_dir:
        # Mutated before uvicorn starts; the lifespan reads settings.db_path.
        settings.data_dir = data_dir
    port = port or settings.web_port
    url = f"http://{settings.web_host}:{port}/?token={TOKEN}"

    console.print("\n[bold]Analyst portal[/bold] is running at:")
    console.print(f"    [cyan]{url}[/cyan]")
    console.print(f"[dim]Chats are stored in {settings.db_path}[/dim]")
    console.print("[dim]Press Ctrl-C to stop.[/dim]\n")

    if not no_browser:
        webbrowser.open(url)
    # log_level=warning: uvicorn's access log would bury the URL above.
    uvicorn.run(web_app, host=settings.web_host, port=port, log_level="warning")


@app.command()
def check(query_json: str) -> None:
    """Validate a JSON query against the gate without executing it."""

    async def run():
        from .cube import gate

        client = _client()
        try:
            cat = Catalog.from_meta(await client.meta())
            parsed, errors = gate.parse(json.loads(query_json))
            if parsed is None:
                console.print("[red]Tier 1 (schema):[/red]", errors)
                return
            result = gate.semantic_check(parsed, cat)
            console.print("[red]Tier 2:[/red]" if not result.ok else "[green]Tier 2 passed[/green]")
            if result.errors or result.warnings:
                console.print(result.as_message())
            if result.ok:
                compiled = await client.compile(parsed)
                if compiled.ok:
                    console.print("[green]Tier 3 passed[/green]")
                    console.print(Syntax(compiled.sql or "", "sql", word_wrap=True))
                else:
                    console.print("[red]Tier 3:[/red]", compiled.error)
        finally:
            await client.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    app()
