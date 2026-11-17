"""Start the ledger: ``python -m ledger``.

The server listens on 127.0.0.1 only. It shows billing data and has no login of its own, so
it should not be reachable from other machines.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from .config import ConfigError, load_settings
from .web.app import create_app


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.exit(f"Configuration problem: {exc}")
    port = int(os.environ.get("LEDGER_PORT", "8000"))
    print(f"SignalWire account ledger for {settings.host}: http://127.0.0.1:{port}")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
