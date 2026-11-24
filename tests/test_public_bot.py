import unittest

import bot
from tickets import load_ticket_settings


class PublicBotTests(unittest.TestCase):
    def test_expected_command_groups_are_registered(self):
        names = {command.name for command in bot.bot.tree.get_commands()}
        expected = {"ping", "status", "ticket", "staff", "giveaway", "steplanguage"}
        self.assertTrue(expected.issubset(names), f"Missing commands: {expected - names}")

    def test_moderation_commands_are_registered(self):
        names = {command.name for command in bot.bot.tree.get_commands()}
        for command in ("kick", "ban", "unban", "timeout", "untimeout"):
            self.assertIn(command, names)

    def test_tracker_and_devops_commands_are_absent(self):
        names = {command.name.lower() for command in bot.bot.tree.get_commands()}
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


if __name__ == "__main__":
    unittest.main()
