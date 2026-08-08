from __future__ import annotations

import argparse
import uvicorn
from .config import settings
from .register import main as register_main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args()
    if args.register:
        register_main()
        return
    uvicorn.run("bridge.app:app", host=settings.host, port=settings.port, reload=False, workers=1)


if __name__ == "__main__":
    main()
