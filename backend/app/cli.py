from __future__ import annotations

import argparse
import getpass
import sys

from cryptography.fernet import Fernet

from app.db import SessionLocal, init_db
from app.models import User
from app.services.accounts import create_user


def gen_secrets() -> None:
    print("JWT_SECRET=" + Fernet.generate_key().decode())
    print("FERNET_KEYS=" + Fernet.generate_key().decode())


def create_admin(username: str) -> None:
    init_db()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match", file=sys.stderr)
        raise SystemExit(1)
    if len(password) < 10:
        print("Password must be at least 10 characters", file=sys.stderr)
        raise SystemExit(1)
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == username).first():
            print("That username already exists", file=sys.stderr)
            raise SystemExit(1)
        create_user(db, username=username, password=password, role="admin", must_change_password=False)
    finally:
        db.close()
    print(f"Admin '{username}' created")


def main() -> None:
    parser = argparse.ArgumentParser(description="Learning revision admin commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gen-secrets")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--username", required=True)
    args = parser.parse_args()
    if args.command == "gen-secrets":
        gen_secrets()
    elif args.command == "create-admin":
        create_admin(args.username)


if __name__ == "__main__":
    main()
