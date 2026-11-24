"""Persistent opt-in ticket entry panel with support and giveaway-claim buttons."""
import json
import logging
import os
from pathlib import Path

import discord

from guild_language import interaction_language, translate

LOG = logging.getLogger(__name__)
SUPPORT_ID = "neurix:ticket:panel:support:v1"
GIVEAWAY_ID = "neurix:ticket:panel:giveaway:v1"


def panel_embed(language="fr"):
    embed = discord.Embed(
        title=translate(language, "🎫 Centre d'assistance • NeurixBot", "🎫 Support Center • NeurixBot"),
        description=translate(
            language,
            "**Besoin d'aide ?** Ouvre un ticket privé pour discuter avec notre équipe.\n\n"
            "Explique clairement ta demande, avec les informations utiles. "
            "Un membre du staff pourra prendre en charge ton ticket.",
            "**Need help?** Open a private ticket to talk to our team.\n\n"
            "Describe your request and include any useful details. A staff member can claim your ticket.",
        ),
        color=0x9B59B6,
    )
    embed.add_field(
        name="💬 Contact Support",
        value=translate(language, "Questions, assistance, signalements ou problème lié au serveur.", "Questions, help, reports or server issues."),
        inline=False,
    )
    embed.add_field(
        name="🎁 Claim GW",
        value=translate(language,
                        "Réclamer une récompense gagnée lors d'un giveaway. "
                        "Précise le giveaway concerné et fournis une preuve si nécessaire.",
                        "Claim a giveaway prize. Tell us which giveaway and provide proof if needed."),
        inline=False,
    )
    embed.set_footer(text=translate(language, "Un ticket = un salon privé • Un ticket ouvert par membre à la fois", "Each ticket has a private channel • One open ticket per member"))
    return embed


class TicketPanelView(discord.ui.View):
    def __init__(self, manager, language="fr"):
        super().__init__(timeout=None)
        self.manager = manager
        self.claim_gw.label = translate(language, "Claim GW", "Claim Prize")

    @discord.ui.button(label="Contact Support", emoji="💬",
                       style=discord.ButtonStyle.primary, custom_id=SUPPORT_ID)
    async def contact_support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.manager.open_ticket(interaction, "💬 Contact Support")

    @discord.ui.button(label="Claim GW", emoji="🎁",
                       style=discord.ButtonStyle.success, custom_id=GIVEAWAY_ID)
    async def claim_gw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.manager.open_ticket(interaction, "🎁 Giveaway Claim")


def _state_path(manager):
    return manager.path.with_name("ticket_panel_state.json")


def _load_panel_id(path, guild_id):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(str(guild_id))
    if not isinstance(entry, dict):
        return None
    channel_id, message_id = entry.get("channel_id"), entry.get("message_id")
    if isinstance(channel_id, int) and isinstance(message_id, int) and channel_id > 0 and message_id > 0:
        return channel_id, message_id
    return None


def _save_panel_id(path, guild_id, channel_id, message_id):
    data = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except (OSError, ValueError):
            raise ValueError("Fichier d'état du panneau endommagé") from None
    data[str(guild_id)] = {"channel_id": channel_id, "message_id": message_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


async def publish_panel(manager, interaction, channel):
    """Owner-only; reuse the previously posted panel rather than duplicating it."""
    language = interaction_language(interaction)
    if interaction.user.id != manager.owner_id:
        return await interaction.response.send_message(translate(language, "⛔ Réservé au propriétaire.", "⛔ Only the bot owner can publish this panel."), ephemeral=True)
    if not manager.enabled:
        return await interaction.response.send_message(translate(language, "⚠️ Tickets désactivés.", "⚠️ Tickets are disabled."), ephemeral=True)
    if interaction.guild is None or channel.guild.id != interaction.guild.id:
        return await interaction.response.send_message(translate(language, "⚠️ Choisis un salon de ce serveur.", "⚠️ Choose a channel in this server."), ephemeral=True)
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        return await interaction.response.send_message(
            translate(language, "⚠️ Le bot doit pouvoir voir le salon, envoyer des messages et des embeds.", "⚠️ The bot needs permission to view this channel and send messages and embeds."), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with manager.lock:
        path = _state_path(manager)
        old = _load_panel_id(path, interaction.guild.id)
        if old:
            old_channel = interaction.guild.get_channel(old[0])
            if old_channel is not None:
                try:
                    message = await old_channel.fetch_message(old[1])
                    await message.edit(embed=panel_embed(language), view=TicketPanelView(manager, language),
                                       allowed_mentions=discord.AllowedMentions.none())
                    return await interaction.followup.send(
                        translate(
                            language,
                            "✅ Panneau existant mis à jour dans {channel}. "
                            "Pour le déplacer, supprime l'ancien message puis relance la commande.",
                            "✅ Existing panel updated in {channel}. "
                            "To move it, delete the old message and run the command again.",
                        ).format(channel=old_channel.mention),
                        ephemeral=True)
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    return await interaction.followup.send(
                        translate(language, "⚠️ Impossible de mettre à jour l'ancien panneau : vérifie les permissions.", "⚠️ Could not update the existing panel. Check the bot's permissions."),
                        ephemeral=True)
        try:
            message = await channel.send(embed=panel_embed(language), view=TicketPanelView(manager, language),
                                         allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return await interaction.followup.send(translate(language, "⚠️ Impossible de publier le panneau.", "⚠️ Could not publish the panel."), ephemeral=True)
        try:
            _save_panel_id(path, interaction.guild.id, channel.id, message.id)
        except (OSError, ValueError):
            try:
                await message.delete()
            except discord.HTTPException:
                LOG.warning("Could not delete untracked panel message")
            return await interaction.followup.send(translate(language, "⚠️ Sauvegarde impossible : panneau non activé.", "⚠️ Could not save the panel. It was not activated."), ephemeral=True)
        await interaction.followup.send(translate(language, "✅ Panneau d'assistance publié dans {channel}.", "✅ Support panel published in {channel}.").format(channel=channel.mention), ephemeral=True)
