// Preload script — runs in renderer with context isolation
// Exposes minimal safe APIs to the renderer process

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('novaDesktop', {
  platform: process.platform,
  isDesktop: true,
  version: require('./package.json').version,
});
