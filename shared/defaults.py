"""Default configuration and normalization (MongoDB document shape)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_PANEL_DROPDOWN: dict[str, Any] = {
    "enabled": True,
    "placeholder": "Select ticket type…",
    "options": [
        {
            "id": "support",
            "label": "General Support",
            "description": "Help with general questions",
            "emoji": "⚔️",
            "category_id": None,
        },
        {
            "id": "purchase",
            "label": "Purchase / Payment",
            "description": "Billing or orders",
            "emoji": "🛒",
            "category_id": None,
        },
        {
            "id": "report",
            "label": "Report",
            "description": "Report a user or issue",
            "emoji": "🛡️",
            "category_id": None,
        },
    ],
}

DEFAULT_PANEL_BUTTONS: dict[str, Any] = {
    "enabled": False,
    "rows": [
        [
            {
                "id": "quick",
                "label": "Quick ticket",
                "emoji": None,
                "style": "primary",
                "category_id": None,
                "type_id": "support",
            }
        ]
    ],
}

DEFAULT_PANEL_EMBED: dict[str, Any] = {
    "thumbnail_url": "",
    "image_url": "",
    "author_name": "",
    "author_icon_url": "",
}

DEFAULT_CLOSE_DM: dict[str, Any] = {
    "enabled": True,
    "title": "Ticket closed",
    "description": "Your ticket **{channel_name}** was closed.\n**Reason:** {reason}",
    "color": 12933422,
    "footer": "SQL TICKET",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "guild_id": None,
    "ticket_category_id": None,
    "log_channel_id": None,
    "transcript_channel_id": None,
    "panel_channel_id": None,
    "panel_message_id": None,
    "max_open_per_user": 3,
    "ticket_counter": 0,
    "blacklist_user_ids": [],
    "panel_title": "SQL TICKET",
    "panel_description": "Use the dropdown and/or buttons below to open a private ticket.",
    "panel_color": 0xC41E3A,
    "panel_footer": "SQL Ticket System",
    "panel_embed": dict(DEFAULT_PANEL_EMBED),
    "panel_dropdown": dict(DEFAULT_PANEL_DROPDOWN),
    "panel_buttons": dict(DEFAULT_PANEL_BUTTONS),
    "welcome_title": "Ticket opened",
    "welcome_body": "Describe your issue; staff will assist shortly.",
    # Legacy (kept for migration / dashboard fallbacks)
    "ticket_types": [],
    "admin_role_ids": [],
    "support_roles": [],
    "support_role_ids": [],
    "staff_role_ids": [],
    "bot_paused": False,
    "pending_post_panel_channel_id": None,
    "close_dm_embed": dict(DEFAULT_CLOSE_DM),
}

DEFAULT_TICKETS: dict[str, Any] = {"open": []}

DEFAULT_STATS: dict[str, Any] = {
    "tickets_created_total": 0,
    "tickets_closed_total": 0,
    "last_activity": None,
}

DEFAULT_BOT_SNAPSHOT: dict[str, Any] = {
    "latency_ms": None,
    "guild_member_count": None,
    "open_ticket_count": None,
    "updated_at": None,
}


def default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = deepcopy(v) if isinstance(v, (dict, list)) else v
        return out
    if isinstance(base, list) and isinstance(override, list):
        return deepcopy(override)
    return deepcopy(override) if override is not None else deepcopy(base)


def normalize_config(data: dict[str, Any] | None) -> dict[str, Any]:
    merged = _deep_merge(default_config(), data if isinstance(data, dict) else {})
    if not merged.get("admin_role_ids") and merged.get("staff_role_ids"):
        merged["admin_role_ids"] = [int(x) for x in merged.get("staff_role_ids", [])]
    dd = merged.get("panel_dropdown")
    if isinstance(dd, dict) and (not dd.get("options")) and merged.get("ticket_types"):
        dd["options"] = deepcopy(merged["ticket_types"])
    if isinstance(dd, dict) and not dd.get("options"):
        dd["options"] = deepcopy(DEFAULT_PANEL_DROPDOWN["options"])
    if not isinstance(merged.get("panel_buttons"), dict):
        merged["panel_buttons"] = deepcopy(DEFAULT_PANEL_BUTTONS)
    if not isinstance(merged.get("panel_embed"), dict):
        merged["panel_embed"] = deepcopy(DEFAULT_PANEL_EMBED)
    if not isinstance(merged.get("close_dm_embed"), dict):
        merged["close_dm_embed"] = deepcopy(DEFAULT_CLOSE_DM)
    if not isinstance(merged.get("support_roles"), list):
        merged["support_roles"] = []
    if merged.get("support_roles") == [] and merged.get("support_role_ids"):
        for rid in merged.get("support_role_ids", []):
            cats = merged.get("ticket_category_id")
            cat_list = [int(cats)] if cats else []
            merged["support_roles"].append({"role_id": int(rid), "category_ids": cat_list})
    return merged
