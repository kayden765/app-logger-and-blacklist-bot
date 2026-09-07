# Logging Main

Complete setup: Cloudflare Worker telemetry backend + Discord admin bot + Python logger module.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Part 1 — Discord Webhooks](#part-1--discord-webhooks)
4. [Part 2 — Discord Bot](#part-2--discord-bot)
5. [Part 3 — Cloudflare Worker](#part-3--cloudflare-worker)
6. [Part 4 — Python Telemetry Logger](#part-4--python-telemetry-logger)
7. [Part 5 — Test Everything](#part-5--test-everything)
8. [Troubleshooting](#troubleshooting)
9. [Security Notes](#security-notes)

---

## Overview

This system has three parts:

| Component | What it does |
|-----------|-------------|
| **Cloudflare Worker** (`worker.js`) | Receives telemetry from your Python app. Checks ban/whitelist KV namespaces. Posts rich Discord embeds to your webhooks. |
| **Discord Bot** (`bot.py`) | Runs in your Discord server. Lets authorized admins use `!ban`, `!unban`, `!whitelist`, etc. from chat. |
| **Python Logger** (`telemetry_logger.py`) | A single class you import into any Python app. Asks the user for consent, then sends only machine ID, public IP, local IP, and desktop name. If the user declines, only the machine ID is sent for access checks. |

**Data flow:**

```
Your Python App
    |
    |  User consent prompt:
    |  "Share public IP, local IP, desktop name?"
    |       |
    |   Yes -> GET /?id=<machine_uuid>&data={"public_ip":...,"local_ip":...,"hostname":...}
    |    No -> GET /?id=<machine_uuid>&data={}
    v
Cloudflare Worker
    |-- Machine banned?      -> Post to ban webhook -> return {"banned": true}
    |-- Machine whitelisted? -> skip logging        -> return {"banned": false}
    |-- New machine + data?  -> Post rich embed     -> return {"banned": false}
    |-- New machine + no data? -> return banned:false (no webhook post)
    v
Discord (your server channels)

Discord Admin uses !ban <hwid>
    |
    v
bot.py -> POST /admin (with X-Admin-Token)
    |
    v
Cloudflare Worker -> BLACKLIST_KV.put / delete / list
```

---

## Prerequisites

Before starting, make sure you have these installed:

- **Python 3.9+** — https://www.python.org/downloads/ (check "Add Python to PATH" on Windows)
- **Node.js 18+** — https://nodejs.org/ (includes npm)
- **A Discord account** — https://discord.com/
- **A Cloudflare account** — https://dash.cloudflare.com/sign-up (free tier is fine)

To verify installs:

```bash
python --version
node --version
npm --version
```

---

## Part 1 — Discord Webhooks

Webhooks are URLs Discord gives you that let external services post messages into your server channels. You need **three** webhooks.

### Step 1.1 — Open Server Settings

1. Open **Discord** (desktop app or web browser).
2. Find your server in the left sidebar.
3. Click the **server name** at the top-left of the screen (next to the server icon).
4. A dropdown appears — click **Server Settings**.

### Step 1.2 — Go to Integrations

1. In the left menu of Server Settings, click **Integrations**.
2. Scroll down and click **Webhooks**.
3. Click **New Webhook**.

### Step 1.3 — Create the Main Telemetry Webhook

1. **Channel:** Choose the channel where you want general telemetry logs to appear (e.g., a channel named `#telemetry` or `#logs`).
2. **Name:** Change the webhook name to something like `Telemetry Logger`.
3. Click **Copy Webhook URL** — paste it somewhere safe temporarily (Notepad).
4. Click **Save Changes**.

### Step 1.4 — Create the Ban Webhook

1. In the same Webhooks page, click **New Webhook** again.
2. **Channel:** Choose a channel for banned-user alerts (e.g., `#banned`).
3. **Name:** `Ban Alert`.
4. Click **Copy Webhook URL** — save it.
5. Click **Save Changes**.

### Step 1.5 — Create the Whitelist Webhook

1. Click **New Webhook** again.
2. **Channel:** Choose a channel for whitelist notifications (e.g., `#whitelist`).
3. **Name:** `Whitelist Alert`.
4. Click **Copy Webhook URL** — save it.
5. Click **Save Changes**.

You should now have three webhook URLs that look like:

```
https://discord.com/api/webhooks/000000000000000000/XXXXXXXXXX_XXXXXXXXXX_XXXXXXXXXX
```

Keep all three URLs handy — you will paste them into the Worker secrets and the bot `.env` file.

---

## Part 2 — Discord Bot

The bot runs in your server and translates chat commands like `!ban` into API calls to the Worker.

### Step 2.1 — Create the Application

1. Go to https://discord.com/developers/applications
2. Click **New Application** (top-right).
3. Enter a name (e.g., `Telemetry Admin Bot`) and click **Create**.
4. You are now on the **General Information** page. Copy the **Application ID** and save it somewhere.
5. On the left sidebar, click **Bot**.
6. Click **Add Bot** — confirm with **Yes, do it!**.

### Step 2.2 — Enable Message Content Intent

1. On the **Bot** page, scroll down to **Privileged Gateway Intents**.
2. Toggle **Message Content Intent** to **ON**.
3. Scroll to the top of the Bot page.
4. Click **Reset Token** (or **Copy** if you haven't copied it yet).
5. **Copy the token and save it.** You will not see this token again after closing the page.
   - ⚠️ If you lose it, you must click **Reset Token** again.

### Step 2.3 — Invite the Bot to Your Server

1. On the left sidebar, click **OAuth2** → **URL Generator**.
2. In the **Scopes** section, check:
   - `bot`
   - `applications.commands`
3. Below the scopes, in **Bot Permissions**, check:
   - `Send Messages`
   - `Read Message History`
   - `Use Slash Commands` (optional but recommended)
4. At the bottom of the page, a **Generated URL** appears. Copy it.
5. Paste the URL into your browser's address bar and press Enter.
6. Select your server from the dropdown and click **Authorize**.
7. Complete the CAPTCHA if prompted.

The bot should now appear in your server's member list (it will show as offline until you run it).

### Step 2.4 — Get Your Discord User ID

You need your own Discord user ID to authorize yourself as an admin.

**Option A — Developer Mode (quickest):**
1. Open Discord settings → **Advanced** → enable **Developer Mode**.
2. Right-click your own username in the member list.
3. Click **Copy User ID**.

**Option B — If right-click doesn't work:**
1. Enable Developer Mode as above.
2. Right-click your profile picture anywhere.
3. Click **Copy User ID**.

Save this number — it looks like `123456789012345678`.

### Step 2.5 — Get Guild and Channel IDs

- **Guild ID (Server ID):** Right-click your server icon in the left sidebar → **Copy Server ID**.
- **Admin Channel ID:** Right-click the channel where you want the bot to send its log messages → **Copy ID**.
- **Banned Log Channel ID:** Right-click the channel for banned-user alerts → **Copy ID**.

---

## Part 3 — Cloudflare Worker

The Worker is the central brain. It receives telemetry, checks ban/whitelist status, and posts to Discord.

### Step 3.1 — Install Wrangler

```bash
npm install -g wrangler
```

Verify:

```bash
wrangler --version
```

### Step 3.2 — Log In

```bash
wrangler login
```

A browser window opens. Log in to your Cloudflare account and authorize Wrangler.

### Step 3.3 — Create KV Namespaces

KV (Key-Value) is Cloudflare's edge storage. You need two namespaces: one for bans, one for whitelists.

Run these commands **one at a time**:

```bash
wrangler kv namespace create "BLACKLIST_KV"
```

Output will look like:

```
{ binding = "BLACKLIST_KV", id = "YOUR_BLACKLIST_KV_ID" }
```

Copy the `id` value. Then create the preview namespace (for local testing):

```bash
wrangler kv namespace create "BLACKLIST_KV" --preview
```

Output will look like:

```
{ binding = "BLACKLIST_KV", preview_id = "YOUR_BLACKLIST_KV_PREVIEW_ID" }
```

Copy the `preview_id`. Now repeat for the whitelist:

```bash
wrangler kv namespace create "WHITELIST_KV"
wrangler kv namespace create "WHITELIST_KV" --preview
```

You should now have **four** IDs total:
- `BLACKLIST_KV` id
- `BLACKLIST_KV` preview_id
- `WHITELIST_KV` id
- `WHITELIST_KV` preview_id

### Step 3.4 — Update wrangler.toml

Open `wrangler.toml` and replace the placeholder IDs with the real ones you just got:

```toml
name = "telemetry-worker"
main = "worker.js"
compatibility_date = "2024-01-01"

[[kv_namespaces]]
binding = "BLACKLIST_KV"
id = "YOUR_BLACKLIST_KV_ID"              # <-- replace with your real BLACKLIST id
preview_id = "YOUR_BLACKLIST_KV_PREVIEW_ID"   # <-- replace with your real BLACKLIST preview_id

[[kv_namespaces]]
binding = "WHITELIST_KV"
id = "YOUR_WHITELIST_KV_ID"                       # <-- replace with your real WHITELIST id
preview_id = "YOUR_WHITELIST_KV_PREVIEW_ID"        # <-- replace with your real WHITELIST preview_id
```

### Step 3.5 — Set Worker Secrets

Secrets are encrypted environment variables stored on Cloudflare's servers. The Worker reads them via `env.VARIABLE_NAME`.

From the `logging-main` folder, run:

```bash
wrangler secret put DISCORD_WEBHOOK_URL
```

It will prompt: `Enter the secret value:` — paste your **main telemetry webhook URL** and press Enter.

Repeat for the other two webhooks:

```bash
wrangler secret put DISCORD_BAN_WEBHOOK_URL
# Paste your ban webhook URL

wrangler secret put DISCORD_WHITELIST_WEBHOOK_URL
# Paste your whitelist webhook URL
```

Now set the admin token (choose a strong random string — this protects the `/admin` endpoint):

```bash
wrangler secret put ADMIN_TOKEN
# Paste something like: aB3$xY9!mK2@pL7
# Do NOT use your Discord bot token here. Make up a new random string.
```

### Step 3.6 — Deploy

```bash
wrangler deploy
```

You should see output like:

```
Uploaded telemetry-worker (X.XX sec)
Published telemetry-worker (X.XX sec)
  https://telemetry-worker.YOUR_SUBDOMAIN.workers.dev
```

Copy your Worker URL — you will need it for the bot `.env` and the Python logger.

---

## Part 4 — Python Telemetry Logger

This is the module you drop into your own Python app.

### Step 4.1 — Install Dependencies

From the `logging-main` folder:

```bash
pip install -r requirements.txt
```

This installs:
- `discord.py` — for the bot
- `psutil` — for system/network telemetry collection
- `colorama` — for colored terminal output in the bot

### Step 4.2 — Where to Paste the Logging Code

Open **your own Python app** (the one you want to add logging to). You need to do two things:

#### A. Import the module

At the **top** of your main script, add:

```python
from telemetry_logger import TelemetryLogger
```

If your script is in a different folder than `logging-main`, add the path:

```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "path", "to", "logging-main"))

from telemetry_logger import TelemetryLogger
```

Or install it as a package by copying `telemetry_logger.py` into the same folder as your script.

#### B. Initialize and send telemetry

Find the place in your code where the app **starts up** (e.g., right after login, right before showing the main menu, or in `main()`). Paste the following:

```python
# --- Telemetry Logger Setup ---
TELEMETRY_WORKER_URL = "https://telemetry-worker.YOUR_SUBDOMAIN.workers.dev"
TELEMETRY_ADMIN_TOKEN = "the_same_random_string_you_set_in_wrangler_secret"

telemetry = TelemetryLogger(
    worker_url=TELEMETRY_WORKER_URL,
    admin_token=TELEMETRY_ADMIN_TOKEN,
    app_name="YourAppName",
    app_version="1.0.0",
    enabled=True,
)

# Get the current username / machine identifier to send
current_username = "player"  # replace with your actual username variable

# Send telemetry and check if this machine is banned
# The user will be prompted to opt in or out of data sharing
resp = telemetry.send_telemetry(username=current_username)

if resp and isinstance(resp, dict):
    if resp.get("banned") is True:
        print(f"\n[ACCESS DENIED] {resp.get('reason', 'You have been banned.')}")
        input("Press Enter to exit...")
        sys.exit(1)
    else:
        print("[+] Access granted.")
else:
    print("[!] Could not reach telemetry server. Continuing anyway.")
```

When `send_telemetry()` runs, the user will see a prompt like:

```
[Telemetry] This app sends a one-time check to verify access.
[Telemetry] Machine ID : 123456789012345
[Telemetry] Public IP  : 203.0.113.45
[Telemetry] Local IP   : 192.168.1.7
[Telemetry] Desktop    : DESKTOP-ABC123
[Telemetry] Do you want to share these details? [Y/n]:
```

- If they type **Y** or press Enter, the app sends: machine ID, public IP, local IP, and desktop name.
- If they type **N**, only the machine ID is sent for access checks. No IP or desktop name is shared.

**Important notes:**
- Replace `TELEMETRY_WORKER_URL` with your actual Worker URL from Part 3.6.
- Replace `TELEMETRY_ADMIN_TOKEN` with the **same string** you used in `wrangler secret put ADMIN_TOKEN`.
- Replace `current_username` with whatever username variable your app already uses. If your app doesn't have one, you can leave it as `"unknown"`.
- Place this code **before** your main menu loop so the ban check happens on startup.

#### C. Optional — Ban/whitelist checks later in your app

If you want to check ban status again later (e.g., after login), you can call:

```python
machine_uuid = telemetry._get_machine_uuid()

if telemetry.check_banned(machine_uuid):
    print("This machine is banned!")
    sys.exit(1)

if telemetry.check_whitelisted(machine_uuid):
    print("This machine is whitelisted.")
```

---

## Part 5 — Discord Bot Configuration

### Step 5.1 — Configure .env

Copy `.env.example` to `.env` in the `logging-main` folder:

```bash
copy .env.example .env
```

Edit `.env` with a text editor and fill in all values:

```env
# Discord Bot Token (from Part 2.2)
BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN

# Server / Channel IDs (from Part 2.4 and 2.5)
GUILD_ID=YOUR_GUILD_ID
ADMIN_CHANNEL_ID=YOUR_ADMIN_CHANNEL_ID
BANNED_LOG_CHANNEL_ID=YOUR_BANNED_LOG_CHANNEL_ID

# Admin Token (must match wrangler secret ADMIN_TOKEN from Part 3.5)
ADMIN_TOKEN=YOUR_ADMIN_TOKEN

# Your Discord User ID (from Part 2.4)
AUTHORIZED_USER_IDS=["YOUR_DISCORD_USER_ID"]

# Worker /admin endpoint URL (from Part 3.6)
WORKER_URL=https://telemetry-worker.YOUR_SUBDOMAIN.workers.dev/admin
```

Fill in **every** field with your real values. Do not leave any `YOUR_...` placeholders.

### Step 5.2 — Run the Bot

```bash
python bot.py
```

If everything is configured correctly, you will see:

```
Logged in as YourBotName#1234
```

The bot is now online in your server.

### Step 5.3 — Use Admin Commands

In your Discord server, go to any channel and type:

```
!ban <hwid>
```

Where `<hwid>` is the machine UUID of the user you want to ban (you can get this from the telemetry embeds in your main webhook channel).

Full command list:

| Command | Example | What it does |
|---------|---------|-------------|
| `!ban <hwid>` | `!ban 1234567890123456` | Bans the machine |
| `!unban <hwid>` | `!unban 1234567890123456` | Removes ban |
| `!banned` | `!banned` | Lists all banned machines |
| `!whitelist <hwid>` | `!whitelist 1234567890123456` | Whitelists the machine |
| `!unwhitelist <hwid>` | `!unwhitelist 1234567890123456` | Removes from whitelist |
| `!whitelisted` | `!whitelisted` | Lists all whitelisted machines |

Only the Discord user IDs listed in `AUTHORIZED_USER_IDS` can use these commands. If someone else tries, the bot replies with `❌ You are not authorized to use this command.`

---

## Part 6 — Folder Structure

After setup, your `logging-main` folder should look like this:

```
logging-main/
  bot.py                  <- Discord bot (run with: python bot.py)
  worker.js               <- Cloudflare Worker source code
  wrangler.toml           <- Worker config with your KV namespace IDs
  telemetry_logger.py     <- Python module to import into your app
  requirements.txt        <- Python dependencies
  .env.example            <- Template for environment variables
  .env                    <- Your real config (DO NOT commit this)
  README.md               <- This file
```

Your own app folder should look like this (example):

```
my-app/
  main.py                 <- Your app's main script
  telemetry_logger.py     <- Copied from logging-main/ (or import via path)
  requirements.txt        <- Your app's own dependencies
```

---

## Part 7 — Testing

### Test 1 — Worker is deployed

Visit your Worker URL in a browser:

```
https://telemetry-worker.YOUR_SUBDOMAIN.workers.dev/?id=test123&data={"test":true}
```

You should see a JSON response like:

```json
{"banned":false}
```

If you get an error, check:
- `wrangler deploy` completed successfully
- KV namespaces are bound in `wrangler.toml`
- Worker secrets are set

### Test 2 — Telemetry logging works

Run your Python app. You will see a consent prompt:

```
[Telemetry] This app sends a one-time check to verify access.
[Telemetry] Machine ID : 123456789012345
[Telemetry] Public IP  : 203.0.113.45
[Telemetry] Local IP   : 192.168.1.7
[Telemetry] Desktop    : DESKTOP-ABC123
[Telemetry] Do you want to share these details? [Y/n]:
```

- If you type **Y** or press Enter, the app sends the machine ID, public IP, local IP, and desktop name to the Worker. A rich embed will appear in your main Discord webhook channel.
- If you type **N**, only the machine ID is sent. The Worker still checks ban/whitelist status, but no Discord embed is posted and no IP/desktop data is shared.

### Test 3 — Ban works

1. Copy the `machine_uuid` from the telemetry embed in Discord.
2. In your Discord server, type: `!ban <machine_uuid>`
3. The bot should reply with `✅ Banned: <machine_uuid>`.
4. Try running your Python app again on that machine — it should print `[ACCESS DENIED]` and exit.

### Test 4 — Whitelist bypasses logging

1. Type `!whitelist <machine_uuid>` in Discord.
2. The bot should reply with `✅ Whitelisted: <machine_uuid>`.
3. Run your Python app again — it should connect without being banned, and no new embed should appear in Discord (whitelisted machines don't trigger webhooks on every startup).

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'telemetry_logger'"

Make sure `telemetry_logger.py` is in the same folder as your script, or that you added the correct path with `sys.path.insert(0, ...)` before importing.

### "Telemetry upload failed" / timeout

- Check that your Worker URL is correct (no trailing slash issues).
- Check that you deployed with `wrangler deploy` and the Worker is live.
- Make sure outbound HTTPS is not blocked by a firewall.

### Bot says "You are not authorized"

- Check that your Discord user ID is in `AUTHORIZED_USER_IDS` in `.env`.
- Make sure `bot.py` was restarted after editing `.env`.

### No Discord embeds appear

- Verify webhook URLs are correct in both the Worker secrets and the Worker code.
- Check that the Worker has the `DISCORD_WEBHOOK_URL` secret set: `wrangler secret list`.
- Check the Worker logs: `wrangler tail`.

### KV errors ("KV not bound")

- Make sure `wrangler.toml` has the correct `id` and `preview_id` for both `BLACKLIST_KV` and `WHITELIST_KV`.
- Redeploy after editing `wrangler.toml`: `wrangler deploy`.

### "Message Content Intent" warning

If the bot doesn't respond to commands, go back to the Discord Developer Portal → your application → Bot → Privileged Gateway Intents and make sure **Message Content Intent** is toggled **ON**.

---

## Security Notes

- **Never** commit `.env` to version control. Add `.env` to your `.gitignore`.
- The `ADMIN_TOKEN` must be the same everywhere: in `wrangler secret put ADMIN_TOKEN` and in `bot.py`'s `.env`. Treat it like a password.
- The `/admin` endpoint is only as secure as your `ADMIN_TOKEN`. Use a long, random string.
- KV namespace IDs are not secret, but if you don't want others to inspect your infrastructure, avoid publishing your production `wrangler.toml`.
- Discord bot tokens are **secret**. If one is accidentally exposed, go to the Developer Portal → Bot → Reset Token immediately.
- The Python logger asks for consent before collecting data. If the user agrees, it collects: public IP, local IP, desktop name, and machine ID. If the user declines, only the machine ID is sent. Always ensure you have consent from anyone running the app.
