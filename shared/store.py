"""Thread-safe JSON persistence for bot + dashboard."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

_lock = threading.Lock()
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def _path(name: str) -> Path:
    return data_dir() / name


def read_json(name: str, default: Any) -> Any:
    path = _path(name)
    with _lock:
        if not path.is_file():
            return deepcopy(default)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return deepcopy(default)


def write_json(name: str, data: Any) -> None:
    path = _path(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _lock:
        data_dir()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def update_json(
    name: str, default: dict, mutator: Callable[[dict], None]
) -> dict:
    """Load dict, apply mutator in-place, save atomically. Returns a deep copy."""
    with _lock:
        path = _path(name)
        data_dir()
        cur: Any
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cur = json.load(f)
            except (json.JSONDecodeError, OSError):
                cur = deepcopy(default)
        else:
            cur = deepcopy(default)
        if not isinstance(cur, dict):
            cur = deepcopy(default)
        mutator(cur)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return deepcopy(cur)
