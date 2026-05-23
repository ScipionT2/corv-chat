# Nova Desktop App — TODO

_Last updated: 2026-05-22_

---

## 🔴 High Priority

### 1. Apple Developer Account ($99/year)
- Sign up: https://developer.apple.com/programs/
- Get Team ID + signing certificate
- Configure electron-builder to code sign + notarize
- Eliminates ALL Gatekeeper "malware" warnings
- Users can open app with zero friction

### 2. Code Signing & Notarization (macOS)
- Add `CSC_LINK` (p12 cert) and `CSC_KEY_PASSWORD` to build env
- Add `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` for notarization
- Update `package.json` build config with signing identity
- Test: `spctl --assess` should pass after signing

### 3. Windows Build (.exe)
- Can't build Windows from macOS natively
- Option A: Set up GitHub Actions CI to auto-build Windows NSIS installer
- Option B: Use a Windows VM/machine
- Need to generate `icon.ico` from the PNG (use electron-icon-builder or online tool)
- Test on Windows 10/11

### 4. GitHub Actions CI/CD
- Auto-build on push/tag for macOS + Windows
- Auto-upload to GitHub Releases
- Auto-notarize macOS builds
- Template: `.github/workflows/build.yml`

---

## 🟡 Medium Priority

### 5. Auto-Update System
- Add `electron-updater` package
- Check for updates on app launch
- Download + install in background
- Notify user when update is ready
- Uses GitHub Releases as update source

### 6. Windows Code Signing
- Get a code signing certificate (DigiCert, Sectigo, etc.)
- Or use Azure Trusted Signing
- Prevents Windows SmartScreen warnings

### 7. Offline Fallback Page
- Show a branded "No internet" page when nov-assistant.com is unreachable
- Retry button + link to check status
- Currently shows Electron's default error page

---

## 🟢 Nice to Have

### 8. System Tray / Menu Bar
- Minimize to tray instead of closing
- Quick access: New Chat, open app
- Show notification badge

### 9. Native Notifications
- Push notifications for chat responses
- Configurable in settings

### 10. Deep Links
- Register `nova://` protocol
- Allow opening specific chats/features from links

### 11. Touch Bar Support (macOS)
- New Chat, model selector, quick actions

### 12. Linux Build
- AppImage already configured in package.json
- Test on Ubuntu/Fedora
- Add to release workflow

---

## ✅ Completed
- [x] Electron app scaffolded (main.js, preload.js, package.json)
- [x] macOS .dmg built (universal — Intel + Apple Silicon)
- [x] App icon (.icns, .png) generated from Nova logo
- [x] Security hardened v1.0.1 (sandbox, CSP, permissions, entitlements)
- [x] GitHub Release v1.0.1-beta published
- [x] Download page linked to real .dmg
