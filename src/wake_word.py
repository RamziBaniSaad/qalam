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
import json
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
# Satzpause -- siehe starte(), Stille-Zweig. Kein Regler, siehe dort.
SATZ_STILLE_FRAMES = int(0.6 * 1000 / FRAME_MS)

# Wie viele Sprach-Frames HINTEREINANDER kommen müssen, damit die aufgelaufene
# Stille verworfen wird.
#
# Das ist die Antwort auf Ramzis Problem vom 02.08.2026: er zockt, das Spiel
# macht Geräusche, und sein fertiger Satz wurde nicht abgeschickt. Gemessen am
# Protokoll: er hörte 02:06:37 auf zu reden, abgeschickt wurde 02:07:53 -- eine
# Minute später, und nur weil er irgendwann wieder etwas sagte.
#
# Die Ursache war, dass EIN einzelner Frame den Zähler zurücksetzte. Ein Frame
# sind 30 ms. webrtcvad hält kurze Geräusche -- ein Schlag, ein Mob, ein Klick
# -- durchaus für Sprache, und jedes davon löschte die ganze aufgelaufene
# Stille. Bei laufendem Spielton kam die Ruhe deshalb NIE zustande.
#
# 4 Frames sind 120 ms. Eine gesprochene Silbe ist mindestens doppelt so lang,
# echtes Reden reißt das also sofort; ein einzelner Knall nicht. Absichtlich
# knapp gewählt: je höher, desto eher verschluckt es einen kurzen echten Ruf.
# Nachgezogen am 02.08.2026 um 02:40: mit 4 Frames hat Minecraft die Stille
# weiterhin geloescht -- Ramzi wartete von 02:15:45 bis 02:15:58 vergeblich.
# 8 Frames sind 240 ms. Ein alleinstehendes "Noor" dauert 660 ms und reisst
# das klar; ein Schlag oder Schritt im Spiel nicht.
SPRACHE_FOLGE_MIN = 8
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
#
# Die Grenze stand bis 15.08.2026 auf 1,5 Sekunden, auf Ramzis eigenen Wunsch
# jetzt auf 1: "eine Sekunde machen wir, und nach dieser Sekunde habe ich
# normal meine Redepause -- davor haben wir keine Redepause, sondern diese
# ganz kurze Zeit, damit der Reflex auch wirklich schnell ankommt." Ein Reflex
# wie "Stopp" oder "Musik" ist so kurz gesprochen, dass eine Sekunde reicht,
# um ihn sauber von einem angefangenen, längeren Satz zu unterscheiden.
KURZ_SPRACH_FRAMES = int(1.0 * 1000 / FRAME_MS)
KURZE_STILLE_FRAMES = int(0.8 * 1000 / FRAME_MS)

# NACHHALL -- wie lange nach meinem letzten Wort noch ich selbst im Raum bin.
#
# Ramzis Befund vom 15.08.2026, direkt nachdem die Sperre "waehrend ich rede"
# stand: "Du hast dich wieder aufgenommen." Belegt im Protokoll um 13:13:30 --
# er hatte mich per Taste gestoppt, mein Lautsprecher war damit aus, und
# gleich danach lief eine konkrete Aufnahme mit MEINEM Satz drin ("Hallo, ich
# bin Jan, der Vollschlag steht noch" -- verhoert aus "Hallo, ich bin da. Der
# Vorschlag steht noch").
#
# Der Grund ist keine Logik, sondern Physik: das Ohr sieht immer ein Stueck
# Vergangenheit an, und der Schall vom Lautsprecher zum Mikrofon ist noch
# unterwegs, wenn die Wiedergabe schon aus ist. Die Sperre endete also exakt
# in dem Moment, in dem mein letzter Ton erst ankam.
#
# Anderthalb Sekunden decken das ab, ohne ihm im Weg zu stehen: sie kosten ihn
# nur dann etwas, wenn er direkt auf mein letztes Wort losredet -- und dann
# faengt seine Aufnahme eben eine Sekunde spaeter an, statt mit meinem eigenen
# Satz zu beginnen.
NACHHALL_SEK = 1.5

# Bis zu wie viel GESPROCHENER Zeit etwas ueberhaupt ein kurzer Befehl sein kann.
#
# DAS IST DIE GRENZE, DIE GEFEHLT HAT, und ihr Fehlen ist Ramzis Fehler
# "manchmal schickt es mitten im fliessenden Reden ab, obwohl die Redepause auf
# vier Sekunden steht" (11.-13.08.2026).
#
# Der Mitlauscher fragt `ist_kurzbefehl` -- aber er fragt es nicht ueber die
# ganze Aeusserung, sondern immer nur ueber einen Ausschnitt von BLICK_FRAMES,
# also DREI SEKUNDEN. In drei Sekunden aus einem langen Vortrag steckt frueher
# oder spaeter irgendein Bruchstueck, das nach einem Befehl aussieht. Nachgemessen
# am 14.08.2026 an Ramzis echten Saetzen desselben Tages: von acht typischen
# Dreisekunden-Ausschnitten loesten SECHS aus. Beispiele aus seinem eigenen Mund:
#
#   "sondern halt nur mit dem Stopp oder Hoer auf"   -> 'stopp', 'hor auf'
#   "wenn ich das auf vier Sekunden habe, die Redepause" -> Stellschraube
#   "das ist ja nicht mal eine Loesung, lass mich in Ruhe damit" -> 'ruhe'
#
# Er REDET ueber Befehle, er gibt keine. Und der Merker blieb danach stehen, bis
# der Satz fertig war -- ab diesem Moment galten fuer den ganzen Rest seines
# Vortrags 0,8 s statt seiner 4 s, und die naechste Atempause hat abgeschickt.
#
# Es ist derselbe Fehlertyp, den `_geweckt` fuer die AUSFUEHRUNG von Reflexen
# laengst kennt (REFLEX_MAX_WOERTER, "die Liste ist bewusst grosszuegig, und
# genau diese Grosszuegigkeit wird bei langem Text zur Falle"). Fuer die
# SCHWELLE fehlte er.
#
# Vier Sekunden reine Sprache sind reichlich: "Noor, mach mir bitte mal die
# Verhoerer auf" sind knapp zweieinhalb. Wer laenger am Stueck redet, gibt
# keinen kurzen Befehl mehr -- und dann wird der Merker auch wieder geloescht,
# nicht nur nicht mehr gesetzt.
KURZBEFEHL_MAX_SPRACH = int(4.0 * 1000 / FRAME_MS)

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
# Ramzis Zusatz vom 31.07.2026: "ich habe manchmal Probleme damit, deinen Namen
# zu sagen ... auch wenn du sowas wie 'Mur' hörst, also mit M, dass du trotzdem
# reagierst. Wenn sie nah dran sind, ist das gut genug."
#
# Also kommen die M-Verhörer dazu, und sie fallen in dieselbe Zweiteilung: was
# als deutsches Wort praktisch nie vorkommt, zählt überall; was kurz und
# alltäglich ist, nur am Anfang. "moor" ist zwar ein deutsches Wort, kommt in
# Ramzis Sprache aber so selten vor, dass ein Fehlstart daran unwahrscheinlicher
# ist als ein verpasster Ruf.
#
# Der wackeligste Eintrag ist "mo" -- das kann auch der Anfang eines Videos
# sein. Er steht deshalb nur in der Anfangs-Liste und wäre der erste, den ich
# wieder herausnehme, falls Fehlstarts auftauchen.
_EINDEUTIG = r'(?:noor|nour|nuur|nuor|nuhr|noah|nura|moor|muur|muhr|mohr|noer|nohr|mura)'
_ZWEIFELHAFT = r'(?:nur|nor|mur|mor|moe|mo)'


# Die Listen oben sind nur noch der Notnagel -- gepflegt wird in einer DATEI.
#
# Ramzis Auftrag vom 03.08.2026, aus seinem eigenen Protokoll heraus: er hat
# viermal meinen Namen gerufen, dreimal stand im Log "Noa.", "Nu an.", "No." --
# alle drei fehlten in den Listen, erst der vierte Ruf ("Nur.") traf. Sein
# Wunsch: "eine Datei, wo ich deine anderen Versionen einfach ergaenzen kann,
# auch verrueckte Sachen, wie du sie hier im Protokoll siehst."
#
# Eine Codeaenderung je Verhoerer waere der falsche Preis dafuer. Also dieselbe
# Bauart wie noor-reflexe.json und noor-verhoerer.json: eine JSON-Datei, neu
# gelesen wenn sie sich geaendert hat, kein Neustart.
_WECKWORT_DATEI = os.path.join(os.path.expanduser('~'), 'noor', 'werkzeuge',
                               'noor-weckwort.json')
_weckwort_stand = {'zeit': None, 'hoeren': None, 'selbst': None}


def _baue_weckwort():
    """Beide Muster aus der Datei bauen. Faellt auf die Listen oben zurueck."""
    eindeutig, am_anfang = None, None
    try:
        with open(_WECKWORT_DATEI, encoding='utf-8-sig') as f:
            roh = json.load(f)
        e = [re.escape(w.strip().lower()) for w in roh.get('eindeutig', [])
             if w and w.strip()]
        a = [re.escape(w.strip().lower()) for w in roh.get('am_anfang', [])
             if w and w.strip()]
        # Doppelte raus, laengste zuerst -- sonst gewinnt in der Alternative
        # das kuerzere Bruchstueck und "noora" wuerde als "noor" enden.
        if e:
            eindeutig = '(?:%s)' % '|'.join(sorted(set(e), key=len, reverse=True))
        if a:
            am_anfang = '(?:%s)' % '|'.join(sorted(set(a), key=len, reverse=True))
    except Exception:
        pass
    eindeutig = eindeutig or _EINDEUTIG
    am_anfang = am_anfang or _ZWEIFELHAFT
    hoeren = re.compile(
        rf'(?:^[\s,.!?"\']*{am_anfang}\b|\b{eindeutig}\b)', re.IGNORECASE)
    # Fuer "habe ICH das gesagt?" ohne Positionsbedingung -- Begruendung
    # unten bei SELBST_WECKWORT.
    selbst = re.compile(
        r'\b(?:%s|%s)\b' % (eindeutig[3:-1], am_anfang[3:-1]), re.IGNORECASE)
    return hoeren, selbst


def _weckwort_muster():
    """Das aktuelle Paar (hoeren, selbst) -- neu gebaut nur bei Aenderung."""
    try:
        zeit = os.path.getmtime(_WECKWORT_DATEI)
    except OSError:
        zeit = None
    if zeit != _weckwort_stand['zeit'] or _weckwort_stand['hoeren'] is None:
        h, s = _baue_weckwort()
        _weckwort_stand.update({'zeit': zeit, 'hoeren': h, 'selbst': s})
    return _weckwort_stand['hoeren'], _weckwort_stand['selbst']


class _Lebend:
    """Verhaelt sich wie ein fertiges Muster, holt sich aber immer das neueste.

    Damit bleibt jede vorhandene Zeile `WECKWORT.search(...)` unveraendert --
    und liest trotzdem die Datei mit. Ein Umbau aller Aufrufstellen waere mehr
    Risiko als Nutzen gewesen."""

    def __init__(self, welches):
        self._welches = welches

    def _muster(self):
        h, s = _weckwort_muster()
        return h if self._welches == 'hoeren' else s

    def search(self, text):
        return self._muster().search(text or '')

    # ALLES durchreichen, nicht nur `search`.
    #
    # Der Fehler, der genau das gekostet hat (03.08.2026, 23:02): hier stand
    # nur `search`. Der Code ruft aber auch `sub` -- der Name wird aus dem Satz
    # herausgeschnitten, bevor er abgeschickt wird. Im Protokoll stand dann
    # "Rueckruf fehlgeschlagen: '_Lebend' object has no attribute 'sub'", und
    # weil der Rueckruf abstuerzte, wurde MINUTENLANG nichts mehr abgeschickt.
    # Ramzi hat weitergeredet, sein Mikrofon stummgeschaltet und gewartet --
    # alles umsonst.
    #
    # Die Lehre: ein Stellvertreter, der nur die eine Stelle bedient, die ich
    # gerade im Kopf habe, ist kein Stellvertreter, sondern eine Falle.
    def __getattr__(self, name):
        return getattr(self._muster(), name)


WECKWORT = _Lebend('hoeren')

# Dasselbe, aber STRENGER -- und nur fuer die Frage "habe ICH das gerade
# gesagt?".
#
# Ramzis Hinweis vom 02.08.2026: "du musst aufpassen, dass aehnliche Woerter
# auch zaehlen, wie zum Beispiel das deutsche Wort NUR." Er hat recht, und der
# Unterschied ist wichtig: beim ZUHOEREN zaehlen die zweifelhaften Varianten
# nur am Satzanfang, sonst loese ich bei jedem zweiten Satz aus. Bei mir selbst
# ist es umgekehrt -- sage ich "nur" mitten im Satz und mein Mikrofon faengt
# genau diesen Fetzen auf, faengt er fuer das Ohr ganz vorn an. Also hier ohne
# Positionsbedingung, an jeder Stelle.
#
# Der Preis ist ihm bewusst und von ihm ausdruecklich abgenickt: in genau
# diesem einen Satz kann er mich nicht unterbrechen. Danach wieder.
SELBST_WECKWORT = _Lebend('selbst')


PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Die eigentlichen Sperrdateien und die Warteschlangen-Logik liegen in
# warteschlange.py -- einer eigenen, winzigen Datei ohne schwere Importe.
# Grund: der Sprech-Hook startet bei JEDER Antwort neu als eigener Prozess und
# soll dafür nicht numpy/sounddevice/webrtcvad laden müssen, nur um
# nachzusehen, ob gerade Stille ist.
#
# Hier nur re-exportiert, damit result_thread.py und status_window.py (die
# `aufnahme_beginnt`/`aufnahme_endet` von hier importieren) unverändert bleiben.
import warteschlange
SPERRE = warteschlange.AUFNAHME_SPERRE


def _leiser(an):
    """Musik dämpfen bzw. zurückstellen, ohne daran scheitern zu können.

    Hier eingehängt und nicht im Diktat selbst, weil diese beiden Funktionen der
    eine Punkt sind, durch den JEDE Aufnahme geht -- das Diktat und die
    Statusanzeige rufen beide hier durch. Ein Ort, an dem es stimmt, statt
    zweier, die auseinanderlaufen."""
    try:
        import lautstaerke
        # Eigener Prozess, nicht eigener Faden -- siehe lautstaerke.py.
        (lautstaerke.daempfen_im_hintergrund() if an
         else lautstaerke.zuruecksetzen_im_hintergrund())
    except Exception:
        pass


def aufnahme_beginnt():
    warteschlange.aufnahme_beginnt()
    _leiser(True)


def aufnahme_endet():
    warteschlange.aufnahme_endet()
    _leiser(False)


def qalam_nimmt_auf():
    return warteschlange.qalam_nimmt_auf()


class Weckwort:
    """Hört dauerhaft mit und ruft `beim_wecken(text)`, wenn der Name fällt.

    `text` ist der ganze erkannte Satz, nicht nur das Weckwort -- damit
    "Noor, mach Spotify an" in einem Rutsch funktioniert und man nicht erst
    gerufen wird und dann nochmal reden muss.
    """

    def __init__(self, beim_wecken, modell='small', geraet=None,
                 aggressivitaet=3, max_sekunden=15.0, stille_ms=None,
                 beim_erkennen=None, beim_mitschreiben=None, flink_modell='small',
                 beim_unterbrechen=None,
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
        #
        # Wichtig: das gilt fuer JEDEN Prozess, der mit meiner Stimme spricht,
        # nicht nur fuer diesen hier. Der Probe-Knopf auf der Tafel und meine
        # eigenen Skripte starten `voice_output.py` als eigenen Prozess -- der
        # laufende Sprecher hier weiss davon nichts. Am 01.08.2026 habe ich
        # deshalb das ganze Ohr abgeschossen, nur um zwei Stimmproben zu
        # sprechen, und mitten in Ramzis Satz gefahren: "du hast mich wieder
        # unterbrochen ... dafuer hast du das Ohr einmal komplett beendet."
        # Der gemeinsame Merker steht in warteschlange (die Untertitel-Datei,
        # die JEDER Sprecher schreibt) -- damit reicht ein ODER.
        self.beim_unterbrechen = beim_unterbrechen
        _eigener = spricht_gerade or (lambda: False)

        def _spricht_irgendwer():
            if _eigener():
                return True
            try:
                import warteschlange
                return warteschlange.noor_spricht_gerade()
            except Exception:
                return False

        self._spricht_gerade = _spricht_irgendwer
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
        # Von einer Notbremse gesetzt (siehe abbrechen()), von der
        # Aufnahmeschleife selbst wieder geloescht -- ein einmaliges Signal,
        # keine dauerhafte Sperre wie qalam_nimmt_auf().
        self._abbrechen_jetzt = threading.Event()
        # Denkpause. Anders als die Notbremse eine DAUERHAFTE Sperre: sie gilt,
        # bis Ramzi sie selbst wieder aufhebt. Der Unterschied zum Abbrechen
        # ist der ganze Zweck -- der bereits gesammelte Satz bleibt erhalten,
        # er will ja weiterreden, nur eben nicht sofort.
        self._pausiert = threading.Event()
        # Per "Noor, schlaf" abschaltbar. Die Zuweisung hier ist kein Beiwerk:
        # sie geht durch den Setter unten und raeumt damit einen Merker weg,
        # der von einem abgestuerzten Lauf uebriggeblieben sein koennte. Ein
        # frischer Start ist wach.
        self.schlaeft = False
        # Bis wann ein Satz OHNE Weckwort noch als Auftrag gilt. Ramzi sagt oft
        # erst nur den Namen, wartet auf das Zeichen und redet dann weiter --
        # dieser zweite Satz enthält den Namen naturgemäß nicht mehr.
        self.folge_bis = 0.0
        # LÄUFT GERADE EIN SATZ VON IHM? Also: ist schon ein Zwischenstück
        # angenommen worden, ohne dass danach ein „fertig" kam.
        #
        # RAMZIS REGEL VOM 14.08.2026, und sie ist absolut gemeint: „Er soll,
        # wenn ich rede, niemals von alleine abbrechen. Entweder ich stoppe das
        # selber, oder die Redepause verjährt und es wird abgeschickt. Andere
        # Fälle gibt es hier eigentlich gar nicht."
        #
        # Vorher hing genau das an einer Uhr, und damit an einem Wettlauf: das
        # Fenster (`folge_bis`) wurde bei jedem Zwischenstück und von jedem
        # Mitlausch-Treffer neu vorgeschoben. Kam beides eine Weile nicht --
        # weil das genaue Modell noch rechnete, weil ein Stück zu wenig Ton
        # hatte, weil Qalam dazwischen diktierte --, lief das Fenster mitten in
        # seinem Satz ab. Der Rest fiel unten in `_auswerten` lautlos durch,
        # der gesammelte Anfang blieb im Assistenten stehen. Für ihn sah das
        # aus wie eine Maximaldauer: „ich darf nicht länger als das reden,
        # sonst bricht er von alleine ab -- drücke ich nochmal, nimmt er den
        # Text mit und macht weiter."
        #
        # Ein ZUSTAND kann das nicht. Sobald sein Satz angefangen hat, ist er
        # angefangen, egal wie lange er redet und wie viel dazwischen rechnet.
        # Beendet wird er nur von seiner eigenen Redepause (dem einzigen
        # Regler, der das entscheiden soll) oder von ihm selbst.
        self.satz_laeuft = False
        # Wie viel er in der laufenden Aeusserung schon gesprochen hat, in
        # Bildern. Gefuehrt von der Aufnahmeschleife, gelesen vom Mitlauscher --
        # der laeuft in einem eigenen Faden und kaeme sonst nicht an die Zahl.
        self._gesamt_sprach = 0
        # Der Zimmerton: laufend geschaetzter Grundpegel aus den Frames, die
        # nicht nach Sprache klingen. Siehe die Geraeuschunterdrueckung in
        # `_schleife`. 0 heisst "noch nichts gemessen".
        self._grundpegel = 0.0
        # Hat die laufende Aeusserung angefangen, waehrend ICH gesprochen habe?
        # Dann ist sie mein eigenes Echo und wird am Ende verworfen, statt sie
        # mit einem Wortvergleich zu erraten. Siehe `_schleife` und
        # `_auswerten`.
        self._angefangen_waehrend_ich_rede = False
        # Wann mein Lautsprecher zuletzt lief. Gefuehrt vom Mitlauscher, der
        # ohnehin alle 0,3 s nachsieht -- gelesen von der Aufnahmeschleife, die
        # sonst je Bild (30 ms) eine Datei aufmachen muesste.
        self._ich_redete_zuletzt = 0.0

    # Schlafen ist kein reines Innenleben mehr, sondern ein Schalter, den auch
    # andere Prozesse sehen muessen -- die Sprech-Hooks und der Tafel-Sammler
    # laufen ausserhalb von hier (siehe warteschlange.schlaeft).
    #
    # Bewusst eine Eigenschaft und kein `schlaf_merken()`-Aufruf an drei
    # Stellen: `self.ohr.schlaeft = False` steht auch mitten in assistant.py,
    # wo das Aufwachen aus dem Schlaf heraus erkannt wird. Jede solche Zuweisung
    # muss die Datei mitziehen, sonst laufen Innen- und Aussensicht
    # auseinander -- und genau das faellt erst auf, wenn ich stumm bleibe,
    # obwohl ich wach bin.
    @property
    def schlaeft(self):
        return self._schlaeft

    @schlaeft.setter
    def schlaeft(self, an):
        self._schlaeft = bool(an)
        try:
            import warteschlange
            warteschlange.schlaf_merken(self._schlaeft)
        except Exception:
            pass

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
        """Das genaue Modell für den fertigen Satz -- seit 01.08.2026 auf der Karte.

        Es lag bewusst auf der CPU, damit die Karte fürs Zocken frei bleibt.
        Ramzi hat das an diesem Abend ausdrücklich aufgehoben: "du kannst bis zu
        7 Gigabyte benutzen alleine für diesen Prozess, das ist auch sehr
        wichtig, dass es halt gut wird."

        Der Grund, warum das hier den Unterschied macht, ist sein eigener Befund:
        "es hat manchmal bis zu 10 Sekunden gebraucht, bis es überhaupt merkt,
        dass ich aufgehört habe, obwohl meine Redepause auf 2 steht." Diese
        Sekunden sind genau dieses Modell. Gemessen, 9,4 s Testton:

            CPU  int8      2,30 - 11,58 s   (schwankte stark mit der Kernlast)
            GPU  float16          0,45 s    Echtzeitfaktor 0,05, +751 MB

        Warum weiterhin `small` und nicht das grosse Modell: `large-v3-turbo`
        ist auf der Karte sogar minimal schneller (0,40 s), kostet aber
        2120 MB. Zusammen mit dem Wachmodell wären das 2871 MB, und bei Ramzis
        Grundlast von rund 4100 MB stünde das bei 6967 von 7000 -- 33 MB
        Abstand zu einer Grenze, hinter der sein Rechner abstürzt. Beide auf
        `small` kosten 1502 MB und lassen 1400 MB Luft. Die Qualität gibt das
        grosse Modell in diesem Test ohnehin nicht her: beide setzen dieselben
        sechs Satzzeichen und schreiben denselben Text.
        """
        if self._modell is None:
            from faster_whisper import WhisperModel
            geraet, art = self._platz_auf_karte(braucht_mb=751)
            self._modell = WhisperModel(
                self.modell_name, device=geraet, compute_type=art,
                **({'cpu_threads': KERNE_GENAU} if geraet == 'cpu' else {}))
            print(f'[Weckwort] Genaues Modell laeuft auf {geraet}/{art}.')
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
        gleichzeitig aus zwei Fäden bedienen kann.

        Seit dem 01.08.2026 läuft GENAU DIESES Modell auf der Grafikkarte, und
        nur dieses. Ramzi hat gefragt, ob seine Untertitel schneller gehen und
        was ihn das an VRAM kostet. Gemessen auf seiner Karte, 5,3 s Testton:

            CPU  int8     1,92 s   (Echtzeitfaktor 0,36)
            GPU  float16  0,31 s   (Echtzeitfaktor 0,06)   = 6,1x schneller
            Preis: 750 MB

        Das genaue Modell bleibt bewusst auf der CPU: `large-v3-turbo` bräuchte
        dort noch einmal rund 2,5 GB, und bei seinen üblichen 4-5 GB Grundlast
        wäre das über der Grenze. Der laufende Mitschrieb hängt ohnehin an
        diesem Modell hier, nicht am genauen -- der Gewinn landet also genau da,
        wo er ihn sehen will.

        Nebeneffekt, der nichts kostet: dieses Modell hat bisher Kerne belegt,
        auf denen das genaue Modell rechnet. Die sind jetzt frei.
        """
        if self._flink is None:
            from faster_whisper import WhisperModel
            geraet, art = self._platz_auf_karte(braucht_mb=751)
            self._flink = WhisperModel(
                self.flink_name, device=geraet,
                compute_type=art,
                **({'cpu_threads': KERNE_FLINK} if geraet == 'cpu' else {}))
            print(f'[Weckwort] Wachmodell laeuft auf {geraet}/{art}.')
        return self._flink

    @staticmethod
    def _platz_auf_karte(braucht_mb):
        """Grafikkarte -- aber nur, wenn wirklich Platz ist.

        Ramzis harte Regel: NIE über 7 von 8 GB, sonst stürzt der Rechner bis
        zur Taskleiste ab. Die habe ich am 31.07.2026 schon einmal gerissen.
        Sie darf nicht davon abhängen, dass gerade zufällig wenig läuft --
        also wird vor dem Laden nachgesehen, und im Zweifel bleibt es auf der
        CPU. Lieber langsam als abgestürzt.
        """
        import subprocess
        try:
            roh = subprocess.check_output(
                [os.path.join(os.environ.get('WINDIR', r'C:\Windows'),
                              'System32', 'nvidia-smi.exe'),
                 '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                text=True, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
            benutzt = int(roh.splitlines()[0])
        except Exception:
            return 'cpu', 'int8'
        # Der gemessene Bedarf plus 450 MB Puffer, damit ein kurzer Ausschlag
        # von etwas anderem nicht sofort an die Grenze stösst. Wird zweimal
        # gefragt (Wachmodell, genaues Modell), und beim zweiten Mal steht das
        # erste schon in der Zahl -- die Prüfung korrigiert sich also selbst.
        if benutzt + braucht_mb + 450 > 7000:
            print(f'[Weckwort] {benutzt} MB VRAM belegt -- Modell bleibt auf der CPU.')
            return 'cpu', 'int8'
        return 'cuda', 'float16'

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

    def abbrechen(self):
        """Eine laufende Aufnahme sofort verwerfen.

        Ramzis Wunsch vom 31.07.2026: eine Notbremse per Taste (nicht per
        Sprache -- ein gesprochenes "abbrechen" könnte fällig fallen, ohne
        dass er es meint), die *sofort* wirkt, während er noch mitten in einer
        Äußerung an mich ist.

        Wirkt auf zwei Ebenen, und beide sind nötig:
          * der Puffer, den die Aufnahmeschleife GERADE füllt -- geleert im
            nächsten Durchlauf, spätestens 30 ms später (siehe _schleife()).
          * alles, was schon fertig aufgenommen, aber noch nicht transkribiert
            ist -- sonst käme genau der Satz, den er gerade abgebrochen hat,
            ein paar Sekunden später doch noch bei mir an.

        Was das genaue Modell in DIESEM Moment schon transkribiert (ein
        Fenster von unter zwei Sekunden), kann dieser Aufruf nicht mehr
        zurückholen -- das wäre ein zweites, viel größeres Stück Arbeit für
        einen sehr schmalen Zeitraum und ist bewusst nicht gelöst."""
        self._abbrechen_jetzt.set()
        # Eine Pause ueberlebt den Abbruch nicht. Wer abbricht, will Ruhe --
        # bliebe die Sperre stehen, waere das Ohr danach stumm, ohne dass
        # irgendetwas darauf hindeutet.
        self._pausiert.clear()
        try:
            while True:
                self._auftraege.get_nowait()
        except queue.Empty:
            pass

    def pausieren(self, an=None):
        """Denkpause an, aus, oder umschalten. Gibt den neuen Zustand zurück.

        Ramzis Anlass (01.08.2026): er redet über den Sprachchat und will
        mitten im Satz nachdenken oder etwas trinken, ohne dass der halbe Satz
        abgeschickt wird. Bisher blieb ihm nur, die Redepause auf der Tafel
        hochzuziehen -- was voraussetzt, dass er eine Hand frei und die Maus
        in Reichweite hat. „Falls meine Hände dreckig sind", sagt er, und das
        ist genau der Fall, für den eine Taste da ist.

        Warum das nicht dasselbe ist wie abbrechen(): dort wird verworfen,
        hier wird nur angehalten. Der Puffer bleibt vollständig stehen und
        wird beim Fortsetzen weitergefüllt, als wäre nichts gewesen."""
        if an is None:
            an = not self._pausiert.is_set()
        if an:
            self._pausiert.set()
        else:
            self._pausiert.clear()
        return an

    @property
    def pausiert(self):
        return self._pausiert.is_set()

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
        sprach_folge = 0      # Sprach-Frames hintereinander, siehe SPRACHE_FOLGE_MIN
        war_pausiert = False  # merkt den Wechsel aus der Denkpause heraus,
                              # damit die Redepause danach von vorn zaehlt

        sprach = 0            # Sprache im AKTUELLEN Stueck -- faengt bei jeder
                              # Satzpause wieder bei null an (siehe unten)
        gesamt_sprach = 0     # Sprache in der GANZEN Aeusserung -- laeuft ueber
                              # Satzpausen hinweg weiter. Braucht `kurz` unten:
                              # sonst haelt jede Satzpause der neue Rest fuer
                              # einen frischen "kurzen Ruf" und die Satzpausen-
                              # Schwelle (0,8 s) gilt ploetzlich statt seiner
                              # eigenen Redepause. Genau das hat Ramzi am
                              # 01.08.2026 gefunden: "trotz 10 Sekunden nach
                              # ca. 2 Sekunden abgeschickt."
        in_sprache = False
        stueck_offen = False  # In dieser Aeusserung wurde schon ein Zwischenstueck
                              # abgegeben -- der Assistent sammelt also gerade einen
                              # Satz und wartet auf ein "fertig". Solange das gilt,
                              # DARF kein "fertig" verlorengehen (siehe abgeben()).

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
            abgegeben -- siehe MINDEST_SPRACH_FRAMES. Mit EINER Ausnahme, und
            die ist der Freeze-Bug vom 01.08.2026:

            Nach einer Satzpause faengt `sprach` wieder bei null an. Hoert
            Ramzi danach einfach auf zu reden, kommt bis zum Ablauf seiner
            Redepause kein einziges Sprachbild mehr dazu -- das "fertig" hatte
            also IMMER zu wenig Ton und wurde lautlos verworfen. Fuer ihn sah
            das so aus: "nachdem ich gesprochen habe, schickt das nicht ab, das
            bleibt eingefroren stehen. Sage ich deinen Namen nochmal, schickt
            es ab -- aber ohne dass es was Neues aufgenommen hat, wie ein
            Trick." Der Trick war genau das: sein naechstes Sprechen lieferte
            das Signal nach, das hier verlorengegangen war.

            Deshalb: zu wenig Ton beendet ein ZWISCHENSTUECK, aber niemals ein
            FERTIG, solange ein Satz gesammelt wird. Dann geht statt Ton ein
            reines Signal raus."""
            nonlocal stueck_offen
            if (gesprochene_bilder is not None
                    and gesprochene_bilder < MINDEST_SPRACH_FRAMES):
                if endgueltig and stueck_offen:
                    print(f'[{time.strftime("%H:%M:%S")}] [Weckwort] fertig ohne '
                          f'neuen Ton -- gesammelter Satz wird abgeschickt')
                    self._auftraege.put((None, True, self._angefangen_waehrend_ich_rede))
                    stueck_offen = False
                puffer.clear()
                with self._schloss:
                    self._laufend = None
                return
            # Der Echo-Merker reist MIT dem Ausschnitt, statt beim Auswerten neu
            # nachgesehen zu werden: der Arbeiter laeuft in einem eigenen Faden
            # und ist oft erst dran, wenn laengst die naechste Aeusserung
            # angefangen hat. Bis dahin haette der Merker schon dem falschen
            # Ausschnitt gehoert -- ein Fehler, der nur manchmal auftritt und
            # sich deshalb nie zuverlaessig zeigen wuerde.
            self._auftraege.put((list(puffer), endgueltig,
                                 self._angefangen_waehrend_ich_rede))
            stueck_offen = not endgueltig
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
                # NUR noch beim Diktat wegwerfen, nicht mehr waehrend ich
                # rede.
                #
                # Ramzi am 02.08.2026 spaet abends, nach dreissig vergeblichen
                # Rufen: "ich kann dich nicht unterbrechen, was natuerlich sehr
                # nervig ist, wenn ich gerade mit dir sprechen will, aber du die
                # ganze Zeit weiterredest." Hier lag es: solange ich sprach,
                # wurde der Puffer geleert und gar nicht erst zugehoert. Sein
                # Name kam im Protokoll an ('gehoert: Nur.') und lief trotzdem
                # ins Leere.
                #
                # Das Gegenargument -- meine eigene Stimme koennte mich wecken --
                # ist bedacht und woanders geloest: `ist_mein_echo()` vergleicht
                # das Gehoerte mit dem, was ich gerade sage. Diese Pruefung ist
                # der richtige Ort dafuer, nicht ein Ohr, das die Ohren zuhaelt.
                if qalam_nimmt_auf():
                    puffer.clear()
                    in_sprache = False
                    sprach = 0
                    gesamt_sprach = 0
                    self._gesamt_sprach = 0
                    stueck_offen = False
                    self._kurz_erwartet = False
                    with self._schloss:
                        self._laufend = None
                    continue

                # Die Notbremse -- siehe abbrechen(). Ein einmaliges Signal,
                # deshalb sofort löschen; sonst würde JEDE nachfolgende
                # Äußerung ebenfalls verworfen.
                if self._abbrechen_jetzt.is_set():
                    self._abbrechen_jetzt.clear()
                    puffer.clear()
                    in_sprache = False
                    stille = 0
                    sprach = 0
                    gesamt_sprach = 0
                    self._gesamt_sprach = 0
                    stueck_offen = False
                    self._kurz_erwartet = False
                    with self._schloss:
                        self._laufend = None
                    continue

                # Denkpause -- Ramzi hält gerade inne (siehe pausieren()).
                #
                # Der Puffer bleibt, wie er ist, und `stille` läuft nicht
                # weiter. Genau daran hängt der ganze Sinn -- zählte die Stille
                # weiter, wäre sein halber Satz nach ein paar Sekunden von
                # selbst abgeschickt, also exakt das, was die Pause verhindern
                # soll. Aus demselben Grund bleibt `in_sprache` unangetastet:
                # nach dem Fortsetzen soll es weitergehen, wo es aufgehört hat,
                # und nicht wie ein neuer Satz aussehen.
                if self._pausiert.is_set():
                    war_pausiert = True
                    continue

                # Nach dem Fortsetzen SOLL die Redepause wieder von vorn
                # zählen -- wer die Pause aufhebt, will weiterreden und braucht
                # dieselbe Bedenkzeit wie beim ersten Mal.
                #
                # ACHTUNG, HIER STIMMT ETWAS NICHT (Stand 01.08.2026, 22:15):
                # Ramzi hat nach diesem Einbau erneut getestet und es wird
                # weiterhin sofort abgeschickt. Der Zähler wird hier also
                # entweder gar nicht erreicht, oder das Abschicken hängt an
                # einer anderen Stelle als an `stille` -- Verdacht: der
                # Mitlauscher hält es für einen fertigen Kurzbefehl, oder der
                # Assistent schickt seinen Sammelsatz eigenständig ab.
                #
                # Ramzis Entscheidung dazu: "das ist gar kein Fehler, das kann
                # auch so bleiben, das ist einfach nur eine Designentscheidung."
                # Also bleibt es vorerst so und wird NICHT weiterverfolgt. Der
                # Block steht trotzdem hier, weil er semantisch richtig ist --
                # aber er ist unbewiesen, und das gehört dazugeschrieben statt
                # als erledigt zu gelten.
                if war_pausiert:
                    war_pausiert = False
                    stille = 0

                try:
                    ist_sprache = self.vad.is_speech(frame.tobytes(), RATE)
                except Exception:
                    continue

                # GERAEUSCHUNTERDRUECKUNG -- Ramzis Auftrag vom 14.08.2026:
                # "meine Ventilatoren sind an, ich glaube es kommt zu viel
                # Rauschen, und dadurch schickt er einfach nicht ab."
                #
                # Der Stimmenmelder steht auf der empfindlichsten Stufe und
                # haelt gleichmaessiges Rauschen fuer Sprache. Damit reisst die
                # Stille nie ab, die Redepause laeuft nie voll -- und sein Satz
                # bleibt haengen. Das ist kein Verstaendnisproblem, sondern eine
                # Schwelle, die zu tief liegt.
                #
                # Also eine zweite Bedingung: laut genug muss es AUCH sein.
                # Gemessen wird nicht absolut, sondern gegen den eigenen
                # Zimmerton -- die Ventilatoren laufen mal, mal nicht, und ein
                # fester Wert waere morgen wieder falsch. Der Grundpegel wird
                # laufend aus den Frames geschaetzt, die der Melder NICHT fuer
                # Sprache haelt: langsam, damit ein einzelner Huster ihn nicht
                # verschiebt.
                #
                # Der Regler sagt, wie viel lauter Sprache sein muss: 0 = aus
                # (nur der Melder, wie vorher), 100 = achtzehn Dezibel ueber dem
                # Grundpegel. Das ist viel -- wer bei 100 fluestert, wird nicht
                # gehoert. Deshalb ist der Vorschlag 35.
                pegel = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)) + 1.0)
                if not ist_sprache:
                    # Nur wenn nicht gesprochen wird, ist das der Zimmerton.
                    self._grundpegel = (0.98 * self._grundpegel + 0.02 * pegel
                                        if self._grundpegel else pegel)
                staerke = 0
                try:
                    from einstellungen import hole as _hole
                    staerke = _hole('geraeuschunterdrueckung') or 0
                except Exception:
                    pass
                if ist_sprache and staerke > 0 and self._grundpegel:
                    noetig = self._grundpegel * (10 ** ((staerke / 100.0 * 18.0) / 20.0))
                    if pegel < noetig:
                        ist_sprache = False

                if not ist_sprache:
                    # Die Folge reisst bei JEDEM stillen Frame -- auch dann,
                    # wenn gerade keine Aeusserung laeuft. Ohne diese Zeile
                    # war der Zaehler kein Folge-Zaehler, sondern eine Summe:
                    # verstreute Geraeusch-Frames ueber Minuten addierten sich
                    # und loeschten die Stille doch. Genau daran ist der erste
                    # Versuch gescheitert, obwohl die Idee richtig war.
                    sprach_folge = 0

                if ist_sprache:
                    # FING DIESE AEUSSERUNG AN, WAEHREND ICH GEREDET HABE?
                    #
                    # Dann bin ich es selbst, die da anfaengt -- mein
                    # Lautsprecher steht neben seinem Mikrofon. Ramzi am
                    # 15.08.2026, nachdem der Merker allein nicht reichte:
                    # "Du sollst nicht, waehrend du redest, anfangen
                    # aufzunehmen. Und weil du geredet hast, unterbrichst du
                    # dich selber gleichzeitig. Das macht keinen Sinn."
                    #
                    # Gemerkt wird NUR der Anfang, nicht jedes Bild dazwischen:
                    # faellt mitten in SEINEN Satz ein Satz von mir, gehoert
                    # der Satz trotzdem ihm und darf nicht verlorengehen. Wer
                    # zuerst da war, dem gehoert die Aeusserung.
                    if not in_sprache:
                        self._angefangen_waehrend_ich_rede = (
                            warteschlange.noor_spricht_gerade()
                            or (time.time() - self._ich_redete_zuletzt
                                < NACHHALL_SEK))
                    in_sprache = True
                    # NUR bei mehreren Frames hintereinander gilt die Stille als
                    # gebrochen -- sonst löscht ein einzelnes Spielgeräusch sie.
                    # Begründung samt Messung oben bei SPRACHE_FOLGE_MIN.
                    sprach_folge += 1
                    if sprach_folge >= SPRACHE_FOLGE_MIN:
                        stille = 0
                    sprach += 1
                    gesamt_sprach += 1
                    # Wie viel er insgesamt schon gesprochen hat, auch fuer den
                    # Mitlauscher sichtbar: der laeuft in einem eigenen Faden und
                    # sieht diese Zaehler sonst nicht. Er braucht die Zahl, um
                    # gar nicht erst zu fragen, ob ein Dreisekunden-Ausschnitt
                    # nach einem kurzen Befehl aussieht.
                    self._gesamt_sprach = gesamt_sprach
                    # UND DER MERKER GEHT WIEDER WEG, sobald klar ist, dass das
                    # kein kurzer Befehl mehr sein kann. Nicht nur "nicht mehr
                    # setzen": gesetzt wird er schon nach zwei Sekunden, und dann
                    # redet er noch drei Minuten weiter. Ohne diese Zeile bliebe
                    # die 0,8-Sekunden-Schwelle den ganzen Vortrag ueber stehen.
                    if self._kurz_erwartet and gesamt_sprach > KURZBEFEHL_MAX_SPRACH:
                        self._kurz_erwartet = False
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
                    sprach_folge = 0
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
                    #
                    # GESAMT_sprach, nicht sprach: `sprach` faengt bei jeder
                    # Satzpause wieder bei null an (siehe unten). Mit `sprach`
                    # haette JEDER Rest nach einer Satzpause als "kurzer Ruf"
                    # gegolten, und die Satzpausen-Schwelle (0,8 s) haette
                    # ploetzlich statt seiner eigenen Redepause gegriffen. Genau
                    # das hat Ramzi gefunden: "trotz 10 Sekunden nach ca. 2
                    # Sekunden abgeschickt."
                    kurz = gesamt_sprach <= KURZ_SPRACH_FRAMES or self._kurz_erwartet
                    schwelle = (min(KURZE_STILLE_FRAMES, self.stille_frames) if kurz
                                else self.stille_frames)

                    # Satzpause: den bisherigen Satz durchs GENAUE Modell schicken
                    # und zeigen -- ohne aufzuhoeren zuzuhoeren.
                    #
                    # Der eigentliche Fehler, den Ramzi am 01.08.2026 gefunden hat:
                    # "manchmal live, manchmal nicht, zufaellig". Ursache war eine
                    # Verwechslung zweier Zwecke. self._laufend/_hoer_kurz sind fuers
                    # WECKWORT gebaut -- ein 3-Sekunden-Fenster reicht, um "Noor" zu
                    # finden, mehr braucht das nicht. Genau dieses 3-Sekunden-Fenster
                    # landete aber auch als "Live-Vorschau" auf dem Streifen -- der
                    # wuchsende Satz wurde nie angezeigt, nur ein wechselnder
                    # Ausschnitt seines Endes.
                    #
                    # Bisher wurde das GENAUE Modell nur bei echter Stille
                    # (schwelle, meist mehrere Sekunden) oder wenn der Puffer
                    # ueberlief gerufen. Jetzt zusaetzlich bei einer kurzen
                    # Satzpause: mit den Modellen auf der Karte kostet ein
                    # Zwischenstueck nur noch Bruchteile einer Sekunde (gemessen
                    # 0,25-0,6 s), das ist guenstig genug fuer jeden Atemzug.
                    #
                    # SATZ_STILLE_FRAMES ist bewusst fest und NICHT sein Regler:
                    # der Regler ("stille_ms") entscheidet, wann er wirklich
                    # FERTIG ist -- das hier ist kein zweiter Regler, sondern nur
                    # ein Zwischenstand. Wirkt nur, wenn sie klar VOR seiner
                    # eigenen Schwelle liegt, sonst wuerden beide auf demselben
                    # Bild feuern.
                    if (not kurz and SATZ_STILLE_FRAMES < schwelle
                            and stille == SATZ_STILLE_FRAMES):
                        abgeben(endgueltig=False, gesprochene_bilder=sprach)
                        sprach = 0     # naechstes Stueck faengt bei null an

                    if stille >= schwelle:
                        # echte Stille -- er ist fertig
                        #
                        # UND ES STEHT IM PROTOKOLL, WELCHE SCHWELLE GEGOLTEN
                        # HAT. Genau das hat drei Wochen gefehlt: Ramzis Befund
                        # "es schickt mitten im Reden ab, obwohl die Redepause
                        # auf vier Sekunden steht" war nirgends nachpruefbar --
                        # das Protokoll zeigte den Satz, aber nicht, WARUM er
                        # als beendet galt. Eine Zeile je fertigem Satz ist
                        # billig; sie nicht zu haben hat gekostet.
                        print(f'[{time.strftime("%H:%M:%S")}] [Weckwort] fertig '
                              f'nach {stille * FRAME_MS / 1000:.1f}s Stille '
                              f'(Schwelle {schwelle * FRAME_MS / 1000:.1f}s, '
                              f'{"kurz" if kurz else "Redepause"}; gesprochen '
                              f'{gesamt_sprach * FRAME_MS / 1000:.1f}s, '
                              f'Kurzbefehl erwartet: '
                              f'{"ja" if self._kurz_erwartet else "nein"})',
                              flush=True)
                        abgeben(endgueltig=True, gesprochene_bilder=sprach)
                        in_sprache = False
                        stille = 0
                        sprach = 0
                        gesamt_sprach = 0
                        self._gesamt_sprach = 0
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
                frames, endgueltig, mein_echo = self._auftraege.get(timeout=0.4)
            except queue.Empty:
                continue
            self._arbeiter_rechnet.set()
            try:
                self._pruefe(frames, endgueltig, mein_echo)
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
            # Sein Platz in der Warteschlange -- siehe warteschlange.py.
            #
            # Sobald der Name erkannt ist oder das Folgefenster offen ist,
            # redet Ramzi mit mir, und niemand darf ihm dabei ins Wort fallen.
            # Erneuert jede 0,3 s, solange das gilt; explizit gelöscht wird der
            # Platz in assistant.py, sobald eine echte Stille eintrifft --
            # nicht erst, wenn diese Markierung von selbst verfaellt.
            #
            # ABER nicht, während ich selbst rede: sein Lautsprecher steht neben
            # seinem Mikrofon, das Ohr hört mich also mit und hielte das für ihn.
            # In diesem Fall wird erst unten entschieden, wenn der Text da ist
            # und sich sagen lässt, ob er von ihm stammt oder von mir.
            # `satz_laeuft` mit dazu, aus demselben Grund wie unten in
            # `_auswerten`: hat sein Satz einmal angefangen, laeuft er, bis er
            # fertig ist -- keine Uhr darf ihn mittendrin fuer beendet erklaeren.
            im_gespraech = erkannt or self.satz_laeuft or time.time() < self.folge_bis
            ich_rede = warteschlange.noor_spricht_gerade()
            if ich_rede:
                self._ich_redete_zuletzt = time.time()
            # Mein Nachhall zaehlt wie mein Reden -- siehe NACHHALL_SEK. Sonst
            # endet die Sperre genau in dem Moment, in dem mein letzter Ton am
            # Mikrofon erst ankommt.
            ich_rede = ich_rede or (time.time() - self._ich_redete_zuletzt
                                    < NACHHALL_SEK)
            if im_gespraech and not ich_rede:
                warteschlange.redet_merken(True)

            # SOLANGE ICH REDE, HOERE ICH NUR AUF EIN STOPPWORT -- SONST NICHTS.
            #
            # Das steht so weit oben, weil Ramzi am 15.08.2026 den Rest des
            # Problems gefunden hat, nachdem die Weckwort-Sperre schon stand:
            # "Meine konkrete Aufnahme ist die ganze Zeit an. Es hoert auch gar
            # nicht auf, sogar nachdem das abschickt."
            #
            # Er hatte recht, und der Grund lag ein paar Zeilen tiefer:
            # `_streifen_wachhalten` schickt den letzten Text noch einmal, damit
            # der Untertitel-Streifen nicht ablaeuft -- und das lief weiter,
            # waehrend ich sprach. Fuer ihn ist der Streifen die konkrete
            # Aufnahme; ein Streifen, der nie ablaeuft, ist eine Aufnahme, die
            # nie aufhoert. Ich hatte also die Tuer zugemacht und daneben das
            # Fenster offen gelassen.
            #
            # Deshalb endet die Runde hier vollstaendig: kein Wachhalten, kein
            # Weckwort, kein Mitschrieb, kein Zaehler. Nur der eine Zuruf, der
            # mich erreichen koennen MUSS, wird noch geprueft -- mit seinen
            # beiden Sicherungen gegen mein eigenes Echo (steht das Stoppwort in
            # meinem laufenden Satz, und kam es gerade aus meinem Lautsprecher).
            if ich_rede:
                if not self._arbeiter_rechnet.is_set():
                    zuruf = self._hoer_kurz(schnipsel)
                    if (zuruf and warteschlange.ist_stoppwort(zuruf)
                            and not warteschlange.ist_stoppwort(
                                warteschlange._mein_satz() or '')
                            and not warteschlange.war_kuerzlich_mein_satz(zuruf)):
                        print('[Weckwort] Stoppwort gehoert: %r' % zuruf[:40],
                              flush=True)
                        warteschlange.redet_merken(True)
                        self._melde(self.beim_unterbrechen)
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
                # Die Grenze spart Rechenzeit bei Gemurmel, das kein Ruf war.
                # Die frühere Ausnahme "solange ICH rede, gilt sie nicht" ist
                # weggefallen -- während ich rede, kommt der Ablauf gar nicht
                # mehr bis hierher (siehe oben).
                continue

            blicke += 1
            vorlaeufig = self._hoer_kurz(schnipsel)
            if not vorlaeufig:
                self._streifen_wachhalten(letzter, erkannt)
                continue
            letzter = vorlaeufig

            # Ab hier ist sicher, dass ich still bin -- die Runde waere sonst
            # oben schon beendet worden. Der Name startet die konkrete Aufnahme
            # deshalb ohne jede Rueckfrage, ob ich es selbst gewesen sein
            # koennte: ich kann mich nicht selbst gehoert haben, wenn ich nichts
            # sage. Genau das macht den frueheren Echo-Rateversuch an dieser
            # Stelle ueberfluessig -- eine Bedingung, die waehrend meines Redens
            # gar nicht mehr erreichbar ist, braucht keine Vermutung.
            if not erkannt and WECKWORT.search(vorlaeufig):
                erkannt = True
                self._melde(self.beim_erkennen)
            if erkannt or time.time() < self.folge_bis:
                self._melde(self.beim_mitschreiben, vorlaeufig)
                # Steckt in dem, was bisher zu hören war, schon ein kurzer
                # Befehl? Dann muss auf keine Denkpause gewartet werden.
                #
                # Aber nur, solange die Äußerung ÜBERHAUPT noch kurz sein kann.
                # `vorlaeufig` ist ein Ausschnitt von drei Sekunden, nicht der
                # ganze Satz -- ohne diese Grenze sagt irgendein Bruchstück aus
                # einem langen Vortrag „das war ein Befehl", und ab da gilt die
                # kurze Schwelle für den ganzen Rest. Begründung und Messung
                # stehen bei KURZBEFEHL_MAX_SPRACH.
                if (self.ist_kurzbefehl and not self._kurz_erwartet
                        and self._gesamt_sprach <= KURZBEFEHL_MAX_SPRACH):
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

    def _pruefe(self, puffer, endgueltig=True, mein_echo=False):
        """Segment genau transkribieren und entscheiden.

        `endgueltig=False` heißt: nur ein Zwischenstück, Ramzi redet noch
        weiter (siehe abgeben()). `beim_wecken` bekommt das mitgeteilt und
        entscheidet selbst, ob es sammelt oder ausführt -- hier wird nur
        gehört und weitergegeben, nicht bewertet.

        `mein_echo=True` heißt: dieses Stück hat angefangen, während mein
        eigener Lautsprecher lief. Dann bin ich es selbst und es wird
        verworfen -- Begründung unten bei der Prüfung."""
        # Ein reines "fertig"-Signal ohne Ton -- siehe abgeben(). Es gibt nichts
        # zu rechnen, aber der Assistent wartet auf genau diesen Anruf, um den
        # gesammelten Satz abzuschicken. Kommt nur vor, wenn vorher schon ein
        # Zwischenstueck geliefert wurde; wir sind also sicher im Gespraech und
        # brauchen die Weckwort-Pruefung unten nicht.
        if puffer is None:
            self._melde(self.beim_wecken, '', True)
            return
        if len(puffer) < 8:      # unter ~0,25 s ist es kein Wort, sondern ein Geräusch
            return
        audio = np.concatenate(list(puffer)).astype(np.float32) / 32768.0
        dauer_audio = len(puffer) * FRAME_MS / 1000

        # NEBEN der Transkription auf Lachen horchen, nicht davor und nicht
        # danach. Siehe lachen.py, warum es das ueberhaupt gibt.
        #
        # Der eigene Faden ist der ganze Punkt: gemessen kostet die Lach-Pruefung
        # 12-18 ms je Fenster, bei einer langen Aeusserung also bis zu ~0,2 s.
        # Whisper braucht auf denselben Ton 0,3-0,9 s. Nacheinander waeren das
        # 0,2 s, die Ramzi laenger auf seine Antwort wartet -- und wir haben am
        # 07.08. genau solche Zehntel aus der Bruecke herausgearbeitet. Parallel
        # kostet es ihn nichts, weil Whisper ohnehin laenger rechnet.
        _lachen = {}

        def _horch_auf_lachen():
            try:
                import lachen
                _lachen['fund'] = lachen.pruefe(audio)
            except Exception as e:
                print(f'[Lachen] Faden fehlgeschlagen: {e}')

        _lach_faden = threading.Thread(target=_horch_auf_lachen, daemon=True)
        _lach_faden.start()

        _start = time.time()
        try:
            # vad_filter + no_speech-Schwelle: dieselbe Absicherung wie beim
            # flink-Modell (_hoer_kurz oben), hier bisher gefehlt. Whisper
            # erfindet auf Stille Text -- am 03.08.2026 im Protokoll gesehen:
            # "Ich habe keine Erkrankungen. Ich erkenne sie. ..." mehrfach
            # wiederholt, mitten in einer echten Aeusserung. Ramzis Auftrag,
            # das Ohr solle "ein bisschen besser hoeren", ist genau das: ein
            # erfundener Satz zaehlte bisher als gehoertes Wort.
            segmente, _ = self.modell.transcribe(audio, language='de', beam_size=1,
                                                 vad_filter=True)
            text = ' '.join(s.text for s in segmente
                            if s.no_speech_prob < ERFINDUNGS_SCHWELLE).strip()
        except Exception as e:
            print(f'[Weckwort] Erkennung fehlgeschlagen: {e}')
            return
        # Die Messung, die in der Sitzung vom 31.07.2026 fehlte: wie lange
        # braucht das genaue Modell wirklich? Siehe STAND-Sprachschicht.md.
        dauer_rechnen = time.time() - _start
        # Der Name des Modells stand hier fest als "small" -- und das war
        # falsch: gemessen wird das GENAUE Modell. Am 01.08.2026 hat mich genau
        # diese Zeile in die Irre gefuehrt: ich habe das Wachmodell auf die
        # Grafikkarte geholt, im Protokoll weiter 11 s gesehen und kurz
        # geglaubt, der Umzug haette nichts gebracht. Ein Protokoll, das das
        # falsche Bauteil nennt, ist schlimmer als keins.
        # WAS gehoert wurde, gehoert ins Protokoll -- nicht nur wie lange es
        # gedauert hat.
        #
        # Ramzi am 02.08.2026 abends: "ich hab jetzt mindestens 30 mal deinen
        # Namen gerufen und du hoerst mich einfach nicht." Das Ohr lief, nahm
        # auch auf, und im Protokoll standen lauter Zeilen wie "1,2s Audio ->
        # 0,09s Rechenzeit" -- ohne ein einziges Wort davon. Damit war nicht
        # feststellbar, ob es ihn falsch verstanden, gar nichts verstanden oder
        # den Namen nur nicht erkannt hat. Eine Messung ohne das Ergebnis ist
        # keine Messung.

        # Jetzt erst auf die Lach-Pruefung warten -- sie lief die ganze Zeit
        # nebenher und ist in aller Regel laengst fertig. Der Deckel ist eine
        # Notbremse und kein erwarteter Fall: haengt das Geraeusch-Modell aus
        # irgendeinem Grund, wartet Ramzi lieber ohne Lach-Marker als gar nicht.
        _lach_faden.join(timeout=1.0)
        fund = _lachen.get('fund')

        lach_marker = ''
        lach_notiz = ''
        if fund:
            wert, klasse, lach_dauer = fund
            try:
                import lachen
                grenze = lachen.schwelle()
                if wert >= grenze:
                    lach_marker = lachen.marker(klasse)
            except Exception:
                grenze = None
            # JEDE Pruefung ins Protokoll, mit ihrer Zahl -- auch die 0,00.
            #
            # Erst stand hier `if wert > 0.05`, um das Protokoll ruhig zu
            # halten. Das hat sich in der ERSTEN Minute geraecht: Ramzi hat auf
            # Zuruf gelacht, im Protokoll stand keine Lach-Zeile, und damit war
            # nicht zu unterscheiden, ob (a) geprueft und nichts gefunden wurde
            # oder (b) die Pruefung gar nicht lief. Genau diese beiden Faelle
            # auseinanderhalten zu koennen ist der einzige Grund, warum hier
            # ueberhaupt etwas ins Protokoll geht.
            #
            # Eine Anzeige, die bei "nichts gefunden" schweigt, sieht genauso
            # aus wie eine kaputte. Das ist in diesem Ordner schon einmal teuer
            # geworden (die Tafel, die zwoelf Stunden still alte Zahlen zeigte).
            lach_notiz = (f' | lachen: {wert:.2f} ({klasse or "nichts"}'
                          + (f', Grenze {grenze:.2f}' if grenze else '')
                          + f', {lach_dauer * 1000:.0f} ms)'
                          + (' TREFFER' if lach_marker else ''))
        else:
            # Auch das ist eine Aussage, und zwar eine wichtige: entweder steht
            # die Schwelle auf 0 (dann ist es Absicht) oder das Modell fehlt.
            lach_notiz = ' | lachen: -- (nicht geprueft)'

        print(f'[{time.strftime("%H:%M:%S")}] [Weckwort] {dauer_audio:.1f}s Audio -> '
              f'{dauer_rechnen:.2f}s Rechenzeit '
              f'({self.modell_name}, genau, {getattr(self._modell, "device", "?")}) '
              f'| gehoert: {text!r}{lach_notiz}')

        # HAT DIESE AEUSSERUNG ANGEFANGEN, WAEHREND ICH REDETE? Dann bin ich es
        # selbst gewesen, und sie wird hier verworfen -- ohne Wortvergleich,
        # ohne Raten.
        #
        # Ramzis Regel vom 15.08.2026, und sie ist schaerfer als das, was der
        # Echo-Schutz je konnte: "Es soll niemals sein, dass du ein Echo von
        # dir erkennst und das trotzdem durchgeht und abgeschickt wird. Dann
        # soll es verworfen werden."
        #
        # Der Wortvergleich in `ist_mein_echo` konnte das nicht halten, weil er
        # den TEXT vergleicht: kommt mein Satz stark verhoert zurueck ("am
        # elften" -> "einen Elfen"), teilt er mit dem Original keine Woerter
        # mehr und rutscht durch. Der Zeitpunkt luegt dagegen nicht -- wer
        # anfaengt zu reden, waehrend mein Lautsprecher laeuft, ist mein
        # Lautsprecher.
        #
        # Das Stoppwort ist ausgenommen, und nur das: es ist der eine Zuruf,
        # der mich waehrend des Redens erreichen koennen MUSS, und er ist
        # bereits eine Zeile vorher gegen mein eigenes Echo abgesichert.
        if mein_echo and text and not warteschlange.ist_stoppwort(text):
            print(f'[{time.strftime("%H:%M:%S")}] [Weckwort] verworfen -- '
                  f'fing an, waehrend ich sprach (mein Echo): {text[:60]!r}',
                  flush=True)
            if endgueltig:
                self.satz_laeuft = False
            return

        # Der Marker haengt sich an den Satz, statt ihn zu ersetzen: „das war
        # wirklich lustig (lacht)" ist die Information, nicht „(lacht)" allein.
        #
        # Und wenn KEIN Wort erkannt wurde, ist der Marker der ganze Inhalt --
        # genau der Fall, um den es Ramzi geht. Er lacht, sagt nichts dazu, und
        # bisher kam bei mir gar nichts an. Das gilt aber nur MITTEN IM GESPRAECH
        # (`im_gespraech` unten): ein Lacher vor dem Fernseher darf mich nicht
        # anspringen -- dafuer sorgt dieselbe Tuer, durch die auch jedes Wort
        # muss.
        if lach_marker:
            text = f'{text} {lach_marker}'.strip() if text else lach_marker

            # BEWUSST KEINE MELDUNG AUF DER INSEL. Es gab sie eine Stunde lang,
            # weil Ramzi gefragt hatte, wo er das denn erkennen solle -- und er
            # hat sie sofort wieder abbestellt:
            #
            #   „Kannst du bitte, wenn ich lache, das nicht anzeigen auf der
            #    Insel? Ist unnötig und nervig, beim Lachen dann auf einmal eine
            #    Benachrichtigung zu erhalten."
            #
            # Er hat recht, und es ist eine Regel und kein Einzelfall: eine
            # Meldung UEBER eine beiläufige menschliche Regung unterbricht genau
            # die Regung, um die es geht. Lachen ist keine erledigte Aufgabe und
            # kein fertiger Download -- es will nicht quittiert werden.
            #
            # Sichtbar bleibt es trotzdem: der Marker steht im Satz und damit im
            # Untertitel, und die Zahl steht im Protokoll. Das genügt, weil er
            # nichts davon BESTAETIGEN muss.

        # Der Folgesatz braucht den Namen nicht mehr.
        #
        # Ramzi sagt oft erst nur "Noor", wartet auf das Zeichen und redet dann
        # weiter. Dieser zweite Satz enthält den Namen naturgemäß nicht -- ohne
        # diese Regel wäre er verloren, und genau das hat sich für ihn angefühlt
        # wie "sie hört mir nicht mehr zu".
        #
        # ODER es läuft schon ein Satz von ihm -- dann zählt die Uhr gar nicht
        # mehr mit. Ein angefangener Satz wird zu Ende gehört, egal wie lange
        # er dauert; Schluss ist erst bei seinem „fertig". Siehe `satz_laeuft`
        # im Kopf dieser Klasse: das ist Ramzis Regel vom 14.08.2026 und der
        # Grund, warum es hier ein Zustand ist und keine Frist.
        im_gespraech = time.time() < self.folge_bis or self.satz_laeuft

        # Nichts verstanden. Bei einem Zwischenstueck ist das folgenlos -- bei
        # einem FERTIG mitten im Gespraech nicht: dann haengt der gesammelte
        # Satz im Assistenten fest und wartet auf genau dieses Signal. Das ist
        # dieselbe Falle wie in abgeben(), nur eine Etage weiter unten.
        if not text:
            if endgueltig and im_gespraech:
                print(f'[{time.strftime("%H:%M:%S")}] [Weckwort] fertig ohne '
                      f'erkannten Text -- gesammelter Satz wird abgeschickt')
                self._melde(self.beim_wecken, '', True)
            if endgueltig:
                self.satz_laeuft = False
            return

        if WECKWORT.search(text) or im_gespraech:
            self._melde(self.beim_wecken, text, endgueltig)
            # Ab hier ist sein Satz angefangen -- und ab hier kann ihn keine
            # ablaufende Uhr mehr wegnehmen. Ein „fertig" schliesst ihn wieder.
            self.satz_laeuft = not endgueltig
        elif endgueltig:
            # Nicht angenommen und trotzdem fertig: das war jemand anders im
            # Raum oder der Fernseher. Der Merker darf davon nicht haengen
            # bleiben, sonst waere die naechste Bemerkung im Zimmer ein Auftrag.
            self.satz_laeuft = False


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
