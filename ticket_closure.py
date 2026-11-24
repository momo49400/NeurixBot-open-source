"""Opt-in ticket closure, explicit confirmation and private transcript archiving."""
import io
import logging
from datetime import timezone

import discord

from guild_language import interaction_language, translate

LOG = logging.getLogger(__name__)
CLOSE_ID = "neurix:ticket:close:v1"
MAX_MESSAGES = 500
MAX_TRANSCRIPT_CHARS = 180_000


async def make_transcript(channel, language="fr"):
    """Bounded UTF-8 plain-text archive; attachments are referenced by name only."""
    lines = [
        translate(language, "Transcription NeurixBot | salon {channel}", "NeurixBot ticket transcript | channel {channel}").format(channel=channel.id),
        translate(language, "Maximum : {count} messages récents | ordre chronologique",
                  "Maximum: {count} recent messages | chronological order").format(count=MAX_MESSAGES), "",
    ]
    size = sum(len(line) + 1 for line in lines)
    messages = []
    async for message in channel.history(limit=MAX_MESSAGES, oldest_first=False):
        messages.append(message)
    for message in reversed(messages):
        at = message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{message.author} ({message.author.id})"
        body = (message.content or "").replace("\r", " ").replace("\x00", "")
        if not body and message.embeds:
            body = "[embed: " + "; ".join(str(embed.title or translate(language, "sans titre", "untitled"))[:100] for embed in message.embeds[:3]) + "]"
        attachments = ", ".join(a.filename[:100] for a in message.attachments[:10])
        entry = f"[{at}] {author}: {body}"
        if attachments:
            entry += translate(language, "\n  Pièces jointes (noms uniquement) : {names}",
                               "\n  Attachments (names only): {names}").format(names=attachments)
        if size + len(entry) > MAX_TRANSCRIPT_CHARS:
            lines.append(translate(language, "[Transcription tronquée : limite de taille atteinte]",
                                   "[Transcript truncated: size limit reached]"))
            break
        lines.append(entry)
        size += len(entry) + 1
    return ("\n".join(lines) + "\n").encode("utf-8")


class ConfirmCloseView(discord.ui.View):
    def __init__(self, manager, requester_id, channel_id, language="fr"):
        super().__init__(timeout=60)
        self.manager = manager
        self.requester_id = requester_id
        self.channel_id = channel_id
        self.confirm.label = translate(language, "Confirmer la fermeture", "Confirm closure")
        self.cancel.label = translate(language, "Annuler", "Cancel")

    async def interaction_check(self, interaction):
        language = interaction_language(interaction)
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(translate(language, "⛔ Cette confirmation ne t'appartient pas.", "⛔ This confirmation belongs to another user."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer la fermeture", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await finish_close(self.manager, interaction, self.channel_id)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        language = interaction_language(interaction)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=translate(language, "✅ Fermeture annulée.", "✅ Closure cancelled."), view=self)


async def request_close(manager, interaction):
    language = interaction_language(interaction)
    if not manager.enabled or interaction.guild is None or interaction.channel is None:
        return await interaction.response.send_message(translate(language, "⚠️ Ticket indisponible.", "⚠️ Ticket unavailable."), ephemeral=True)
    record = manager.tickets.get(str(interaction.channel.id))
    if not record or record.get("guild_id") != interaction.guild.id or not record.get("open", True):
        return await interaction.response.send_message(translate(language, "⚠️ Ce salon n'est pas un ticket ouvert.", "⚠️ This channel is not an open ticket."), ephemeral=True)
    if interaction.user.id != record["user_id"] and not manager.can_claim(interaction.user):
        return await interaction.response.send_message(translate(language, "⛔ Seul l'auteur ou le staff peut fermer ce ticket.", "⛔ Only the ticket author or staff can close this ticket."), ephemeral=True)
    if not manager.archive_channel_id:
        return await interaction.response.send_message(
            translate(language, "⚠️ L'archivage n'est pas configuré : demande au propriétaire de définir TICKET_ARCHIVE_CHANNEL_ID.", "⚠️ Archiving is not configured. Ask the bot owner to set TICKET_ARCHIVE_CHANNEL_ID."),
            ephemeral=True)
    view = ConfirmCloseView(manager, interaction.user.id, interaction.channel.id, language)
    await interaction.response.send_message(
        translate(language, "🔒 Confirmer la fermeture ? Les messages seront archivés dans le salon privé du staff, puis le salon sera supprimé.", "🔒 Close this ticket? Messages will be archived in the private staff channel before this channel is deleted."),
        view=view, ephemeral=True)


async def finish_close(manager, interaction, channel_id):
    language = interaction_language(interaction)
    if interaction.guild is None or interaction.channel is None or interaction.channel.id != channel_id:
        return await interaction.response.send_message(translate(language, "⚠️ Mauvais salon.", "⚠️ Wrong channel."), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with manager.lock:
        record = manager.tickets.get(str(channel_id))
        if not record or not record.get("open", True) or record.get("guild_id") != interaction.guild.id:
            return await interaction.followup.send(translate(language, "ℹ️ Ticket déjà fermé ou introuvable.", "ℹ️ Ticket is already closed or could not be found."), ephemeral=True)
        if interaction.user.id != record["user_id"] and not manager.can_claim(interaction.user):
            return await interaction.followup.send(translate(language, "⛔ Action interdite.", "⛔ You cannot perform this action."), ephemeral=True)
        guild = interaction.guild
        archive = guild.get_channel(manager.archive_channel_id)
        staff_role = guild.get_role(manager.staff_role_id)
        # Do not store private user discussions in a public or incorrectly configured channel.
        if (not isinstance(archive, discord.TextChannel) or staff_role is None
                or archive.overwrites_for(guild.default_role).view_channel is not False
                or not archive.permissions_for(staff_role).view_channel
                or not archive.permissions_for(guild.me).send_messages):
            return await interaction.followup.send(
                translate(language, "⚠️ Salon d'archives absent ou non privé. Vérifie les permissions du salon (masqué à @everyone, visible au staff).", "⚠️ Archive channel is missing or public. Check that only staff can see it and that it is hidden from @everyone."),
                ephemeral=True)
        channel = guild.get_channel(channel_id)
        if channel is None:
            return await interaction.followup.send(translate(language, "⚠️ Salon du ticket introuvable.", "⚠️ Ticket channel not found."), ephemeral=True)
        try:
            transcript = await make_transcript(channel, language)
            archive_embed = discord.Embed(title=translate(language, "📁 Ticket fermé • Archive privée", "📁 Ticket closed • Private archive"), color=0x5865F2)
            archive_embed.add_field(name="Ticket", value=str(channel_id), inline=True)
            archive_embed.add_field(name=translate(language, "Utilisateur", "User"), value=f"<@{record['user_id']}>", inline=True)
            archive_embed.add_field(name=translate(language, "Fermé par", "Closed by"), value=interaction.user.mention, inline=True)
            archive_embed.add_field(name=translate(language, "Sujet", "Subject"), value=str(record.get("subject", "—"))[:250], inline=False)
            archive_embed.add_field(name=translate(language, "Staff responsable", "Assigned staff"), value=(
                f"<@{record['claimed_by']}>" if record.get("claimed_by") else translate(language, "Non attribué", "Unassigned")), inline=False)
            file = discord.File(io.BytesIO(transcript), filename=f"ticket-{channel_id}.txt")
            await archive.send(embed=archive_embed, file=file, allowed_mentions=discord.AllowedMentions.none())
            record["open"] = False
            record["closed_by"] = interaction.user.id
            try:
                manager._save()
            except OSError:
                record["open"] = True
                record.pop("closed_by", None)
                LOG.exception("Ticket state could not be persisted for %s", channel_id)
                return await interaction.followup.send(
                    translate(language, "⚠️ Archive envoyée, mais sauvegarde impossible. Salon conservé : avertis un administrateur.", "⚠️ Archive sent, but state could not be saved. The channel was kept. Tell an administrator."),
                    ephemeral=True)
            try:
                await channel.delete(reason=f"Ticket archived and closed by {interaction.user.id}")
            except discord.HTTPException:
                LOG.exception("Ticket archived but channel deletion failed for %s", channel_id)
                return await interaction.followup.send(
                    translate(language, "⚠️ Ticket archivé et marqué fermé, mais suppression du salon impossible. Vérifie les permissions du bot.", "⚠️ Ticket archived and marked closed, but the channel could not be deleted. Check the bot's permissions."),
                    ephemeral=True)
            await interaction.followup.send(translate(language, "✅ Ticket fermé. Transcription archivée dans le salon privé du staff.", "✅ Ticket closed. The transcript was archived in the private staff channel."), ephemeral=True)
        except (discord.HTTPException, discord.Forbidden, OSError):
            LOG.exception("Ticket closure failed; channel retained for %s", channel_id)
            await interaction.followup.send(
                translate(language, "⚠️ Archivage impossible : le ticket reste ouvert et aucun message n'a été supprimé.", "⚠️ Archiving failed. The ticket remains open and its messages were kept."),
                ephemeral=True)


class TicketCloseView(discord.ui.View):
    def __init__(self, manager, language="fr"):
        super().__init__(timeout=None)
        self.manager = manager
        self.close_button.label = translate(language, "Fermer le ticket", "Close ticket")

    @discord.ui.button(label="Fermer le ticket", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id=CLOSE_ID)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await request_close(self.manager, interaction)
