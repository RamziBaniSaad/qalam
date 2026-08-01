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

# Die Zeichen, mit denen geprüft wird, ob ein Klick wirklich in einem
# Eingabefeld gelandet ist.
#
# Gewöhnliche Schriftzeichen, keine Sonderzeichen -- die lösen in Oberflächen
# gern Tastenkürzel aus. Aber ZWEI davon, und eine Folge, die in deutschem Text
# praktisch nicht vorkommt: die Probe muss im ausgeschnittenen Text
# wiederzufinden sein, auch wenn sie nicht am Ende steht. Ein einzelnes "x"
# steht irgendwo in "extra" oder "Text" auch, und dann wäre nicht mehr
# entscheidbar, welches davon die Probe war.
PROBE = 'xq'


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

    Wird nichts getroffen, steht in `vorhandener_text` trotzdem, was dabei
    versehentlich aus einem Feld herausgeschnitten wurde -- damit der Aufrufer
    es zurücklegen kann. Am 31.07.2026 hat Ramzi genau daran einen langen,
    gesprochenen Text verloren.
    """
    from pynput.keyboard import Key

    def _versuch():
        """Prüfzeichen tippen und nachsehen, ob es angekommen ist.

        Rückgabe: (angekommen, was_vorher_drinstand, was_gerettet_werden_muss)"""
        # Schreibmarke ans Ende, BEVOR die Probe getippt wird.
        #
        # Ohne das galt die stille Annahme, Ramzis Schreibmarke stünde schon am
        # Ende seines Textes. Beim Tippen stimmt das meistens -- aber sobald er
        # zum Korrigieren mitten in seinen Satz zurückklickt, landet die Probe
        # dort, und dann fand sich am Ende nichts. Gemessen am 31.07.2026:
        # aus "Das hier tippe ich" wurde 'xDas hier tippe ich'. Die Prüfung
        # meldete daraufhin "kein Eingabefeld" -- nachdem sie seinen Text
        # bereits ausgeschnitten hatte. Sein Text war damit weg.
        _taste(tastatur, Key.end)
        time.sleep(0.12)
        tastatur.type(PROBE)
        time.sleep(0.25)

        _zwischenablage_schreiben(MARKE)
        time.sleep(0.12)
        _taste(tastatur, 'a')
        time.sleep(0.22)
        _taste(tastatur, 'x')          # ausschneiden, nicht kopieren
        time.sleep(0.45)
        inhalt = _zwischenablage_lesen() or ''

        # Zu lang heißt: das war der Chatverlauf, nicht das Feld. Ausgeschnitten
        # wurde dabei nichts (ein Verlauf ist nicht bearbeitbar).
        if len(inhalt) > CHAT_STATT_FELD or PROBE not in inhalt:
            _taste(tastatur, Key.end, mit_strg=False)   # Markierung auflösen
            # ABER: hängt hier etwas, das weder die Marke noch der Chatverlauf
            # ist, dann wurde eben doch etwas ausgeschnitten -- ein Feld, in dem
            # die Probe nicht angekommen ist. Das ist Ramzis Text, und er darf
            # nicht verschwinden, nur weil die Prüfung ihn nicht wiedererkennt.
            gerettet = ''
            if inhalt and inhalt != MARKE and len(inhalt) <= CHAT_STATT_FELD:
                gerettet = inhalt
            return False, '', gerettet

        # Die Probe ist wieder da -- damit ist BEWIESEN, dass hier ein
        # bearbeitbares Feld ist, und zwar unabhängig davon, wo sie gelandet
        # ist. Am Ende ist der Normalfall; sonst die eine Fundstelle entfernen.
        if inhalt.endswith(PROBE):
            return True, inhalt[:-len(PROBE)], ''
        return True, inhalt.replace(PROBE, '', 1), ''

    # --- Erst ganz ohne Maus ------------------------------------------------
    # Chat-Oberflächen lenken einen Tastendruck von selbst ins Eingabefeld --
    # deshalb hat der allererste Brückenversuch auch ohne jeden Klick
    # funktioniert. Wenn das reicht, ist es der mit Abstand robusteste Weg:
    # keine Koordinaten, nichts, was sich beim nächsten Fensterumbau verschiebt.
    ok, vorher, gerettet = _versuch()
    if ok:
        return True, (vorher.strip() == ''), vorher
    # Wurde beim Fehlversuch etwas herausgeschnitten, ist damit BEWIESEN, dass
    # hier ein Eingabefeld ist -- sonst wäre nichts weggegangen. Dann ist
    # weitersuchen und -klicken falsch: es würde nur mehr kaputtmachen. Zurück
    # damit an den Aufrufer, der legt es wieder hinein.
    if gerettet:
        return False, False, gerettet

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
        ok, vorher, gerettet = _versuch()
        if ok:
            _merke_abstand(abstand)
            return True, (vorher.strip() == ''), vorher
        if gerettet:
            return False, False, gerettet

    return False, False, ''


def _einfuegen_und_senden(tastatur):
    from pynput.keyboard import Key
    _taste(tastatur, 'v')
    time.sleep(0.35)
    tastatur.press(Key.enter)
    tastatur.release(Key.enter)


def _zurueckschreiben(tastatur, war_leer, seiner):
    """Ramzis eigenen Text zurück ins Eingabefeld legen -- ohne abzuschicken.

    Tut nichts, wenn das Feld ohnehin leer war. Wartet vorher kurz: nach dem
    Abschicken braucht die Oberfläche einen Moment, bis sie das Feld geräumt
    hat, und wer zu früh einfügt, hängt seinen Text an den gerade abgeschickten
    Auftrag."""
    if war_leer or not seiner:
        return
    time.sleep(0.6)
    if not _zwischenablage_schreiben(seiner):
        return
    time.sleep(0.15)
    _taste(tastatur, 'v')
    time.sleep(0.3)


RETTUNG = os.path.join(tempfile.gettempdir(), 'noor-bruecke-rettung.txt')


def _rettung_schreiben(auftrag, seiner=''):
    """Alles Gesprochene und Getippte auf die Platte legen, bevor etwas
    schiefgehen kann.

    Ramzi hat am 31.07.2026 einen langen, gesprochenen Text verloren, weil die
    Brücke das Claude-Fenster nicht nach vorn bekam und danach nichts mehr da
    war -- nicht im Feld, nicht in der Zwischenablage. Sein Satz dazu: bevor
    überhaupt versucht wird einzufügen, muss das Gesagte gesichert sein.

    Eine Datei und nicht nur die Zwischenablage: die Zwischenablage hat immer
    nur einen Platz, und auf dem Weg durch die Fokusprüfung wird sie mehrfach
    gebraucht. Was auf der Platte liegt, übersteht auch einen Absturz."""
    try:
        with open(RETTUNG, 'w', encoding='utf-8') as f:
            f.write(f'--- gesprochen {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n')
            f.write(auftrag + '\n')
            if seiner:
                f.write('\n--- was im Eingabefeld stand ---\n')
                f.write(seiner + '\n')
    except OSError:
        pass


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

    # WAS LAG VORHER VORN? Wird am Ende zurückgeholt.
    #
    # Ramzis Frage vom 02.08.2026: "wenn der das Eingabefeld findet, fokussiert
    # er darauf und schickt ab -- und dadurch geht mein Minecraft-Vollbild zu.
    # Gibt es da eine Lösung?" Ja, und es ist nicht die naheliegende: den Fokus
    # gar nicht zu nehmen geht NICHT, weil ohne Fokus kein Zeichen im Feld
    # landet (drei Fehlschläge am 31.07.2026, siehe fokussiere_eingabefeld).
    #
    # Also nehmen wir ihn -- und geben ihn danach zurück. Aus einem
    # "Vollbild ist zu" wird damit ein kurzes Flackern. Der Weg ist in
    # derselben Nacht schon bewiesen worden: noor-bruecke-probe.py stellt
    # zwischen zwanzig Messungen genau so die Ausgangslage wieder her.
    vorheriges_fenster = 0
    try:
        vorheriges_fenster = int(_user32.GetForegroundWindow() or 0)
        if vorheriges_fenster == int(hwnd):
            vorheriges_fenster = 0      # Claude lag schon vorn, nichts zu tun
    except Exception:
        pass

    # Die Merkdatei ZUERST: sie ist das Signal für den Stop-Hook. Läge sie erst
    # nach dem Absenden, könnte eine sehr kurze Antwort schneller fertig sein
    # als das Schreiben hier -- dann bliebe die Antwort stumm.
    try:
        with open(MERKER, 'w', encoding='utf-8') as f:
            json.dump({'gestellt': time.strftime('%Y-%m-%dT%H:%M:%S'),
                       'auftrag': auftrag}, f, ensure_ascii=False)
    except OSError:
        pass

    # Sichern, BEVOR irgendetwas angefasst wird. Von hier an kann nichts mehr
    # spurlos verschwinden -- siehe _rettung_schreiben().
    _rettung_schreiben(auftrag)

    from pynput.keyboard import Controller
    tastatur = Controller()

    vorher = _zwischenablage_lesen()
    # Bei einem Fehlschlag bleibt bewusst etwas Wichtiges in der Zwischenablage
    # liegen. Dann darf sie am Ende NICHT auf ihren alten Inhalt zurückgesetzt
    # werden -- sonst räumt die Aufräumarbeit genau die Rettung weg.
    behalten = False

    def _in_die_zwischenablage_retten(text):
        nonlocal behalten
        if text and _zwischenablage_schreiben(text):
            behalten = True
            return True
        return False

    try:
        # Reihenfolge mit Absicht: erst nach vorn, dann nachsehen, ob das Feld
        # frei ist, und ERST DANN den Auftrag in die Zwischenablage legen. Wer
        # zuerst schreibt und dann merkt, dass er nicht darf, hat Ramzis
        # Zwischenablage schon überschrieben.
        if not hole_nach_vorn(hwnd):
            _merker_weg()
            # Hier ist noch nichts angefasst worden, aber der Auftrag wäre weg.
            # Also in die Zwischenablage damit -- Ramzi drückt einmal Strg+V
            # und hat ihn zurück.
            if _in_die_zwischenablage_retten(auftrag):
                return False, ('Ich bekomme das Claude-Fenster nicht nach vorn. '
                               'Dein Satz liegt in der Zwischenablage.')
            return False, 'Ich bekomme das Claude-Fenster nicht nach vorn.'
        time.sleep(0.2)

        getroffen, leer, seiner = fokussiere_eingabefeld(hwnd, tastatur)
        if not getroffen:
            _merker_weg()
            # `seiner` ist hier nicht leer, wenn die Prüfung etwas aus einem
            # Feld herausgeschnitten hat, ohne es wiederzuerkennen. Dann gehört
            # SEIN Text in die Zwischenablage, nicht mein Auftrag -- meinen
            # kann er sich wieder sagen, seinen getippten nicht.
            _rettung_schreiben(auftrag, seiner)
            if seiner and _in_die_zwischenablage_retten(seiner):
                return False, ('Ich finde das Eingabefeld nicht. Dein getippter Text '
                               'liegt in der Zwischenablage, und was du gesagt hast '
                               'steht in der Rettungsdatei.')
            if _in_die_zwischenablage_retten(auftrag):
                return False, ('Ich finde das Eingabefeld nicht. Dein Satz liegt '
                               'in der Zwischenablage.')
            return False, 'Ich finde das Eingabefeld nicht. Da schreibe ich nichts blind hinein.'

        # An dieser Stelle ist das Feld IMMER leer: die Fokusprüfung schneidet
        # aus, was drinstand, und gibt es als `seiner` zurück. Stand dort etwas,
        # muss es hinterher wieder hinein.
        if not leer:
            # Sein Text ist ausgeschnitten und hängt nur noch an dieser
            # Variablen. Auf die Platte damit, bevor die Zwischenablage für den
            # Auftrag gebraucht wird.
            _rettung_schreiben(auftrag, seiner)

        if not _zwischenablage_schreiben(VORSATZ + auftrag):
            _zurueckschreiben(tastatur, leer, seiner)
            _merker_weg()
            return False, 'Ich komme an die Zwischenablage nicht heran.'

        _einfuegen_und_senden(tastatur)
        # Und jetzt sein eigener Text zurück ins leere Feld -- ohne Enter.
        #
        # Ramzis Wunsch, wortgetreu: er will nicht, dass sein gesprochener
        # Auftrag liegen bleibt, nur weil er nebenher tippt. Für ihn soll es
        # aussehen, als wäre nichts passiert -- sein Text steht unverändert im
        # Feld, und der gesprochene Auftrag ist zusätzlich abgeschickt.
        #
        # Er hat es als "zweites Element der Zwischenablage" beschrieben. Das
        # braucht es nicht: sein Text liegt schon als gewöhnliche Variable
        # `seiner` vor, also genügt es, zweimal hintereinander das Richtige in
        # die Zwischenablage zu legen. Kein Windows-Zwischenablage-Verlauf
        # (Win+V) nötig, der oft gar nicht eingeschaltet ist.
        _zurueckschreiben(tastatur, leer, seiner)
    finally:
        # Zwischenablage zurückgeben -- Ramzi hat da oft etwas drin, das er
        # gleich braucht. Kurz warten, sonst überhole ich das eigene Einfügen.
        # Liegt dort aber eine Rettung, bleibt sie liegen: einen verlorenen Satz
        # wiederherzustellen ist wichtiger, als eine Zwischenablage aufzuräumen.
        time.sleep(0.4)
        if vorher is not None and not behalten:
            _zwischenablage_schreiben(vorher)

        # Und den Fokus zurück, wo er war -- damit Ramzis Vollbildspiel nicht
        # zubleibt. Ganz am Ende und im `finally`: auch ein Fehlschlag soll ihn
        # nicht aus dem Spiel werfen.
        #
        # Nur wenn das Fenster noch existiert. Zwischen dem Merken und hier
        # liegen ein paar Sekunden, in denen es geschlossen worden sein kann --
        # ein Griff auf ein totes Fenster würde den Fokus auf den Desktop
        # legen, und das ist schlechter als ihn zu lassen, wo er ist.
        if vorheriges_fenster and _user32.IsWindow(ctypes.c_void_p(vorheriges_fenster)):
            try:
                # Als c_void_p und nicht als int: ctypes macht aus einem nackten
                # int ein 32-Bit-Argument, und ein Fenstergriff oberhalb von
                # 0x7FFFFFFF wuerde dabei abgeschnitten. Genau dafuer gibt es
                # oben `_gleich`.
                hole_nach_vorn(ctypes.c_void_p(vorheriges_fenster))
            except Exception:
                pass

    return True, None


# --------------------------------------------------------------------------
if __name__ == '__main__':
    text = ' '.join(sys.argv[1:]) or 'Sag kurz Hallo.'
    ok, meldung = sende(text)
    print('abgeschickt' if ok else f'nicht abgeschickt: {meldung}')
