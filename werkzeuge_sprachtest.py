"""Der Sprachweg ohne Mikrofon und ohne Lautsprecher durchspielen.

WOZU: die sieben Fehler, die am 07.08.2026 behoben wurden, sind alle vom Typ
"passiert nur, wenn zwei Dinge gleichzeitig laufen" -- ein Stopp mitten im
Satz, ein Auftrag, der waehrend Ramzis Rede eingeht, ein Video, das zwischen
zwei Saetzen anspringt. So etwas von Hand nachzustellen dauert Minuten und
gelingt nicht zuverlaessig. Hier dauert es Sekunden und gelingt immer.

Was hier NICHT geprueft wird, weil es ohne echtes Mikrofon nicht geht: ob das
Ohr einen Zuruf versteht, ob die Stimme gut klingt, ob die Bruecke das
Claude-Fenster findet. Das steht in Ramzis kurzer Testliste in KONTEXT.md.

    python werkzeuge_sprachtest.py
"""
import os
import shutil
import sys
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, 'src'))

import sprechzentrale as z          # noqa: E402
import warteschlange as w           # noqa: E402

_fehler = []

# EIGENE MERKDATEIEN FUER DEN TEST -- und das ist keine Bequemlichkeit.
#
# Das Ohr laeuft normalerweise, waehrend dieser Test laeuft, und schreibt alle
# 0,3 s in genau dieselben Dateien: `.ramzi-redet.lock`, `noor-gesagt.log`.
# Ohne eigene Ablage prueft der Test also nicht seinen eigenen Aufbau, sondern
# was gerade nebenan im Zimmer passiert -- er schlaegt zufaellig fehl und wird
# damit wertlos. Umgekehrt landen sonst Probesaetze im echten Sprachprotokoll,
# und "nochmal" spielt sie Ramzi vor.
_ABLAGE = tempfile.mkdtemp(prefix='noor-sprachtest-')
w.REDET_SPERRE = os.path.join(_ABLAGE, 'ramzi-redet.lock')
w.AUFNAHME_SPERRE = os.path.join(_ABLAGE, 'aufnahme.lock')
w.SCHLAF_MERKER = os.path.join(_ABLAGE, 'schlaeft.lock')
w.GESAGT = os.path.join(_ABLAGE, 'noor-gesagt.json')
w.GESAGT_LOG = os.path.join(_ABLAGE, 'noor-gesagt.log')


def pruefe(bedingung, was):
    print(('  OK   ' if bedingung else '  FEHLT') + '  ' + was)
    if not bedingung:
        _fehler.append(was)


class Lautsprecher:
    """Ein Sprecher, der nicht spricht, sondern mitschreibt.

    `dauer` macht ihn zu etwas, das man unterbrechen KANN -- ein Sprecher, der
    sofort fertig ist, laesst sich nicht mitten im Satz stoppen, und genau das
    ist der Fall, um den es geht.
    """

    def __init__(self, dauer=0.3):
        self.gesagt = []
        self.dauer = dauer
        self._stand = 0
        self._reden = 0

    def sprich(self, text):
        self.gesagt.append(text)
        meiner = self._stand
        self._reden += 1
        try:
            ende = time.time() + self.dauer
            while time.time() < ende:
                if self._stand != meiner:
                    return 'abgebrochen'
                time.sleep(0.02)
            return 'fertig'
        finally:
            self._reden -= 1

    def stoppe(self):
        self._stand += 1

    def spricht_gerade(self):
        return self._reden > 0


class Buehne:
    """Statt Video und Lautstaerke nur mitzaehlen, wer sie haelt."""

    def __init__(self):
        self.ereignisse = []

    def buehne_an(self):
        self.ereignisse.append('an')

    def buehne_aus(self):
        self.ereignisse.append('aus')


def _neu(dauer=0.3):
    """Zentrale frisch aufsetzen -- jeder Fall faengt bei null an."""
    z.beenden()
    # Wirklich warten, bis der alte Faden aus seinem Satz heraus ist -- sonst
    # prueft der naechste Fall gegen einen Sprecher, der noch dem vorigen
    # gehoert.
    if z._faden is not None:
        z._faden.join(timeout=3.0)
    with z._sperre:
        z._auftraege.clear()
    z._buehne_gehalten = False
    buehne = Buehne()
    sys.modules['voice_output'] = buehne
    sprecher = Lautsprecher(dauer)
    z.starte(sprecher)
    return sprecher, buehne


def _warte_bis(pruefung, hoechstens=4.0):
    ende = time.time() + hoechstens
    while time.time() < ende:
        if pruefung():
            return True
        time.sleep(0.03)
    return False


# --- 1. Reihenfolge nach Rang ---------------------------------------------
def fall_reihenfolge():
    print('\n1. Der wichtigste Auftrag kommt zuerst dran')
    s, _ = _neu(dauer=0.05)
    z.einwerfen('vorlesen', z.RANG_VORLESEN, 'test')
    z.einwerfen('zwischen', z.RANG_ZWISCHEN, 'test')
    z.einwerfen('sofort', z.RANG_SOFORT, 'test')
    z.einwerfen('antwort', z.RANG_ANTWORT, 'test')
    _warte_bis(lambda: len(s.gesagt) == 4)
    pruefe(s.gesagt == ['sofort', 'antwort', 'zwischen', 'vorlesen'],
           'Rang schlaegt Reihenfolge des Einwerfens: %r' % (s.gesagt,))


# --- 2. Verfallen ----------------------------------------------------------
def fall_verfallen():
    print('\n2. Ein ueberholter Satz wird nicht mehr gesagt')
    s, _ = _neu(dauer=0.05)
    z.einwerfen('laengst ueberholt', z.RANG_VORLESEN, 'test')
    with z._sperre:
        z._auftraege[-1]['verfaellt'] = time.time() - 1
    time.sleep(0.5)
    pruefe('laengst ueberholt' not in s.gesagt,
           'Verfallenes wird nicht gesprochen')
    pruefe(_im_protokoll('UNGESAGT', 'laengst ueberholt'),
           'Verfallenes steht als UNGESAGT im Protokoll')


# --- 3. Stopp ----------------------------------------------------------
def fall_stopp():
    print('\n3. Stopp bricht ab UND raeumt die Warteschlange leer')
    s, _ = _neu(dauer=2.0)
    z.einwerfen('der laufende Satz', z.RANG_ZWISCHEN, 'test')
    for i in range(3):
        z.einwerfen('wartender Satz %d' % i, z.RANG_ZWISCHEN, 'test')
    pruefe(_warte_bis(lambda: s.spricht_gerade()), 'es wird wirklich gesprochen')
    weg = z.stoppe_alles('Test')
    pruefe(weg == 3, 'die drei wartenden Saetze sind weggeraeumt (%d)' % weg)
    pruefe(_warte_bis(lambda: not s.spricht_gerade(), 1.0),
           'der laufende Satz ist innerhalb einer Sekunde still')
    pruefe(_warte_bis(lambda: _im_protokoll('ABGEBROCHEN', 'der laufende Satz')),
           'der abgebrochene Satz steht als ABGEBROCHEN im Protokoll')
    pruefe(_im_protokoll('UNGESAGT', 'wartender Satz 0'),
           'die weggeraeumten stehen als UNGESAGT im Protokoll')


# --- 4. Unterbrechen laesst das Wartende stehen ---------------------------
def fall_unterbrechen():
    print('\n4. Faengt er nur an zu reden, stirbt nur der laufende Satz')
    s, _ = _neu(dauer=2.0)
    z.einwerfen('laeuft gerade', z.RANG_ZWISCHEN, 'test')
    z.einwerfen('soll noch kommen', z.RANG_ZWISCHEN, 'test')
    _warte_bis(lambda: s.spricht_gerade())
    z.unterbrich('Test')
    pruefe(_warte_bis(lambda: 'soll noch kommen' in s.gesagt),
           'der wartende Satz kommt danach trotzdem noch')


# --- 5. Waehrend er redet, sage ich nichts ---------------------------------
def fall_nicht_reinreden():
    print('\n5. Solange er redet, bleibe ich still')
    s, _ = _neu(dauer=0.05)
    w.redet_merken(True)
    try:
        z.einwerfen('das darf jetzt nicht kommen', z.RANG_ZWISCHEN, 'test')
        time.sleep(0.8)
        pruefe(not s.gesagt, 'nichts gesprochen, solange sein Merker steht')
    finally:
        w.redet_merken(False)
    pruefe(_warte_bis(lambda: 'das darf jetzt nicht kommen' in s.gesagt, 3.0),
           'danach wird es nachgeholt')


# --- 6. Nachlauf -----------------------------------------------------------
def fall_nachlauf():
    print('\n6. Nach seinem letzten Wort bleibt es kurz still')
    # Der Merker wird NICHT geloescht, sondern laeuft von selbst ab -- genau
    # das ist die Denkpause mitten im Satz. (Loescht das Ohr ihn ausdruecklich,
    # hat es echte Stille gemessen und der Nachlauf entfaellt mit Absicht.)
    w.redet_merken(True)
    begonnen = time.time()
    w.warte_bis_er_fertig_ist()
    gewartet = time.time() - begonnen
    noetig = w.REDET_ALTER + w.NACHLAUF
    pruefe(gewartet >= noetig,
           'Ablauf (%.1f s) plus Nachlauf (%.1f s) abgewartet: %.2f s'
           % (w.REDET_ALTER, w.NACHLAUF, gewartet))
    w.redet_merken(False)


# --- 7. Die Buehne haelt ueber den ganzen Zug ------------------------------
def fall_buehne():
    print('\n7. Das Video haelt einmal an und laeuft einmal weiter')
    s, b = _neu(dauer=0.15)
    for i in range(4):
        z.einwerfen('satz %d' % i, z.RANG_ZWISCHEN, 'test')
    _warte_bis(lambda: len(s.gesagt) == 4)
    time.sleep(0.4)
    pruefe(b.ereignisse == ['an', 'aus'],
           'genau einmal an und einmal aus, nicht je Satz: %r' % (b.ereignisse,))


# --- 8. Stoppwoerter kommen durch den Echo-Schutz --------------------------
def fall_stoppwoerter():
    print('\n8. Ein Stoppwort gilt nie als mein Echo')
    for satz in ('stopp', 'Noor stopp', 'halt mal', 'sei still', 'hoer auf',
                 'hör auf', 'Ruhe', 'warte'):
        pruefe(w.ist_stoppwort(satz), 'erkannt: %r' % satz)
    for satz in ('Das ist der Inhalt der Datei', 'ich erhalte das so',
                 'die Stoppuhr laeuft', 'das war ein Halteverbot'):
        pruefe(not w.ist_stoppwort(satz), 'kein Fehlalarm: %r' % satz)


# --- 9. Echo kommt nicht ueber die Bruecke ---------------------------------
def fall_echo_bruecke():
    print('\n9. Mein eigenes Echo geht nicht als seine Nachricht durch')
    import bruecke
    meiner = ('Die Sprech-Zentrale entscheidet ab jetzt allein, welcher Satz '
              'wann gesprochen wird.')
    w.merke(meiner, 'GESAGT')
    pruefe(bruecke._woertlich_von_mir(meiner),
           'mein eigener Satz wird erkannt')
    pruefe(bruecke._woertlich_von_mir('Ja genau, ' + meiner + ' Alles klar.'),
           'auch mit seinem Text drumherum')
    pruefe(not bruecke._woertlich_von_mir(
        'Kannst du mal nachsehen, warum der Login bei flexpass haengt?'),
        'sein echter Auftrag geht durch')


# --- Protokoll-Hilfe -------------------------------------------------------
def _im_protokoll(art, stueck):
    try:
        with open(w.GESAGT_LOG, encoding='utf-8') as f:
            zeilen = f.readlines()[-60:]
    except OSError:
        return False
    return any(art in zeile and stueck in zeile for zeile in zeilen)


def main():
    print('Sprachweg-Selbsttest -- ohne Mikrofon, ohne Lautsprecher')
    for fall in (fall_reihenfolge, fall_verfallen, fall_stopp,
                 fall_unterbrechen, fall_nicht_reinreden, fall_nachlauf,
                 fall_buehne, fall_stoppwoerter, fall_echo_bruecke):
        fall()
    z.beenden()
    shutil.rmtree(_ABLAGE, ignore_errors=True)
    print('\n' + '-' * 60)
    if _fehler:
        print('%d Punkte stimmen nicht:' % len(_fehler))
        for f in _fehler:
            print('  - ' + f)
        return 1
    print('Alles gruen.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
