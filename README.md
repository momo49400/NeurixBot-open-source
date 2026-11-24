# NeurixBot — Open Source

NeurixBot is a self-hosted Discord bot foundation. This repository contains the public core only; private infrastructure and operational integrations are intentionally not included.

## Included

- Discord connection using discord.py
- Slash commands: /ping, /about, and optional /ask
- Optional OpenAI integration
- Environment-based configuration
- Basic logging and startup validation

## Not included

The following private/optional services are intentionally left out:

- DevOps monitoring and deployment automation
- Private GitHub tracker
- Private dashboard and internal alerts
- Private Roblox tracking configuration and state
- Internal staff/security workflows and production data

The services directory documents extension points only. It does not connect to private Neurix infrastructure.

## Requirements

- Python 3.10+
- A Discord application and bot token
- An OpenAI API key only if you want to use /ask

## Installation

1. Clone this repository.
2. Create and activate a virtual environment.
3. Install dependencies:

   Windows:
   python -m venv .venv
   .venv\\Scripts\\activate

   macOS / Linux:
   python -m venv .venv
   source .venv/bin/activate

   Then:
   pip install -r requirements.txt

4. Copy .env.example to .env.
5. Set DISCORD_TOKEN. Set OPENAI_API_KEY if you want the AI command.
6. Enable application commands in the Discord Developer Portal and invite the bot with the bot and applications.commands scopes.
7. Start the bot with: python bot.py

Slash commands are synchronized when the bot starts. Discord may take a short time to display them.

## Configuration

| Variable | Required | Description |
|---|---|---|
| DISCORD_TOKEN | Yes | Discord bot token |
| OPENAI_API_KEY | No | Enables /ask |
| OPENAI_MODEL | No | OpenAI model name; defaults to gpt-4o-mini |
| BOT_OWNER_ID | No | Reserved for future owner-only extensions |

Never commit your real .env file, bot token, API key, or server-specific private data. If a credential is accidentally published, revoke and rotate it immediately.

## Development

Run the bot locally with a test Discord server. Keep secrets in your local .env file and add tests for new commands or services. Optional integrations should be isolated behind their own modules and disabled by default.

## License

MIT. See LICENSE.
