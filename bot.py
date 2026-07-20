#!/usr/bin/env python3
"""
Discord Marketplace Bot - COMPLETE WORKING VERSION
Production-ready with all features
"""

import os
import sys
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

# ============================================================================
# SETUP
# ============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger("marketplace")

# Load environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
SELLING_CHANNEL_ID = int(os.getenv("SELLING_CHANNEL_ID")) if os.getenv("SELLING_CHANNEL_ID") else None
BUYING_CHANNEL_ID = int(os.getenv("BUYING_CHANNEL_ID")) if os.getenv("BUYING_CHANNEL_ID") else None
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID")) if os.getenv("DASHBOARD_CHANNEL_ID") else None
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID")) if os.getenv("TICKET_CATEGORY_ID") else None
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID")) if os.getenv("MOD_ROLE_ID") else None
SCAM_LOG_CHANNEL_ID = int(os.getenv("SCAM_LOG_CHANNEL_ID")) if os.getenv("SCAM_LOG_CHANNEL_ID") else None
DB_PATH = os.getenv("DB_PATH", "marketplace.db")
MAX_LISTINGS_PER_USER_PER_DAY = int(os.getenv("MAX_LISTINGS_PER_USER_PER_DAY", "5"))
MARKETPLACE_ROLE_ID = int(os.getenv("MARKETPLACE_ROLE_ID")) if os.getenv("MARKETPLACE_ROLE_ID") else None

# Validate config
if not TOKEN:
    log.error("❌ DISCORD_TOKEN not set")
    sys.exit(1)
if not GUILD_ID:
    log.error("❌ GUILD_ID not set")
    sys.exit(1)

log.info("Config loaded:")
log.info(f"  Guild ID: {GUILD_ID}")
log.info(f"  Selling Channel: {SELLING_CHANNEL_ID}")
log.info(f"  Buying Channel: {BUYING_CHANNEL_ID}")

# Create bot with intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)

# ============================================================================
# DATABASE
# ============================================================================

async def init_db():
    """Initialize SQLite database"""
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
    log.info("✅ Database initialized")

# ============================================================================
# HELPERS
# ============================================================================

def _rand_digits(n=4):
    """Generate random digits"""
    return "".join(str(random.randint(0, 9)) for _ in range(n))

async def get_or_create_anon(user_id: int, kind: str) -> str:
    """Get or create anonymous seller/buyer name"""
    kind = "seller" if kind == "seller" else "buyer"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT anon_name FROM anonymous WHERE user_id = ? AND kind = ?",
            (str(user_id), kind)
        )
        row = await cur.fetchone()
        if row:
            return row[0]
        
        # Try to create unique name
        for _ in range(10):
            anon = f"{'Seller' if kind == 'seller' else 'Buyer'}-{_rand_digits(4)}"
            c = await db.execute("SELECT 1 FROM anonymous WHERE anon_name = ?", (anon,))
            if not await c.fetchone():
                await db.execute(
                    "INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)",
                    (str(user_id), kind, anon)
                )
                await db.commit()
                return anon
        
        # Fallback
        anon = f"{'Seller' if kind == 'seller' else 'Buyer'}-{random.randint(1000, 9999)}"
        await db.execute(
            "INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)",
            (str(user_id), kind, anon)
        )
        await db.commit()
        return anon

def _gen_listing_ref():
    return f"L-{_rand_digits(6)}"

def _gen_ticket_ref():
    return f"T-{_rand_digits(6)}"

def _gen_report_ref():
    return f"R-{_rand_digits(8)}"

# URL check
URL_PATTERN = re.compile(r"https?://|discord\.gg|\.gg/", re.IGNORECASE)

def has_links(text):
    """Check if text contains disallowed links"""
    return bool(URL_PATTERN.search(text or ""))

def bot_can_create_channel(guild):
    """Check if bot can create channels"""
    m = guild.get_member(bot.user.id)
    return m and m.guild_permissions.manage_channels

def bot_can_send(channel):
    """Check if bot can send messages in channel"""
    m = channel.guild.get_member(bot.user.id)
    if not m:
        return False
    p = channel.permissions_for(m)
    return p.send_messages and p.embed_links

def has_marketplace_role(member):
    """Check if member has marketplace role"""
    if not MARKETPLACE_ROLE_ID:
        return True
    return any(r.id == MARKETPLACE_ROLE_ID for r in member.roles)

# ============================================================================
# UI COMPONENTS
# ============================================================================

class DashboardView(View):
    """Main dashboard with Sell/Buy/Info buttons"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Selling", style=discord.ButtonStyle.danger, emoji="🟥", custom_id="dashboard_selling")
    async def selling(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        
        if not member:
            return await interaction.response.send_message("Member info unavailable", ephemeral=True)
        
        if not has_marketplace_role(member):
            return await interaction.response.send_message(
                "❌ You need the **Marketplace** role to create selling listings.",
                ephemeral=True
            )
        
        await interaction.response.send_modal(ListingModal("sell"))

    @discord.ui.button(label="Buying", style=discord.ButtonStyle.success, emoji="🟩", custom_id="dashboard_buying")
    async def buying(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ListingModal("buy"))

    @discord.ui.button(label="Information", style=discord.ButtonStyle.secondary, emoji="ℹ️", custom_id="dashboard_info")
    async def info(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="Marketplace Information", color=0x2F3136)
        embed.add_field(
            name="How it works",
            value="Create a listing, contact the other party via ticket, complete the trade safely.",
            inline=False
        )
        embed.add_field(
            name="Safety Guidelines",
            value="• Keep all trades in private tickets\n• Never send payment outside tickets\n• Use official middleman when needed\n• Report scammers immediately",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ListingModal(Modal, title="Create Listing"):
    """Modal for creating buy/sell listings"""
    product = TextInput(label="Product Name", placeholder="What are you selling/buying?", max_length=150)
    price = TextInput(label="Price or Budget", placeholder="e.g., $5, VHB, or Budget", max_length=64, required=False)
    details = TextInput(
        label="Details (no links!)",
        placeholder="Payment methods, delivery info, other details",
        style=discord.TextStyle.long,
        max_length=1500,
        required=False
    )

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        # Check for links
        if has_links(self.product.value) or has_links(self.price.value) or has_links(self.details.value):
            return await interaction.response.send_message(
                "❌ Your listing contains links or invites. Links are not allowed in public listings.",
                ephemeral=True
            )

        # Check marketplace role for sellers
        if self.mode == "sell":
            guild = interaction.guild
            member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
            if not member:
                return await interaction.response.send_message("Member info unavailable", ephemeral=True)
            if not has_marketplace_role(member):
                return await interaction.response.send_message(
                    "❌ You need the **Marketplace** role to create selling listings.",
                    ephemeral=True
                )

        # Check rate limit
        async with aiosqlite.connect(DB_PATH) as db:
            cutoff = datetime.utcnow() - timedelta(days=1)
            cur = await db.execute(
                "SELECT COUNT(*) FROM listings WHERE poster_id = ? AND created_at > ?",
                (str(interaction.user.id), cutoff.isoformat())
            )
            row = await cur.fetchone()
            cnt = row[0] if row else 0
            if cnt >= MAX_LISTINGS_PER_USER_PER_DAY:
                return await interaction.response.send_message(
                    f"❌ Daily limit reached: {MAX_LISTINGS_PER_USER_PER_DAY} listings per day",
                    ephemeral=True
                )

        # Get channel
        ch_id = SELLING_CHANNEL_ID if self.mode == "sell" else BUYING_CHANNEL_ID
        if not ch_id:
            return await interaction.response.send_message("❌ Marketplace channel not configured", ephemeral=True)
        
        ch = interaction.guild.get_channel(ch_id)
        if not ch or not bot_can_send(ch):
            return await interaction.response.send_message("❌ Bot cannot send in marketplace channel", ephemeral=True)

        # Create listing
        ref = _gen_listing_ref()
        anon = await get_or_create_anon(interaction.user.id, self.mode)
        
        embed = discord.Embed(
            title=f"Marketplace — {'SELLING' if self.mode == 'sell' else 'BUYING'}",
            color=0xFF531A
        )
        embed.add_field(name="Listing ID", value=f"`{ref}`", inline=True)
        embed.add_field(name=("Seller" if self.mode == "sell" else "Buyer"), value=f"`{anon}`", inline=True)
        embed.add_field(name="Product", value=self.product.value, inline=True)
        embed.add_field(name="Price/Budget", value=self.price.value or "—", inline=True)
        embed.add_field(name="Details", value=self.details.value or "—", inline=False)
        embed.timestamp = datetime.utcnow()

        try:
            msg = await ch.send(embed=embed)
            view = ListingView(ref)
            await msg.edit(view=view)
            
            # Save to DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO listings (listing_ref, mode, poster_id, anon_name, product, price, details, channel_id, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ref, self.mode, str(interaction.user.id), anon, self.product.value, self.price.value or "", self.details.value or "", str(ch.id), str(msg.id))
                )
                await db.commit()
            
            bot.add_view(view)
            await interaction.response.send_message(f"✅ Your listing has been posted: {msg.jump_url}", ephemeral=True)
        except Exception as e:
            log.exception(f"Failed to post listing: {e}")
            await interaction.response.send_message("❌ Failed to post listing (internal error)", ephemeral=True)

class ListingView(View):
    """Buttons on listing: Contact, Report"""
    def __init__(self, listing_ref: str):
        super().__init__(timeout=None)
        self.listing_ref = listing_ref

    @discord.ui.button(label="Contact", style=discord.ButtonStyle.primary)
    async def contact(self, interaction: discord.Interaction, button: Button):
        # Get listing info
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT mode, poster_id FROM listings WHERE listing_ref = ?",
                (self.listing_ref,)
            )
            row = await cur.fetchone()
        
        if not row:
            return await interaction.response.send_message("❌ Listing not found or was deleted", ephemeral=True)
        
        mode, poster_id = row[0], int(row[1])
        clicker_id = interaction.user.id
        
        # Determine seller/buyer
        if mode == "sell":
            seller_id = poster_id
            buyer_id = clicker_id
        else:
            seller_id = clicker_id
            buyer_id = poster_id
        
        if seller_id == buyer_id:
            return await interaction.response.send_message("❌ You cannot contact yourself", ephemeral=True)

        if not bot_can_create_channel(interaction.guild):
            return await interaction.response.send_message("❌ Bot lacks channel creation permission", ephemeral=True)

        # Check for existing ticket
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT channel_id FROM tickets WHERE seller_id = ? AND buyer_id = ? AND listing_ref = ?",
                (str(seller_id), str(buyer_id), self.listing_ref)
            )
            existing = await cur.fetchone()
        
        if existing:
            ch = interaction.guild.get_channel(int(existing[0]))
            if ch:
                return await interaction.response.send_message(f"✅ A ticket already exists: {ch.mention}", ephemeral=True)

        # Create ticket
        try:
            overwrites = {interaction.guild.default_role: PermissionOverwrite(view_channel=False)}
            overwrites[seller_id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            overwrites[buyer_id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            if MOD_ROLE_ID:
                overwrites[MOD_ROLE_ID] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            overwrites[bot.user.id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            
            category = interaction.guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
            name = f"ticket-{self.listing_ref.lower()}-{_rand_digits(3)}"
            channel = await interaction.guild.create_text_channel(
                name=name,
                overwrites=overwrites,
                category=category
            )
            
            ticket_ref = _gen_ticket_ref()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO tickets (ticket_ref, channel_id, listing_ref, seller_id, buyer_id, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (ticket_ref, str(channel.id), self.listing_ref, str(seller_id), str(buyer_id), "Waiting")
                )
                await db.commit()
            
            embed = discord.Embed(title="Trade Ticket", color=0x835AF1)
            embed.add_field(name="Listing ID", value=f"`{self.listing_ref}`", inline=True)
            embed.add_field(name="Status", value="Waiting for Trade", inline=False)
            embed.set_footer(text=f"Ticket: {ticket_ref}")
            
            controls = TicketControls(ticket_ref)
            await channel.send(content=f"<@{seller_id}> <@{buyer_id}>", embed=embed, view=controls)
            bot.add_view(controls)
            
            await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
        except Exception as e:
            log.exception(f"Failed to create ticket: {e}")
            await interaction.response.send_message("❌ Failed to create ticket", ephemeral=True)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger)
    async def report(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "To report this listing, open the Contact button to create a ticket, then use Report Scammer inside the ticket.",
            ephemeral=True
        )

class TicketControls(View):
    """Buttons in ticket: Close, Delete, Report, Claim"""
    def __init__(self, ticket_ref: str):
        super().__init__(timeout=None)
        self.ticket_ref = ticket_ref

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        try:
            for target in list(interaction.channel.overwrites.keys()):
                tid = target.id if hasattr(target, 'id') else target
                if tid != bot.user.id and (not MOD_ROLE_ID or tid != MOD_ROLE_ID):
                    await interaction.channel.set_permissions(target, send_messages=False)
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ? WHERE channel_id = ?", ("Closed", str(interaction.channel.id)))
                await db.commit()
            
            await interaction.response.send_message("✅ Ticket closed and set to read-only", ephemeral=True)
        except Exception as e:
            log.exception(f"Close failed: {e}")

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.secondary)
    async def delete_ticket(self, interaction: discord.Interaction, button: Button):
        member = interaction.user
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in member.roles):
            return await interaction.response.send_message("❌ Only staff can delete tickets", ephemeral=True)
        
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM tickets WHERE channel_id = ?", (str(interaction.channel.id),))
                await db.commit()
            
            await interaction.response.send_message("Deleting ticket...", ephemeral=True)
            await interaction.channel.delete()
        except Exception as e:
            log.exception(f"Delete failed: {e}")

    @discord.ui.button(label="Report Scammer", style=discord.ButtonStyle.danger)
    async def report_scammer(self, interaction: discord.Interaction, button: Button):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT ticket_ref, listing_ref FROM tickets WHERE channel_id = ?", (str(interaction.channel.id),))
            row = await cur.fetchone()
            ticket_ref = row[0] if row else None
            listing_ref = row[1] if row else None
        
        report_ref = _gen_report_ref()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO reports (report_ref, ticket_ref, listing_ref, reporter_id, reason) VALUES (?, ?, ?, ?, ?)",
                    (report_ref, ticket_ref, listing_ref, str(interaction.user.id), "Reported via ticket button")
                )
                await db.commit()
            
            if SCAM_LOG_CHANNEL_ID:
                guild = interaction.guild
                log_chan = guild.get_channel(SCAM_LOG_CHANNEL_ID)
                if log_chan and bot_can_send(log_chan):
                    embed = discord.Embed(
                        title="Scam Report",
                        description=f"Report `{report_ref}` for ticket `{ticket_ref}` reported by <@{interaction.user.id}>",
                        color=0xE74C3C
                    )
                    await log_chan.send(embed=embed)
            
            await interaction.response.send_message("✅ Report submitted to staff. They will review.", ephemeral=True)
        except Exception as e:
            log.exception(f"Report failed: {e}")

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary)
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        member = interaction.user
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in member.roles):
            return await interaction.response.send_message("❌ Only staff can claim tickets", ephemeral=True)
        
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ?, claimer_id = ? WHERE channel_id = ?", ("Claimed", str(member.id), str(interaction.channel.id)))
                await db.commit()
            
            await interaction.response.send_message(f"✅ Ticket claimed by {member.mention}", ephemeral=True)
        except Exception as e:
            log.exception(f"Claim failed: {e}")

# ============================================================================
# COMMANDS - MUST BE DEFINED BEFORE on_ready
# ============================================================================

@bot.tree.command(name="dashboard", description="Post the marketplace dashboard")
async def cmd_dashboard(interaction: discord.Interaction):
    """Post marketplace dashboard with buttons"""
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ You must be a server manager", ephemeral=True)
    
    embed = discord.Embed(
        title="📦 Marketplace",
        description="Welcome to the Marketplace! Use the buttons to create listings or read the rules.",
        color=0x2F3136
    )
    embed.add_field(
        name="Keep Trades Safe",
        value="Keep all trades inside private tickets. Never send payment outside the ticket. Use official middleman when required.",
        inline=False
    )
    view = DashboardView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="sell", description="Create a selling listing")
async def cmd_sell(interaction: discord.Interaction):
    """Open selling modal"""
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    
    if not member:
        return await interaction.response.send_message("❌ Member info not available", ephemeral=True)
    
    if not has_marketplace_role(member):
        return await interaction.response.send_message(
            "❌ You must have the **Marketplace** role before creating selling listings.",
            ephemeral=True
        )
    
    await interaction.response.send_modal(ListingModal(mode="sell"))

@bot.tree.command(name="buy", description="Create a buying listing")
async def cmd_buy(interaction: discord.Interaction):
    """Open buying modal"""
    await interaction.response.send_modal(ListingModal(mode="buy"))

# ============================================================================
# BOT EVENTS
# ============================================================================

@bot.event
async def on_ready():
    """Called when bot connects to Discord"""
    log.info("=" * 70)
    log.info(f"✅ BOT READY: {bot.user}")
    log.info(f"Bot ID: {bot.user.id}")
    log.info(f"Guild ID: {GUILD_ID}")
    log.info("=" * 70)
    
    # Initialize database
    await init_db()
    
    # Add persistent views
    bot.add_view(DashboardView())
    
    # Re-register listing views from database
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT listing_ref FROM listings")
            rows = await cur.fetchall()
        for (listing_ref,) in rows:
            view = ListingView(listing_ref=listing_ref)
            bot.add_view(view)
        log.info(f"✅ Re-registered {len(rows)} listing views")
    except Exception as e:
        log.exception(f"Failed to re-register views: {e}")
    
# Sync commands to guild
try:
    guild = discord.Object(id=GUILD_ID)

    # Copy global commands to this guild
    bot.tree.copy_global_to(guild=guild)

    # Show registered commands before syncing
    log.info("=== REGISTERED COMMANDS ===")
    for cmd in bot.tree.get_commands():
        log.info(f"Found command: {cmd.name}")
    log.info("===========================")

    # Sync commands
    synced = await bot.tree.sync(guild=guild)

    log.info(f"✅ SYNCED {len(synced)} COMMANDS TO GUILD:")
    for cmd in synced:
        log.info(f"   ✓ /{cmd.name}")

except discord.Forbidden:
    log.error("❌ FORBIDDEN - Bot lacks permission to sync commands")
    log.error("   Fix: Discord Dev Portal > Installation > Scopes > applications.commands")

except discord.HTTPException as e:
    log.error(f"❌ HTTP ERROR: {e}")

except Exception as e:
    log.exception(f"❌ COMMAND SYNC FAILED: {e}")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    log.info("Starting bot...")
    try:
        bot.run(TOKEN)
    except Exception as e:
        log.exception(f"Bot crashed: {e}")
        sys.exit(1)
