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
import oeffnen                              # noqa: E402
import schliessen                           # noqa: E402
import stellschrauben                       # noqa: E402
import verhoerer                            # noqa: E402
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

# Bis zu wie vielen Woertern eine Aeusserung als Reflex gelten darf.
# Begruendung steht in _geweckt(), an der Stelle, wo es geprueft wird.
REFLEX_MAX_WOERTER = 10


def ton(name):
    """Ein Tonzeichen abspielen, ohne auf das Ende zu warten.

    Ramzis wichtigster Wunsch dabei: er will HÖREN, dass ich zuhöre, bevor er
    weiterredet -- sonst redet er ins Ungewisse. Deshalb asynchron und ganz
    vorn, vor jeder Verarbeitung.

    winsound und keine Bibliothek: es ist in Windows eingebaut, braucht kein
    Audiogerät zu öffnen und ist damit auch dann noch da, wenn Piper gerade
    spricht.

    Wie LAUT, entscheidet ton.py -- dort hängt jeder Klang an seinem eigenen
    Regler, und dort steht auch, warum die Lautstärke in die Datei gerechnet
    werden muss (winsound kennt keine). Hier bleibt nur der Aufruf: es soll
    genau eine Stelle geben, die weiß, wie ein Ton laut wird.

    `import` erst hier und nicht oben: das Modul heißt `ton` wie diese
    Funktion, und ein Modulname auf Dateiebene würde von ihr verdeckt."""
    try:
        import ton as tonmodul
        tonmodul.spiele(name)
    except Exception:
        pass


# Was zuletzt auf RAMZIS Streifen stand -- als Wortliste, damit sich sagen
# lässt, welche Wörter neu dazugekommen sind. Absichtlich ein Modul-Zustand und
# kein Feld im Assistenten: geschrieben wird aus zwei Ecken (_mitschreiben und
# _geweckt), und beide meinen denselben Streifen.
_ramzi_worte = []

# Bis wann eine Vorschau NICHT mehr anzeigen darf.
#
# DER FEHLER, DEN RAMZI GESEHEN HAT (01.08.2026): "warum steht da jetzt auf
# einmal mein erster Satz? Das verstehe ich nicht." Mal war der Streifen live,
# mal blieb er stehen, mal sprang er zurueck.
#
# Es sind ZWEI Schreiber aus ZWEI Faeden:
#   der Mitlauscher  -> die laufende Vorschau (_mitschreiben)
#   der Arbeiter     -> der fertige Satz      (_geweckt)
# Beide beschriften dieselbe Datei, und wer zuletzt schreibt, gewinnt. Der
# fertige Satz betrifft aber Ton, der SCHON VORBEI ist -- kommt er nach einer
# neueren Vorschau an, ueberschreibt er Neues mit Aeltererem. Genau das sah
# Ramzi als "mein erster Satz steht wieder da".
#
# Mit den Modellen auf der Grafikkarte ist es sogar HAEUFIGER geworden: die
# Ergebnisse kommen jetzt so schnell, dass sie sich ueberholen.
#
# Die Regel dagegen ist einfach: ein fertiger Satz gewinnt, und kurz danach
# darf keine Vorschau mehr dazwischenfunken. Sie handelt ohnehin von Ton, den
# der fertige Satz schon enthaelt.
_final_sperre_bis = 0.0
SPERRE_NACH_FINAL = 1.5


def _ramzi_untertitel(voller_text, offen, vorschau=False):
    """Ramzis eigene Untertitel -- dasselbe System wie meine, so weit es geht.

    Sein Auftrag vom 01.08.2026: "ich hätte gerne, dass es bei mir so
    funktioniert wie bei dir. Was möglich ist, kann rein; was nicht möglich
    ist, muss nicht."

    ÜBERNOMMEN:
      * die Einteilung (zwei Sätze, gedeckelt durch Länge) -- gemeinsame
        Fassung in untertitel.einteilen()
      * der harte Wortumbruch, wenn Whisper gar keine Satzzeichen setzt. Genau
        daran lag seine Textwand: die Zwei-Satz-Grenze griff nie, weil alles
        EIN Satz war.
      * dass der Streifen nicht mitten im Satz verschwindet -- solange er redet,
        steht die Anzeige (`offen`), danach zählt eine Lesezeit statt seines
        Reglers.

    NICHT ÜBERNOMMEN, und das ist keine Bequemlichkeit:
      Bei mir leuchtet das Wort, das GERADE klingt. Das kann ich nur, weil ich
      den Ton selbst erzeuge. Sein Text entsteht erst, NACHDEM er gesprochen
      hat -- ein "aktuelles Wort" gibt es dort nicht mehr.

      Ich hatte es mit einem Durchlauf über die neu verstandenen Wörter
      versucht. Ramzi hat es gesehen und abgelehnt, mit Recht: "am besten
      machst du das komplett weg." Bei einer Verzögerung von mehreren Sekunden
      betont die Bewegung genau das Falsche -- sie sieht lebendig aus, während
      der Text alt ist. Der Streifen ist hier zum Lesen da, nicht zum Gucken.
    """
    global _ramzi_worte, _final_sperre_bis
    if vorschau and time.time() < _final_sperre_bis:
        return                      # ein fertiger Satz steht -- nicht ueberschreiben
    if not vorschau:
        _final_sperre_bis = time.time() + SPERRE_NACH_FINAL
    try:
        import untertitel
        anzeigen = untertitel.einteilen(voller_text)
        if not anzeigen:
            _ramzi_worte = []
            untertitel.zeige('', 'ramzi')
            return
        anzeige = anzeigen[-1]
        _ramzi_worte = anzeige.split()

        # Ohne `worte`: keine Bewegung, nur Text. Siehe oben.
        untertitel.zeige(anzeige, 'ramzi',
                         start=time.time(),
                         dauer=None if offen else untertitel.lesezeit(anzeige),
                         offen=offen)
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
    und die Reflexe unten brauchen jede Schreibweise nur einmal.

    Zuletzt: bekannte Verhörer korrigieren (verhoerer.py), damit "efnes" schon
    hier zu "oeffne" wird und jede Reflex-Prüfung danach die Korrektur
    geschenkt bekommt, statt sie selbst nachzubauen."""
    t = (text or '').lower()
    for a, b in (('ä', 'a'), ('ö', 'o'), ('ü', 'u'), ('ß', 'ss'),
                 ('ae', 'a'), ('oe', 'o'), ('ue', 'u')):
        t = t.replace(a, b)
    t = re.sub(r'[^\wäöüß ]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return verhoerer.korrigiere(t)


def _laeuft_programm(name):
    """Läuft dieses Programm gerade? Über die Windows-Prozessliste.

    tasklist statt einer Bibliothek: es ist eingebaut, braucht keine
    Abhängigkeit und kostet rund hundert Millisekunden -- bei einem Befehl, der
    ohnehin ein Fenster aufmacht, fällt das nicht auf."""
    try:
        aus = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}'],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return name.lower() in (aus.stdout or '').lower()
    except Exception:
        return True      # im Zweifel annehmen, dass es läuft -- dann wird nur
                         # die Taste geschickt, statt ein Fenster aufzudrängen


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
        self.ohr = Weckwort(self._geweckt, modell=modell,
                            beim_erkennen=self._erkannt,
                            beim_mitschreiben=self._mitschreiben,
                            # Sofort die Klappe halten, wenn er mitten in
                            # meinem Satz meinen Namen sagt. Der Mitlauscher
                            # ruft das an, sobald er ihn hoert -- ohne auf eine
                            # Sprechpause zu warten.
                            beim_unterbrechen=self._unterbrich_mich,
                            ist_kurzbefehl=self._ist_kurzbefehl,
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

            # Ramzis Notausgang vom 03.08.2026, nachdem sein Video kaum noch zu
            # hören war: „Statt dass ich in die Einstellungen gehe, in den
            # Sound, und bei all meinen Apps alles auf 100 Prozent mache, sage
            # ich das ganz kurz, und ich kann weitermachen."
            #
            # Absichtlich viele Formulierungen: er hat selbst gesagt, er wisse
            # nicht, welcher Befehl es sein soll. Also muss jeder gehen, der
            # ihm einfällt -- in dem Moment ist er genervt und überlegt nicht,
            # wie es „richtig" heißt.
            (['alles wieder laut', 'alles laut', 'mach alles laut',
              'alles auf hundert', 'alles auf 100', 'lautstarke zuruck',
              'lautstarke wieder normal', 'wieder normal laut',
              'mach die lautstarke wieder', 'ton wieder normal',
              'volle lautstarke', 'alles wieder auf hundert'],
             lambda: self._alles_laut()),

            # "musik" allein steht mit drin, seit Ramzi am 01.08.2026 genau das
            # gesagt hat und es im Chat landete. Sein Wunsch war ausdrücklich,
            # den Umschalt-Charakter zu behalten: sagt er "Musik aus", während
            # nichts läuft, geht sie trotzdem an -- und das ist richtig so, denn
            # Qalam verwechselt "an" und "aus", und der Umschalter tut dann
            # zufällig das Gewollte.
            (['musik', 'spotify an', 'spotify aus', 'spotify pause',
              'lied an', 'lied aus', 'song an', 'song aus', 'playlist an'],
             lambda: self._musik()),

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

    def _alles_laut(self):
        """Jedes Programm auf volle Lautstärke -- Ramzis Handbremse.

        Läuft in einem eigenen Faden: das Aufzählen der Audio-Sitzungen geht
        über COM und braucht ein paar hundert Millisekunden. Solange darf das
        Ohr nicht stehen -- dieselbe Begründung wie bei der Dämpfung selbst.
        """
        def _lauf():
            try:
                # Erst hier importiert, wie überall sonst in dieser Datei: das
                # Modul zieht pycaw und COM nach, und das soll den Start des
                # Ohrs nicht aufhalten.
                import lautstaerke
                anzahl = lautstaerke.alles_laut()
                print(f'[Lautstärke] {anzahl} Programme auf 100 Prozent.', flush=True)
            except Exception as e:
                print(f'[Lautstärke] fehlgeschlagen: {e}', flush=True)
        threading.Thread(target=_lauf, daemon=True).start()
        return 'Alles wieder auf volle Lautstärke.'

    def _musik(self):
        """Wiedergabe umschalten -- oder Spotify erst mal aufmachen.

        Die Multimedia-Taste wirkt mit jedem Player, aber wenn gar keiner läuft,
        wirkt sie mit keinem: sie geht ins Leere, und ich hätte trotzdem "Okay"
        gesagt. Genau die Sorte Antwort, die Ramzi glauben lässt, es sei etwas
        passiert. Läuft Spotify nicht, mache ich es also auf, statt eine Taste
        an niemanden zu schicken."""
        if not _laeuft_programm('Spotify.exe'):
            oeffnen.oeffne('spotify')
            return 'Ich mache Spotify auf.'
        return 'Okay.' if _medien('play_pause') else 'Das hat nicht geklappt.'

    # ------------------------------------------------------------------
    def _abbrechen(self, per_taste=True):
        """Eine laufende Äußerung sofort verwerfen -- Ramzis Notbremse.

        Sein Wunsch, wortgetreu (31.07.2026): "während ich aufnehme, drücke
        ich <Taste>, dann passiert das" -- ausdrücklich eine Taste, KEIN
        Sprachbefehl. Ein gesprochenes "abbrechen" könnte fällig fallen, ohne
        dass er es meint; eine Taste ist ein bewusster Griff.

        "Du kannst das Ganze in der Zwischenablage speichern, falls ich das
        aus Versehen gemacht habe und doch meine Meinung geändert habe" --
        genau das passiert hier: nichts geht endgültig verloren, es landet nur
        nicht bei mir.

        Wirkt auf zwei Ebenen (Begründung in wake_word.py::abbrechen()):
        den Puffer, den die Aufnahmeschleife gerade füllt, UND alles, was
        schon abgegeben, aber noch nicht transkribiert ist."""
        text = self._sammelsatz
        self._sammelsatz = ''
        self.ohr.folge_bis = 0.0
        self.ohr.abbrechen()

        try:
            import warteschlange
            warteschlange.redet_merken(False)
        except Exception:
            pass

        if text:
            try:
                from bruecke import _zwischenablage_schreiben
                _zwischenablage_schreiben(text)
            except Exception:
                pass
            print(f'[Noor] Abgebrochen -- in der Zwischenablage gesichert: {text!r}')
        else:
            print('[Noor] Abbrechen gedrückt, nichts Laufendes zu verwerfen.')

        ton('noor_nichts.wav')
        # Leerer Text versteckt den Streifen -- sichtbares Zeichen, dass es
        # weg ist, ohne dass ich etwas dazu sage (er will ja gerade Ruhe davor).
        # Ueber _ramzi_untertitel, damit auch der Merker der zuletzt gezeigten
        # Woerter mit zurueckgesetzt wird -- sonst gaelte der naechste Satz
        # teilweise als "schon dagewesen" und wuerde nicht aufleuchten.
        _ramzi_untertitel('', offen=False)

    def _pause_umschalten(self):
        """Denkpause an oder aus -- mit Ton UND Anzeige.

        Beides, nicht eines davon: bei einem Umschalter muss er wissen, in
        welchem Zustand er gelandet ist, und ein einzelner Blick auf den
        Bildschirm ist genau das, was er in dem Moment nicht hat (deshalb ja
        die Taste). Der Ton sagt ihm sofort, was passiert ist; die Anzeige
        beantwortet die Frage "bin ich eigentlich noch pausiert?", die zehn
        Sekunden später kommt.

        ZWEI verschiedene Töne, nicht zweimal derselbe. Bei einem Umschalter
        wäre ein identisches Zeichen für beide Richtungen wertlos -- er müsste
        mitzählen, um zu wissen, wo er steht. Absteigend heißt angehalten,
        aufsteigend heißt es geht weiter; das versteht man ohne Erklärung."""
        an = self.ohr.pausieren()
        if an:
            ton('noor_pause_an.wav')
            _ramzi_untertitel('Denkpause – ich warte.', offen=True)
            print('[Noor] Denkpause -- der Satz bleibt stehen.')
        else:
            ton('noor_pause_aus.wav')
            _ramzi_untertitel('', offen=False)
            print('[Noor] Denkpause beendet.')
        return an

    def _abbruch_taste_starten(self):
        """Die rechte Strg-Taste: einmal pausieren, zweimal abbrechen.

        Die RECHTE Strg-Taste, allein. Ramzis Wahl vom 31.07.2026 aus vier
        Vorschlägen: "ich benutze immer die linke für alles, was es braucht --
        die rechte ist für mich wie eine leere Taste."

        Sie ist gleichzeitig ein Modifikator, also könnte ein Strg+C mit der
        rechten Hand hier fälschlich auslösen. Bewusst NICHT abgesichert, auf
        seine ausdrückliche Ansage: "viel zu kompliziert und viel zu
        unwahrscheinlich -- und jetzt, wo ich es weiß, mache ich es sowieso
        nicht." Eine Sonderbehandlung für einen Fall, den es nicht gibt, wäre
        Code, den niemand je wieder versteht.

        DIE DOPPELBELEGUNG (01.08.2026, seine Idee und sein erster Vorschlag):
        einmal drücken hält an, zweimal schnell hintereinander bricht ab.
        Dieselbe Taste für beides, weil beides zur selben Sache gehört -- und
        weil eine zweite Sondertaste eine zweite Sache zum Merken wäre.

        WARUM DER ERSTE DRUCK SOFORT PAUSIERT, statt erst eine Sekunde auf
        einen möglichen zweiten zu warten: in genau dieser Wartesekunde liefe
        die Stille weiter, und sein halber Satz könnte dabei abgeschickt
        werden -- also exakt das, was die Pause verhindern soll. Ein
        Doppeldruck *eskaliert* deshalb: erst hält es an, dann wird verworfen.
        Das ist auch von der Bedeutung her richtig herum, denn Abbrechen aus
        einer Pause heraus ergibt Sinn, umgekehrt nicht.

        Ändern reicht weiterhin eine Zeile: `keyboard.Key.ctrl_r` unten."""
        # Wie schnell ist "zweimal"? Ramzis Vorschlag, und er passt: unter
        # einer Sekunde macht das niemand versehentlich, und wer wirklich
        # abbrechen will, haemmert ohnehin schneller.
        DOPPEL_FENSTER = 1.0
        self._letzter_tastendruck = 0.0
        try:
            from pynput import keyboard

            def _gedrueckt(taste):
                if taste != keyboard.Key.ctrl_r:
                    return
                jetzt = time.time()
                if jetzt - self._letzter_tastendruck <= DOPPEL_FENSTER:
                    # Zurücksetzen, damit ein DRITTER Druck wieder normal
                    # pausiert und nicht als Teil einer Kette gilt.
                    self._letzter_tastendruck = 0.0
                    self._abbrechen()
                else:
                    self._letzter_tastendruck = jetzt
                    self._pause_umschalten()

            self._abbruch_listener = keyboard.Listener(on_press=_gedrueckt)
            self._abbruch_listener.start()
        except Exception as e:
            self._abbruch_listener = None
            print(f'[Noor] Abbruch-Taste nicht verfügbar: {e}')

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

    def _wach_werden(self):
        """Aufwachen: Ton, Platzhalter, Folgefenster -- auf welchem Weg der Name
        auch gehört wurde.

        DASS ES DIESE FUNKTION GIBT, IST DER KERN DER VERZÖGERUNG VOM
        31.07.2026. Das Aufwachen hing vorher allein am Mitlauscher, und der
        kann einen kurzen, alleinstehenden Ruf "Noor" gar nicht hören:
        nachgemessen (`werkzeuge_ohr_messen.py`) sind das 0,66 s Sprache, der
        Mitlauscher sieht aber erst ab 0,36 s hin und das schnelle Modell gibt
        für unter einer Sekunde Ton oft gar keinen Text zurück. Ergebnis für
        Ramzi: er ruft, nichts passiert -- kein Ton, kein Streifen -- und der
        Satz danach war zusätzlich verloren, weil `folge_bis` nur hier gesetzt
        wird. Genau das war sein "der Ton kam erst beim dritten Versuch".

        Jetzt darf auch das genaue Modell wecken (siehe _geweckt). Das kostet
        eine gute Sekunde mehr als der Mitlauscher, ist aber der Weg, der immer
        funktioniert -- und ein Ton nach 1,5 s ist unendlich besser als keiner.

        Doppelt tönen kann es nicht: ein offenes Folgefenster heißt, dass schon
        geweckt wurde. Dafür braucht es keinen eigenen Merker, der wieder
        veralten könnte."""
        if time.time() < self.ohr.folge_bis:
            return
        ton('noor_wach.wav')
        # Musik leiser, solange er redet -- nicht aus. Ramzis Wunsch vom
        # 31.07.2026; die Begründung und das Zurückstellen auf den Wert von
        # VORHER stehen in lautstaerke.py. Zurück geht es in _lautstaerke_wache().
        # Im Hintergrund: das Aufzählen der Audio-Sitzungen geht über COM und
        # kostet ein paar hundert Millisekunden. Diese Zeile läuft im Faden des
        # Mitlauschers, und Ramzi hat sofort gemerkt, dass die Reaktion dadurch
        # länger dauerte. Nichts, was er hört, darf hinter einer
        # Lautstärke-Abfrage warten.
        try:
            import lautstaerke
            lautstaerke.daempfen_im_hintergrund()
        except Exception:
            pass
        # Ramzi will SOFORT etwas Sichtbares zum Ton, egal was drinsteht. Das
        # frühere Flacker-Problem lag an der 180-Sekunden-Haltezeit, nicht am
        # Platzhalter selbst -- mit dem einstellbaren Regler (Standard 10 s)
        # wird er zuverlässig durch echten Text ersetzt.
        _untertitel('… ich höre zu …', 'ramzi')
        # Ab jetzt zählt auch der nächste Satz ohne Namen als Auftrag: er sagt
        # den Namen, wartet auf dieses Zeichen und redet dann erst los.
        self.ohr.folge_bis = time.time() + einstellungen.hole('folge_sekunden')

    def _mitschreiben(self, vorlaeufig):
        """Was gerade zu hören ist, sofort auf den Streifen -- noch während
        Ramzi redet.

        Ramzis Grund dafür, und es ist der bessere Grund als "sieht nett aus":
        er redet nach dem Ton weiter und braucht ein Zeichen, dass er nicht ins
        Leere spricht. Am 31.07.2026: "ich habe darauf gehofft, dass der
        Untertitel gleich kommt, damit ich weiß, dass du immer noch zuhörst.
        Ist nie passiert."

        Vorher war das abgeschaltet, weil der Streifen wild wechselnden Unsinn
        zeigte -- das kam aber vom kleinen Modell, das über 3-Sekunden-Fenster
        stolperte. Der Mitlauscher hört inzwischen mit demselben genauen Modell
        wie der Rest (siehe wake_word.py, Eigenschaft `flink`), und im
        Protokoll stehen dort lesbare Sätze.

        Der Text bleibt trotzdem ein Ausschnitt der letzten Sekunden, nicht der
        ganze Satz -- den vollständigen legt _geweckt() darüber, sobald er
        fertig ist. Das ist Absicht: hier zählt "sie hört mich", nicht
        Wortgenauigkeit.

        UND DAS FOLGEFENSTER BLEIBT OFFEN, SOLANGE ER REDET. Das ist kein
        Nebeneffekt, sondern der Grund, warum Ramzi am 31.07.2026 drei Minuten
        in den Wind gesprochen hat. Im Protokoll standen seine Sätze
        vollständig und gut lesbar -- aber ohne Reaktion, weil das genaue
        Modell den Namen im ersten Stück anders geschrieben hatte als der
        Mitlauscher. Damit war das Fenster nie aufgegangen, und jedes weitere
        Stück wurde transkribiert und weggeworfen. Das Ohr hörte ihn, verstand
        ihn, und tat nichts. Wer mitgeschrieben wird, wird auch gehört."""
        if not vorlaeufig:
            return
        # `offen`: er redet noch -- der Streifen bleibt stehen, egal wie kurz
        # die Haltezeit eingestellt ist. `vorschau`: das hier darf einem
        # fertigen Satz nicht ins Wort fallen (siehe _final_sperre_bis).
        _ramzi_untertitel(vorlaeufig, offen=True, vorschau=True)
        self.ohr.folge_bis = max(self.ohr.folge_bis,
                                 time.time() + einstellungen.hole('folge_sekunden'))

    def _ist_kurzbefehl(self, text):
        """Ist das schon ein fertiger Reflex? Dann nicht auf eine Denkpause
        warten.

        Wird vom Ohr gefragt, WÄHREND Ramzi noch redet, und entscheidet dort
        über die Stille-Schwelle. Bei "Noor, wie spät ist es" kommt nichts mehr
        -- vier Sekunden darauf zu warten hat den Reflex langsamer gemacht als
        selbst auf die Uhr zu sehen, und genau das hat Ramzi bemängelt."""
        ohne_namen = WECKWORT.sub('', text or '')
        geglaettet = normalisiere(ohne_namen)
        if not geglaettet:
            return False
        if any(b in geglaettet
               for bruchstuecke, _ in self.reflexe for b in bruchstuecke):
            return True
        # Eine Stellschraube ist genauso fertig wie "wie spät ist es": nach
        # "mach die Redepause auf 2 Sekunden" kommt nichts mehr. Gerade DIESER
        # Befehl darf nicht an der alten, langen Redepause hängen -- sonst wartet
        # das Ohr bis zu zehn Sekunden, um zu erfahren, dass es weniger warten
        # soll.
        if stellschrauben.ist_stellschraube(ohne_namen):
            return True
        # „Mach mir YouTube auf" ist genauso fertig -- danach kommt nichts mehr.
        # „Mach das wieder zu" ebenso: beide sind mit dem letzten Wort zu Ende,
        # und wer darauf noch die volle Redepause wartet, lässt Ramzi ohne
        # Grund vor einem Fenster stehen, das längst zugehen könnte.
        try:
            if schliessen.verstehe(ohne_namen) is not None:
                return True
            return oeffnen.verstehe(ohne_namen) is not None
        except Exception:
            return False

    def _erkannt(self):
        """Der Name ist gefallen -- mitten im Satz, nicht erst danach.

        Der schnelle Weg: der Mitlauscher hat den Namen im laufenden Satz
        gefunden, lange bevor der Satz fertig ist. Klappt das, kommt der Ton
        nach Bruchteilen einer Sekunde. Klappt es nicht, weckt _geweckt().

        Der Untertitel zeigt während des Sprechens bewusst nur den Platzhalter
        und nicht den rohen Mitschrieb: Ramzi hat am 31.07.2026 ein Video
        geschickt, in dem der Streifen wild wechselnden Unsinn zeigte. Ursache
        war nicht das Ohr -- die richtigen Sätze im Log waren gut lesbar --
        sondern die Anzeige: sie zeigte den Text aus unabhängigen
        3-Sekunden-Fenstern, die mitten im Wort anfangen und aufhören."""
        self._wach_werden()

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

        # Ein "fertig" ohne Text ist seit dem 01.08.2026 ein gueltiger Fall: das
        # Ohr meldet damit "er ist fertig", wenn nach der letzten Satzpause nur
        # noch Stille kam (siehe wake_word.abgeben -- der Freeze-Bug). Ist dann
        # aber auch nichts gesammelt, gaebe es einen leeren Auftrag. Das darf
        # nicht lautlos passieren, deshalb steht es im Protokoll.
        if endgueltig and not text and not self._sammelsatz:
            print(f'[{time.strftime("%H:%M:%S")}] [Noor] fertig, aber nichts '
                  f'gesammelt -- nichts abzuschicken')
            return

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

        # Im Schlaf höre ich weiter, reagiere aber nur aufs Aufwachen.
        if self.ohr.schlaeft:
            if self._sammelsatz:
                _ramzi_untertitel(self._sammelsatz, offen=not endgueltig)
            if endgueltig and self.AUFWECKER.search(text):
                self.ohr.schlaeft = False
                self._sag('Ich bin wieder da.')
            self._sammelsatz = ''
            return

        # Der zweite Weg zum Aufwachen -- der, der immer funktioniert. Hat der
        # Mitlauscher den Namen verpasst (bei einem kurzen Ruf ist das die
        # Regel, nicht die Ausnahme), weckt hier das genaue Modell. Siehe
        # _wach_werden() für die Messung dahinter.
        if WECKWORT.search(text):
            self._wach_werden()

        # Leeren Text NICHT auf den Streifen legen: der Streifen versteckt sich
        # bei leerem Text. Ruft Ramzi nur den Namen, bleibt nach dem Abziehen
        # des Weckworts genau das übrig -- und damit verschwand der Platzhalter
        # sofort wieder, den _wach_werden() eine Zeile vorher hingeschrieben
        # hat. Für Ramzi sah es aus, als würde nur noch meine Antwort
        # untertitelt und sein eigener Satz nie (sein Fund 2 vom 31.07.2026).
        if self._sammelsatz:
            # `offen` hängt daran, ob das eine echte Stille war: solange er
            # mitten im Satz nachdenkt, bleibt die Anzeige stehen. Ist er
            # fertig, zählt eine Lesezeit -- und nicht mehr sein Regler.
            _ramzi_untertitel(self._sammelsatz, offen=not endgueltig)

        # Wenn ich gerade rede und angesprochen werde: erst mal Klappe halten.
        # Das ist die einfache Form vom Unterbrochenwerden -- noch nicht
        # mitten im Wort, aber schon "du hast Vorrang".
        if self.sprecher.spricht_gerade():
            self.sprecher.stoppe()

        if not endgueltig:
            # Er redet weiter -- Fenster offenhalten, noch nichts ausführen.
            self.ohr.folge_bis = time.time() + 20.0
            return

        # Echte Stille -- er hat seinen Platz in der Warteschlange abgegeben.
        #
        # Ramzis Idee vom 31.07.2026: sein Reden gehört zur selben Warteschlange
        # wie mein Sprechen, damit ich ihm nie ins Wort falle. EXPLIZIT hier
        # freigeben, nicht erst warten, bis die Markierung von selbst verfällt
        # (siehe warteschlange.py) -- sonst bliebe mein eigener Reflex kurz
        # stumm, obwohl er längst fertig ist. Der Mitlauscher markiert den
        # Platz laufend, solange `erkannt` oder `folge_bis` gilt; hier, wo
        # `endgueltig=True` feststeht, ist genau der Moment, ihn abzugeben.
        try:
            import warteschlange
            warteschlange.redet_merken(False)
        except Exception:
            pass

        auftrag = self._sammelsatz
        self._sammelsatz = ''
        geglaettet = normalisiere(auftrag)

        # Reflexe gelten NUR fuer kurze Aeusserungen.
        #
        # Das ist die teuerste Lektion des 31.07.2026. Ramzi hat vier Minuten
        # geredet, wurde leise -- und bekam als Antwort die Uhrzeit. Der Grund
        # steht in seinem eigenen Satz: "bald um 10 UHR IST mein Limit
        # zurueckgesetzt". Das Bruchstueck "uhr ist" steht in der Liste unten,
        # und geprueft wurde der GANZE gesammelte Text. In vier Minuten Rede
        # steckt irgendwo immer eines dieser Bruchstuecke -- die Liste ist
        # bewusst grosszuegig, damit er nicht Kommandos aufsagen muss, und
        # genau diese Grosszuegigkeit wird bei langem Text zur Falle. Sein
        # ganzer Auftrag war damit weg, und er hat es als "ich rede fuenf
        # Minuten umsonst" erlebt.
        #
        # Ein Reflex ist von Natur aus kurz: "wie spaet ist es", "mach die
        # Musik an", "lass mich in Ruhe". Zehn Woerter sind reichlich Luft --
        # der laengste echte Reflex in seinen Tests hatte sechs.
        kurz_genug = len(geglaettet.split()) <= REFLEX_MAX_WOERTER
        for bruchstuecke, handler in (self.reflexe if kurz_genug else []):
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

        # Die Stellschrauben -- NACH den Reflexen oben, und das ist wichtig.
        #
        # "mach die Musik lauter" enthält "lauter", und das ist auch das Wort,
        # mit dem Ramzi MEINE Stimme lauter macht. Stünde die Prüfung vor den
        # Reflexen, würde ich seine Musik in Ruhe lassen und stattdessen mich
        # selbst lauter stellen -- ein falsch verstandener Befehl, der zwei
        # Dinge auf einmal verkehrt macht. Die Musik-Reflexe sind die
        # spezielleren, also kommen sie zuerst.
        #
        # Absichtlich der ROHE Auftrag und nicht `geglaettet`: normalisiere()
        # macht aus "1,4" ein "1 4", und damit wäre jede Kommazahl kaputt, bevor
        # sie ankommt. stellschrauben glättet selbst und lässt den Dezimalpunkt
        # dabei stehen.
        try:
            gestellt = stellschrauben.verstehe(auftrag)
        except Exception as e:
            gestellt = None
            print(f'[Noor] Stellschraube fehlgeschlagen: {e}')
        if gestellt:
            self.ohr.folge_bis = 0.0
            ton('noor_reflex.wav')
            self._sag(gestellt)
            return

        # „Mach das wieder zu." VOR dem Öffnen, obwohl beide dasselbe Netz
        # benutzen: oeffnen.verstehe() weist Schließ-Sätze zwar über GEGENTEIL
        # ab, aber diese Reihenfolge macht die Absicht im Code sichtbar --
        # wer „zu" sagt, meint nicht „auf", und das soll man nicht erst aus
        # einer Ausschlussliste im anderen Modul erschließen müssen.
        try:
            zugemacht = schliessen.mach(auftrag)
        except Exception as e:
            zugemacht = None
            print(f'[Noor] Schließen fehlgeschlagen: {e}')
        if zugemacht:
            self.ohr.folge_bis = 0.0
            # Kein eigener Ton hier: noor-links-zu.ps1 und noor-zu.ps1 spielen
            # den Abwärts-Ton selbst, sobald sie wirklich etwas geschlossen
            # haben. Ein Ton von hier käme auch dann, wenn gar nichts zu
            # schließen war -- und ein Zeichen für nichts ist schlimmer als
            # keins.
            self._sag(zugemacht)
            return

        # „Mach mir YouTube auf." Zuletzt in der Kette, weil es das breiteste
        # Netz hat: ein Stichwort aus dem Katalog kann in vielen Sätzen
        # vorkommen. Was vorher greift, ist spezieller und hat Vorrang.
        try:
            aufgemacht = oeffnen.mach(auftrag)
        except Exception as e:
            aufgemacht = None
            print(f'[Noor] Öffnen fehlgeschlagen: {e}')
        if aufgemacht:
            self.ohr.folge_bis = 0.0
            ton('noor_fenster_auf.wav')
            self._sag(aufgemacht)
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

            # Die Übergabe MITSCHREIBEN -- der Grund ist teuer bezahlt.
            #
            # Ramzis Befund "in 15--20 % der Fälle kommt mein Satz nicht an" war
            # aus dem Protokoll nicht einzukreisen, weil hier weder Erfolg noch
            # Fehlschlag eine Zeile hinterließ: bei Erfolg absichtlich nichts,
            # bei Fehlschlag nur ein gesprochener Satz, der nirgends landet.
            # Am 02.08.2026 blieben nach der Auswertung von 269 Sätzen 14 Fälle
            # übrig, über die sich schlicht NICHTS sagen ließ -- nicht, weil sie
            # rätselhaft waren, sondern weil niemand hingesehen hatte.
            #
            # Mitgeschrieben wird auch, WAS vorn lag. Das ist die Größe, die
            # Ramzi selbst im Verdacht hat ("beim Zocken im Vollbild geht es
            # nicht"), und sie lässt sich hinterher nicht mehr rekonstruieren.
            vorn = ''
            try:
                import ctypes
                u = ctypes.windll.user32
                h = u.GetForegroundWindow()
                n = u.GetWindowTextLengthW(h)
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                vorn = b.value[:40]
            except Exception:
                pass
            zeit = time.strftime('%H:%M:%S')
            print(f'[{zeit}] [Brücke] gebe weiter: {len(auftrag)} Zeichen, '
                  f'vorn liegt {vorn!r}')

            ok, meldung = sende(auftrag)
            print(f'[{time.strftime("%H:%M:%S")}] [Brücke] '
                  + ('angekommen' if ok else f'FEHLSCHLAG -- {meldung}'))
            if not ok:
                self._sag(meldung or 'Das hat nicht geklappt.')
            # Bei Erfolg sage ich hier NICHTS. Die eigentliche Antwort spricht
            # der Stop-Hook, sobald sie steht -- eine Zwischenansage wuerde sich
            # bei kurzen Antworten mit ihr ins Wort fallen.

        threading.Thread(target=_lauf, daemon=True).start()

    # ------------------------------------------------------------------
    def _lautstaerke_wache(self):
        """Die Musik wieder laut machen, sobald das Gespräch vorbei ist.

        Ein Wächter und nicht ein Aufruf an jeder Stelle, an der ein Auftrag
        endet: es gibt viele solche Stellen (Reflex, Brücke, Fenster läuft ab,
        Fehler unterwegs), und würde eine davon vergessen, bliebe Ramzis Musik
        für immer leise. Ein Wächter, der auf einen Zustand schaut statt auf ein
        Ereignis, kann das nicht -- er heilt sich selbst.

        Gedämpft bleibt es auch, solange ich noch antworte: sonst wird die Musik
        mitten in meinem Satz laut."""
        while self._laeuft.is_set():
            time.sleep(0.4)
            try:
                import lautstaerke
                if not lautstaerke.gedaempft():
                    continue
                if time.time() < self.ohr.folge_bis:
                    continue
                if self.sprecher.spricht_gerade():
                    continue
                from wake_word import qalam_nimmt_auf
                if qalam_nimmt_auf():
                    continue        # Ramzi diktiert -- das dämpft selbst
                lautstaerke.zuruecksetzen_im_hintergrund()
            except Exception:
                continue

    def starte(self):
        print('[Noor] Lade Modelle …')
        self.ohr.starte()
        self._laeuft.set()
        threading.Thread(target=self._lautstaerke_wache, daemon=True).start()
        threading.Thread(target=self._sprechpost_wache, daemon=True).start()
        threading.Thread(target=self._stimmen_wache, daemon=True).start()
        self._abbruch_taste_starten()
        # XTTS im Hintergrund warmlaufen lassen, damit nicht der erste Satz
        # 16 Sekunden auf das Modell wartet. Bis es da ist, spricht Piper.
        try:
            import stimme_xtts
            stimme_xtts.vorwaermen()
        except Exception as e:
            # Nicht stumm verschlucken: geht das schief, spricht zwar Piper
            # weiter und alles klingt gesund -- aber dann suche ich beim
            # naechsten Mal am falschen Ende. Genau so hat sich der
            # torchaudio-Konflikt am 02.08.2026 drei Anlaeufe lang versteckt.
            print('[Stimme] XTTS-Vorwaermen nicht moeglich: %s' % e, flush=True)
        print('[Noor] Ich höre zu. Sag meinen Namen.')
        threading.Thread(target=self._begruessen, daemon=True).start()

    def _begruessen(self):
        """Die Startansage -- aber erst, wenn die richtige Stimme da ist.

        Ramzi am 02.08.2026: "beim Start sagst du mit Thorsten immer noch 'ich
        höre zu'." Zwei Dinge daran, beide seine Ansage: der Satz heißt jetzt
        "Ich bin jetzt da.", und er wartet auf XTTS. Sonst begrüßt mich die
        Stimme, die wir gerade ersetzt haben -- ausgerechnet der eine Satz, den
        er jedes Mal hört.

        Gewartet wird begrenzt: kommt XTTS nicht (kein Platz auf der Karte),
        wird trotzdem gesprochen. Lieber die alte Stimme als gar keine Ansage,
        denn sie ist auch das Zeichen "ich laufe wieder".
        """
        try:
            import stimme_xtts
            for _ in range(160):         # bis zu 80 s -- Laden dauert 16 s, plus Anlaeufe
                if stimme_xtts.bereit() or not self._laeuft.is_set():
                    break
                time.sleep(0.5)
            else:
                # XTTS kam nicht -- dann keine Ansage. Siehe voice_output.
                return
        except Exception:
            return
        # Laenger als Ramzis Wortlaut, und das ist kein Eigenmaechtigkeit:
        # unter ~40 Zeichen halluziniert XTTS nachweislich ("Fertig." kam auf
        # 2 von 12 sauber). "Ich bin jetzt da." allein sind 17 Zeichen -- und
        # genau das ist passiert, Ramzi hoerte danach fuenf Sekunden Unsinn.
        self.sprecher.sprich('Ich bin jetzt da, du kannst mich ansprechen.')

    def _stimmen_wache(self):
        """Den Umschalter auf der Tafel befolgen -- ohne Neustart.

        Ramzi will beim Zocken die Karte frei haben, aber sonst Ludvig hoeren.
        Ein Neustart dafuer waere in einem Spiel unbrauchbar. Also sieht dieser
        Faden auf den Wert und handelt: Haken raus -> Modell entladen und die
        Karte hergeben; Haken rein -> im Hintergrund wieder laden.

        Welche Stimme SPRICHT, entscheidet voice_output ohnehin bei jedem Satz
        neu am selben Wert. Hier geht es allein um den Speicher.
        """
        import stimme_xtts
        while self._laeuft.is_set():
            try:
                gewuenscht = (einstellungen.hole('stimme_motor') or 'xtts')
                if gewuenscht == 'piper':
                    if stimme_xtts.entladen():
                        print('[Stimme] Ludvig entladen, Karte frei.', flush=True)
                elif not stimme_xtts.bereit():
                    stimme_xtts.vorwaermen()
            except Exception:
                pass
            time.sleep(2.0)

    def _unterbrich_mich(self):
        """Ramzi hat mitten in meinem Satz meinen Namen gesagt -- aufhoeren.

        Bewusst hart und ohne Ausklingen: er hat Vorrang, und ein Satz, der
        noch zu Ende gesprochen wird, fuehlt sich fuer ihn genau wie das an,
        worueber er sich beschwert hat.
        """
        try:
            if self.sprecher.spricht_gerade():
                self.sprecher.stoppe()
                print('[Stimme] unterbrochen -- Ramzi hat uebernommen.', flush=True)
        except Exception:
            pass

    def _sprechpost_wache(self):
        """Saetze aussprechen, die andere Prozesse eingeworfen haben.

        Warum das hier laeuft und nicht im Absender: mit XTTS haelt nur EIN
        Prozess das Modell (2,6 GB auf der Karte, 16 s Ladezeit). Ein
        kurzlebiger Aufruf wie noor-sprich.ps1 kann es nicht selbst laden --
        er wirft seinen Satz ein und ist fertig, ich spreche ihn.

        Die Warteschlange-Regeln gelten unveraendert: `sprich()` wartet von
        selbst, wenn Ramzi gerade redet. Ich hole hier also nur ab.
        """
        import sprechpost
        while self._laeuft.is_set():
            try:
                sprechpost.bereit_melden()
                text = sprechpost.abholen()
                if text:
                    self.sprecher.sprich(text)
                else:
                    time.sleep(0.25)
            except Exception:
                time.sleep(1.0)

    def stoppe(self):
        self._laeuft.clear()
        self.ohr.stoppe()
        self.sprecher.stoppe()
        if getattr(self, '_abbruch_listener', None):
            self._abbruch_listener.stop()


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
