"""Der Dienst, der zuhört und antwortet — Ohr und Mund zusammengesteckt.

    Weckwort erkannt  ->  Reflex (lokal, sofort)  oder  an Noor weiterreichen

Die Aufteilung ist Absicht und der Kern des ganzen Entwurfs: "wie spät ist es"
darf keine Netzwerkrunde kosten, und "erklär mir das" darf ruhig eine dauern.
Alles hier drin ist lokal und kostet nichts.

Start:
    python -m src.assistant
    python -m src.assistant --stimme de_DE-thorsten-medium
"""
import datetime
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from voice_output import Sprecher          # noqa: E402
from wake_word import Weckwort, WECKWORT   # noqa: E402

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sag_uhrzeit():
    jetzt = datetime.datetime.now()
    return f'Es ist {jetzt.hour} Uhr {jetzt.minute:02d}.'


def _sag_datum():
    tage = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
    h = datetime.date.today()
    return f'Heute ist {tage[h.weekday()]}, der {h.day}. {h.month}.'


def _untertitel(text, wer):
    """Auf den Untertitel-Streifen schreiben, falls es ihn gibt.

    Weich verdrahtet: läuft kein Streifen, passiert nichts. Der Assistent darf
    an einer Anzeige nicht scheitern."""
    try:
        from untertitel import zeige
        zeige(text, wer)
    except Exception:
        pass


def normalisiere(text):
    """Satz auf eine vergleichbare Form bringen.

    Umlaute auflösen, klein schreiben, Satzzeichen und Mehrfach-Leerzeichen
    weg. Damit ist es egal, ob Whisper "spät", "spaet" oder "Spät!" schreibt --
    und die Reflexe unten brauchen jede Schreibweise nur einmal."""
    t = (text or '').lower()
    for a, b in (('ä', 'a'), ('ö', 'o'), ('ü', 'u'), ('ß', 'ss'),
                 ('ae', 'a'), ('oe', 'o'), ('ue', 'u')):
        t = t.replace(a, b)
    t = re.sub(r'[^\wäöüß ]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _medien(taste):
    """Wiedergabe steuern -- geht ueber die Multimedia-Tasten, also mit jedem
    Player, nicht nur mit Spotify."""
    try:
        from media_controller import MediaController
        MediaController().send_media_key(taste)
        return True
    except Exception:
        # Notfallweg ueber die Windows-Tastensimulation
        try:
            import ctypes
            codes = {'play_pause': 0xB3, 'next': 0xB0, 'previous': 0xB1}
            vk = codes.get(taste)
            if vk:
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
                return True
        except Exception:
            pass
    return False


class Assistent:
    """Hört auf den Namen, antwortet mit Stimme."""

    # Reflexe: Muster -> was zurückgesagt wird. Bewusst klein gehalten --
    # alles, was hier nicht steht, geht an Noor.
    def __init__(self, stimme=None, modell='base'):
        self.sprecher = Sprecher(stimme) if stimme else Sprecher()
        self.ohr = Weckwort(self._geweckt, modell=modell)
        self._laeuft = threading.Event()

        # Reflexe als BRUCHSTÜCKE statt als ganze Sätze.
        #
        # Vorher stand hier ein Muster wie "wie spät|uhrzeit|wie viel uhr" --
        # das zwingt Ramzi, eine von drei Formulierungen zu treffen. Er will
        # reden, nicht ein Kommando aufsagen. Also wird der Satz erst
        # normalisiert (klein, Umlaute aufgelöst, Satzzeichen weg) und dann auf
        # kurze Bruchstücke geprüft: ein Treffer genügt.
        #
        # Das ist immer noch Mustererkennung, keine Bedeutung. Echtes Verstehen
        # ("mach mal das Ding aus") kann erst das kleine lokale Modell aus
        # Stufe 2 -- solange das nicht läuft, ist eine großzügige Liste die
        # ehrlichere Lösung als ein enges Muster, das oft danebengreift.
        self.reflexe = [
            (['wie spat', 'wie spaet', 'uhrzeit', 'viel uhr', 'uhr haben', 'uhr ist',
              'zeit haben', 'welche zeit', 'spat ist', 'sag mir die zeit', 'zeit sag'],
             lambda: _sag_uhrzeit()),

            (['welcher tag', 'welchen tag', 'welches datum', 'wievielte', 'datum',
              'fur ein tag', 'wochentag', 'heute fur ein'],
             lambda: _sag_datum()),

            (['schlaf', 'sei still', 'sei ruhig', 'halt die klappe', 'lass mich in ruhe',
              'pause machen', 'mach pause'],
             lambda: self._schlafen()),

            (['wach auf', 'aufwachen', 'wach mal auf', 'bist du da', 'bist du wach'],
             lambda: self._aufwachen()),

            (['hor auf', 'sei mal still', 'ruhe', 'stopp reden', 'nicht weiter reden'],
             lambda: self._still()),

            (['musik an', 'musik aus', 'musik weiter', 'musik pause', 'musik stopp',
              'spotify an', 'spotify aus', 'spotify pause', 'mach musik', 'mach mal musik',
              'lied an', 'lied aus', 'song an', 'song aus', 'playlist an', 'pausier die musik',
              'stell die musik', 'mach die musik'],
             lambda: 'Okay.' if _medien('play_pause') else 'Das hat nicht geklappt.'),

            (['nachstes lied', 'nachster song', 'nachste lied', 'nachsten song', 'nachstes stuck',
              'skip', 'uberspring', 'weiter im lied', 'nachster titel', 'nachstes titel'],
             lambda: 'Okay.' if _medien('next') else 'Das hat nicht geklappt.'),

            (['vorheriges lied', 'vorheriger song', 'ein lied zuruck', 'nochmal von vorne',
              'letztes lied'],
             lambda: 'Okay.' if _medien('previous') else 'Das hat nicht geklappt.'),
        ]

    # ------------------------------------------------------------------
    def _schlafen(self):
        self.ohr.schlaeft = True
        return 'Okay, ich bin still. Sag "Noor, wach auf", wenn du mich brauchst.'

    def _aufwachen(self):
        self.ohr.schlaeft = False
        return 'Ich bin da.'

    def _still(self):
        self.sprecher.stoppe()
        return None      # nichts sagen -- er will ja gerade Ruhe

    # ------------------------------------------------------------------
    # Was mich aus dem Schlaf holt. Absichtlich grosszuegig und ohne Weckwort
    # gedacht -- wer mich zehnmal ruft, soll nicht an einer Formulierung
    # scheitern.
    AUFWECKER = re.compile(r'\b(wach auf|aufwachen|wach mal auf|bist du (da|wach)|hallo)\b', re.I)

    def _sag(self, text, wer='noor'):
        """Sprechen UND untertiteln.

        Immer beides zusammen, nie nur eins -- sonst driftet das, was zu hören
        ist, von dem ab, was zu lesen ist."""
        if text:
            _untertitel(text, wer)
            self.sprecher.sprich_im_hintergrund(text)

    def _geweckt(self, text):
        """Wird gerufen, sobald der Name gefallen ist. `text` ist der ganze Satz."""
        print(f'[Noor] gehört: {text!r}')
        # Zuerst zeigen, was angekommen ist -- noch vor jeder Reaktion. Geht ein
        # Befehl daneben, ist damit sofort sichtbar, ob es am Sagen oder am
        # Hören lag.
        _untertitel(text, 'ramzi')

        # Im Schlaf höre ich weiter, reagiere aber nur aufs Aufwachen.
        if self.ohr.schlaeft:
            if self.AUFWECKER.search(text):
                self.ohr.schlaeft = False
                self._sag('Ich bin wieder da.')
            return

        # Wenn ich gerade rede und angesprochen werde: erst mal Klappe halten.
        # Das ist die einfache Form vom Unterbrochenwerden -- noch nicht
        # mitten im Wort, aber schon "du hast Vorrang".
        if self.sprecher.spricht_gerade():
            self.sprecher.stoppe()

        # Weckwort aus dem Satz nehmen, damit der Rest der reine Auftrag ist.
        auftrag = WECKWORT.sub('', text).strip(' ,.!?')
        geglaettet = normalisiere(auftrag)

        for bruchstuecke, handler in self.reflexe:
            if any(b in geglaettet for b in bruchstuecke):
                antwort = handler()
                if antwort:
                    self._sag(antwort)
                return

        if not auftrag:
            self._sag('Ja?')
            return

        # Alles andere gehört Noor: über die Brücke in die laufende Sitzung.
        self._an_noor(auftrag)

    # ------------------------------------------------------------------
    def _an_noor(self, auftrag):
        """Auftrag über die Brücke weiterreichen.

        In einem eigenen Thread, weil das Fenster-nach-vorn-Holen und das
        Einfügen zusammen fast eine Sekunde dauern -- so lange darf das Ohr
        nicht taub sein, sonst geht das nächste "Noor, stopp" verloren."""
        def _lauf():
            try:
                from bruecke import sende
            except Exception as e:
                self._sag('Die Brücke lässt sich nicht laden.')
                print(f'[Noor] Brücke nicht ladbar: {e}')
                return
            ok, meldung = sende(auftrag)
            if not ok:
                self._sag(meldung or 'Das hat nicht geklappt.')
            # Bei Erfolg sage ich hier NICHTS. Die eigentliche Antwort spricht
            # der Stop-Hook, sobald sie steht -- eine Zwischenansage wuerde sich
            # bei kurzen Antworten mit ihr ins Wort fallen.

        threading.Thread(target=_lauf, daemon=True).start()

    # ------------------------------------------------------------------
    def starte(self):
        print('[Noor] Lade Modelle …')
        _ = self.sprecher.stimme
        self.ohr.starte()
        self._laeuft.set()
        print('[Noor] Ich höre zu. Sag meinen Namen.')
        self.sprecher.sprich('Ich höre zu.')

    def stoppe(self):
        self._laeuft.clear()
        self.ohr.stoppe()
        self.sprecher.stoppe()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description='Noor hört zu (lokal)')
    p.add_argument('--stimme', default=None)
    p.add_argument('--modell', default='base', help='Whisper-Größe fürs Weckwort')
    args = p.parse_args(argv)

    a = Assistent(stimme=args.stimme, modell=args.modell)
    a.starte()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('\n[Noor] Bis später.')
        a.stoppe()
    return 0


if __name__ == '__main__':
    sys.exit(main())
