# Contributing to EP Agent (Voice Bridge)

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# 1. Fork and clone the repo
git clone https://github.com/<your-username>/jarvis-voice-bridge.git
cd jarvis-voice-bridge

# 2. Run setup
bash setup.sh

# 3. Activate the virtual environment
source .venv/bin/activate

# 4. Verify tests pass
python -m pytest tests/ -v
```

## Code Standards

- **Python 3.10+** — use type hints throughout
- **Docstrings** — Google/NumPy style on all public functions and classes
- **Imports** — `from __future__ import annotations` in every module
- **Formatting** — consistent style with the existing codebase
- **No secrets** — never commit API keys, tokens, or `.env` files

## Testing

Every feature must include tests. We use **pytest**.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_commands.py -v

# Run with coverage
pip install pytest-cov
python -m pytest tests/ -v --cov=src --cov-report=term-missing
```

### Test Guidelines

- Mock all hardware (microphone, speaker) and network calls
- Tests must run without audio devices or a running Ollama instance
- Use `tmp_path` fixture for file-based tests
- Aim for both happy-path and error-path coverage

## Making Changes

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```

2. **Write code** with type hints and docstrings

3. **Write tests** for your changes

4. **Run the full test suite** and make sure everything passes:
   ```bash
   python -m pytest tests/ -v --tb=short
   ```

5. **Commit** with a descriptive message:
   ```bash
   git commit -m "feat: add cool new feature"
   ```

6. **Push** and open a Pull Request

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Use for |
|--------|---------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `docs:` | Documentation only |
| `test:` | Adding/fixing tests |
| `ci:` | CI/CD changes |
| `refactor:` | Code refactoring (no behavior change) |
| `chore:` | Maintenance tasks |

## Project Structure

```
src/
├── audio.py       # Audio playback utilities
├── commands.py    # Built-in voice command parser
├── health.py      # Health/status HTTP server
├── llm.py         # Ollama LLM client
├── memory.py      # Conversation history persistence
├── pipeline.py    # Full pipeline orchestration
├── recorder.py    # Audio recorder with VAD
├── stt.py         # Speech-to-text (faster-whisper)
├── tts.py         # Text-to-speech (Piper + macOS say)
└── wake_word.py   # Wake word detection (OpenWakeWord)
```

## Important Notes

- **Don't modify `wake_word.py` wake word resolution logic** — it handles alias mapping and was carefully tuned
- **Audio won't work in Docker** — keep tests mockable
- **macOS `say` is the primary TTS** on Apple Silicon — Piper isn't available on ARM64

## Reporting Issues

Open an issue on GitHub with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your OS and Python version

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
