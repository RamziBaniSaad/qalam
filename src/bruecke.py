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
# Die Zwischenablage gehört unter Windows immer nur einem Prozess gleichzeitig.
# Direkt nach einem Strg+C hält der kopierende Prozess sie noch, und
# OpenClipboard scheitert schlicht. Ohne Wiederholung sah das aus wie "da ist
# kein Text" -- und die Prüfung, ob das Eingabefeld leer ist, hat daraufhin
# jedes Mal "leer" gesagt, egal was drinstand. Am 31.07.2026 gemessen.
def _mit_geduld(arbeit, versuche=12, pause=0.08):
    import win32clipboard
    for i in range(versuche):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(pause)
            continue
        try:
            return True, arbeit(win32clipboard)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    return False, None


def _zwischenablage_lesen():
    def _lies(wc):
        try:
            return wc.GetClipboardData(wc.CF_UNICODETEXT)
        except Exception:
            return None          # kein Text drin -- das ist keine Störung
    ok, wert = _mit_geduld(_lies)
    return wert if ok else None


def _zwischenablage_schreiben(text):
    def _schreib(wc):
        wc.EmptyClipboard()
        wc.SetClipboardText(text, wc.CF_UNICODETEXT)
        return True
    ok, _ = _mit_geduld(_schreib)
    return ok


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


# Wenn eine Markierung mehr als so viele Zeichen liefert, war es nicht das
# Eingabefeld, sondern der Chatverlauf. Ramzis längste Nachrichten sind lange
# Diktate von zwei- bis dreitausend Zeichen; ein Chatverlauf ist ein Vielfaches
# davon. Die Grenze trennt beides sicher.
CHAT_STATT_FELD = 8000

# KEIN \x00 darin: die Windows-Zwischenablage behandelt das Null-Zeichen als
# Ende der Zeichenkette und legt effektiv einen leeren Text ab. Der Marker war
# damit nicht wiederzuerkennen, und die Prüfung meldete jedes Mal "leer" --
# egal was wirklich im Feld stand.
MARKE = '~~noor-marke-4711~~'

# Das Zeichen, mit dem geprüft wird, ob ein Klick wirklich in einem Eingabefeld
# gelandet ist. Muss ein ganz gewöhnliches Schriftzeichen sein -- Sonderzeichen
# lösen in Oberflächen gern Tastenkürzel aus.
PROBE = 'x'


ABSTAND_MERKER = os.path.join(tempfile.gettempdir(), 'noor-bruecke-abstand.txt')


def _gemerkter_abstand():
    """Welcher Abstand hat beim letzten Mal getroffen?

    Die Oberfläche ändert sich selten. Sich den Treffer zu merken, spart beim
    nächsten Mal das Durchprobieren -- und jeder Fehlversuch markiert sichtbar
    den Chatverlauf, das sieht Ramzi."""
    try:
        with open(ABSTAND_MERKER, encoding='utf-8') as f:
            return int(f.read().strip())
    except Exception:
        return None


def _merke_abstand(abstand):
    try:
        with open(ABSTAND_MERKER, 'w', encoding='utf-8') as f:
            f.write(str(abstand))
    except OSError:
        pass


def _klick(x, y):
    """Einmal klicken und den Mauszeiger zurückstellen.

    Zurückstellen, weil Ramzi nicht merken soll, dass jemand seine Maus
    benutzt hat -- und weil ein Zeiger, der irgendwo stehen bleibt,
    Schwebe-Menüs aufklappt."""
    from pynput.mouse import Controller as Maus, Button
    maus = Maus()
    vorher = maus.position
    maus.position = (x, y)
    time.sleep(0.12)
    maus.click(Button.left)
    time.sleep(0.3)
    maus.position = vorher


def fokussiere_eingabefeld(hwnd, tastatur):
    """Das Eingabefeld treffen -- und es BEWEISEN, statt es zu hoffen.

    Drei Anläufe am 31.07.2026, jeder auf seine Art danebengegangen:

      1. Ohne Klick stand nicht fest, welches Element den Tastaturfokus hat.
         Strg+A markierte den Chatverlauf.
      2. Klick auf `unterkante - 60` traf die Werkzeugleiste UNTER dem Feld.
      3. Die Prüfung selbst war blind: sie legte eine Marke in die
         Zwischenablage und wollte sehen, ob sie ein Strg+C überlebt. Electron
         **leert** die Zwischenablage aber beim Kopieren einer leeren Auswahl --
         danach ist gar nichts mehr da, und "gar nichts" sah aus wie "leeres
         Feld". Die Prüfung sagte deshalb IMMER "leer", egal was drinstand.
         Auch der Weg über die Windows-Zugänglichkeit fiel aus: Electron gibt
         seinen Baum nur an Vorlesehilfen heraus, alle Felder kamen ohne
         Ausmaße und ohne Wert zurück.

    Was jetzt passiert, hängt an nichts davon ab: hinklicken, **ein Zeichen
    tippen** und nachsehen, ob es angekommen ist. Kommt es an, war es ein
    Eingabefeld -- das ist kein Indiz, das ist ein Beweis. Was vorher drinstand,
    fällt beim Ausschneiden mit ab und wird bei Abbruch zurückgelegt.

    Rückgabe: (getroffen, feld_ist_leer, vorhandener_text)
    """
    from pynput.keyboard import Key

    def _versuch():
        """Prüfzeichen tippen und nachsehen, ob es angekommen ist.

        Rückgabe: (angekommen, was_vorher_drinstand)"""
        tastatur.press(PROBE)
        tastatur.release(PROBE)
        time.sleep(0.25)

        _zwischenablage_schreiben(MARKE)
        time.sleep(0.12)
        _taste(tastatur, 'a')
        time.sleep(0.22)
        _taste(tastatur, 'x')          # ausschneiden, nicht kopieren
        time.sleep(0.45)
        inhalt = _zwischenablage_lesen() or ''

        if not inhalt.endswith(PROBE) or len(inhalt) > CHAT_STATT_FELD:
            _taste(tastatur, Key.end, mit_strg=False)   # Markierung auflösen
            return False, ''
        return True, inhalt[:-len(PROBE)]

    # --- Erst ganz ohne Maus ------------------------------------------------
    # Chat-Oberflächen lenken einen Tastendruck von selbst ins Eingabefeld --
    # deshalb hat der allererste Brückenversuch auch ohne jeden Klick
    # funktioniert. Wenn das reicht, ist es der mit Abstand robusteste Weg:
    # keine Koordinaten, nichts, was sich beim nächsten Fensterumbau verschiebt.
    ok, vorher = _versuch()
    if ok:
        return True, (vorher.strip() == ''), vorher

    r = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False, False, ''
    x = (r.left + r.right) // 2

    # --- Sonst klicken, und zwar nach Erfahrung ------------------------------
    # Abstände von der Unterkante, weil das Feld unten sitzt. Sie schwanken
    # aber: über dem Feld kann eine weitere Leiste stehen (der Zweig- und
    # PR-Balken), und das Feld wächst mit seinem Inhalt nach oben. Genau daran
    # ist der Klick am 31.07.2026 einmal zu tief gelandet. Deshalb mehrere
    # Höhen -- und die, die getroffen hat, wird gemerkt und beim nächsten Mal
    # zuerst probiert.
    kandidaten = [120, 95, 145, 170, 75, 195, 225]
    gemerkt = _gemerkter_abstand()
    if gemerkt:
        kandidaten = [gemerkt] + [k for k in kandidaten if k != gemerkt]

    for abstand in kandidaten:
        y = r.bottom - abstand
        if y <= r.top + 40:
            continue
        _klick(x, y)
        ok, vorher = _versuch()
        if ok:
            _merke_abstand(abstand)
            return True, (vorher.strip() == ''), vorher

    return False, False, ''


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

        getroffen, leer, seiner = fokussiere_eingabefeld(hwnd, tastatur)
        if not getroffen:
            _merker_weg()
            return False, 'Ich finde das Eingabefeld nicht. Da schreibe ich nichts blind hinein.'
        if not leer:
            # Sein Text hängt jetzt in der Zwischenablage -- zurücklegen, bevor
            # abgebrochen wird. Sonst hätte die Prüfung genau das zerstört, was
            # sie schützen sollte.
            _zwischenablage_schreiben(seiner)
            time.sleep(0.15)
            _taste(tastatur, 'v')
            time.sleep(0.3)
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
