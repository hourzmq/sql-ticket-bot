"""Start Discord bot + web dashboard together."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _init_db() -> None:
    from shared import mongo_db

    mongo_db.import_from_json_files_if_empty()
    mongo_db.ensure_indexes()


def _bot_main() -> None:
    from bot.client import main

    asyncio.run(main())


def main() -> None:
    _init_db()
    threading.Thread(target=_bot_main, daemon=True, name="discord-bot").start()
    from web.app import run_web

    run_web()


if __name__ == "__main__":
    main()
