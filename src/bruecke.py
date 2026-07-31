"""Die Brücke — was Qalam hört und nicht selbst kann, geht an Noor.

    Weckwort  ->  Reflex (lokal)          bleibt hier
              ->  alles andere            geht über diese Brücke

WARUM NICHT `claude -p`:
    Der naheliegende Weg wäre, für jeden Satz einen eigenen Claude-Lauf zu
    starten. Auf diesem Rechner scheitert das an der Anmeldung: der gebündelte
    CLI (`AppData/Roaming/Claude/claude-code/<version>/claude.exe`) meldet
    "Not logged in", weil die Zugangsdaten der Desktop-App nicht in
    `~/.claude/.credentials.json` liegen. Geprüft am 31.07.2026.

    Es wäre aber auch ohne diese Hürde der schlechtere Weg: ein eigener Lauf
    hätte weder den Verlauf dieser Sitzung noch das, was wir gerade zusammen
    tun. Er wüsste jedes Mal wieder nichts.

WAS STATTDESSEN PASSIERT:
    Der Auftrag wird in das **laufende** Claude-Fenster geschrieben — dieselbe
    Sitzung, in der Ramzi sonst tippt. Damit ist der Kontext geschenkt statt
    teuer nachgebaut, und es kostet keinen zweiten Zugang.

    Der Rückweg hängt am Stop-Hook `noor/werkzeuge/noor-sprich.ps1`: sobald die
    Antwort steht, liest er sie aus dem Transkript und spricht sie. Damit
    getippte Fragen still bleiben, hinterlässt diese Datei hier eine Merkdatei —
    nur wenn die liegt, wird gesprochen.

Alleine ausprobieren:
    python src/bruecke.py "Was steht heute an?"
"""
import ctypes
import json
import os
import sys
import tempfile
import time

IS_WINDOWS = sys.platform == 'win32'

MERKER = os.path.join(tempfile.gettempdir(), 'noor-bruecke.json')

# Der Titel des Fensters, in das geschrieben wird. Die Desktop-App heißt schlicht
# "Claude". Die Tafel heißt "Noor" und wird davon nicht getroffen.
FENSTER_TITEL = 'Claude'

# Damit ich in der Antwort weiß, dass gesprochen wurde und nicht getippt — eine
# vorgelesene Antwort muss kurz und ohne Code sein.
VORSATZ = '(gesprochen) '


# --------------------------------------------------------------------------
# Fenster finden und nach vorn holen
# --------------------------------------------------------------------------
if IS_WINDOWS:
    _user32 = ctypes.WinDLL('user32', use_last_error=True)
    _ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def finde_fenster(titel=FENSTER_TITEL):
    """Das sichtbare Fenster mit genau diesem Titel, sonst das erste, das ihn
    enthält. Genau zuerst, weil "Claude" als Teilstring auch in Browsertiteln
    steckt — und dann würde der Auftrag in eine Webseite getippt."""
    if not IS_WINDOWS:
        return None

    genau, enthaelt = [], []

    def _rueckruf(h, _p):
        if not _user32.IsWindowVisible(h):
            return True
        laenge = _user32.GetWindowTextLengthW(h)
        if laenge == 0:
            return True
        puffer = ctypes.create_unicode_buffer(laenge + 1)
        _user32.GetWindowTextW(h, puffer, laenge + 1)
        t = puffer.value
        if t == titel:
            genau.append(h)
        elif titel.lower() in t.lower():
            enthaelt.append(h)
        return True

    _user32.EnumWindows(_ENUM(_rueckruf), None)
    if genau:
        return genau[0]
    if enthaelt:
        return enthaelt[0]
    return None


def hole_nach_vorn(hwnd):
    """Fenster nach vorn holen.

    Windows lässt einen Hintergrundprozess nicht einfach den Vordergrund an
    sich reißen. Der anerkannte Weg ist, sich kurz an den Eingabe-Thread des
    aktuellen Vordergrundfensters zu hängen — dann gilt man als berechtigt."""
    SW_RESTORE = 9
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)

    vorn = _user32.GetForegroundWindow()
    if vorn == hwnd:
        return True

    mein = _user32.GetWindowThreadProcessId(vorn, None)
    sein = _user32.GetWindowThreadProcessId(hwnd, None)
    if mein and sein and mein != sein:
        _user32.AttachThreadInput(mein, sein, True)
        ok = bool(_user32.SetForegroundWindow(hwnd))
        _user32.AttachThreadInput(mein, sein, False)
    else:
        ok = bool(_user32.SetForegroundWindow(hwnd))

    time.sleep(0.25)
    return ok or _user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------
# Text hineinschreiben
# --------------------------------------------------------------------------
def _zwischenablage_lesen():
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _zwischenablage_schreiben(text):
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def _einfuegen_und_senden():
    from pynput.keyboard import Controller, Key
    tastatur = Controller()
    with tastatur.pressed(Key.ctrl):
        tastatur.press('v')
        tastatur.release('v')
    time.sleep(0.35)
    tastatur.press(Key.enter)
    tastatur.release(Key.enter)


def _merker_weg():
    """Merkdatei wieder wegräumen, wenn das Absenden doch nicht geklappt hat.

    Sonst läge sie bis zu einer Viertelstunde herum und die nächste getippte
    Antwort würde aus dem Nichts vorgelesen."""
    try:
        os.remove(MERKER)
    except OSError:
        pass


def sende(auftrag, fenster_titel=FENSTER_TITEL):
    """Auftrag in die laufende Claude-Sitzung schreiben und abschicken.

    Rückgabe: (geklappt, meldung) — die Meldung ist so formuliert, dass sie
    vorgelesen werden kann, falls es schiefging."""
    auftrag = ' '.join((auftrag or '').split())
    if not auftrag:
        return False, 'Da war kein Auftrag dabei.'
    if not IS_WINDOWS:
        return False, 'Die Brücke gibt es bisher nur unter Windows.'

    hwnd = finde_fenster(fenster_titel)
    if not hwnd:
        return False, 'Ich finde das Claude-Fenster nicht. Ist es offen?'

    # Die Merkdatei ZUERST: sie ist das Signal für den Stop-Hook. Läge sie erst
    # nach dem Absenden, könnte eine sehr kurze Antwort schneller fertig sein
    # als das Schreiben hier -- dann bliebe die Antwort stumm.
    try:
        with open(MERKER, 'w', encoding='utf-8') as f:
            json.dump({'gestellt': time.strftime('%Y-%m-%dT%H:%M:%S'),
                       'auftrag': auftrag}, f, ensure_ascii=False)
    except OSError:
        pass

    vorher = _zwischenablage_lesen()
    if not _zwischenablage_schreiben(VORSATZ + auftrag):
        _merker_weg()
        return False, 'Ich komme an die Zwischenablage nicht heran.'

    try:
        if not hole_nach_vorn(hwnd):
            _merker_weg()
            return False, 'Ich bekomme das Claude-Fenster nicht nach vorn.'
        time.sleep(0.2)
        _einfuegen_und_senden()
    finally:
        # Zwischenablage zurückgeben -- Ramzi hat da oft etwas drin, das er
        # gleich braucht. Kurz warten, sonst überhole ich das eigene Einfügen.
        time.sleep(0.4)
        if vorher is not None:
            _zwischenablage_schreiben(vorher)

    return True, None


# --------------------------------------------------------------------------
if __name__ == '__main__':
    text = ' '.join(sys.argv[1:]) or 'Sag kurz Hallo.'
    ok, meldung = sende(text)
    print('abgeschickt' if ok else f'nicht abgeschickt: {meldung}')
