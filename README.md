# SQL TICKET

A Discord ticket bot with a **password-protected web dashboard**, **MongoDB** persistence, and a configurable ticket panel (dropdown and/or buttons). Each ticket type opens in its own Discord **category**; staff permissions can be scoped by category through the support matrix.

---

## Features

- **Ticket panel** — Custom embed, optional dropdown (up to 25 options) and/or button rows; each option carries its own `category_id`.
- **Ticket channels** — Private text channels with overwrites for the opener, bot, admin roles, and support roles mapped to categories.
- **In-ticket controls** — Persistent view: close (with reason modal), claim, add user, transcript (optional log/transcript channel).
- **Dashboard** — Edit panel JSON, channels, roles, blacklist, pause ticketing, queue a new panel post, live bot snapshot, logs console, and safe reset of overview metrics (stats + snapshot only).
- **Operations** — Slash commands for owners/moderators; DM on close (configurable); optional transcript upload to a log channel.

---

## Requirements

- **Python** 3.10 or newer (recommended 3.11+)
- A **Discord application** (bot token) with the **Message Content** intent enabled if you rely on message content in transcripts
- **MongoDB** (Atlas or self-hosted) — connection URI required
- Bot invited to your server with **Manage Channels**, **Manage Roles** (as needed), **Send Messages**, **Embed Links**, **Read Message History**, and slash-command permissions for your guild

---

## Installation

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy `.env` from your secure template and fill every **required** variable (see below).

3. On first run, if MongoDB has no config document, the app may import legacy `data/*.json` files when present.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Bot token from the Discord Developer Portal. |
| `GUILD_ID` | Yes | Target Discord server (guild) ID. |
| `OWNER_ID` | Yes | Discord user ID of the bot owner (full dashboard + owner-only commands). |
| `MONGODB_URI` | Yes | MongoDB connection string. |
| `FLASK_SECRET_KEY` | Yes | Secret key for Flask session signing (use a long random string). |
| `DASHBOARD_PASSWORD` | Yes | Password for the web dashboard login. |
| `MONGODB_DB` | No | Database name (default: `sql_ticket`). |
| `DASHBOARD_HOST` | No | Bind address for the web server (default: `0.0.0.0`). |
| `DASHBOARD_PORT` | No | Port for the dashboard (default: `8080`). |

Never commit `.env` or share your token, secret key, or dashboard password.

---

## Running the application

From the project root:

```bash
python run.py
```

This starts:

1. **Discord bot** — on a background thread (asyncio event loop).
2. **Web dashboard** — Flask HTTP server on `DASHBOARD_HOST`:`DASHBOARD_PORT`.

Open `http://your_host_url:8080` (or your configured host/port), sign in with `DASHBOARD_PASSWORD`, and manage configuration through the UI.

---

## Discord usage (summary)

| Command | Who | Purpose |
|---------|-----|---------|
| `/ticket_panel` | Owner | Post the ticket panel to a channel and save panel message IDs. |
| `/sync_panel` | Owner | Re-register persistent panel components after a bot restart. |
| `/close` | Moderators (per rules) | Close the current ticket channel. |

Ticket **open** actions are driven by the panel **dropdown** and **buttons** defined in the dashboard JSON. Ensure every option and button includes a valid numeric **`category_id`** (Discord category ID).

---

## MongoDB layout (high level)

- **`bot_config`** — Single main document: ticketing and panel settings.
- **`open_tickets`** — Open ticket metadata (channel, user, type, control message id, parent category, etc.).
- **`bot_stats`** — Aggregate counters (e.g. tickets created/closed totals).
- **`bot_logs`** — Recent log lines for the dashboard console.
- **`bot_meta`** — Bot snapshot (latency, member count, etc.) for the live stats strip.

The dashboard **Reset metrics** control clears aggregate stats and the live snapshot document only; it does **not** delete open tickets, logs, or configuration.

---

## Project structure

| Path | Role |
|------|------|
| `run.py` | Entry point: DB init, bot thread, Flask server. |
| `bot/client.py` | Discord client, config cache, extension load, slash sync. |
| `bot/cogs/tickets.py` | Ticket lifecycle, panel wiring, persistent views, slash commands. |
| `bot/panel_ui.py` | Panel embed and dynamic views (select + buttons). |
| `web/` | Flask dashboard (templates, static assets, routes). |
| `shared/mongo_db.py` | MongoDB access layer. |
| `shared/defaults.py` | Default configuration shape and normalization. |

---

## Security and operations

- Run the dashboard **only on trusted networks**, or put it behind a reverse proxy with TLS and access control if exposed beyond localhost.
- Use a **strong** `DASHBOARD_PASSWORD` and rotate it if compromised.
- Restrict MongoDB credentials with least-privilege network rules (IP allowlist for Atlas, firewall for self-hosted).

---

## Credits

**SQL TICKET** — developed for private server operations. Adjust branding and legal notices here if you distribute a fork.

---

## Author

Developer: eclipsiumx
Owner: HourZmq Lxnnister

## Support Sevrer
discord.gg/altyapi