"""Tests for vision-related voice commands."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.commands import CommandResult, parse_command


class TestVisionAnalyzeCommand:
    """Test one-shot screen analysis voice triggers."""

    def test_what_do_you_see(self):
        result = parse_command("Jarvis, what do you see?")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_what_can_you_see(self):
        result = parse_command("what can you see")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_analyze_screen(self):
        result = parse_command("Jarvis, analyze my screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_analyze_the_screen(self):
        result = parse_command("analyze the screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_look_at_my_screen(self):
        result = parse_command("look at my screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_read_my_screen(self):
        result = parse_command("read my screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_whats_on_screen(self):
        result = parse_command("what's on my screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_what_is_on_the_screen(self):
        result = parse_command("what is on the screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_screen_analysis(self):
        result = parse_command("screen analysis")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_describe_screen(self):
        result = parse_command("describe my screen")
        assert result.result == CommandResult.VISION_ANALYZE

    def test_has_response_message(self):
        result = parse_command("what do you see")
        assert result.message is not None
        assert "screen" in result.message.lower() or "analyz" in result.message.lower()


class TestVisionToggleCommand:
    """Test analysis mode toggle voice triggers."""

    def test_start_analysis(self):
        result = parse_command("start analysis mode")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_start_analysis_no_mode(self):
        result = parse_command("start analysis")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_stop_analysis(self):
        result = parse_command("stop analysis mode")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_enable_screen_mode(self):
        result = parse_command("enable screen mode")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_disable_screen_mode(self):
        result = parse_command("disable screen mode")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_toggle_analysis(self):
        result = parse_command("toggle analysis mode")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_turn_on_analysis(self):
        result = parse_command("turn on analysis")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_turn_off_analysis(self):
        result = parse_command("turn off analysis")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_begin_screen_analysis(self):
        result = parse_command("Jarvis, begin screen analysis")
        assert result.result == CommandResult.VISION_TOGGLE

    def test_end_screen_analysis(self):
        result = parse_command("end screen analysis")
        assert result.result == CommandResult.VISION_TOGGLE


class TestNonVisionCommands:
    """Ensure normal text is NOT matched as vision commands."""

    def test_normal_question(self):
        result = parse_command("what is the meaning of life")
        assert result.result == CommandResult.NOT_A_COMMAND

    def test_see_in_different_context(self):
        result = parse_command("can you see why this code fails")
        assert result.result == CommandResult.NOT_A_COMMAND

    def test_screen_in_sentence(self):
        result = parse_command("my screen is broken")
        assert result.result == CommandResult.NOT_A_COMMAND

    def test_existing_commands_still_work(self):
        assert parse_command("what time is it").result == CommandResult.HANDLED
        assert parse_command("clear history").result == CommandResult.CLEAR_HISTORY
        assert parse_command("stop listening").result == CommandResult.PAUSE
        assert parse_command("resume").result == CommandResult.RESUME
