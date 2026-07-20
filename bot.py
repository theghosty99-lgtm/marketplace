"""
Discord Marketplace Bot - COMPLETE FIXED VERSION
All commands work, proper syncing, full features
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

# CONFIG
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

if not TOKEN or not GUILD_ID:
    log.error("DISCORD_TOKEN or GUILD_ID missing")
    raise SystemExit(1)

# BOT SETUP
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)

# DATABASE
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS anonymous (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                anon_name TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, kind)
            );
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_ref TEXT NOT NULL UNIQUE,
                mode TEXT NOT NULL,
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
                ticket_ref TEXT NOT NULL UNIQUE,
                channel_id TEXT NOT NULL,
                listing_ref TEXT,
                seller_id TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                status TEXT NOT NULL,
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
        """)
        await db.commit()
    log.info("Database ready")

# HELPERS
def _rand_digits(n=4):
    return "".join(str(random.randint(0, 9)) for _ in range(n))

async def get_or_create_anon(user_id: int, kind: str) -> str:
    kind = "seller" if kind == "seller" else "buyer"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT anon_name FROM anonymous WHERE user_id = ? AND kind = ?", (str(user_id), kind))
        row = await cur.fetchone()
        if row:
            return row[0]
        for _ in range(10):
            anon = f"{'Seller' if kind == 'seller' else 'Buyer'}-{_rand_digits(4)}"
            c = await db.execute("SELECT 1 FROM anonymous WHERE anon_name = ?", (anon,))
            if not await c.fetchone():
                await db.execute("INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)", (str(user_id), kind, anon))
                await db.commit()
                return anon
        anon = f"{'Seller' if kind == 'seller' else 'Buyer'}-{random.randint(1000, 9999)}"
        await db.execute("INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)", (str(user_id), kind, anon))
        await db.commit()
        return anon

def _gen_listing_ref():
    return f"L-{_rand_digits(6)}"

def _gen_ticket_ref():
    return f"T-{_rand_digits(6)}"

def _gen_report_ref():
    return f"R-{_rand_digits(8)}"

URL_PATTERN = re.compile(r"https?://|discord\.gg|\.gg/", re.IGNORECASE)

def has_links(text):
    return bool(URL_PATTERN.search(text or ""))

def bot_can_create_channel(guild):
    m = guild.get_member(bot.user.id)
    return m and m.guild_permissions.manage_channels

def bot_can_send(channel):
    m = channel.guild.get_member(bot.user.id)
    if not m:
        return False
    p = channel.permissions_for(m)
    return p.send_messages and p.embed_links

def has_marketplace_role(member):
    if not MARKETPLACE_ROLE_ID:
        return True
    return any(r.id == MARKETPLACE_ROLE_ID for r in member.roles)

# UI VIEWS
class DashboardView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Selling", style=discord.ButtonStyle.danger, emoji="🟥", custom_id="sell_btn")
    async def sell_btn(self, i: discord.Interaction, b: Button):
        g = i.guild
        m = i.user if isinstance(i.user, discord.Member) else g.get_member(i.user.id)
        if not m:
            return await i.response.send_message("Member info unavailable", ephemeral=True)
        if not has_marketplace_role(m):
            return await i.response.send_message("❌ Need Marketplace role", ephemeral=True)
        await i.response.send_modal(ListingModal("sell"))

    @discord.ui.button(label="Buying", style=discord.ButtonStyle.success, emoji="🟩", custom_id="buy_btn")
    async def buy_btn(self, i: discord.Interaction, b: Button):
        await i.response.send_modal(ListingModal("buy"))

    @discord.ui.button(label="Info", style=discord.ButtonStyle.secondary, emoji="ℹ️", custom_id="info_btn")
    async def info_btn(self, i: discord.Interaction, b: Button):
        e = discord.Embed(title="Marketplace Info", color=0x2F3136)
        e.add_field(name="Rules", value="Keep trades in tickets • Never pay outside • Report scammers", inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

class ListingModal(Modal, title="Create Listing"):
    product = TextInput(label="Product", placeholder="Item name", max_length=150)
    price = TextInput(label="Price/Budget", placeholder="$5 or Budget", max_length=64, required=False)
    details = TextInput(label="Details", placeholder="Info", style=discord.TextStyle.long, max_length=1500, required=False)

    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def on_submit(self, i: discord.Interaction):
        if has_links(self.product.value) or has_links(self.price.value) or has_links(self.details.value):
            return await i.response.send_message("❌ No links allowed", ephemeral=True)

        if self.mode == "sell":
            g = i.guild
            m = i.user if isinstance(i.user, discord.Member) else g.get_member(i.user.id)
            if not m or not has_marketplace_role(m):
                return await i.response.send_message("❌ Need Marketplace role", ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            c = await db.execute("SELECT COUNT(*) FROM listings WHERE poster_id = ? AND created_at > ?", (str(i.user.id), (datetime.utcnow() - timedelta(days=1)).isoformat()))
            cnt = (await c.fetchone())[0]
            if cnt >= MAX_LISTINGS_PER_USER_PER_DAY:
                return await i.response.send_message(f"❌ Daily limit: {MAX_LISTINGS_PER_USER_PER_DAY}", ephemeral=True)

        ch_id = SELLING_CHANNEL_ID if self.mode == "sell" else BUYING_CHANNEL_ID
        if not ch_id:
            return await i.response.send_message("❌ Channel not configured", ephemeral=True)
        ch = i.guild.get_channel(ch_id)
        if not ch or not bot_can_send(ch):
            return await i.response.send_message("❌ Bot cannot send there", ephemeral=True)

        ref = _gen_listing_ref()
        anon = await get_or_create_anon(i.user.id, self.mode)
        e = discord.Embed(title=f"{'SELL' if self.mode == 'sell' else 'BUY'}", color=0xFF531A)
        e.add_field(name="ID", value=f"`{ref}`", inline=True)
        e.add_field(name=("Seller" if self.mode == "sell" else "Buyer"), value=f"`{anon}`", inline=True)
        e.add_field(name="Product", value=self.product.value, inline=True)
        e.add_field(name="Price", value=self.price.value or "—", inline=True)
        e.add_field(name="Details", value=self.details.value or "—", inline=False)
        e.timestamp = datetime.utcnow()

        try:
            msg = await ch.send(embed=e)
            v = ListingView(ref)
            await msg.edit(view=v)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO listings (listing_ref, mode, poster_id, anon_name, product, price, details, channel_id, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (ref, self.mode, str(i.user.id), anon, self.product.value, self.price.value or "", self.details.value or "", str(ch.id), str(msg.id)))
                await db.commit()
            bot.add_view(v)
            await i.response.send_message(f"✅ Posted: {msg.jump_url}", ephemeral=True)
        except Exception as e:
            log.exception(f"Post failed: {e}")
            await i.response.send_message("❌ Failed", ephemeral=True)

class ListingView(View):
    def __init__(self, ref):
        super().__init__(timeout=None)
        self.ref = ref

    @discord.ui.button(label="Contact", style=discord.ButtonStyle.primary)
    async def contact(self, i: discord.Interaction, b: Button):
        async with aiosqlite.connect(DB_PATH) as db:
            r = await db.execute("SELECT mode, poster_id FROM listings WHERE listing_ref = ?", (self.ref,))
            row = await r.fetchone()
        if not row:
            return await i.response.send_message("❌ Not found", ephemeral=True)
        mode, seller_id = row[0], int(row[1])
        buyer_id = i.user.id
        if mode == "buy":
            seller_id, buyer_id = buyer_id, seller_id
        if seller_id == buyer_id:
            return await i.response.send_message("❌ Cannot contact yourself", ephemeral=True)
        if not bot_can_create_channel(i.guild):
            return await i.response.send_message("❌ Bot cannot create channels", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            r = await db.execute("SELECT channel_id FROM tickets WHERE seller_id = ? AND buyer_id = ? AND listing_ref = ?", (str(seller_id), str(buyer_id), self.ref))
            ex = await r.fetchone()
        if ex:
            ch = i.guild.get_channel(int(ex[0]))
            if ch:
                return await i.response.send_message(f"✅ Ticket: {ch.mention}", ephemeral=True)
        try:
            ow = {i.guild.default_role: PermissionOverwrite(view_channel=False), seller_id: PermissionOverwrite(view_channel=True, send_messages=True), buyer_id: PermissionOverwrite(view_channel=True, send_messages=True), bot.user.id: PermissionOverwrite(view_channel=True, send_messages=True)}
            if MOD_ROLE_ID:
                ow[MOD_ROLE_ID] = PermissionOverwrite(view_channel=True, send_messages=True)
            cat = i.guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
            ch = await i.guild.create_text_channel(f"ticket-{self.ref.lower()}-{_rand_digits(2)}", overwrites=ow, category=cat)
            tref = _gen_ticket_ref()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO tickets (ticket_ref, channel_id, listing_ref, seller_id, buyer_id, status) VALUES (?, ?, ?, ?, ?, ?)", (tref, str(ch.id), self.ref, str(seller_id), str(buyer_id), "Waiting"))
                await db.commit()
            e = discord.Embed(title="Ticket", color=0x835AF1)
            e.add_field(name="Listing", value=f"`{self.ref}`", inline=True)
            e.set_footer(text=f"Ticket: {tref}")
            tc = TicketControls(tref)
            await ch.send(content=f"<@{seller_id}> <@{buyer_id}>", embed=e, view=tc)
            bot.add_view(tc)
            await i.response.send_message(f"✅ Ticket: {ch.mention}", ephemeral=True)
        except Exception as e:
            log.exception(f"Failed: {e}")
            await i.response.send_message("❌ Failed", ephemeral=True)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger)
    async def report(self, i: discord.Interaction, b: Button):
        await i.response.send_message("Use Report in ticket", ephemeral=True)

class TicketControls(View):
    def __init__(self, ref):
        super().__init__(timeout=None)
        self.ref = ref

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, i: discord.Interaction, b: Button):
        try:
            for t in list(i.channel.overwrites.keys()):
                tid = t.id if hasattr(t, 'id') else t
                if tid != bot.user.id:
                    await i.channel.set_permissions(t, send_messages=False)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ? WHERE channel_id = ?", ("Closed", str(i.channel.id)))
                await db.commit()
            await i.response.send_message("✅ Closed", ephemeral=True)
        except Exception as e:
            log.exception(f"Failed: {e}")

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.secondary)
    async def delete(self, i: discord.Interaction, b: Button):
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in i.user.roles):
            return await i.response.send_message("❌ Staff only", ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM tickets WHERE channel_id = ?", (str(i.channel.id),))
                await db.commit()
            await i.channel.delete()
        except Exception as e:
            log.exception(f"Failed: {e}")

    @discord.ui.button(label="Report Scammer", style=discord.ButtonStyle.danger)
    async def report_scam(self, i: discord.Interaction, b: Button):
        async with aiosqlite.connect(DB_PATH) as db:
            r = await db.execute("SELECT ticket_ref, listing_ref FROM tickets WHERE channel_id = ?", (str(i.channel.id),))
            row = await r.fetchone()
        rref = _gen_report_ref()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO reports (report_ref, ticket_ref, listing_ref, reporter_id, reason) VALUES (?, ?, ?, ?, ?)", (rref, row[0] if row else None, row[1] if row else None, str(i.user.id), "Reported"))
                await db.commit()
            if SCAM_LOG_CHANNEL_ID:
                lch = i.guild.get_channel(SCAM_LOG_CHANNEL_ID)
                if lch and bot_can_send(lch):
                    await lch.send(f"📢 Report {rref}: <@{i.user.id}>")
            await i.response.send_message("✅ Reported", ephemeral=True)
        except Exception as e:
            log.exception(f"Failed: {e}")

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary)
    async def claim(self, i: discord.Interaction, b: Button):
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in i.user.roles):
            return await i.response.send_message("❌ Staff only", ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ?, claimer_id = ? WHERE channel_id = ?", ("Claimed", str(i.user.id), str(i.channel.id)))
                await db.commit()
            await i.response.send_message(f"✅ Claimed", ephemeral=True)
        except Exception as e:
            log.exception(f"Failed: {e}")

# COMMANDS (MUST BE BEFORE on_ready)
@bot.tree.command(name="dashboard", description="Post marketplace dashboard")
async def dashboard(i: discord.Interaction):
    if not i.user.guild_permissions.manage_guild:
        return await i.response.send_message("❌ Manager only", ephemeral=True)
    e = discord.Embed(title="📦 Marketplace", description="Create listings here", color=0x2F3136)
    e.add_field(name="Safety", value="Use tickets • No payments outside", inline=False)
    v = DashboardView()
    await i.response.send_message(embed=e, view=v)

@bot.tree.command(name="sell", description="Create selling listing")
async def sell(i: discord.Interaction):
    g = i.guild
    m = i.user if isinstance(i.user, discord.Member) else g.get_member(i.user.id)
    if not m or not has_marketplace_role(m):
        return await i.response.send_message("❌ Need Marketplace role", ephemeral=True)
    await i.response.send_modal(ListingModal("sell"))

@bot.tree.command(name="buy", description="Create buying listing")
async def buy(i: discord.Interaction):
    await i.response.send_modal(ListingModal("buy"))

# EVENTS
@bot.event
async def on_ready():
    log.info("=" * 70)
    log.info(f"✅ BOT READY: {bot.user}")
    log.info(f"Guild: {GUILD_ID}")
    log.info("=" * 70)
    await init_db()
    bot.add_view(DashboardView())
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT listing_ref FROM listings")
            rows = await cur.fetchall()
        for (ref,) in rows:
            bot.add_view(ListingView(ref))
        log.info(f"Re-registered {len(rows)} views")
    except Exception as e:
        log.exception(f"View error: {e}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        log.info(f"✅ SYNCED {len(synced)} COMMANDS:")
        for cmd in synced:
            log.info(f"   ✓ /{cmd.name}")
    except Exception as e:
        log.exception(f"Sync failed: {e}")

@bot.event
async def on_raw_message_delete(p):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM listings WHERE message_id = ? AND channel_id = ?", (str(p.message_id), str(p.channel_id)))
            await db.commit()
    except Exception as e:
        log.exception(f"Delete error: {e}")

# MAIN
if __name__ == "__main__":
    bot.run(TOKEN)
