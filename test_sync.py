import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None

print(f"\nDIAGNOSTIC")
print(f"Token: {'SET' if TOKEN else 'MISSING'}")
print(f"Guild: {GUILD_ID}\n")

intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Define commands
@bot.tree.command(name="test1", description="Test 1")
async def test1(i: discord.Interaction):
    await i.response.send_message("Test 1")

@bot.tree.command(name="test2", description="Test 2")
async def test2(i: discord.Interaction):
    await i.response.send_message("Test 2")

@bot.event
async def on_ready():
    print(f"✅ Connected: {bot.user}\n")
    print(f"Commands in tree: {len(bot.tree._get_all_commands())}")
    for cmd in bot.tree._get_all_commands():
        print(f"  - {cmd.name}")
    
    print(f"\nSyncing to {GUILD_ID}...")
    try:
        result = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"✅ Synced: {len(result)}")
        for cmd in result:
            print(f"  ✓ {cmd.name}")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")

bot.run(TOKEN)
