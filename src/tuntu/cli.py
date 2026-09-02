from __future__ import annotations

import argparse
import sys

import uvicorn

from tuntu.api import build_services, create_app
from tuntu.config import StartupConfig
from tuntu.logging_setup import configure_logging


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="tuntu")
    subcommands = result.add_subparsers(dest="command")
    subcommands.add_parser("serve", help="启动 Tuntu Web 服务")
    subcommands.add_parser(
        "reset-setup-token", help="撤销旧 Setup Token 并生成新的短期 Token"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = args.command or "serve"
    config = StartupConfig()
    if command == "reset-setup-token":
        services = build_services(config, start_scheduler=False)
        try:
            grant = services.auth.rotate_setup_token()
            print(f"Setup Token（{grant.expires_at.isoformat()} 前有效）：{grant.token}")
        finally:
            services.database.dispose()
        return 0
    configure_logging(config)
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level=config.log_level.casefold(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
