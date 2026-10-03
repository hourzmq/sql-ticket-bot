"""Ticket system: MongoDB-backed, panel (dropdown + buttons), roles, DM on close."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import env_util
from bot.panel_ui import build_panel_embed, build_panel_view
from shared.defaults import DEFAULT_STATS, default_config
from shared import mongo_db

log = logging.getLogger("sql.tickets")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _db_list_open() -> list[dict[str, Any]]:
    return await asyncio.to_thread(mongo_db.list_open_tickets)


async def _db_upsert_ticket(doc: dict[str, Any]) -> None:
    await asyncio.to_thread(mongo_db.upsert_open_ticket, doc)


async def _db_delete_ticket(channel_id: int) -> None:
    await asyncio.to_thread(mongo_db.delete_open_ticket, channel_id)


async def _db_update_ticket(channel_id: int, fields: dict[str, Any]) -> None:
    await asyncio.to_thread(mongo_db.update_open_ticket, channel_id, fields)


async def _db_bump_stat(key: str, inc: int = 1) -> None:
    def m(d: dict[str, Any]) -> None:
        d[key] = int(d.get(key, 0)) + inc
        d["last_activity"] = _utcnow_iso()

    await asyncio.to_thread(mongo_db.update_stats, m)


async def _db_cfg_update(mutator: Any) -> dict[str, Any]:
    return await asyncio.to_thread(mongo_db.update_config, mutator)


def _admin_role_ids(cfg: dict[str, Any]) -> list[int]:
    return [int(x) for x in cfg.get("admin_role_ids", [])]


def _support_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in cfg.get("support_roles") or []:
        if not isinstance(row, dict):
            continue
        try:
            rid = int(row.get("role_id"))
        except (TypeError, ValueError):
            continue
        cats: list[int] = []
        for c in row.get("category_ids") or []:
            try:
                cats.append(int(c))
            except (TypeError, ValueError):
                continue
        out.append({"role_id": rid, "category_ids": cats})
    return out


def _is_discord_admin(member: discord.Member) -> bool:
    return bool(member.guild_permissions.administrator)


def _has_admin_role(member: discord.Member, cfg: dict[str, Any]) -> bool:
    ids = set(_admin_role_ids(cfg))
    if not ids:
        return False
    return any(r.id in ids for r in member.roles)


def _support_matches_ticket(member: discord.Member, ticket: dict[str, Any], cfg: dict[str, Any]) -> bool:
    pcid = int(ticket.get("parent_category_id") or 0)
    if not pcid:
        return False
    for entry in _support_entries(cfg):
        if entry["role_id"] not in [r.id for r in member.roles]:
            continue
        cats = entry.get("category_ids") or []
        if not cats:
            continue
        if pcid in cats:
            return True
    return False


def can_moderate_ticket(member: discord.Member, ticket: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if member.id == env_util.owner_id():
        return True
    if _is_discord_admin(member):
        return True
    if _has_admin_role(member, cfg):
        return True
    return _support_matches_ticket(member, ticket, cfg)


def _roles_to_ping(cfg: dict[str, Any], parent_category_id: int) -> list[int]:
    ping: set[int] = set(_admin_role_ids(cfg))
    for entry in _support_entries(cfg):
        cats = entry.get("category_ids") or []
        if parent_category_id and parent_category_id in cats:
            ping.add(int(entry["role_id"]))
    return list(ping)


def _apply_support_admin_overwrites(
    guild: discord.Guild,
    overwrites: dict[Any, discord.PermissionOverwrite],
    cfg: dict[str, Any],
    parent_category_id: int,
) -> None:
    for rid in _admin_role_ids(cfg):
        role = guild.get_role(int(rid))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
    for entry in _support_entries(cfg):
        cats = entry.get("category_ids") or []
        if parent_category_id and cats and parent_category_id in cats:
            role = guild.get_role(int(entry["role_id"]))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )


async def _find_ticket(channel_id: int) -> Optional[dict[str, Any]]:
    for t in await _db_list_open():
        if int(t.get("channel_id", 0)) == channel_id:
            return t
    return None


async def _open_for_user(user_id: int) -> list[dict[str, Any]]:
    return [t for t in await _db_list_open() if int(t.get("user_id", 0)) == user_id]


async def _build_transcript(channel: discord.TextChannel) -> str:
    lines: list[str] = [f"Transcript: #{channel.name} ({channel.id})", "=" * 40]
    async for msg in channel.history(limit=500, oldest_first=True):
        ts = msg.created_at.isoformat()
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content or ""
        if msg.attachments:
            content += " [attachments: " + ", ".join(a.url for a in msg.attachments) + "]"
        if msg.embeds:
            content += f" [embeds: {len(msg.embeds)}]"
        lines.append(f"[{ts}] {author}: {content}")
    return "\n".join(lines)


class AddUserModal(discord.ui.Modal, title="Add user to ticket"):
    user_id = discord.ui.TextInput(label="Member user ID", required=True, max_length=22)

    def __init__(self, channel_id: int) -> None:
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Member only.", ephemeral=True)
        ch = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("Channel missing.", ephemeral=True)
        t = await _find_ticket(self.channel_id)
        if not t:
            return await interaction.response.send_message("Not a ticket.", ephemeral=True)
        cfg = await interaction.client.get_cfg()
        if not can_moderate_ticket(interaction.user, t, cfg):
            return await interaction.response.send_message("Not allowed.", ephemeral=True)
        raw = str(self.user_id.value).strip()
        try:
            uid = int(raw)
        except ValueError:
            return await interaction.response.send_message("Invalid user ID.", ephemeral=True)
        member = interaction.guild.get_member(uid) if interaction.guild else None
        if member is None:
            return await interaction.response.send_message("Member not in this server.", ephemeral=True)
        await ch.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await interaction.response.send_message(f"Added {member.mention}.", ephemeral=True)
        await ch.send(f"{member.mention} was added to this ticket by {interaction.user.mention}.")


class TicketControlView(discord.ui.View):
    def __init__(self, channel_id: int) -> None:
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="sql_btn_close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            return await interaction.response.send_message("Use in server.", ephemeral=True)
        t = await _find_ticket(self.channel_id)
        if not t:
            return await interaction.response.send_message("Not linked as an open ticket.", ephemeral=True)
        cfg = await interaction.client.get_cfg()
        if not can_moderate_ticket(interaction.user, t, cfg):
            return await interaction.response.send_message("You cannot close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseTicketModal(self.channel_id))

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="sql_btn_claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ch = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("Channel missing.", ephemeral=True)
        cfg = await interaction.client.get_cfg()
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message("Use this inside the server.", ephemeral=True)
        t = await _find_ticket(self.channel_id)
        if not t or not can_moderate_ticket(member, t, cfg):
            return await interaction.response.send_message("You cannot claim this ticket.", ephemeral=True)
        await ch.set_permissions(member, read_messages=True, send_messages=True)
        await _db_update_ticket(self.channel_id, {"claimed_by": member.id})
        emb = discord.Embed(
            title="Ticket claimed",
            description=f"Claimed by {member.mention}",
            color=0xC41E3A,
        )
        await interaction.response.send_message(embed=emb)
        await ch.send(embed=emb)

    @discord.ui.button(label="Add user", style=discord.ButtonStyle.success, custom_id="sql_btn_add")
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            return await interaction.response.send_message("Use in server.", ephemeral=True)
        t = await _find_ticket(self.channel_id)
        if not t:
            return await interaction.response.send_message("Not a ticket.", ephemeral=True)
        cfg = await interaction.client.get_cfg()
        if not can_moderate_ticket(interaction.user, t, cfg):
            return await interaction.response.send_message("You cannot add users here.", ephemeral=True)
        await interaction.response.send_modal(AddUserModal(self.channel_id))

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, custom_id="sql_btn_tr")
    async def tr_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ch = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("Channel missing.", ephemeral=True)
        cfg = await interaction.client.get_cfg()
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message("Member only.", ephemeral=True)
        t = await _find_ticket(self.channel_id)
        if not t or not can_moderate_ticket(member, t, cfg):
            return await interaction.response.send_message("Not allowed.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        text = await _build_transcript(ch)
        tid = int(cfg.get("transcript_channel_id") or cfg.get("log_channel_id") or 0)
        if tid and interaction.guild:
            dest = interaction.guild.get_channel(tid)
            if isinstance(dest, discord.TextChannel):
                f = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{ch.name}-transcript.txt")
                await dest.send(f"Transcript (preview) for `#{ch.name}`", file=f)
        await interaction.followup.send("Done (sent to log/transcript channel if set).", ephemeral=True)


class CloseTicketModal(discord.ui.Modal, title="Close ticket"):
    reason = discord.ui.TextInput(
        label="Reason (optional)",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, channel_id: int) -> None:
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ch = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("Channel missing.", ephemeral=True)
        reason = str(self.reason.value).strip() or "No reason provided."
        cog = interaction.client.get_cog("TicketsCog")
        if isinstance(cog, TicketsCog):
            await cog._finalize_close(interaction, ch, reason, send_dm=True)
        else:
            await interaction.response.send_message("Bot error: cog missing.", ephemeral=True)


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        asyncio.create_task(self._dashboard_worker())

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != env_util.guild_id():
            return
        for t in await _db_list_open():
            if int(t.get("user_id", 0)) != member.id:
                continue
            ch = member.guild.get_channel(int(t.get("channel_id", 0)))
            if isinstance(ch, discord.TextChannel):
                try:
                    await self._finalize_close(
                        None,
                        ch,
                        "Member left the server",
                        closer_label="System",
                        send_dm=False,
                    )
                except Exception:
                    log.exception("auto-close on leave failed for %s", ch.id)

    async def panel_open(
        self,
        interaction: discord.Interaction,
        type_id: str,
        *,
        source: str,
        category_id_override: int | None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Use this in the server.", ephemeral=True)
        cfg = await self.bot.get_cfg()
        if cfg.get("bot_paused"):
            return await interaction.response.send_message("Ticketing is paused from the dashboard.", ephemeral=True)
        if interaction.user.id in [int(x) for x in cfg.get("blacklist_user_ids", [])]:
            return await interaction.response.send_message("You cannot open tickets.", ephemeral=True)
        await self.open_ticket(interaction, type_id, category_id_override=category_id_override)

    async def _dashboard_worker(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                cfg = await asyncio.to_thread(mongo_db.load_config)
                pending = cfg.get("pending_post_panel_channel_id")
                if pending:
                    guild = self.bot.get_guild(env_util.guild_id())
                    ch = guild.get_channel(int(pending)) if guild else None
                    if isinstance(ch, discord.TextChannel):
                        cfg_live = await self.bot.get_cfg()
                        msg = await self._post_panel_message(ch, cfg_live)

                        def save_panel(d: dict) -> None:
                            d["panel_channel_id"] = ch.id
                            d["panel_message_id"] = msg.id
                            d["pending_post_panel_channel_id"] = None

                        await _db_cfg_update(save_panel)
                        log.info("Posted ticket panel from dashboard to channel %s", ch.id)
                    else:

                        def clear_bad(d: dict) -> None:
                            d["pending_post_panel_channel_id"] = None

                        await _db_cfg_update(clear_bad)
            except Exception:
                log.exception("dashboard_worker")
            await asyncio.sleep(3)

    async def _post_panel_message(self, channel: discord.TextChannel, cfg: dict[str, Any]) -> discord.Message:
        embed = build_panel_embed(cfg)
        view = build_panel_view(cfg, self.panel_open)
        if len(view.children) == 0:
            return await channel.send(embed=embed)
        msg = await channel.send(embed=embed, view=view)
        self.bot.add_view(view, message_id=msg.id)
        return msg

    async def register_persistent_views(self) -> None:
        for t in await _db_list_open():
            cid = int(t.get("channel_id", 0))
            mid = int(t.get("control_message_id", 0))
            if cid and mid:
                self.bot.add_view(TicketControlView(cid), message_id=mid)

        cfg = await asyncio.to_thread(mongo_db.load_config)
        pc, pm = cfg.get("panel_channel_id"), cfg.get("panel_message_id")
        guild = self.bot.get_guild(env_util.guild_id())
        if pc and pm and guild:
            ch = guild.get_channel(int(pc))
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.fetch_message(int(pm))
                except (discord.NotFound, discord.HTTPException):
                    log.warning("Panel message not found; run /sync_panel after reposting.")
                else:
                    v = build_panel_view(cfg, self.panel_open)
                    if len(v.children) > 0:
                        self.bot.add_view(v, message_id=int(pm))
        log.info("Persistent views registered.")

    async def open_ticket(
        self,
        interaction: discord.Interaction,
        type_id: str,
        *,
        category_id_override: int | None = None,
    ) -> None:
        cfg = await self.bot.get_cfg()
        gid = env_util.guild_id()
        if interaction.guild is None or interaction.guild.id != gid:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)
        if cfg.get("bot_paused"):
            return await interaction.response.send_message("Ticketing is paused.", ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message("Member only.", ephemeral=True)
        max_open = int(cfg.get("max_open_per_user", 3))
        if len(await _open_for_user(member.id)) >= max_open:
            return await interaction.response.send_message(
                f"You already have {max_open} open ticket(s). Close one first.",
                ephemeral=True,
            )
        try:
            cat_id = int(category_id_override) if category_id_override is not None else 0
        except (TypeError, ValueError):
            cat_id = 0
        if not cat_id:
            return await interaction.response.send_message(
                "This ticket type has no **category_id**. Set a Discord category ID on each dropdown option and each "
                "panel button in the dashboard JSON (there is no global default category anymore).",
                ephemeral=True,
            )
        category = discord.utils.get(interaction.guild.categories, id=int(cat_id))
        if category is None:
            return await interaction.response.send_message("Ticket category missing.", ephemeral=True)

        def bump_counter(d: dict) -> None:
            d["ticket_counter"] = int(d.get("ticket_counter", 0)) + 1

        new_cfg = await _db_cfg_update(bump_counter)
        num = int(new_cfg.get("ticket_counter", 1))
        slug = "".join(c if c.isalnum() else "-" for c in member.name.lower())[:12] or "user"
        channel_name = f"ticket-{num}-{slug}"
        parent_category_id = int(category.id)

        overwrites: dict[Any, discord.PermissionOverwrite] = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        _apply_support_admin_overwrites(interaction.guild, overwrites, cfg, parent_category_id)

        await interaction.response.defer(ephemeral=True)
        try:
            ch = await interaction.guild.create_text_channel(
                name=channel_name[:100],
                category=category,
                overwrites=overwrites,
                reason=f"Ticket {type_id} for {member}",
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(f"Failed to create channel: {e}", ephemeral=True)

        welcome = discord.Embed(
            title=str(cfg.get("welcome_title", "Ticket")),
            description=str(cfg.get("welcome_body", "")),
            color=int(cfg.get("panel_color", 0xC41E3A)),
        )
        welcome.add_field(name="Type", value=type_id, inline=True)
        welcome.add_field(name="Opened by", value=member.mention, inline=True)
        welcome.set_footer(text=str(cfg.get("panel_footer", "SQL")))

        view = TicketControlView(ch.id)
        ctrl = await ch.send(content=member.mention, embed=welcome, view=view)
        self.bot.add_view(view, message_id=ctrl.id)

        ping_ids = _roles_to_ping(cfg, parent_category_id)
        if ping_ids:
            await ch.send(" ".join(f"<@&{rid}>" for rid in ping_ids))

        await _db_upsert_ticket(
            {
                "channel_id": ch.id,
                "user_id": member.id,
                "guild_id": interaction.guild.id,
                "control_message_id": ctrl.id,
                "opened_at": _utcnow_iso(),
                "type_id": type_id,
                "claimed_by": None,
                "parent_category_id": parent_category_id,
            }
        )
        await _db_bump_stat("tickets_created_total", 1)
        await interaction.followup.send(f"Opened: {ch.mention}", ephemeral=True)
        log_id = cfg.get("log_channel_id")
        if log_id:
            log_ch = interaction.guild.get_channel(int(log_id))
            if isinstance(log_ch, discord.TextChannel):
                le = discord.Embed(
                    title="Ticket opened",
                    description=f"{ch.mention} — {member} — type `{type_id}`",
                    color=0xC41E3A,
                )
                await log_ch.send(embed=le)

    async def _finalize_close(
        self,
        interaction: discord.Interaction | None,
        ch: discord.TextChannel,
        reason: str,
        *,
        closer_label: str | None = None,
        send_dm: bool = True,
    ) -> None:
        cfg = await self.bot.get_cfg()
        ticket = await _find_ticket(ch.id)
        opener_id = int(ticket.get("user_id", 0)) if ticket else 0

        if interaction is not None and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        text = await _build_transcript(ch)
        tid = int(cfg.get("transcript_channel_id") or cfg.get("log_channel_id") or 0)
        if tid and ch.guild:
            dest = ch.guild.get_channel(tid)
            if isinstance(dest, discord.TextChannel):
                f = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{ch.name}-closed.txt")
                e = discord.Embed(title="Ticket closed", description=reason, color=0x2B0000)
                e.add_field(name="Channel", value=f"#{ch.name}", inline=True)
                who = closer_label or (str(interaction.user) if interaction and interaction.user else "System")
                e.add_field(name="Closed by", value=who, inline=True)
                await dest.send(embed=e, file=f)

        if send_dm and opener_id and cfg.get("close_dm_embed", {}).get("enabled", True):
            dm = cfg.get("close_dm_embed") if isinstance(cfg.get("close_dm_embed"), dict) else {}
            try:
                user = self.bot.get_user(opener_id) or await self.bot.fetch_user(opener_id)
            except Exception:
                user = None
            if user:
                title = str(dm.get("title", "Ticket closed"))[:256]
                body = str(
                    dm.get("description", "Your ticket **{channel_name}** was closed.\n**Reason:** {reason}")
                )[:4096]
                body = body.replace("{channel_name}", ch.name).replace("{reason}", str(reason))
                emb = discord.Embed(
                    title=title,
                    description=body,
                    color=int(dm.get("color", 12933422)),
                )
                foot = str(dm.get("footer", "")).strip()
                if foot:
                    emb.set_footer(text=foot[:2048])
                try:
                    await user.send(embed=emb)
                except discord.HTTPException:
                    pass

        await _db_delete_ticket(ch.id)
        await _db_bump_stat("tickets_closed_total", 1)
        if interaction is not None:
            try:
                await interaction.followup.send("Ticket closed.", ephemeral=True)
            except discord.HTTPException:
                pass
        try:
            await ch.delete(reason=f"Ticket closed: {reason}")
        except discord.HTTPException:
            log.exception("delete channel failed")

    @app_commands.command(name="close", description="Close this ticket (moderators)")
    @app_commands.describe(reason="Optional reason")
    async def slash_close(self, interaction: discord.Interaction, reason: str = "Closed") -> None:
        if not interaction.channel or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Text channel only.", ephemeral=True)
        t = await _find_ticket(interaction.channel.id)
        if not t:
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
        cfg = await self.bot.get_cfg()
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message("Member only.", ephemeral=True)
        if not can_moderate_ticket(member, t, cfg):
            return await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
        await self._finalize_close(interaction, interaction.channel, reason, send_dm=True)

    @app_commands.command(name="ticket_panel", description="Post the ticket panel (owner)")
    @app_commands.describe(channel="Where to post the panel")
    async def ticket_panel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if interaction.user.id != env_util.owner_id():
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        cfg = await self.bot.get_cfg()
        msg = await self._post_panel_message(channel, cfg)

        def save_panel(d: dict) -> None:
            d["panel_channel_id"] = channel.id
            d["panel_message_id"] = msg.id

        await _db_cfg_update(save_panel)
        await interaction.followup.send(f"Panel posted: {msg.jump_url}", ephemeral=True)

    @app_commands.command(name="sync_panel", description="Re-register panel controls after restart (owner)")
    async def sync_panel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != env_util.owner_id():
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        cfg = await self.bot.get_cfg()
        pc = cfg.get("panel_channel_id")
        pm = cfg.get("panel_message_id")
        if not pc or not pm:
            return await interaction.response.send_message("No panel saved in config.", ephemeral=True)
        ch = interaction.guild.get_channel(int(pc)) if interaction.guild else None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("Panel channel missing.", ephemeral=True)
        try:
            await ch.fetch_message(int(pm))
        except discord.NotFound:
            return await interaction.response.send_message("Panel message not found.", ephemeral=True)
        v = build_panel_view(cfg, self.panel_open)
        if len(v.children) == 0:
            return await interaction.response.send_message(
                "Panel has no components (enable dropdown options and/or buttons in the dashboard).",
                ephemeral=True,
            )
        self.bot.add_view(v, message_id=int(pm))
        await interaction.response.send_message("Panel view re-registered.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))
