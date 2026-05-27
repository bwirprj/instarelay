from __future__ import annotations

import argparse

from app.config import ensure_runtime_dirs
from app.db import migrate
from app.security import create_api_key


def main() -> None:
    parser = argparse.ArgumentParser(description="InstaRelay CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-key", help="Create an API key")
    create.add_argument("--name", default="homelessbot")
    args = parser.parse_args()

    ensure_runtime_dirs()
    migrate()
    if args.command == "create-key":
        record, token = create_api_key(args.name)
        print(f"id={record['id']}")
        print(f"name={record['name']}")
        print(f"prefix={record['key_prefix']}")
        print(f"token={token}")


if __name__ == "__main__":
    main()
