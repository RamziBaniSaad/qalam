"""
Tastenwaechter fuer den Umschalter (toggle_tools.vbs).

Meldet Strg+Alt+W direkt beim System an (RegisterHotKey) und startet bei
jedem Druck toggle_tools.vbs. Laeuft dauerhaft im Hintergrund, unabhaengig
davon, ob Qalam gerade an oder aus ist.

Warum nicht die Tastenkombination an der Verknuepfung (.lnk)?
Die fuehrt der Explorer aus, und der tut das nicht, solange ein Spiel im
Vollbild vorne ist -- genau dann, wenn man den Umschalter braucht. Eine
selbst angemeldete Tastenkombination wird vom System vor der Zustellung an
das Vordergrundfenster ausgewertet und kommt deshalb auch im Vollbild an.

Start: pythonw.exe toggle_hotkey.py  (ohne Fenster; nur Standardbibliothek)
Protokoll: umschalter.log neben diesem Skript.
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from datetime import datetime

HIER = os.path.dirname(os.path.abspath(__file__))
UMSCHALTER = os.path.join(HIER, "toggle_tools.vbs")
LOG = os.path.join(HIER, "umschalter.log")
LOG_MAX = 200_000  # Bytes; darueber wird beim Start auf die letzte Haelfte gekuerzt

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_W = 0x57
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
ERROR_ALREADY_EXISTS = 183
ERROR_HOTKEY_ALREADY_REGISTERED = 1409


def log(text):
    zeile = "%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(zeile)
    except OSError:
        pass


def log_kuerzen():
    try:
        if os.path.getsize(LOG) > LOG_MAX:
            with open(LOG, "rb") as f:
                f.seek(-LOG_MAX // 2, os.SEEK_END)
                rest = f.read()
            with open(LOG, "wb") as f:
                f.write(rest[rest.find(b"\n") + 1:])
    except OSError:
        pass


def vordergrund():
    """Name des Vordergrundprozesses -- fuer das Protokoll, damit sichtbar ist,
    in welchem Programm die Taste ankam (oder eben nicht)."""
    try:
        hwnd = user32.GetForegroundWindow()
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "pid %d (kein Zugriff)" % pid.value
        try:
            puffer = ctypes.create_unicode_buffer(1024)
            groesse = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, puffer, ctypes.byref(groesse)):
                return os.path.basename(puffer.value)
            return "pid %d" % pid.value
        finally:
            kernel32.CloseHandle(h)
    except Exception as e:  # noqa: BLE001 -- nur Protokoll, darf nie den Waechter reissen
        return "unbekannt (%s)" % e


def einzige_instanz():
    """Ein zweiter Waechter wuerde an RegisterHotKey scheitern und endlos
    warten -- besser sofort erkennen und gehen."""
    kernel32.CreateMutexW(None, False, "Local\\qalam-umschalter-taste")
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def taste_anmelden():
    """Wartet, falls noch jemand anderes die Kombination haelt (z. B. der
    Explorer, bis er eine geaenderte Verknuepfung nachgeladen hat)."""
    versuch = 0
    while True:
        if user32.RegisterHotKey(None, HOTKEY_ID, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_W):
            log("Strg+Alt+W angemeldet" + (" (nach %d Versuchen)" % versuch if versuch else ""))
            return
        fehler = kernel32.GetLastError()
        versuch += 1
        if versuch == 1 or versuch % 12 == 0:
            grund = "haelt schon jemand anderes" if fehler == ERROR_HOTKEY_ALREADY_REGISTERED else "Fehler %d" % fehler
            log("Strg+Alt+W nicht anmeldbar (%s), warte weiter" % grund)
        time.sleep(5)


def umschalten(laufend):
    if laufend is not None and laufend.poll() is None:
        log("Druck ignoriert, der vorige Umschaltvorgang laeuft noch")
        return laufend
    try:
        return subprocess.Popen(
            [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe"), UMSCHALTER],
            cwd=HIER,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except OSError as e:
        log("Umschalter konnte nicht gestartet werden: %s" % e)
        return laufend


def main():
    log_kuerzen()
    if not einzige_instanz():
        log("laeuft schon, zweite Instanz beendet sich")
        return
    if not os.path.exists(UMSCHALTER):
        log("toggle_tools.vbs fehlt neben dem Skript, Abbruch")
        return
    log("Waechter gestartet (pid %d)" % os.getpid())
    taste_anmelden()

    laufend = None
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            log("Druck im Vordergrund von: " + vordergrund())
            laufend = umschalten(laufend)
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnregisterHotKey(None, HOTKEY_ID)
    log("Waechter beendet")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("Waechter abgestuerzt: %r" % e)
        sys.exit(1)
