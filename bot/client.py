"""Discord client bootstrap, MongoDB config sync, stats snapshot, logging."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord.ext import commands

from bot import env_util
from shared.defaults import default_config
from shared import mongo_db
from shared.mongo_logging import MongoLogHandler

log = logging.getLogger("sql.bot")

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.messages = True
INTENTS.message_content = True


class SQLBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or("!w "),
            intents=INTENTS,
            help_command=None,
        )
        self._config_cache: dict[str, Any] = default_config()
        self._config_lock = asyncio.Lock()

    async def get_cfg(self) -> dict[str, Any]:
        async with self._config_lock:
            return dict(self._config_cache)

    async def _refresh_config(self) -> None:
        raw = await asyncio.to_thread(mongo_db.load_config)
        raw["guild_id"] = env_util.guild_id()
        async with self._config_lock:
            self._config_cache = raw

    async def setup_hook(self) -> None:
        await self._refresh_config()
        await self.load_extension("bot.cogs.tickets")
        guild = discord.Object(id=env_util.guild_id())
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        user = self.user
        log.info("Logged in as %s (%s)", user, user.id if user else "?")
        cog = self.get_cog("TicketsCog")
        if cog is not None and hasattr(cog, "register_persistent_views"):
            try:
                await cog.register_persistent_views()  # type: ignore[union-attr]
            except Exception:
                log.exception("register_persistent_views on_ready")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="SQL Ticket",
            )
        )

    async def config_watcher(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._refresh_config()
            except Exception:
                log.exception("config refresh failed")
            await asyncio.sleep(4)

    async def stats_snapshot_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                g = self.get_guild(env_util.guild_id())
                lat = round(self.latency * 1000, 2) if self.latency is not None else None
                open_cnt = len(await asyncio.to_thread(mongo_db.list_open_tickets))
                snap = {
                    "latency_ms": lat,
                    "guild_member_count": g.member_count if g else None,
                    "open_ticket_count": open_cnt,
                }
                await asyncio.to_thread(mongo_db.set_bot_snapshot, snap)
            except Exception:
                log.exception("stats_snapshot_loop")
            await asyncio.sleep(45)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    root_w = logging.getLogger("sql")
    root_w.setLevel(logging.INFO)
    if not any(isinstance(h, MongoLogHandler) for h in root_w.handlers):
        mh = MongoLogHandler()
        mh.setLevel(logging.INFO)
        root_w.addHandler(mh)

    bot = SQLBot()
    asyncio.create_task(bot.config_watcher())
    asyncio.create_task(bot.stats_snapshot_loop())
    await bot.start(env_util.discord_token())
