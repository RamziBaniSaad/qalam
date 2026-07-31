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

        self.reflexe = [
            # "ae/oe/ue" mit erlauben: Whisper schreibt zwar Umlaute, aber
            # Tastatur-Eingaben und Tests tun das nicht immer.
            (re.compile(r'\b(wie sp(ä|ae|a)t|uhrzeit|wie viel uhr)\b', re.I), lambda m: _sag_uhrzeit()),
            (re.compile(r'\b(welcher tag|welches datum|der wievielte)\b', re.I), lambda m: _sag_datum()),
            (re.compile(r'\b(schlaf|sei (still|ruhig)|halt die klappe)\b', re.I), self._schlafen),
            (re.compile(r'\b(wach auf|aufwachen|bist du da)\b', re.I), self._aufwachen),
            (re.compile(r'\b(stopp?|h[öo]r auf|ruhe)\b', re.I), self._still),
            (re.compile(r'\b(musik|spotify|lied|song).*(an|aus|weiter|pause|stopp?)', re.I),
             lambda m: 'Okay.' if _medien('play_pause') else 'Das hat nicht geklappt.'),
            (re.compile(r'\b(n[äa]chste[rs]?|weiter)\s+(lied|song|titel)', re.I),
             lambda m: 'Okay.' if _medien('next') else 'Das hat nicht geklappt.'),
        ]

    # ------------------------------------------------------------------
    def _schlafen(self, _m):
        self.ohr.schlaeft = True
        return 'Okay, ich bin still. Sag "Noor, wach auf", wenn du mich brauchst.'

    def _aufwachen(self, _m):
        self.ohr.schlaeft = False
        return 'Ich bin da.'

    def _still(self, _m):
        self.sprecher.stoppe()
        return None      # nichts sagen -- er will ja gerade Ruhe

    # ------------------------------------------------------------------
    # Was mich aus dem Schlaf holt. Absichtlich grosszuegig und ohne Weckwort
    # gedacht -- wer mich zehnmal ruft, soll nicht an einer Formulierung
    # scheitern.
    AUFWECKER = re.compile(r'\b(wach auf|aufwachen|wach mal auf|bist du (da|wach)|hallo)\b', re.I)

    def _geweckt(self, text):
        """Wird gerufen, sobald der Name gefallen ist. `text` ist der ganze Satz."""
        print(f'[Noor] gehört: {text!r}')

        # Im Schlaf höre ich weiter, reagiere aber nur aufs Aufwachen.
        if self.ohr.schlaeft:
            if self.AUFWECKER.search(text):
                self.ohr.schlaeft = False
                self.sprecher.sprich_im_hintergrund('Ich bin wieder da.')
            return

        # Wenn ich gerade rede und angesprochen werde: erst mal Klappe halten.
        # Das ist die einfache Form vom Unterbrochenwerden -- noch nicht
        # mitten im Wort, aber schon "du hast Vorrang".
        if self.sprecher.spricht_gerade():
            self.sprecher.stoppe()

        # Weckwort aus dem Satz nehmen, damit der Rest der reine Auftrag ist.
        auftrag = WECKWORT.sub('', text).strip(' ,.!?')

        for muster, handler in self.reflexe:
            treffer = muster.search(auftrag)
            if treffer:
                antwort = handler(treffer)
                if antwort:
                    self.sprecher.sprich_im_hintergrund(antwort)
                return

        if not auftrag:
            self.sprecher.sprich_im_hintergrund('Ja?')
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
                self.sprecher.sprich_im_hintergrund('Die Brücke lässt sich nicht laden.')
                print(f'[Noor] Brücke nicht ladbar: {e}')
                return
            ok, meldung = sende(auftrag)
            if not ok:
                self.sprecher.sprich_im_hintergrund(meldung or 'Das hat nicht geklappt.')
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
