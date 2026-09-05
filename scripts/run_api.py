#!/usr/bin/env python3
"""Start the Community API without customer-runtime or broker dependencies."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description="Start the MultiTrading Community API")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    argv = ["uvicorn", "api.main:app", "--host", args.host, "--port", str(args.port)]
    if args.dev:
        argv.append("--reload")
    os.execvp(sys.executable, [sys.executable, "-m", *argv])


if __name__ == "__main__":
    main()
