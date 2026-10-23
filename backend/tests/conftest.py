from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-32")
os.environ.setdefault("FERNET_KEYS", Fernet.generate_key().decode())
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("COOKIE_SECURE", "false")
# Settings are cached at first import. Force the test env to win.
os.environ["JWT_SECRET"] = os.environ["JWT_SECRET"]
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["FERNET_KEYS"] = os.environ["FERNET_KEYS"]
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""
