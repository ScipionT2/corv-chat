const { app, BrowserWindow, Menu, shell, nativeTheme, session, dialog } = require('electron');
const path = require('path');

// ── Config ──────────────────────────────────────────────────────────
const NOVA_URL = 'https://nov-assistant.com';
const ALLOWED_HOSTS = ['nov-assistant.com', 'www.nov-assistant.com'];
const APP_NAME = 'Nova AI';

let mainWindow = null;

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

// ── Helper: check if URL is allowed ─────────────────────────────────
function isAllowedURL(url) {
  try {
    const parsed = new URL(url);
    return ALLOWED_HOSTS.includes(parsed.hostname) && parsed.protocol === 'https:';
  } catch {
    return false;
  }
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
      // ── SECURITY: Maximum isolation ──
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,       // Isolate preload from renderer
      nodeIntegration: false,        // No Node.js in renderer
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,                 // OS-level sandbox
      webviewTag: false,             // No <webview> tags
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      enableBlinkFeatures: '',       // No extra Blink features
      webSecurity: true,             // Enforce same-origin
      spellcheck: true,
      navigateOnDragDrop: false,     // Prevent drag-drop navigation
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

  // ── SECURITY: Block all new windows except Nova domains ──
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedURL(url)) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── SECURITY: Permission handlers ───────────────────────────────────
function setupPermissions() {
  const ses = session.defaultSession;

  // Only allow specific permissions
  const ALLOWED_PERMISSIONS = [
    'clipboard-read',
    'clipboard-sanitized-write',
    'pointerLock',
    'fullscreen',
    'notifications',
  ];

  ses.setPermissionRequestHandler((webContents, permission, callback) => {
    const url = webContents.getURL();
    if (isAllowedURL(url) && ALLOWED_PERMISSIONS.includes(permission)) {
      callback(true);
    } else {
      console.log(`[Security] Denied permission: ${permission} for ${url}`);
      callback(false);
    }
  });

  ses.setPermissionCheckHandler((webContents, permission) => {
    if (!webContents) return false;
    const url = webContents.getURL();
    return isAllowedURL(url) && ALLOWED_PERMISSIONS.includes(permission);
  });

  // ── SECURITY: Content Security Policy ──
  ses.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self' https://nov-assistant.com https://www.nov-assistant.com; " +
          "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://nov-assistant.com https://www.nov-assistant.com https://accounts.google.com https://apis.google.com; " +
          "style-src 'self' 'unsafe-inline' https://nov-assistant.com https://www.nov-assistant.com https://fonts.googleapis.com; " +
          "font-src 'self' https://fonts.gstatic.com https://fonts.googleapis.com; " +
          "img-src 'self' data: blob: https: http:; " +
          "connect-src 'self' https://nov-assistant.com https://www.nov-assistant.com wss://nov-assistant.com https://accounts.google.com https://apis.google.com https://openrouter.ai https://api.openai.com https://api.anthropic.com https://api.groq.com https://api.sambanova.ai; " +
          "frame-src https://accounts.google.com; " +
          "media-src 'self' blob: data:; " +
          "object-src 'none'; " +
          "base-uri 'self';"
        ],
      },
    });
  });
}

// ── SECURITY: Navigation guard ──────────────────────────────────────
function setupNavigationSecurity() {
  app.on('web-contents-created', (_, contents) => {
    // Block navigation to non-Nova URLs
    contents.on('will-navigate', (event, url) => {
      if (!isAllowedURL(url)) {
        event.preventDefault();
        shell.openExternal(url);
      }
    });

    // Block new window creation for non-Nova URLs
    contents.setWindowOpenHandler(({ url }) => {
      if (!isAllowedURL(url)) {
        shell.openExternal(url);
        return { action: 'deny' };
      }
      return { action: 'allow' };
    });

    // Prevent attaching webviews
    contents.on('will-attach-webview', (event) => {
      event.preventDefault();
    });
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
  setupPermissions();
  setupNavigationSecurity();
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

// ── SECURITY: Disable remote module entirely ────────────────────────
app.on('remote-require', (event) => event.preventDefault());
app.on('remote-get-builtin', (event) => event.preventDefault());
app.on('remote-get-global', (event) => event.preventDefault());
app.on('remote-get-current-window', (event) => event.preventDefault());
app.on('remote-get-current-web-contents', (event) => event.preventDefault());
