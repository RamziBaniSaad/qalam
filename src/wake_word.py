"""Weckwort — Qalam hört zu, ohne dass eine Taste gedrückt wird.

WARUM NICHT openWakeWord (Stand 30.07.2026):
    openWakeWord ist der übliche Weg, aber es bringt nur englische Modelle mit
    ("alexa", "hey jarvis"). Für "Noor" müsste erst ein eigenes Modell trainiert
    werden -- machbar, aber eine eigene Baustelle. Bis dahin geht es einfacher,
    weil wir alles Nötige schon im Haus haben:

        webrtcvad merkt, DASS jemand spricht   (CPU, ~0 Last, hat Qalam schon)
        faster-whisper hört, WAS gesagt wurde  (winziges Modell, auf der CPU)

    Whisper läuft nur an, wenn wirklich gesprochen wurde -- nicht dauernd. Das
    kostet kein VRAM (bewusst CPU, damit Ramzis 8-GB-Karte frei bleibt) und
    versteht Deutsch ab der ersten Sekunde, ohne Training.

    Wenn "Noor" später als echtes Weckwortmodell trainiert ist, kann es hier
    davorgehängt werden; der Rest bleibt wie er ist.
"""
import collections
import os
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

RATE = 16000
FRAME_MS = 30
FRAME_LEN = int(RATE * FRAME_MS / 1000)
# Wie viel Ton der Mitlauscher jeweils ansieht: drei Sekunden reichen, um den
# Namen zu finden, und halten seine Rechenzeit konstant.
BLICK_FRAMES = int(3.0 * 1000 / FRAME_MS)
# Ab wie viel Ton der Mitlauscher überhaupt hinsieht. Nachgemessen mit
# `werkzeuge_ohr_messen.py`: ein alleinstehendes "Noor" sind 0,66 s -- die
# Schwelle muss deutlich darunter liegen, sonst wird der häufigste Ruf
# überhaupt nie angesehen.
MINDEST_FRAMES = int(0.35 * 1000 / FRAME_MS)
# Wie viel nachlaufende Stille noch in den Ausschnitt des Mitlauschers wandert.
# Ohne sie sieht er nur die stimmhaften Bilder und damit nie das Ende einer
# Äußerung mit seinem natürlichen Ausklang.
NACHLAUF_FRAMES = int(0.5 * 1000 / FRAME_MS)
# Ab welcher Unsicherheit ein Satz des schnellen Modells als erfunden gilt.
# Nachgemessen, siehe _hoer_kurz().
ERFINDUNGS_SCHWELLE = 0.35
# Wie viele Kerne die beiden Modelle jeweils nehmen dürfen.
#
# Der Rechner hat sechs. Ohne Deckel nimmt sich JEDES Modell alle sechs, und
# dann rechnen sie gegeneinander statt nebeneinander: im Protokoll vom
# 31.07.2026 brauchte das genaue Modell für 15 s Ton normalerweise 1,3-3 s,
# in den schlechtesten Fällen aber 18-24 s -- immer dann, wenn lange
# durchgesprochen wurde und der Mitlauscher parallel dauerlief.
#
# Beide bekommen vier, nicht drei. Sie laufen fast nie gleichzeitig: das genaue
# Modell fängt erst nach vier Sekunden Stille an, und dann hat der Mitlauscher
# längst aufgehört (Name gefunden, oder die Äußerung ist vorbei). Für die kurze
# Überschneidung ein Drittel Tempo beim Aufwachen zu verschenken wäre der
# falsche Tausch -- die Zeit bis zum Ton ist das, was Ramzi spürt.
KERNE_FLINK = 4
KERNE_GENAU = 4

# Wie oft der Mitlauscher in EINER Äußerung nach dem Namen sucht, bevor er
# aufgibt.
#
# Ohne diese Grenze läuft er bei fremder Sprache im Raum (Fernseher, Telefonat)
# endlos weiter und rechnet gegen das genaue Modell. Wer nach einigen Anläufen
# den Namen nicht gesagt hat, ruft nicht -- dann ist Schweigen die richtige
# Antwort, und beim nächsten Redeansatz wird neu gesucht.
BLICKE_JE_AEUSSERUNG = 6

# Eine KURZE Äußerung braucht keine lange Nachdenkpause.
#
# Die Stille-Schwelle steht auf vier Sekunden, und das ist richtig: Ramzi denkt
# mitten im Satz, und kürzere Werte haben ihn früher mitten im Reden
# abgeschnitten. Für einen Ruf, der nur aus seinem Namen besteht, ist dieselbe
# Schwelle aber absurd -- "Noor" sind 0,66 Sekunden, da ist nichts unfertig,
# was noch kommen könnte. Und weil vorher immer die vier Sekunden abgewartet
# wurden, kam der Ton auf einen einzelnen Ruf erst nach fünf bis acht Sekunden.
# Ramzi am 31.07.2026: "die Verzögerung von 8 Sekunden kann ich nicht
# akzeptieren."
#
# Also zwei Schwellen: wer weniger als KURZ_SPRACH_FRAMES gesprochen hat, ist
# nach KURZE_STILLE_FRAMES fertig. Wer länger geredet hat, bekommt seine vollen
# vier Sekunden Denkpause -- daran ändert sich nichts.
KURZ_SPRACH_FRAMES = int(1.5 * 1000 / FRAME_MS)
KURZE_STILLE_FRAMES = int(0.8 * 1000 / FRAME_MS)

# Ab wann etwas ueberhaupt ein Wort sein kann.
#
# Der Stimmenmelder steht auf der empfindlichsten Stufe und schlaegt auch bei
# einem Huesteln, einem Tastenklick oder einem Gerausch von draussen an. Solange
# jedes Segment vier Sekunden Stille abwarten musste, fiel das nicht auf -- was
# danach kam, wuchs einfach mit hinein. Mit der kurzen Schwelle wird aus jedem
# Gerausch ein eigener Auftrag: im Protokoll vom 31.07.2026 ueber sechzig
# Einträge "0,9 s Ton -> 1,3 s Rechenzeit", alle ergebnislos. Und weil das
# genaue Modell dabei dauernd beschaeftigt war, standen daneben Ausreisser von
# 9,7 s und 25,8 s -- die Wartezeit, die Ramzi spuert.
#
# "Noor" sind 0,66 Sekunden. Ein Drittel davon ist eine sichere Untergrenze:
# darunter ist es kein Ruf, und dann lohnt sich das Rechnen nicht.
MINDEST_SPRACH_FRAMES = int(0.35 * 1000 / FRAME_MS)

# Wie Whisper "Noor" verhören kann -- zweigeteilt, und das ist der Kern.
#
# "nur" ist eines der häufigsten deutschen Wörter. Solange es überall im Satz
# als Weckwort galt, hat mich jedes Video geweckt: am 31.07.2026 hat Ramzis
# TikTok mir mitten in der Nacht einen zusammenhanglosen Satz über
# "legislative, judicial or executive" geschickt. Das ist nicht nur lästig --
# jeder Fehlstart schickt eine Nachricht an mich und kostet ihn Nutzungslimit.
#
# Ramzis Entscheidung dazu (31.07.2026): die eindeutigen Schreibweisen zählen
# überall, die zweifelhaften nur am ANFANG. Wer ruft, fängt mit dem Namen an;
# wer "ich habe nur kurz" sagt, hat ihn mitten im Satz. Das trennt beides
# sauber, ohne einen echten Ruf zu verlieren.
#
# Der Preis, bewusst in Kauf genommen: sagt er mitten im Satz "und Noor, mach
# mal ..." und Whisper schreibt es als "nur", geht dieser Ruf verloren.
_EINDEUTIG = r'(?:noor|nour|nuur|nuor|nuhr|noah|nura)'
_ZWEIFELHAFT = r'(?:nur|nor)'
WECKWORT = re.compile(
    rf'(?:^[\s,.!?"\']*{_ZWEIFELHAFT}\b|\b{_EINDEUTIG}\b)',
    re.IGNORECASE
)

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPERRE = os.path.join(PROJEKT, '.aufnahme.lock')


# --------------------------------------------------------------------------
# Sperre: solange Qalam selbst aufnimmt, ist das Weckwort taub.
#
# Ramzi sagt meinen Namen ständig, während er mir etwas diktiert. Ohne diese
# Sperre würde ich mitten in seinem Diktat aufwachen -- genau das soll nicht
# passieren.
# --------------------------------------------------------------------------
def _leiser(an):
    """Musik dämpfen bzw. zurückstellen, ohne daran scheitern zu können.

    Hier eingehängt und nicht im Diktat selbst, weil diese beiden Funktionen der
    eine Punkt sind, durch den JEDE Aufnahme geht -- das Diktat und die
    Statusanzeige rufen beide hier durch. Ein Ort, an dem es stimmt, statt
    zweier, die auseinanderlaufen."""
    try:
        import lautstaerke
        lautstaerke.daempfen() if an else lautstaerke.zuruecksetzen()
    except Exception:
        pass


def aufnahme_beginnt():
    try:
        with open(SPERRE, 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass
    _leiser(True)


def aufnahme_endet():
    try:
        os.remove(SPERRE)
    except OSError:
        pass
    _leiser(False)


def qalam_nimmt_auf():
    if not os.path.exists(SPERRE):
        return False
    # Sicherheitsnetz: bleibt die Sperre nach einem Absturz liegen, taut sie
    # nach 15 Minuten von selbst auf, statt das Weckwort dauerhaft zu töten.
    try:
        if time.time() - os.path.getmtime(SPERRE) > 900:
            aufnahme_endet()
            return False
    except OSError:
        return False
    return True


class Weckwort:
    """Hört dauerhaft mit und ruft `beim_wecken(text)`, wenn der Name fällt.

    `text` ist der ganze erkannte Satz, nicht nur das Weckwort -- damit
    "Noor, mach Spotify an" in einem Rutsch funktioniert und man nicht erst
    gerufen wird und dann nochmal reden muss.
    """

    def __init__(self, beim_wecken, modell='small', geraet=None,
                 aggressivitaet=3, max_sekunden=15.0, stille_ms=None,
                 beim_erkennen=None, beim_mitschreiben=None, flink_modell='small',
                 spricht_gerade=None, ist_kurzbefehl=None):
        self.beim_wecken = beim_wecken
        # Darf gefragt werden, ob der laufende Satz ein kurzer Befehl ist.
        #
        # Damit löst sich der Zielkonflikt, an dem Ramzi am 31.07.2026 hängen
        # geblieben ist: die vier Sekunden Stille braucht er, um mitten im Satz
        # denken zu können -- aber bei "Noor, wie spät ist es" kommt nichts
        # mehr, und dann sind vier Sekunden Warten auf nichts einfach nur
        # langsam. Seine Worte: "wenn ich erst nach 10 Sekunden eine Antwort
        # bekomme, kann ich auch selber auf die Uhr gucken."
        #
        # Die Auskunft kostet nichts: der Mitlauscher hat den Satz ohnehin
        # schon gehört, während gesprochen wurde. Er fragt einfach nach.
        self.ist_kurzbefehl = ist_kurzbefehl
        self._kurz_erwartet = False
        # Solange ICH spreche, ist das Ohr taub -- sonst hört das Mikrofon
        # meine eigene Stimme aus den Lautsprechern mit, das flinke Modell
        # verhört sich daran zu Zufallstext, und der landet als "Befehl" bei
        # mir selbst. Ramzi hat das am 31.07.2026 live erlebt: "du hörst,
        # was du geschrieben hast, und schickst das rüber". Derselbe
        # Mechanismus wie qalam_nimmt_auf(), nur für die eigene Stimme statt
        # für ein laufendes Diktat.
        self._spricht_gerade = spricht_gerade or (lambda: False)
        # Wird gerufen, SOBALD der Name im laufenden Satz auftaucht -- lange
        # bevor der Satz fertig ist. Dafür ist der Ton da.
        self.beim_erkennen = beim_erkennen
        # Wird mit dem vorläufigen Text gerufen, während noch gesprochen wird.
        self.beim_mitschreiben = beim_mitschreiben

        self.modell_name = modell
        self.flink_name = flink_modell
        self.geraet = geraet
        self.vad = webrtcvad.Vad(aggressivitaet)
        self.max_frames = int(max_sekunden * 1000 / FRAME_MS)
        self._stille_ms = stille_ms
        self._modell = None
        self._flink = None
        self._stop = threading.Event()
        self._thread = None
        self._schloss = threading.Lock()
        self._laufend = None
        self._auftraege = queue.Queue()
        # Rechnet das genaue Modell gerade? Dann hält der Mitlauscher still.
        #
        # Die beiden teilen sich sechs Kerne, und wenn sie gleichzeitig rechnen,
        # verlieren beide. Im Protokoll vom 31.07.2026 stand dafür ein
        # eindeutiger Beweis: 15 s Ton brauchten normalerweise 2 s, in der
        # Überschneidung aber 31 s. Der Vorrang ist klar -- das genaue Modell
        # hält Ramzis fertigen Satz in der Hand, der Mitlauscher sucht nur
        # nach dem Namen und kann das eine Runde später tun.
        self._arbeiter_rechnet = threading.Event()
        self.schlaeft = False   # per "Noor, schlaf" abschaltbar
        # Bis wann ein Satz OHNE Weckwort noch als Auftrag gilt. Ramzi sagt oft
        # erst nur den Namen, wartet auf das Zeichen und redet dann weiter --
        # dieser zweite Satz enthält den Namen naturgemäß nicht mehr.
        self.folge_bis = 0.0

    @property
    def stille_frames(self):
        """Wie lange Schweigen einen Satz beendet -- aus den Einstellungen.

        Stand bis 31.07.2026 fest auf 600 ms. Ramzi konnte damit keinen Satz zu
        Ende sprechen: jede Denkpause hat mitten im Satz abgeschickt. Jetzt
        einstellbar und deutlich länger."""
        ms = self._stille_ms
        if ms is None:
            try:
                from einstellungen import hole
                ms = hole('stille_ms')
            except Exception:
                ms = 1600
        return int(ms / FRAME_MS)

    @property
    def modell(self):
        """Das genaue Modell für den fertigen Satz. Bewusst auf der CPU:
        int8, kein VRAM -- die Karte bleibt fürs Diktat und fürs Zocken frei."""
        if self._modell is None:
            from faster_whisper import WhisperModel
            self._modell = WhisperModel(self.modell_name, device='cpu', compute_type='int8',
                                        cpu_threads=KERNE_GENAU)
        return self._modell

    @property
    def flink(self):
        """Das Modell für den Blick MITTENDRIN -- nur zum Aufwachen.

        Der Zielkonflikt, den es auflöst: eine lange Redepause braucht eine
        lange Stille-Schwelle (4 s), sonst kann Ramzi keinen Satz zu Ende
        sprechen. Aber dann käme das Zeichen "ich höre dich" erst über fünf
        Sekunden nachdem er seinen Namen gesagt hat. Also wird schon WÄHREND
        des Sprechens mitgehört, immer nur auf den letzten Sekunden.

        WARUM HIER `small` STEHT UND NICHT `base` -- nachgemessen am 31.07.2026,
        und der Grund ist wichtig genug, ihn aufzuschreiben:

        `base` ist auf gut verständlichen Sätzen dreimal schneller (0,6 s
        gegen 1,4 s) und wäre die naheliegende Wahl. Auf dem Fall, um den es
        geht, versagt es aber vollständig: für einen kurzen, alleinstehenden
        Ruf "Noor" gibt es entweder gar keinen Text zurück oder erfundenen --
        und es braucht dafür 2,4 bis 4,0 s, weil unklarer Ton das Modell in
        lange Dekodierschleifen treibt. Es ist also gerade dort langsam UND
        blind, wo es gebraucht wird, und blockiert in dieser Zeit die Kerne,
        die das genaue Modell zum Wecken braucht. Ramzi hat genau das gemerkt:
        die Wartezeit bis zum Ton wurde dadurch länger, nicht kürzer.

        `small` hört den Namen wirklich (im Protokoll bewiesen) und braucht
        dafür 1,4 s. Es ist ein zweites, eigenes Exemplar mit weniger Kernen --
        nicht dasselbe wie `modell`, weil faster-whisper ein Exemplar nicht
        gleichzeitig aus zwei Fäden bedienen kann."""
        if self._flink is None:
            from faster_whisper import WhisperModel
            self._flink = WhisperModel(self.flink_name, device='cpu', compute_type='int8',
                                       cpu_threads=KERNE_FLINK)
        return self._flink

    # ----------------------------------------------------------------------
    def starte(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._schleife, daemon=True)
        self._thread.start()

    def stoppe(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def laeuft(self):
        return bool(self._thread and self._thread.is_alive())

    # ----------------------------------------------------------------------
    def _schleife(self):
        """Nur zuhören und einsammeln -- NIEMALS hier etwas ausrechnen.

        Der Fehler, der das am 31.07.2026 einmal komplett zerstört hat: Die
        Erkennung lief in genau dieser Schleife. Während transkribiert wurde,
        hat niemand mehr Ton vom Mikrofon abgeholt, der Eingangspuffer lief
        über, und Ramzi bekam nach zwanzig Sekunden alles auf einmal und
        verstümmelt. Er hat es sofort gemerkt: "das ist komplett kaputt".

        Merksatz: eine Aufnahmeschleife darf nur lesen. Alles, was Zeit kostet,
        gehört in einen eigenen Faden.
        """
        _ = self.flink   # das schnelle zuerst -- es wird als Erstes gebraucht
        _ = self.modell

        self._auftraege = queue.Queue()
        self._laufend = None
        threading.Thread(target=self._arbeiter, daemon=True).start()
        threading.Thread(target=self._mitlauscher, daemon=True).start()

        puffer = collections.deque()
        stille = 0
        sprach = 0            # wie viele Bilder davon wirklich Sprache waren
        in_sprache = False

        def abgeben(endgueltig, gesprochene_bilder=None):
            """Segment an den Arbeiter geben und neu anfangen.

            `endgueltig` unterscheidet ZWEI ganz verschiedene Gründe, warum ein
            Segment endet:

              * echte Stille (endgueltig=True)  -- Ramzi ist wirklich fertig
              * die Längenbegrenzung (endgueltig=False) -- er redet noch,
                das Segment wurde nur aus technischen Gründen zerschnitten

            Der Unterschied ist entscheidend. Ohne ihn wurde am 31.07.2026 eine
            zwei Minuten lange, durchgehende Äußerung in 15-Sekunden-Stücke
            gehackt, und JEDES Stück einzeln als fertiger Befehl behandelt --
            das erste enthielt "Noor" und wurde sofort (unvollständig!) an
            Noor geschickt, wonach das Fenster wieder zuging und der ganze
            Rest verloren war.

            War zu wenig Sprache dabei, um ein Wort zu sein, wird gar nichts
            abgegeben -- siehe MINDEST_SPRACH_FRAMES."""
            if (gesprochene_bilder is not None
                    and gesprochene_bilder < MINDEST_SPRACH_FRAMES):
                puffer.clear()
                with self._schloss:
                    self._laufend = None
                return
            self._auftraege.put((list(puffer), endgueltig))
            puffer.clear()
            with self._schloss:
                self._laufend = None

        with sd.InputStream(samplerate=RATE, channels=1, dtype='int16',
                            blocksize=FRAME_LEN, device=self.geraet) as strom:
            while not self._stop.is_set():
                block, _ueberlauf = strom.read(FRAME_LEN)
                frame = block[:, 0]

                # Solange Qalam aufnimmt: wirklich taub. Das ist der einzige
                # harte Riegel -- Ramzi diktiert gerade, da habe ich zu schweigen.
                #
                # "Schlafen" ist ausdruecklich NICHT hier: wer schlaeft, muss
                # trotzdem geweckt werden koennen. Genau daran ist es beim
                # ersten Test gescheitert -- ich habe "wach auf" nie gehoert,
                # weil ich an dieser Stelle schon abgebrochen habe.
                if qalam_nimmt_auf() or self._spricht_gerade():
                    puffer.clear()
                    in_sprache = False
                    sprach = 0
                    self._kurz_erwartet = False
                    with self._schloss:
                        self._laufend = None
                    continue

                try:
                    ist_sprache = self.vad.is_speech(frame.tobytes(), RATE)
                except Exception:
                    continue

                if ist_sprache:
                    in_sprache = True
                    stille = 0
                    sprach += 1
                    puffer.append(frame)
                    # Nur einen Ausschnitt hinlegen, damit der Mitlauscher in
                    # seinem eigenen Faden etwas zu tun hat. Kopieren, nicht
                    # teilen: an der Deque wird gleich weitergearbeitet.
                    with self._schloss:
                        self._laufend = list(puffer)[-BLICK_FRAMES:]
                    if len(puffer) >= self.max_frames:
                        abgeben(endgueltig=False)   # er redet noch -- nur ein Zwischenstück
                        in_sprache = True            # bleibt in Sprache, es geht ja weiter
                elif in_sprache:
                    stille += 1
                    puffer.append(frame)
                    # Die erste Sekunde Stille gehört noch zum Ausschnitt.
                    # Sonst sieht der Mitlauscher bei einem kurzen "Noor" nur
                    # 0,66 s Ton, und dafür gibt das schnelle Modell oft gar
                    # keinen Text zurück -- nachgemessen, siehe MINDEST_FRAMES.
                    if stille <= NACHLAUF_FRAMES:
                        with self._schloss:
                            self._laufend = list(puffer)[-BLICK_FRAMES:]
                    # Kurze Schwelle in zwei Fällen: es war ohnehin nur ein
                    # kurzer Ruf (siehe KURZ_SPRACH_FRAMES), ODER der
                    # Mitlauscher hat schon einen fertigen kurzen Befehl gehört
                    # (siehe ist_kurzbefehl). Sonst bleiben es die vollen vier
                    # Sekunden, damit Ramzi mitten im Satz denken kann.
                    kurz = sprach <= KURZ_SPRACH_FRAMES or self._kurz_erwartet
                    schwelle = (min(KURZE_STILLE_FRAMES, self.stille_frames) if kurz
                                else self.stille_frames)
                    if stille >= schwelle:
                        # echte Stille -- er ist fertig
                        abgeben(endgueltig=True, gesprochene_bilder=sprach)
                        in_sprache = False
                        stille = 0
                        sprach = 0
                        self._kurz_erwartet = False

    def _melde(self, rueckruf, *args):
        """Rückruf aufrufen, ohne dass ein Fehler darin das Ohr umbringt."""
        if not rueckruf:
            return
        try:
            rueckruf(*args)
        except Exception as e:
            print(f'[Weckwort] Rückruf fehlgeschlagen: {e}')

    def _arbeiter(self):
        """Fertige Segmente genau transkribieren -- in Ruhe, neben der Aufnahme."""
        while not self._stop.is_set():
            try:
                frames, endgueltig = self._auftraege.get(timeout=0.4)
            except queue.Empty:
                continue
            self._arbeiter_rechnet.set()
            try:
                self._pruefe(frames, endgueltig)
            except Exception as e:
                print(f'[Weckwort] Auswertung fehlgeschlagen: {e}')
            finally:
                self._arbeiter_rechnet.clear()

    def _mitlauscher(self):
        """Mithören, WÄHREND gesprochen wird -- eigener Faden, eigenes Tempo.

        Er nimmt sich immer nur den letzten Ausschnitt, den die Aufnahme
        hingelegt hat. Braucht er dafür mal länger, verzögert das die Aufnahme
        um keine Millisekunde -- genau daran ist die erste Fassung gescheitert.
        """
        erkannt = False
        blicke = 0
        letzter = ''
        while not self._stop.is_set():
            time.sleep(0.3)
            with self._schloss:
                schnipsel = self._laufend
            if not schnipsel:
                erkannt = False          # Satz vorbei, beim nächsten neu suchen
                blicke = 0
                letzter = ''
                continue
            if self.schlaeft or len(schnipsel) < MINDEST_FRAMES:
                continue
            # Vorrang für das genaue Modell -- siehe _arbeiter_rechnet.
            if self._arbeiter_rechnet.is_set():
                self._streifen_wachhalten(letzter, erkannt)
                continue
            # Ist der Name gefunden und will niemand den laufenden Mitschrieb,
            # gibt es hier nichts mehr zu holen -- dann weiter zu rechnen wäre
            # reine Verschwendung. Und keine harmlose: das genaue Modell rechnet
            # auf denselben Kernen, und genau in dieser Lage (langer Satz, Name
            # längst erkannt) standen im Protokoll vom 31.07.2026 die
            # Ausreißer von 18-24 s Rechenzeit.
            if erkannt and not self.beim_mitschreiben:
                continue
            if blicke >= BLICKE_JE_AEUSSERUNG and not erkannt:
                continue                 # das war kein Ruf -- siehe die Konstante

            blicke += 1
            vorlaeufig = self._hoer_kurz(schnipsel)
            if not vorlaeufig:
                self._streifen_wachhalten(letzter, erkannt)
                continue
            letzter = vorlaeufig
            if not erkannt and WECKWORT.search(vorlaeufig):
                erkannt = True
                self._melde(self.beim_erkennen)
            if erkannt or time.time() < self.folge_bis:
                self._melde(self.beim_mitschreiben, vorlaeufig)
                # Steckt in dem, was bisher zu hören war, schon ein kurzer
                # Befehl? Dann muss auf keine Denkpause gewartet werden.
                if self.ist_kurzbefehl and not self._kurz_erwartet:
                    try:
                        if self.ist_kurzbefehl(vorlaeufig):
                            self._kurz_erwartet = True
                    except Exception:
                        pass

    def _streifen_wachhalten(self, letzter, erkannt):
        """Den letzten Stand nochmal schicken, damit der Streifen nicht abläuft.

        Es gibt zwei Gründe, warum eine Runde des Mitlauschers keinen neuen Text
        liefert: das genaue Modell hat Vorrang, oder der Ausschnitt gab nichts
        Lesbares her. Beide sind harmlos -- aber für Ramzi sieht es aus, als
        wäre das Ohr weg. Am 31.07.2026: "manchmal gibt es kleine Lücken, da
        habe ich ein bisschen Angst, dass du auf einmal nicht mehr zuhörst.
        Da würde ich einfach weiterreden."

        Und das ist der teure Teil: er redet dann ins Ungewisse weiter. Also
        wird derselbe Text noch einmal geschickt. Der Streifen sieht daran einen
        neuen Zeitpunkt und bleibt stehen, statt in der Haltezeit zu verfallen.
        """
        if letzter and (erkannt or time.time() < self.folge_bis):
            self._melde(self.beim_mitschreiben, letzter)

    def _hoer_kurz(self, frames):
        """Einen kurzen Ausschnitt mithören, um den Namen zu finden.

        Immer nur ein Ausschnitt, nie der ganze Satz: die Rechenzeit soll
        gleich bleiben, egal wie lange Ramzi schon spricht."""
        if len(frames) < MINDEST_FRAMES:
            return None
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        try:
            # vad_filter schneidet die Stille weg, BEVOR gerechnet wird. Der
            # Ausschnitt besteht zum großen Teil aus Stille -- Ramzi hat seinen
            # Namen gesagt und wartet -- und ohne den Filter rechnet das Modell
            # darauf mit. Nachgemessen am 31.07.2026: reine Stille kostet mit
            # Filter 0,01 s statt 1,4 s, und der Name wird zuverlässiger
            # erkannt ("Nur welcher Tag" statt "Moa, welcher Tag").
            segmente, _ = self.flink.transcribe(audio, language='de', beam_size=1,
                                                vad_filter=True)
            # Nur was das Modell selbst für Sprache hält.
            #
            # Whisper erfindet auf Stille Text -- das ist bekannt und war hier
            # gefährlich, weil so ein erfundener Satz den Namen enthalten und
            # mich mitten in der Ruhe wecken kann. Nachgemessen am 31.07.2026:
            # echte Sätze liegen bei no_speech 0,03-0,23, erfundene bei
            # 0,44-0,49. Die Schwelle liegt dazwischen, mit Luft nach beiden
            # Seiten.
            return ' '.join(s.text for s in segmente
                            if s.no_speech_prob < ERFINDUNGS_SCHWELLE).strip()
        except Exception:
            return None

    def _pruefe(self, puffer, endgueltig=True):
        """Segment genau transkribieren und entscheiden.

        `endgueltig=False` heißt: nur ein Zwischenstück, Ramzi redet noch
        weiter (siehe abgeben()). `beim_wecken` bekommt das mitgeteilt und
        entscheidet selbst, ob es sammelt oder ausführt -- hier wird nur
        gehört und weitergegeben, nicht bewertet."""
        if len(puffer) < 8:      # unter ~0,25 s ist es kein Wort, sondern ein Geräusch
            return
        audio = np.concatenate(list(puffer)).astype(np.float32) / 32768.0
        dauer_audio = len(puffer) * FRAME_MS / 1000
        _start = time.time()
        try:
            segmente, _ = self.modell.transcribe(audio, language='de', beam_size=1)
            text = ' '.join(s.text for s in segmente).strip()
        except Exception as e:
            print(f'[Weckwort] Erkennung fehlgeschlagen: {e}')
            return
        # Die Messung, die in der Sitzung vom 31.07.2026 fehlte: wie lange
        # braucht das genaue Modell wirklich? Siehe STAND-Sprachschicht.md.
        dauer_rechnen = time.time() - _start
        print(f'[{time.strftime("%H:%M:%S")}] [Weckwort] {dauer_audio:.1f}s Audio -> '
              f'{dauer_rechnen:.2f}s Rechenzeit (small)')

        if not text:
            return

        # Der Folgesatz braucht den Namen nicht mehr.
        #
        # Ramzi sagt oft erst nur "Noor", wartet auf das Zeichen und redet dann
        # weiter. Dieser zweite Satz enthält den Namen naturgemäß nicht -- ohne
        # diese Regel wäre er verloren, und genau das hat sich für ihn angefühlt
        # wie "sie hört mir nicht mehr zu".
        im_gespraech = time.time() < self.folge_bis
        if WECKWORT.search(text) or im_gespraech:
            self._melde(self.beim_wecken, text, endgueltig)


# --------------------------------------------------------------------------
if __name__ == '__main__':
    # Selbsttest: sag "Noor" in dein Mikrofon.
    def gerufen(text):
        print(f'>>> geweckt: {text!r}')

    w = Weckwort(gerufen)
    print('Höre zu. Sag "Noor ..." -- Strg+C beendet.')
    w.starte()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        w.stoppe()
