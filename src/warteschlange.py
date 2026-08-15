"""Die Warteschlange fürs Reden -- wer redet gerade, dem redet niemand rein.

RAMZIS IDEE, WORTGETREU (31.07.2026): "es gibt eine Warteschlange für die
Sachen, die du sagst -- ich möchte, dass das Sprechen von mir selber auch zu
dieser Warteschlange gehört. Wenn ich gerade am Sprechen bin, bin ich gerade in
dieser Warteschlange, und wenn du dann etwas sagen möchtest, packst du es in
die Warteschlange und bist als nächstes dran."

Ausgelöst dadurch, dass der Stop-Hook mitten in seiner Beschreibung angefangen
hat, meine vorherige Antwort vorzulesen -- beide Stimmen übereinander.

Zwei Zustände, aus zwei ganz verschiedenen Quellen, die beide "Ramzi hat gerade
den Platz in der Warteschlange" bedeuten:

    Qalam nimmt ein Diktat auf           -- .aufnahme.lock (gab es schon)
    er ist mitten in einem Satz an mich  -- .ramzi-redet.lock (neu)

Absichtlich eine eigene, winzige Datei ohne schwere Importe (nur os, time):
gebraucht wird sie von JEDEM Prozess, der sprechen will -- auch vom Sprech-Hook,
der bei jeder Antwort neu startet und nicht erst numpy/sounddevice laden soll,
nur um nachzusehen, ob gerade Stille ist.
"""
import collections
import json
import os
import re
import tempfile
import time

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUFNAHME_SPERRE = os.path.join(PROJEKT, '.aufnahme.lock')
REDET_SPERRE = os.path.join(PROJEKT, '.ramzi-redet.lock')
SCHLAF_MERKER = os.path.join(PROJEKT, '.schlaeft.lock')

# Sicherheitsnetze, keine normalen Ablaufwerte -- der normale Ablauf räumt
# explizit auf (siehe aufnahme_endet() und assistant.py::_geweckt()). Diese
# Zahlen greifen nur, wenn genau das mal ausbleibt (Absturz, Ausnahme).
AUFNAHME_ALTER = 900     # 15 Minuten, wie zuvor bei der reinen Aufnahme-Sperre
REDET_ALTER = 1.0        # kurz: wird alle 0,3 s erneuert, solange er redet


def qalam_nimmt_auf():
    """Nimmt Qalam gerade ein Diktat auf?"""
    if not os.path.exists(AUFNAHME_SPERRE):
        return False
    try:
        if time.time() - os.path.getmtime(AUFNAHME_SPERRE) > AUFNAHME_ALTER:
            return False
    except OSError:
        return False
    return True


def aufnahme_beginnt():
    try:
        with open(AUFNAHME_SPERRE, 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass


def aufnahme_endet():
    try:
        os.remove(AUFNAHME_SPERRE)
    except OSError:
        pass


# --- Schlafe ich gerade? ---------------------------------------------------
#
# RAMZIS AUFBAU (01.08.2026, sein Wort: "wie ein Tony-Stark-Setup"): bin ich
# wach, spreche ich IMMER -- Rueckfragen, Zusammenfassungen, egal ob sein
# Auftrag gesprochen, diktiert oder getippt kam. Schlafe ich, ist alles still:
# keine Frage wird vorgelesen, keine Zusammenfassung, und das Ohr hoert nur
# noch auf das Aufwachen.
#
# Damit haengt das Reden nicht mehr daran, WIE er mich erreicht hat, sondern
# nur noch an einem einzigen Schalter. Der muss deshalb dort liegen, wo ihn
# jeder sehen kann: die Sprech-Hooks sind eigene PowerShell-Prozesse und der
# Tafel-Sammler auch -- kein einziger von ihnen kann in den Speicher des
# Assistenten schauen, wo `schlaeft` bisher allein stand.
#
# Fehlt die Datei, bin ich WACH. Das ist die richtige Vorgabe: weiss niemand
# etwas, soll ich ansprechbar sein und nicht stumm. Aufgeraeumt wird beim
# Start des Assistenten -- ein frischer Start ist wach, damit ein Absturz im
# Schlaf mich nicht dauerhaft verstummen laesst.


def schlaeft():
    """Schlafe ich gerade? Für JEDEN Prozess lesbar."""
    return os.path.exists(SCHLAF_MERKER)


def schlaf_merken(an):
    """Einschlafen oder aufwachen -- geht durch wake_word.schlaeft (Property)."""
    try:
        if an:
            with open(SCHLAF_MERKER, 'w') as f:
                f.write(str(time.time()))
        else:
            os.remove(SCHLAF_MERKER)
    except OSError:
        pass


def darf_sprechen():
    """Darf ich jetzt den Mund aufmachen? Die eine Frage, die die Hooks stellen."""
    return not schlaeft()


def ramzi_redet():
    """Ist er gerade mitten in einem gesprochenen Satz an mich?"""
    try:
        return (time.time() - os.path.getmtime(REDET_SPERRE)) < REDET_ALTER
    except OSError:
        return False


def redet_merken(an):
    """Setzen oder löschen -- wird von wake_word.py und assistant.py gerufen.

    Gesetzt: alle 0,3 s neu, solange der Mitlauscher den Namen erkannt hat oder
    das Folgefenster offen ist (siehe wake_word.py::_mitlauscher).
    Gelöscht: sofort, sobald assistant.py::_geweckt() eine ECHTE Stille meldet
    (endgueltig=True) -- nicht erst, wenn REDET_ALTER abgelaufen ist. Sonst
    bliebe mein eigener Reflex bis zu eine Sekunde stumm, nachdem er
    aufgehört hat zu reden."""
    try:
        if an:
            with open(REDET_SPERRE, 'w') as f:
                f.write(str(time.time()))
        else:
            os.remove(REDET_SPERRE)
    except OSError:
        pass


# --- WER HAT DAS WORT? Die eine Quelle, an der alles andere haengt ---------
#
# Bis zum 15.08.2026 gab es drei halbe Auskuenfte darueber, ob ich rede, und
# jede war in einer anderen Luecke falsch (gemessen, siehe FEHLER.md L4/L5):
#
#   der Sprecher      meldet "nein" zwischen zwei Saetzen desselben Redezugs
#   der Untertitel    kennt nur das laufende Stueck, geschaetzte Dauer
#   die Warteschlange sagt "nichts da", waehrend die Stimme noch erzeugt wird
#                     (gemessen 0,7-0,9 s zwischen Auftrag und erstem Ton)
#
# Alle drei stimmen fuer sich und ergeben zusammen "es ist still" -- genau in
# diesen Luecken ist die Aufnahme angesprungen und hatte meinen eigenen Satz
# drin. Ein Zustand, der zwischen zwei Zustaenden verschwindet, ist keiner.
#
# Also eine Klammer um den GANZEN Vorgang, von der Absicht bis zum Ende:
# gesetzt wird sie in voice_output.Sprecher.sprich(), sobald feststeht, dass
# gesprochen wird -- und zwar als Herzschlag, damit ein abgestuerzter Sprecher
# mich nicht dauerhaft taub macht. Eine Datei, weil mehrere Prozesse sprechen.
REDEZUG = os.path.join(PROJEKT, '.noor-redezug.lock')
REDEZUG_ALTER = 1.0      # Herzschlag alle 0,25 s -- danach gilt er als tot
NACHHALL_SEK = 2.5       # so lange ist mein Schall noch auf dem Weg zum Mikrofon


def noor_redezug_herzschlag():
    """Ich rede -- ab der Absicht, nicht erst ab dem ersten Ton."""
    try:
        with open(REDEZUG, 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass


def noor_still_seit():
    """Sekunden seit meinem letzten Herzschlag. Sehr gross, wenn nie einer kam.

    Die Datei wird bewusst NICHT geloescht: ihr Zeitstempel ist die Antwort auf
    "wie lange ist es her" -- und genau die braucht der Nachhall."""
    try:
        return time.time() - os.path.getmtime(REDEZUG)
    except OSError:
        return 1e9


def noor_hat_das_wort(nachhall=0.0):
    """Rede ich gerade -- egal aus welchem Prozess und in welcher Luecke?

    ODER mit der alten Auskunft, nie statt ihr: zwei Quellen, die beide nur
    "ja" sagen koennen, machen die Antwort sicherer. Faellt eine aus, bleibt
    die andere."""
    return (noor_still_seit() < REDEZUG_ALTER + nachhall
            or noor_spricht_gerade())


# --- Kuemmert sich jemand um die Lautstaerke? ------------------------------
#
# Es darf genau EINE Stelle geben, die die Musik wieder laut macht -- die
# Waechterin in assistant.py. Jede zweite Tuer laesst sie irgendwann zur
# falschen Zeit hochgehen, und fuer Ramzi sieht das aus wie Zufall (FEHLER.md,
# L1: "eine Bedingung, die nur an einer von zwei Tueren haengt, ist keine").
#
# Laeuft das Ohr aber gar nicht (Qalam allein), gibt es diese Waechterin nicht,
# und die Musik bliebe fuer immer leise. Deshalb sagt sie hier Bescheid, dass
# es sie gibt -- und nur wenn sie schweigt, springt die Notbremse ein.
WAECHTER = os.path.join(PROJEKT, '.lautstaerke-waechter.lock')
WAECHTER_ALTER = 2.0     # gemeldet alle 0,4 s


def waechter_lebt_melden():
    try:
        with open(WAECHTER, 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass


def waechter_lebt():
    try:
        return (time.time() - os.path.getmtime(WAECHTER)) < WAECHTER_ALTER
    except OSError:
        return False


# --- Mein eigenes Echo erkennen -------------------------------------------
#
# Das Problem, das Ramzi am 01.08.2026 nicht losgelassen hat: er redet, ich
# rede trotzdem weiter. Sein Bild dazu war richtig -- wer redet, hält den Platz,
# wie eine geöffnete Tabelle -- und mein Einwand hat ihn zu Recht gestört.
#
# Der Einwand stimmt trotzdem, und der Grund steht in wake_word.py: der Platz
# wird gesetzt, sobald der Name erkannt ist ODER das Folgefenster offen ist,
# und das ist 20 Sekunden lang der Fall. In genau diesen 20 Sekunden antworte
# ich. Sein Lautsprecher steht neben seinem Mikrofon, also hört das Ohr MICH,
# hält das für "da redet jemand" und erneuert den Platz. Prüfte ich den Platz
# während des Redens, würgte ich mich nach dem ersten Satz selbst ab.
#
# Die Lösung braucht aber keine Stimmerkennung, wie ich zuerst dachte, sondern
# nur etwas, das seit heute Nacht ohnehin da ist: ICH WEISS, WAS ICH GERADE
# SAGE. Der Untertitel-Streifen enthält genau den Satz samt Startzeit und
# gemessener Dauer. Was das Mikrofon hört, lässt sich also mit meinem eigenen
# Text vergleichen -- deckt es sich, war ich es selbst.
UNTERTITEL = os.path.join(tempfile.gettempdir(), 'noor-untertitel.json')


def _mein_satz():
    """Was ich GERADE sage -- oder None, wenn ich gerade nichts sage."""
    try:
        with open(UNTERTITEL, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return None
    if (d.get('wer') or '').lower() != 'noor':
        return None
    start, dauer = d.get('start'), d.get('dauer')
    if not start or not dauer:
        return None
    # Etwas Nachlauf: der Schall braucht seinen Weg, und das Ohr sieht immer
    # ein Stück Vergangenheit an.
    if not (start - 0.3 <= time.time() <= start + dauer + 1.0):
        return None
    text = d.get('text') or None
    # Nebenbei ins Kurzgedaechtnis. Hier und nur hier, damit es genau die
    # Saetze enthaelt, die auch wirklich ueber den Lautsprecher gegangen sind.
    _satz_merken(text)
    return text


def noor_spricht_gerade():
    return _mein_satz() is not None


def _worte(text):
    return {w for w in ''.join(
        c.lower() if c.isalnum() or c.isspace() else ' ' for c in text).split() if len(w) > 2}


# --- Was ich in den letzten Sekunden gesagt habe ---------------------------
#
# Ramzi am 08.08.2026, zweimal kurz hintereinander erlebt: ich breche mitten
# im Satz ab, ohne dass er etwas gesagt hat. Gemessen sind es ZWEI Fehler, die
# erst zusammen wirken.
#
#   1. Mein eigenes Wort wird zum Stoppwort verhoert. "Zurueckgenommen" kam
#      als "zur RUHE genommen" zurueck, "Inhaltlich" als "in HALT lich" --
#      genau die Luecke, gegen die im Muster oben extra Wortgrenzen stehen,
#      nur setzt das Modell die Luecke diesmal selbst hinein.
#   2. Das Echo kommt zu SPAET. Geprueft wurde gegen den Satz, den ich GERADE
#      spreche -- das Echo gehoert aber zum vorigen. Verglichen wurde also mit
#      dem falschen Text, und der war natuerlich sauber.
#
# Deshalb ein kurzes Gedaechtnis: die Saetze der letzten Sekunden, nicht nur
# der laufende. Es fuellt sich von selbst, weil die Lauschschleife _mein_satz()
# ohnehin staendig aufruft -- kein zweiter Erzeuger, der auseinanderlaufen
# koennte.
_ECHO_FENSTER = 12.0          # Sekunden zurueck
_letzte_saetze = collections.deque(maxlen=8)


def _satz_merken(text):
    if not text:
        return
    if _letzte_saetze and _letzte_saetze[-1][1] == text:
        return
    _letzte_saetze.append((time.time(), text))


def war_kuerzlich_mein_satz(gehoert, schwelle=0.5):
    """Ist das Gehoerte das Echo von etwas, das ich eben gesagt habe?

    Wie ist_mein_echo, aber ueber ein Zeitfenster statt nur ueber den
    laufenden Satz -- und OHNE dessen Notausgang fuer Stoppwoerter. Genau
    darum geht es hier: ob ein Stoppwort aus meinem eigenen Lautsprecher kam.

    Die Schwelle liegt niedriger als die 60 % von ist_mein_echo, weil ein
    gehoerter Fetzen ueber zwei meiner Saetze laufen kann und dann mit jedem
    einzelnen nur teilweise uebereinstimmt. Sein echter Zuruf bleibt klar
    darunter: "stopp" oder "Noor, warte" teilen mit meinem Satz nichts.
    """
    g = _worte(gehoert or '')
    if not g:
        return False
    jetzt = time.time()
    treffer = set()
    for zeit, text in _letzte_saetze:
        if jetzt - zeit <= _ECHO_FENSTER:
            treffer |= (g & _worte(text))
    return len(treffer) / len(g) >= schwelle


# --- Der Notausgang darf nie als Echo gelten -------------------------------
#
# Ramzis Befund vom 07.08.2026: "Noor, stopp" kam nicht durch. Der Grund steht
# unten in ist_mein_echo -- `kurz_erlaubt` hebt genau dann, wenn ich spreche,
# die Regel "unter fünf Wörtern ist nie Echo" auf. Das musste sein (mein
# eigener Lautsprecher würgt mir sonst den Satz ab), macht aber kurze Zurufe
# unmöglich, und "stopp" ist der kürzeste und wichtigste davon. Habe ich das
# Wort in der letzten Minute selbst gesagt, war sein Zuruf rechnerisch mein
# Echo.
#
# Bewusst eine Ausnahmeliste und KEIN Aufweichen der 60-Prozent-Schwelle: die
# Schwelle ist gemessen und richtig. Sie darf nur nicht über den Notausgang
# entscheiden. Die Liste bleibt klein und wörtlich -- was hier steht, kommt
# immer durch, und das soll nachlesbar sein.
#
# Wortgrenzen sind Pflicht: ohne sie träfe "halt" auch "Inhalt" und "enthält",
# und dann wäre jeder zweite Satz vom Echo-Schutz ausgenommen.
_STOPPWORT = re.compile(
    r'\b(stopp?t?|halt|sei still|hoer auf|hor auf|aufhoren|ruhe|warte)\b')


def _ohne_umlaute(text):
    for a, b in (('ä', 'a'), ('ö', 'o'), ('ü', 'u'), ('ß', 'ss')):
        text = text.replace(a, b)
    return text


def ist_stoppwort(gehoert):
    """Ist das ein Zuruf, mit dem er mich anhalten will?"""
    if not gehoert:
        return False
    flach = _ohne_umlaute(gehoert.lower())
    flach = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in flach)
    return bool(_STOPPWORT.search(' '.join(flach.split())))


# Bis zu wie vielen Woertern ein gehoertes Stoppwort ein ZURUF sein kann.
#
# Ramzis Test vom 15.08.2026, 16:46 -- und ich habe es mir selbst eingebrockt.
# Ich habe erklaert: "Ein fertiger Kurzbefehl wie STOPP oder wie spaet ist es
# kommt trotzdem sofort durch." Mein eigener Satz kam ueber sein Mikrofon
# zurueck, verhoert als "Ein einfacher Kurs befindet die Stopp od" -- und hat
# mich mitten im Wort abgeschaltet.
#
# Beide vorhandenen Sicherungen mussten dabei versagen, und das war absehbar:
#   * der Vergleich mit meinem LAUFENDEN Satz griff nicht, weil ich beim
#     Eintreffen des Echos laengst beim naechsten war
#   * der Wortvergleich ueber die letzten Sekunden kam auf 2 von 6 Woertern --
#     das Verhoeren hat die Uebereinstimmung zerstoert, genau wie bei
#     "am elften" -> "einen Elfen"
#
# Was die beiden Faelle wirklich trennt, ist nicht der Inhalt, sondern die
# LAENGE. Ein Zuruf ist von Natur aus kurz: "stopp", "hoer auf", "Noor, stopp".
# Ein Satz, in dem das Wort nur VORKOMMT, ist laenger -- ich REDE dann ueber
# Befehle, ich gebe keinen. Es ist derselbe Fehlertyp, den REFLEX_MAX_WOERTER
# und KURZBEFEHL_MAX_SPRACH laengst kennen; hier hat die Grenze gefehlt.
#
# Vier Woerter lassen jeden echten Zuruf durch und sperren den Vortrag darueber.
STOPP_MAX_WOERTER = 4


def ist_zuruf_stopp(gehoert):
    """Ist das ein ZURUF zum Anhalten -- und nicht das Wort in einem Satz?"""
    if not ist_stoppwort(gehoert):
        return False
    return len(str(gehoert or '').split()) <= STOPP_MAX_WOERTER


# Und die zweite Haelfte derselben Sache, die Ramzi selbst benannt hat:
# "du erkennst nicht, dass es von dir kommt, und dann stoppst du dich selber
# die ganze Zeit."
#
# Die Laengengrenze oben faengt den langen Vortrag ab. Sie faengt NICHT den
# Fall, dass ausgerechnet ein Drei-Sekunden-Ausschnitt meines eigenen Satzes
# kurz genug ist: "das Wort Stopp" sind drei Woerter und kaemen durch.
#
# Was hier hilft, ist das Einzige, was nicht luegt: ICH WEISS, WAS ICH GESAGT
# HABE. Nicht wie es zurueckkam -- das ist verhoert und darum wertlos --
# sondern was aus meinem Lautsprecher kam. Habe ich das Wort in den letzten
# Sekunden selbst gesagt, ist ein gehoertes Stoppwort waehrend meines Redens
# so gut wie sicher mein eigenes Echo.
#
# Der Preis ist klein und benannt: sage ich selbst "stopp" oder "warte", kann
# er mich fuenfzehn Sekunden lang nicht per ZURUF anhalten. Die rechte
# Strg-Taste und der Knopf auf der Tafel wirken weiter -- und die sind ohnehin
# die Wege, die nie danebengreifen koennen.
STOPP_EIGEN_FENSTER = 15.0


def _stoppwoerter(text):
    """WELCHE Haltewoerter stehen drin -- nicht nur ob eines drinsteht."""
    if not text:
        return set()
    flach = _ohne_umlaute(str(text).lower())
    flach = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in flach)
    return set(_STOPPWORT.findall(' '.join(flach.split())))


def habe_ich_stopp_gesagt(gehoert, sekunden=STOPP_EIGEN_FENSTER):
    """Kam GENAU DIESES Haltewort in den letzten Sekunden aus meinem Lautsprecher?

    Erst stand hier "habe ich IRGENDEIN Haltewort gesagt", und das war zu grob.
    Ramzis Test 3 am 15.08.2026: sechs seiner Rufe wurden unterdrueckt, weil in
    meinem eigenen Satz "in Ruhe" vorkam -- ein voellig normales Wort, das
    zufaellig auf der Liste steht. Er rief "Stopp", ich hatte "Ruhe" gesagt,
    und trotzdem galt sein Ruf als mein Echo. Erst nach fuenfzehn Sekunden kam
    er durch, und genau das hat er gemerkt: "ziemlich spaet auf jeden Fall".

    Ein Echo kann nur enthalten, was ich wirklich gesagt habe. Also wird
    verglichen, WELCHES Wort es war. Sage ich "in Ruhe" und er ruft "Stopp",
    kann das unmoeglich mein Echo sein.
    """
    seine = _stoppwoerter(gehoert)
    if not seine:
        return False
    try:
        with open(GESAGT, encoding='utf-8') as f:
            alt = json.load(f)
    except Exception:
        return False
    jetzt = time.time()
    meine = set()
    for e in alt:
        if jetzt - e.get('t', 0) < sekunden:
            meine |= _stoppwoerter(e.get('text', ''))
    return bool(meine & seine)


def ist_mein_echo(gehoert, kurz_erlaubt=False):
    """Ist das, was das Ohr gehört hat, in Wahrheit meine eigene Stimme?

    Verglichen wird über die Wörter, nicht Zeichen für Zeichen: das schnelle
    Modell verhört sich, und ein einziger falscher Buchstabe darf die Erkennung
    nicht kippen. Kurze Wörter fliegen raus, weil "und", "das", "ist" in jedem
    Satz vorkommen und sonst jede Äußerung wie mein Echo aussähe.
    """
    if not gehoert:
        return False

    # Der Notausgang zuerst -- siehe ist_stoppwort(). Lieber einmal mein
    # eigenes Echo als Stopp missverstehen (dann bin ich kurz still, und das
    # kostet ihn nichts) als seinen Zuruf überhören, während ich rede.
    #
    # ABER: nur, wenn es nicht klar mein eigenes Echo ist. Ramzi am
    # 08.08.2026, zweite Stelle mit demselben Grundfehler wie ist_stoppwort()
    # weiter unten -- "Jetzt der Test, und ich baue absichtlich genau die"
    # brach hier ab, obwohl der Fix fuer ist_stoppwort schon griff. Ohne
    # war_kuerzlich_mein_satz() galt jedes verhoerte Stoppwort im eigenen Text
    # weiter automatisch als "Ramzi hat übernommen".
    if ist_stoppwort(gehoert) and not war_kuerzlich_mein_satz(gehoert):
        return False

    g = _worte(gehoert)
    if not g:
        return False

    meiner = _mein_satz()
    if meiner and len(g & _worte(meiner)) / len(g) >= 0.6:
        return True

    # Der Rückblick auf die letzte Minute -- Begründung in merke_gesagt().
    #
    # Erst ab fünf Wörtern, und das ist die Sicherung, auf die es ankommt:
    # ein kurzer Zuruf wie „Noor, stopp" oder „ja, mach" muss IMMER
    # durchkommen, auch wenn ich dieselben Wörter gerade selbst gesagt habe.
    # Ramzi wollte ausdrücklich weiter unterbrechen können -- lieber einmal
    # mein eigenes Echo bearbeiten als ihn überhören.
    # `kurz_erlaubt` hebt diese Sperre auf, und zwar genau dort, wo ich SELBST
    # gerade spreche (wake_word._mitlauscher).
    #
    # Ramzis Befund vom 03.08.2026, den ich vorher nicht gesehen hatte: beim
    # Vorlesen bricht mein eigener Lautsprecher meinen Satz ab. Der Mitlauscher
    # hört alle paar Sekunden einen kurzen Fetzen -- zwei, drei Wörter -- und
    # der fällt durch beide Netze: `_mein_satz()` hält nur das gerade laufende
    # Stück (der Schall hinkt hinterher, es passt oft nicht), und der Rückblick
    # unten greift erst ab fünf Wörtern. Also galt mein eigenes Echo als "Ramzi
    # hat übernommen", und ich habe mich selbst gestoppt.
    #
    # Während ich rede, ist meine Stimme nachweislich im Raum. Ein
    # Wortvergleich, der dann anschlägt, ist viel wahrscheinlicher Echo als
    # Zufall -- deshalb darf die Längensperre dort fallen. Sie bleibt überall
    # sonst: ein kurzer Zuruf muss durchkommen.
    if len(g) < 5 and not kurz_erlaubt:
        return False
    frueher = _zuletzt_gesagt()
    return bool(frueher) and len(g & _worte(frueher)) / len(g) >= 0.6


GESAGT = os.path.join(PROJEKT, '.noor-gesagt.json')
GESAGT_ALTER = 60.0      # so lange kann ein Satz von mir noch zurückkommen

# Dasselbe noch einmal zum LESEN. Die Datei oben ist eine Arbeitsdatei: sie
# hält nur die letzte Minute und sieht aus wie Maschinentext.
#
# Ramzis Auftrag vom 03.08.2026: was ich zwischendurch sage, steht nirgends zum
# Nachlesen -- die Zusammenfassung am Ende landet im Chat, die Zwischenrufe nur
# in der Luft und im Untertitel. Hat er gerade weggeschaut, ist es weg. Also
# hier, und aufmachen kann er es mit „mach mir auf, was du gesagt hast".
GESAGT_LOG = os.path.join(PROJEKT, 'noor-gesagt.log')


def merke(text, art='GESAGT'):
    """Aufschreiben, was aus mir kam -- und was NICHT aus mir kam.

    Drei Arten, und der Unterschied ist der ganze Punkt (Ramzis eigener
    Vorschlag vom 07.08.2026, und er ist der richtige):

        GESAGT       kam wirklich aus dem Lautsprecher
        UNGESAGT     ist verfallen oder wurde beim Stopp weggeraeumt
        ABGEBROCHEN  lief, wurde aber mitten im Satz gestoppt

    Vorher stand hier nur, was wirklich zu hoeren war. Damit spielte "nochmal"
    zwangslaeufig das Falsche: den Satz, den er schon kennt -- und nie den, der
    verschluckt wurde. Jetzt steht beides da, unterscheidbar markiert, und
    noor-nochmal.ps1 kann das Fehlende holen.

    Nur GESAGT zaehlt fuer den Echo-Vergleich unten. Was nie gesprochen wurde,
    war auch nie im Raum und kann darum nicht als mein Echo zurueckkommen --
    stuende es im Vergleich, wuerde ich Ramzi faelschlich ueberhoeren.

    WARUM ES NICHT REICHT, NUR AUF DEN LAUFENDEN SATZ ZU SCHAUEN -- der Fehler

    WARUM ES NICHT REICHT, NUR AUF DEN LAUFENDEN SATZ ZU SCHAUEN -- der Fehler
    vom 03.08.2026, der mir meine eigene Antwort als Auftrag zurückgeschickt
    hat:

    Das Ohr wertet einen ganzen Block aus, wenn Ramzi eine Pause macht -- im
    Fall des Fehlers waren das 14,1 Sekunden am Stück. Ausgewertet wird also
    NACH dem Sprechen, nicht währenddessen. `_mein_satz()` weiß aber nur, was
    ich GERADE sage; eine Sekunde später gibt es None zurück, und mein eigener
    Satz sah aus wie seiner.

    Dazu kam ein zweiter Grund: Piper spricht in Stücken, und der Untertitel
    hält immer nur das laufende Stück. Selbst im richtigen Moment hätte der
    Vergleich also nur einen Bruchteil dessen gesehen, was zu hören war -- zu
    wenig für die Sechzig-Prozent-Schwelle.

    Also wird hier mitgeschrieben, was in der letzten Minute aus mir kam. Eine
    Datei und keine Variable im Speicher, weil Sprechen und Hören zwei
    getrennte Prozesse sind.
    """
    if not text:
        return
    jetzt = time.time()
    if art == 'GESAGT':
        try:
            with open(GESAGT, encoding='utf-8') as f:
                alt = json.load(f)
        except Exception:
            alt = []
        neu = [e for e in alt if jetzt - e.get('t', 0) < GESAGT_ALTER]
        neu.append({'t': jetzt, 'text': text})
        try:
            # Über eine Zwischendatei, damit der lesende Prozess nie eine halb
            # geschriebene Datei erwischt -- dort würde json still scheitern und
            # der Echo-Schutz wäre lautlos aus.
            h, tmp = tempfile.mkstemp(dir=PROJEKT, suffix='.json')
            with os.fdopen(h, 'w', encoding='utf-8') as f:
                json.dump(neu[-40:], f, ensure_ascii=False)
            os.replace(tmp, GESAGT)
        except Exception:
            pass
    # Zum Nachlesen, anhängend und mit Uhrzeit. Darf nie der Grund sein, warum
    # ein Satz nicht gesprochen wird -- deshalb sein eigenes try.
    #
    # Der Zeitstempel bleibt vorn und im selben Format: noor-nochmal.ps1 und
    # noor-protokolle-kuerzen.ps1 lesen diese Datei, und eine Marke am Zeilen-
    # anfang haette beiden den Boden weggezogen.
    try:
        einzeilig = ' '.join(text.split())
        with open(GESAGT_LOG, 'a', encoding='utf-8') as f:
            f.write('%s  %-11s %s\n'
                    % (time.strftime('%Y-%m-%d %H:%M:%S'), art, einzeilig))
    except OSError:
        pass


def merke_gesagt(text):
    """Der alte Name -- es gibt ihn noch, weil voice_output ihn benutzt."""
    merke(text, 'GESAGT')


def _zuletzt_gesagt():
    """Alles, was in der letzten Minute aus mir kam, als ein Text."""
    try:
        with open(GESAGT, encoding='utf-8') as f:
            alt = json.load(f)
    except Exception:
        return ''
    jetzt = time.time()
    return ' '.join(e.get('text', '') for e in alt
                    if jetzt - e.get('t', 0) < GESAGT_ALTER)


def ramzi_ist_dran():
    """Ist gerade sein Platz in der Warteschlange -- soll ich also warten?"""
    return qalam_nimmt_auf() or ramzi_redet()


# Nach seinem letzten Wort erst einmal Ruhe halten.
#
# Ramzi macht Denkpausen MITTEN im Satz -- er denkt beim Reden, das steht so
# auch in seinem Gedächtnis-Eintrag. Bisher reichte eine Atempause, damit ich
# losgelegt habe; für ihn war das Reinreden, denn er war ja nicht fertig.
# 0,6 s ist die Spanne, in der ein Mensch nach Luft holt, ohne den Faden
# abzugeben -- lang genug, um seine Pausen zu überstehen, kurz genug, dass eine
# Antwort noch als Antwort ankommt.
NACHLAUF = 0.6

# Wann ein Merker als kaputt gilt.
#
# Der Notausgang muss bleiben (hängt ein Merker fest, wäre ich sonst für immer
# stumm), aber er saß an der falschen Stelle: nach 20 Sekunden wurde einfach
# weitergeredet -- mitten in einen Satz, der 30 bis 60 Sekunden dauert. Im
# Ohr-Protokoll stand das dreimal in Folge, und genau das erlebt er als "du
# redest mir rein".
#
# Drei Minuten ist keine Wartezeit, sondern eine Diagnose: so lange redet
# niemand am Stück. Bis dahin ist ein Auftrag ohnehin längst verfallen (siehe
# sprechzentrale.FRIST) -- der Notausgang ist damit das, was er sein soll, ein
# Netz gegen einen Fehler, und keine Regel für den Alltag.
KAPUTT_NACH = 180.0


def ruhig_seit():
    """Wie lange ist er schon still? Sehr groß, wenn er es lange ist."""
    if qalam_nimmt_auf():
        return 0.0
    try:
        return time.time() - (os.path.getmtime(REDET_SPERRE) + REDET_ALTER)
    except OSError:
        return 1e9


# Wie lange seine Stille dauern muss, bevor ich sie als "fertig" lese.
#
# Ramzis Befund vom 08.08.2026 bei Test 3: "ich habe aufgehoert zu reden und
# darauf gewartet, dass es abschickt. Aber weil ich meine Redepause auf mehrere
# Sekunden habe, muss ich halt warten -- und genau da hast du mir
# reingesprochen, obwohl das noch nicht abgeschickt war."
#
# Das ist der Kern: seine Stille ist NICHT das Ende seines Zuges. Qalam sammelt
# nach dem letzten Wort noch `stille_ms` lang, bevor die Aeusserung ueberhaupt
# abgeschickt wird. In diesem Fenster ist er dran, auch wenn kein Ton kommt --
# er wartet ja selbst darauf. Mit festen 0,6 s musste ich hineinfallen, und
# zwar umso sicherer, je groesser er den Regler stellt.
#
# Also folgt der Nachlauf seinem eigenen Regler, plus dem alten Puffer fuer die
# Zeit vom Abschicken bis zum Ankommen.
def noetige_ruhe():
    try:
        import einstellungen
        stille = float(einstellungen.hole('stille_ms') or 0) / 1000.0
    except Exception:
        stille = 0.0
    return max(NACHLAUF, stille + NACHLAUF)


def er_ist_fertig(nachlauf=None):
    """Darf ich jetzt anfangen -- ist er still UND war es lange genug still?"""
    return ruhig_seit() >= (noetige_ruhe() if nachlauf is None else nachlauf)


def warte_bis_er_fertig_ist(nachlauf=None):
    """Blockiert, bis Ramzi seinen Platz in der Warteschlange abgegeben hat.

    Gibt True zurück, wenn er wirklich fertig ist, und False, wenn ein Merker
    festhängt und deshalb trotzdem gesprochen wird.

    Ohne Obergrenze im gewohnten Sinn: gewartet wird, solange er redet, Punkt.
    Was zu lange wartet, wird nicht doch noch gesprochen, sondern verfällt --
    das entscheidet die Sprech-Zentrale, nicht diese Funktion. Hier bleibt nur
    das Netz gegen einen kaputten Merker (KAPUTT_NACH).
    """
    begonnen = time.time()
    musste_warten = not er_ist_fertig(nachlauf)
    while True:
        while ramzi_ist_dran():
            if time.time() - begonnen > KAPUTT_NACH:
                print('[Warteschlange] seit %.0f s blockiert -- der Merker gilt '
                      'als kaputt, ich rede.' % (time.time() - begonnen),
                      flush=True)
                return False
            time.sleep(0.1)
        # Er ist still. Jetzt den Nachlauf abwarten -- und wenn er in dieser
        # Zeit wieder anfängt, war es nur eine Denkpause und es geht von vorn
        # los.
        if er_ist_fertig(nachlauf):
            break
        time.sleep(0.05)

    if musste_warten:
        print('[Warteschlange] %.1fs gewartet, er war fertig'
              % (time.time() - begonnen), flush=True)
    return True
