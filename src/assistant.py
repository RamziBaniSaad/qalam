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

import einstellungen                        # noqa: E402
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


TOENE = os.path.join(PROJEKT, 'assets')


def ton(name):
    """Ein Tonzeichen abspielen, ohne auf das Ende zu warten.

    Ramzis wichtigster Wunsch dabei: er will HÖREN, dass ich zuhöre, bevor er
    weiterredet -- sonst redet er ins Ungewisse. Deshalb asynchron und ganz
    vorn, vor jeder Verarbeitung.

    winsound und keine Bibliothek: es ist in Windows eingebaut, braucht kein
    Audiogerät zu öffnen und ist damit auch dann noch da, wenn Piper gerade
    spricht."""
    if not einstellungen.hole('toene'):
        return
    try:
        import winsound
        pfad = os.path.join(TOENE, name)
        if os.path.exists(pfad):
            winsound.PlaySound(pfad, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


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
    # "small" statt "base": base hat Ramzis "wie spät ist es" am 31.07.2026 als
    # "wie ich pittest es" verstanden -- der Reflex konnte gar nicht greifen,
    # und der Satz ging unnötig über die Brücke an mich. Nicht die Musterliste
    # war zu eng, das Hören war zu schlecht.
    #
    # small kostet etwa dreimal so viel Rechenzeit wie base, aber weiterhin
    # KEIN VRAM (int8 auf der CPU, ~250 MB RAM). Bei Sätzen von zwei, drei
    # Sekunden fällt der Unterschied nicht auf -- ein verhörter Befehl schon.
    def __init__(self, stimme=None, modell='small'):
        self.sprecher = Sprecher(stimme) if stimme else Sprecher()
        # Zwischenstücke einer laufenden, langen Äußerung sammeln sich hier,
        # bis die echte Stille kommt -- siehe _geweckt().
        self._sammelsatz = ''
        # beim_mitschreiben bewusst NICHT verdrahtet -- siehe _erkannt() fuer
        # die Begruendung, warum die rohe Vorschau nicht mehr angezeigt wird.
        self.ohr = Weckwort(self._geweckt, modell=modell,
                            beim_erkennen=self._erkannt,
                            spricht_gerade=lambda: self.sprecher.spricht_gerade())
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

    def _erkannt(self):
        """Der Name ist gefallen -- mitten im Satz, nicht erst danach.

        Ton, und ein ruhiger Platzhalter im Untertitel. Ramzi hat am
        31.07.2026 ein Video geschickt: der Untertitel zeigte waehrend des
        Redens wild wechselnden Unsinn. Ursache war NICHT das Ohr -- die
        richtigen Saetze im Log waren gut lesbar -- sondern die Anzeige: sie
        zeigte den rohen Text aus _mitschreiben(), und der kommt aus
        unabhaengigen 3-Sekunden-Fenstern, die irgendwo mitten im Wort
        anfangen und aufhoeren. Selbst ein perfektes Modell wuerde daraus
        etwas Zusammenhangloses machen. Deshalb zeigt der Untertitel waehrend
        des Sprechens jetzt nur noch diesen Platzhalter; den echten,
        zuverlaessigen Text liefert _geweckt() aus dem genauen Modell."""
        ton('noor_wach.wav')
        # KEIN Platzhalter im Untertitel mehr. Er wuerde den letzten echten Satz
        # ueberschreiben und genau das Flackern erzeugen, das Ramzi stoert --
        # der Streifen soll stehen bleiben, bis echter Text ihn ersetzt. Dass
        # ich zuhoere, sagt der Ton, und der ist schneller als jede Anzeige.
        # Ab jetzt zählt auch der nächste Satz ohne Namen als Auftrag: er sagt
        # den Namen, wartet auf dieses Zeichen und redet dann erst los.
        self.ohr.folge_bis = time.time() + einstellungen.hole('folge_sekunden')

    def _geweckt(self, text, endgueltig=True):
        """Wird gerufen, wenn ein Segment erkannt wurde.

        `endgueltig=False`: nur ein Zwischenstück einer noch laufenden, langen
        Äußerung (die Längenbegrenzung hat mitten im Reden geschnitten, nicht
        eine echte Pause). Das darf NICHT als fertiger Befehl behandelt werden
        -- genau das ist Ramzi am 31.07.2026 passiert: sein erster Satzteil
        enthielt "Noor" und wurde sofort unvollständig verschickt, danach ging
        das Fenster zu und der Rest seiner ein bis zwei Minuten war verloren.
        """
        # Zeitstempel im Log -- ohne den war nicht messbar, wie lange zwischen
        # Sprechen und Reaktion wirklich vergeht. Siehe
        # Privat/Ideen/Noor-Ueberall/STAND-Sprachschicht.md, Abschnitt 1.
        print(f'[{time.strftime("%H:%M:%S")}] [Noor] gehört ({"fertig" if endgueltig else "Zwischenstück"}): {text!r}')

        # Weckwort aus dem Satz nehmen und an das bisher Gesammelte anhängen --
        # VOR dem Untertitel, nicht danach. Ramzi hat einen echten Fehler
        # gefunden: der Untertitel zeigte bisher nur das JEWEILS NEUE
        # Zwischenstück, nicht den gewachsenen Satz. Bei einem Stück mit
        # mehreren Sätzen, von denen der Streifen nur die letzten zwei zeigt,
        # verschwand der erste Satz dieses Stücks lautlos -- er wurde nie
        # angezeigt, nicht mal kurz. Jetzt bekommt der Streifen immer den
        # ganzen bisher gesammelten Text; das Abschneiden auf die letzten
        # Sätze passiert dort auf der vollständigen, wachsenden Fassung.
        stueck = WECKWORT.sub('', text).strip(' ,.!?')
        self._sammelsatz = f'{self._sammelsatz} {stueck}'.strip() if self._sammelsatz else stueck
        _untertitel(self._sammelsatz, 'ramzi')

        # Im Schlaf höre ich weiter, reagiere aber nur aufs Aufwachen.
        if self.ohr.schlaeft:
            if endgueltig and self.AUFWECKER.search(text):
                self.ohr.schlaeft = False
                self._sag('Ich bin wieder da.')
            self._sammelsatz = ''
            return

        # Wenn ich gerade rede und angesprochen werde: erst mal Klappe halten.
        # Das ist die einfache Form vom Unterbrochenwerden -- noch nicht
        # mitten im Wort, aber schon "du hast Vorrang".
        if self.sprecher.spricht_gerade():
            self.sprecher.stoppe()

        if not endgueltig:
            # Er redet weiter -- Fenster offenhalten, noch nichts ausführen.
            self.ohr.folge_bis = time.time() + 20.0
            return

        auftrag = self._sammelsatz
        self._sammelsatz = ''
        geglaettet = normalisiere(auftrag)

        for bruchstuecke, handler in self.reflexe:
            if any(b in geglaettet for b in bruchstuecke):
                # Ein Auftrag ist erledigt -- ab jetzt wieder zu, bis der Name
                # erneut fällt. Ohne das bliebe das Ohr nach jedem "Noor, wie
                # spät ist es" noch 15 Sekunden offen und würde die nächste
                # Bemerkung im Raum als weiteren Befehl nehmen.
                self.ohr.folge_bis = 0.0
                # Musik bekommt ihr eigenes Zeichen -- Ramzi hört dann schon am
                # Ton, dass es kein Missverständnis war, bevor Spotify reagiert.
                ton('noor_musik.wav' if 'musik' in bruchstuecke[0] or 'lied' in bruchstuecke[0]
                    else 'noor_reflex.wav')
                antwort = handler()
                if antwort:
                    self._sag(antwort)
                return

        # Nur der Name, kein Auftrag: das Ohr bleibt bewusst offen (folge_bis
        # läuft weiter) -- genau das ist Ramzis "ich sage den Namen, warte auf
        # den Ton, rede dann erst los". Der Wach-Ton oben hat schon alles
        # gesagt, ein gesprochenes "Ja?" würde ihm nur ins Wort fallen.
        if not auftrag:
            return

        # Alles andere gehört Noor: über die Brücke in die laufende Sitzung.
        # Auch das ist ein erledigter Auftrag -- wieder zu.
        self.ohr.folge_bis = 0.0
        ton('noor_bruecke.wav')
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
    p.add_argument('--modell', default='small', help='Whisper-Größe fürs Weckwort')
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
