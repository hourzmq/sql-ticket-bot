"""MongoDB log handler (sync) — attach to 'sql' logger tree."""

from __future__ import annotations

import logging


class MongoLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from shared import mongo_db

            msg = self.format(record)
            mongo_db.insert_log(record.levelname, msg, record.name)
        except Exception:
            pass
