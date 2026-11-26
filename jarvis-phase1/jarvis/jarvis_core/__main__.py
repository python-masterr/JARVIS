from __future__ import annotations

import argparse
import getpass
import sys

from .api.app import API_KEY_SECRET, API_TOKEN_SECRET
from .config import Settings
from .security.secure_storage import get_secure_storage


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis_core")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the localhost API")
    sub.add_parser("set-api-key", help="store the Anthropic API key in secure storage")
    sub.add_parser("show-token", help="print the local API token (for trusted local clients)")
    args = p.parse_args(argv)
    settings = Settings()
    settings.ensure_dirs()

    if args.cmd == "serve":
        import uvicorn

        from .api.app import create_app
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
    elif args.cmd == "set-api-key":
        key = getpass.getpass("Anthropic API key (input hidden): ").strip()
        if not key:
            print("No key entered.", file=sys.stderr)
            return 1
        get_secure_storage(settings.data_dir).set(API_KEY_SECRET, key)
        print("Stored securely, Boss.")
    elif args.cmd == "show-token":
        print(get_secure_storage(settings.data_dir).get_or_create(API_TOKEN_SECRET, lambda: __import__("secrets").token_urlsafe(32)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
