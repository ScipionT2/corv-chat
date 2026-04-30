"""Tests for the shutdown voice command."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.commands import CommandResult, parse_command


class TestShutdownCommand:
    def test_jarvis_off(self):
        result = parse_command("Jarvis, off")
        assert result.result == CommandResult.SHUTDOWN

    def test_just_off(self):
        result = parse_command("off")
        assert result.result == CommandResult.SHUTDOWN

    def test_shutdown(self):
        result = parse_command("shutdown")
        assert result.result == CommandResult.SHUTDOWN

    def test_shut_down(self):
        result = parse_command("shut down")
        assert result.result == CommandResult.SHUTDOWN

    def test_power_off(self):
        result = parse_command("power off")
        assert result.result == CommandResult.SHUTDOWN

    def test_exit(self):
        result = parse_command("exit")
        assert result.result == CommandResult.SHUTDOWN

    def test_quit(self):
        result = parse_command("quit")
        assert result.result == CommandResult.SHUTDOWN

    def test_goodbye(self):
        result = parse_command("goodbye")
        assert result.result == CommandResult.SHUTDOWN

    def test_good_bye(self):
        result = parse_command("good bye")
        assert result.result == CommandResult.SHUTDOWN

    def test_terminate(self):
        result = parse_command("terminate")
        assert result.result == CommandResult.SHUTDOWN

    def test_jarvis_prefix_off(self):
        result = parse_command("Jarvis off")
        assert result.result == CommandResult.SHUTDOWN

    def test_has_goodbye_message(self):
        result = parse_command("Jarvis, off")
        assert result.message is not None
        assert "goodbye" in result.message.lower() or "shutting" in result.message.lower()

    def test_normal_text_not_shutdown(self):
        result = parse_command("turn off the lights")
        assert result.result == CommandResult.NOT_A_COMMAND

    def test_off_in_sentence_not_shutdown(self):
        result = parse_command("what is the offside rule")
        assert result.result == CommandResult.NOT_A_COMMAND
