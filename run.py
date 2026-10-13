from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

def main() -> None:
    parser = argparse.ArgumentParser(description="Surface Conductivity Monitor")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run only the local FastAPI server instead of the desktop app",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Override the server host in --server mode",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the server port in --server mode",
    )
    args = parser.parse_args()

    if args.server:
        import uvicorn

        from surface_conductivity_monitor.config import load_config

        cfg = load_config()
        uvicorn.run(
            "surface_conductivity_monitor.main:app",
            host=args.host or cfg.host,
            port=args.port or cfg.port,
            reload=False,
        )
        return

    from surface_conductivity_monitor.desktop import run_desktop_app

    run_desktop_app()


if __name__ == "__main__":
    main()
