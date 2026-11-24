# NeurixBot — Open Source

NeurixBot is a self-hosted Discord bot built with Python and discord.py. This edition keeps the main bot commands, moderation tools, giveaways, staff security controls, language settings and the support ticket system.

## Included features

- Discord slash commands and classic `!` prefix commands
- Ping and bot status
- Moderation: kick, ban, unban, timeout and untimeout
- Giveaways: create, cancel, end, reroll and inspect
- Dynamic server roles for joins, boosts and giveaway winners
- Staff authentication and role-based staff controls
- French / English server language setting
- Optional OpenAI-powered `!ask` command
- Support tickets with staff claiming, closure confirmation and private transcript archiving

Tracker integrations and DevOps commands are not included in this public edition. The private dashboard that depended on those integrations is also omitted. The ticket system remains included.

## Requirements

- Python 3.10 or newer
- A Discord application and bot token
- An OpenAI API key only if you want to enable `!ask`

## Installation

1. Clone this repository.
2. Create a virtual environment:

   ```bash
   python -m venv .venv
   ```

3. Activate it:

   **Windows**
   ```powershell
   .venv\Scripts\Activate.ps1
   ```

   **macOS / Linux**
   ```bash
   source .venv/bin/activate
   ```

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Copy `.env.example` to `.env` and fill in the values you need.
6. In the Discord Developer Portal, enable **Server Members Intent** and **Message Content Intent**.
7. Invite the bot with the `bot` and `applications.commands` scopes, then start it:

   ```bash
   python bot.py
   ```

Slash commands are synchronized when the bot starts. Discord can take a short time to display them.

## Main commands

| Command | Purpose |
|---|---|
| `/ping` and `!ping` | Check bot response |
| `/status` and `!status` | Show latency, uptime and version |
| `/ticket ouvrir` | Open a private support ticket |
| `/ticket fermer` | Request ticket closure |
| `/ticket panel` | Publish or update the support panel |
| `/steplanguage` | Set the server language to French or English |
| `/giveaway` | Manage giveaways |
| `/staff` | Staff authentication and permissions |
| `!ask <question>` | Ask the optional OpenAI assistant |

The bot also includes moderation and dynamic-role slash commands. Discord displays their descriptions and parameters in the command picker.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `BOT_OWNER_ID` | Recommended | Discord user ID of the bot owner |
| `OPENAI_API_KEY` | No | Enables `!ask` |
| `OPENAI_MODEL` | No | Model used by `!ask`; defaults to `gpt-4o-mini` |
| `TICKET_ENABLED` | No | Set to `true` to enable tickets |
| `TICKET_CATEGORY_ID` | For tickets | Category where private tickets are created |
| `TICKET_STAFF_CHANNEL_ID` | For tickets | Staff notification channel |
| `TICKET_STAFF_ROLE_ID` | For tickets | Role allowed to claim tickets |
| `TICKET_STATE_PATH` | No | Ticket state file path |
| `TICKET_ARCHIVE_CHANNEL_ID` | For ticket closure | Private staff-only transcript archive |
| `GUILD_LANGUAGE_DB` | No | SQLite database path for server language settings |

To enable tickets, set `TICKET_ENABLED=true` and configure the category, staff channel and staff role IDs. For transcript archiving, configure a private archive channel hidden from `@everyone` and visible to the staff role.

## Security

- Never commit `.env`, bot tokens, API keys, server IDs or private production state.
- The repository includes `.env.example`, not a real `.env` file.
- Keep the transcript archive private and grant access only to trusted staff.
- If a credential is accidentally published, revoke and rotate it immediately.

## License

MIT. See [LICENSE](LICENSE).
