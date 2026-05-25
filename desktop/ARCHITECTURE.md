# Nova Desktop — Agent OS Architecture

All OpenJarvis-like features are **desktop-only** (Electron + Node.js).
The web version (nov-assistant.com) stays as a chat-only UI.

## Directory Structure

```
desktop/
├── main.js              # Electron main process (existing)
├── preload.js           # Existing preload
├── offline.html         # Existing offline fallback
├── agents/              # Agent framework
│   ├── index.js         # Agent registry & runner
│   ├── base-agent.js    # Base agent class
│   ├── simple.js        # Single-turn chat (no tools)
│   ├── orchestrator.js  # Multi-turn reasoning + auto tool selection
│   ├── research.js      # Multi-hop research with citations
│   ├── code-agent.js    # CodeAct — generates & executes Python/JS
│   ├── monitor.js       # Continuous agent with state + memory
│   ├── digest.js        # Morning digest (email, calendar, news)
│   ├── react-agent.js   # ReAct (Thought-Action-Observation) loop
│   └── operative.js     # Persistent autonomous agent
├── skills/              # Skills/plugin system
│   ├── index.js         # Skill registry, loader, catalog
│   ├── base-skill.js    # Skill interface
│   └── builtin/         # Built-in skills
│       ├── web-search.js
│       ├── file-ops.js
│       ├── shell-exec.js
│       ├── calculator.js
│       ├── timer.js
│       └── weather.js
├── memory/              # Memory & indexing
│   ├── index.js         # Memory manager
│   ├── store.js         # SQLite-backed vector store
│   ├── embedder.js      # Local embeddings (Ollama)
│   └── retriever.js     # Semantic search + retrieval
├── scheduler/           # Scheduled agents
│   ├── index.js         # Cron-like scheduler
│   └── jobs.js          # Job persistence
├── cli/                 # CLI interface
│   └── nova-cli.js      # `nova` command (bin entry)
└── ui/                  # Desktop-only UI pages
    ├── agents.html      # Agent management panel
    ├── skills.html      # Skills marketplace
    └── memory.html      # Memory explorer
```

## Agent Types (matching OpenJarvis)

| Agent         | Type        | Description                                    |
|---------------|-------------|------------------------------------------------|
| simple        | On-demand   | Single-turn chat, no tools                     |
| orchestrator  | On-demand   | Multi-turn reasoning, auto tool selection      |
| research      | On-demand   | Multi-hop research with citations              |
| code          | On-demand   | CodeAct — generates and executes code          |
| react         | On-demand   | ReAct loop (Thought-Action-Observation)        |
| monitor       | Continuous  | Long-horizon monitoring with memory            |
| digest        | Scheduled   | Morning briefing from email/calendar/news      |
| operative     | Continuous  | Persistent autonomous agent with state mgmt    |

## Key Principles

1. **All Ollama-powered** — runs through the existing sidecar
2. **SQLite for persistence** — agents, skills, memory all stored locally
3. **IPC bridge** — Electron main ↔ renderer communication
4. **Desktop-only** — these features don't exist on the web version
5. **Installable skills** — JSON manifest, can be loaded from GitHub
