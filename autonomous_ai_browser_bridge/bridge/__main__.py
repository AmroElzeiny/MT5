from __future__ import annotations

import argparse
import uvicorn
from .config import settings
from .register import main as register_main
from .diagnose import main as diagnose_main
from .manual_login import main as manual_login_main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--login", action="store_true")
    args = parser.parse_args()
    if args.login:
        manual_login_main(); return
    if args.register:
        register_main(); return
    if args.diagnose:
        diagnose_main(); return
    uvicorn.run("bridge.app:app", host=settings.host, port=settings.port, reload=False, workers=1)


if __name__ == "__main__":
    main()
