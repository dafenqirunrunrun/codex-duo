#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h}"
APP="$ROOT/dist/Codex Duo.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -parse-as-library -O -framework AppKit -framework Security "$ROOT/Sources/CodexDuoMenu.swift" "$ROOT/Sources/ControlDashboard.swift" "$ROOT/Sources/ClaudeUsagePanel.swift" "$ROOT/Sources/WindowArranger.swift" "$ROOT/Sources/DuoAppIcon.swift" -o "$APP/Contents/MacOS/CodexDuoMenu"
cp "$ROOT/codex_duo.py" "$APP/Contents/Resources/codex_duo.py"
cp "$ROOT/history_sync.py" "$APP/Contents/Resources/history_sync.py"
cp "$ROOT/claude_usage.py" "$APP/Contents/Resources/claude_usage.py"
"$APP/Contents/MacOS/CodexDuoMenu" --export-icon "$APP/Contents/Resources/AppIcon.png"
sips -s format icns "$APP/Contents/Resources/AppIcon.png" --out "$APP/Contents/Resources/AppIcon.icns" >/dev/null
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>local.codex.duo</string>
  <key>CFBundleName</key><string>Codex Duo</string>
  <key>CFBundleExecutable</key><string>CodexDuoMenu</string>
  <key>CFBundleIconFile</key><string>AppIcon.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
plutil -lint "$APP/Contents/Info.plist"
echo "Built $APP"
