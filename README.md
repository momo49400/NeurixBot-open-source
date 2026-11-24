# NeurixBot — Open Source

NeurixBot is a self-hosted Discord bot built with Python and `discord.py`. This public edition includes the core bot, moderation, giveaways, Staff Security, server language settings and support tickets.

## Features

- Slash commands and classic `!` prefix commands
- Ping, status and latency information
- Moderation: kick, ban, unban, timeout and untimeout
- Giveaways: create, cancel, end, reroll and inspect
- Dynamic roles for joins, boosts and giveaway winners
- Staff authentication, role levels and protected staff actions
- French and English server language settings
- Optional OpenAI-powered `!ask` command
- Optional private support tickets, staff claiming and transcript archiving

The private Neurix Tracker, DevOps integrations and internal dashboard are not part of this public edition. The `services/` directories are documentation placeholders for independent extensions.

## Requirements

- Python 3.11 or newer
- A Discord application and bot token
- An OpenAI API key only if you want to use `!ask`

## Quick start

1. Clone the repository and enter its directory.
2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   **Windows PowerShell**
   ```powershell
   .venv\Scripts\Activate.ps1
   ```

   **macOS / Linux**
   ```bash
   source .venv/bin/activate
   ```

3. Install the dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and fill in the values you need.
5. Configure the bot in the [Discord Developer Portal](https://discord.com/developers/applications).
6. Enable **Server Members Intent** and **Message Content Intent** in the bot's privileged gateway intents.
7. Invite the bot using the `bot` and `applications.commands` OAuth2 scopes. Grant only the server permissions required by the features you enable.
8. Start the bot:

   ```bash
   python bot.py
   ```

Slash commands are registered when the bot starts. Discord may need a little time to display them.

## Configuration

All supported environment variables are listed in `.env.example`.

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Discord bot token. Required. |
| `BOT_OWNER_ID` | Discord user ID of the bot owner. Used for owner-level controls and the optional startup DM example. |
| `OPENAI_API_KEY` | Optional API key for `!ask`. Leave empty to disable the AI command. |
| `OPENAI_MODEL` | Model used by `!ask`; defaults to `gpt-4o-mini`. |
| `STAFF_ROLE_IDS` | Optional comma-separated Discord role IDs assigned the Staff level. |
| `ADMIN_ROLE_IDS` | Optional comma-separated Discord role IDs assigned the Admin level. |
| `CRITICAL_ROLE_IDS` | Optional comma-separated Discord role IDs assigned the Critical level. |
| `TICKET_ENABLED` | Set to `true` to enable the ticket system. Disabled by default. |
| `TICKET_CATEGORY_ID` | Category where private ticket channels are created. |
| `TICKET_STAFF_CHANNEL_ID` | Channel where staff receive new-ticket notifications. |
| `TICKET_STAFF_ROLE_ID` | Role allowed to claim tickets. |
| `TICKET_STATE_PATH` | Path to the ticket state JSON file. |
| `TICKET_ARCHIVE_CHANNEL_ID` | Optional private channel used to archive ticket transcripts. |
| `GUILD_LANGUAGE_DB` | SQLite database path for per-server language settings. |

Role IDs can be separated with commas, semicolons or spaces. Keep IDs as strings in the environment file.

### Support tickets

Tickets are disabled by default. To enable them, set `TICKET_ENABLED=true` and provide `TICKET_CATEGORY_ID`, `TICKET_STAFF_CHANNEL_ID` and `TICKET_STAFF_ROLE_ID`. The bot needs permission to view and manage the relevant channels, create private text channels, and send messages.

For transcript archiving, set `TICKET_ARCHIVE_CHANNEL_ID` to a staff-only channel. Make sure its permissions prevent regular members from viewing archived transcripts.

### Optional online notification

The bot already sends the owner a direct message when startup finishes if `BOT_OWNER_ID` is configured. If you want to add the same notification to another version of the bot, place this snippet inside `on_ready()`, after the giveaway and Staff Security restoration steps:

```python
if BOT_OWNER_ID:
    try:
        user = await bot.fetch_user(BOT_OWNER_ID)
        await user.send("🟢 NeurixBot est maintenant en ligne !")
    except discord.HTTPException as exc:
        print(f"Startup notification failed: {type(exc).__name__}: {exc}", flush=True)
```

Set `BOT_OWNER_ID` in `.env`. The owner must allow direct messages from the bot.

## Commands

| Command | Description |
|---|---|
| `/ping`, `!ping` | Check whether the bot responds. |
| `/status`, `!status` | Display bot status, latency and uptime. |
| `/ticket ouvrir` | Open a private support ticket. Requires tickets to be enabled. |
| `/ticket fermer` | Start the ticket closure flow. |
| `/ticket panel` | Publish or update the support panel. |
| `/steplanguage` | Set the server language to French or English. |
| `/giveaway` | Access giveaway management actions. |
| `/staff` | Access Staff Security actions. |
| `!ask <question>` | Ask the optional OpenAI assistant. |

The bot also registers moderation commands (`/kick`, `/ban`, `/unban`, `/timeout`, `/untimeout`, `/say`) and dynamic-role configuration commands. Discord's command picker shows their descriptions and parameters. Staff actions require the appropriate Staff Security level.

## Docker

A Dockerfile is provided. Build and run the image from the repository directory:

```bash
docker build -t neurixbot .
docker run --env-file .env neurixbot
```

If you enable tickets or persistent language settings in a container, mount persistent storage and point `TICKET_STATE_PATH` and `GUILD_LANGUAGE_DB` to paths on that storage. Otherwise, container replacement may discard local state.

## Project layout

| Path | Purpose |
|---|---|
| `bot.py` | Main bot entry point and core commands. |
| `tickets.py`, `ticket_panel.py`, `ticket_closure.py` | Support ticket system. |
| `guild_language.py` | Persistent French / English server language setting. |
| `services/` | Documentation placeholders for optional independent integrations. |
| `tests/` | Automated tests. |
| `.github/workflows/ci.yml` | GitHub Actions checks for Python 3.11 and 3.12. |

## Tests

Install the project dependencies, then run:

```bash
python -m compileall -q bot.py tickets.py ticket_panel.py ticket_closure.py guild_language.py tests
python -m unittest discover -s tests -v
```

## Security

- Never commit `.env`, Discord tokens, API keys or private production state.
- Keep `.env.example` free of real credentials and server-specific IDs.
- Restrict access to ticket transcript archives.
- Give the bot only the Discord permissions required for the enabled features.
- If a credential is exposed, revoke it and generate a replacement immediately.

## License

MIT. See [LICENSE](LICENSE).
