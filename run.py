#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-telegram-bot==21.11",
# ]
# ///

from __future__ import annotations

import argparse
from pathlib import Path

from tgcourier.telegram_bot import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="tg-courier")
    parser.add_argument(
        "--echo-local",
        action="store_true",
        help="Print all bot activity locally (still logs to file by default).",
    )
    parser.add_argument(
        "--log",
        metavar="FILE",
        default=None,
        help="Log file path (default: ./data/tg-courier.log).",
    )
    args = parser.parse_args()

    main(
        echo_local=bool(args.echo_local),
        log_path=Path(args.log).expanduser() if args.log else None,
    )
