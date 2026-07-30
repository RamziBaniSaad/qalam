# Hinweise für KI-Werkzeuge, die an Qalam arbeiten

> Keine Befehle, sondern Gedächtnisstützen. Wer einen guten Grund hat, davon
> abzuweichen, weicht ab — und schreibt dazu, warum.

## Was Qalam ist

Ein Diktierwerkzeug für Ramzi, entstanden als Fork von
[WhisperWriter](https://github.com/savbell/whisper-writer) (GPLv3). Es ist
inzwischen mehr als das: es ist die **Sprachschicht** — Ohren und Mund.

- `src/transcription.py` — Zuhören (faster-whisper, lokal)
- `src/voice_output.py` — Sprechen (Piper, lokal, satzweise)
- `src/wake_word.py` — Weckwort, ohne Tastendruck
- `src/assistant.py` — Reflexe (Uhrzeit, Medien, schlafen) und Weiterleitung

## Die wichtigste Regel: zwei Betriebssysteme, ein Werkzeug

Qalam läuft auf **Windows** (Ramzis Privat-PC) **und macOS** (sein MacBook).
Beide Geräte sind für ihn gleichwertig — fällt eines aus, arbeitet er auf dem
anderen weiter. Das ist der Grund für diese Datei.

**Wer hier etwas ändert, denkt beide Seiten mit:**

1. Keine Windows-eigene Bibliothek ohne Ausweichweg. `winsound` ist in
   `status_window.py` bereits so gebaut: Windows → `winsound`, macOS → `afplay`,
   Linux → `paplay`/`aplay`. Neue Stellen genauso.
2. Pfade nie zusammenkleben, immer `os.path.join`.
3. Tastenkombinationen: Windows `Strg`, macOS `Cmd`. Siehe `clipboard_utils.py`.
4. **Ungetestet ist ungetestet.** Wer auf Windows baut und macOS nicht prüfen
   kann, schreibt es in den Commit — nicht „läuft", sondern „auf macOS noch
   nicht getestet". Eine falsche Sicherheitsangabe ist schlimmer als eine
   fehlende.

## Was NICHT plattformübergreifend ist — und bewusst so bleibt

- `toggle_tools.vbs` und `signal.ps1` (Strg+Alt+W): reines Windows. Auf dem
  MacBook wird nicht gezockt, dort braucht es keinen VRAM-Schalter.
- `werkzeuge/noor-zeigen.ps1` liegt im **noor**-Repo, nicht hier, und ist
  Windows-only (Win32-Fensterverwaltung). Das macOS-Gegenstück wäre eine eigene
  Umsetzung über AppleScript und Spaces.

## Kleinigkeiten, die schon mal wehgetan haben

- **Keine BOM** in Dateien, die Werkzeuge parsen. PowerShell 5.1 schreibt bei
  `Set-Content -Encoding UTF8` eine — git und YAML stolpern darüber.
- `config.yaml` **immer** mit `encoding='utf-8'` lesen und schreiben, sonst wird
  der deutsche Prompt zu Kauderwelsch.
- Das Weckwort ist während einer laufenden Aufnahme taub (`.aufnahme.lock`).
  Wer an `updateStatus` etwas ändert, prüft, dass die Sperre gesetzt **und**
  wieder gelöst wird.
- Emojis gehören nicht in die Oberfläche und nicht in Ausgaben, die Ramzi sieht.

## Herkunft und Lizenz

**GPLv3, und das bleibt so.** Die ursprünglichen Autoren stehen zu Recht in der
Historie; ihre Nennung ist Bedingung der Lizenz, kein Versehen. Qalam darf
verändert und weitergegeben werden, aber nicht geschlossen werden.
