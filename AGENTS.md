# AGENTS.md — Nova AI

> Guidelines for AI agents (Copilot, OpenClaw, Claude, Codex, etc.) working on this codebase.

## Project Overview

**Nova** is a personal AI operating system — local-first, privacy-first. It has two surfaces:

1. **Desktop App** (macOS) — PyQt6 sidebar with wake word, STT, LLM, TTS, 8 agents, 12 skills, memory, scheduler, CLI
2. **Web App** — FastAPI + static frontend hosted at [nov-assistant.com](https://nov-assistant.com), multi-provider LLM chat with streaming, Google OAuth, image generation

## Architecture

```
Nova/
├── main.py              # Desktop app entry point (PyQt6, main thread)
├── nova.py              # CLI entry point
├── config.py            # Centralized config (env vars, NOVA_* prefix)
├── launcher.py          # App launcher / setup
├── src/
│   ├── agents/          # Agent registry & models (Simple, Orchestrator, Research, ReAct, Code, Monitor, Operative, Digest)
│   ├── skills/          # Skill registry, loader, decorators
│   ├── audio.py         # Audio I/O
│   ├── stt.py           # Speech-to-text (Whisper)
│   ├── tts.py           # Text-to-speech (macOS say)
│   ├── llm.py           # LLM interface (Ollama)
│   ├── providers.py     # Multi-provider fallback
│   ├── wake_word.py     # Wake word detection
│   ├── pipeline.py      # Voice pipeline orchestration
│   ├── sidebar.py       # PyQt6 sidebar UI
│   ├── memory/          # Semantic memory with local embeddings
│   ├── web/             # Web control hub (FastAPI backend)
│   ├── vision.py        # Screen analysis / vision
│   └── ...
├── web/
│   ├── server.py        # Standalone FastAPI web backend (production)
│   ├── static/          # Frontend assets
│   └── ...
├── website/             # Marketing site (nov-assistant.com)
├── tests/               # pytest test suite
├── scripts/             # Utility scripts (tunnel, deploy, etc.)
├── desktop/             # Desktop packaging / .app build
└── agents/              # Agent config files (DESI, LESI, REDI, etc.)
```

## Tech Stack

- **Language:** Python 3.10+
- **UI:** PyQt6 (desktop), HTML/CSS/JS (web)
- **Backend:** FastAPI + uvicorn
- **LLM:** Ollama (local), OpenRouter/Groq/SambaNova (cloud fallback)
- **STT:** Whisper (local)
- **TTS:** macOS `say`
- **Wake Word:** openwakeword
- **Database:** SQLite (aiosqlite for web)
- **Auth:** Google OAuth (web)
- **Hosting:** GCP e2-micro + nginx + Let's Encrypt

## Code Standards

- **Type hints** on all functions and methods
- **Docstrings** (Google/NumPy style) on all public APIs
- **`from __future__ import annotations`** in every module
- **Config via env vars** — use `config.py` helpers, never hardcode secrets
- **No secrets in code** — `.env` files are gitignored

## Key Constraints

1. **PyQt6 + macOS Cocoa:** `QApplication` and sidebar UI must be created on the **main thread**. Never move Qt widgets to background threads.
2. **Ollama dependency:** Desktop features assume Ollama is running locally. Always check model availability before switching defaults.
3. **Privacy-first:** No telemetry, no cloud calls without explicit user opt-in. Local-first is a core value.
4. **Zero-config startup:** The app must work out of the box with sensible defaults. Don't require manual setup steps.
5. **Backward compat:** Config supports `NOVA_*`, `EP_*`, and `JARVIS_*` env prefixes (legacy).

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ -v --cov=src --cov-report=term-missing
```

- Mock all hardware (mic, speaker) and network calls
- Tests must pass without audio devices or a running Ollama instance
- Use `tmp_path` for file-based tests
- Cover both happy-path and error-path

## Working With This Codebase

### Do

- Read `config.py` before adding new settings
- Check `src/agents/registry.py` and `src/skills/registry.py` before adding agents/skills
- Run the test suite after changes
- Keep commits focused — one feature/fix per commit
- Use the existing patterns (registries, decorators, pipeline stages)

### Don't

- Rewrite large files in one shot — make focused, incremental edits
- Add cloud dependencies without a local fallback
- Break the zero-config experience
- Commit `.env`, API keys, or tokens
- Skip tests for new features

## Branch Strategy

- `main` — stable, deployable
- Feature branches: `feat/description`
- Bug fixes: `fix/description`
- Always PR into `main`

## Deployment

- **Web app:** Deployed to GCP VM (`nova-hub`) via nginx reverse proxy → uvicorn on port 8766
- **Desktop app:** Built as macOS `.app` bundle via `desktop/` tooling
- **Domain:** `nov-assistant.com` (Cloudflare DNS → GCP)
- **HTTPS:** Let's Encrypt (auto-renews via certbot)

## Contact

- **Maintainer:** [@escipionpedroza147-commits](https://github.com/escipionpedroza147-commits)
- **Website:** [nov-assistant.com](https://nov-assistant.com)
