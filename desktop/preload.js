// Nova AI Desktop — Preload Script
// Sandboxed IPC bridge: all sensitive ops go through ipcRenderer → main process
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('novaDesktop', {
  platform: process.platform,
  isDesktop: true,
  version: '2.0.0',

  // ── Ollama (local AI) ──
  ollama: {
    chat: (messages, model) => ipcRenderer.invoke('ollama:chat', messages, model),
    status: () => ipcRenderer.invoke('ollama:status'),
    models: () => ipcRenderer.invoke('ollama:models'),
  },

  // ── Voice ──
  voice: {
    speak: (text) => ipcRenderer.invoke('voice:speak', text),
    stopSpeaking: () => ipcRenderer.invoke('voice:stop'),
  },

  // ── Notifications ──
  notification: {
    show: (title, body) => ipcRenderer.invoke('notification:show', title, body),
  },

  // ── App control ──
  app: {
    info: () => ipcRenderer.invoke('app:info'),
    getSettings: () => ipcRenderer.invoke('app:getSettings'),
    setSetting: (key, val) => ipcRenderer.invoke('app:setSetting', key, val),
    switchMode: (online) => ipcRenderer.invoke('app:switchMode', online),
    openExternal: (url) => ipcRenderer.invoke('app:openExternal', url),
  },

  // ── Events from main → renderer ──
  on: (channel, callback) => {
    const allowed = ['voice:hotkey', 'mode:changed', 'ollama:status-changed'];
    if (allowed.includes(channel)) {
      ipcRenderer.on(channel, (_event, ...args) => callback(...args));
    }
  },
});
