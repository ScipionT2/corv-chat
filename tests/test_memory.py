"""Tests for the conversation memory module."""

import json
from pathlib import Path

import pytest

from src.memory import ConversationMemory


@pytest.fixture
def tmp_history(tmp_path: Path) -> Path:
    """Return a temporary history file path."""
    return tmp_path / "jarvis" / "history.json"


@pytest.fixture
def memory(tmp_history: Path) -> ConversationMemory:
    """Return a ConversationMemory pointed at a temp file."""
    return ConversationMemory(history_file=str(tmp_history), max_entries=100)


class TestConversationMemory:
    """Tests for ConversationMemory."""

    def test_load_empty_when_no_file(self, memory: ConversationMemory) -> None:
        result = memory.load()
        assert result == []
        assert memory.count == 0

    def test_add_persists_to_file(
        self, memory: ConversationMemory, tmp_history: Path
    ) -> None:
        memory.add("user", "Hello")
        assert tmp_history.exists()
        data = json.loads(tmp_history.read_text())
        assert len(data) == 1
        assert data[0] == {"role": "user", "content": "Hello"}

    def test_add_multiple_entries(self, memory: ConversationMemory) -> None:
        memory.add("user", "Hi")
        memory.add("assistant", "Hello!")
        memory.add("user", "How are you?")
        assert memory.count == 3
        assert memory.history[0]["role"] == "user"
        assert memory.history[1]["role"] == "assistant"

    def test_load_restores_history(
        self, tmp_history: Path
    ) -> None:
        # Pre-populate the file
        entries = [
            {"role": "user", "content": "question 1"},
            {"role": "assistant", "content": "answer 1"},
        ]
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text(json.dumps(entries))

        mem = ConversationMemory(history_file=str(tmp_history), max_entries=100)
        loaded = mem.load()
        assert len(loaded) == 2
        assert loaded[0]["content"] == "question 1"

    def test_clear_empties_file(
        self, memory: ConversationMemory, tmp_history: Path
    ) -> None:
        memory.add("user", "something")
        assert memory.count == 1
        memory.clear()
        assert memory.count == 0
        data = json.loads(tmp_history.read_text())
        assert data == []

    def test_max_entries_trims_old(self, tmp_history: Path) -> None:
        mem = ConversationMemory(history_file=str(tmp_history), max_entries=4)
        for i in range(6):
            mem.add("user", f"msg {i}")
        assert mem.count == 4
        # Oldest should be trimmed
        assert mem.history[0]["content"] == "msg 2"

    def test_load_corrupt_file_resets(self, tmp_history: Path) -> None:
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text("NOT VALID JSON{{{")
        mem = ConversationMemory(history_file=str(tmp_history), max_entries=100)
        loaded = mem.load()
        assert loaded == []

    def test_load_non_list_resets(self, tmp_history: Path) -> None:
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text(json.dumps({"not": "a list"}))
        mem = ConversationMemory(history_file=str(tmp_history), max_entries=100)
        loaded = mem.load()
        assert loaded == []

    def test_history_returns_copy(self, memory: ConversationMemory) -> None:
        memory.add("user", "test")
        h = memory.history
        h.clear()  # modifying copy shouldn't affect internal state
        assert memory.count == 1

    def test_creates_parent_directories(
        self, tmp_path: Path
    ) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "history.json"
        mem = ConversationMemory(history_file=str(deep_path), max_entries=50)
        mem.add("user", "nested test")
        assert deep_path.exists()

    def test_load_trims_oversized_file(self, tmp_history: Path) -> None:
        entries = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text(json.dumps(entries))

        mem = ConversationMemory(history_file=str(tmp_history), max_entries=5)
        loaded = mem.load()
        assert len(loaded) == 5
        assert loaded[0]["content"] == "msg 15"
