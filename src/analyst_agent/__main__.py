"""Launch the portal: `analyst`, or `python -m analyst_agent`. A launcher, not a CLI, and not
replaceable by uvicorn — `web.server.TOKEN` is minted at import and nothing else prints it.
"""

import webbrowser


def main() -> None:

    import uvicorn

    from .api.server import TOKEN
    from .api.server import app as web_app
    from .core.config import settings

    port = settings.web_port
    url = f"http://{settings.web_host}:{port}/?token={TOKEN}"

    print(f"\nAnalyst portal is running at:\n    {url}")
    print("Press Ctrl-C to stop.\n", flush=True)

    webbrowser.open(url)
    uvicorn.run(web_app, host=settings.web_host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
