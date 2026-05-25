/**
 * @module preload-desktop
 * @description Desktop-only preload additions for Nova.
 * Exposes the novaDesktop.agents, skills, memory, scheduler, and system
 * APIs via contextBridge. Merges with the base preload.js — import this
 * AFTER the base preload or combine both in a single preload entry.
 *
 * Usage in main.js webPreferences:
 *   preload: path.join(__dirname, 'preload-desktop.js')
 *
 * Or load both preloads via a wrapper preload that requires both files.
 */

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// ── Helper: create a scoped invoke function ─────────────────────────
const invoke = (channel, ...args) => ipcRenderer.invoke(channel, ...args);

// ── Desktop-only APIs ───────────────────────────────────────────────
// These extend the base novaDesktop namespace from preload.js.
// If loaded standalone, they create the full namespace.

const desktopAPIs = {
  // ── Agents ──
  agents: {
    /** List all available agents */
    list: () => invoke('agents:list'),
    /** Run an agent with the given input */
    run: (agentId, input) => invoke('agents:run', agentId, input),
    /** Stop a running agent */
    stop: (agentId) => invoke('agents:stop', agentId),
    /** Update agent configuration */
    configure: (agentId, config) => invoke('agents:configure', agentId, config),
  },

  // ── Skills ──
  skills: {
    /** List all installed skills (builtin + custom) */
    list: () => invoke('skills:list'),
    /** Install a skill from a GitHub URL */
    install: (githubUrl) => invoke('skills:install', githubUrl),
    /** Uninstall a custom skill by ID */
    uninstall: (skillId) => invoke('skills:uninstall', skillId),
  },

  // ── Memory ──
  memory: {
    /** Semantic search across indexed memories */
    search: (query, topK) => invoke('memory:search', query, topK),
    /** Index a single file into the memory store */
    indexFile: (filePath) => invoke('memory:index-file', filePath),
    /** Index an entire directory into the memory store */
    indexDir: (dirPath) => invoke('memory:index-dir', dirPath),
    /** Delete a specific memory by ID */
    forget: (id) => invoke('memory:forget', id),
    /** Get memory store statistics */
    stats: () => invoke('memory:stats'),
    /** Store a new memory with optional metadata */
    remember: (text, metadata) => invoke('memory:remember', text, metadata),
  },

  // ── Scheduler ──
  scheduler: {
    /** List all scheduled tasks */
    list: () => invoke('scheduler:list'),
    /** Add a new scheduled task */
    add: (config) => invoke('scheduler:add', config),
    /** Remove a scheduled task */
    remove: (id) => invoke('scheduler:remove', id),
    /** Toggle a scheduled task on/off */
    toggle: (id) => invoke('scheduler:toggle', id),
  },

  // ── System ──
  system: {
    /** Run system diagnostics (Ollama, models, disk, etc.) */
    doctor: () => invoke('system:doctor'),
    /** Show native folder picker dialog */
    pickFolder: () => invoke('system:pick-folder'),
    /** Show native file picker dialog */
    pickFile: () => invoke('system:pick-file'),
  },
};

// ── Event listeners for agent streaming ─────────────────────────────
const desktopEvents = {
  /** Listen for agent chunk events (streaming) */
  onAgentChunk: (agentId, callback) => {
    ipcRenderer.on(`agent:chunk:${agentId}`, (_event, chunk) => callback(chunk));
  },
  /** Listen for agent step events (execution trace) */
  onAgentStep: (agentId, callback) => {
    ipcRenderer.on(`agent:step:${agentId}`, (_event, step) => callback(step));
  },
  /** Remove all listeners for an agent's events */
  offAgent: (agentId) => {
    ipcRenderer.removeAllListeners(`agent:chunk:${agentId}`);
    ipcRenderer.removeAllListeners(`agent:step:${agentId}`);
  },
};

// ── Expose to renderer ──────────────────────────────────────────────
// Check if novaDesktop was already partially exposed by base preload.js
// If so, we need to merge. contextBridge doesn't support patching, so
// we expose the full combined object.

try {
  // Build the full object combining base preload APIs + desktop APIs
  const fullAPI = {
    // ── Base APIs (duplicated here for standalone use) ──
    platform: process.platform,
    isDesktop: true,
    version: '2.0.0',

    ollama: {
      chat: (messages, model) => invoke('ollama:chat', messages, model),
      status: () => invoke('ollama:status'),
      models: () => invoke('ollama:models'),
    },
    voice: {
      speak: (text) => invoke('voice:speak', text),
      stopSpeaking: () => invoke('voice:stop'),
    },
    notification: {
      show: (title, body) => invoke('notification:show', title, body),
    },
    app: {
      info: () => invoke('app:info'),
      getSettings: () => invoke('app:getSettings'),
      setSetting: (key, val) => invoke('app:setSetting', key, val),
      switchMode: (online) => invoke('app:switchMode', online),
      openExternal: (url) => invoke('app:openExternal', url),
    },
    navigate: {
      agents: () => invoke('navigate:agents'),
      skills: () => invoke('navigate:skills'),
      memory: () => invoke('navigate:memory'),
      home: () => invoke('navigate:home'),
    },
    setup: {
      ensure: () => invoke('ollama:ensure'),
    },
    on: (channel, callback) => {
      const allowed = [
        'voice:hotkey', 'mode:changed', 'ollama:status-changed',
        'setup:pulling', 'setup:progress', 'setup:done',
      ];
      if (allowed.includes(channel)) {
        ipcRenderer.on(channel, (_event, ...args) => callback(...args));
      }
    },

    // ── Desktop-only APIs ──
    ...desktopAPIs,
    ...desktopEvents,
  };

  contextBridge.exposeInMainWorld('novaDesktop', fullAPI);
} catch (err) {
  // If contextBridge already has novaDesktop (from base preload),
  // log and continue — the renderer can still use the base APIs
  console.warn('[Nova Preload Desktop] Could not expose novaDesktop:', err.message);
  console.warn('[Nova Preload Desktop] Desktop APIs may not be available. Use preload-desktop.js as the sole preload.');
}
