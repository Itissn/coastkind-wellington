"""Grant or revoke administrator access from the local database operator's terminal."""
import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.parse import quote

import accounts
from server import ROOT, Store, configured_public_origin, load_config


def change_role(store, email, role):
    """Match an entire existing email address; never create or guess an account."""
    email = accounts.normalize_email(email)
    if role not in {"member", "admin"}:
        raise ValueError("Choose member or admin as the account role.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            raise ValueError("No account has that exact email address. Register it first or use the create command.")
        db.execute("UPDATE users SET role=? WHERE id=?", (role, row["id"]))
    return {"email": email, "role": role}


def create_admin(store, email, display_name, password):
    user = accounts.register(store, {"email": email, "display_name": display_name, "password": password})
    change_role(store, user["email"], "admin")
    user["role"] = "admin"
    return user


def create_temporary_admin(store, email, display_name, password):
    """Local API for an explicitly authorized first-login temporary credential."""
    return accounts.create_temporary_admin(store, email, display_name, password)


def main():
    load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("COASTKIND_DB_PATH", str(ROOT / "data" / "coastkind.sqlite3")), help="Local SQLite database managed by this operator.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("grant", "revoke"):
        command = commands.add_parser(name, help=f"{name.capitalize()} administrator access for an existing account.")
        command.add_argument("email", help="Exact full email address of the existing account.")
    commands.add_parser("create", help="Create an administrator interactively; passwords are never command-line arguments.")
    commands.add_parser("create-temporary", help="Create an administrator with a supplied temporary password and force a first-login password change.")
    invite = commands.add_parser("invite", help="Write a one-use setup invitation to a private local file.")
    invite.add_argument("email", help="Exact email address allowed to create the administrator account.")
    invite.add_argument("--out", required=True, help="New private JSON file for the setup secret. Never publish this file.")
    invite.add_argument("--origin", default=os.getenv("COASTKIND_PUBLIC_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000")
    args = parser.parse_args()
    store = Store(args.db)
    try:
        if args.command == "invite":
            origin = configured_public_origin(args.origin)
            output = Path(args.out).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents replacing an existing private invitation.
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as private_file:
                invitation = accounts.create_admin_invite(store, args.email)
                invitation["url"] = f"{origin}/admin#setup={quote(invitation['token'], safe='')}"
                json.dump(invitation, private_file, indent=2)
                private_file.write("\n")
            print(f"Private administrator setup invitation written to {output}")
            print("Valid for 24 hours and one use. Keep this file private; do not commit or publish it.")
        elif args.command in ("create", "create-temporary"):
            email = input("Administrator email: ").strip()
            display_name = input("Public display name: ").strip()
            password = getpass.getpass("Temporary password: " if args.command == "create-temporary" else "Password (12-128 characters): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise ValueError("Passwords did not match. No account was created.")
            user = (create_temporary_admin if args.command == "create-temporary" else create_admin)(store, email, display_name, password)
            print(f"Administrator account created for {user['email']}. Sign in at /admin with the chosen password.")
            if user.get("password_change_required"):
                print("A new password is required before administrator access or other changes are allowed.")
        else:
            result = change_role(store, args.email, "admin" if args.command == "grant" else "member")
            print(f"Administrator access {'granted' if result['role'] == 'admin' else 'revoked'} for {result['email']}.")
            print("Existing sessions use the updated role on their next request.")
    except (ValueError, EOFError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
