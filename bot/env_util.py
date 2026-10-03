"""Load environment and resolve paths."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def discord_token() -> str:
    return _req("DISCORD_TOKEN")


def guild_id() -> int:
    return int(_req("GUILD_ID"))


def owner_id() -> int:
    return int(_req("OWNER_ID"))


def dashboard_host() -> str:
    return os.environ.get("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"


def dashboard_port() -> int:
    return int(os.environ.get("DASHBOARD_PORT", "8080"))


def flask_secret_key() -> str:
    return _req("FLASK_SECRET_KEY")


def dashboard_password() -> str:
    return _req("DASHBOARD_PASSWORD")


def mongodb_uri() -> str:
    return _req("MONGODB_URI")


def mongodb_database() -> str:
    return (os.environ.get("MONGODB_DB", "sql_ticket") or "sql_ticket").strip()
