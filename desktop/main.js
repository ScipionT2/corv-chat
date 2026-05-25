const { app, BrowserWindow, Menu, Tray, shell, nativeTheme, session, dialog, globalShortcut, ipcMain, Notification, net } = require('electron');
const path = require('path');
const { execFile } = require('child_process');
const fs = require('fs');
const http = require('http');
const { registerDesktopIPC } = require('./ipc-handlers');

// ── Config ──────────────────────────────────────────────────────────
const NOVA_URL = 'https://nov-assistant.com';
const ALLOWED_HOSTS = ['nov-assistant.com', 'www.nov-assistant.com'];
// Google OAuth requires navigating to these hosts
const OAUTH_HOSTS = ['accounts.google.com', 'www.google.com', 'content.googleapis.com', 'lh3.googleusercontent.com'];
const APP_NAME = 'Nova AI';

// Set the app name so macOS menu bar shows "Nova AI" instead of "Electron"
if (app.setName) app.setName(APP_NAME);
if (app.name !== undefined) app.name = APP_NAME;
const OLLAMA_URL = 'http://localhost:11434';
const OLLAMA_MODEL = 'llama3.2:3b';
const SETTINGS_PATH = path.join(app.getPath('userData'), 'settings.json');

// Bundled Ollama binary path
const OLLAMA_BIN = app.isPackaged
  ? path.join(process.resourcesPath, 'bin', 'ollama')
  : path.join(__dirname, 'bin', 'ollama');

let mainWindow = null;
let tray = null;
let ollamaProcess = null;
let isOnlineMode = true;
let ollamaAvailable = false;
let setupComplete = false;
let settings = loadSettings();

// ── Force dark mode ─────────────────────────────────────────────────
nativeTheme.themeSource = 'dark';

// ── Settings persistence ────────────────────────────────────────────
function loadSettings() {
  try {
    if (fs.existsSync(SETTINGS_PATH)) {
      return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf-8'));
    }
  } catch (_) {}
  return { autoStart: false, ollamaModel: '', ollamaUrl: OLLAMA_URL };
}

function saveSettings() {
  try {
    fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2));
  } catch (_) {}
}

// ── Single instance lock ────────────────────────────────────────────
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

// ── Helper: check if URL is allowed ─────────────────────────────────
function isAllowedURL(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:') return false;
    return ALLOWED_HOSTS.includes(parsed.hostname) || OAUTH_HOSTS.includes(parsed.hostname);
  } catch { return false; }
}

// ── Ollama sidecar management ───────────────────────────────────────
function startOllamaSidecar() {
  if (ollamaProcess) return;
  const binPath = OLLAMA_BIN;
  if (!fs.existsSync(binPath)) {
    console.log('[Nova] Ollama binary not found at', binPath);
    return;
  }
  const env = { ...process.env, OLLAMA_HOST: '127.0.0.1:11434' };
  // Add bin dir to DYLD path so Ollama finds its dylibs
  const binDir = path.dirname(binPath);
  env.DYLD_LIBRARY_PATH = binDir + (env.DYLD_LIBRARY_PATH ? ':' + env.DYLD_LIBRARY_PATH : '');
  env.LD_LIBRARY_PATH = binDir + (env.LD_LIBRARY_PATH ? ':' + env.LD_LIBRARY_PATH : '');

  ollamaProcess = require('child_process').spawn(binPath, ['serve'], {
    env,
    stdio: 'ignore',
    detached: false,
  });
  ollamaProcess.on('error', (err) => {
    console.error('[Nova] Ollama start error:', err.message);
    ollamaProcess = null;
  });
  ollamaProcess.on('exit', (code) => {
    console.log('[Nova] Ollama exited with code', code);
    ollamaProcess = null;
  });
  console.log('[Nova] Ollama sidecar started, PID:', ollamaProcess.pid);
}

function stopOllamaSidecar() {
  if (ollamaProcess) {
    ollamaProcess.kill();
    ollamaProcess = null;
  }
}

async function ensureModelPulled() {
  const model = settings.ollamaModel || OLLAMA_MODEL;
  // Check if model already exists
  try {
    const res = await ollamaRequest('/api/tags', { timeout: 5000 });
    if (res.status === 200 && res.data.models) {
      const names = res.data.models.map(m => m.name);
      if (names.some(n => n.startsWith(model.split(':')[0]))) {
        setupComplete = true;
        return true; // Already have it
      }
    }
  } catch {}
  // Need to pull — tell renderer to show setup screen
  if (mainWindow) mainWindow.webContents.send('setup:pulling', model);
  return new Promise((resolve) => {
    const binPath = OLLAMA_BIN;
    const env = { ...process.env, OLLAMA_HOST: '127.0.0.1:11434' };
    const binDir = path.dirname(binPath);
    env.DYLD_LIBRARY_PATH = binDir + (env.DYLD_LIBRARY_PATH ? ':' + env.DYLD_LIBRARY_PATH : '');
    env.LD_LIBRARY_PATH = binDir + (env.LD_LIBRARY_PATH ? ':' + env.LD_LIBRARY_PATH : '');
    const pull = require('child_process').spawn(binPath, ['pull', model], { env, stdio: ['ignore', 'pipe', 'pipe'] });
    let progress = '';
    pull.stdout.on('data', (d) => {
      progress = d.toString().trim();
      if (mainWindow) mainWindow.webContents.send('setup:progress', progress);
    });
    pull.stderr.on('data', (d) => {
      progress = d.toString().trim();
      if (mainWindow) mainWindow.webContents.send('setup:progress', progress);
    });
    pull.on('exit', (code) => {
      setupComplete = (code === 0);
      if (mainWindow) mainWindow.webContents.send('setup:done', code === 0);
      resolve(code === 0);
    });
    pull.on('error', () => {
      if (mainWindow) mainWindow.webContents.send('setup:done', false);
      resolve(false);
    });
  });
}

// ── Ollama helpers ──────────────────────────────────────────────────
function ollamaRequest(path, options = {}) {
  const url = (settings.ollamaUrl || OLLAMA_URL) + path;
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      port: urlObj.port || 11434,
      path: urlObj.pathname,
      method: options.method || 'GET',
      headers: { 'Content-Type': 'application/json' },
      timeout: options.timeout || 5000,
    };
    const req = http.request(reqOptions, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, data: data }); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    if (options.body) req.write(JSON.stringify(options.body));
    req.end();
  });
}

async function checkOllama() {
  try {
    const res = await ollamaRequest('/api/tags', { timeout: 3000 });
    ollamaAvailable = res.status === 200;
    return ollamaAvailable;
  } catch {
    ollamaAvailable = false;
    return false;
  }
}

async function getOllamaModels() {
  try {
    const res = await ollamaRequest('/api/tags', { timeout: 5000 });
    if (res.status === 200 && res.data.models) {
      return res.data.models.map(m => m.name);
    }
  } catch {}
  return [];
}

// ── Ollama streaming chat (returns full response) ───────────────────
function ollamaChat(messages, model) {
  return new Promise((resolve, reject) => {
    const url = new URL((settings.ollamaUrl || OLLAMA_URL) + '/api/chat');
    const body = JSON.stringify({ model: model || settings.ollamaModel || 'llama3.2:3b', messages, stream: false });
    const reqOptions = {
      hostname: url.hostname,
      port: url.port || 11434,
      path: url.pathname,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout: 120000,
    };
    const req = http.request(reqOptions, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve(parsed.message?.content || '');
        } catch { reject(new Error('Invalid response from Ollama')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Ollama timeout')); });
    req.write(body);
    req.end();
  });
}

// ── TTS (macOS native say, fallback: no-op on Windows) ──────────────
function speak(text) {
  return new Promise((resolve) => {
    if (process.platform === 'darwin') {
      // Use macOS 'say' command
      const voice = settings.voice || 'Samantha';
      execFile('say', ['-v', voice, text], (err) => resolve(!err));
    } else if (process.platform === 'win32') {
      // PowerShell TTS on Windows
      const ps = `Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('${text.replace(/'/g, "''")}')`;
      execFile('powershell', ['-Command', ps], (err) => resolve(!err));
    } else {
      resolve(false);
    }
  });
}

function stopSpeaking() {
  if (process.platform === 'darwin') {
    execFile('killall', ['say'], () => {});
  }
}

// ── Check if nov-assistant.com is reachable ─────────────────────────
function checkOnline() {
  return new Promise((resolve) => {
    const req = net.request({ method: 'HEAD', url: NOVA_URL });
    req.on('response', () => resolve(true));
    req.on('error', () => resolve(false));
    setTimeout(() => { req.abort(); resolve(false); }, 5000);
    req.end();
  });
}

// ── Create window ───────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: APP_NAME,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 16 },
    backgroundColor: '#050816',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload-desktop.js'),
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      sandbox: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
      webSecurity: true,
      spellcheck: true,
      navigateOnDragDrop: false,
    },
    icon: path.join(__dirname, 'icons', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
  });

  // Decide what to load
  loadAppropriateContent();

  // Block navigation to non-Nova URLs
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedURL(url)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Desktop-only page navigation via IPC
  ipcMain.handle('navigate:agents', () => {
    mainWindow.loadFile(path.join(__dirname, 'ui', 'agents.html'));
  });
  ipcMain.handle('navigate:skills', () => {
    mainWindow.loadFile(path.join(__dirname, 'ui', 'skills.html'));
  });
  ipcMain.handle('navigate:memory', () => {
    mainWindow.loadFile(path.join(__dirname, 'ui', 'memory.html'));
  });
  ipcMain.handle('navigate:home', () => {
    loadAppropriateContent();
  });

  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.on('close', (e) => {
    // On macOS, clicking the red × hides to tray.
    // Cmd+Q / menu Quit / tray Quit all set app.isQuitting first.
    if (process.platform === 'darwin' && !app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

async function loadAppropriateContent() {
  if (!isOnlineMode) {
    mainWindow.loadFile(path.join(__dirname, 'offline.html'));
    return;
  }
  const online = await checkOnline();
  if (online) {
    mainWindow.loadURL(NOVA_URL);
  } else {
    // Fallback to offline
    isOnlineMode = false;
    mainWindow.loadFile(path.join(__dirname, 'offline.html'));
  }
}

// ── System Tray ─────────────────────────────────────────────────────
function createTray() {
  const iconPath = path.join(__dirname, 'icons', process.platform === 'win32' ? 'icon.ico' : 'icon.png');
  tray = new Tray(iconPath);
  tray.setToolTip(APP_NAME);
  updateTrayMenu();
  tray.on('click', () => {
    if (mainWindow) {
      mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
    }
  });
}

function updateTrayMenu() {
  if (!tray) return;
  const template = [
    { label: mainWindow?.isVisible() ? 'Hide Nova' : 'Show Nova', click: () => mainWindow?.isVisible() ? mainWindow.hide() : mainWindow?.show() },
    { type: 'separator' },
    { label: '🤖 Agents', click: () => { mainWindow?.show(); mainWindow?.loadFile(path.join(__dirname, 'ui', 'agents.html')); } },
    { label: '🧩 Skills', click: () => { mainWindow?.show(); mainWindow?.loadFile(path.join(__dirname, 'ui', 'skills.html')); } },
    { label: '🧠 Memory', click: () => { mainWindow?.show(); mainWindow?.loadFile(path.join(__dirname, 'ui', 'memory.html')); } },
    { type: 'separator' },
    { label: 'Online Mode', type: 'radio', checked: isOnlineMode, click: () => switchMode(true) },
    { label: 'Offline Mode', type: 'radio', checked: !isOnlineMode, click: () => switchMode(false) },
    { type: 'separator' },
    { label: `Ollama: ${ollamaAvailable ? '● Connected' : '○ Not found'}`, enabled: false },
    { type: 'separator' },
    { label: 'Auto-Start', type: 'checkbox', checked: settings.autoStart, click: (item) => { settings.autoStart = item.checked; saveSettings(); app.setLoginItemSettings({ openAtLogin: item.checked }); } },
    { type: 'separator' },
    { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } },
  ];
  tray.setContextMenu(Menu.buildFromTemplate(template));
}

function switchMode(online) {
  isOnlineMode = online;
  loadAppropriateContent();
  updateTrayMenu();
  mainWindow?.webContents.send('mode:changed', online);
}

// ── Global Shortcuts ────────────────────────────────────────────────
function registerShortcuts() {
  const mod = process.platform === 'darwin' ? 'CommandOrControl+Shift' : 'Ctrl+Shift';
  globalShortcut.register(`${mod}+N`, () => {
    if (mainWindow) {
      mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
    }
  });
  globalShortcut.register(`${mod}+V`, () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.webContents.send('voice:hotkey');
    }
  });
}

// ── IPC Handlers ────────────────────────────────────────────────────
function setupIPC() {
  // Register desktop-only agent/skills/memory/scheduler IPC handlers
  registerDesktopIPC(ipcMain, () => mainWindow);

  ipcMain.handle('ollama:chat', async (_e, messages, model) => {
    try { return { ok: true, content: await ollamaChat(messages, model) }; }
    catch (err) { return { ok: false, error: err.message }; }
  });

  ipcMain.handle('ollama:status', async () => {
    const available = await checkOllama();
    return { online: available, url: settings.ollamaUrl || OLLAMA_URL };
  });

  ipcMain.handle('ollama:models', async () => {
    return await getOllamaModels();
  });

  ipcMain.handle('voice:speak', async (_e, text) => {
    return await speak(text);
  });

  ipcMain.handle('voice:stop', async () => {
    stopSpeaking();
    return true;
  });

  ipcMain.handle('notification:show', (_e, title, body) => {
    if (Notification.isSupported()) {
      new Notification({ title, body, icon: path.join(__dirname, 'icons', 'icon.png') }).show();
      return true;
    }
    return false;
  });

  ipcMain.handle('app:info', () => ({
    version: '2.0.0',
    platform: process.platform,
    online: isOnlineMode,
    ollamaAvailable,
    setupComplete,
    ollamaBin: OLLAMA_BIN,
  }));

  ipcMain.handle('ollama:ensure', async () => {
    startOllamaSidecar();
    // Wait for Ollama to be ready (up to 10s)
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 500));
      if (await checkOllama()) break;
    }
    if (!ollamaAvailable) return { ok: false, error: 'Ollama failed to start' };
    const pulled = await ensureModelPulled();
    return { ok: pulled };
  });

  ipcMain.handle('app:getSettings', () => settings);

  ipcMain.handle('app:setSetting', (_e, key, val) => {
    settings[key] = val;
    saveSettings();
    if (key === 'autoStart') app.setLoginItemSettings({ openAtLogin: val });
    return true;
  });

  ipcMain.handle('app:switchMode', (_e, online) => {
    switchMode(online);
    return true;
  });

  ipcMain.handle('app:openExternal', (_e, url) => {
    shell.openExternal(url);
    return true;
  });
}

// ── Permissions ─────────────────────────────────────────────────────
function setupPermissions() {
  const ses = session.defaultSession;
  const ALLOWED_PERMISSIONS = ['clipboard-read', 'clipboard-sanitized-write', 'pointerLock', 'fullscreen', 'notifications', 'media'];

  ses.setPermissionRequestHandler((webContents, permission, callback) => {
    const url = webContents.getURL();
    if ((isAllowedURL(url) || url.startsWith('file://')) && ALLOWED_PERMISSIONS.includes(permission)) {
      callback(true);
    } else { callback(false); }
  });

  ses.setPermissionCheckHandler((webContents, permission) => {
    if (!webContents) return false;
    const url = webContents.getURL();
    return (isAllowedURL(url) || url.startsWith('file://')) && ALLOWED_PERMISSIONS.includes(permission);
  });
}

// ── Navigation Security ─────────────────────────────────────────────
function setupNavigationSecurity() {
  app.on('web-contents-created', (_, contents) => {
    // Allow in-window navigation to Nova + Google OAuth (same window).
    // Google OAuth redirects through many subdomains (accounts.google.com,
    // content.googleapis.com, play.google.com, etc.) so we allow any
    // google.com subdomain and our own domain. Everything else opens externally.
    contents.on('will-navigate', (event, url) => {
      try {
        const parsed = new URL(url);
        if (parsed.protocol !== 'https:' && !url.startsWith('file://')) {
          event.preventDefault();
          return;
        }
        const host = parsed.hostname;
        const isNova = host === 'nov-assistant.com' || host === 'www.nov-assistant.com';
        const isGoogle = host.endsWith('.google.com') || host.endsWith('.googleapis.com') || host === 'google.com';
        const isLocal = url.startsWith('file://');
        if (!isNova && !isGoogle && !isLocal) {
          event.preventDefault();
          shell.openExternal(url);
        }
      } catch {
        event.preventDefault();
      }
    });
    // Block new-window popups to non-Nova hosts
    contents.setWindowOpenHandler(({ url }) => {
      try {
        const parsed = new URL(url);
        const host = parsed.hostname;
        if (host === 'nov-assistant.com' || host === 'www.nov-assistant.com') {
          return { action: 'allow' };
        }
      } catch {}
      shell.openExternal(url);
      return { action: 'deny' };
    });
    contents.on('will-attach-webview', (event) => event.preventDefault());
  });
}

// ── App Menu ────────────────────────────────────────────────────────
function createMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{
      label: APP_NAME,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { label: 'Check for Updates...', click: () => shell.openExternal('https://nov-assistant.com/site/download.html') },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' },
        { type: 'separator' },
        { label: 'Quit Nova AI', accelerator: 'CmdOrCtrl+Q', click: () => { app.isQuitting = true; app.quit(); } },
      ],
    }] : []),
    { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: 'View', submenu: [{ role: 'reload' }, { role: 'forceReload' }, { type: 'separator' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { type: 'separator' }, { role: 'togglefullscreen' }] },
    {
      label: 'Nova',
      submenu: [
        { label: 'Home', accelerator: isMac ? 'Cmd+Shift+H' : 'Ctrl+Shift+H', click: () => { isOnlineMode = true; loadAppropriateContent(); } },
        { label: 'New Chat', accelerator: isMac ? 'Cmd+N' : 'Ctrl+N', click: () => mainWindow?.webContents.executeJavaScript('if(typeof startNewChat==="function")startNewChat()') },
        { type: 'separator' },
        { label: 'Toggle Online/Offline', accelerator: isMac ? 'Cmd+Shift+O' : 'Ctrl+Shift+O', click: () => switchMode(!isOnlineMode) },
        { label: 'Voice Input', accelerator: isMac ? 'Cmd+Shift+V' : 'Ctrl+Shift+V', click: () => mainWindow?.webContents.send('voice:hotkey') },
        { type: 'separator' },
        { label: '🤖 Agents', accelerator: isMac ? 'Cmd+Shift+A' : 'Ctrl+Shift+A', click: () => mainWindow?.loadFile(path.join(__dirname, 'ui', 'agents.html')) },
        { label: '🧩 Skills', accelerator: isMac ? 'Cmd+Shift+S' : 'Ctrl+Shift+S', click: () => mainWindow?.loadFile(path.join(__dirname, 'ui', 'skills.html')) },
        { label: '🧠 Memory', accelerator: isMac ? 'Cmd+Shift+M' : 'Ctrl+Shift+M', click: () => mainWindow?.loadFile(path.join(__dirname, 'ui', 'memory.html')) },
        { type: 'separator' },
        { label: 'Features', click: () => { isOnlineMode = true; mainWindow?.loadURL(`${NOVA_URL}/site/features.html`); } },
        { label: 'Pricing', click: () => { isOnlineMode = true; mainWindow?.loadURL(`${NOVA_URL}/site/pricing.html`); } },
      ],
    },
    { label: 'Window', submenu: [{ role: 'minimize' }, { role: 'zoom' }, ...(isMac ? [{ type: 'separator' }, { role: 'front' }] : [{ role: 'close' }])] },
    { label: 'Help', submenu: [
      { label: 'Nova Website', click: () => shell.openExternal('https://nov-assistant.com/site/') },
      { label: 'GitHub', click: () => shell.openExternal('https://github.com/escipionpedroza147-commits/Nova') },
      { type: 'separator' },
      { label: 'Report Issue', click: () => shell.openExternal('https://github.com/escipionpedroza147-commits/Nova/issues') },
    ]},
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── App lifecycle ───────────────────────────────────────────────────
app.whenReady().then(async () => {
  setupPermissions();
  setupNavigationSecurity();
  setupIPC();
  createMenu();
  createWindow();
  createTray();
  registerShortcuts();

  // Start bundled Ollama sidecar
  startOllamaSidecar();

  // Wait a moment then check status
  await new Promise(r => setTimeout(r, 2000));
  await checkOllama();
  updateTrayMenu();

  // If Ollama is up, ensure model is ready
  if (ollamaAvailable) {
    await ensureModelPulled();
  }

  // Periodic Ollama check
  setInterval(async () => {
    const prev = ollamaAvailable;
    await checkOllama();
    if (prev !== ollamaAvailable) {
      updateTrayMenu();
      mainWindow?.webContents.send('ollama:status-changed', ollamaAvailable);
    }
  }, 30000);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else mainWindow?.show();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  stopOllamaSidecar();
});

app.isQuitting = false;
app.on('before-quit', () => {
  app.isQuitting = true;
});

// ── Security: Disable remote module ─────────────────────────────────
app.on('remote-require', (event) => event.preventDefault());
app.on('remote-get-builtin', (event) => event.preventDefault());
app.on('remote-get-global', (event) => event.preventDefault());
app.on('remote-get-current-window', (event) => event.preventDefault());
app.on('remote-get-current-web-contents', (event) => event.preventDefault());
