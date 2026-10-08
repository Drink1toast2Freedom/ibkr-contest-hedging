import argparse
from pathlib import Path

import uvicorn
import yaml

from .models import Settings
from .service import Service
from .web import create_app


def main():
    parser = argparse.ArgumentParser(
        description="Standalone shadow/synthetic surplus research; no broker execution"
    )
    parser.add_argument("command", choices=["serve", "validate"], nargs="?", default="serve")
    parser.add_argument("--config", default="config/surplus-addon.template.yaml")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--demo", action="store_true", help="Start with a fictional account and stepped clock"
    )
    parser.add_argument("--ledger", default="data/surplus-addon/research.sqlite")
    args = parser.parse_args()
    settings = Settings.model_validate(yaml.safe_load(Path(args.config).read_text()))
    if args.command == "validate":
        print("Valid research-only configuration; broker execution unavailable")
        return
    if not 1024 <= args.port <= 65535 or args.port in (7496, 7497, 8765, 11111):
        parser.error("Use a separate local dashboard port, e.g. 8770")
    service = Service(settings, ledger_path=args.ledger, demo=args.demo)
    uvicorn.run(create_app(service), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
