"""MongoDB persistence (sync) for bot + dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection

from shared.defaults import DEFAULT_STATS, default_config, normalize_config

_client: Optional[MongoClient] = None


def mongo_uri() -> str:
    u = os.environ.get("MONGODB_URI", "").strip()
    if not u:
        raise RuntimeError("MONGODB_URI is required in .env")
    return u


def mongo_db_name() -> str:
    return (os.environ.get("MONGODB_DB", "sql_ticket") or "sql_ticket").strip()


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(mongo_uri(), serverSelectionTimeoutMS=8000)
    return _client


def get_db() -> Any:
    return get_client()[mongo_db_name()]


def _config_col() -> Collection:
    return get_db()["bot_config"]


def _tickets_col() -> Collection:
    return get_db()["open_tickets"]


def _stats_col() -> Collection:
    return get_db()["bot_stats"]


def _logs_col() -> Collection:
    return get_db()["bot_logs"]


def _meta_col() -> Collection:
    return get_db()["bot_meta"]


def ensure_indexes() -> None:
    _logs_col().create_index([("ts", DESCENDING)])
    _tickets_col().create_index([("user_id", ASCENDING)])
    _tickets_col().create_index([("guild_id", ASCENDING)])


def load_config() -> dict[str, Any]:
    doc = _config_col().find_one({"_id": "main"})
    if not doc or "data" not in doc:
        cfg = default_config()
        _config_col().replace_one({"_id": "main"}, {"_id": "main", "data": cfg}, upsert=True)
        return normalize_config(cfg)
    return normalize_config(doc["data"])


def save_config_full(cfg: dict[str, Any]) -> None:
    cfg = normalize_config(cfg)
    _config_col().replace_one({"_id": "main"}, {"_id": "main", "data": cfg}, upsert=True)


def update_config(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    cur = load_config()
    mutator(cur)
    cur = normalize_config(cur)
    _config_col().replace_one({"_id": "main"}, {"_id": "main", "data": cur}, upsert=True)
    return cur


def list_open_tickets() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in _tickets_col().find({}):
        d = dict(doc)
        d.pop("_id", None)
        out.append(d)
    return out


def upsert_open_ticket(doc: dict[str, Any]) -> None:
    cid = int(doc["channel_id"])
    payload = dict(doc)
    payload["_id"] = cid
    _tickets_col().replace_one({"_id": cid}, payload, upsert=True)


def delete_open_ticket(channel_id: int) -> None:
    _tickets_col().delete_one({"_id": int(channel_id)})


def update_open_ticket(channel_id: int, fields: dict[str, Any]) -> None:
    cid = int(channel_id)
    _tickets_col().update_one({"_id": cid}, {"$set": fields})


def get_stats() -> dict[str, Any]:
    doc = _stats_col().find_one({"_id": "main"})
    if not doc:
        st = dict(DEFAULT_STATS)
        _stats_col().replace_one({"_id": "main"}, {"_id": "main", **st}, upsert=True)
        return st
    d = dict(doc)
    d.pop("_id", None)
    return d


def update_stats(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    cur = dict(get_stats())
    mutator(cur)
    cur["_id"] = "main"
    _stats_col().replace_one({"_id": "main"}, cur, upsert=True)
    cur.pop("_id", None)
    return cur


def reset_stats() -> None:
    st = dict(DEFAULT_STATS)
    st["_id"] = "main"
    _stats_col().replace_one({"_id": "main"}, st, upsert=True)


def reset_overview_dashboard_metrics() -> None:
    """Reset counters used on Overview + Bot stats (totals + cached live snapshot). Does not delete open tickets or logs."""
    reset_stats()
    _meta_col().delete_one({"_id": "snapshot"})


def insert_log(level: str, message: str, logger_name: str = "") -> None:
    _logs_col().insert_one(
        {
            "ts": datetime.now(timezone.utc),
            "level": level[:16],
            "logger": logger_name[:120],
            "message": message[:4000],
        }
    )
    try:
        n = _logs_col().estimated_document_count()
        if n > 8000:
            # trim oldest beyond 6000
            cutoff = list(_logs_col().find().sort("ts", ASCENDING).limit(max(1, n - 6000)))
            if cutoff:
                oldest = cutoff[0]["ts"]
                _logs_col().delete_many({"ts": {"$lt": oldest}})
    except Exception:
        pass


def recent_logs(limit: int = 200) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 500))
    rows: list[dict[str, Any]] = []
    for doc in _logs_col().find().sort("ts", DESCENDING).limit(lim):
        d = {
            "ts": doc.get("ts").isoformat() if doc.get("ts") else "",
            "level": doc.get("level", ""),
            "logger": doc.get("logger", ""),
            "message": doc.get("message", ""),
        }
        rows.append(d)
    rows.reverse()
    return rows


def set_bot_snapshot(data: dict[str, Any]) -> None:
    payload = dict(data)
    payload["_id"] = "snapshot"
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _meta_col().replace_one({"_id": "snapshot"}, payload, upsert=True)


def get_bot_snapshot() -> dict[str, Any]:
    doc = _meta_col().find_one({"_id": "snapshot"})
    if not doc:
        return {}
    d = dict(doc)
    d.pop("_id", None)
    return d


def db_counts() -> dict[str, int]:
    db = get_db()
    return {
        "open_tickets": _tickets_col().estimated_document_count(),
        "logs": _logs_col().estimated_document_count(),
        "collections": len(db.list_collection_names()),
    }


def import_from_json_files_if_empty() -> None:
    """One-time migration from legacy data/*.json if Mongo config missing."""
    from pathlib import Path

    import json

    if _config_col().find_one({"_id": "main"}):
        return
    root = Path(__file__).resolve().parent.parent / "data"
    cfg_path = root / "config.json"
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            save_config_full(cfg if isinstance(cfg, dict) else default_config())
        except Exception:
            save_config_full(default_config())
    else:
        save_config_full(default_config())
    tickets_path = root / "tickets.json"
    if tickets_path.is_file():
        try:
            with open(tickets_path, "r", encoding="utf-8") as f:
                t = json.load(f)
            for row in (t or {}).get("open", []) or []:
                if isinstance(row, dict) and row.get("channel_id"):
                    upsert_open_ticket(row)
        except Exception:
            pass
    stats_path = root / "stats.json"
    if stats_path.is_file():
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                s = json.load(f)
            if isinstance(s, dict):
                s2 = dict(DEFAULT_STATS)
                s2.update({k: s.get(k, s2[k]) for k in s2})
                s2["_id"] = "main"
                _stats_col().replace_one({"_id": "main"}, s2, upsert=True)
        except Exception:
            pass
