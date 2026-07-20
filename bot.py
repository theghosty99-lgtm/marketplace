"""
MINIMAL TEST BOT - For diagnosing command sync issues
Run this to identify the exact problem
"""

import os
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None

print(f"\n{'='*60}")
print(f"TEST BOT STARTING")
print(f"TOKEN: {'✅ SET' if TOKEN else '❌ MISSING'}")
print(f"GUILD_ID: {GUILD_ID} {'✅' if GUILD_ID else '❌ MISSING'}")
print(f"{'='*60}\n")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"\n{'='*60}")
    print(f"✅ BOT CONNECTED")
    print(f"Bot Name: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print(f"{'='*60}\n")
    
    # Try to sync commands
    try:
        if GUILD_ID:
            print(f"[SYNC] Attempting to sync to guild {GUILD_ID}...")
            synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print(f"[SYNC] ✅ SUCCESS! Synced {len(synced)} commands:")
            for cmd in synced:
                print(f"       - /{cmd.name}")
        else:
            print("[SYNC] No GUILD_ID set, syncing globally...")
            synced = await bot.tree.sync()
            print(f"[SYNC] ✅ SUCCESS! Synced {len(synced)} global commands")
    except discord.Forbidden as e:
        print(f"[SYNC] ❌ FORBIDDEN ERROR: {e}")
        print(f"       Bot may not have permission to create commands in this guild")
        print(f"       Check: Discord Dev Portal > App > Installation > Scopes > applications.commands")
    except discord.HTTPException as e:
        print(f"[SYNC] ❌ HTTP ERROR: {e}")
    except Exception as e:
        print(f"[SYNC] ❌ UNKNOWN ERROR: {type(e).__name__}: {e}")

@bot.tree.command(name="ping", description="Test if commands work")
async def ping(interaction: discord.Interaction):
    """Simple test command"""
    await interaction.response.send_message(f"🏓 Pong! Bot is working!")

@bot.tree.command(name="test", description="Another test command")
async def test(interaction: discord.Interaction):
    """Another test"""
    await interaction.response.send_message("✅ Test command works!")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: DISCORD_TOKEN not set in .env")
        exit(1)
    if not GUILD_ID:
        print("❌ ERROR: GUILD_ID not set in .env")
        exit(1)
    
    print("[BOT] Starting bot...\n")
    bot.run(TOKEN)
