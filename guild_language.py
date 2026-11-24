"""Persistent display language for Discord guilds."""
import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import discord
from discord import app_commands

LOG = logging.getLogger(__name__)
DEFAULT_LANGUAGE = "fr"


def database_path():
    configured = os.getenv("GUILD_LANGUAGE_DB")
    if configured:
        return Path(configured)
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return Path(volume) / "guild_languages.sqlite3"
    return Path(os.getenv("TICKET_STATE_PATH", "./tickets_state.json")).with_name(
        "guild_languages.sqlite3"
    )


def get_language(guild_id):
    path = database_path()
    if guild_id is None or not path.is_file():
        return DEFAULT_LANGUAGE
    try:
        with closing(sqlite3.connect(path, timeout=0.1)) as connection:
            row = connection.execute(
                "SELECT language FROM guild_languages WHERE guild_id = ?", (guild_id,)
            ).fetchone()
    except sqlite3.Error:
        LOG.warning("Could not read guild language", exc_info=True)
        return DEFAULT_LANGUAGE
    return row[0] if row else DEFAULT_LANGUAGE


def set_language(guild_id, language):
    if guild_id is None or language not in ("fr", "en"):
        raise ValueError("Expected a guild and a supported language")
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS guild_languages "
            "(guild_id INTEGER PRIMARY KEY, language TEXT NOT NULL CHECK(language IN ('fr', 'en')))"
        )
        connection.execute(
            "INSERT INTO guild_languages (guild_id, language) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET language = excluded.language",
            (guild_id, language),
        )


def interaction_language(interaction):
    return get_language(getattr(getattr(interaction, "guild", None), "id", None))


def translate(language, french, english):
    return english if language == "en" else french


def register_language_command(bot):
    @bot.tree.command(name="steplanguage", description="Choose the bot language / Choisir la langue du bot")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(language=[
        app_commands.Choice(name="Français", value="fr"),
        app_commands.Choice(name="English", value="en"),
    ])
    async def steplanguage(interaction: discord.Interaction, language: app_commands.Choice[str]):
        if interaction.guild_id is None or not interaction.permissions.administrator:
            await interaction.response.send_message(
                translate(interaction_language(interaction),
                          "⛔ Réservé aux administrateurs du serveur.",
                          "⛔ Only server administrators can change the language."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await asyncio.to_thread(set_language, interaction.guild_id, language.value)
        except (OSError, sqlite3.Error, ValueError):
            LOG.exception("Could not save guild language")
            await interaction.followup.send(
                translate(interaction_language(interaction),
                          "⚠️ Impossible d'enregistrer la langue du serveur.",
                          "⚠️ Could not save the server language."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            translate(language.value, "✅ Langue du serveur : français.", "✅ Server language: English."),
            ephemeral=True,
        )
