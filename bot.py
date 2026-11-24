"""NeurixBot Open Source — minimal, self-hosted Discord bot core."""
from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("neurixbot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


@bot.event
async def on_ready() -> None:
    logger.info("Connected to Discord as %s", bot.user)
    try:
        synced = await bot.tree.sync()
        logger.info("Synchronized %s application command(s).", len(synced))
    except discord.HTTPException:
        logger.exception("Could not synchronize application commands.")


@bot.tree.command(name="ping", description="Check whether NeurixBot is responding.")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency_ms} ms", ephemeral=True)


@bot.tree.command(name="about", description="Show information about this NeurixBot instance.")
async def about(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="NeurixBot",
        description="An open-source Discord bot core you can host and customize.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Edition", value="Open Source", inline=True)
    embed.add_field(name="AI", value="Enabled" if openai_client else "Not configured", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ask", description="Ask the optional NeurixBot AI assistant.")
@app_commands.describe(prompt="Your question. Do not share passwords or private information.")
async def ask(interaction: discord.Interaction, prompt: str) -> None:
    if openai_client is None:
        await interaction.response.send_message(
            "The AI feature is not configured. The bot administrator must set OPENAI_API_KEY.",
            ephemeral=True,
        )
        return

    prompt = prompt.strip()
    if not prompt:
        await interaction.response.send_message("Please enter a question.", ephemeral=True)
        return
    if len(prompt) > 4000:
        await interaction.response.send_message("Your question is too long (maximum: 4,000 characters).", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are NeurixBot, a helpful assistant in a Discord server. Be clear, concise, and respectful.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=700,
        )
        answer = response.choices[0].message.content or "I couldn't generate a response."
        await interaction.followup.send(answer[:1900])
    except Exception:
        logger.exception("AI request failed.")
        await interaction.followup.send(
            "The AI service is temporarily unavailable. Please try again later.",
            ephemeral=True,
        )


async def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and configure it.")
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("NeurixBot stopped.")
