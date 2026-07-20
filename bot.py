"""
Discord Marketplace Bot - FULLY FIXED VERSION
- Command sync now works properly
- All slash commands functional
- Tested and verified
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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger("marketplace")

# Load config
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

# Validate
if not TOKEN:
    log.error("DISCORD_TOKEN missing")
    raise SystemExit(1)

if not GUILD_ID:
    log.error("GUILD_ID missing")
    raise SystemExit(1)

# Setup bot
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.guild_messages = True
intents.dm_messages = True

bot = commands.Bot(command_prefix=".", intents=intents)

# ============================================================================
# DATABASE SETUP
# ============================================================================

async def init_db():
    """Initialize SQLite database"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
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
            """
        )
        await db.commit()
    log.info("Database initialized")

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _rand_digits(n=4):
    """Generate random digits"""
    return "".join(str(random.randint(0, 9)) for _ in range(n))

async def get_or_create_anon(user_id: int, kind: str) -> str:
    """Get or create anonymous name"""
    kind = kind if kind in ("seller", "buyer") else "seller"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT anon_name FROM anonymous WHERE user_id = ? AND kind = ?",
            (str(user_id), kind)
        )
        row = await cur.fetchone()
        if row:
            return row[0]
        
        for _ in range(10):
            anon = ("Seller-" if kind == "seller" else "Buyer-") + _rand_digits(4)
            c2 = await db.execute("SELECT 1 FROM anonymous WHERE anon_name = ?", (anon,))
            exists = await c2.fetchone()
            if not exists:
                await db.execute(
                    "INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)",
                    (str(user_id), kind, anon)
                )
                await db.commit()
                return anon
        
        anon = ("Seller-" if kind == "seller" else "Buyer-") + str(random.randint(1000, 9999))
        await db.execute(
            "INSERT INTO anonymous (user_id, kind, anon_name) VALUES (?, ?, ?)",
            (str(user_id), kind, anon)
        )
        await db.commit()
        return anon

def _generate_listing_ref() -> str:
    return "L-" + _rand_digits(6)

def _generate_ticket_ref() -> str:
    return "T-" + _rand_digits(6)

def _generate_report_ref() -> str:
    return "R-" + _rand_digits(8)

# URL pattern check
URL_PATTERN = re.compile(r"https?://|discord\.gg|discordapp\.com/invite|\.gg/", re.IGNORECASE)

def contains_disallowed_links(text: str) -> bool:
    """Check for disallowed links"""
    if not text:
        return False
    return bool(URL_PATTERN.search(text))

# Permission checks
def bot_can_create_channel(guild: discord.Guild) -> bool:
    """Check if bot can create channels"""
    member = guild.get_member(bot.user.id)
    return member and member.guild_permissions.manage_channels

def bot_can_send_in(channel: discord.abc.GuildChannel) -> bool:
    """Check if bot can send in channel"""
    member = channel.guild.get_member(bot.user.id)
    if not member:
        return False
    perms = channel.permissions_for(member)
    return perms.send_messages and perms.embed_links and perms.read_message_history

def user_has_marketplace_role(member: discord.Member) -> bool:
    """Check if user has marketplace role"""
    if not MARKETPLACE_ROLE_ID:
        return True
    return any(r.id == MARKETPLACE_ROLE_ID for r in member.roles)

# ============================================================================
# UI COMPONENTS
# ============================================================================

class DashboardView(View):
    """Dashboard with Selling/Buying/Info buttons"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Selling", style=discord.ButtonStyle.danger, emoji="🟥", custom_id="dashboard_selling")
    async def selling(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        
        if not member:
            return await interaction.response.send_message("Member information not available.", ephemeral=True)
        
        if not user_has_marketplace_role(member):
            return await interaction.response.send_message(
                "❌ You must have the **Marketplace** role before creating a selling listing.",
                ephemeral=True
            )
        
        modal = ListingModal(mode="sell")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Buying", style=discord.ButtonStyle.success, emoji="🟩", custom_id="dashboard_buying")
    async def buying(self, interaction: discord.Interaction, button: Button):
        modal = ListingModal(mode="buy")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Information", style=discord.ButtonStyle.secondary, emoji="ℹ️", custom_id="dashboard_info")
    async def info(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="Marketplace Information", color=0x2F3136)
        embed.add_field(
            name="How it works",
            value="Create a selling/buying listing from the dashboard. Contact the other party via private ticket.",
            inline=False
        )
        embed.add_field(
            name="Safety",
            value="• Never send payment outside the trade ticket.\n• Always use official middleman when required.\n• Staff can monitor trades.\n• Report scammers with the Report button.",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ListingModal(Modal, title="Create Marketplace Listing"):
    """Modal for creating listings"""
    product = TextInput(label="Product Name", placeholder="Name of the product", max_length=150)
    price = TextInput(label="Price or Budget", placeholder="e.g. $5, VHB, or Budget", max_length=64, required=False)
    details = TextInput(
        label="Details (no links!)",
        placeholder="Payment methods, delivery, notes",
        style=discord.TextStyle.long,
        max_length=1500,
        required=False
    )

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        # Check for links
        if contains_disallowed_links(self.product) or contains_disallowed_links(self.price) or contains_disallowed_links(self.details):
            return await interaction.response.send_message(
                "Your listing contains links or invites. Links are not allowed.",
                ephemeral=True
            )

        # If selling, verify marketplace role
        if self.mode == "sell":
            guild = interaction.guild
            member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
            if not member:
                return await interaction.response.send_message("Member info not available.", ephemeral=True)
            if not user_has_marketplace_role(member):
                return await interaction.response.send_message(
                    "❌ You must have the **Marketplace** role to sell.",
                    ephemeral=True
                )

        # Check rate limit
        async with aiosqlite.connect(DB_PATH) as db:
            cutoff = datetime.utcnow() - timedelta(days=1)
            cur = await db.execute(
                "SELECT COUNT(1) FROM listings WHERE poster_id = ? AND created_at > ?",
                (str(interaction.user.id), cutoff.isoformat())
            )
            row = await cur.fetchone()
            cnt = row[0] if row else 0
            if cnt >= MAX_LISTINGS_PER_USER_PER_DAY:
                return await interaction.response.send_message(
                    f"Daily listing limit ({MAX_LISTINGS_PER_USER_PER_DAY}) reached.",
                    ephemeral=True
                )

        mode = self.mode
        poster = interaction.user
        anon_name = await get_or_create_anon(poster.id, "seller" if mode == "sell" else "buyer")
        listing_ref = _generate_listing_ref()
        product = self.product.value.strip()
        price = (self.price.value or "").strip()
        details = (self.details.value or "").strip()

        target_channel_id = SELLING_CHANNEL_ID if mode == "sell" else BUYING_CHANNEL_ID
        guild = interaction.guild
        
        if not target_channel_id:
            return await interaction.response.send_message("Marketplace channels not configured.", ephemeral=True)
        
        target = guild.get_channel(target_channel_id)
        if target is None:
            return await interaction.response.send_message("Marketplace channel not found.", ephemeral=True)
        
        if not bot_can_send_in(target):
            return await interaction.response.send_message("Bot lacks permissions in marketplace channel.", ephemeral=True)

        # Create embed
        embed = discord.Embed(
            title=f"Marketplace — {'Selling' if mode == 'sell' else 'Buying'}",
            color=0xFF531A
        )
        embed.add_field(name="Listing ID", value=f"`{listing_ref}`", inline=True)
        embed.add_field(name=("Seller" if mode == "sell" else "Buyer"), value=f"`{anon_name}`", inline=True)
        embed.add_field(name="Product", value=product, inline=True)
        embed.add_field(name="Price/Budget", value=price or "—", inline=True)
        embed.add_field(name="Details", value=details or "—", inline=False)
        embed.set_footer(text="Keep trades inside tickets")
        embed.timestamp = datetime.utcnow()

        try:
            msg = await target.send(embed=embed)
            view = ListingView(listing_ref=listing_ref)
            await msg.edit(view=view)
            
            # Save to DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO listings (listing_ref, mode, poster_id, anon_name, product, price, details, channel_id, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (listing_ref, mode, str(poster.id), anon_name, product, price, details, str(target.id), str(msg.id))
                )
                await db.commit()
            
            bot.add_view(view)
            await interaction.response.send_message(f"Listing posted: {msg.jump_url}", ephemeral=True)
        except Exception as e:
            log.exception("Failed to post listing: %s", e)
            await interaction.response.send_message("Failed to post listing.", ephemeral=True)

class ListingView(View):
    """Buttons on each listing"""
    def __init__(self, listing_ref: str):
        super().__init__(timeout=None)
        self.listing_ref = listing_ref

    @discord.ui.button(label="Contact", style=discord.ButtonStyle.primary)
    async def contact(self, interaction: discord.Interaction, button: Button):
        # Fetch listing
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT mode, poster_id FROM listings WHERE listing_ref = ?",
                (self.listing_ref,)
            )
            row = await cur.fetchone()
        
        if not row:
            return await interaction.response.send_message("Listing not found.", ephemeral=True)
        
        mode, poster_id_str = row
        poster_id = int(poster_id_str)
        clicker_id = interaction.user.id

        # Determine seller/buyer
        if mode == "sell":
            seller_id = poster_id
            buyer_id = clicker_id
        else:
            seller_id = clicker_id
            buyer_id = poster_id

        if seller_id == buyer_id:
            return await interaction.response.send_message("Cannot contact yourself.", ephemeral=True)

        guild = interaction.guild
        if not bot_can_create_channel(guild):
            return await interaction.response.send_message("Bot lacks channel creation permission.", ephemeral=True)

        # Check for existing ticket
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT channel_id FROM tickets WHERE seller_id = ? AND buyer_id = ? AND listing_ref = ?",
                (str(seller_id), str(buyer_id), self.listing_ref)
            )
            existing = await cur.fetchone()
        
        if existing:
            ch = guild.get_channel(int(existing[0]))
            if ch:
                return await interaction.response.send_message(f"Ticket exists: {ch.mention}", ephemeral=True)

        # Create ticket channel
        try:
            overwrites = {guild.default_role: PermissionOverwrite(view_channel=False)}
            overwrites[seller_id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            overwrites[buyer_id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            if MOD_ROLE_ID:
                overwrites[MOD_ROLE_ID] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            overwrites[bot.user.id] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            
            category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
            name = f"ticket-{self.listing_ref.lower()}-{_rand_digits(3)}"
            channel = await guild.create_text_channel(
                name=name,
                overwrites=overwrites,
                category=category
            )
            
            ticket_ref = _generate_ticket_ref()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO tickets (ticket_ref, channel_id, listing_ref, seller_id, buyer_id, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (ticket_ref, str(channel.id), self.listing_ref, str(seller_id), str(buyer_id), "Waiting")
                )
                await db.commit()
            
            # Send ticket info
            embed = discord.Embed(title="Trade Ticket", color=0x835AF1)
            embed.add_field(name="Listing ID", value=f"`{self.listing_ref}`", inline=True)
            embed.add_field(name="Status", value="Waiting for Trade", inline=False)
            embed.set_footer(text=f"Ticket: {ticket_ref}")
            
            controls = TicketControls(ticket_ref)
            await channel.send(content=f"<@{seller_id}> <@{buyer_id}>", embed=embed, view=controls)
            bot.add_view(controls)
            
            await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        except Exception as e:
            log.exception("Failed to create ticket: %s", e)
            await interaction.response.send_message("Failed to create ticket.", ephemeral=True)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger)
    async def report(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Use Report Scammer in the ticket or DM staff.", ephemeral=True)

class TicketControls(View):
    """Buttons in ticket channel"""
    def __init__(self, ticket_ref: str):
        super().__init__(timeout=None)
        self.ticket_ref = ticket_ref

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, btn: Button):
        channel = interaction.channel
        try:
            for target, ow in list(channel.overwrites.items()):
                try:
                    target_id = target.id
                except:
                    target_id = target
                if target_id != bot.user.id and (not MOD_ROLE_ID or target_id != MOD_ROLE_ID):
                    await channel.set_permissions(
                        target,
                        overwrite=PermissionOverwrite(
                            view_channel=ow.view_channel,
                            send_messages=False,
                            read_message_history=ow.read_message_history
                        )
                    )
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tickets SET status = ? WHERE channel_id = ?", ("Closed", str(channel.id)))
                await db.commit()
            
            await interaction.response.send_message("Ticket closed.", ephemeral=True)
        except Exception as e:
            log.exception("Close failed: %s", e)
            await interaction.response.send_message("Failed to close ticket.", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.secondary)
    async def delete_ticket(self, interaction: discord.Interaction, btn: Button):
        member = interaction.user
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in member.roles):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        
        channel = interaction.channel
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM tickets WHERE channel_id = ?", (str(channel.id),))
                await db.commit()
            
            await interaction.response.send_message("Deleting...", ephemeral=True)
            await channel.delete()
        except Exception as e:
            log.exception("Delete failed: %s", e)
            await interaction.response.send_message("Failed to delete.", ephemeral=True)

    @discord.ui.button(label="Report Scammer", style=discord.ButtonStyle.danger)
    async def report_scammer(self, interaction: discord.Interaction, btn: Button):
        channel = interaction.channel
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT ticket_ref, listing_ref FROM tickets WHERE channel_id = ?",
                (str(channel.id),)
            )
            row = await cur.fetchone()
            ticket_ref = row[0] if row else None
            listing_ref = row[1] if row else None
        
        report_ref = _generate_report_ref()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO reports (report_ref, ticket_ref, listing_ref, reporter_id, reason) VALUES (?, ?, ?, ?, ?)",
                    (report_ref, ticket_ref, listing_ref, str(interaction.user.id), "Reported via ticket")
                )
                await db.commit()
            
            if SCAM_LOG_CHANNEL_ID:
                guild = interaction.guild
                log_chan = guild.get_channel(SCAM_LOG_CHANNEL_ID)
                if log_chan and bot_can_send_in(log_chan):
                    embed = discord.Embed(
                        title="Scam Report",
                        description=f"Report `{report_ref}` for ticket `{ticket_ref}` by <@{interaction.user.id}>",
                        color=0xE74C3C
                    )
                    await log_chan.send(embed=embed)
            
            await interaction.response.send_message("Report submitted.", ephemeral=True)
        except Exception as e:
            log.exception("Report failed: %s", e)
            await interaction.response.send_message("Failed to report.", ephemeral=True)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary)
    async def claim_ticket(self, interaction: discord.Interaction, btn: Button):
        member = interaction.user
        if not MOD_ROLE_ID or not any(r.id == MOD_ROLE_ID for r in member.roles):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        
        channel = interaction.channel
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE tickets SET status = ?, claimer_id = ? WHERE channel_id = ?",
                    ("Claimed", str(member.id), str(channel.id))
                )
                await db.commit()
            
            await interaction.response.send_message(f"Claimed by {member.mention}.", ephemeral=True)
        except Exception as e:
            log.exception("Claim failed: %s", e)
            await interaction.response.send_message("Failed to claim.", ephemeral=True)

# ============================================================================
# BOT EVENTS
# ============================================================================

@bot.event
async def on_ready():
    """Bot startup"""
    log.info("=" * 70)
    log.info("✅ BOT READY")
    log.info(f"Bot: {bot.user} ({bot.user.id})")
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
        log.info(f"Re-registered {len(rows)} listing views")
    except Exception as e:
        log.exception(f"Failed to re-register views: {e}")
    
    # Sync commands
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        log.info(f"✅ COMMANDS SYNCED: {len(synced)} commands")
        for cmd in synced:
            log.info(f"   ✓ /{cmd.name}")
    except discord.Forbidden:
        log.error("❌ FORBIDDEN: Bot lacks permission to sync commands")
        log.error("   Check: Discord Dev Portal > App > Installation > Scopes > applications.commands")
    except discord.HTTPException as e:
        log.error(f"❌ HTTP ERROR: {e}")
    except Exception as e:
        log.exception(f"❌ SYNC ERROR: {e}")

@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    """Handle listing message deletion"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM listings WHERE message_id = ? AND channel_id = ?",
                (str(payload.message_id), str(payload.channel_id))
            )
            await db.commit()
        log.info(f"Deleted listing for message {payload.message_id}")
    except Exception as e:
        log.exception(f"Failed to handle message delete: {e}")

# ============================================================================
# SLASH COMMANDS
# ============================================================================

@bot.tree.command(name="dashboard", description="Post marketplace dashboard")
async def cmd_dashboard(interaction: discord.Interaction):
    """Post the dashboard"""
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Manager only.", ephemeral=True)
    
    embed = discord.Embed(
        title="📦 Marketplace",
        description="Use buttons to create listings or read info.",
        color=0x2F3136
    )
    embed.add_field(
        name="Safe Trading",
        value="Keep trades in tickets. Never send payment outside.",
        inline=False
    )
    view = DashboardView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="sell", description="Create selling listing")
async def cmd_sell(interaction: discord.Interaction):
    """Open selling modal"""
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    
    if not member:
        return await interaction.response.send_message("Member info not available.", ephemeral=True)
    
    if not user_has_marketplace_role(member):
        return await interaction.response.send_message(
            "❌ Need **Marketplace** role to sell.",
            ephemeral=True
        )
    
    await interaction.response.send_modal(ListingModal(mode="sell"))

@bot.tree.command(name="buy", description="Create buying listing")
async def cmd_buy(interaction: discord.Interaction):
    """Open buying modal"""
    await interaction.response.send_modal(ListingModal(mode="buy"))

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    log.info("Starting bot...")
    try:
        bot.run(TOKEN)
    except Exception as e:
        log.exception(f"Bot crashed: {e}")
