"""Run the web app with automatic asset builds and optional reload."""

import argparse

from workingtitle.devserver import ReloadSpec, run_server

from .assets import ASSETS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    run_server(
        "vms:app",
        assets=ASSETS,
        host=args.host,
        port=args.port,
        reload=ReloadSpec(directories=(ASSETS.root / "src",)) if args.reload else None,
    )


if __name__ == "__main__":
    main()
