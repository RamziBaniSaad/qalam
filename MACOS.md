# Qalam auf macOS

Diese Datei beschreibt das Einrichten und die Besonderheiten der macOS-Version.
Die Codebasis ist **eine** gemeinsame Codebasis für Windows und macOS – der
plattformabhängige Teil (Clipboard, Paste-Tastenkürzel, Audio-Steuerung) wird
zur Laufzeit über `sys.platform` / `platform.system()` gewählt. Windows läuft
unverändert weiter.

> ⚠️ **Status (2026-07-17): auf Windows geschrieben, auf macOS noch NICHT
> getestet.** Der Code ist so gebaut, dass er auf macOS starten und
> grundsätzlich funktionieren sollte, aber Mikrofon-/Bedienungshilfen-Rechte
> und die Tastatur-Injection müssen auf dem Mac real verifiziert werden.

## 1. Setup

Voraussetzung: Python 3.12 (`brew install python@3.12`) und – für die lokale
Strukturierung – [Ollama](https://ollama.com) (`brew install ollama`).

```bash
cd qalam
python3 -m venv venv
source venv/bin/activate
pip install .            # torch/torchaudio installieren sich als CPU-Build
```

Auf macOS gibt es kein CUDA – `faster-whisper` läuft auf der CPU (bzw. Metal je
nach Build). Für Apple Silicon reicht die CPU für die kleineren Modelle gut aus;
`run.py` überspringt die CUDA-Einrichtung automatisch (der Windows-CUDA-Pfad
existiert dort nicht).

## 2. Starten

```bash
./run_mac.command        # oder im Finder doppelklicken
```

Beim allerersten Start ggf. einmalig ausführbar machen:

```bash
chmod +x run_mac.command
```

## 3. macOS-Berechtigungen (WICHTIG)

macOS blockiert Mikrofon-Zugriff und synthetische Tastatureingaben, bis man sie
explizit erlaubt. **Ohne diese Rechte tippt Qalam nichts und gibt keine
Fehlermeldung aus.**

Unter **Systemeinstellungen → Datenschutz & Sicherheit**:

1. **Mikrofon** → das Terminal/den Launcher erlauben (fürs Aufnehmen).
2. **Bedienungshilfen** (Accessibility) → das Terminal/den Launcher erlauben
   (fürs Tippen des Textes und fürs Einfügen mit Cmd+V).
3. **Eingabeüberwachung** (Input Monitoring) → ebenfalls erlauben (für den
   globalen Aufnahme-Hotkey via `pynput`).

Nach dem Erteilen der Rechte die App einmal neu starten.

## 4. Plattform-Unterschiede im Code

| Bereich | Windows | macOS/Linux |
|---|---|---|
| Clipboard | `win32clipboard` (alle Formate) | `pyperclip` (nur Text), `clipboard_utils.py` |
| Einfügen | Ctrl+V | **Cmd+V** (macOS), Ctrl+V (Linux) |
| Audio pausieren | pycaw/COM | `osascript` (macOS), `pactl` (Linux) – bereits in `media_controller.py` |
| Launcher | `launcher.bat` / `.vbs` | `run_mac.command` |

## 5. Noch offen / später auf dem Mac zu erledigen

- **Real testen**: Hotkey-Aufnahme, Transkription, Tippen, Cmd+V-Einfügen, der
  Clipboard-Cleanup-Shortcut.
- **Autostart** (optional): LaunchAgent-`.plist` in `~/Library/LaunchAgents/`
  anlegen, die `run_mac.command` startet.
- **VRAM-/Prozess-Toggle** (Pendant zu `toggle_tools.vbs` unter Windows): auf
  macOS z. B. via `skhd` oder Kurzbefehle/Automator – bewusst noch nicht gebaut,
  weil blind nicht testbar.
