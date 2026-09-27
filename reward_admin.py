"""Local operator tool for legitimate voucher inventory; never logs voucher codes."""
import argparse
import json
from pathlib import Path
import sqlite3

import rewards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent / "data" / "wainet.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog", help="Show public availability, without voucher codes.")
    configure = commands.add_parser("configure", help="Set an explicit operator-approved points cost and voucher value.")
    configure.add_argument("reward_id")
    configure.add_argument("--brand", required=True)
    configure.add_argument("--title", required=True)
    configure.add_argument("--points", type=int, required=True)
    configure.add_argument("--value-label", required=True, help="Actual voucher denomination and currency, as supplied by its issuer.")
    configure.add_argument("--enable", action="store_true", help="Enable redemption of imported, unexpired inventory. Disabled by default.")
    inventory = commands.add_parser("import", help="Import legally obtained vouchers from a private local JSON file.")
    inventory.add_argument("reward_id")
    inventory.add_argument("--file", required=True, help="JSON array of {code, expires_at}; expiry is a timezone timestamp or explicit null.")
    inventory.add_argument("--operator", required=True)
    args = parser.parse_args()
    try:
        # Imported here to keep the rewards module independent of the HTTP server.
        from server import Store
        store = Store(args.db)
        rewards.initialize_rewards(store)
        if args.command == "catalog":
            result = rewards.catalog(store)
        elif args.command == "configure":
            rewards.configure_reward(store, args.reward_id, args.brand, args.title, args.points, args.value_label, args.enable)
            result = {"reward_id": args.reward_id, "enabled": args.enable}
        else:
            source = Path(args.file)
            if source.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("The inventory file must be smaller than 20 MB.")
            try:
                vouchers = json.loads(source.read_text(encoding="utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError):
                raise ValueError("The inventory file must contain valid UTF-8 JSON.") from None
            result = rewards.import_inventory(store, args.reward_id, vouchers, args.operator)
        print(json.dumps(result, indent=2))
    except (OSError, sqlite3.Error):
        parser.exit(1, "The database or inventory file could not be accessed. Check the local paths and permissions.\n")
    except ValueError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
