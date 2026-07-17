#!/bin/bash
# ============================================================
#  Erzeugt ~/Applications/Qalam.app – einen schlanken Wrapper,
#  der qalam über das projekteigene venv startet:
#   - KEIN Terminal-Fenster
#   - eigene, stabile App-Identität (com.ramzibanisaad.qalam)
#     -> macOS-Rechte (Mikrofon/Bedienungshilfen/Eingabeüberwachung)
#        werden EINMAL erteilt und bleiben haften
#   - als Login-Objekt/Autostart nutzbar
#
#  Nur macOS. Windows nutzt weiterhin launcher.bat / launcher_hidden.vbs.
#  Bündelt KEINE Abhängigkeiten (nutzt das vorhandene venv) -> klein & robust.
# ============================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Qalam.app"

if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    echo "[FEHLER] venv nicht gefunden. Erst Setup ausführen (siehe MACOS.md)."
    exit 1
fi

mkdir -p "$HOME/Applications"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Qalam</string>
    <key>CFBundleDisplayName</key><string>Qalam</string>
    <key>CFBundleIdentifier</key><string>com.ramzibanisaad.qalam</string>
    <key>CFBundleExecutable</key><string>Qalam</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <!-- Menüleisten-App: kein Dock-Icon, kein App-Switcher-Eintrag. -->
    <key>LSUIElement</key><true/>
    <!-- Ohne diese Beschreibung verweigert macOS den Mikrofonzugriff. -->
    <key>NSMicrophoneUsageDescription</key><string>Qalam nutzt dein Mikrofon für die Sprache-zu-Text-Diktierung.</string>
</dict>
</plist>
PLIST

# WICHTIG: src/main.py DIREKT starten (nicht über run.py). Sonst wäre die GUI ein
# Enkel-Unterprozess, den LaunchServices nicht als App-Prozess führt -> stirbt beim
# Start über den Finder/das Dock. run.py macht auf macOS ohnehin nur load_dotenv()
# (jetzt in main.py) + Windows-CUDA-Setup.
cat > "$APP/Contents/MacOS/Qalam" <<LAUNCH
#!/bin/bash
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/venv/bin/python" src/main.py
LAUNCH
chmod +x "$APP/Contents/MacOS/Qalam"

# Ad-hoc-Signatur, damit LaunchServices die App ohne Library-Validation-Probleme startet.
codesign --force --deep -s - "$APP" 2>/dev/null || true

# LaunchServices die neue/aktualisierte App bekannt machen.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

echo "Fertig: $APP"
echo "Start:  open \"$APP\"   (oder im Finder in ~/Applications doppelklicken)"
