import asyncio
import hashlib
import hmac
import json
import math
import os
import random
import re
import secrets
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands
from dotenv import load_dotenv
from openai import OpenAI

from tickets import register_ticket_commands
from guild_language import interaction_language, register_language_command, translate
from discord import app_commands 

load_dotenv()

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID") or "0")
TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BOT_START_TIME = datetime.now(timezone.utc)
DYNAMIC_ROLES_PATH = os.path.join(
    os.path.dirname(__file__),
    "dynamic_roles.json",
)
STAFF_SECURITY_PATH = os.path.join(
    os.path.dirname(__file__),
    "staff_security.json",
)
GIVEAWAY_STATE_PATH = os.path.join(os.path.dirname(__file__), "giveaways.json")
STAFF_SESSION_HOURS = 24
STAFF_LOGIN_MAX_ATTEMPTS = 5
STAFF_LOGIN_LOCK_MINUTES = 15
STAFF_LOGIN_WINDOW_MINUTES = 15
STAFF_ROLE_LEVEL_RANK = {"Staff": 1, "Admin": 2, "Critical": 3}
STAFF_PERMISSION_LEVELS = {
    "giveaway_create": "Staff",
    "giveaway_cancel": "Staff",
    "giveaway_end": "Staff",
    "giveaway_reroll": "Staff",
    "giveaway_info": "Staff",
    "say": "Admin",
    "kick": "Critical",
    "ban": "Critical",
    "unban": "Critical",
    "timeout": "Critical",
    "untimeout": "Critical",
}

def _format_giveaway_duration(duration):
    remaining = int(duration.total_seconds())
    units =  units = (
        (2592000, "mois"),
        (604800, "semaine"),
        (86400, "jour"),
        (3600, "heure"),
        (60, "minute"),
        (1, "seconde"),
    )

    parts = []
    for seconds, label in units:
        count, remaining = divmod(remaining, seconds)
        if count:
            parts.append(f"{count} {label}{'' if count == 1 else 's'}")
        if len(parts) == 2:
            break
    return " ".join(parts) or "0 seconde"


def build_giveaway_preview_embed(draft):
    end_timestamp = int(draft["end_at"].timestamp())
    lines = [
        f"**{draft['prize']}**",
        "",
        "Clique sur 🎉 pour participer !",
        "",
        f"Gagnants : **{draft['winner_count']}**",
        f"Durée : **{_format_giveaway_duration(draft['duration'])}**",
        f"Fin : <t:{end_timestamp}:F>",
        f"Salon : {draft['channel_mention']}",
    ]
    if draft.get("excluded_role_mention"):
        lines.append(f"Ne doit pas avoir le rôle : {draft['excluded_role_mention']}")
    return discord.Embed(
        title="🎁 Giveaway",
        description="\n".join(lines),
        color=0x5865F2,
    )


async def _publish_giveaway_draft(interaction, draft):
    guild = interaction.guild
    if guild is None or str(guild.id) != draft["guild_id"]:
        return None, "Cette configuration n’appartient plus à ce serveur."
    channel = guild.get_channel(int(draft["channel_id"]))
    if channel is None or not hasattr(channel, "send"):
        return None, "Le salon sélectionné n’existe plus ou n’est plus accessible."
    if not _bot_can_manage_giveaway_channel(guild, channel):
        return None, "Le bot doit pouvoir voir le salon, envoyer des messages et intégrer des liens/composants."
    try:
        duration = parse_giveaway_duration(draft["duration_text"])
    except ValueError as exc:
        return None, str(exc)
    prize = str(draft["prize"]).strip()
    winner_count = int(draft["winner_count"])
    if not prize or len(prize) > 1000 or not 1 <= winner_count <= 20:
        return None, "Vérifie la récompense et le nombre de gagnants avant de lancer."

    excluded_role_id = draft.get("excluded_role_id")
    if excluded_role_id is not None and guild.get_role(int(excluded_role_id)) is None:
        return None, "Le rôle sélectionné n’existe plus sur ce serveur."

    end_at = datetime.now(timezone.utc) + duration
    record = {
        "guild_id": str(guild.id),
        "channel_id": str(channel.id),
        "message_id": "",
        "host_id": str(draft["host_id"]),
        "prize": prize,
        "winner_count": winner_count,
        "end_at": end_at.isoformat(),
        "participants": [],
        "status": "active",
        "winners": [],
        "reroll_winners": [],
        "rerolls": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "logs": [],
    }
    if excluded_role_id is not None:
        record["must_not_have_role_id"] = str(excluded_role_id)
    try:
        message = await channel.send(embed=build_giveaway_embed(record), view=GiveawayView())
    except (discord.Forbidden, discord.HTTPException) as exc:
        _giveaway_log(record, "error", stage="create_message", error=type(exc).__name__)
        print(f"Giveaway: création Discord impossible ({type(exc).__name__}).")
        return None, "Discord n’a pas permis de publier le giveaway. Vérifie les permissions du salon."

    record["message_id"] = str(message.id)
    giveaways[record["message_id"]] = record
    _giveaway_log(record, "created", user_id=str(draft["host_id"]))
    try:
        save_giveaways()
    except OSError:
        giveaways.pop(record["message_id"], None)
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        return None, "Le giveaway n’a pas pu être sauvegardé."
    try:
        await message.edit(
            embed=build_giveaway_embed(record),
            view=GiveawayView(len(record["participants"])),
        )
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        _giveaway_log(record, "error", stage="create_embed_update", error=type(exc).__name__)
        try:
            save_giveaways()
        except OSError:
            pass
    schedule_giveaway(record["message_id"])
    return message, None


class GiveawayEditModal(discord.ui.Modal, title="Modifier le giveaway"):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        self.prize_input = discord.ui.TextInput(
            label="🎁 Récompense",
            default=setup_view.draft["prize"],
            max_length=1000,
        )
        self.winner_count_input = discord.ui.TextInput(
            label="🏆 Nombre de gagnants (1–20)",
            default=str(setup_view.draft["winner_count"]),
            max_length=2,
        )
        self.duration_input = discord.ui.TextInput(
            label="⏰ Durée (ex. 30m, 2h, 22d)",
            default=setup_view.draft["duration_text"],
            max_length=32,
        )
        self.add_item(self.prize_input)
        self.add_item(self.winner_count_input)
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not authorize_staff_action(interaction.user, "giveaway_create"):
            await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
            return
        prize = str(self.prize_input.value).strip()
        try:
            winner_count = int(self.winner_count_input.value)
            duration_text = str(self.duration_input.value).strip()
            duration = parse_giveaway_duration(duration_text)
        except (TypeError, ValueError) as exc:
            await _giveaway_ephemeral(interaction, f"❌ Paramètres invalides : {exc}")
            return
        if not prize or len(prize) > 1000 or not 1 <= winner_count <= 20:
            await _giveaway_ephemeral(interaction, "❌ Vérifie la récompense et choisis entre 1 et 20 gagnants.")
            return

        async with self.setup_view.lock:
            if self.setup_view.closed or self.setup_view.started:
                await _giveaway_ephemeral(interaction, "Cette configuration n’est plus modifiable.")
                return
            self.setup_view.draft.update({
                "prize": prize,
                "winner_count": winner_count,
                "duration_text": duration_text,
                "duration": duration,
                "end_at": datetime.now(timezone.utc) + duration,
            })
            if not await _giveaway_defer(interaction):
                return
            try:
                if self.setup_view.preview_message is not None:
                    await self.setup_view.preview_message.edit(
                        embed=build_giveaway_preview_embed(self.setup_view.draft),
                        view=self.setup_view,
                    )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                print(f"Giveaway: aperçu non actualisé après édition ({type(exc).__name__}).")
            await _giveaway_ephemeral(interaction, "✅ Aperçu mis à jour.")


class GiveawaySetupView(discord.ui.View):
    def __init__(self, draft):
        super().__init__(timeout=900)
        self.draft = draft
        self.preview_message = None
        self.lock = asyncio.Lock()
        self.started = False
        self.closed = False

    async def on_timeout(self):
        self.closed = True
        if self.preview_message is not None:
            try:
                await self.preview_message.edit(view=None)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

    @discord.ui.button(label="🔧 Edit", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not authorize_staff_action(interaction.user, "giveaway_create"):
            await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
            return
        if self.closed or self.started:
            await _giveaway_ephemeral(interaction, "Cette configuration n’est plus modifiable.")
            return
        await interaction.response.send_modal(GiveawayEditModal(self))

    @discord.ui.button(label="▶️ Start", style=discord.ButtonStyle.success, row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not authorize_staff_action(interaction.user, "giveaway_create"):
            await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
            return
        if not await _giveaway_defer(interaction):
            return
        async with self.lock:
            if self.closed or self.started:
                await _giveaway_ephemeral(interaction, "Cette configuration a déjà été fermée ou lancée.")
                return
            self.started = True
            message, error_message = await _publish_giveaway_draft(interaction, self.draft)
            if message is None:
                self.started = False
                await _giveaway_ephemeral(interaction, f"❌ {error_message}")
                return
            self.closed = True
            await _giveaway_ephemeral(interaction, f"✅ Giveaway publié dans <#{self.draft['channel_id']}> : {message.jump_url}")
            if self.preview_message is not None:
                try:
                    await self.preview_message.edit(content="✅ Giveaway publié.", embed=None, view=None)
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"Giveaway: fermeture de l’aperçu impossible ({type(exc).__name__}).")

    @discord.ui.button(label="✨ Premium: Customize Message", style=discord.ButtonStyle.primary, row=1)
    async def premium_customize(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not authorize_staff_action(interaction.user, "giveaway_create"):
            await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
            return
        await _giveaway_ephemeral(interaction, "✨ La personnalisation Premium sera disponible ultérieurement.")

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not authorize_staff_action(interaction.user, "giveaway_create"):
            await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
            return
        async with self.lock:
            if self.started:
                await _giveaway_ephemeral(interaction, "Le giveaway est déjà en cours de lancement.")
                return
            self.closed = True
            await interaction.response.edit_message(content="Création du giveaway annulée.", embed=None, view=None)

_staff_session_tasks = {}

dynamic_roles = {} 
staff_security_state = {"members": {}, "role_ids": [], "logs": []}


def parse_staff_role_ids(raw_value):
    if raw_value is None:
        return []

    if isinstance(raw_value, list):
        items = raw_value
    elif isinstance(raw_value, str):
        items = [item.strip() for item in re.split(r"[,;\s]+", raw_value.strip()) if item.strip()]
    else:
        items = [str(raw_value).strip()]

    normalized = []
    for item in items:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized


def load_staff_role_levels_from_environment():
    role_levels = {}
    for env_name, level in (
        ("STAFF_ROLE_IDS", "Staff"),
        ("ADMIN_ROLE_IDS", "Admin"),
        ("CRITICAL_ROLE_IDS", "Critical"),
    ):
        for role_id in parse_staff_role_ids(os.getenv(env_name, "")):
            role_levels[str(role_id)] = level
    return role_levels


def load_staff_security_state():
    environment_levels = load_staff_role_levels_from_environment()
    default_state = {
        "members": {},
        "role_ids": parse_staff_role_ids(os.getenv("STAFF_ROLE_IDS", "")),
        "role_levels": environment_levels,
        "staff_channels": {},
        "staff_channel_previous": {},
        "logs": [],
        "login_attempts": {},
    }

    if not os.path.exists(STAFF_SECURITY_PATH):
        return default_state

    try:
        with open(STAFF_SECURITY_PATH, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return default_state

    if not isinstance(payload, dict):
        return default_state

    members = payload.get("members", {})
    role_ids = payload.get("role_ids", [])
    role_levels = payload.get("role_levels", {})
    logs = payload.get("logs", [])
    login_attempts = payload.get("login_attempts", {})
    staff_channels = payload.get("staff_channels", {})
    staff_channel_previous = payload.get("staff_channel_previous", {})
    if not isinstance(role_levels, dict):
        role_levels = {}
    role_levels = {**environment_levels, **role_levels}
    for role_id in parse_staff_role_ids(role_ids):
        role_levels.setdefault(str(role_id), "Staff")

    return {
        "members": members if isinstance(members, dict) else {},
        "role_ids": parse_staff_role_ids(role_ids),
        "role_levels": role_levels,
        "staff_channels": staff_channels if isinstance(staff_channels, dict) else {},
        "staff_channel_previous": staff_channel_previous if isinstance(staff_channel_previous, dict) else {},
        "logs": logs if isinstance(logs, list) else [],
        "login_attempts": login_attempts if isinstance(login_attempts, dict) else {},
    }


def save_staff_security_state():
    with open(STAFF_SECURITY_PATH, "w", encoding="utf-8") as file:
        json.dump(staff_security_state, file, indent=2, ensure_ascii=False)


def hash_staff_secret(secret, salt=None):
    normalized = str(secret).strip()
    if not normalized:
        raise ValueError("Secret staff vide")
    salt_bytes = salt if isinstance(salt, bytes) else bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        salt_bytes,
        310000,
    )
    return f"pbkdf2_sha256$310000${salt_bytes.hex()}${digest.hex()}"


def generate_staff_secret():
    token = secrets.token_urlsafe(18)
    token = re.sub(r"[^A-Za-z0-9]", "", token).upper()
    if len(token) < 20:
        token = (token + "X" * 20)[:20]
    return token[:24]


def register_staff_member(user_id, secret, staff_id=None):
    member_id = str(user_id)
    normalized_staff_id = str(staff_id if staff_id is not None else user_id).strip()
    if not normalized_staff_id or len(normalized_staff_id) > 64:
        raise ValueError("Staff ID invalide")
    existing_record = staff_security_state.get("members", {}).get(member_id)
    if existing_record and str(existing_record.get("staff_id", member_id)) != normalized_staff_id:
        raise ValueError("Staff ID existant non modifiable")
    existing_owner, _ = _find_staff_account(normalized_staff_id)
    if existing_owner is not None and existing_owner != member_id:
        raise ValueError("Staff ID déjà lié à un autre compte Discord")
    secret_value = str(secret).strip()
    record = staff_security_state.setdefault("members", {}).setdefault(member_id, {})
    record["secret_hash"] = hash_staff_secret(secret_value)
    record.pop("secret_salt", None)
    record["staff_id"] = normalized_staff_id
    record["discord_user_id"] = member_id
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    record["failed_attempts"] = 0
    record["session_expires_at"] = None
    record.pop("lock_until", None)
    save_staff_security_state()
    return record


def _verify_secret_hash(record, secret):
    stored_hash = record.get("secret_hash")
    if not stored_hash:
        return False

    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        computed = hashlib.pbkdf2_hmac(
            "sha256",
            str(secret).strip().encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        ).hex()
        return hmac.compare_digest(digest_hex, computed)
    except (ValueError, TypeError):
        # Verify the legacy fixed-salt hash once, then migrate it on successful login.
        if re.fullmatch(r"[0-9a-fA-F]{64}", str(stored_hash)):
            legacy_salt = str(record.get("secret_salt") or "neurixbot-v1-staff")
            legacy_digest = hashlib.pbkdf2_hmac(
                "sha256",
                str(secret).strip().encode("utf-8"),
                legacy_salt.encode("utf-8"),
                200000,
            ).hex()
            return hmac.compare_digest(str(stored_hash), legacy_digest)
        return False


def verify_staff_secret(user_id, secret, staff_id=None):
    member_id = str(user_id)
    record = staff_security_state.get("members", {}).get(member_id, {})
    if staff_id is not None and str(record.get("staff_id", member_id)) != str(staff_id).strip():
        return False
    return _verify_secret_hash(record, secret)


def is_staff_login_locked(user_id):
    attempts = staff_security_state.get("login_attempts", {}).get(str(user_id), {})
    lock_until = attempts.get("lock_until")
    if not lock_until:
        return False
    try:
        lock_time = datetime.fromisoformat(lock_until)
        if lock_time.tzinfo is None:
            lock_time = lock_time.replace(tzinfo=timezone.utc)
        return lock_time > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _record_staff_login_failure(user_id, staff_id):
    member_id = str(user_id)
    attempts = staff_security_state.setdefault("login_attempts", {}).setdefault(member_id, {})
    now = datetime.now(timezone.utc)
    try:
        window_start = datetime.fromisoformat(attempts.get("window_started_at", ""))
    except (TypeError, ValueError):
        window_start = now
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    try:
        old_lock = datetime.fromisoformat(attempts.get("lock_until", ""))
    except (TypeError, ValueError):
        old_lock = None
    if old_lock is not None and old_lock <= now:
        attempts.clear()
        window_start = now
    if now - window_start > timedelta(minutes=STAFF_LOGIN_WINDOW_MINUTES):
        attempts["count"] = 0
        window_start = now
    attempts["window_started_at"] = window_start.isoformat()
    attempts["count"] = int(attempts.get("count", 0)) + 1
    if attempts["count"] >= STAFF_LOGIN_MAX_ATTEMPTS:
        attempts["lock_until"] = (now + timedelta(minutes=STAFF_LOGIN_LOCK_MINUTES)).isoformat()
    log_staff_security_event(
        "staff_login",
        member_id,
        "denied",
        "invalid_staff_id_or_secret",
        staff_id=staff_id,
        command="staff login",
        success=False,
    )
    save_staff_security_state()


def login_staff_member(user_id, secret, expires_in_hours=STAFF_SESSION_HOURS, staff_id=None):
    member_id = str(user_id)
    if is_staff_login_locked(member_id):
        log_staff_security_event("staff_login", member_id, "denied", "temporary_lockout", staff_id=staff_id, command="staff login", success=False)
        return False

    record = staff_security_state.get("members", {}).get(member_id, {})
    expected_staff_id = str(record.get("staff_id", member_id))
    if staff_id is not None and expected_staff_id != str(staff_id).strip():
        _record_staff_login_failure(member_id, staff_id)
        return False
    if not verify_staff_secret(member_id, secret, expected_staff_id):
        _record_staff_login_failure(member_id, staff_id or expected_staff_id)
        return False

    record = staff_security_state.setdefault("members", {}).setdefault(member_id, {})
    record["staff_id"] = expected_staff_id
    # Upgrade a legacy fixed-salt hash to a per-secret random salt after verification.
    if not str(record.get("secret_hash", "")).startswith("pbkdf2_sha256$"):
        record["secret_hash"] = hash_staff_secret(secret)
        record.pop("secret_salt", None)
    record["last_login_at"] = datetime.now(timezone.utc).isoformat()
    record["session_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
    ).isoformat()
    record["failed_attempts"] = 0
    staff_security_state.setdefault("login_attempts", {}).pop(member_id, None)
    log_staff_security_event("staff_login", member_id, "granted", "session_created", staff_id=expected_staff_id, command="staff login", success=True)
    save_staff_security_state()
    return True


def has_active_staff_session(user_id):
    record = staff_security_state.get("members", {}).get(str(user_id), {})
    expires_at = record.get("session_expires_at")
    if not expires_at:
        return False

    try:
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def log_staff_security_event(action, user_id, status, details=None, *, staff_id=None, command=None, target_id=None, success=None):
    record = staff_security_state.get("members", {}).get(str(user_id), {})
    normalized_status = str(status).lower()
    if success is None:
        success = normalized_status in {"granted", "success", "allowed", "revoked"}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user_id": str(user_id),
        "discord_user_id": str(user_id),
        "staff_id": str(staff_id or record.get("staff_id") or ""),
        "command": command or str(action),
        "target_id": str(target_id) if target_id is not None else None,
        "success": bool(success),
        "status": status,
        "event": str(action),
        "reason": details or "",
        "details": details or "",
    }
    staff_security_state.setdefault("logs", []).append(entry)
    if len(staff_security_state["logs"]) > 1000:
        del staff_security_state["logs"][:-1000]
    save_staff_security_state()
    return entry


def revoke_staff_session(user_id, staff_id=None):
    member_id = str(user_id)
    record = staff_security_state.get("members", {}).get(member_id)
    if not record:
        return False
    if staff_id is not None and str(record.get("staff_id", member_id)) != str(staff_id).strip():
        return False
    record["session_expires_at"] = None
    log_staff_security_event("session", member_id, "revoked", "manual_revocation", staff_id=record.get("staff_id"), command="staff revoke", success=True)
    save_staff_security_state()
    return True


def staff_role_level_for_member(member):
    if member is None:
        return None
    role_levels = staff_security_state.get("role_levels", {})
    legacy_staff_roles = set(parse_staff_role_ids(staff_security_state.get("role_ids", [])))
    highest = 0
    for role in getattr(member, "roles", []) or []:
        role_id = str(getattr(role, "id", ""))
        level = role_levels.get(role_id)
        if level is None and role_id.isdigit() and int(role_id) in legacy_staff_roles:
            level = "Staff"
        highest = max(highest, STAFF_ROLE_LEVEL_RANK.get(str(level), 0))
    return next((level for level, rank in reversed(list(STAFF_ROLE_LEVEL_RANK.items())) if rank == highest), None)


def configure_staff_role(role_id, level):
    if level not in STAFF_ROLE_LEVEL_RANK:
        raise ValueError("Niveau staff invalide")
    role_id = str(int(role_id))
    staff_security_state.setdefault("role_levels", {})[role_id] = level
    staff_security_state["role_ids"] = sorted(
        set(parse_staff_role_ids(staff_security_state.get("role_ids", []))) | {int(role_id)}
    )
    save_staff_security_state()


def get_configured_staff_channel_ids(guild_id):
    channels = staff_security_state.get("staff_channels", {}).get(str(guild_id), {})
    if isinstance(channels, dict):
        return [int(channel_id) for channel_id in channels]
    return [int(channel_id) for channel_id in channels]


def _bot_can_manage_channel_permissions(channel):
    guild = getattr(channel, "guild", None)
    bot_member = getattr(guild, "me", None) if guild else None
    if guild is None or bot_member is None:
        return False
    permissions = channel.permissions_for(bot_member)
    return bool(permissions.view_channel and permissions.manage_roles)


def _permission_pair(overwrite):
    return {
        "view_channel": overwrite.view_channel,
        "read_message_history": overwrite.read_message_history,
    }


async def _set_channel_overwrite_pair(channel, target, values, reason):
    overwrite = channel.overwrites_for(target)
    overwrite.view_channel = values.get("view_channel")
    overwrite.read_message_history = values.get("read_message_history")
    if overwrite.is_empty():
        await channel.set_permissions(target, overwrite=None, reason=reason)
    else:
        await channel.set_permissions(target, overwrite=overwrite, reason=reason)


def _security_roles_for_guild(guild):
    role_ids = set(parse_staff_role_ids(staff_security_state.get("role_ids", [])))
    role_ids.update(int(role_id) for role_id in staff_security_state.get("role_levels", {}))
    roles = []
    for role_id in role_ids:
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


async def add_staff_channel(guild, channel):
    if guild is None or channel is None or not callable(getattr(channel, "set_permissions", None)):
        return False, "Salon ou serveur invalide."
    if not _bot_can_manage_channel_permissions(channel):
        return False, "Le bot doit disposer de Voir le salon et Gérer les rôles."

    guild_id = str(guild.id)
    channel_id = str(channel.id)
    server_channels = staff_security_state.setdefault("staff_channels", {}).setdefault(guild_id, {})
    if channel_id in server_channels:
        return True, "Ce salon est déjà configuré."

    targets = [guild.default_role, *_security_roles_for_guild(guild)]
    original = {str(target.id): _permission_pair(channel.overwrites_for(target)) for target in targets}
    updated = []
    try:
        for target in targets:
            await _set_channel_overwrite_pair(
                channel,
                target,
                {"view_channel": False, "read_message_history": False},
                "Configuration Staff Security : salon privé par session",
            )
            updated.append(target)
    except (discord.Forbidden, discord.HTTPException) as exc:
        for target in reversed(updated):
            try:
                await _set_channel_overwrite_pair(channel, target, original[str(target.id)], "Rollback Staff Security")
            except (discord.Forbidden, discord.HTTPException):
                pass
        return False, f"Discord a refusé la configuration des permissions ({type(exc).__name__})."

    server_channels[channel_id] = {
        "original_overwrites": original,
        "configured_at": datetime.now(timezone.utc).isoformat(),
    }
    save_staff_security_state()
    if not await reconcile_staff_channel_members(guild):
        return True, "Salon verrouillé, mais la synchronisation de certains membres a échoué. Vérifiez l’intent Server Members et les permissions du bot."
    return True, "Salon configuré : visibilité réservée aux sessions staff actives."


async def remove_staff_channel(guild, channel):
    if guild is None or channel is None or not _bot_can_manage_channel_permissions(channel):
        return False, "Le bot doit disposer de Voir le salon et Gérer les rôles."
    guild_id = str(guild.id)
    channel_id = str(channel.id)
    server_channels = staff_security_state.get("staff_channels", {}).get(guild_id, {})
    record = server_channels.get(channel_id) if isinstance(server_channels, dict) else None
    if record is None:
        return False, "Ce salon n’est pas configuré comme salon Staff."

    previous_records = staff_security_state.setdefault("staff_channel_previous", {})
    for discord_user_id, previous_map in previous_records.items():
        previous = previous_map.get(f"{guild_id}:{channel_id}")
        if previous is None:
            continue
        try:
            member = guild.get_member(int(discord_user_id)) or await guild.fetch_member(int(discord_user_id))
            await _set_channel_overwrite_pair(channel, member, previous, "Retrait du salon Staff Security")
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as exc:
            return False, f"Restauration incomplète ({type(exc).__name__})."
        previous_map.pop(f"{guild_id}:{channel_id}", None)

    original = record.get("original_overwrites", {})
    for target_id, saved in original.items():
        target = guild.default_role if int(target_id) == int(guild.default_role.id) else guild.get_role(int(target_id))
        if target is None:
            continue
        try:
            await _set_channel_overwrite_pair(channel, target, saved, "Restauration permissions avant Staff Security")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return False, f"Restauration incomplète ({type(exc).__name__})."

    del server_channels[channel_id]
    if not server_channels:
        staff_security_state["staff_channels"].pop(guild_id, None)
    save_staff_security_state()
    return True, "Configuration Staff retirée et permissions précédentes restaurées."


async def set_staff_channel_visibility(member, visible):
    if member is None or getattr(member, "guild", None) is None:
        return False
    guild = member.guild
    guild_id = str(guild.id)
    configured_ids = set(get_configured_staff_channel_ids(guild_id))
    if not configured_ids:
        return True

    if not has_active_staff_session(member.id):
        visible = False
    if visible and staff_role_level_for_member(member) is None:
        visible = False

    previous_records = staff_security_state.setdefault("staff_channel_previous", {})
    previous_map = previous_records.setdefault(str(member.id), {})
    success = True
    for channel_id in configured_ids:
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                success = False
                continue
        if not _bot_can_manage_channel_permissions(channel):
            success = False
            continue

        key = f"{guild_id}:{channel_id}"
        if key not in previous_map:
            previous_map[key] = _permission_pair(channel.overwrites_for(member))
        target_values = (
            {"view_channel": True, "read_message_history": True}
            if visible
            else {"view_channel": False, "read_message_history": False}
        )
        current_overwrite = channel.overwrites_for(member)
        current_values = _permission_pair(current_overwrite)
        if current_values == target_values:
            continue
        try:
            await _set_channel_overwrite_pair(
                channel,
                member,
                target_values,
                "Staff Security session visibility update",
            )
        except (discord.Forbidden, discord.HTTPException):
            success = False
    if previous_map:
        save_staff_security_state()
    return success


async def _expire_staff_session(discord_user_id, expires_at):
    member_id = str(discord_user_id)
    try:
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        await asyncio.sleep(max(0, (expiry - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return

    record = staff_security_state.get("members", {}).get(member_id, {})
    if record.get("session_expires_at") != expires_at or has_active_staff_session(member_id):
        return
    record["session_expires_at"] = None
    log_staff_security_event("session", member_id, "expired", "session_expired", command="staff session expiry", success=False)
    save_staff_security_state()
    await sync_staff_channel_visibility_for_user(member_id, visible=False)


def schedule_staff_session_expiry(discord_user_id):
    member_id = str(discord_user_id)
    record = staff_security_state.get("members", {}).get(member_id, {})
    expires_at = record.get("session_expires_at")
    previous_task = _staff_session_tasks.get(member_id)
    if previous_task and not previous_task.done():
        previous_task.cancel()
    if expires_at:
        _staff_session_tasks[member_id] = asyncio.create_task(_expire_staff_session(member_id, expires_at))


async def sync_staff_channel_visibility_for_user(discord_user_id, visible=None):
    member_id = int(discord_user_id)
    overall_success = True
    for guild_id in list(staff_security_state.get("staff_channels", {})):
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            continue
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        should_show = has_active_staff_session(member_id) and staff_role_level_for_member(member) is not None
        if visible is not None:
            should_show = bool(visible and should_show)
        if not await set_staff_channel_visibility(member, should_show):
            overall_success = False
    return overall_success


async def reconcile_staff_channel_members(guild):
    if not get_configured_staff_channel_ids(guild.id):
        return True
    success = True
    try:
        async for member in guild.fetch_members(limit=None):
            if staff_role_level_for_member(member) is not None:
                should_see = has_active_staff_session(member.id)
                if not await set_staff_channel_visibility(member, should_see):
                    success = False
    except (discord.Forbidden, discord.HTTPException):
        return False
    return success


async def secure_staff_role_in_configured_channels(guild, role):
    guild_channels = staff_security_state.get("staff_channels", {}).get(str(guild.id), {})
    for channel_id, config in guild_channels.items():
        channel = guild.get_channel(int(channel_id))
        if channel is None or not _bot_can_manage_channel_permissions(channel):
            return False
        original = config.setdefault("original_overwrites", {})
        original.setdefault(str(role.id), _permission_pair(channel.overwrites_for(role)))
        try:
            await _set_channel_overwrite_pair(
                channel,
                role,
                {"view_channel": False, "read_message_history": False},
                "Staff Security : rôle visible uniquement avec session",
            )
        except (discord.Forbidden, discord.HTTPException):
            return False
    save_staff_security_state()
    return True


async def resume_staff_channel_sessions():
    for guild_id in list(staff_security_state.get("staff_channels", {})):
        guild = bot.get_guild(int(guild_id))
        if guild is not None and not await reconcile_staff_channel_members(guild):
            log_staff_security_event("staff_channel_sync", guild_id, "failed", "member_permission_reconciliation_failed", command="staff startup reconciliation", success=False)
    for discord_user_id, record in list(staff_security_state.get("members", {}).items()):
        if has_active_staff_session(discord_user_id):
            schedule_staff_session_expiry(discord_user_id)
        elif record.get("session_expires_at"):
            record["session_expires_at"] = None
            log_staff_security_event("session", discord_user_id, "expired", "expired_during_restart", command="staff session expiry", success=False)
            save_staff_security_state()


def authorize_staff_action(member, permission_name="general", role_ids=None, target_id=None):
    if member is None:
        return False

    member_id = str(getattr(member, "id", ""))
    if str(BOT_OWNER_ID) == member_id:
        log_staff_security_event(permission_name, member_id, "granted", "bot_owner", command=permission_name, target_id=target_id, success=True)
        return True

    role_level = staff_role_level_for_member(member)
    if role_ids is not None and not set(parse_staff_role_ids(role_ids)).intersection(
        {int(getattr(role, "id", 0)) for role in (getattr(member, "roles", []) or [])}
    ):
        role_level = None
    required_level = STAFF_PERMISSION_LEVELS.get(permission_name, "Staff")
    if role_level is None or STAFF_ROLE_LEVEL_RANK[role_level] < STAFF_ROLE_LEVEL_RANK[required_level]:
        log_staff_security_event(permission_name, member_id, "denied", "missing_required_staff_role_level", command=permission_name, target_id=target_id, success=False)
        return False

    member_record = staff_security_state.get("members", {}).get(member_id, {})
    staff_id = str(member_record.get("staff_id") or member_id)
    if not has_active_staff_session(member_id):
        log_staff_security_event(permission_name, member_id, "denied", "missing_or_expired_session", staff_id=staff_id, command=permission_name, target_id=target_id, success=False)
        return False

    log_staff_security_event(permission_name, member_id, "granted", "staff_session_valid", staff_id=staff_id, command=permission_name, target_id=target_id, success=True)
    return True


staff_security_state = load_staff_security_state()


class ParisTimezoneFallback(tzinfo):
    def utcoffset(self, dt):
        return self._offset_for(dt) if dt is not None else self._offset_for(datetime.now())

    def dst(self, dt):
        return timedelta(0) if not self._is_summer_time(dt) else timedelta(hours=1)

    def tzname(self, dt):
        return "CEST" if self._is_summer_time(dt) else "CET"

    @staticmethod
    def _is_summer_time(dt):
        if dt is None:
            return False
        naive = dt.replace(tzinfo=None)
        year = naive.year
        last_sunday_march = datetime(year, 3, 31)
        while last_sunday_march.weekday() != 6:
            last_sunday_march -= timedelta(days=1)
        last_sunday_october = datetime(year, 10, 31)
        while last_sunday_october.weekday() != 6:
            last_sunday_october -= timedelta(days=1)
        return last_sunday_march <= naive < last_sunday_october

    @staticmethod
    def _offset_for(dt):
        if dt is None:
            dt = datetime.now(timezone.utc)
        if ParisTimezoneFallback._is_summer_time(dt):
            return timezone(timedelta(hours=2)).utcoffset(dt)
        return timezone(timedelta(hours=1)).utcoffset(dt)


try:
    PARIS_TIMEZONE = ZoneInfo("Europe/Paris")
except ZoneInfoNotFoundError:
    PARIS_TIMEZONE = ParisTimezoneFallback()


def load_dynamic_roles():
    if not os.path.exists(DYNAMIC_ROLES_PATH):
        return {}

    try:
        with open(DYNAMIC_ROLES_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_dynamic_roles():
    with open(DYNAMIC_ROLES_PATH, "w", encoding="utf-8") as file:
        json.dump(dynamic_roles, file, indent=4)


dynamic_roles = load_dynamic_roles() 

DYNAMIC_EVENT_ROLE_KEYS = {
    "boost": "booster_role_id",
    "join": "join_role_id",
    "giveaway": "giveaway_winner_role_id",
}


def configure_dynamic_event_role(guild_id, event, role_id):
    if event not in DYNAMIC_EVENT_ROLE_KEYS:
        raise ValueError("Événement Dynamic Role inconnu")
    guild_config = dynamic_roles.setdefault(str(guild_id), {})
    event_roles = guild_config.setdefault("event_roles", {})
    key = DYNAMIC_EVENT_ROLE_KEYS[event]
    event_roles[key] = int(role_id) if role_id is not None else None
    save_dynamic_roles()
    return event_roles[key]


def get_dynamic_event_role_id(guild_id, event):
    key = DYNAMIC_EVENT_ROLE_KEYS.get(event)
    if key is None:
        return None
    return dynamic_roles.get(str(guild_id), {}).get("event_roles", {}).get(key)


def _bot_can_manage_dynamic_role(guild, role):
    bot_member = getattr(guild, "me", None)
    if bot_member is None or role is None or getattr(role, "managed", False):
        return False
    permissions = getattr(bot_member, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "manage_roles", False):
        return False
    return int(getattr(bot_member.top_role, "position", 0)) > int(getattr(role, "position", 0))


async def apply_dynamic_event_role(member, event, add=True):
    guild = getattr(member, "guild", None)
    if guild is None or event not in DYNAMIC_EVENT_ROLE_KEYS:
        return False
    role_id = get_dynamic_event_role_id(guild.id, event)
    if role_id is None:
        return False
    role = guild.get_role(int(role_id))
    if role is None or not _bot_can_manage_dynamic_role(guild, role):
        return False
    member_roles = getattr(member, "roles", []) or []
    has_role = any(getattr(existing, "id", None) == role.id for existing in member_roles)
    if add and has_role or not add and not has_role:
        return True
    action = "add_roles" if add else "remove_roles"
    reason = {
        "boost": "Dynamic Role: Server Boost" if add else "Dynamic Role: Server Boost ended",
        "join": "Dynamic Role: Join Role",
        "giveaway": "Dynamic Role: Giveaway Winner",
    }[event]
    try:
        await getattr(member, action)(role, reason=reason)
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


async def assign_giveaway_winner_role(member):
    return await apply_dynamic_event_role(member, "giveaway", add=True)


async def _configure_dynamic_event_role_from_interaction(interaction, event, role):
    if interaction.guild is None or not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("⛔ La permission Gérer les rôles est requise.", ephemeral=True)
        return
    if role is not None:
        if role.guild.id != interaction.guild.id or interaction.guild.get_role(role.id) is None:
            await interaction.response.send_message("❌ Le rôle n’existe pas sur ce serveur.", ephemeral=True)
            return
        if not _bot_can_manage_dynamic_role(interaction.guild, role):
            await interaction.response.send_message("❌ Le bot doit avoir Gérer les rôles et une position supérieure au rôle.", ephemeral=True)
            return
    configure_dynamic_event_role(interaction.guild.id, event, role.id if role else None)
    label = {"boost": "Server Boost", "join": "Join Role", "giveaway": "Giveaway Winner"}[event]
    value = role.mention if role else "Désactivé"
    await interaction.response.send_message(f"✅ Rôle `{label}` configuré : {value}", ephemeral=True)

print("Dynamic Roles chargés :", dynamic_roles) 


CURRENT_VERSION = "V1.5.0-dev"
PREVIOUS_VERSION = "V1.4.0"
PREVIOUS_PING = "131 ms"
AI_STATUS = "in progress"
BOT_VERSION = CURRENT_VERSION

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class NeurixBot(commands.Bot):
    async def setup_hook(self):
        registered_commands = self.tree.get_commands()
        print("🔌 Authentification Discord réussie ; préparation de la connexion...", flush=True)
        print(f"🔄 Synchronisation des slash commands ({len(registered_commands)} enregistrées)...", flush=True)

        try:
            synced_commands = await self.tree.sync()
        except Exception as exc:
            print(
                f"❌ Échec de la synchronisation des slash commands : "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            raise RuntimeError("La synchronisation initiale des slash commands a échoué.") from exc

        synced_count = len(synced_commands)
        registered_count = len(registered_commands)
        print(
            f"✅ Slash commands synchronisées : {synced_count}/{registered_count}",
            flush=True,
        )
        if synced_count != registered_count:
            registered_names = [command.name for command in registered_commands]
            synced_names = [command.name for command in synced_commands]
            raise RuntimeError(
                "Le nombre de slash commands synchronisées ne correspond pas au nombre "
                f"enregistré (locales={registered_count}, synchronisées={synced_count}; "
                f"locales={registered_names}, synchronisées={synced_names})."
            )


bot = NeurixBot(command_prefix="!", intents=intents)
register_language_command(bot)
ticket_manager = register_ticket_commands(bot, BOT_OWNER_ID)

staff_group = app_commands.Group(name="staff", description="Sécurité staff")
giveaway_group = app_commands.Group(name="giveaway", description="Gestion des giveaways")

GIVEAWAY_MAX_DURATION_SECONDS = 365 * 24 * 60 * 60
GIVEAWAY_BUTTON_CUSTOM_ID = "neurixbot:giveaway:participate"


def parse_giveaway_duration(value):
    compact = re.sub(r"\s+", "", str(value).lower())
    parts = list(re.finditer(r"(\d+)([smhd])", compact))
    if not parts or "".join(part.group(0) for part in parts) != compact:
        raise ValueError("Format attendu : 30m, 2h ou 1d12h.")

    unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = sum(int(part.group(1)) * unit_seconds[part.group(2)] for part in parts)
    if seconds < 10 or seconds > GIVEAWAY_MAX_DURATION_SECONDS:
        raise ValueError("La durée doit être comprise entre 10 secondes et 30 jours.")
    return timedelta(seconds=seconds)


def _giveaway_datetime(value):
    end_at = datetime.fromisoformat(str(value))
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    return end_at.astimezone(timezone.utc)


def load_giveaways():
    if not os.path.exists(GIVEAWAY_STATE_PATH):
        return {}
    try:
        with open(GIVEAWAY_STATE_PATH, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    loaded = {}
    for message_id, record in payload.items():
        if not isinstance(record, dict):
            continue
        try:
            required_ids = (record["guild_id"], record["channel_id"], message_id)
            if any(not str(value).isdigit() for value in required_ids):
                continue
            winner_count = int(record["winner_count"])
            prize = str(record["prize"]).strip()
            participants = record.get("participants", [])
            status = str(record.get("status", "active"))
            if winner_count < 1 or not prize or not isinstance(participants, list):
                continue
            if status not in {"active", "ending", "ended", "cancelled"}:
                continue
            normalized_participants = list(dict.fromkeys(
                str(participant) for participant in participants if str(participant).isdigit()
            ))
            _giveaway_datetime(record["end_at"])
        except (KeyError, TypeError, ValueError):
            continue
        normalized_winners = list(dict.fromkeys(
            str(winner) for winner in record.get("winners", []) if str(winner).isdigit()
        )) if isinstance(record.get("winners", []), list) else []
        normalized_reroll_winners = list(dict.fromkeys(
            str(winner) for winner in record.get("reroll_winners", []) if str(winner).isdigit()
        )) if isinstance(record.get("reroll_winners", []), list) else []
        normalized = dict(record)
        normalized.update({
            "guild_id": str(record["guild_id"]),
            "channel_id": str(record["channel_id"]),
            "message_id": str(message_id),
            "host_id": str(record.get("host_id", "")),
            "prize": prize,
            "winner_count": winner_count,
            "end_at": _giveaway_datetime(record["end_at"]).isoformat(),
            "participants": normalized_participants,
            "status": status,
            "winners": normalized_winners,
            "reroll_winners": normalized_reroll_winners,
            "rerolls": record.get("rerolls", []) if isinstance(record.get("rerolls", []), list) else [],
            "logs": record.get("logs", [])[-500:] if isinstance(record.get("logs", []), list) else [],
            "created_at": record.get("created_at", record.get("end_at")),
            "ended_at": record.get("ended_at"),
            "announcement_sent": bool(record.get("announcement_sent", False)),
        })
        loaded[str(message_id)] = normalized
    return loaded


def save_giveaways():
    temporary_path = f"{GIVEAWAY_STATE_PATH}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as file:
            json.dump(giveaways, file, indent=2, ensure_ascii=False)
        os.replace(temporary_path, GIVEAWAY_STATE_PATH)
    except OSError:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise


giveaways = load_giveaways()
giveaway_tasks = {}
giveaway_locks = {}
giveaway_last_clicks = {}
_giveaway_view_registered = False


def build_giveaway_embed(record, status="active", winner_ids=None):
    end_timestamp = int(_giveaway_datetime(record["end_at"]).timestamp())
    titles = {
        "active": "🎁 Giveaway",
        "ending": "🎁 Giveaway — tirage en cours",
        "ended": "🎁 Giveaway terminé",
        "cancelled": "🎁 Giveaway annulé",
    }
    if status not in titles:
        status = record.get("status", "active")
    embed = discord.Embed(
        title=titles[status],
        color=0x5865F2,
    )
    host_id = record.get("host_id")
    hosted_by = f"<@{host_id}>" if str(host_id).isdigit() else "Inconnu"
    lines = [
        f"🎁 **{record['prize']}**",
        "",
        "Clique sur 🎉 pour participer !" if status == "active" else "",
        "",
        f"Gagnants : **{record['winner_count']}**",
        f"Hébergé par : {hosted_by}",
        f"Fin : <t:{end_timestamp}:R>",
    ]
    excluded_role_id = record.get("must_not_have_role_id", record.get("excluded_role_id"))
    if excluded_role_id is not None and str(excluded_role_id).isdigit():
        lines.append(f"Ne doit pas avoir le rôle : <@&{excluded_role_id}>")
    if status == "ended":
        selected_winners = record.get("winners", []) if winner_ids is None else winner_ids
        result = ", ".join(f"<@{winner_id}>" for winner_id in selected_winners) or "Aucun gagnant : personne n’a participé."
        lines.append(f"\n🎉 Gagnant(s) : {result}")
    elif status == "cancelled":
        lines.append("\nGiveaway annulé.")
    reroll_winners = record.get("reroll_winners", [])
    if reroll_winners:
        lines.append(f"\n🔄 Nouveau(x) gagnant(s) : {', '.join(f'<@{winner_id}>' for winner_id in reroll_winners)}")
    embed.description = "\n".join(line for line in lines if line is not None)
    return embed


def _bot_can_manage_giveaway_channel(guild, channel):
    bot_member = getattr(guild, "me", None)
    if bot_member is None or channel is None or not hasattr(channel, "permissions_for"):
        return False
    try:
        permissions = channel.permissions_for(bot_member)
    except (AttributeError, discord.HTTPException):
        return False
    return all(
        getattr(permissions, permission, False)
        for permission in ("view_channel", "send_messages", "embed_links")
    )


def _giveaway_lock(message_id):
    return giveaway_locks.setdefault(str(message_id), asyncio.Lock())


def _giveaway_log(record, event, **details):
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": str(event),
        **details,
    }
    logs = record.setdefault("logs", [])
    logs.append(entry)
    del logs[:-500]
    return entry


async def _giveaway_defer(interaction):
    response = getattr(interaction, "response", None)
    if response is None:
        return False
    try:
        is_done = getattr(response, "is_done", None)
        if callable(is_done) and is_done() is True:
            return True
        await response.defer(ephemeral=True)
        return True
    except (AttributeError, discord.InteractionResponded, discord.NotFound, discord.HTTPException) as exc:
        print(f"Giveaway: acquittement Discord impossible ({type(exc).__name__}).")
        return False


async def _giveaway_ephemeral(interaction, message):
    try:
        is_done = getattr(interaction.response, "is_done", None)
        if callable(is_done) and is_done() is True:
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (AttributeError, discord.InteractionResponded, discord.NotFound, discord.HTTPException) as exc:
        print(f"Giveaway: réponse d’interaction impossible ({type(exc).__name__}).")


class GiveawayView(discord.ui.View):
    def __init__(self, participant_count=0):
        super().__init__(timeout=None)
        self.children[0].label = f"🎉 Enter Giveaway • {participant_count}"

    @discord.ui.button(
        label="🎉 Participer",
        style=discord.ButtonStyle.success,
        custom_id=GIVEAWAY_BUTTON_CUSTOM_ID,
    )
    async def participate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _giveaway_defer(interaction):
            return
        message = interaction.message
        message_id = str(getattr(message, "id", ""))
        async with _giveaway_lock(message_id):
            record = giveaways.get(message_id)
            if record is None or str(getattr(interaction.guild, "id", "")) != record["guild_id"]:
                await _giveaway_ephemeral(interaction, "Ce giveaway n’est plus actif.")
                return
            if record.get("status", "active") != "active":
                await _giveaway_ephemeral(interaction, "Les participations sont terminées.")
                return
            if getattr(interaction.user, "bot", False):
                await _giveaway_ephemeral(interaction, "Les bots ne peuvent pas participer.")
                return
            if _giveaway_datetime(record["end_at"]) <= datetime.now(timezone.utc):
                schedule_giveaway(message_id)
                await _giveaway_ephemeral(interaction, "Les participations sont terminées.")
                return
            if not _bot_can_manage_giveaway_channel(interaction.guild, interaction.channel):
                await _giveaway_ephemeral(interaction, "Le bot ne peut plus modifier ce message.")
                return

            participant_id = str(interaction.user.id)
            now = asyncio.get_running_loop().time()
            for recent_key, clicked_at in list(giveaway_last_clicks.items()):
                if now - clicked_at >= 5:
                    giveaway_last_clicks.pop(recent_key, None)
            click_key = (message_id, participant_id)
            if now - giveaway_last_clicks.get(click_key, float("-inf")) < 1.5:
                await _giveaway_ephemeral(interaction, "Clic déjà pris en compte. Attends un instant avant de modifier ta participation.")
                return
            giveaway_last_clicks[click_key] = now
            previous_participants = list(record["participants"])
            previous_logs = list(record.get("logs", []))
            if participant_id in record["participants"]:
                record["participants"].remove(participant_id)
                action = "left"
                reply = "Tu as quitté le giveaway."
            else:
                record["participants"].append(participant_id)
                action = "joined"
                reply = "Tu participes au giveaway !"
            _giveaway_log(record, "participation", user_id=participant_id, action=action)
            try:
                save_giveaways()
            except OSError:
                record["participants"] = previous_participants
                record["logs"] = previous_logs
                giveaway_last_clicks.pop(click_key, None)
                await _giveaway_ephemeral(interaction, "Impossible d’enregistrer ta participation. Réessaie plus tard.")
                return

            await _giveaway_ephemeral(interaction, reply)
            try:
                await message.edit(
                    embed=build_giveaway_embed(record),
                    view=GiveawayView(len(record["participants"])),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                _giveaway_log(record, "error", stage="participant_embed_update", error=type(exc).__name__)
                try:
                    save_giveaways()
                except OSError:
                    pass
                print(f"Giveaway {message_id}: mise à jour du compteur impossible ({type(exc).__name__}).")


async def finish_giveaway(message_id, bot_instance=None):
    key = str(message_id)
    async with _giveaway_lock(key):
        record = giveaways.get(key)
        if record is None or record.get("status", "active") in {"ended", "cancelled"}:
            return True
        if record.get("status", "active") not in {"active", "ending"}:
            return False

        if record.get("status", "active") == "active":
            unique_participants = list(dict.fromkeys(record.get("participants", [])))
            winners = random.sample(unique_participants, min(record["winner_count"], len(unique_participants)))
            updated = dict(record)
            updated["participants"] = unique_participants
            updated["winners"] = winners
            updated["status"] = "ending"
            updated["ended_at"] = datetime.now(timezone.utc).isoformat()
            updated["announcement_sent"] = False
            _giveaway_log(
                updated,
                "draw_started",
                participant_count=len(unique_participants),
                winners=winners,
            )
            giveaways[key] = updated
            try:
                save_giveaways()
            except OSError:
                giveaways[key] = record
                print(f"Giveaway {key}: tirage non sauvegardé; aucune action Discord exécutée.")
                return False
            record = updated

        winners = list(record.get("winners", []))
        client = bot_instance or bot
        guild = client.get_guild(int(record["guild_id"]))
        channel = guild.get_channel(int(record["channel_id"])) if guild else None
        if channel is None:
            channel = client.get_channel(int(record["channel_id"]))
        if guild is None or channel is None:
            _giveaway_log(record, "error", stage="finish", error="guild_or_channel_missing")
        elif not _bot_can_manage_giveaway_channel(guild, channel):
            _giveaway_log(record, "error", stage="finish", error="channel_permissions_missing")
            try:
                save_giveaways()
            except OSError:
                pass
            return False
        else:
            mentions = " ".join(f"<@{winner_id}>" for winner_id in winners)
            announcement = f"🎉 Giveaway terminé ! Gagnant(s) : {mentions}" if winners else "🎁 Giveaway terminé : aucun gagnant, personne n’a participé."
            allowed_mentions = discord.AllowedMentions(
                users=[discord.Object(id=int(winner_id)) for winner_id in winners],
                roles=False,
                everyone=False,
            )
            if not record.get("announcement_sent", False):
                try:
                    announcement_message = await channel.send(announcement, allowed_mentions=allowed_mentions)
                    record["announcement_sent"] = True
                    record["announcement_message_id"] = str(announcement_message.id)
                    _giveaway_log(record, "announcement_sent", winners=winners)
                    try:
                        save_giveaways()
                    except OSError:
                        print(f"Giveaway {key}: annonce envoyée mais sauvegarde de l’annonce impossible.")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    _giveaway_log(record, "error", stage="announcement", error=type(exc).__name__)
                    print(f"Giveaway {key}: annonce impossible ({type(exc).__name__}).")
                    try:
                        save_giveaways()
                    except OSError:
                        pass
                    return False

            try:
                message = await channel.fetch_message(int(record["message_id"]))
                await message.edit(embed=build_giveaway_embed(record, "ended", winners), view=None)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                _giveaway_log(record, "error", stage="close_message", error=type(exc).__name__)
                print(f"Giveaway {key}: fermeture du message impossible ({type(exc).__name__}).")

        configured_role_id = get_dynamic_event_role_id(record["guild_id"], "giveaway")
        role_results = record.setdefault("role_assignments", {})
        if configured_role_id is None:
            if winners:
                _giveaway_log(record, "role_assignment_skipped", reason="role_not_configured")
        elif guild is None:
            _giveaway_log(record, "error", stage="role_assignment", error="guild_missing")
        else:
            try:
                role = guild.get_role(int(configured_role_id))
                if role is None:
                    _giveaway_log(record, "error", stage="role_assignment", error="role_missing")
                elif not _bot_can_manage_dynamic_role(guild, role):
                    _giveaway_log(record, "error", stage="role_assignment", error="role_permissions_missing")
                else:
                    for winner_id in winners:
                        if role_results.get(winner_id) == "assigned":
                            continue
                        try:
                            member = guild.get_member(int(winner_id))
                            if member is None:
                                member = await guild.fetch_member(int(winner_id))
                            assigned = await assign_giveaway_winner_role(member)
                            role_results[winner_id] = "assigned" if assigned else "failed"
                            _giveaway_log(
                                record,
                                "role_assigned" if assigned else "error",
                                winner_id=winner_id,
                                stage="role_assignment",
                                **({} if assigned else {"error": "assignment_failed"}),
                            )
                        except Exception as exc:
                            role_results[winner_id] = "failed"
                            _giveaway_log(record, "error", stage="role_assignment", winner_id=winner_id, error=type(exc).__name__)
            except Exception as exc:
                _giveaway_log(record, "error", stage="role_assignment", error=type(exc).__name__)

        record["status"] = "ended"
        record["ended_at"] = record.get("ended_at") or datetime.now(timezone.utc).isoformat()
        _giveaway_log(record, "ended", winners=winners)
        try:
            save_giveaways()
        except OSError:
            record["status"] = "ending"
            print(f"Giveaway {key}: état final impossible à sauvegarder.")
            return False
        return True


async def _run_giveaway(message_id):
    key = str(message_id)
    try:
        while key in giveaways:
            remaining = (_giveaway_datetime(giveaways[key]["end_at"]) - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining)
            try:
                if await finish_giveaway(key):
                    return
            except Exception as exc:
                record = giveaways.get(key)
                if record is None:
                    return
                _giveaway_log(record, "error", stage="scheduled_finish", error=type(exc).__name__)
                try:
                    save_giveaways()
                except OSError:
                    pass
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        raise
    finally:
        if giveaway_tasks.get(key) is asyncio.current_task():
            giveaway_tasks.pop(key, None)


def schedule_giveaway(message_id):
    key = str(message_id)
    task = giveaway_tasks.get(key)
    record = giveaways.get(key, {})
    if record.get("status", "active") in {"active", "ending"} and (task is None or task.done()):
        giveaway_tasks[key] = asyncio.create_task(_run_giveaway(key))


async def _refresh_active_giveaway_displays():
    for message_id, record in list(giveaways.items()):
        if record.get("status", "active") != "active":
            continue
        guild = bot.get_guild(int(record["guild_id"]))
        channel = guild.get_channel(int(record["channel_id"])) if guild else None
        if guild is None or channel is None or not _bot_can_manage_giveaway_channel(guild, channel):
            continue
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(
                embed=build_giveaway_embed(record),
                view=GiveawayView(len(record["participants"])),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f"Giveaway {message_id}: rafraîchissement visuel impossible ({type(exc).__name__}).")


async def restore_giveaways():
    global _giveaway_view_registered
    if not _giveaway_view_registered:
        bot.add_view(GiveawayView())
        _giveaway_view_registered = True
    await _refresh_active_giveaway_displays()
    for message_id, record in list(giveaways.items()):
        if record.get("status", "active") in {"active", "ending"}:
            schedule_giveaway(message_id)

@giveaway_group.command(name="create", description="Configure et prévisualise un giveaway.")
@app_commands.describe(
    prize="Récompense du giveaway",
    winner_count="Nombre de gagnants (1 à 20)",
    duration="Durée (ex. 30m, 2h ou 1d12h; de 10 secondes à 30 jours)",
    channel="Salon où publier le giveaway",
    excluded_role="Rôle interdit pour ce giveaway (facultatif)",
)
async def giveaway_create(
    interaction: discord.Interaction,
    prize: str,
    winner_count: app_commands.Range[int, 1, 20],
    duration: str,
    channel: discord.TextChannel,
    excluded_role: discord.Role | None = None,
):
    if not authorize_staff_action(interaction.user, "giveaway_create"):
        await interaction.response.send_message("⛔ Une session Staff active est requise.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée dans un serveur.", ephemeral=True)
        return
    if channel.guild.id != interaction.guild.id:
        await interaction.response.send_message("❌ Le salon doit appartenir à ce serveur.", ephemeral=True)
        return
    if not hasattr(channel, "send") or not _bot_can_manage_giveaway_channel(interaction.guild, channel):
        await interaction.response.send_message("❌ Le bot doit pouvoir voir le salon, envoyer des messages et utiliser les composants.", ephemeral=True)
        return
    if excluded_role is not None and excluded_role.guild.id != interaction.guild.id:
        await interaction.response.send_message("❌ Le rôle doit appartenir à ce serveur.", ephemeral=True)
        return
    try:
        giveaway_duration = parse_giveaway_duration(duration)
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return
    normalized_prize = str(prize).strip()
    if not normalized_prize or len(normalized_prize) > 1000:
        await interaction.response.send_message("❌ La récompense doit contenir de 1 à 1000 caractères.", ephemeral=True)
        return

    draft = {
        "guild_id": str(interaction.guild.id),
        "host_id": str(interaction.user.id),
        "prize": normalized_prize,
        "winner_count": int(winner_count),
        "duration_text": duration.strip(),
        "duration": giveaway_duration,
        "end_at": datetime.now(timezone.utc) + giveaway_duration,
        "channel_id": str(channel.id),
        "channel_mention": channel.mention,
        "excluded_role_id": str(excluded_role.id) if excluded_role is not None else None,
        "excluded_role_mention": excluded_role.mention if excluded_role is not None else None,
    }
    view = GiveawaySetupView(draft)
    await interaction.response.send_message(
        embed=build_giveaway_preview_embed(draft),
        view=view,
        ephemeral=True,
    )
    try:
        view.preview_message = await interaction.original_response()
    except (discord.NotFound, discord.HTTPException) as exc:
        print(f"Giveaway: aperçu créé, mais sa référence n’a pas pu être récupérée ({type(exc).__name__}).")


@giveaway_group.command(name="cancel", description="Annule un giveaway actif par ID de message.")
@app_commands.describe(message_id="ID du message du giveaway")
async def giveaway_cancel(interaction: discord.Interaction, message_id: str):
    if not authorize_staff_action(interaction.user, "giveaway_cancel", target_id=message_id):
        await interaction.response.send_message("⛔ Une session Staff active est requise.", ephemeral=True)
        return
    if interaction.guild is None or not str(message_id).isdigit():
        await interaction.response.send_message("❌ Cette commande nécessite un serveur et un ID de message valide.", ephemeral=True)
        return
    key = str(message_id)
    record = giveaways.get(key)
    if record is None or record["guild_id"] != str(interaction.guild.id):
        await interaction.response.send_message("❌ Aucun giveaway actif correspondant dans ce serveur.", ephemeral=True)
        return
    if not await _giveaway_defer(interaction):
        return

    async with _giveaway_lock(key):
        record = giveaways.get(key)
        if record is None or record.get("status", "active") != "active":
            await _giveaway_ephemeral(interaction, "❌ Ce giveaway n’est plus actif.")
            return
        channel = interaction.guild.get_channel(int(record["channel_id"]))
        message = None
        if channel is not None:
            if not _bot_can_manage_giveaway_channel(interaction.guild, channel):
                await _giveaway_ephemeral(interaction, "❌ Le bot ne peut pas accéder ou modifier le message du giveaway.")
                return
            try:
                message = await channel.fetch_message(int(key))
            except discord.NotFound:
                _giveaway_log(record, "error", stage="cancel_fetch_message", error="message_missing")
            except (discord.Forbidden, discord.HTTPException) as exc:
                _giveaway_log(record, "error", stage="cancel_fetch_message", error=type(exc).__name__)
                await _giveaway_ephemeral(interaction, "❌ Discord n’a pas permis d’accéder au giveaway.")
                return

        previous_status = record.get("status", "active")
        record["status"] = "cancelled"
        record["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        _giveaway_log(record, "cancelled", user_id=str(interaction.user.id))
        try:
            save_giveaways()
        except OSError:
            record["status"] = previous_status
            record.pop("cancelled_at", None)
            if record.get("logs") and record["logs"][-1].get("event") == "cancelled":
                record["logs"].pop()
            await _giveaway_ephemeral(interaction, "❌ L’annulation n’a pas pu être sauvegardée.")
            return
        task = giveaway_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        if message is not None:
            try:
                await message.edit(embed=build_giveaway_embed(record, "cancelled"), view=None)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                _giveaway_log(record, "error", stage="cancel_edit_message", error=type(exc).__name__)
                print(f"Giveaway {key}: fermeture après annulation impossible ({type(exc).__name__}).")
                try:
                    save_giveaways()
                except OSError:
                    pass
        await _giveaway_ephemeral(interaction, "✅ Giveaway annulé.")


async def reroll_giveaway(message_id, bot_instance=None):
    key = str(message_id)
    async with _giveaway_lock(key):
        record = giveaways.get(key)
        if record is None or record.get("status") != "ended":
            return False, None, "Le reroll est disponible uniquement après la fin du giveaway."

        rerolls = record.setdefault("rerolls", [])
        pending = next(
            (entry for entry in reversed(rerolls) if isinstance(entry, dict) and entry.get("status") == "pending"),
            None,
        )
        if pending is None:
            excluded = set(record.get("winners", [])) | set(record.get("reroll_winners", []))
            eligible = [participant for participant in record.get("participants", []) if participant not in excluded]
            if not eligible:
                _giveaway_log(record, "reroll_unavailable", reason="no_new_eligible_participants")
                try:
                    save_giveaways()
                except OSError:
                    pass
                return False, None, "Aucun nouveau participant éligible pour le reroll."
            winner_id = random.choice(eligible)
            pending = {
                "winner_id": winner_id,
                "status": "pending",
                "selected_at": datetime.now(timezone.utc).isoformat(),
            }
            rerolls.append(pending)
            record.setdefault("reroll_winners", []).append(winner_id)
            _giveaway_log(record, "reroll_selected", winner_id=winner_id)
            try:
                save_giveaways()
            except OSError:
                rerolls.remove(pending)
                record["reroll_winners"].remove(winner_id)
                record["logs"].pop()
                return False, None, "Le reroll n’a pas pu être sauvegardé. Réessaie."

        winner_id = str(pending["winner_id"])
        client = bot_instance or bot
        guild = client.get_guild(int(record["guild_id"]))
        channel = guild.get_channel(int(record["channel_id"])) if guild else None
        if channel is None:
            _giveaway_log(record, "error", stage="reroll_announcement", error="channel_missing", winner_id=winner_id)
            try:
                save_giveaways()
            except OSError:
                pass
            return False, winner_id, "Salon introuvable; le reroll est conservé et pourra être retenté."
        if not _bot_can_manage_giveaway_channel(guild, channel):
            _giveaway_log(record, "error", stage="reroll_announcement", error="channel_permissions_missing", winner_id=winner_id)
            try:
                save_giveaways()
            except OSError:
                pass
            return False, winner_id, "Le bot ne peut pas envoyer dans le salon; le reroll est conservé pour être retenté."
        try:
            announcement = await channel.send(
                f"🔄 Nouveau gagnant du giveaway `{key}` : <@{winner_id}>",
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=int(winner_id))], roles=False, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            _giveaway_log(record, "error", stage="reroll_announcement", winner_id=winner_id, error=type(exc).__name__)
            try:
                save_giveaways()
            except OSError:
                pass
            return False, winner_id, "Annonce impossible; le reroll est conservé et pourra être retenté."

        pending["status"] = "announced"
        pending["announcement_message_id"] = str(announcement.id)
        pending["announced_at"] = datetime.now(timezone.utc).isoformat()
        _giveaway_log(record, "reroll_announced", winner_id=winner_id)
        try:
            save_giveaways()
        except OSError:
            print(f"Giveaway {key}: annonce du reroll envoyée mais état non sauvegardé.")

        try:
            role_id = get_dynamic_event_role_id(record["guild_id"], "giveaway")
            if role_id is not None and guild is not None:
                role = guild.get_role(int(role_id))
                if role is None:
                    _giveaway_log(record, "error", stage="reroll_role_assignment", winner_id=winner_id, error="role_missing")
                elif not _bot_can_manage_dynamic_role(guild, role):
                    _giveaway_log(record, "error", stage="reroll_role_assignment", winner_id=winner_id, error="role_permissions_missing")
                else:
                    member = guild.get_member(int(winner_id))
                    if member is None:
                        member = await guild.fetch_member(int(winner_id))
                    assigned = await assign_giveaway_winner_role(member)
                    record.setdefault("role_assignments", {})[winner_id] = "assigned" if assigned else "failed"
                    _giveaway_log(record, "role_assigned" if assigned else "error", stage="reroll_role_assignment", winner_id=winner_id)
        except Exception as exc:
            record.setdefault("role_assignments", {})[winner_id] = "failed"
            _giveaway_log(record, "error", stage="reroll_role_assignment", winner_id=winner_id, error=type(exc).__name__)
        try:
            save_giveaways()
        except OSError:
            pass
        return True, winner_id, "Reroll annoncé."


@giveaway_group.command(name="end", description="Termine immédiatement un giveaway actif.")
@app_commands.describe(message_id="ID du message du giveaway")
async def giveaway_end(interaction: discord.Interaction, message_id: str):
    if not authorize_staff_action(interaction.user, "giveaway_end", target_id=message_id):
        await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
        return
    if interaction.guild is None or not str(message_id).isdigit():
        await _giveaway_ephemeral(interaction, "❌ Cette commande nécessite un serveur et un ID valide.")
        return
    key = str(message_id)
    record = giveaways.get(key)
    if record is None or record["guild_id"] != str(interaction.guild.id):
        await _giveaway_ephemeral(interaction, "❌ Aucun giveaway correspondant dans ce serveur.")
        return
    if record.get("status", "active") not in {"active", "ending"}:
        await _giveaway_ephemeral(interaction, "❌ Ce giveaway n’est plus actif.")
        return
    if not await _giveaway_defer(interaction):
        return
    task = giveaway_tasks.pop(key, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    if not await finish_giveaway(key):
        schedule_giveaway(key)
        await _giveaway_ephemeral(interaction, "❌ La fin n’a pas pu être publiée; elle sera retentée automatiquement.")
        return
    await _giveaway_ephemeral(interaction, "✅ Giveaway terminé.")


@giveaway_group.command(name="reroll", description="Tire un nouveau gagnant d’un giveaway terminé.")
@app_commands.describe(message_id="ID du message du giveaway terminé")
async def giveaway_reroll(interaction: discord.Interaction, message_id: str):
    if not authorize_staff_action(interaction.user, "giveaway_reroll", target_id=message_id):
        await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
        return
    if interaction.guild is None or not str(message_id).isdigit():
        await _giveaway_ephemeral(interaction, "❌ Cette commande nécessite un serveur et un ID valide.")
        return
    record = giveaways.get(str(message_id))
    if record is None or record["guild_id"] != str(interaction.guild.id):
        await _giveaway_ephemeral(interaction, "❌ Aucun giveaway correspondant dans ce serveur.")
        return
    if not await _giveaway_defer(interaction):
        return
    success, winner_id, message = await reroll_giveaway(message_id)
    if success:
        record["reroll_winners"] = list(dict.fromkeys(record.get("reroll_winners", [])))
        channel = interaction.guild.get_channel(int(record["channel_id"]))
        if channel is not None:
            try:
                original_message = await channel.fetch_message(int(message_id))
                await original_message.edit(embed=build_giveaway_embed(record, "ended"), view=None)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                _giveaway_log(record, "error", stage="reroll_embed_update", error=type(exc).__name__)
                try:
                    save_giveaways()
                except OSError:
                    pass
        await _giveaway_ephemeral(interaction, f"✅ {message} Nouveau gagnant : <@{winner_id}>.")
    else:
        await _giveaway_ephemeral(interaction, f"❌ {message}")


@giveaway_group.command(name="info", description="Affiche les informations d’un giveaway.")
@app_commands.describe(message_id="ID du message du giveaway")
async def giveaway_info(interaction: discord.Interaction, message_id: str):
    if not authorize_staff_action(interaction.user, "giveaway_info", target_id=message_id):
        await _giveaway_ephemeral(interaction, "⛔ Une session Staff active est requise.")
        return
    if interaction.guild is None or not str(message_id).isdigit():
        await _giveaway_ephemeral(interaction, "❌ Cette commande nécessite un serveur et un ID valide.")
        return
    record = giveaways.get(str(message_id))
    if record is None or record["guild_id"] != str(interaction.guild.id):
        await _giveaway_ephemeral(interaction, "❌ Aucun giveaway correspondant dans ce serveur.")
        return
    embed = build_giveaway_embed(record, record.get("status", "active"))
    if record.get("status") == "ending":
        embed.title = "🎁 Giveaway — tirage en attente"
    embed.add_field(name="Statut", value=record.get("status", "active"), inline=True)
    embed.add_field(name="Participations enregistrées", value=str(len(record.get("participants", []))), inline=True)
    if record.get("logs"):
        latest = record["logs"][-1]
        embed.set_footer(text=f"Dernier événement : {latest.get('event', 'inconnu')} • {latest.get('at', '')}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(giveaway_group)

@bot.tree.command(name="ping", description="Vérifie si NeurixBot répond.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong !")

@bot.tree.command(name="status", description="Affiche le statut de NeurixBot.")
async def status_slash(interaction: discord.Interaction):
    latency_value = bot.latency
    if latency_value is None or math.isnan(latency_value):
        latency_value = 0.0

    latency = round(latency_value * 1000)
    uptime = datetime.now(timezone.utc) - BOT_START_TIME

    total_seconds = int(uptime.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    uptime_parts = []

    if days:
        uptime_parts.append(f"{days}j")
    if hours or uptime_parts:
        uptime_parts.append(f"{hours}h")
    if minutes or uptime_parts:
        uptime_parts.append(f"{minutes}m")

    uptime_parts.append(f"{seconds}s")

    uptime_display = " ".join(uptime_parts)

    embed = discord.Embed(
        title="🤖 NEURIXBOT — STATUS",
        color=0x5865F2
    )

    embed.add_field(name="🚧 Current Version", value=f"`{CURRENT_VERSION}`", inline=False)
    embed.add_field(name="📦 Version", value=f"`{CURRENT_VERSION}`", inline=False)
    embed.add_field(name="⚡ Ping", value=f"`{latency} ms`", inline=False)
    embed.add_field(name="🧠 AI", value=f"`{AI_STATUS}`", inline=False)
    embed.add_field(name="⏱️ Uptime", value=f"`{uptime_display}`", inline=False)
    embed.add_field(name="📜 Previous Version", value=f"`{PREVIOUS_VERSION}`", inline=False)
    embed.add_field(name="⚡ Last Ping", value=f"`{PREVIOUS_PING}`", inline=False)
    embed.add_field(name="✅ Status", value="`Stable`", inline=False)
    embed.add_field(name="🌐 Servers", value=f"`{len(bot.guilds)}`", inline=False)
    embed.set_footer(text="🟢 NeurixBot is operational")

    await interaction.response.send_message(embed=embed)


def _staff_role_gate(interaction, required_level="Staff"):
    if str(interaction.user.id) == str(BOT_OWNER_ID):
        return True
    level = staff_role_level_for_member(interaction.user)
    return level is not None and STAFF_ROLE_LEVEL_RANK[level] >= STAFF_ROLE_LEVEL_RANK[required_level]


def _find_staff_account(staff_id):
    normalized_staff_id = str(staff_id).strip()
    for discord_user_id, record in staff_security_state.get("members", {}).items():
        if str(record.get("staff_id", discord_user_id)) == normalized_staff_id:
            return str(discord_user_id), record
    return None, None


@staff_group.command(name="generate", description="Crée un secret staff lié à un Staff ID.")
@app_commands.describe(staff_id="Identifiant interne unique du membre staff")
async def staff_generate_slash(interaction: discord.Interaction, staff_id: str):
    if not _staff_role_gate(interaction, "Staff"):
        log_staff_security_event("staff_secret_generate", interaction.user.id, "denied", "missing_staff_role", staff_id=staff_id, command="staff generate", success=False)
        await interaction.response.send_message("⛔ Un rôle Staff configuré est requis.", ephemeral=True)
        return
    staff_id = str(staff_id).strip()
    if not staff_id or len(staff_id) > 64:
        await interaction.response.send_message("❌ Staff ID invalide (1 à 64 caractères).", ephemeral=True)
        return
    secret = generate_staff_secret()
    try:
        register_staff_member(interaction.user.id, secret, staff_id=staff_id)
    except ValueError:
        await interaction.response.send_message("❌ Staff ID déjà lié à un autre compte ou invalide.", ephemeral=True)
        return
    log_staff_security_event("staff_secret_generate", interaction.user.id, "granted", "secret_created", staff_id=staff_id, command="staff generate", success=True)
    await interaction.response.send_message(
        f"✅ Secret généré pour Staff ID `{staff_id}`. Il ne sera plus affiché. Copiez-le et gardez-le privé.\n\n"
        f"```{secret}```\n\n"
        f"Connectez-vous ensuite avec `/staff login staff_id:{staff_id}`.",
        ephemeral=True,
    )


@staff_group.command(name="login", description="Authentifie un membre staff avec son secret personnel.")
@app_commands.describe(staff_id="Votre Staff ID", secret="Votre secret personnel (ne le partagez pas)")
async def staff_login_slash(interaction: discord.Interaction, staff_id: str, secret: str):
    if not _staff_role_gate(interaction, "Staff"):
        log_staff_security_event("staff_login", interaction.user.id, "denied", "missing_staff_role", staff_id=staff_id, command="staff login", success=False)
        await interaction.response.send_message("⛔ Un rôle Staff configuré est requis.", ephemeral=True)
        return
    if is_staff_login_locked(interaction.user.id):
        await interaction.response.send_message("⏳ Trop de tentatives. Réessayez après le verrouillage temporaire.", ephemeral=True)
        return
    if login_staff_member(interaction.user.id, secret, staff_id=staff_id):
        schedule_staff_session_expiry(interaction.user.id)
        if not await sync_staff_channel_visibility_for_user(interaction.user.id, visible=True):
            revoke_staff_session(interaction.user.id, staff_id)
            task = _staff_session_tasks.pop(str(interaction.user.id), None)
            if task and not task.done():
                task.cancel()
            await sync_staff_channel_visibility_for_user(interaction.user.id, visible=False)
            await interaction.response.send_message("❌ Session non activée : permissions bot insuffisantes pour ouvrir les salons Staff.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Authentification validée pour Staff ID `{staff_id}`. Session active pendant {STAFF_SESSION_HOURS}h.", ephemeral=True)
        return
    if is_staff_login_locked(interaction.user.id):
        await interaction.response.send_message("⏳ Trop de tentatives. Votre accès est temporairement verrouillé.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Identifiants invalides. Le secret n’a pas été enregistré.", ephemeral=True)


@staff_group.command(name="status", description="Affiche l’état de la sécurité staff pour l’utilisateur actuel.")
async def staff_status_slash(interaction: discord.Interaction):
    member_id = str(interaction.user.id)
    account = staff_security_state.get("members", {}).get(member_id, {})
    is_owner = member_id == str(BOT_OWNER_ID)
    active = is_owner or (has_active_staff_session(member_id) and _staff_role_gate(interaction, "Staff"))
    status = "✅ Authentifié" if active else "❌ Non authentifié"
    await interaction.response.send_message(
        f"🛡️ **Staff Security**\nStaff ID : `{account.get('staff_id', 'Non configuré')}`\nNiveau Discord : `{staff_role_level_for_member(interaction.user) or ('Owner' if is_owner else 'Aucun')}`\nSession : {status}",
        ephemeral=True,
    )


@staff_group.command(name="revoke", description="Révoque la session staff actuelle pour l’utilisateur.")
@app_commands.describe(staff_id="Votre Staff ID")
async def staff_revoke_slash(interaction: discord.Interaction, staff_id: str):
    member_id, record = _find_staff_account(staff_id)
    if record is None or (member_id != str(interaction.user.id) and str(interaction.user.id) != str(BOT_OWNER_ID)):
        await interaction.response.send_message("⛔ Staff ID introuvable ou révocation non autorisée.", ephemeral=True)
        return
    revoke_staff_session(member_id, staff_id)
    task = _staff_session_tasks.pop(member_id, None)
    if task and not task.done():
        task.cancel()
    await sync_staff_channel_visibility_for_user(member_id, visible=False)
    await interaction.response.send_message("🛑 Session staff révoquée. Le compte et son secret hashé sont conservés.", ephemeral=True)


@staff_group.command(name="regenerate", description="Génère un nouveau secret staff et invalide l’ancien.")
@app_commands.describe(staff_id="Votre Staff ID")
async def staff_regenerate_slash(interaction: discord.Interaction, staff_id: str):
    member_id, record = _find_staff_account(staff_id)
    if member_id != str(interaction.user.id) or not record or not authorize_staff_action(interaction.user, "staff_regenerate"):
        await interaction.response.send_message("⛔ Une session Staff valide est requise pour régénérer votre secret.", ephemeral=True)
        return
    secret = generate_staff_secret()
    try:
        register_staff_member(interaction.user.id, secret, staff_id=staff_id)
    except ValueError:
        await interaction.response.send_message("❌ Staff ID déjà lié à un autre compte ou invalide.", ephemeral=True)
        return
    expiry_task = _staff_session_tasks.pop(member_id, None)
    if expiry_task and not expiry_task.done():
        expiry_task.cancel()
    await sync_staff_channel_visibility_for_user(member_id, visible=False)
    log_staff_security_event("staff_secret_regenerate", interaction.user.id, "granted", "secret_rotated_session_revoked", staff_id=staff_id, command="staff regenerate", success=True)
    await interaction.response.send_message(
        "🔁 Nouveau secret généré. L’ancien est invalidé et la session doit être renouvelée.\n\n"
        f"```{secret}```",
        ephemeral=True,
    )


@staff_group.command(name="role", description="Configure le niveau Staff/Admin/Critical d’un rôle (owner uniquement).")
@app_commands.describe(role="Rôle Discord", level="Niveau de sécurité accordé")
@app_commands.choices(level=[
    app_commands.Choice(name="Staff", value="Staff"),
    app_commands.Choice(name="Admin", value="Admin"),
    app_commands.Choice(name="Critical", value="Critical"),
])
async def staff_role_slash(interaction: discord.Interaction, role: discord.Role, level: app_commands.Choice[str]):
    if str(interaction.user.id) != str(BOT_OWNER_ID):
        log_staff_security_event("staff_role_configure", interaction.user.id, "denied", "owner_only", command="staff role", target_id=role.id, success=False)
        await interaction.response.send_message("⛔ Seul le propriétaire peut configurer les rôles de sécurité.", ephemeral=True)
        return
    if interaction.guild and not await secure_staff_role_in_configured_channels(interaction.guild, role):
        await interaction.response.send_message("❌ Permissions bot insuffisantes pour sécuriser ce rôle dans les salons Staff.", ephemeral=True)
        return
    configure_staff_role(role.id, level.value)
    log_staff_security_event("staff_role_configure", interaction.user.id, "granted", f"role_id={role.id}; level={level.value}", command="staff role", target_id=role.id, success=True)
    await interaction.response.send_message(f"✅ {role.mention} configuré au niveau `{level.value}`.", ephemeral=True)


@staff_group.command(name="channel_add", description="Configure un salon réservé aux sessions Staff authentifiées.")
@app_commands.describe(channel="Salon textuel à protéger par Staff Security")
async def staff_channel_add_slash(interaction: discord.Interaction, channel: discord.TextChannel):
    if str(interaction.user.id) != str(BOT_OWNER_ID):
        await interaction.response.send_message("⛔ Seul le propriétaire peut configurer les salons Staff.", ephemeral=True)
        return
    if interaction.guild is None or channel.guild.id != interaction.guild.id:
        await interaction.response.send_message("❌ Le salon doit appartenir au serveur courant.", ephemeral=True)
        return
    success, message = await add_staff_channel(interaction.guild, channel)
    await interaction.response.send_message(("✅ " if success else "❌ ") + message, ephemeral=True)


@staff_group.command(name="channel_remove", description="Retire la protection Staff d’un salon configuré.")
@app_commands.describe(channel="Salon à retirer de la configuration Staff")
async def staff_channel_remove_slash(interaction: discord.Interaction, channel: discord.TextChannel):
    if str(interaction.user.id) != str(BOT_OWNER_ID):
        await interaction.response.send_message("⛔ Seul le propriétaire peut configurer les salons Staff.", ephemeral=True)
        return
    success, message = await remove_staff_channel(interaction.guild, channel)
    await interaction.response.send_message(("✅ " if success else "❌ ") + message, ephemeral=True)


@staff_group.command(name="channels", description="Affiche les salons Staff configurés sur ce serveur.")
async def staff_channels_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée dans un serveur.", ephemeral=True)
        return
    configured = get_configured_staff_channel_ids(interaction.guild.id)
    if not configured:
        await interaction.response.send_message("ℹ️ Aucun salon Staff configuré.", ephemeral=True)
        return
    lines = [f"• <#{channel_id}> (`{channel_id}`)" for channel_id in configured]
    await interaction.response.send_message("🔒 **Salons Staff**\n" + "\n".join(lines), ephemeral=True)


@staff_group.command(name="logs", description="Affiche les derniers événements de sécurité staff (owner uniquement).")
async def staff_logs_slash(interaction: discord.Interaction):
    if str(interaction.user.id) != str(BOT_OWNER_ID):
        await interaction.response.send_message("⛔ Seul le propriétaire peut consulter les logs de sécurité.", ephemeral=True)
        return
    entries = staff_security_state.get("logs", [])[-15:]
    if not entries:
        await interaction.response.send_message("ℹ️ Aucun événement de sécurité enregistré.", ephemeral=True)
        return
    lines = [
        f"`{entry.get('timestamp', 'N/A')}` • `{entry.get('staff_id') or 'N/A'}` / `{entry.get('discord_user_id', entry.get('user_id', 'N/A'))}` • `{entry.get('command', entry.get('action', 'N/A'))}` • `{entry.get('status', 'N/A')}` • {entry.get('reason', entry.get('details', ''))}"
        for entry in reversed(entries)
    ]
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


bot.tree.add_command(staff_group)
@bot.tree.command(name="role_dynamic_add", description="Ajoute une règle de rôle dynamique")
@app_commands.describe(
    required_role="Rôle que le membre doit posséder",
    dynamic_role="Rôle à attribuer automatiquement",
)
async def role_dynamic_add(
    interaction: discord.Interaction,
    required_role: discord.Role,
    dynamic_role: discord.Role,
):
    guild_id = str(interaction.guild.id)

    if guild_id not in dynamic_roles:
        dynamic_roles[guild_id] = {}

    dynamic_roles[guild_id][str(dynamic_role.id)] = {
        "required_role_id": required_role.id,
    }

    save_dynamic_roles()

    await interaction.response.send_message(
        f"✅ Règle ajoutée : si un membre possède {required_role.mention}, "
        f"il pourra recevoir {dynamic_role.mention}."
    ) 
@bot.tree.command(
    name="role_dynamic_list",
    description="Affiche les règles de rôles dynamiques",
)
async def role_dynamic_list(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)

    if guild_id not in dynamic_roles or not dynamic_roles[guild_id]:
        await interaction.response.send_message(
            "📋 Aucune règle de rôle dynamique configurée."
        )
        return

    lines = ["📋 **Dynamic Roles**"]

    for role_id, config in dynamic_roles[guild_id].items():
        if not str(role_id).isdigit():
            continue
        dynamic_role = interaction.guild.get_role(int(role_id))

        required_role_id = config.get("required_role_id")
        required_role = interaction.guild.get_role(int(required_role_id))

        if dynamic_role is None or required_role is None:
            continue

        lines.append(
            f"🟦 {required_role.mention} → 🟩 {dynamic_role.mention}"
        )

    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="role_boost_set", description="Configure le rôle automatique des boosters.")
@app_commands.describe(role="Rôle attribué pendant le boost; omettre pour désactiver")
async def role_boost_set(interaction: discord.Interaction, role: discord.Role | None = None):
    await _configure_dynamic_event_role_from_interaction(interaction, "boost", role)


@bot.tree.command(name="role_join_set", description="Configure le rôle automatique des nouveaux membres.")
@app_commands.describe(role="Rôle attribué à l’arrivée; omettre pour désactiver")
async def role_join_set(interaction: discord.Interaction, role: discord.Role | None = None):
    await _configure_dynamic_event_role_from_interaction(interaction, "join", role)


@bot.tree.command(name="role_giveaway_set", description="Configure le rôle attribué à un gagnant de giveaway.")
@app_commands.describe(role="Rôle du gagnant; omettre pour désactiver")
async def role_giveaway_set(interaction: discord.Interaction, role: discord.Role | None = None):
    await _configure_dynamic_event_role_from_interaction(interaction, "giveaway", role)


@bot.tree.command(name="role_dynamic_config", description="Affiche les règles de rôles V1 et les rôles événementiels V2.")
async def role_dynamic_config(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée dans un serveur.", ephemeral=True)
        return
    guild_id = str(interaction.guild.id)
    guild_config = dynamic_roles.get(guild_id, {})
    lines = ["🎭 **Dynamic Roles — Configuration**"]
    v1_count = 0
    for role_id, config in guild_config.items():
        if not str(role_id).isdigit() or not isinstance(config, dict):
            continue
        target = interaction.guild.get_role(int(role_id))
        required = interaction.guild.get_role(int(config.get("required_role_id", 0)))
        if target and required:
            lines.append(f"V1 • {required.mention} → {target.mention}")
            v1_count += 1
    if not v1_count:
        lines.append("V1 • Aucune règle configurée")

    event_roles = guild_config.get("event_roles", {})
    for event, key, label in (
        ("boost", "booster_role_id", "🚀 Server Boost"),
        ("join", "join_role_id", "👋 Join Role"),
        ("giveaway", "giveaway_winner_role_id", "🎁 Giveaway Winner"),
    ):
        role_id = event_roles.get(key)
        role = interaction.guild.get_role(int(role_id)) if role_id is not None else None
        lines.append(f"V2 • {label} : {role.mention if role else 'Désactivé'}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)
@bot.tree.command(name="kick", description="Expulse un membre du serveur")
@app_commands.describe(
    member="Membre à expulser",
    reason="Raison de l'expulsion",
)
@app_commands.default_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Aucune raison fournie",
):
    if not authorize_staff_action(interaction.user, "kick", target_id=member.id):
        await interaction.response.send_message(
            "⛔ Vous devez être authentifié avec Staff Security pour utiliser cette commande.",
            ephemeral=True,
        )
        return

    try:
        await member.kick(reason=reason)
        log_staff_security_event("moderation_action", interaction.user.id, "success", reason, command="kick", target_id=member.id, success=True)

        await interaction.response.send_message(
            f"✅ {member.mention} a été expulsé.\n"
            f"📝 Raison : {reason}"
        )

    except discord.Forbidden:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "discord_forbidden", command="kick", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission d'expulser ce membre.",
            ephemeral=True,
        )

    except discord.HTTPException:
        log_staff_security_event("moderation_action", interaction.user.id, "failed", "discord_http_exception", command="kick", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Une erreur Discord est survenue.",
            ephemeral=True,
        )
@bot.tree.command(name="ban", description="Bannit un membre du serveur")
@app_commands.describe(
    member="Membre à bannir",
    reason="Raison du bannissement",
)
@app_commands.default_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Aucune raison fournie",
):
    if not authorize_staff_action(interaction.user, "ban", target_id=member.id):
        await interaction.response.send_message(
            "⛔ Vous devez être authentifié avec Staff Security pour utiliser cette commande.",
            ephemeral=True,
        )
        return

    try:
        await member.ban(reason=reason)
        log_staff_security_event("moderation_action", interaction.user.id, "success", reason, command="ban", target_id=member.id, success=True)

        await interaction.response.send_message(
            f"🔨 {member.mention} a été banni.\n"
            f"📝 Raison : {reason}"
        )

    except discord.Forbidden:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "discord_forbidden", command="ban", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission de bannir ce membre.",
            ephemeral=True,
        )

    except discord.HTTPException:
        log_staff_security_event("moderation_action", interaction.user.id, "failed", "discord_http_exception", command="ban", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Une erreur Discord est survenue.",
            ephemeral=True,
        )
@bot.tree.command(name="unban", description="Débannit un utilisateur")
@app_commands.describe(
    user_id="ID Discord de l'utilisateur",
    reason="Raison du débannissement",
)
@app_commands.default_permissions(ban_members=True)
async def unban(
    interaction: discord.Interaction,
    user_id: str,
    reason: str = "Aucune raison fournie",
):
    if not authorize_staff_action(interaction.user, "unban", target_id=user_id):
        await interaction.response.send_message(
            "⛔ Vous devez être authentifié avec Staff Security pour utiliser cette commande.",
            ephemeral=True,
        )
        return

    if not user_id.isdigit():
        await interaction.response.send_message(
            "❌ ID utilisateur invalide.",
            ephemeral=True,
        )
        return

    try:
        user = discord.Object(id=int(user_id))
        await interaction.guild.unban(user, reason=reason)
        log_staff_security_event("moderation_action", interaction.user.id, "success", reason, command="unban", target_id=user_id, success=True)

        await interaction.response.send_message(
            f"✅ L'utilisateur `{user_id}` a été débanni.\n"
            f"📝 Raison : {reason}"
        )

    except discord.NotFound:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "user_not_banned", command="unban", target_id=user_id, success=False)
        await interaction.response.send_message(
            "❌ Cet utilisateur n'est pas banni.",
            ephemeral=True,
        )

    except discord.Forbidden:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "discord_forbidden", command="unban", target_id=user_id, success=False)
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission de débannir cet utilisateur.",
            ephemeral=True,
        )

    except discord.HTTPException:
        log_staff_security_event("moderation_action", interaction.user.id, "failed", "discord_http_exception", command="unban", target_id=user_id, success=False)
        await interaction.response.send_message(
            "❌ Une erreur Discord est survenue.",
            ephemeral=True,
        )
@bot.tree.command(name="timeout", description="Met un membre en timeout")
@app_commands.describe(
    member="Membre à mettre en timeout",
    duration="Durée en minutes",
    reason="Raison du timeout",
)
@app_commands.default_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    duration: int,
    reason: str = "Aucune raison fournie",
):
    if not authorize_staff_action(interaction.user, "timeout", target_id=member.id):
        await interaction.response.send_message(
            "⛔ Vous devez être authentifié avec Staff Security pour utiliser cette commande.",
            ephemeral=True,
        )
        return

    if duration < 1 or duration > 40320:
        await interaction.response.send_message(
            "❌ La durée doit être comprise entre 1 minute et 28 jours.",
            ephemeral=True,
        )
        return

    try:
        await member.timeout(
            timedelta(minutes=duration),
            reason=reason,
        )
        log_staff_security_event("moderation_action", interaction.user.id, "success", reason, command="timeout", target_id=member.id, success=True)

        await interaction.response.send_message(
            f"🔇 {member.mention} a été mis en timeout pendant **{duration} minutes**.\n"
            f"📝 Raison : {reason}"
        )

    except discord.Forbidden:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "discord_forbidden", command="timeout", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission de mettre ce membre en timeout.",
            ephemeral=True,
        )

    except discord.HTTPException:
        log_staff_security_event("moderation_action", interaction.user.id, "failed", "discord_http_exception", command="timeout", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Une erreur Discord est survenue.",
            ephemeral=True,
        )
@bot.tree.command(name="untimeout", description="Retire le timeout d'un membre")
@app_commands.describe(
    member="Membre dont le timeout doit être retiré",
    reason="Raison du retrait",
)
@app_commands.default_permissions(moderate_members=True)
async def untimeout(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Aucune raison fournie",
):
    if not authorize_staff_action(interaction.user, "untimeout", target_id=member.id):
        await interaction.response.send_message(
            "⛔ Vous devez être authentifié avec Staff Security pour utiliser cette commande.",
            ephemeral=True,
        )
        return

    try:
        await member.timeout(None, reason=reason)
        log_staff_security_event("moderation_action", interaction.user.id, "success", reason, command="untimeout", target_id=member.id, success=True)

        await interaction.response.send_message(
            f"🔊 Le timeout de {member.mention} a été retiré.\n"
            f"📝 Raison : {reason}"
        )

    except discord.Forbidden:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "discord_forbidden", command="untimeout", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission de retirer ce timeout.",
            ephemeral=True,
        )

    except discord.HTTPException:
        log_staff_security_event("moderation_action", interaction.user.id, "failed", "discord_http_exception", command="untimeout", target_id=member.id, success=False)
        await interaction.response.send_message(
            "❌ Une erreur Discord est survenue.",
            ephemeral=True,
        )                                
@bot.tree.command(name="say", description="Envoie un message personnalisé")
@app_commands.describe(message="Message que le bot doit envoyer")
async def say(
    interaction: discord.Interaction,
    message: str,
):
    if not authorize_staff_action(interaction.user, "say", target_id=getattr(interaction.channel, "id", None)):
        await interaction.response.send_message(
            "⛔ Vous devez être authentifié avec Staff Security.",
            ephemeral=True,
        )
        return

    if interaction.channel is None:
        await interaction.response.send_message(
            "❌ Cette commande ne peut pas être utilisée ici.",
            ephemeral=True,
        )
        return

    try:
        await interaction.channel.send(message)
        log_staff_security_event("moderation_action", interaction.user.id, "success", "message_sent", command="say", target_id=interaction.channel.id, success=True)

        await interaction.response.send_message(
            "✅ Message envoyé avec succès.",
            ephemeral=True,
        )

    except discord.Forbidden:
        log_staff_security_event("moderation_action", interaction.user.id, "denied", "discord_forbidden", command="say", target_id=getattr(interaction.channel, "id", None), success=False)
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission d'envoyer des messages dans ce salon.",
            ephemeral=True,
        )

    except discord.HTTPException:
        log_staff_security_event("moderation_action", interaction.user.id, "failed", "discord_http_exception", command="say", target_id=getattr(interaction.channel, "id", None), success=False)
        await interaction.response.send_message(
            "❌ Impossible d'envoyer le message.",
            ephemeral=True,
        )
@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 **NeurixBot répond !**\n⚡ Latence : `{latency} ms`")


@bot.command()
async def status(ctx):
    latency_value = bot.latency
    if latency_value is None or math.isnan(latency_value):
        latency_value = 0.0

    latency = round(latency_value * 1000)
    uptime = datetime.now(timezone.utc) - BOT_START_TIME
    total_seconds = int(uptime.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    uptime_parts = []
    if days:
        uptime_parts.append(f"{days}j")
    if hours or uptime_parts:
        uptime_parts.append(f"{hours}h")
    if minutes or uptime_parts:
        uptime_parts.append(f"{minutes}m")
    uptime_parts.append(f"{seconds}s")

    uptime_display = " ".join(uptime_parts)

    embed = discord.Embed(title="🤖 NEURIXBOT — STATUS", color=0x5865F2)
    embed.add_field(name="🚧 Current Version", value=f"`{CURRENT_VERSION}`", inline=False)
    embed.add_field(name="📦 Version", value=f"`{CURRENT_VERSION}`", inline=False)
    embed.add_field(name="⚡ Ping", value=f"`{latency} ms`", inline=False)
    embed.add_field(name="🧠 AI", value=f"`{AI_STATUS}`", inline=False)
    embed.add_field(name="⏱️ Uptime", value=f"`{uptime_display}`", inline=False)
    embed.add_field(name="📜 Previous Version", value=f"`{PREVIOUS_VERSION}`", inline=False)
    embed.add_field(name="⚡ Last Ping", value=f"`{PREVIOUS_PING}`", inline=False)
    embed.add_field(name="✅ Status", value="`Stable`", inline=False)
    embed.add_field(name="🌐 Servers", value=f"`{len(bot.guilds)}`", inline=False)
    embed.set_footer(text="🟢 NeurixBot is operational")

    await ctx.send(embed=embed)


@bot.command(name="staffgenerate")
async def staff_generate(ctx, staff_id=None):
    await ctx.send("🔐 Pour générer un secret de manière privée, utilisez `/staff generate` avec votre Staff ID.")


@bot.command(name="stafflogin")
async def staff_login(ctx, secret=None):
    await ctx.send("🔐 N'envoyez pas votre secret dans un message. Utilisez `/staff login` en réponse privée.")


@bot.command(name="staffstatus")
async def staff_status(ctx):
    is_owner = int(ctx.author.id) == int(BOT_OWNER_ID)
    status = "✅ Authentifié" if is_owner or has_active_staff_session(str(ctx.author.id)) else "❌ Non authentifié"
    await ctx.send(f"🛡️ **Staff Security**\nStatus : {status}\nDroit d’action : {'Oui' if is_owner or has_active_staff_session(str(ctx.author.id)) else 'Non'}")


@bot.command(name="staffrevoke")
async def staff_revoke(ctx, staff_id=None):
    member_id, record = _find_staff_account(staff_id) if staff_id else (None, None)
    if member_id != str(ctx.author.id) or not record:
        await ctx.send("⛔ Utilisez `/staff revoke` avec votre Staff ID.")
        return
    revoke_staff_session(member_id, staff_id)
    task = _staff_session_tasks.pop(member_id, None)
    if task and not task.done():
        task.cancel()
    await sync_staff_channel_visibility_for_user(member_id, visible=False)
    await ctx.send("🛑 Session staff révoquée.")


@bot.command(name="staffregenerate")
async def staff_regenerate(ctx):
    await ctx.send("🔐 Utilisez `/staff regenerate` en réponse privée pour ne jamais exposer le secret.")


@bot.command(name="ask")
async def ask(ctx, *, question: str = ""):
    if not question.strip():
        await ctx.send("❓ Utilisation : `!ask <question>`")
        return

    if not OPENAI_API_KEY:
        await ctx.send("⚠️ La variable d'environnement `OPENAI_API_KEY` n'est pas configurée.")
        return

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = await asyncio.to_thread(
            client.responses.create,
            model=OPENAI_MODEL,
            input=question.strip(),
        )
        answer = (response.output_text or "").strip()
        if not answer:
            answer = "Je n'ai pas pu générer de réponse pour cette question."
        await ctx.send(f"🤖 Réponse de l'IA :\n{answer[:1800]}")
    except Exception as exc:
        print(f"Erreur OpenAI : {type(exc).__name__}: {exc}", flush=True)
        await ctx.send(f"⚠️ Erreur OpenAI : `{type(exc).__name__}`")


@bot.event
async def on_ready():
    global _startup_completed, _startup_in_progress

    print(f"✅ Connecté à Discord en tant que {bot.user}", flush=True)
    if _startup_completed:
        print("ℹ️ Initialisation déjà effectuée ; aucune opération de démarrage relancée.", flush=True)
        return
    if _startup_in_progress:
        print("ℹ️ Initialisation déjà en cours ; cet événement on_ready est ignoré.", flush=True)
        return

    _startup_in_progress = True
    try:
        print("♻️ Restauration des giveaways...", flush=True)
        await restore_giveaways()


        print("🛡️ Restauration des sessions Staff Security...", flush=True)
        await resume_staff_channel_sessions()

        _startup_completed = True
        print("✅ Bot prêt.", flush=True)
    except Exception as exc:
        print(
            f"❌ Échec des opérations de démarrage : {type(exc).__name__}: {exc}",
            flush=True,
        )
        raise
    finally:
        _startup_in_progress = False

@bot.event
async def on_message(message):
    print(f"📩 Reçu : {message.content}")

    if message.author == bot.user:
        return

    if message.content == "!test":
        await message.channel.send(translate(interaction_language(message),
                                             "✅ Le bot reçoit les messages !", "✅ The bot is receiving messages!"))

    await bot.process_commands(message)
@bot.event
async def on_member_update(before, after):
    before_level = staff_role_level_for_member(before)
    after_level = staff_role_level_for_member(after)
    if before_level != after_level or after_level is not None:
        await set_staff_channel_visibility(
            after,
            visible=bool(after_level and has_active_staff_session(after.id)),
        )

    if before.premium_since is None and after.premium_since is not None:
        await apply_dynamic_event_role(after, "boost", add=True)
    elif before.premium_since is not None and after.premium_since is None:
        await apply_dynamic_event_role(after, "boost", add=False)

    guild_id = str(after.guild.id)

    if guild_id not in dynamic_roles:
        return

    for role_id, config in dynamic_roles[guild_id].items():
        if not str(role_id).isdigit():
            continue
        role = after.guild.get_role(int(role_id))

        if role is None:
            continue

        required_role_id = config.get("required_role_id")

        if required_role_id is None:
            continue

        has_required_role = any(
            member_role.id == required_role_id
            for member_role in after.roles
        )

        if has_required_role and role not in after.roles:
            try:
                await after.add_roles(
                    role,
                    reason="Dynamic Role",
                )
            except discord.Forbidden:
                pass
        elif not has_required_role and role in after.roles:
            try:
                await after.remove_roles(
                    role,
                    reason="Dynamic Role",
                )
            except discord.Forbidden:
                pass 


@bot.event
async def on_member_join(member):
    if staff_role_level_for_member(member) is not None:
        await set_staff_channel_visibility(member, visible=False)
    await apply_dynamic_event_role(member, "join", add=True)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN n'est pas défini dans les variables d'environnement.")
    bot.run(TOKEN) 

