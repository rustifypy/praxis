import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "model_store.app:app",
        host=os.environ.get("MODEL_STORE_HOST", "0.0.0.0"),
        port=int(os.environ.get("MODEL_STORE_PORT", "8500")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
