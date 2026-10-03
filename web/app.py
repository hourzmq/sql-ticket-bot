"""Password-protected control dashboard (Flask) + MongoDB."""

from __future__ import annotations

import json
import secrets
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from bot import env_util
from shared import mongo_db

_PKG = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(_PKG / "templates"), static_folder=str(_PKG / "static"))
app.secret_key = env_util.flask_secret_key()


def _password_ok(pw: str) -> bool:
    good = env_util.dashboard_password()
    return secrets.compare_digest(pw.encode("utf-8"), good.encode("utf-8"))


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    err = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if _password_ok(pw):
            session["auth"] = True
            session.permanent = True
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        err = "Invalid password."
    return render_template("login.html", error=err)


@app.route("/logout")
def logout() -> Any:
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def dashboard() -> Any:
    cfg = mongo_db.load_config()
    stats = mongo_db.get_stats()
    open_list = mongo_db.list_open_tickets()
    snap = mongo_db.get_bot_snapshot()
    counts = mongo_db.db_counts()
    dd_json = json.dumps(cfg.get("panel_dropdown", {}), indent=2, ensure_ascii=False)
    pb_json = json.dumps(cfg.get("panel_buttons", {}), indent=2, ensure_ascii=False)
    sr_json = json.dumps(cfg.get("support_roles", []), indent=2, ensure_ascii=False)
    cdm_json = json.dumps(cfg.get("close_dm_embed", {}), indent=2, ensure_ascii=False)
    return render_template(
        "dashboard.html",
        cfg=cfg,
        stats=stats,
        open_tickets=open_list,
        guild_id=env_util.guild_id(),
        dd_json=dd_json,
        pb_json=pb_json,
        sr_json=sr_json,
        cdm_json=cdm_json,
        snap=snap,
        counts=counts,
    )


@app.get("/api/logs")
@login_required
def api_logs() -> Any:
    lim = int(request.args.get("limit", "200"))
    return jsonify({"logs": mongo_db.recent_logs(lim)})


@app.get("/api/db")
@login_required
def api_db() -> Any:
    return jsonify({"counts": mongo_db.db_counts()})


@app.get("/api/bot")
@login_required
def api_bot() -> Any:
    return jsonify({"snapshot": mongo_db.get_bot_snapshot()})


@app.get("/api/summary")
@login_required
def api_summary() -> Any:
    return jsonify(
        {
            "stats": mongo_db.get_stats(),
            "open": len(mongo_db.list_open_tickets()),
            "snapshot": mongo_db.get_bot_snapshot(),
            "counts": mongo_db.db_counts(),
        }
    )


@app.post("/save-core")
@login_required
def save_core() -> Any:
    def mut(d: dict) -> None:
        d["log_channel_id"] = _int_or_none(request.form.get("log_channel_id"))
        d["transcript_channel_id"] = _int_or_none(request.form.get("transcript_channel_id"))
        d["max_open_per_user"] = max(1, min(10, int(request.form.get("max_open_per_user") or 3)))
        d["bot_paused"] = request.form.get("bot_paused") == "on"

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-admin-roles")
@login_required
def save_admin_roles() -> Any:
    ids = _parse_id_list(request.form.get("admin_role_ids", ""))

    def mut(d: dict) -> None:
        d["admin_role_ids"] = ids

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-support-matrix")
@login_required
def save_support_matrix() -> Any:
    raw = request.form.get("support_roles_json", "").strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("must be list")
        clean: list[dict[str, Any]] = []
        for row in parsed:
            if not isinstance(row, dict):
                continue
            try:
                rid = int(row.get("role_id"))
            except (TypeError, ValueError):
                continue
            cats: list[int] = []
            for x in row.get("category_ids") or []:
                try:
                    cats.append(int(x))
                except (TypeError, ValueError):
                    continue
            clean.append({"role_id": rid, "category_ids": cats})
    except (json.JSONDecodeError, ValueError, TypeError):
        return redirect(url_for("dashboard"))

    def mut(d: dict) -> None:
        d["support_roles"] = clean

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-text")
@login_required
def save_text() -> Any:
    def mut(d: dict) -> None:
        d["panel_title"] = request.form.get("panel_title", "SQL TICKET").strip()[:256]
        d["panel_description"] = request.form.get("panel_description", "").strip()[:4096]
        d["panel_footer"] = request.form.get("panel_footer", "").strip()[:2048]
        d["welcome_title"] = request.form.get("welcome_title", "").strip()[:256]
        d["welcome_body"] = request.form.get("welcome_body", "").strip()[:4096]
        raw = request.form.get("panel_color", "12699610").strip()
        try:
            d["panel_color"] = int(raw)
        except ValueError:
            d["panel_color"] = 0xC41E3A

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-panel-embed")
@login_required
def save_panel_embed() -> Any:
    def mut(d: dict) -> None:
        pe = d.get("panel_embed") if isinstance(d.get("panel_embed"), dict) else {}
        pe["thumbnail_url"] = request.form.get("thumbnail_url", "").strip()[:500]
        pe["image_url"] = request.form.get("image_url", "").strip()[:500]
        pe["author_name"] = request.form.get("author_name", "").strip()[:256]
        pe["author_icon_url"] = request.form.get("author_icon_url", "").strip()[:500]
        d["panel_embed"] = pe

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-dropdown-json")
@login_required
def save_dropdown_json() -> Any:
    raw = request.form.get("panel_dropdown_json", "").strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("object")
    except (json.JSONDecodeError, ValueError):
        return redirect(url_for("dashboard"))

    def mut(d: dict) -> None:
        d["panel_dropdown"] = parsed

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-buttons-json")
@login_required
def save_buttons_json() -> Any:
    raw = request.form.get("panel_buttons_json", "").strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("object")
    except (json.JSONDecodeError, ValueError):
        return redirect(url_for("dashboard"))

    def mut(d: dict) -> None:
        d["panel_buttons"] = parsed

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-close-dm-json")
@login_required
def save_close_dm_json() -> Any:
    raw = request.form.get("close_dm_json", "").strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("object")
    except (json.JSONDecodeError, ValueError):
        return redirect(url_for("dashboard"))

    def mut(d: dict) -> None:
        d["close_dm_embed"] = parsed

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/save-blacklist")
@login_required
def save_blacklist() -> Any:
    ids = _parse_id_list(request.form.get("blacklist_user_ids", ""))

    def mut(d: dict) -> None:
        d["blacklist_user_ids"] = ids

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/queue-panel")
@login_required
def queue_panel() -> Any:
    cid = _int_or_none(request.form.get("post_channel_id"))

    def mut(d: dict) -> None:
        d["pending_post_panel_channel_id"] = cid

    mongo_db.update_config(mut)
    return redirect(url_for("dashboard"))


@app.post("/reset-stats")
@login_required
def reset_stats() -> Any:
    mongo_db.reset_stats()
    return redirect(url_for("dashboard"))


@app.post("/reset-overview-metrics")
@login_required
def reset_overview_metrics() -> Any:
    mongo_db.reset_overview_dashboard_metrics()
    return redirect(url_for("dashboard"))


def _int_or_none(s: str | None) -> int | None:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_id_list(blob: str) -> list[int]:
    out: list[int] = []
    for part in blob.replace(",", " ").split():
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def run_web() -> None:
    app.run(
        host=env_util.dashboard_host(),
        port=env_util.dashboard_port(),
        debug=False,
        use_reloader=False,
        threaded=True,
    )
