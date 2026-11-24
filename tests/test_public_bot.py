import unittest
from unittest.mock import AsyncMock, MagicMock

import bot as neurixbot
from tickets import load_ticket_settings


class PublicBotTests(unittest.TestCase):
    def test_expected_command_groups_are_registered(self):
        names = {command.name for command in neurixbot.bot.tree.get_commands()}
        expected = {"ping", "status", "ticket", "staff", "giveaway", "steplanguage"}
        self.assertTrue(expected.issubset(names), f"Missing commands: {expected - names}")

    def test_moderation_commands_are_registered(self):
        names = {command.name for command in neurixbot.bot.tree.get_commands()}
        for command in ("kick", "ban", "unban", "timeout", "untimeout"):
            self.assertIn(command, names)

    def test_tracker_and_devops_commands_are_absent(self):
        names = {command.name.lower() for command in neurixbot.bot.tree.get_commands()}
        forbidden = {"tracker", "roblox", "devops", "github_tracker"}
        self.assertTrue(names.isdisjoint(forbidden), f"Unexpected commands: {names & forbidden}")

    def test_tickets_are_disabled_by_default(self):
        enabled, category, staff_channel, staff_role, _ = load_ticket_settings({})
        self.assertFalse(enabled)
        self.assertIsNone(category)
        self.assertIsNone(staff_channel)
        self.assertIsNone(staff_role)

    def test_ticket_configuration_requires_all_ids_when_enabled(self):
        with self.assertRaises(ValueError):
            load_ticket_settings({"TICKET_ENABLED": "true"})


class StatusCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_slash_status_sends_exactly_one_embed_response(self):
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()

        await neurixbot.status_slash.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIn("embed", kwargs)
        self.assertNotIn("content", kwargs)
        self.assertEqual(kwargs["embed"].title, "🤖 NEURIXBOT — STATUS")


if __name__ == "__main__":
    unittest.main()
