"""Launch the portal: `analyst`, or `python -m analyst_agent`.

A launcher, not a CLI. `cli.py` was one — typer and rich for a single command, and both
were direct dependencies nothing else in the tree used.

It cannot be replaced by pointing uvicorn at the app. `web.server.TOKEN` is minted at
import and nothing else announces it, so `uvicorn analyst_agent.web.server:app` serves a
portal no one can authenticate against. Printing the URL next to the port this module
picked is also what stops the two disagreeing.
"""

import argparse
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(prog="analyst", description="Open the analyst portal.")
    parser.add_argument("--port", type=int, default=0, help="Port to serve on (default WEB_PORT).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    parser.add_argument(
        "--data-dir",
        default="",
        help="Where chats are stored (default DATA_DIR, ~/.analyst). Point this at a "
        "scratch directory when testing — never at real history.",
    )
    args = parser.parse_args()

    import uvicorn

    from .config import settings
    from .web.server import TOKEN
    from .web.server import app as web_app

    if args.data_dir:
        # Mutated before uvicorn starts; the lifespan reads settings.db_path.
        settings.data_dir = args.data_dir
    port = args.port or settings.web_port
    url = f"http://{settings.web_host}:{port}/?token={TOKEN}"

    # flush: stdout is block-buffered when it is not a tty, so redirected to a log the
    # token would sit in the buffer for the life of the server — which is to say, never.
    print(f"\nAnalyst portal is running at:\n    {url}")
    print(f"Chats are stored in {settings.db_path}")
    print("Press Ctrl-C to stop.\n", flush=True)

    if not args.no_browser:
        webbrowser.open(url)
    # log_level=warning: uvicorn's access log would bury the URL above.
    uvicorn.run(web_app, host=settings.web_host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
