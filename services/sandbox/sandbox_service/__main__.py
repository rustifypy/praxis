import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sandbox_service.app:app",
        host=os.environ.get("SANDBOX_HOST", "0.0.0.0"),
        port=int(os.environ.get("SANDBOX_PORT", "8600")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
