# Nova AI — Desktop App

Your Personal AI Operating System, packaged as a native desktop app.

## Download

Grab the latest release from the [Releases](https://github.com/escipionpedroza147-commits/Nova/releases) page:

| Platform | File |
|----------|------|
| macOS (Universal) | `Nova AI-2.0.0-universal.dmg` |
| Windows (x64) | `Nova AI Setup 2.0.0.exe` |

---

## macOS Installation

1. **Download** the `.dmg` file
2. **Open** the DMG and drag **Nova AI** to your **Applications** folder
3. **Launch** Nova AI from Applications

### ⚠️ "App is damaged" or "unidentified developer" warning

Since Nova AI is not yet code-signed with an Apple Developer certificate, macOS may block it. Here's how to fix it:

**Option A — Right-click to open (easiest)**
1. Right-click (or Control-click) on **Nova AI** in Applications
2. Select **Open** from the context menu
3. Click **Open** in the dialog that appears
4. You only need to do this once

**Option B — Run the fix script**
```bash
bash scripts/fix-macos.sh
```

**Option C — Terminal command**
```bash
xattr -cr /Applications/Nova\ AI.app
```

Then open Nova AI normally.

---

## Windows Installation

1. **Download** the `.exe` installer
2. **Run** the installer — choose your install directory
3. Nova AI will create a desktop shortcut and Start Menu entry
4. If Windows Defender SmartScreen appears, click **More info** → **Run anyway**

---

## Building from Source

### Prerequisites
- [Node.js](https://nodejs.org/) v18+
- npm or yarn

### Install dependencies
```bash
cd desktop
npm install
```

### Run in development
```bash
npm start
```

### Build for your platform
```bash
# macOS
npm run build:mac

# Windows
npm run build:win

# All platforms
npm run build:all
```

Built artifacts go to the `dist/` folder.

---

## License

MIT © Escipion Pedroza
