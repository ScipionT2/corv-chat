// Preload script — minimal safe API surface
// Runs in sandboxed context with contextIsolation enabled
// SECURITY: Only expose read-only metadata, no Node.js APIs

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('novaDesktop', {
  platform: process.platform,
  isDesktop: true,
  version: '1.0.0',
});

// SECURITY: Do not expose any additional APIs
// No ipcRenderer, no fs, no shell, no require
