const { app, BrowserWindow, Menu, shell, nativeTheme, Tray, nativeImage } = require('electron');
const path = require('path');

// ── Config ──────────────────────────────────────────────────────────
const NOVA_URL = 'https://nov-assistant.com';
const APP_NAME = 'Nova AI';

let mainWindow = null;
let tray = null;

// ── Force dark mode ─────────────────────────────────────────────────
nativeTheme.themeSource = 'dark';

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
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: true,
    },
    icon: path.join(__dirname, 'icons', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
  });

  // Graceful show after load
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
  });

  // Load Nova
  mainWindow.loadURL(NOVA_URL);

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://nov-assistant.com') || url.startsWith('https://www.nov-assistant.com')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Navigate external links to browser
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const parsed = new URL(url);
    if (parsed.hostname !== 'nov-assistant.com' && parsed.hostname !== 'www.nov-assistant.com') {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── App menu ────────────────────────────────────────────────────────
function createMenu() {
  const isMac = process.platform === 'darwin';

  const template = [
    ...(isMac ? [{
      label: APP_NAME,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        {
          label: 'Check for Updates...',
          click: () => shell.openExternal('https://nov-assistant.com/site/download.html'),
        },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { type: 'separator' },
        { role: 'toggleDevTools' },
      ],
    },
    {
      label: 'Navigate',
      submenu: [
        {
          label: 'Home',
          accelerator: isMac ? 'Cmd+Shift+H' : 'Ctrl+Shift+H',
          click: () => mainWindow?.loadURL(NOVA_URL),
        },
        {
          label: 'New Chat',
          accelerator: isMac ? 'Cmd+N' : 'Ctrl+N',
          click: () => mainWindow?.webContents.executeJavaScript('if(typeof startNewChat==="function")startNewChat()'),
        },
        { type: 'separator' },
        {
          label: 'Features',
          click: () => mainWindow?.loadURL(`${NOVA_URL}/site/features.html`),
        },
        {
          label: 'Pricing',
          click: () => mainWindow?.loadURL(`${NOVA_URL}/site/pricing.html`),
        },
      ],
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(isMac ? [
          { type: 'separator' },
          { role: 'front' },
        ] : [
          { role: 'close' },
        ]),
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'Nova Website',
          click: () => shell.openExternal('https://nov-assistant.com/site/'),
        },
        {
          label: 'GitHub',
          click: () => shell.openExternal('https://github.com/escipionpedroza147-commits/Nova'),
        },
        { type: 'separator' },
        {
          label: 'Report Issue',
          click: () => shell.openExternal('https://github.com/escipionpedroza147-commits/Nova/issues'),
        },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// ── App lifecycle ───────────────────────────────────────────────────
app.whenReady().then(() => {
  createMenu();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// ── Security ────────────────────────────────────────────────────────
app.on('web-contents-created', (_, contents) => {
  // Prevent navigation to non-Nova URLs
  contents.on('will-navigate', (event, url) => {
    const parsed = new URL(url);
    if (parsed.hostname !== 'nov-assistant.com' && parsed.hostname !== 'www.nov-assistant.com') {
      event.preventDefault();
    }
  });
});
