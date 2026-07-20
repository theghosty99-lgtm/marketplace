# name=bot.py
"""
Discord Marketplace Bot - Starter (updated with Marketplace role verification)

Same features as earlier plus:
- MARKETPLACE_ROLE_ID enforcement for Selling
"""

import os
import re
import random
import logging
import aiosqlite
from datetime import datetime, timedelta
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from discord import PermissionOverwrite

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("marketplace")

# Config from .env
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
SELLING_CHANNEL_ID = int(os.getenv("SELLING_CHANNEL_ID")) if os.getenv("SELLING_CHANNEL_ID") else None
BUYING_CHANNEL_ID = int(os.getenv("BUYING_CHANNEL_ID")) if os.getenv("BUYING_CHANNEL_ID") else None
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID")) if os.getenv("DASHBOARD_CHANNEL_ID") else None
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID")) if os.getenv("TICKET_CATEGORY_ID") else None
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID")) if os.getenv("MOD_ROLE_ID") else None
SCAM_LOG_CHANNEL_ID = int(os.getenv("SCAM_LOG_CHANNEL_ID")) if os.getenv("SCAM_LOG_CHANNEL_ID") else None
DB_PATH = os.getenv("DB_PATH") or "marketplace.db"
MAX_LISTINGS_PER_USER_PER_DAY = int(os.getenv("MAX_LISTINGS_PER_USER_PER_DAY") or 5)
MARKETPLACE_ROLE_ID = int(os.getenv("MARKETPLACE_ROLE_ID")) if os.getenv("MARKETPLACE_ROLE_ID") else None

# Minimal validation
if not TOKEN:
    log.error("DISCORD_TOKEN missing in .env")
    raise SystemExit(1)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True  # required for permission overwrites & role checks

bot = commands.Bot(command_prefix=".", intents=intents)

# DB: initialize
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS anonymous (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL, -- 'seller' or 'buyer'
                anon_name TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, kind)
            );

            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_ref TEXT NOT NULL UNIQUE, -- e.g. L-1234
                mode TEXT NOT NULL, -- 'sell' or 'buy'
                poster_id TEXT NOT NULL,
                anon_name TEXT NOT NULL,
                product TEXT NOT NULL,
                price TEXT,
                details TEXT,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_ref TEXT NOT NULL UNIQUE, -- e.g. T-1234
                channel_id TEXT NOT NULL,
                listing_ref TEXT,
                seller_id TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                status TEXT NOT NULL, -- Waiting, Claimed, Closed, Deleted
                claimer_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_ref TEXT NOT NULL UNIQUE,
                ticket_ref TEXT,
                listing_ref TEXT,
                reporter_id TEXT NOT NULL,
                reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await db.commit()
    log.info("SQLite DB initialized at %s", DB_PATH)

# Helpers: anon name generation & DB ops
def _rand_digits(n=4):
    return "".join(str(random.randint(0,9)) for _ in range(n))

async def get_or_create_anon(user_id: int, kind: str) -> str:
    """Return anon name for (user, kind). Create if not exists."""
    kind = kind if kind in ("seller", "buyer") else "seller"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT anon_name FROM anonymous WHERE user_id = ? AND kind = ?", (str(user_id), kind))
        row = await cur.fetchone()
        if row:
            return row[0]
        # create unique anon name
        for _ in range(10):
            anon = ("Seller-" if kind=="seller" else "Buyer-") + _rand_digits(4)
            # ensure uniqueness
            c2 = await db.execute("SELECT 1 FROM anonymous WHERE anon_name = ?", (anon,))
            exists = await c2.fetchone()
            if not exists:
                await db.execute("INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)", (str(user_id), kind, anon))
                await db.commit()
                return anon
        # fallback
        anon = ("Seller-" if kind=="seller" else "Buyer-") + str(random.randint(1000,9999))
        await db.execute("INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)", (str(user_id), kind, anon))
        await db.commit()
        return anon

def _generate_listing_ref() -> str:
    return "L-" + _rand_digits(6)

def _generate_ticket_ref() -> str:
    return "T-" + _rand_digits(6)

def _generate_report_ref() -> str:
    return "R-" + _rand_digits(8)

# Minimal sanitization: disallow URLs / invites
URL_PATTERN = re.compile(r"https?://|discord\.gg|discordapp\.com/invite|\.gg/", re.IGNORECASE)

def contains_disallowed_links(text: str) -> bool:
    if not text:
        return False
    return bool(URL_PATTERN.search(text))

# Permission checks
def bot_can_create_channel(guild: discord.Guild) -> bool:
    member = guild.get_member(bot.user.id)
    return member and member.guild_permissions.manage_channels

def bot_can_send_in(channel: discord.abc.GuildChannel) -> bool:
    member = channel.guild.get_member(bot.user.id)
    if not member:
        return False
    perms = channel.permissions_for(member)
    return perms.send_messages and perms.embed_links and perms.read_message_history

def user_has_marketplace_role(member: discord.Member) -> bool:
    """Return True if MARKETPLACE_ROLE_ID not set or member has the role."""
    if not MARKETPLACE_ROLE_ID:
        return True  # enforcement disabled unless env config provided
    return any(r.id == MARKETPLACE_ROLE_ID for r in member.roles)

# Views and Modals
class DashboardView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Selling", style=discord.ButtonStyle.danger, emoji="🟥", custom_id="dashboard_selling")
    async def selling(self, interaction: discord.Interaction, button: Button):
        # Marketplace role enforcement before opening Selling modal
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if not member:
            return await interaction.response.send_message("Member information not available.", ephemeral=True)
        if not user_has_marketplace_role(member):
            return await interaction.response.send_message("❌ You must have the **Marketplace** role before creating a selling listing. Please obtain the role from the server before posting items for sale.", ephemeral=True)
        # open modal for selling
        modal = ListingModal(mode="sell")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Buying", style=discord.ButtonStyle.success, emoji="🟩", custom_id="dashboard_buying")
    async def buying(self, interaction: discord.Interaction, button: Button):
        modal = ListingModal(mode="buy")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Information", style=discord.ButtonStyle.secondary, emoji="ℹ️", custom_id="dashboard_info")
    async def info(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="Marketplace Information", color=0x2F3136)
        embed.add_field(name="How it works", value="Create a selling/buying listing from the dashboard. Contact the other party via private ticket.", inline=False)
        embed.add_field(name="Safety (Buyers & Sellers)", value="• Never send payment outside the trade ticket.\n• Always use official middleman when required.\n• Staff can monitor every trade.\n• Report scammers with the Report button.", inline=False)
        embed.add_field(name="Reporting", value="Press the Report Listing or Report Scammer buttons. Staff will be notified and the report saved.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ListingModal(Modal, title="Create Marketplace Listing"):
    product = TextInput(label="Product Name", placeholder="Name of the product", max_length=150)
    price = TextInput(label="Price or Budget", placeholder="e.g. $5, VHB, or Budget", max_length=64, required=False)
    details = TextInput(label="Details (no links!)", placeholder="Payment methods, delivery, notes", style=discord.TextStyle.long, max_length=1500, required=False)

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode  # 'sell' or 'buy'

    async def on_submit(self, interaction: discord.Interaction):
        # Validate no links
        if contains_disallowed_links(self.product) or contains_disallowed_links(self.price) or contains_disallowed_links(self.details):
            return await interaction.response.send_message("Your listing contains links or invites. Links are not allowed in public listings.", ephemeral=True)

        # If selling, verify marketplace role again (safety)
        if self.mode == "sell":
            guild = interaction.guild
            member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
            if not member:
                return await interaction.response.send_message("Member information not available.", ephemeral=True)
            if not user_has_marketplace_role(member):
                return await interaction.response.send_message("❌ You must have the **Marketplace** role before creating a selling listing. Please obtain the role from the server before posting items for sale.", ephemeral=True)

        # Rate-limit: count listings in last 24h
        async with aiosqlite.connect(DB_PATH) as db:
            cutoff = datetime.utcnow() - timedelta(days=1)
            cur = await db.execute("SELECT COUNT(1) FROM listings WHERE poster_id = ? AND created_at > ?", (str(interaction.user.id), cutoff.isoformat()))
            row = await cur.fetchone()
            cnt = row[0] if row else 0
            if cnt >= MAX_LISTINGS_PER_USER_PER_DAY:
                return await interaction.response.send_message(f"You have reached your daily listing limit ({MAX_LISTINGS_PER_USER_PER_DAY}).", ephemeral=True)

        mode = self.mode
        poster = interaction.user
        anon_name = await get_or_create_anon(poster.id, "seller" if mode=="sell" else "buyer")
        listing_ref = _generate_listing_ref()
        product = self.product.value.strip()
        price = (self.price.value or "").strip()
        details = (self.details.value or "").strip()

        target_channel_id = SELLING_CHANNEL_ID if mode == "sell" else BUYING_CHANNEL_ID
        guild = interaction.guild
        if not target_channel_id:
            return await interaction.response.send_message("Marketplace channels are not configured. Contact an admin.", ephemeral=True)
        target = guild.get_channel(target_channel_id)
        if target is None:
            return await interaction.response.send_message("Configured marketplace channel not found on this server.", ephemeral=True)
        if not bot_can_send_in(target):
            return await interaction.response.send_message("Bot lacks permission to send messages or embeds in the marketplace channel.", ephemeral=True)

        embed = discord.Embed(title=f"Marketplace — {'Selling' if mode=='sell' else 'Buying'}", color=0xFF531A)
        embed.add_field(name="Listing ID", value=f"`{listing_ref}`", inline=True)
        embed.add_field(name=("Seller" if mode=='sell' else "Buyer"), value=f"`{anon_name}`", inline=True)
        embed.add_field(name="Product", value=product, inline=True)
        embed.add_field(name="Price/Budget", value=price or "—", inline=True)
        embed.add_field(name="Details", value=details or "—", inline=False)
        embed.set_footer(text="Marketplace • Keep trades inside tickets")
        embed.timestamp = datetime.utcnow()

        try:
            # send message and attach contact/report buttons
            msg = await target.send(embed=embed)
            custom_id = f"contact:{listing_ref}"
            view = ListingView(listing_ref=listing_ref)
            await msg.edit(view=view)  # attach buttons
            # save to DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO listings (listing_ref, mode, poster_id, anon_name, product, price, details, channel_id, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (listing_ref, mode, str(poster.id), anon_name, product, price, details, str(target.id), str(msg.id)))
                await db.commit()
            # register view globally so it survives restart
            bot.add_view(view)
            await interaction.response.send_message(f"Your listing has been posted: {msg.jump_url}", ephemeral=True)
        except Exception as e:
            log.exception("Failed to post listing: %s", e)
            await interaction.response.send_message("Failed to post listing (internal error). Contact an admin.", ephemeral=True)

class ListingView(View):
    """View for each listing with Contact Seller/Buyer and Report Listing."""
    def __init__(self, listing_ref: str):
        super().__init__(timeout=None)
        self.listing_ref = listing_ref

    @discord.ui.button(label="Contact", style=discord.ButtonStyle.primary, custom_id=lambda: f"contact:{''}")
    async def contact(self, interaction: discord.Interaction, button: Button):
        # This callback won't be used because we handle the custom_id in on_interaction.
        await interaction.response.send_message("Please use the Contact button. (Fallback)", ephemeral=True)

    @discord.ui.button(label="Report Listing", style=discord.ButtonStyle.danger, custom_id=lambda: f"report_listing:{''}")
    async def report_listing(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("To report this listing, please use the Report Scammer button inside the ticket after contacting or DM staff.", ephemeral=True)

# Ticket controls View (persistent)
class TicketControls(View):
    def __init__(self, ticket_ref: str):
        super().__init__(timeout=None)
        self.ticket_ref = ticket_ref

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id=lambda: f"close_ticket:{''}")
    async def close_ticket(self, interaction: discord.Interaction, btn: Button):
        # make channel read-only for non-staff
        channel = interaction.channel
        guild = interaction.guild
        try:
            for target, ow in list(channel.overwrites.items()):
                try:
                    target_id = target.id
                except Exception:
                    target_id = target
                if target_id == bot.user.id or (MOD_ROLE_ID and target_id == MOD_ROLE_ID):
                    continue
                await channel.set_permissions(target, overwrite=PermissionOverwrite(view_channel=ow.view_channel, send_messages=False, read_message_history=ow.read_message_history))
            # update DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ? WHERE channel_id = ?", ("Closed", str(channel.id)))
                await db.commit()
            await interaction.response.send_message("Ticket closed and set to read-only.", ephemeral=True)
        except Exception:
            log.exception("close_ticket failed")
            await interaction.response.send_message("Failed to close ticket (internal error).", ephemeral=True)

    @discord.ui.button(label="🗑 Delete Ticket", style=discord.ButtonStyle.secondary, custom_id=lambda: f"delete_ticket:{''}")
    async def delete_ticket(self, interaction: discord.Interaction, btn: Button):
        # staff only
        member = interaction.user
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in member.roles):
            return await interaction.response.send_message("Only staff can delete tickets.", ephemeral=True)
        channel = interaction.channel
        try:
            # remove from DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM tickets WHERE channel_id = ?", (str(channel.id),))
                await db.commit()
            await interaction.response.send_message("Deleting ticket...", ephemeral=True)
            await channel.delete()
        except Exception:
            log.exception("delete_ticket failed")
            await interaction.response.send_message("Failed to delete ticket (internal error).", ephemeral=True)

    @discord.ui.button(label="🚨 Report Scammer", style=discord.ButtonStyle.danger, custom_id=lambda: f"report_scammer:{''}")
    async def report_scammer(self, interaction: discord.Interaction, btn: Button):
        # Create report for ticket and notify staff
        channel = interaction.channel
        # get ticket by channel_id
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT ticket_ref, listing_ref FROM tickets WHERE channel_id = ?", (str(channel.id),))
            row = await cur.fetchone()
            ticket_ref = row[0] if row else None
            listing_ref = row[1] if row else None
        report_ref = _generate_report_ref()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO reports (report_ref, ticket_ref, listing_ref, reporter_id, reason) VALUES (?, ?, ?, ?, ?)",
                                 (report_ref, ticket_ref, listing_ref, str(interaction.user.id), "Reported via ticket button"))
                await db.commit()
            # Notify staff in the configured scam channel or system channel
            if SCAM_LOG_CHANNEL_ID:
                guild = interaction.guild
                log_chan = guild.get_channel(SCAM_LOG_CHANNEL_ID)
                if log_chan and bot_can_send_in(log_chan):
                    await log_chan.send(embed=discord.Embed(title="Scam Report",
                                                           description=f"Report `{report_ref}` for ticket `{ticket_ref}` listing `{listing_ref}` reported by <@{interaction.user.id}>",
                                                           color=0xE74C3C))
            await interaction.response.send_message("Report submitted to staff. They will review.", ephemeral=True)
        except Exception:
            log.exception("report_scammer failed")
            await interaction.response.send_message("Failed to submit report (internal error).", ephemeral=True)

    @discord.ui.button(label="👮 Claim Ticket", style=discord.ButtonStyle.primary, custom_id=lambda: f"claim_ticket:{''}")
    async def claim_ticket(self, interaction: discord.Interaction, btn: Button):
        member = interaction.user
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in member.roles):
            return await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
        channel = interaction.channel
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ?, claimer_id = ? WHERE channel_id = ?", ("Claimed", str(member.id), str(channel.id)))
                await db.commit()
            await interaction.response.send_message(f"Ticket claimed by {member.mention}.", ephemeral=True)
        except Exception:
            log.exception("claim_ticket failed")
            await interaction.response.send_message("Failed to claim ticket (internal error).", ephemeral=True)

# Global interaction handler for dynamic custom_ids (listing contact, listing report)
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    cid = interaction.data.get("custom_id", "")
    if cid.startswith("contact:"):
        # format contact:L-12345
        try:
            _, listing_ref = cid.split(":", 1)
        except Exception:
            return await interaction.response.send_message("Invalid button.", ephemeral=True)

        # fetch listing
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT mode, poster_id, anon_name, channel_id FROM listings WHERE listing_ref = ?", (listing_ref,))
            row = await cur.fetchone()
        if not row:
            return await interaction.response.send_message("Listing not found or removed.", ephemeral=True)
        mode, poster_id_str, anon_name, listing_channel_id = row
        poster_id = int(poster_id_str)
        clicker_id = interaction.user.id

        # Determine seller and buyer based on mode
        if mode == "sell":
            seller_id = poster_id
            buyer_id = clicker_id
        else:  # 'buy'
            seller_id = clicker_id
            buyer_id = poster_id

        if seller_id == buyer_id:
            return await interaction.response.send_message("You cannot contact yourself.", ephemeral=True)

        guild = interaction.guild
        # Check bot can create channel
        if not bot_can_create_channel(guild):
            return await interaction.response.send_message("Bot lacks Manage Channels permission to create tickets. Contact an admin.", ephemeral=True)

        # Check for existing ticket with same seller/buyer/listing
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT channel_id FROM tickets WHERE seller_id = ? AND buyer_id = ? AND listing_ref = ?", (str(seller_id), str(buyer_id), listing_ref))
            existing = await cur.fetchone()
        if existing:
            ch = guild.get_channel(int(existing[0]))
            if ch:
                return await interaction.response.send_message(f"A ticket already exists: {ch.mention}", ephemeral=True)
            else:
                # remove stale
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM tickets WHERE channel_id = ?", (str(existing[0]),))
                    await db.commit()

        # Create ticket channel with overwrites: seller, buyer, mod role, bot
        try:
            overwrites = { guild.default_role: PermissionOverwrite(view_channel=False) }
            overwrites[seller_id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            overwrites[buyer_id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            if MOD_ROLE_ID:
                overwrites[MOD_ROLE_ID] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            overwrites[bot.user.id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
            # channel name short
            name = f"ticket-{listing_ref.lower()}-{_rand_digits(3)}"
            channel = await guild.create_text_channel(name=name, overwrites=overwrites, topic=f"ticket|listing:{listing_ref}|seller:{seller_id}|buyer:{buyer_id}", category=category)
            ticket_ref = _generate_ticket_ref()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO tickets (ticket_ref, channel_id, listing_ref, seller_id, buyer_id, status) VALUES (?, ?, ?, ?, ?, ?)",
                                 (ticket_ref, str(channel.id), listing_ref, str(seller_id), str(buyer_id), "Waiting"))
                await db.commit()
            # send ticket embed + persistent controls
            seller_anon = await get_or_create_anon(seller_id, "seller")
            buyer_anon = await get_or_create_anon(buyer_id, "buyer")
            embed = discord.Embed(title="Trade Ticket", color=0x835AF1)
            embed.add_field(name="Listing ID", value=f"`{listing_ref}`", inline=True)
            embed.add_field(name="Seller", value=f"`{seller_anon}`", inline=True)
            embed.add_field(name="Buyer", value=f"`{buyer_anon}`", inline=True)
            embed.add_field(name="Trade Status", value="Waiting for Trade", inline=False)
            embed.set_footer(text=f"Ticket: {ticket_ref}")
            controls = TicketControls(ticket_ref)
            await channel.send(content=f"<@{seller_id}> <@{buyer_id}>", embed=embed, view=controls)
            # register controls view globally
            bot.add_view(controls)
            await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        except Exception:
            log.exception("Failed to create ticket")
            await interaction.response.send_message("Failed to create ticket (internal error).", ephemeral=True)
    elif cid.startswith("report_listing:"):
        await interaction.response.send_message("To report a listing, click Contact to open a ticket, then use Report Scammer inside the ticket. Otherwise DM staff.", ephemeral=True)

# Startup: init DB, re-register views for existing listings and ticket controls
@bot.event
async def on_ready():
    log.info("Bot ready: %s (%s)", bot.user, bot.user.id)
    await init_db()

    # Add dashboard & global ticket controls (stateless)
    bot.add_view(DashboardView())

    # Re-register ListingView for each listing stored in DB
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT listing_ref FROM listings")
            rows = await cur.fetchall()
        for (listing_ref,) in rows:
            view = ListingView(listing_ref=listing_ref)
            bot.add_view(view)
        log.info("Registered %d listing views", len(rows))
    except Exception:
        log.exception("Failed to re-register listing views")

    # Sync app commands
    try:
        if GUILD_ID:
            await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            log.info("Synced commands to guild %s", GUILD_ID)
        else:
            await bot.tree.sync()
            log.info("Synced global commands")
    except Exception:
        log.exception("Failed to sync commands")

# Dashboard command for admins (single use or to repost)
@bot.tree.command(name="dashboard", description="Post the marketplace dashboard (Admin only)")
async def cmd_dashboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("You must be a server manager to post the dashboard.", ephemeral=True)
    embed = discord.Embed(title="📦 Marketplace", description="Welcome to the Marketplace. Use the buttons to create listings or read the rules.", color=0x2F3136)
    embed.add_field(name="Safe Trading", value="Keep trades inside private tickets. Never send payment outside the ticket. Use official middleman when required.", inline=False)
    view = DashboardView()
    await interaction.response.send_message(embed=embed, view=view)

# Convenience fallback commands to open modals (enforce Marketplace role on /sell)
@bot.tree.command(name="sell", description="Open selling modal")
async def cmd_sell(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    if not member:
        return await interaction.response.send_message("Member info not available.", ephemeral=True)
    if not user_has_marketplace_role(member):
        return await interaction.response.send_message("❌ You must have the **Marketplace** role before creating a selling listing. Please obtain the role from the server before posting items for sale.", ephemeral=True)
    await interaction.response.send_modal(ListingModal(mode="sell"))

@bot.tree.command(name="buy", description="Open buying modal")
async def cmd_buy(interaction: discord.Interaction):
    await interaction.response.send_modal(ListingModal(mode="buy"))

# Optional: cleanup when listing message deleted (remove DB row)
@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    # payload.message_id and payload.channel_id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM listings WHERE message_id = ? AND channel_id = ?", (str(payload.message_id), str(payload.channel_id)))
            await db.commit()
            log.info("Removed listing DB row for deleted message %s in channel %s", payload.message_id, payload.channel_id)
    except Exception:
        log.exception("Failed to handle raw message delete")

if __name__ == "__main__":
    bot.run(TOKEN)
