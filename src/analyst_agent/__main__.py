"""Launch the portal: `analyst`, or `python -m analyst_agent`. A launcher, not a CLI, and not
replaceable by uvicorn — `web.server.TOKEN` is minted at import and nothing else prints it.
"""

import webbrowser


def main() -> None:

    import uvicorn

    from .core.config import settings
    from .web.server import TOKEN
    from .web.server import app as web_app

    port = settings.web_port
    url = f"http://{settings.web_host}:{port}/?token={TOKEN}"

    # flush: stdout is block-buffered when it is not a tty, so redirected to a log the
    # token would sit in the buffer for the life of the server — which is to say, never.
    print(f"\nAnalyst portal is running at:\n    {url}")
    print("Press Ctrl-C to stop.\n", flush=True)

    webbrowser.open(url)
    # log_level=warning: uvicorn's access log would bury the URL above.
    uvicorn.run(web_app, host=settings.web_host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
