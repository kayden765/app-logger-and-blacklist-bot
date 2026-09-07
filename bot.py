import discord
from discord.ext import commands
import urllib.request
import json

BOT_TOKEN = "YOUR_DISCORD_BOT_TOKEN"
GUILD_ID = "YOUR_GUILD_ID"
ADMIN_CHANNEL_ID = "YOUR_ADMIN_CHANNEL_ID"
BANNED_LOG_CHANNEL_ID = "YOUR_BANNED_LOG_CHANNEL_ID"
WORKER_URL = "YOUR_CLOUDFLARE_WORKER_URL"
ADMIN_TOKEN = "YOUR_ADMIN_TOKEN"
AUTHORIZED_USER_IDS = ["YOUR_DISCORD_USER_ID"]

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command()
async def ban(ctx, hwid: str):
    if str(ctx.author.id) not in AUTHORIZED_USER_IDS:
        await ctx.send("❌ You are not authorized to use this command.")
        return
    if not hwid:
        await ctx.send("Usage: !ban <hwid>")
        return

    result = send_admin_command("ban", hwid)
    if result and result.get("status") == "banned":
        await ctx.send(f"✅ Banned: `{hwid}`")
    else:
        await ctx.send(f"❌ Failed to ban: {result}")


@bot.command()
async def unban(ctx, hwid: str):
    if str(ctx.author.id) not in AUTHORIZED_USER_IDS:
        await ctx.send("❌ You are not authorized to use this command.")
        return
    if not hwid:
        await ctx.send("Usage: !unban <hwid>")
        return

    result = send_admin_command("unban", hwid)
    if result and result.get("status") == "unbanned":
        await ctx.send(f"✅ Unbanned: `{hwid}`")
    else:
        await ctx.send(f"❌ Failed to unban: {result}")


@bot.command()
async def banned(ctx):
    if str(ctx.author.id) not in AUTHORIZED_USER_IDS:
        await ctx.send("❌ You are not authorized to use this command.")
        return

    result = send_admin_command("list", "")
    if result and isinstance(result, dict):
        banned_list = result.get("banned", [])
        if not banned_list:
            await ctx.send("No banned machines.")
        else:
            lines = [f"• `{entry.get('uuid')}` — {entry.get('reason', 'N/A')}" for entry in banned_list]
            await ctx.send("**Banned machines:**\n" + "\n".join(lines))
    else:
        await ctx.send("❌ Failed to fetch banned list.")


@bot.command()
async def whitelist(ctx, hwid: str):
    if str(ctx.author.id) not in AUTHORIZED_USER_IDS:
        await ctx.send("❌ You are not authorized to use this command.")
        return
    if not hwid:
        await ctx.send("Usage: !whitelist <hwid>")
        return

    result = send_admin_command("whitelist", hwid)
    if result and result.get("status") == "whitelisted":
        await ctx.send(f"✅ Whitelisted: `{hwid}`")
    else:
        await ctx.send(f"❌ Failed to whitelist: {result}")


@bot.command()
async def unwhitelist(ctx, hwid: str):
    if str(ctx.author.id) not in AUTHORIZED_USER_IDS:
        await ctx.send("❌ You are not authorized to use this command.")
        return
    if not hwid:
        await ctx.send("Usage: !unwhitelist <hwid>")
        return

    result = send_admin_command("unwhitelist", hwid)
    if result and result.get("status") == "unwhitelisted":
        await ctx.send(f"✅ Unwhitelisted: `{hwid}`")
    else:
        await ctx.send(f"❌ Failed to unwhitelist: {result}")


@bot.command()
async def whitelisted(ctx):
    if str(ctx.author.id) not in AUTHORIZED_USER_IDS:
        await ctx.send("❌ You are not authorized to use this command.")
        return

    result = send_admin_command("list_whitelist", "")
    if result and isinstance(result, dict):
        whitelisted_list = result.get("whitelisted", [])
        if not whitelisted_list:
            await ctx.send("No whitelisted machines.")
        else:
            lines = [f"• `{entry.get('uuid')}`" for entry in whitelisted_list]
            await ctx.send("**Whitelisted machines:**\n" + "\n".join(lines))
    else:
        await ctx.send("❌ Failed to fetch whitelisted list.")


def send_admin_command(command, machine_uuid, reason=""):
    payload = {
        "command": command,
        "machine_uuid": machine_uuid,
        "reason": reason,
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            WORKER_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Admin-Token": ADMIN_TOKEN,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        print(f"Admin command failed: HTTP {e.code}: {body}")
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        print(f"Admin command failed: {e}")
        return {"error": str(e)}


bot.run(BOT_TOKEN)
