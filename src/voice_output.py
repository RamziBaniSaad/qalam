"""Sprachausgabe für Qalam — lokal, offline, ohne Netz und ohne Kosten.

Nutzt Piper (ONNX). Deutsche Stimmen liegen in `stimmen/`.

Warum satzweise gesprochen wird: Bei einem Gespräch zählt nicht, wann die
Antwort *fertig* ist, sondern wann der erste Ton kommt. Piper braucht für einen
Satz wenige Millisekunden — wird also schon gesprochen, während der Rest noch
entsteht, wartet der Zuhörer nie auf den ganzen Text.

Aufruf von außen (das ist der Weg, den Skripte nehmen):
    python src/voice_output.py "Hallo Ramzi."
    python src/voice_output.py --stimme de_DE-ramona-low "Hallo."
    echo "Text" | python src/voice_output.py -

Als Datei aufrufen, nicht als `-m src.voice_output`: bei `-m` liegt der
Repo-Ordner im Suchpfad, nicht `src/` — und die Untertitel-Einteilung holt sich
`untertitel` als Nachbarn. Der Modul-Aufruf bricht deshalb erst mitten im
Sprechen ab, mit `ModuleNotFoundError: untertitel`.
"""
import contextlib
import os
import queue
import re
import sys
import threading
import time

# Piper und sounddevice erst bei Bedarf laden -- der Import kostet ~1 s, und
# nicht jeder Start von Qalam braucht die Stimme.
_voice_cache = {}

STIMMEN_ORDNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stimmen')
STANDARD_STIMME = 'de_DE-thorsten-medium'

# Satzende erkennen: Punkt/Frage/Ausruf gefolgt von Leerzeichen, aber nicht bei
# Abkürzungen wie "z.B." oder Zahlen wie "3.000".
_SATZ_ENDE = re.compile(r'(?<=[.!?…])\s+(?=[A-ZÄÖÜ„"(])')


def _zusammengezogen(phoneme):
    """Zerlegte Zeichen zusammenziehen: 'c' + kombinierende Cedille -> 'ç'.

    DER ICH-LAUT. Gefunden am 01.08.2026, als beim Erzeugen von Stimmproben
    zwanzigmal "Missing phoneme from id map: ̧" im Protokoll stand.

    espeak-ng liefert den deutschen ich-Laut als ZWEI Zeichen (c + U+0327).
    Fünf der neun deutschen Piper-Stimmen -- eva_k, karlsson, kerstin, pavoque,
    ramona, alle aus der älteren "low"-Reihe -- kennen in ihrer Phonem-Karte nur
    die zusammengesetzte Form ç (U+00E7). Die Cedille fällt dort also heraus,
    und übrig bleibt ein nacktes "c": aus "ich" wird ein Laut, den es im
    Deutschen nicht gibt. Bei "ich", "nicht", "mich", "möchte" -- also in jedem
    zweiten Satz.

    Warum das hier steht und nicht in der Bibliothek: `synthesize()` ruft
    `self.phonemize()` auf, und diese Funktion wird beim Laden davorgesetzt.
    Damit gilt die Reparatur für JEDEN Weg (Sprechen, WAV schreiben, Probe-Knopf)
    und die Bibliothek bleibt unangetastet.

    Angefasst wird ausschliesslich ein kombinierendes Zeichen, und nur wenn es
    sich wirklich zusammenziehen lässt -- ein Phonem, das aus mehreren echten
    Zeichen besteht, bleibt unberührt."""
    import unicodedata
    aus = []
    for p in phoneme:
        if aus and len(p) == 1 and unicodedata.combining(p):
            zusammen = unicodedata.normalize('NFC', aus[-1] + p)
            if len(zusammen) == 1:
                aus[-1] = zusammen
                continue
        aus.append(p)
    return aus


def _braucht_reparatur(stimme):
    """Kennt das Modell nur die zusammengesetzte Cedille? Dann fehlt ihm der
    ich-Laut, sobald espeak ihn zerlegt liefert."""
    try:
        karte = stimme.config.phoneme_id_map or {}
    except Exception:
        return False
    return '̧' not in karte and 'ç' in karte


def _lade_stimme(name):
    """Stimme laden und merken. Das Laden kostet ~0,5 s, das Sprechen danach kaum."""
    if name in _voice_cache:
        return _voice_cache[name]

    from piper import PiperVoice
    pfad = os.path.join(STIMMEN_ORDNER, f'{name}.onnx')
    if not os.path.exists(pfad):
        raise FileNotFoundError(
            f'Stimme "{name}" fehlt. Holen mit:\n'
            f'  python -m piper.download_voices --download-dir stimmen {name}'
        )
    stimme = PiperVoice.load(pfad)
    if _braucht_reparatur(stimme):
        # Nur für die betroffenen Modelle. Die aktuelle Stimme
        # (thorsten-medium) kennt beide Schreibweisen und läuft unverändert
        # durch -- an einer Stimme, die funktioniert, wird nichts geändert.
        _urspruenglich = stimme.phonemize
        stimme.phonemize = lambda text: [_zusammengezogen(p)
                                         for p in _urspruenglich(text)]
    _voice_cache[name] = stimme
    return stimme


def in_saetze(text):
    """Text in Sätze zerlegen, damit satzweise gesprochen werden kann."""
    text = ' '.join(text.split())
    if not text:
        return []
    return [s for s in _SATZ_ENDE.split(text) if s.strip()]


# Eingehaengt von assistant.py: "darf Ramzi gerade noch ohne meinen Namen
# antworten?" Solange das gilt, bleibt die Musik unten -- siehe `_leiser`.
# Fehlt der Haken (Qalam allein, Testlauf), verhaelt sich alles wie vorher.
gespraech_offen = None


def _leiser(an):
    """Musik dämpfen bzw. zurückstellen, ohne daran scheitern zu können.

    Beim ZURÜCKSTELLEN wird gewartet, solange Ramzi selbst redet.

    Ramzis Befund vom 06.08.2026, mehrfach erlebt: "während ich rede, geht
    die Musik wieder lauter." Gemessen und bestätigt -- die Dämpfung hing am
    falschen Ereignis. Sie ging hoch, sobald ICH mit Sprechen fertig war,
    ohne zu prüfen, ob ER inzwischen angefangen hat. Genau das ist sein
    Alltag: er fängt mitten in meinem Satz an. Aus seiner Sicht sah es so
    aus, als würde die Musik ausgerechnet dann laut, wenn er verstanden
    werden will.

    Die Dämpfung gehört also an ZWEI Bedingungen, nicht an eine: leise
    bleibt es, solange einer von uns beiden redet. Erst wenn BEIDE still
    sind, geht sie zurück.

    Die Obergrenze ist kein Schönheitsfehler, sondern Absicht: hängt der
    Redet-Merker fest, bliebe die Musik sonst für immer leise, und Ramzi
    müsste seinen Notausgang benutzen ("alles wieder laut"). Ein Wartender,
    der nicht aufgeben kann, ist schlimmer als eine Sekunde zu früh."""
    try:
        import lautstaerke
        if an:
            # Eigener Prozess, nicht eigener Faden -- Begruendung in
            # lautstaerke.py: dieser Code hat das Ohr schon einmal getoetet.
            lautstaerke.daempfen_im_hintergrund()
            return

        import threading
        import warteschlange

        # DRITTE BEDINGUNG: sein Gespraechsfenster.
        #
        # Ramzis Wunsch vom 15.08.2026 -- die Musik soll leise bleiben, solange
        # er nach meiner Antwort noch ohne meinen Namen reden darf. Dann hoert
        # er am Ton, ob sein Fenster offen ist, und sein Mikrofon versteht ihn,
        # weil die Lautsprecher unten sind.
        #
        # UND WARUM ES DREIMAL NICHT GEWIRKT HAT, als ich es nur im Waechter
        # (`assistant._lautstaerke_wache`) gebaut habe: die Musik wird an ZWEI
        # unabhaengigen Stellen wieder hochgestellt. Der Waechter ist die eine,
        # dieser Zweig hier ist die andere -- und er feuert sofort, wenn mein
        # letzter Satz zu Ende ist. Was der Waechter danach noch entscheidet,
        # ist gleichgueltig: hochgestellt war es da schon. Eine Bedingung, die
        # nur an einer von zwei Tueren haengt, ist keine Bedingung.
        def _offen():
            try:
                return bool(gespraech_offen and gespraech_offen())
            except Exception:
                return False

        def _wenn_beide_still():
            # Höchstens 90 s warten. Länger als das redet niemand am Stück,
            # und wenn doch, ist ein einmaliges Zurückstellen das kleinere
            # Übel gegenüber dauerhaft leiser Musik.
            ende = time.time() + 90
            while time.time() < ende:
                try:
                    if not warteschlange.ramzi_redet() and not _offen():
                        break
                except Exception:
                    break
                time.sleep(0.3)
            lautstaerke.zuruecksetzen_im_hintergrund()

        try:
            redet = warteschlange.ramzi_redet()
        except Exception:
            redet = False
        if redet or _offen():
            threading.Thread(target=_wenn_beide_still, daemon=True).start()
        else:
            lautstaerke.zuruecksetzen_im_hintergrund()
    except Exception:
        pass


_video_prozess = None


def _videos(an):
    """Videos anhalten bzw. wieder anlaufen lassen -- siehe videos.py.

    Ramzi am 02.08.2026: Dämpfen reicht bei einem Video nicht, das Bild läuft
    ja weiter. „Ich möchte davon eigentlich nichts verpassen." Musik bleibt
    ausdrücklich beim Dämpfen.

    Eigener PROZESS, kein Faden: die Medien-Schnittstelle kommt über COM/WinRT
    ins Haus, und genau diese Sorte Aufruf hat am 31.07. schon einmal das Ohr
    getötet (siehe lautstaerke.py). Anders als damals bei der Lautstärke ist
    hier nachgemessen, dass ein abgesetzter Prozess die Sitzungen auch wirklich
    sieht -- dort war genau das der Grund, warum es beim Faden bleiben musste.
    """
    global _video_prozess
    try:
        import subprocess
        skript = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'videos.py')
        p = subprocess.Popen(
            [sys.executable, skript, '--anhalten' if an else '--fortsetzen'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if an:
            _video_prozess = p
    except Exception:
        pass


def _videos_abwarten():
    """Warten, bis wirklich angehalten ist -- aber nicht ewig.

    Der Aufruf dauert gemessen ~415 ms. Die stehen NICHT vor dem ersten Wort
    herum: angestoßen wird er zusammen mit der Dämpfung, abgewartet erst kurz
    bevor der erste Ton rausgeht -- dazwischen erzeugt Piper ohnehin. Damit
    kostet das Anhalten in der Praxis fast nichts.

    Der Zeitausfall ist Absicht: hängt die Medien-Schnittstelle, rede ich
    lieber über ein laufendes Video, als gar nicht zu reden.
    """
    global _video_prozess
    p, _video_prozess = _video_prozess, None
    if p is None:
        return
    try:
        p.wait(timeout=3)
    except Exception:
        pass


# --- Die Bühne: leiser stellen und Video anhalten, EINMAL für den Redezug ---
#
# Ramzis Befund vom 07.08.2026, während dieses Umbaus: "nach einer bestimmten
# Zeit geht das Video einfach weiter, obwohl du gerade noch redest."
#
# Der Grund war die Zuständigkeit, nicht die Zeit. Anhalten und Fortsetzen
# hingen am EINZELNEN Satz: jedes `_sprich()` hielt beim Betreten an und ließ
# beim Verlassen wieder laufen. Solange eine lange Antwort ein einziger Aufruf
# war, fiel das nicht auf. Sobald mehrere Aufträge hintereinander kommen --
# und genau das macht die Sprech-Zentrale --, lief das Video zwischen je zwei
# Sätzen kurz an. Für ihn sah es aus, als hätte ich mittendrin aufgegeben.
#
# Also ein Zähler: der erste, der die Bühne betritt, hält das Video an; erst
# wenn der letzte sie verlässt, läuft es weiter. Die Zentrale hält sie über
# den ganzen Zug, die einzelnen Sätze nur der Vollständigkeit halber -- damit
# es auch dann stimmt, wenn ein Satz ohne Zentrale gesprochen wird (Qalam
# allein, ohne laufendes Ohr).
_buehne_zahl = 0
_buehne_sperre = threading.Lock()


def buehne_an():
    """Musik dämpfen und Video anhalten -- und mitzählen, wer sie hält."""
    global _buehne_zahl
    with _buehne_sperre:
        _buehne_zahl += 1
        if _buehne_zahl > 1:
            return
    _leiser(True)
    _videos(True)


def buehne_aus():
    """Loslassen. Das Video läuft erst weiter, wenn niemand mehr redet."""
    global _buehne_zahl
    with _buehne_sperre:
        if _buehne_zahl <= 0:
            return
        _buehne_zahl -= 1
        if _buehne_zahl > 0:
            return
    # Fortgesetzt wird nur, was ich selbst angehalten habe (videos.py). Das
    # Zurückstellen der Lautstärke gehört dem Wächter im Assistenten -- hier
    # nur das Netz für den Fall, dass es den nicht gibt.
    _videos(False)
    _notbremse_lautstaerke()


def _notbremse_lautstaerke():
    """Zurückstellen, falls es sonst niemand tut.

    Normalerweise gehört das Zurückstellen dem Wächter im Assistenten -- der
    weiß, ob Ramzi noch im Gespräch ist, und nur einer darf entscheiden. Läuft
    das Ohr aber gar nicht (Qalam allein, oder es ist abgestürzt), gibt es
    diesen Wächter nicht, und die Musik bliebe für immer leise.

    Deshalb hier ein Netz statt einer zweiten Zuständigkeit: liegt der Merker
    schon länger als eine Minute, hat ihn niemand abgeholt."""
    try:
        import lautstaerke
        if not os.path.exists(lautstaerke.MERKER):
            return
        if time.time() - os.path.getmtime(lautstaerke.MERKER) > 60:
            lautstaerke.zuruecksetzen()
    except Exception:
        pass


def _klangvorgaben():
    """Tempo und Lautstärke aus den Einstellungen holen.

    `length_scale` ist die Länge, nicht die Geschwindigkeit -- also der
    Kehrwert. Tempo 1.25 heisst length_scale 0.8, und Piper spricht schneller.
    Bei jedem Satz neu gelesen, damit ein verschobener Regler sofort wirkt,
    ohne dass irgendetwas neu startet."""
    try:
        from piper import SynthesisConfig
        import einstellungen
        tempo = max(0.5, min(2.0, float(einstellungen.hole('tempo') or 1.0)))
        # Zwei Ebenen, wie überall beim Feedback (Ramzi, 01.08.2026 nachts):
        # `lautstaerke` ist der Hauptregler für alles Hörbare, `laut_stimme` der
        # Anteil davon, der auf meine Stimme entfällt. Wer die Tafel oben ganz
        # herunterzieht, macht damit auch mich still -- genau das war der Wunsch.
        haupt = max(0.0, min(1.5, float(einstellungen.hole('lautstaerke') or 1.0)))
        anteil = max(0.0, float(einstellungen.hole('laut_stimme') or 100)) / 100.0
        laut = max(0.0, min(1.5, haupt * anteil))
        return SynthesisConfig(length_scale=1.0 / tempo, volume=laut)
    except Exception:
        return None


@contextlib.contextmanager
def _redeplatz(wartezeit=25.0):
    """Nur einer redet -- über Prozessgrenzen hinweg.

    Das Problem ist nicht ein Programm, sondern zwei: das Ohr (assistant.py)
    beantwortet einen Reflex, und gleichzeitig liest der Stop-Hook aus einem
    ganz anderen Prozess meine Chat-Antwort vor. Ein Schloss innerhalb eines
    Programms hilft dagegen nicht.

    Ein benannter Windows-Mutex schon: den kennen alle Prozesse unter demselben
    Namen, und Windows gibt ihn von selbst frei, wenn ein Prozess abstürzt --
    eine Sperrdatei bliebe liegen und ich wäre für immer stumm.

    Auf anderen Systemen passiert hier nichts; dort läuft die Sprachschicht
    ohnehin nicht (siehe project_noor_windows_macos im Gedächtnis).
    """
    if sys.platform != 'win32':
        yield
        return

    import ctypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    griff = k32.CreateMutexW(None, False, 'Global\\NoorSprichtGerade')
    if not griff:
        yield
        return
    try:
        # Wartet, statt sofort loszureden. Läuft die Wartezeit ab, wird trotzdem
        # gesprochen -- lieber einmal übereinander als eine verschluckte Antwort.
        k32.WaitForSingleObject(ctypes.c_void_p(griff), int(wartezeit * 1000))
        yield
    finally:
        k32.ReleaseMutex(ctypes.c_void_p(griff))
        k32.CloseHandle(ctypes.c_void_p(griff))


# --- Untertitel für das, was ich selbst sage -------------------------------
#
# Ramzis Wunsch vom 01.08.2026: er will meine gesprochene Antwort mitlesen
# können, ohne im Chat danach zu suchen -- und sehen, welches Wort gerade
# klingt.
#
# Der springende Punkt: bei MIR ist das ein leichtes Problem, im Gegensatz zu
# seinen eigenen Untertiteln. Ich kenne den Text vorher, und ich erzeuge den Ton
# selbst -- ich muss die Dauer also nicht aus dem Tempo ausrechnen, ich ZÄHLE
# die erzeugten Samples. Damit ist auch seine Sorge erledigt, ein verschobener
# Tempo-Regler könnte die Anzeige aus dem Tritt bringen: das Tempo steckt bereits
# im fertigen Ton.
def _anzeigen_einteilen(text):
    """Sätze zu Anzeigen bündeln -- Ramzis Hybrid aus Sätzen UND Länge.

    Sein Wunsch, wortgetreu: "normalerweise zwei Sätze, aber gleichzeitig
    abhängig von der Zeit -- wenn du einen langen Satz hast, der so viel Zeit
    braucht wie zwei, dann nimmst du nur den."

    Die Regel selbst liegt seit dem 01.08.2026 in untertitel.py, weil sie beide
    Seiten betrifft -- meine Sätze und seine. Zwei Fassungen würden
    auseinanderlaufen, und dann sähen seine Untertitel wieder anders aus als
    meine; genau das wollte er ja loswerden.

    `erste_kuerzer` ist der einzige Unterschied und gilt nur hier: bevor der
    erste Ton läuft, muss die erste Anzeige fertig erzeugt sein -- nur so steht
    ihre Dauer fest. Nachgemessen sind das 0,46 s bis zum ersten Wort statt der
    gewohnten 0,12 s; ein kurzer erster Satz drückt das, so weit es geht.
    """
    import untertitel
    return untertitel.einteilen(text, erste_kuerzer=True)


def _wortzeiten(text, dauer):
    """Die gemessene Dauer auf die Wörter verteilen.

    Piper liefert keine echten Wortzeiten -- dafür bräuchte es eine
    Zwangsausrichtung, ein eigenes Modell und deutlich mehr Rechenzeit. Also
    wird nach Länge verteilt, mit einem Aufschlag für Satzzeichen, weil an einem
    Komma oder Punkt hörbar Zeit vergeht.

    Das ist eine Näherung, und ich sage das auch so: auf einem langen Satz kann
    die Hervorhebung ein paar Zehntel verrutschen. Weil jede Anzeige neu bei
    null anfängt, sammelt sich der Fehler aber nicht auf.
    """
    worte = [w for w in text.split() if w]
    if not worte or dauer <= 0:
        return [{'w': w, 'ab': 0.0, 'd': 0.0} for w in worte]

    gewichte = []
    for w in worte:
        g = len(w) + 1.0
        if w[-1] in ',;:':
            g += 2.0
        elif w[-1] in '.!?…':
            g += 3.5
        gewichte.append(g)

    gesamt = sum(gewichte)
    ergebnis, laufend = [], 0.0
    for w, g in zip(worte, gewichte):
        d = dauer * g / gesamt
        ergebnis.append({'w': w, 'ab': round(laufend, 3), 'd': round(d, 3)})
        laufend += d
    return ergebnis


def _untertitel(text, worte, start, dauer):
    """Auf den Streifen legen, ohne daran scheitern zu können.

    `dauer` ist der Grund, warum Ramzis Untertitel-Regler für MEINE Sätze nicht
    mehr gilt: der Streifen weiß damit, wie lange diese Anzeige zu hören ist,
    und blendet sie nicht mehr mitten im Satz aus.
    """
    try:
        import untertitel
        untertitel.zeige(text, 'noor', worte, start, dauer)
    except Exception:
        pass
    # Und mitschreiben, DASS ich es gesagt habe. Der Streifen hält nur das
    # laufende Stück; der Echo-Schutz braucht die letzte Minute am Stück, weil
    # das Ohr erst nach einer Sprechpause auswertet -- ausführliche Begründung
    # in warteschlange.merke_gesagt().
    try:
        import warteschlange
        warteschlange.merke_gesagt(text)
    except Exception:
        pass


def _er_diktiert():
    """Hat er die Diktat-Taste gedrückt, während ich rede?

    Der einzige Grund, aus dem der SPRECHER von sich aus abbricht -- und zwar
    weil er der einzige eindeutige ist: die Aufnahme-Sperre entsteht durch
    einen Tastendruck, mein eigener Lautsprecher kann sie nicht auslösen.

    Über alles andere ("er hat mitten in meinem Satz übernommen") entscheidet
    das OHR und meldet es als Abbruch. Dort liegen die Beweise -- der gehörte
    Text und mein eigener Satz zum Vergleichen. Hier lag früher eine zweite,
    schlechter informierte Meinung darüber, und die hat mich mitten im Satz
    verstummen lassen, wenn mein eigenes Echo den Merker gesetzt hatte.
    """
    try:
        import warteschlange
        return warteschlange.qalam_nimmt_auf()
    except Exception:
        return False


def _er_hat_uebernommen():
    """Hat Ramzi den Platz in der Warteschlange genommen, WÄHREND ich rede?

    Beide Wege zählen, und dass der zweite jetzt dabei sein DARF, ist Ramzis
    Verdienst (01.08.2026). Er hat nicht lockergelassen: "während ich rede, ist
    das Sprechen blockiert -- da gibt's kein Abwürgen." Sein Bild war richtig,
    mein Einwand aber auch: das Ohr hört über sein Mikrofon meine eigene Stimme
    und hätte mich für ihn gehalten.

    Aufgelöst ist das jetzt im Ohr selbst: es vergibt den Platz nicht mehr
    blind, sondern vergleicht das Gehörte mit dem, was ich gerade sage --
    warteschlange.ist_mein_echo(). Damit bedeutet ramzi_redet() endlich das,
    was der Name sagt, und darf hier geprüft werden.

        Aufnahme-Sperre  -- er hat die Diktat-Taste gedrückt (sofort)
        ramzi_redet()    -- er spricht mich an, während ich rede (nach dem
                            nächsten Durchgang des Mitlauschers)
    """
    try:
        import warteschlange
        return warteschlange.ramzi_ist_dran()
    except Exception:
        return False


class Sprecher:
    """Spricht Text über die Lautsprecher. Ein Sprecher pro Prozess reicht.

    `sprich()` blockiert bis zum Ende. `sprich_im_hintergrund()` kehrt sofort
    zurück und lässt sich mit `stoppe()` unterbrechen -- das ist der Haken, an
    dem später das Unterbrechen-Dürfen (barge-in) hängt.
    """

    def __init__(self, stimme=STANDARD_STIMME):
        self.stimme_name = stimme
        self._stimme = None
        # EIN ZAEHLER, KEIN SCHALTER -- Ramzis Befund vom 07.08.2026: "die
        # Stopptaste funktioniert nicht, ich kann dich nicht unterbrechen."
        #
        # Vorher stand hier ein Event, und `stoppe()` setzte es, wartete auf
        # `self._thread` und loeschte es wieder. Das ging genau so lange gut,
        # wie jeder Satz aus `sprich_im_hintergrund()` kam. Seit dem
        # Briefkasten spricht aber ein FREMDER Faden -- `self._thread` ist dann
        # tot oder None, das Warten kehrt sofort zurueck, und das Loeschen
        # passierte, bevor der redende Faden den Schalter je gesehen hatte.
        # Der Stopp fiel lautlos aus.
        #
        # Ein Zaehler kann nicht zurueckgesetzt werden und braucht darum kein
        # Warten: jeder redende Faden merkt sich beim Start seinen Stand und
        # hoert auf, sobald der nicht mehr stimmt -- egal, aus welchem Faden
        # gestoppt wurde und wie viele gerade reden.
        self._abbruch = 0
        self._thread = None
        # Wie viele Aufrufer gerade in sprich() stehen -- siehe spricht_gerade().
        self._reden = 0

    @property
    def stimme(self):
        if self._stimme is None:
            self._stimme = _lade_stimme(self.stimme_name)
        return self._stimme

    def sprich(self, text):
        """Spricht den Text, Satz für Satz. Blockiert bis zum Ende.

        Gibt zurück, was daraus geworden ist -- die Sprech-Zentrale schreibt es
        so ins Protokoll, und "nochmal" holt daraus das Fehlende:

            'fertig'       ganz gesprochen
            'abgebrochen'  lief, wurde aber mitten im Satz gestoppt
            'ungesagt'     kam gar nicht erst heraus (keine Stimme geladen)
        """
        import sounddevice as sd
        # Was bei einem Abbruch NICHT mehr herauskam -- die Zentrale schreibt
        # genau das als ABGEBROCHEN ins Protokoll, und "nochmal" holt es.
        # Hier zurueckgesetzt, damit nie der Rest eines frueheren Satzes
        # stehen bleibt.
        self._nicht_gesagt = ''
        # Ein Zähler und kein Schalter: es kann mehr als einen Aufrufer geben
        # (Briefkasten, Reflex, Stop-Hook), und ein Schalter waere beim Ende des
        # ersten schon wieder aus, obwohl der zweite noch redet.
        self._reden += 1
        try:
            return self._sprich(text, sd)
        finally:
            self._reden -= 1

    def _sprich(self, text, sd):

        saetze = in_saetze(text)
        if not saetze:
            return 'fertig'

        # Der Stand, gegen den ab jetzt geprüft wird -- und zwar VOR dem
        # Warten, nicht erst vor dem ersten Ton: ein Stopp, während ich noch
        # darauf warte, dass Ramzi fertig wird, muss diesen Satz genauso
        # verwerfen wie einen laufenden.
        mein_stand = self._abbruch

        # ERST sein Platz in der Warteschlange, DANN meiner.
        #
        # Ramzis Idee, wortgetreu (31.07.2026): "wenn ich gerade am Sprechen
        # bin, bin ich in der Warteschlange, und wenn du dann etwas sagen
        # willst, packst du es in die Warteschlange und bist als nächstes
        # dran." Ausgelöst dadurch, dass dieser Hook einmal genau mitten in
        # seiner Beschreibung angefangen hat, meine vorherige Antwort
        # vorzulesen -- beide Stimmen übereinander.
        try:
            import warteschlange
            warteschlange.warte_bis_er_fertig_ist()
        except Exception:
            pass

        # Warten, bis niemand sonst von MIR spricht. Ohne das reden zwei
        # Prozesse gleichzeitig: das Ohr beantwortet einen Reflex, während der
        # Stop-Hook meine Chat-Antwort vorliest. Ramzi hat am 31.07.2026 beides
        # übereinander gehört und nicht mehr auseinanderhalten können, was
        # Antwort auf was war.
        with _redeplatz():
            # Musik leiser, solange ICH rede -- nicht nur, solange Ramzi redet.
            #
            # Sein Einwand vom 31.07.2026, und er ist zwingend: "wenn du redest,
            # dann soll es auch runtergehen, sonst redest du mit der Musik und
            # dann höre ich gar nichts."
            #
            # Hier und nicht im Assistenten, weil hier JEDES Sprechen durchkommt:
            # die Reflexe des Ohrs und der Sprech-Hook, der meine Chat-Antwort
            # vorliest -- und der ist ein eigener Prozess. Deshalb liegt der
            # Merker der alten Lautstärken in einer Datei, siehe lautstaerke.py.
            # Beides zusammen und über einen Zähler -- siehe buehne_an().
            # Angestoßen wird hier, abgewartet erst kurz vor dem ersten Ton
            # (_videos_abwarten), damit das Anhalten nichts kostet.
            buehne_an()
            # Welcher Motor spricht: XTTS (Ludvig) oder Piper.
            #
            # XTTS klingt deutlich menschlicher -- Ramzi hat es am 02.08.2026
            # ueber vier Hoerrunden ausgewaehlt. Es hat aber zwei Bedingungen,
            # die Piper nicht hat: es braucht 2,6 GB auf der Karte, und es
            # halluziniert bei ungeschicktem Text (siehe stimme_xtts.py und
            # noor/werkzeuge/stimme-regeln.md).
            #
            # Deshalb ist Piper kein Auslaufmodell, sondern der Rueckfall: ist
            # die Karte voll (Spiel, viele Fenster) oder geht beim Erzeugen
            # etwas schief, spricht Piper SOFORT, statt dass ich stumm bleibe.
            # Auf der CPU wuerde XTTS elf Sekunden je Satz brauchen -- das ist
            # ausdruecklich kein Rueckfall, lieber eine schlichtere Stimme
            # sofort als die schoene eine halbe Minute spaeter.
            motor = 'piper'
            try:
                import einstellungen
                gewuenscht = (einstellungen.hole('stimme_motor') or 'xtts')
            except Exception:
                gewuenscht = 'xtts'
            xtts_rate = None
            if gewuenscht == 'xtts':
                try:
                    import stimme_xtts
                    xtts_rate = stimme_xtts.RATE
                    if stimme_xtts.bereit():
                        motor = 'xtts'
                    else:
                        # LIEBER STUMM ALS MIT DER ALTEN STIMME.
                        #
                        # Ramzi am 02.08.2026, nachdem Piper zum wiederholten
                        # Mal eingesprungen ist: "ich weiss nicht, ob ich den
                        # wirklich als Rueckfall brauche. Wenn Ludvig gerade
                        # nicht da ist, dann einfach gar nicht sprechen."
                        # Er hat recht: ein Rueckfall, der ungefragt mit einer
                        # anderen Stimme redet, ist kein Netz, sondern ein
                        # Fehler, der sich als Funktion tarnt -- und er belegt
                        # nebenbei Speicher fuer etwas, das niemand hoeren will.
                        stimme_xtts.vorwaermen()
                        print('[Stimme] Ludvig noch nicht bereit -- dieser Satz '
                              'bleibt ungesprochen: %r' % (' '.join(saetze))[:60],
                              flush=True)
                        # Die Bühne wieder hergeben: hier wird nichts gesagt,
                        # und ein Video, das dafür stehen bleibt, wäre der
                        # ärgerlichste Fall von allen.
                        buehne_aus()
                        return 'ungesagt'
                except Exception:
                    motor = 'piper'

            print('[Stimme] Motor: %s' % motor, flush=True)
            # Messpunkte fuer "ich spreche in seine Aufnahme hinein".
            #
            # Der Verdacht steht in AUFGABEN.md: die Pruefung, ob Ramzi dran
            # ist, sitzt VOR dem Erzeugen -- und Erzeugen dauert bei XTTS
            # mehrere Sekunden. Faengt er in dieser Luecke an zu reden, merke
            # ich es zu spaet. Bevor ich daran etwas aendere, will ich die
            # Luecke in Zahlen sehen: wann wurde geprueft, wann ging der erste
            # Ton raus.
            _t_gefragt = time.time()
            stimme = None if motor == 'xtts' else self.stimme
            rate = xtts_rate if motor == 'xtts' else stimme.config.sample_rate
            klang = _klangvorgaben()

            # Anzeigen bilden und EINE im Voraus erzeugen.
            #
            # Warum vorausgeschaut wird: um das gerade gesprochene Wort
            # hervorheben zu können, muss die Dauer einer Anzeige feststehen,
            # BEVOR der erste Ton davon läuft -- und die steht erst fest, wenn
            # der Ton erzeugt ist. Würde ich jede Anzeige erst dann erzeugen,
            # wenn die vorige zu Ende ist, entstünde zwischen je zwei Anzeigen
            # eine hörbare Lücke. Also erzeugt ein Faden die nächste, während
            # die aktuelle noch läuft; Piper ist um ein Vielfaches schneller als
            # Echtzeit, der Faden bleibt also mühelos vorn.
            #
            # Die Warteschlange fasst genau eins: mehr im Voraus zu erzeugen
            # brächte nichts und würde beim Abbrechen nur weggeworfen.
            import numpy as np

            anzeigen = _anzeigen_einteilen(' '.join(saetze))
            fertig = queue.Queue(maxsize=1)
            schluss = threading.Event()

            def _abliefern(stueck, ton):
                """Ein fertiges Paar in die Warteschlange, bis Platz ist."""
                while not schluss.is_set():
                    try:
                        fertig.put((stueck, ton), timeout=0.2)
                        return True
                    except queue.Full:
                        continue
                return False

            def _erzeuge_piper():
                for stueck in anzeigen:
                    if schluss.is_set():
                        break
                    try:
                        teile = [c.audio_int16_array
                                 for c in stimme.synthesize(stueck, syn_config=klang)]
                        ton = np.concatenate(teile) if teile else None
                    except Exception:
                        ton = None
                    if not _abliefern(stueck, ton):
                        break

            def _erzeuge():
                # XTTS bekommt den GANZEN Text in einem Aufruf -- Zerlegen ist
                # genau die Ursache des Kauderwelschs (gemessen, 240 Proben).
                # Die Anzeigen bekommen daraus Scheiben, damit die Untertitel
                # weiter mitlaufen. Geliefert wird stueckweise, deshalb faengt
                # der Ton nach ~0,7 s an statt nach 4-5 s.
                if motor == 'xtts':
                    etwas = False
                    try:
                        laut = getattr(klang, 'volume', 1.0) if klang else 1.0
                        # Das Tempo kommt aus RAMZIS Regler, nicht aus einer
                        # Vorgabe von mir. Ich hatte hier 1,15 fest verdrahtet,
                        # weil ihm Ludvig zu langsam war -- damit haette er
                        # gegen meinen Wert anschrauben muessen. Zwei Quellen
                        # fuer dieselbe Sache sind genau der Fehler, den er
                        # schon beim Feedback-Bereich abgestellt hat.
                        try:
                            import einstellungen
                            tempo = float(einstellungen.hole('tempo') or 1.0)
                        except Exception:
                            tempo = 1.0
                        for stueck, ton in stimme_xtts.stuecke(
                                ' '.join(saetze), anzeigen,
                                tempo=max(0.5, min(2.0, tempo))):
                            if schluss.is_set():
                                break
                            if laut != 1.0:
                                ton = (ton.astype(np.float32) * laut) \
                                    .clip(-32768, 32767).astype(np.int16)
                            etwas = True
                            if not _abliefern(stueck, ton):
                                break
                        _abliefern(None, None)
                        return
                    except Exception as e:
                        if etwas:
                            # Mittendrin abgebrochen: NICHT von vorn mit Piper
                            # anfangen -- Ramzi hoerte sonst den halben Satz
                            # zweimal, und die Abtastrate des offenen Stroms
                            # passt ohnehin nicht mehr.
                            print('[Stimme] XTTS brach mitten im Satz ab: %s' % e,
                                  flush=True)
                            _abliefern(None, None)
                            return
                        # Nicht stumm bleiben: Piper springt ein. Der Fehler
                        # gehoert ins Protokoll, sonst verschwindet ein
                        # kaputtes Modell lautlos hinter einer Stimme, die
                        # noch funktioniert.
                        # Piper ist raus (Ramzis Entscheidung: lieber stumm
                        # als mit der alten Stimme). Also nur melden.
                        print('[Stimme] XTTS ausgefallen, nichts gesprochen: %s'
                              % e, flush=True)
                        _abliefern(None, None)
                        return
                _erzeuge_piper()
                # Das Schlusszeichen MUSS ankommen, sonst wartet der Abspieler
                # ewig auf eine Anzeige, die nie kommt. Also so lange anbieten,
                # bis Platz da ist -- ein einmaliger Versuch mit Zeitausfall hat
                # genau diesen Hänger erzeugt (gemessen: 62 s statt 18 s).
                while not schluss.is_set():
                    try:
                        fertig.put((None, None), timeout=0.2)
                        break
                    except queue.Full:
                        continue

            erzeuger = threading.Thread(target=_erzeuge, daemon=True)
            erzeuger.start()

            # JETZT muss das Video stehen -- der nächste Schritt macht Ton.
            _videos_abwarten()

            # Ein Strom für alle Sätze: sonst knackt es bei jedem Satzwechsel.
            strom = sd.OutputStream(samplerate=rate, channels=1, dtype='int16')
            strom.start()
            # In kleine Stücke schreiben statt eine Anzeige am Stück.
            #
            # `strom.write()` kehrt erst zurück, wenn alles abgegeben ist, und
            # eine Anzeige sind zwei Sätze -- mehrere Sekunden, in denen nichts
            # dazwischenkommen konnte. Genau das steckt hinter Ramzis "ich kann
            # dich nicht unterbrechen": geprüft wurde nur an der Satzgrenze.
            # 0,15 s ist klein genug, dass er es als "sofort still" erlebt, und
            # groß genug, dass die Prüfung selbst nichts kostet (zwei Blicke auf
            # Dateizeiten).
            BLOCK = max(1, int(rate * 0.15))
            abgebrochen = False

            # WIE WEIT BIN ICH GEKOMMEN?
            #
            # Ramzis Befund vom 08.08.2026 bei der Abnahme: "nochmal" hat ihm
            # alles von vorn vorgelesen, auch das, was er schon gehoert hatte.
            # Der Grund stand im Protokoll: jeder Teilsatz wird beim Sprechen
            # einzeln als GESAGT vermerkt, beim Abbruch schrieb die Zentrale
            # aber den GANZEN Auftragstext noch einmal als ABGEBROCHEN dazu.
            # Damit stand das Gehoerte doppelt drin, und "nochmal" nimmt genau
            # die Nicht-GESAGT-Zeilen.
            #
            # Der alte Kommentar sagte, man wisse nicht, wo abgeschnitten
            # wurde. Das stimmt fuer das einzelne WORT -- fuer den SATZ nicht:
            # es ist der, dessen Anzeige zuletzt aufgelegt wurde. Diese Koernung
            # hat Ramzi selbst vorgeschlagen ("dann liest du einfach den ganzen
            # Absatz nochmal von vorne"), und sie ist ehrlich, weil sie nichts
            # raet.
            ganzer = ' '.join(saetze)
            gelesen_bis = 0          # alles davor ist sicher heraus
            laufend_ab = None        # Beginn der Anzeige, die gerade laeuft
            laufend_len = 0

            def _anzeige_beginnt(anzeige):
                """Merken, wo im Gesamttext die neue Anzeige anfaengt."""
                nonlocal gelesen_bis, laufend_ab, laufend_len
                if laufend_ab is not None:
                    gelesen_bis = laufend_ab + laufend_len
                wo = ganzer.find(anzeige, gelesen_bis)
                laufend_ab = gelesen_bis if wo < 0 else wo
                laufend_len = len(anzeige)

            def _abspielen(ton):
                """Ein Tonstück abgeben. False heißt: hier wurde abgebrochen."""
                for i in range(0, len(ton), BLOCK):
                    if self._abbruch != mein_stand or _er_diktiert():
                        return False
                    strom.write(ton[i:i + BLOCK])
                return True

            try:
                while True:
                    if self._abbruch != mein_stand:
                        abgebrochen = True
                        break
                    # Und zwischen JEDEM Satz noch einmal nachsehen.
                    #
                    # Ramzi am 31.07.2026, nachdem ich mitten in seine Nachricht
                    # gesprochen habe: "das mit der Warteschlange funktioniert
                    # nicht so gut -- ich rede gerade und du hast trotzdem mit
                    # reingesprochen." Er hat recht, und die Lücke ist genau
                    # diese: die Prüfung oben lief EINMAL, bevor das erste Wort
                    # kam. Fängt er danach an, hört mich niemand mehr auf. Je
                    # länger ich rede, desto größer wird dieses Fenster -- und
                    # ich sollte an dem Abend länger reden, nicht kürzer.
                    #
                    # Der Satz ist die richtige Körnung: ich höre an einer
                    # natürlichen Stelle auf, nicht mitten im Wort.
                    if _er_diktiert():
                        abgebrochen = True
                        break

                    text, ton = fertig.get()
                    if text is None:
                        break
                    if ton is None or len(ton) == 0:
                        continue

                    # Die Dauer wird GEMESSEN, nicht geschätzt: so viele Samples
                    # bei dieser Abtastrate sind genau so viele Sekunden.
                    dauer = len(ton) / float(rate)
                    if _t_gefragt:
                        print('[Stimme] erster Ton %.1f s nach der Pruefung'
                              % (time.time() - _t_gefragt), flush=True)
                        _t_gefragt = None
                    if not text:
                        # Ein leerer Anzeigetext heißt: derselbe Untertitel
                        # bleibt stehen. XTTS liefert viele kleine Tonstücke zu
                        # EINER Anzeige -- die je Stück neu zu setzen würde den
                        # Streifen flackern lassen.
                        if not _abspielen(ton):
                            abgebrochen = True
                            break
                        continue
                    _anzeige_beginnt(text)
                    if motor == 'xtts':
                        # Keine Wort-Hervorhebung: sie wird aus der Zeichenzahl
                        # geschätzt, und XTTS dehnt und pausiert zu
                        # unterschiedlich, als dass das passte. Ramzi hat die
                        # falschen Sprünge sofort gesehen. Die geschätzte Dauer
                        # dient nur noch dazu, dass der Streifen nicht mitten
                        # im Satz ausblendet.
                        _untertitel(text, [], time.time(),
                                    len(text) * stimme_xtts.JE_ZEICHEN)
                    else:
                        _untertitel(text, _wortzeiten(text, dauer),
                                    time.time(), dauer)
                    if not _abspielen(ton):
                        abgebrochen = True
                        break
            finally:
                schluss.set()
                # Den Platz freimachen, damit der Erzeuger nicht ewig wartend
                # im Speicher hängt, wenn hier abgebrochen wurde.
                try:
                    fertig.get_nowait()
                except Exception:
                    pass
                strom.stop()
                strom.close()
                buehne_aus()

            # Was ihm fehlt: ab dem Satz, der gerade lief. Lief keiner mehr
            # (Abbruch genau zwischen zwei Anzeigen), dann ab der naechsten
            # Stelle -- dann hat er alles Bisherige wirklich gehoert.
            if abgebrochen:
                ab = laufend_ab if laufend_ab is not None else gelesen_bis
                self._nicht_gesagt = ganzer[ab:].strip()
        return 'abgebrochen' if abgebrochen else 'fertig'

    def sprich_im_hintergrund(self, text):
        """Startet das Sprechen und kehrt sofort zurück."""
        self.stoppe()
        self._thread = threading.Thread(target=self.sprich, args=(text,), daemon=True)
        self._thread.start()
        return self._thread

    def stoppe(self):
        """Bricht das Sprechen ab -- aus JEDEM Faden, nicht nur aus meinem.

        Nur den Zähler hochsetzen und kurz nachsehen, ob es gewirkt hat. Wer
        gerade redet, prüft alle 0,15 s -- ein Warten von zwei Sekunden auf
        einen bestimmten Faden wie früher braucht es dafür nicht, und es wäre
        auch falsch: gestoppt wird oft aus der Tastenwache heraus, und die darf
        Ramzis Tastatur nicht blockieren.
        """
        self._abbruch += 1
        ende = time.time() + 1.5
        while self.spricht_gerade() and time.time() < ende:
            time.sleep(0.02)

    def spricht_gerade(self):
        """Rede ich gerade -- egal aus welchem Faden.

        Vorher stand hier nur `self._thread.is_alive()`, also der Faden von
        `sprich_im_hintergrund()`. Das ging, solange jeder Satz so anfing.
        Seit dem Briefkasten (sprechpost.py) spricht der Assistent aber aus
        SEINEM eigenen Faden -- und dann war die Antwort falsch: nein.

        Was Ramzi davon merkte (02.08.2026): "du dämpfst Spotify kurz ab und
        machst es dann wieder laut, obwohl du noch redest." Die Wächterin im
        Assistenten fragt genau hier nach, sieht "spricht nicht" und stellt
        die Lautstärke 0,4 s später zurück -- mitten in meinem Satz.

        Deshalb zählt jetzt ein Zähler statt eines Fadens: er steigt am Anfang
        jedes `sprich()` und fällt am Ende, egal wer es aufgerufen hat.
        """
        return self._reden > 0 or bool(self._thread and self._thread.is_alive())


def nach_wav(text, ziel, stimme=STANDARD_STIMME, sprecher=None, klang=None):
    """Text als WAV-Datei ablegen, statt ihn zu sprechen (zum Vergleichen).

    `sprecher` ist die Nummer bei Mehrsprecher-Modellen -- die Gefühls-Stimme
    hat acht (neutral, belustigt, geflüstert ...), das MLS-Modell 236. Ohne
    Angabe gilt die Vorgabe des Modells.

    `klang` überschreibt Tempo und Lautstärke. Das ist kein Beiwerk, sondern
    Voraussetzung für einen fairen Vergleich: ohne diesen Weg holt sich jede
    einzelne Probe die Werte FRISCH aus den Einstellungen -- verschiebt Ramzi
    währenddessen einen Regler, sind die Kandidaten unterschiedlich laut, und
    die lautere Stimme gewinnt jeden Blindtest. Am 01.08.2026 genau so passiert,
    während er die neuen Sprachbefehle ausprobierte."""
    import wave
    s = _lade_stimme(stimme)
    if klang is None:
        klang = _klangvorgaben()      # dieselben Vorgaben wie beim Sprechen
    if sprecher is not None and klang is not None:
        import dataclasses
        klang = dataclasses.replace(klang, speaker_id=sprecher)
    with wave.open(ziel, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(s.config.sample_rate)
        for satz in in_saetze(text) or [text]:
            for stueck in s.synthesize(satz, syn_config=klang):
                f.writeframes(stueck.audio_int16_bytes)
    return ziel


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description='Qalam-Sprachausgabe (Piper, lokal)')
    p.add_argument('text', nargs='?', default='-', help='Text, oder "-" für stdin')
    p.add_argument('--stimme', default=STANDARD_STIMME)
    p.add_argument('--nach-wav', default=None, help='statt sprechen: WAV schreiben')
    # Über eine Datei, weil Aufrufer ohne stdin existieren: ein losgelöst
    # gestarteter Prozess (Start-Process) hat keine Pipe, und der Text enthält
    # Anführungszeichen, Umlaute und Gedankenstriche, an denen die Kommandozeile
    # zwischen PowerShell, Windows und argparse zerbricht.
    p.add_argument('--datei', default=None, help='Text aus dieser UTF-8-Datei lesen')
    args = p.parse_args(argv)

    if args.datei:
        with open(args.datei, encoding='utf-8') as f:
            text = f.read()
    elif args.text == '-':
        text = sys.stdin.read()
    else:
        text = args.text
    text = text.strip()
    if not text:
        return 0

    if args.nach_wav:
        print(nach_wav(text, args.nach_wav, args.stimme))
        return 0

    # Schlafe ich? Dann kommt hier gar nichts raus.
    #
    # Ramzi am 01.08.2026: "jedes Mal, wenn du das Dashboard neu startest, du
    # schlaefst zwar, aber du sagst trotzdem, wie du dich gerade anhoerst. Dass
    # du dich kurz vorstellst, ist ja richtig -- aber das sollst du nur machen,
    # wenn du wach bist."
    #
    # Der Vorsteller ist der Probe-Knopf der Tafel, der beim Start einmal
    # ausloest. Die Sperre gehoert trotzdem NICHT dorthin, sondern hierher:
    # das hier ist die eine Tuer, durch die jeder fremde Sprecher geht -- die
    # Tafel, die Sprech-Hooks, meine eigenen Skripte. Eine Regel an der Tuer
    # ist eine Regel; dieselbe Regel in jedem Aufrufer ist eine Verabredung,
    # die der naechste Aufrufer nicht kennt.
    try:
        import warteschlange
        if not warteschlange.darf_sprechen():
            return 0
    except Exception:
        pass

    # Erst anstellen, dann reden -- auch von aussen.
    #
    # Ramzis Regel von Anfang an: wer redet, haelt den Platz. Der Assistent
    # haelt sich daran, dieser Weg hier bisher nicht -- und das sind genau die
    # Aufrufe, die aus dem Nichts kommen: der Probe-Knopf auf der Tafel, meine
    # eigenen Skripte. Am 01.08.2026 bin ich damit mitten in einen langen Satz
    # von ihm gefahren, den er nicht wiederholen wollte. Ein Aufruf, der nicht
    # wartet, ist kein Werkzeug, sondern ein Dazwischenreden.
    try:
        import warteschlange
        warteschlange.warte_bis_er_fertig_ist()
    except Exception:
        pass
    Sprecher(args.stimme).sprich(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
