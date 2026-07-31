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
    # Ohne restype liefert ctypes ein vorzeichenbehaftetes 32-Bit-Ergebnis --
    # Fenstergriffe oberhalb von 0x7FFFFFFF kämen dann negativ zurück und kein
    # Vergleich würde je stimmen.
    _user32.GetForegroundWindow.restype = ctypes.c_void_p


def _gleich(a, b):
    """Zwei Fenstergriffe vergleichen, egal wie ctypes sie gerade verpackt hat."""
    if a is None or b is None:
        return False
    return (int(a) & 0xFFFFFFFFFFFFFFFF) == (int(b) & 0xFFFFFFFFFFFFFFFF)


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
    if _gleich(vorn, hwnd):
        return True

    mein = _user32.GetWindowThreadProcessId(vorn, None)
    sein = _user32.GetWindowThreadProcessId(hwnd, None)
    if mein and sein and mein != sein:
        _user32.AttachThreadInput(mein, sein, True)
        _user32.SetForegroundWindow(hwnd)
        _user32.AttachThreadInput(mein, sein, False)
    else:
        _user32.SetForegroundWindow(hwnd)

    time.sleep(0.35)

    # NUR nachmessen, nie dem Rückgabewert glauben. SetForegroundWindow meldet
    # Erfolg, obwohl Windows den Wechsel verweigert hat -- gemessen am
    # 31.07.2026: der Aufruf sagte True, vorn stand weiterhin ein anderes
    # Fenster, und der Text landete in dessen Eingabefeld. Ein falsches "hat
    # geklappt" ist hier teurer als ein ehrliches "hat nicht geklappt".
    return _gleich(_user32.GetForegroundWindow(), hwnd)


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


def _taste(tastatur, buchstabe, mit_strg=True):
    from pynput.keyboard import Key
    if mit_strg:
        with tastatur.pressed(Key.ctrl):
            tastatur.press(buchstabe)
            tastatur.release(buchstabe)
    else:
        tastatur.press(buchstabe)
        tastatur.release(buchstabe)


class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


def klick_ins_eingabefeld(hwnd):
    """In das Eingabefeld klicken, bevor irgendetwas getippt wird.

    Ohne das ist nicht bestimmt, welches Element den Tastaturfokus hat. Am
    31.07.2026 hat genau das zugeschlagen: Strg+A hat den **Chatverlauf**
    markiert statt des Eingabefeldes, die Prüfung meldete "da steht was" und
    die Brücke hat grundlos abgebrochen -- bei einem tatsächlich leeren Feld.

    Das Eingabefeld sitzt unten im Fenster über die volle Breite. Ein Klick
    knapp über den unteren Rand, mittig, trifft es. Der Mauszeiger wird danach
    zurückgestellt -- Ramzi soll nicht merken, dass jemand seine Maus benutzt
    hat.
    """
    from pynput.mouse import Controller as Maus, Button
    r = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    x = (r.left + r.right) // 2
    y = r.bottom - 60          # gemessen an der Desktop-App, Eingabezeile unten
    if y <= r.top:
        return False

    maus = Maus()
    vorher = maus.position
    maus.position = (x, y)
    time.sleep(0.12)
    maus.click(Button.left)
    time.sleep(0.25)
    maus.position = vorher
    return True


def _eingabefeld_ist_leer(tastatur):
    """Steht schon etwas im Eingabefeld?

    Setzt voraus, dass vorher ins Feld geklickt wurde -- sonst misst diese
    Prüfung irgendein anderes Element und ihre Antwort ist wertlos.

    Alles markieren, kopieren, lesen, Markierung mit Ende wieder auflösen. Kam
    die gesetzte Marke unverändert zurück, war nichts zu kopieren: das Feld ist
    leer.
    """
    from pynput.keyboard import Key
    marke = '\x00noor-leer\x00'
    _zwischenablage_schreiben(marke)
    time.sleep(0.15)
    _taste(tastatur, 'a')
    time.sleep(0.25)
    _taste(tastatur, 'c')
    time.sleep(0.45)
    inhalt = _zwischenablage_lesen()
    # Markierung auflösen, sonst überschreibt das folgende Einfügen sie.
    _taste(tastatur, Key.end, mit_strg=False)
    time.sleep(0.15)
    return inhalt == marke or not (inhalt or '').strip()


def _einfuegen_und_senden(tastatur):
    from pynput.keyboard import Key
    _taste(tastatur, 'v')
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

    from pynput.keyboard import Controller
    tastatur = Controller()

    vorher = _zwischenablage_lesen()

    try:
        # Reihenfolge mit Absicht: erst nach vorn, dann nachsehen, ob das Feld
        # frei ist, und ERST DANN den Auftrag in die Zwischenablage legen. Wer
        # zuerst schreibt und dann merkt, dass er nicht darf, hat Ramzis
        # Zwischenablage schon überschrieben.
        if not hole_nach_vorn(hwnd):
            _merker_weg()
            return False, 'Ich bekomme das Claude-Fenster nicht nach vorn.'
        time.sleep(0.2)

        # Erst hinklicken, dann messen. Ohne den Klick misst die Prüfung
        # darunter irgendein anderes Element.
        klick_ins_eingabefeld(hwnd)

        if not _eingabefeld_ist_leer(tastatur):
            _merker_weg()
            return False, 'Du hast noch etwas im Eingabefeld. Ich warte.'

        if not _zwischenablage_schreiben(VORSATZ + auftrag):
            _merker_weg()
            return False, 'Ich komme an die Zwischenablage nicht heran.'

        _einfuegen_und_senden(tastatur)
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
