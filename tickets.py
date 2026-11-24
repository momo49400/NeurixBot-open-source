"""Private Discord support tickets. Opt-in, persistent claim buttons and JSON state."""
import asyncio
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands

from guild_language import interaction_language, translate
from ticket_closure import TicketCloseView, request_close
from ticket_panel import TicketPanelView, publish_panel

LOG = logging.getLogger(__name__)
CLAIM_ID = "neurix:ticket:claim:v1"


def _positive_id(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def load_ticket_settings(env=None):
    env = os.environ if env is None else env
    enabled = env.get("TICKET_ENABLED", "false").strip().lower() == "true"
    category_id = _positive_id(env.get("TICKET_CATEGORY_ID"))
    staff_channel_id = _positive_id(env.get("TICKET_STAFF_CHANNEL_ID"))
    staff_role_id = _positive_id(env.get("TICKET_STAFF_ROLE_ID"))
    state_path = Path(env.get("TICKET_STATE_PATH", "./tickets_state.json"))
    if enabled and (category_id is None or staff_channel_id is None or staff_role_id is None):
        raise ValueError("Configure TICKET_CATEGORY_ID, TICKET_STAFF_CHANNEL_ID and TICKET_STAFF_ROLE_ID")
    return enabled, category_id, staff_channel_id, staff_role_id, state_path


class TicketManager:
    def __init__(self, bot, owner_id, env=None):
        self.bot = bot
        self.owner_id = int(owner_id)
        try:
            self.enabled, self.category_id, self.staff_channel_id, self.staff_role_id, self.path = load_ticket_settings(env)
        except ValueError:
            # Fail closed: a missing ticket setting must not prevent the rest of the bot from starting.
            LOG.warning("Tickets disabled: configure TICKET_CATEGORY_ID, TICKET_STAFF_CHANNEL_ID and TICKET_STAFF_ROLE_ID")
            self.enabled, self.category_id, self.staff_channel_id, self.staff_role_id = False, None, None, None
            self.path = Path((os.environ if env is None else env).get("TICKET_STATE_PATH", "./tickets_state.json"))
        self.archive_channel_id = _positive_id((os.environ if env is None else env).get("TICKET_ARCHIVE_CHANNEL_ID"))
        self.lock = asyncio.Lock()
        self.tickets = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            LOG.warning("Tickets state unreadable; tickets disabled until fixed")
            self.enabled = False
            return {}
        if not isinstance(data, dict):
            self.enabled = False
            return {}
        validated = {}
        for key, value in data.items():
            if (str(key).isdigit() and isinstance(value, dict)
                    and _positive_id(value.get("user_id"))
                    and _positive_id(value.get("guild_id"))):
                validated[str(key)] = value
        return validated

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(self.tickets, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(self.path)

    def can_claim(self, member):
        if member.id == self.owner_id:
            return True
        return isinstance(member, discord.Member) and (
            member.guild_permissions.manage_channels
            or any(role.id == self.staff_role_id for role in member.roles)
        )

    def ticket_for(self, guild_id, user_id):
        for channel_id, ticket in self.tickets.items():
            if ticket["guild_id"] == guild_id and ticket["user_id"] == user_id and ticket.get("open", True):
                return channel_id
        return None

    async def open_ticket(self, interaction, subject):
        language = interaction_language(interaction)
        if not self.enabled:
            return await interaction.response.send_message(translate(language, "⚠️ Tickets non configurés ou désactivés.", "⚠️ Tickets are disabled or missing configuration."), ephemeral=True)
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(translate(language, "❌ Utilise cette commande sur le serveur.", "❌ Use this command in your server."), ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        category = guild.get_channel(self.category_id)
        staff_channel = guild.get_channel(self.staff_channel_id)
        staff_role = guild.get_role(self.staff_role_id)
        if (not isinstance(category, discord.CategoryChannel) or staff_channel is None
                or staff_role is None or guild.me is None):
            return await interaction.followup.send(translate(language, "⚠️ Catégorie, salon staff ou rôle staff introuvable.", "⚠️ Ticket category, staff channel or staff role not found."), ephemeral=True)
        async with self.lock:
            existing = self.ticket_for(guild.id, interaction.user.id)
            if existing:
                channel = guild.get_channel(int(existing))
                if channel:
                    return await interaction.followup.send(translate(language, "📩 Tu as déjà un ticket : {channel}", "📩 You already have a ticket: {channel}").format(channel=channel.mention), ephemeral=True)
                # The previous ticket channel was deleted; discard the stale entry.
                self.tickets[existing]["open"] = False
                self._save()
            safe_subject = discord.utils.escape_mentions(discord.utils.escape_markdown(subject.strip()))[:180] or translate(language, "Demande d'assistance", "Support request")
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
            }
            channel = None
            try:
                channel = await guild.create_text_channel(
                    name=f"ticket-{interaction.user.id}",
                    category=category,
                    overwrites=overwrites,
                    reason="NeurixBot support ticket creation",
                )
                self.tickets[str(channel.id)] = {
                    "guild_id": guild.id, "user_id": interaction.user.id,
                    "subject": safe_subject, "claimed_by": None, "open": True
                }
                self._save()
                confirmation = discord.Embed(
                    title=translate(language, "🎫 Votre ticket a été pris en compte !", "🎫 Your ticket has been opened!"),
                    description=translate(language, "Un membre du staff va venir vous prendre en charge. Merci de patienter.", "A staff member will help you shortly. Thanks for your patience."),
                    color=0x57F287,
                )
                confirmation.add_field(name=translate(language, "Sujet", "Subject"), value=safe_subject, inline=False)
                confirmation.add_field(name=translate(language, "Salon privé", "Private channel"), value=channel.mention, inline=False)
                await channel.send(interaction.user.mention, embed=confirmation, view=TicketCloseView(self, language),
                                   allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
                notice = discord.Embed(
                    title=translate(language, "🔔 Nouveau ticket à prendre en charge", "🔔 New ticket awaiting staff"),
                    description=translate(language, "Ticket : {channel}\nUtilisateur : {user}\nSujet : {subject}", "Ticket: {channel}\nUser: {user}\nSubject: {subject}").format(channel=channel.mention, user=interaction.user.mention, subject=safe_subject),
                    color=0xFEE75C,
                )
                notice.set_footer(text=f"Ticket ID: {channel.id}")
                view = TicketClaimView(self, language=language)
                await staff_channel.send(content=staff_role.mention, embed=notice, view=view,
                                         allowed_mentions=discord.AllowedMentions(roles=[staff_role], users=False, everyone=False))
                await interaction.followup.send(embed=confirmation, ephemeral=True)
            except (discord.HTTPException, OSError):
                LOG.warning("Ticket creation or announcement failed", exc_info=True)
                # Do not leave a ticket silently without alerting the staff.
                if channel is not None:
                    try:
                        await channel.delete(reason="Ticket creation rolled back after error")
                    except discord.HTTPException:
                        LOG.warning("Could not remove incomplete ticket %s", channel.id)
                    self.tickets.pop(str(channel.id), None)
                    try:
                        self._save()
                    except OSError:
                        LOG.warning("Could not persist ticket rollback")
                await interaction.followup.send(translate(language, "⚠️ Impossible de créer le ticket. Préviens un administrateur.", "⚠️ Could not open the ticket. Please tell an administrator."), ephemeral=True)

    async def claim(self, interaction):
        language = interaction_language(interaction)
        if interaction.guild is None or not self.can_claim(interaction.user):
            return await interaction.response.send_message(translate(language, "⛔ Réservé aux membres du staff.", "⛔ Only staff can claim tickets."), ephemeral=True)
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message(translate(language, "⚠️ Message du ticket invalide.", "⚠️ Invalid ticket message."), ephemeral=True)
        footer = interaction.message.embeds[0].footer.text or ""
        if not footer.startswith("Ticket ID: "):
            return await interaction.response.send_message(translate(language, "⚠️ Identifiant du ticket absent.", "⚠️ Missing ticket ID."), ephemeral=True)
        ticket_id = footer.removeprefix("Ticket ID: ").strip()
        async with self.lock:
            record = self.tickets.get(ticket_id)
            if not record or record.get("guild_id") != interaction.guild.id or not record.get("open", True):
                return await interaction.response.send_message(translate(language, "⚠️ Ticket introuvable ou fermé.", "⚠️ Ticket not found or closed."), ephemeral=True)
            if record.get("claimed_by"):
                return await interaction.response.send_message(
                    translate(language, "ℹ️ Ce ticket est déjà pris en charge par <@{staff}>.", "ℹ️ This ticket has already been claimed by <@{staff}>.").format(staff=int(record["claimed_by"])), ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none())
            channel = interaction.guild.get_channel(int(ticket_id))
            if channel is None:
                return await interaction.response.send_message(translate(language, "⚠️ Salon du ticket introuvable.", "⚠️ Ticket channel not found."), ephemeral=True)
            record["claimed_by"] = interaction.user.id
            try:
                self._save()
            except OSError:
                record["claimed_by"] = None
                return await interaction.response.send_message(translate(language, "⚠️ Impossible d'enregistrer la prise en charge.", "⚠️ Could not save the ticket assignment."), ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            notice = discord.Embed.from_dict(interaction.message.embeds[0].to_dict())
            notice.title = translate(language, "✅ Ticket pris en charge", "✅ Ticket claimed")
            notice.color = discord.Color.green()
            notice.add_field(name=translate(language, "Staff responsable", "Assigned staff"), value=interaction.user.mention, inline=False)
            try:
                await interaction.message.edit(embed=notice, view=TicketClaimView(self, disabled=True, language=language))
                await channel.send(translate(language, "✅ {staff} prend en charge ce ticket.", "✅ {staff} is handling this ticket.").format(staff=interaction.user.mention),
                                   allowed_mentions=discord.AllowedMentions(users=[interaction.user], roles=False, everyone=False))
            except discord.HTTPException:
                LOG.warning("Ticket claimed but announcement/edit failed for %s", ticket_id)
            await interaction.followup.send(translate(language, "✅ Ticket pris en charge !", "✅ Ticket claimed!"), ephemeral=True)


class TicketClaimView(discord.ui.View):
    def __init__(self, manager, disabled=False, language="fr"):
        super().__init__(timeout=None)
        self.manager = manager
        self.claim_button.disabled = disabled
        self.claim_button.label = translate(language, "Prendre en charge", "Claim ticket")

    @discord.ui.button(label="Prendre en charge", style=discord.ButtonStyle.success,
                       emoji="🙋", custom_id=CLAIM_ID)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.manager.claim(interaction)


def register_ticket_commands(bot, owner_id):
    manager = TicketManager(bot, owner_id)
    group = app_commands.Group(name="ticket", description="Assistance et support")

    @group.command(name="ouvrir", description="Ouvrir un ticket privé pour contacter le staff")
    @app_commands.describe(sujet="Décris brièvement ta demande")
    async def ouvrir(interaction: discord.Interaction, sujet: str = ""):
        await manager.open_ticket(interaction, sujet)

    @group.command(name="fermer", description="Confirmer la fermeture d'un ticket et archiver sa transcription")
    async def fermer(interaction: discord.Interaction):
        await request_close(manager, interaction)

    @group.command(name="panel", description="Publier ou mettre à jour le panneau permanent du support")
    @app_commands.describe(salon="Salon où afficher le panneau")
    async def panel(interaction: discord.Interaction, salon: discord.TextChannel):
        await publish_panel(manager, interaction, salon)

    bot.tree.add_command(group)
    if manager.enabled:
        # Persistent views must be installed inside setup_hook, before Discord events dispatch.
        original_setup_hook = bot.setup_hook

        async def setup_with_tickets():
            bot.add_view(TicketClaimView(manager))
            bot.add_view(TicketCloseView(manager))
            bot.add_view(TicketPanelView(manager))
            await original_setup_hook()

        bot.setup_hook = setup_with_tickets
    return manager
