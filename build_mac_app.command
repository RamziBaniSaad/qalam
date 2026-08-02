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

# --- Icon ------------------------------------------------------------------
# Ohne eigenes Icon zeigt macOS das leere Standard-Programmsymbol -- die App
# saehe dann nicht nach Qalam aus, egal wie sie heisst. Das Fenster- und
# Menueleisten-Symbol setzt src/main.py aus qalam-logo.png; fuer das BUNDLE
# braucht macOS aber zwingend eine .icns-Datei.
#
# Erzeugt wird sie hier aus demselben PNG, mit Bordmitteln (sips + iconutil),
# und nur wenn sie fehlt oder aelter als das PNG ist. Die .icns gehoert damit
# nicht ins Repo -- sie ist abgeleitet, kein Original.
LOGO="$PROJECT_DIR/assets/qalam-logo.png"
ICNS="$PROJECT_DIR/assets/qalam-logo.icns"
if [ -f "$LOGO" ] && { [ ! -f "$ICNS" ] || [ "$LOGO" -nt "$ICNS" ]; }; then
    SET="$(mktemp -d)/qalam.iconset"
    mkdir -p "$SET"
    for GROESSE in 16 32 128 256 512; do
        sips -z $GROESSE $GROESSE "$LOGO" --out "$SET/icon_${GROESSE}x${GROESSE}.png" >/dev/null 2>&1
        sips -z $((GROESSE * 2)) $((GROESSE * 2)) "$LOGO" --out "$SET/icon_${GROESSE}x${GROESSE}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$SET" -o "$ICNS" 2>/dev/null && echo "Icon gebaut: $ICNS"
    rm -rf "$(dirname "$SET")"
fi
if [ -f "$ICNS" ]; then
    cp "$ICNS" "$APP/Contents/Resources/qalam.icns"
    ICON_EINTRAG="    <key>CFBundleIconFile</key><string>qalam.icns</string>"
else
    ICON_EINTRAG="    <!-- kein Icon gefunden: assets/qalam-logo.png fehlt -->"
fi

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
$ICON_EINTRAG
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
