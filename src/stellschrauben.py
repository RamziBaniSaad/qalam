"""Ramzis Stellschrauben per Sprache — ohne Umweg über Noor im Chat.

Sein Auftrag vom 01.08.2026, wörtlich: "ich möchte, dass du die Möglichkeit
hast, dass du für mich die Werte änderst, die ich selber ändern kann. […] Das
wäre dann aber nichts, was über Claude geht, in deinen Nachdenkenmodus quasi,
sondern würde zu diesen Kurzbefehlen gehören, die dich gar nicht erreichen im
Chat." Und der Grund: "das kostet gar nichts, weil das keine Tokens braucht."

Beispiel aus seiner Nachricht: bevor er zu einem langen Diktat ansetzt, sagt er
"mach mal meine Redepausen auf 10 Sekunden" — und kann sofort losreden, statt
erst die Tafel zu suchen.

ZWEI ORTE, und das ist der ganze technische Kern:

    noor-einstellungen.json   Tempo, Lautstärke, Tonzeichen, Redepause
                              -> einstellungen.py, wird bei JEDEM Satz neu
                                 gelesen. Wirkt sofort, ohne Neustart.

    src/config.yaml           recording_timer: Aufnahme-Timer, orange, rot,
                              automatisch abschicken, Countdown-Töne, Startton
                              -> ConfigManager liest das nur beim START. Der
                                 Assistent hier ist ein ANDERER Prozess als
                                 Qalam, ein Aufruf von set_config_value() käme
                                 dort also nie an. Deshalb wird die Datei
                                 geschrieben, und status_window.py liest den
                                 Abschnitt frisch von der Platte nach.

Ausdrücklich NICHT dabei (seine Entscheidung): Post-Processing, Recording
Options, Modell-Optionen. "Ich denke nicht, dass ich irgendwas bei Post
Processing […] ändern wollen würde. Und wenn ich das mal wollen würde, dann
würde es sich lohnen, weil das halt so selten wäre" -- solche Fälle sagt er mir
im Chat, und dann mache ich es von Hand. Eine Liste, die alles kann, wird zu
einer Liste, die falsch greift.

Prüfen ohne Mikrofon (und das ist der Weg, auf dem ich es getestet habe):

    python -m src.stellschrauben --probe          # Tabelle, ändert NICHTS
    python -m src.stellschrauben "mach die redepause auf 2 sekunden"
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import einstellungen                              # noqa: E402
import verhoerer                                   # noqa: E402

SRC = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(SRC, 'config.yaml')

# Bis zu wie vielen Wörtern eine Äußerung als Stellschrauben-Befehl gilt.
#
# Die teuerste Lektion des 31.07.2026 steht in assistant.py: Ramzi hat vier
# Minuten geredet und bekam die Uhrzeit zurück, weil irgendwo in vier Minuten
# Rede das Bruchstück "uhr ist" steckte. Dasselbe droht hier -- in einem langen
# Auftrag kommt leicht mal "Tempo" und irgendeine Zahl vor.
#
# 16 statt der 10 aus assistant.py, weil diese Befehle von Natur aus länger
# sind: "stell die Sekunden für orange bei der Aufnahme mal auf 90" sind 11
# Wörter. Und sie sind gleichzeitig viel enger -- es braucht ein Stichwort UND
# eine Zahl oder ein An/Aus. Beides zufällig in 16 Wörtern zu treffen ist
# unwahrscheinlich; beides in vier Minuten zu treffen wäre sicher.
STELL_MAX_WOERTER = 16


# ----------------------------------------------------------------- Zahlen
#
# Warum überhaupt selbst rechnen: Whisper schreibt "2000" mal als Ziffern und
# mal als "zweitausend" -- dasselbe gesprochene Wort, zwei Schreibweisen. Wer
# nur Ziffern versteht, versteht Ramzi in der Hälfte der Fälle nicht.
_WORTZAHL = {
    'null': 0, 'ein': 1, 'eine': 1, 'einen': 1, 'eins': 1, 'zwei': 2, 'zwo': 2,
    'drei': 3, 'vier': 4, 'funf': 5, 'sechs': 6, 'sieben': 7, 'acht': 8,
    'neun': 9, 'zehn': 10, 'elf': 11, 'zwolf': 12, 'dreizehn': 13,
    'vierzehn': 14, 'funfzehn': 15, 'sechzehn': 16, 'siebzehn': 17,
    'achtzehn': 18, 'neunzehn': 19, 'zwanzig': 20, 'dreissig': 30,
    'vierzig': 40, 'funfzig': 50, 'sechzig': 60, 'siebzig': 70,
    'achtzig': 80, 'neunzig': 90,
}

# Halbe Zahlen: er sagt "anderthalb Minuten", nicht "90 Sekunden".
_HALBE = {
    'einhalb': 0.5, 'anderthalb': 1.5, 'eineinhalb': 1.5, 'zweieinhalb': 2.5,
    'dreieinhalb': 3.5, 'viereinhalb': 4.5, 'funfeinhalb': 5.5,
}


def _wortzahl(wort):
    """Ein einzelnes Zahlwort in eine Zahl. None, wenn es keines ist."""
    if wort in _HALBE:
        return _HALBE[wort]
    if wort in _WORTZAHL:
        return _WORTZAHL[wort]
    # "zweitausend", "eintausend", "tausend", "zweihundert", "hundert"
    for endung, faktor in (('tausend', 1000), ('hundert', 100)):
        if wort.endswith(endung):
            rest = wort[:-len(endung)]
            vor = _WORTZAHL.get(rest, 1) if rest else 1
            return vor * faktor
    # "einundzwanzig", "funfundvierzig"
    if 'und' in wort:
        links, _, rechts = wort.partition('und')
        a, b = _WORTZAHL.get(links), _WORTZAHL.get(rechts)
        if a is not None and b is not None:
            return a + b
    return None


def _zahl(worte):
    """Die erste Zahl in der Äußerung. Ziffern und Zahlwörter gleichberechtigt.

    "eins komma drei" wird zu 1.3 -- Whisper schreibt Kommazahlen mal so, mal
    als "1,3" (und dann steht hier schon "1.3", siehe _glaette)."""
    for i, w in enumerate(worte):
        wert = None
        if re.fullmatch(r'\d+(?:\.\d+)?', w):
            wert = float(w)
        else:
            wert = _wortzahl(w)
        if wert is None:
            continue
        # "komma" dahinter? Dann gehört die nächste Zahl hinter das Komma.
        if worte[i + 1:i + 2] == ['komma']:
            nachkomma = ''
            for w2 in worte[i + 2:]:
                if re.fullmatch(r'\d+', w2):
                    nachkomma += w2
                elif _wortzahl(w2) is not None and _wortzahl(w2) < 10:
                    nachkomma += str(int(_wortzahl(w2)))
                else:
                    break
            if nachkomma:
                return float(f'{int(wert)}.{nachkomma}')
        return wert
    return None


# ----------------------------------------------------------------- An / Aus
_AN = ('an', 'ein', 'einschalten', 'anschalten', 'aktivier', 'aktiviere',
       'aktivieren', 'anmachen', 'wieder an', 'zuruck', 'benutze')
_AUS = ('aus', 'ausschalten', 'abschalten', 'deaktivier', 'deaktiviere',
        'deaktivieren', 'ausmachen', 'weg', 'stumm', 'still', 'nicht mehr',
        'lass', 'ohne', 'abstellen', 'schweig')


def _schalter(text, worte):
    """An oder aus? None, wenn nichts davon gesagt wurde.

    AUS wird zuerst geprüft, weil "mach das aus" beide Wörter enthält: "mach"
    ist harmlos, aber "an" steckt als eigenes Wort in vielen Sätzen ("mach die
    Töne an") -- und "aus" ist das eindeutigere Signal. Wer "aus" sagt, meint
    aus."""
    for wort in _AUS:
        if wort in worte or f' {wort} ' in f' {text} ':
            return False
    for wort in _AN:
        if wort in worte or f' {wort} ' in f' {text} ':
            return True
    return None


# ------------------------------------------------------------- Vorbereitung
def _glaette(text):
    """Wie assistant.normalisiere, mit einem entscheidenden Unterschied.

    Dort werden ALLE Satzzeichen zu Leerzeichen -- aus "1,25" wird "1 25", und
    damit wäre jede Kommazahl kaputt, bevor sie hier ankommt. Also wird ein
    Komma oder Punkt ZWISCHEN ZIFFERN vorher zum Dezimalpunkt gemacht und
    überlebt."""
    t = (text or '').lower()
    for a, b in (('ä', 'a'), ('ö', 'o'), ('ü', 'u'), ('ß', 'ss'),
                 ('ae', 'a'), ('oe', 'o'), ('ue', 'u')):
        t = t.replace(a, b)
    t = re.sub(r'(\d)[.,](\d)', r'\1.\2', t)
    t = re.sub(r'[^\w. ]+', ' ', t)
    t = re.sub(r'(?<!\d)\.|\.(?!\d)', ' ', t)      # Punkte, die keine Zahl sind
    t = re.sub(r'\s+', ' ', t).strip()
    # Bekannte Verhörer korrigieren -- siehe verhoerer.py.
    return verhoerer.korrigiere(t)


# -------------------------------------------------------------- Einheiten
def _einheit(text):
    """Welche Einheit hat Ramzi genannt? Bestimmt, wie die Zahl zu lesen ist."""
    if 'millisekund' in text or ' ms' in f' {text}':
        return 'ms'
    if 'minut' in text:
        return 'min'
    if 'sekund' in text or ' sek' in f' {text}':
        return 's'
    if 'prozent' in text or 'prozentig' in text:
        return '%'
    return None


def _rasten(wert, schritt):
    """Auf das Raster des Tafel-Reglers legen.

    Ein `input type=range` mit `step=0.05` kann 1.33 nicht darstellen -- der
    Browser rundet, schreibt den gerundeten Wert zurück in die Datei, und dann
    steht dort etwas anderes als ich gesagt habe. Also runde ich gleich hier:
    Datei, Regler und meine Ansage sind damit derselbe Wert."""
    if not schritt:
        return wert
    gerastet = round(wert / schritt) * schritt
    return round(gerastet, 6)


def _grenzen(wert, klein, gross):
    return max(klein, min(gross, wert))


# ------------------------------------------------------------- Aussprache
def _sprich_zahl(x):
    """1.3 -> "1,3", 2.0 -> "2". Deutsches Komma, keine leeren Nachkommastellen."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f'{x:.2f}'.rstrip('0').rstrip('.').replace('.', ',')


def _sprich_dauer(sekunden):
    """Sekunden so ansagen, wie ein Mensch sie sagen würde."""
    s = float(sekunden)
    # Minuten nur bei glatten Minuten ab zwei. 90 Sekunden sind "90 Sekunden"
    # und nicht "1,5 Minuten" -- er hat den Wert in Sekunden eingestellt, also
    # bekommt er ihn in Sekunden zurück.
    if s >= 120 and abs(s / 60 - round(s / 60)) < 1e-9:
        return f'{int(round(s / 60))} Minuten'
    return '1 Sekunde' if abs(s - 1) < 1e-9 else f'{_sprich_zahl(s)} Sekunden'


# ------------------------------------------------------- Wohin geschrieben wird
def _yaml_setze(schluessel, wert):
    """Einen Wert im Abschnitt recording_timer von config.yaml ändern.

    Laden, ändern, ganz zurückschreiben -- nicht per Textersetzung. Die Datei
    enthält u.a. Ramzis mehrzeiligen System-Prompt; darin herumzuschneiden wäre
    genau die Art Trick, die irgendwann einen halben Prompt frisst.
    """
    import yaml
    try:
        with open(CONFIG, encoding='utf-8') as f:
            daten = yaml.safe_load(f) or {}
    except OSError:
        daten = {}
    daten.setdefault('recording_timer', {})[schluessel] = wert
    with open(CONFIG, 'w', encoding='utf-8') as f:
        yaml.dump(daten, f, default_flow_style=False, allow_unicode=True)


def _yaml_hole(schluessel, ersatz=None):
    import yaml
    try:
        with open(CONFIG, encoding='utf-8') as f:
            daten = yaml.safe_load(f) or {}
        wert = (daten.get('recording_timer') or {}).get(schluessel)
        return ersatz if wert is None else wert
    except Exception:
        return ersatz


# --------------------------------------------------------------- Die Tabelle
#
# Je Eintrag: die Bruchstücke, an denen Ramzi gemeint sein könnte, wohin der
# Wert geht, und was ich zurücksage.
#
# BRUCHSTÜCKE, NICHT GANZE SÄTZE -- dieselbe Entscheidung wie bei den Reflexen
# in assistant.py: "Er will reden, nicht ein Kommando aufsagen." Sein Auftrag
# sagt es noch mal ausdrücklich: "Du musst einfach nur aufpassen, dass du es von
# der Formulierung her auch lockerer erlaubst."
#
# Reihenfolge = Vorrang. Das Spezifischere steht oben: "Sekunden für orange"
# muss vor "Aufnahme-Timer" greifen, sonst schaltet ein Satz über die
# Orange-Grenze den ganzen Timer ab.

# (bruchstuecke, art, ort, schluessel)
#   art: 'zahl' | 'schalter' | 'probe' | 'stand'
STIMME = 'stimme'          # noor-einstellungen.json
TIMER = 'timer'            # config.yaml, recording_timer

TABELLE = [
    # --- Qalams Aufnahme-Timer: erst die Zahlen, dann die Schalter ---------
    (['orange'], 'zahl', TIMER, 'orange_seconds'),
    (['rot ', ' rot', 'roten', 'rote '], 'zahl', TIMER, 'red_seconds'),

    (['countdown', 'count down', 'runterzahl', 'mitzahl'],
     'schalter', TIMER, 'countdown_sounds_enabled'),
    (['startton', 'start ton', 'start sound', 'startsound', 'startsignal',
      'ton beim start', 'signal beim start', 'ton wenn die aufnahme',
      'ton beim aufnehmen', 'startgerausch'],
     'schalter', TIMER, 'start_sound_enabled'),

    # "automatisch abschicken" kann beides sein: der Schalter oder die
    # Wartezeit. Aufgelöst wird das unten in verstehe() an der Frage, ob eine
    # Zahl im Satz steht -- "mach das automatische Abschicken aus" gegen "mach
    # das automatische Abschicken auf 5 Minuten".
    (['automatisch abschick', 'automatische abschick', 'automatisches abschick',
      'auto abschick', 'selbst abschick', 'allein abschick', 'von alleine abschick',
      'automatisch absend', 'automatisch senden', 'automatisch schick',
      'auto submit', 'selber abschick'],
     'beides', TIMER, ('auto_submit_enabled', 'auto_submit_seconds')),

    (['aufnahme timer', 'aufnahmetimer', 'timer bei der aufnahme',
      'timer beim aufnehmen', 'timer', 'farbkreis', 'farbpunkt'],
     'schalter', TIMER, 'enabled'),

    # --- Meine Stimme und mein Ohr ----------------------------------------
    (['redepause', 'redepausen', 'sprechpause', 'sprechpausen', 'denkpause',
      'denkpausen', 'pause beim reden', 'pausen beim reden', 'stille',
      'schweigen darf', 'pause zwischen', 'wartezeit beim reden'],
     'zahl', STIMME, 'stille_ms'),

    # DAS NACHHÖREN. Bis zum 15.08.2026 hieß das „flüssiges Gespräch", und
    # Ramzi hat es selbst abgeschafft: „Ich muss irgendwie immer sagen, hey,
    # mein flüssiges Gespräch, 50 Sekunden. Das ist richtig scheiße." Der neue
    # Name sagt, was es ist -- wie lange ich nach meinem letzten Wort noch
    # weiter zuhöre -- und er lässt sich in einem Wort sagen: „Nachhören auf
    # zehn". Die alten Wörter bleiben trotzdem stehen: sie kosten nichts, und
    # ein halbes Jahr alter Reflex soll nicht plötzlich ins Leere gehen.
    #
    # EIGENER Wert, nicht `folge_sekunden` -- der ist das Fenster nach dem
    # Weckwort und muss bleiben, sonst darf er nach dem Wach-Ton nicht mehr
    # reden. Hier geht es nur darum, ob er nach MEINER Antwort ohne Namen
    # weiterreden darf. Ramzi stellt das je nach Lage um: läuft Musik, will er
    # drücken müssen; sitzt er still davor, ist das Gespräch schöner. Deshalb
    # ein Regler und keine Entscheidung im Code.
    (['nachhoren', 'nachhoeren', 'nach horen', 'nachhorzeit', 'nachhoerzeit',
      'flussiges gesprach', 'flussige gesprach', 'fliessendes gesprach',
      'gesprachsfenster', 'gesprach fenster', 'folgefenster', 'folge fenster',
      'ohne weckwort', 'ohne deinen namen', 'ohne namen reden',
      'ohne zu drucken', 'nachreden', 'weiterreden ohne'],
     'zahl', STIMME, 'gespraech_sekunden'),

    # Geraeuschunterdrueckung -- Ramzis Ventilatoren.
    (['gerauschunterdruckung', 'gerausch unterdruckung', 'geraeuschunterdrueckung',
      'rauschunterdruckung', 'rausch unterdruckung', 'unterdruckung',
      'geraeusche unterdrucken', 'gerausche unterdrucken', 'hintergrundgerausch',
      'hintergrund gerausch', 'lufter', 'ventilator', 'rauschen'],
     'zahl', STIMME, 'geraeuschunterdrueckung'),

    (['tempo', 'sprechtempo', 'geschwindigkeit', 'schneller reden',
      'langsamer reden', 'schneller sprech', 'langsamer sprech', 'wie schnell du'],
     'zahl', STIMME, 'tempo'),

    # "laut starker" ist kein Tippfehler, sondern was Whisper am 01.08.2026 aus
    # Ramzis gesprochenem "Lautstärke" gemacht hat: zwei Wörter. Der Befehl fiel
    # deshalb durch und landete im Chat -- also genau da, wo er nichts kosten
    # sollte. Ein Spracherkenner trennt zusammengesetzte Wörter, wo er will; die
    # Liste muss das aushalten, nicht er.
    (['lautstarke', 'laut starke', 'laut starker', 'lautstarker', 'lauter',
      'leiser', 'volume', 'volumen', 'wie laut', 'stimme laut', 'stimme leise',
      'zu laut', 'zu leise', 'stumm'],
     'zahl', STIMME, 'lautstaerke'),

    (['tonzeichen', 'signalton', 'signaltone', 'piep', 'gerausche', 'tone ',
      ' tone', 'quittungston', 'bestatigungston', 'wachton', 'wach ton'],
     'schalter', STIMME, 'toene'),

    # --- Die Knöpfe der Tafel, jetzt auch per Sprache ----------------------
    #
    # Ramzis Regel vom 07.08.2026: "all die Knöpfe, die wir hier haben, sollten
    # eigentlich schon als Reflexe da sein." Umgekehrt gilt es ausdrücklich
    # NICHT -- nicht jeder Reflex braucht einen Knopf, das wären viel zu viele.
    #
    # Die Art ist 'wahl' und nicht 'schalter': "Hände frei" ist kein An/Aus im
    # Sinne von `_schalter()` (das kennt "einschalten", "ausmachen" und
    # dergleichen, aber weder "frei" noch "gebunden"), und der Anzeigeschirm
    # hat ohnehin drei Zustände. Jede Möglichkeit bringt ihre eigenen
    # Stichwörter mit -- das ist zugleich die Antwort auf seine Sorge vor
    # Überschneidungen: die inneren Stichwörter werden erst geprüft, NACHDEM
    # das äußere getroffen hat. "Sichtbar" bei den Schritten kann deshalb nicht
    # mit "sichtbar" anderswo kollidieren.
    (['hande', 'haende'], 'wahl', STIMME, ('haende', [
        (['frei', 'darfst', 'ruhig anfassen', 'wieder anfassen'],
         True, 'Hände sind frei.'),
        (['gebunden', 'nicht anfassen', 'finger weg', 'fass nichts'],
         False, 'Hände sind gebunden.'),
    ])),

    (['schritte'], 'wahl', STIMME, ('feedback_modus', [
        (['nur sichtbar', 'sichtbar', 'nur was ich sehe'],
         'sichtbar', 'Ich melde nur noch, was du auch siehst.'),
        (['alle', 'jeden'], 'alle', 'Ich melde jeden Schritt.'),
    ])),

    (['aufnahmefenster', 'aufnahme fenster', 'aufnahmenfenster'],
     'wahl', STIMME, ('bild_fenster_aus', [
        (['versteck', 'ausblenden', 'nicht zeigen', 'unsichtbar'],
         True, 'Das Aufnahmefenster bleibt versteckt.'),
        (['zeig', 'einblenden', 'sichtbar'],
         False, 'Das Aufnahmefenster ist wieder sichtbar.'),
    ])),

    (['anzeigeschirm', 'untertitel auf', 'untertitel bei', 'untertitel nach',
      'untertitel zum', 'untertitel zur'],
     'wahl', STIMME, ('anzeige_schirm', [
        (['tafel', 'sekundar', 'linken'],
         'ich', 'Untertitel laufen bei der Tafel.'),
        (['grossen', 'primar', 'mitte'],
         'ramzi', 'Untertitel laufen auf dem großen.'),
        (['ipad', 'i pad', 'tablet', 'tertiar'],
         'ipad', 'Untertitel laufen auf dem iPad.'),
    ])),

    # Die Stimme über ihre NAMEN und nicht über das Wort "Stimme": das ist
    # schon belegt -- "stimme laut" und "stimme leise" gehören zur Lautstärke.
    # Ein Reflex, der sich mit einem bestehenden beißt, ist schlimmer als
    # keiner: man merkt erst beim Danebengreifen, dass etwas fehlt.
    (['ludvig', 'ludwig', 'thorsten', 'torsten'],
     'wahl', STIMME, ('stimme_motor', [
        (['ludvig', 'ludwig'], 'xtts', 'Ich rede jetzt als Ludvig.'),
        (['thorsten', 'torsten'], 'piper', 'Ich rede jetzt als Thorsten.'),
    ])),

    # "sichtdaur" ist kein Tippfehler: `_glaette` ersetzt oben "ue" durch "u",
    # damit "Ue" und "Ü" dasselbe treffen. Aus "Sichtdauer" wird dadurch
    # "sichtdaur", und ein Stichwort in der richtigen Schreibweise hätte hier
    # nie gegriffen. Dieselbe Sorte Falle wie "laut starker" weiter oben --
    # die Liste muss die geglättete Form enthalten, nicht die gesprochene.
    (['sichtdaur', 'sicht daur', 'sichtdauer'],
     'zahl', STIMME, 'mindest_anzeige_sekunden'),

    # --- Sonderfälle ------------------------------------------------------
    (['probe hor', 'probe an', 'probehor', 'mach mal eine probe', 'probesatz',
      'sag mal was', 'sag was', 'wie klingst du', 'wie horst du sich',
      'wie horst du dich', 'lies mal was', 'probe'],
     'probe', STIMME, None),

    (['welche einstellung', 'wie sind die einstellung', 'wie stehen die',
      'wie sind deine einstellung', 'wie ist es eingestellt', 'sag mir die werte',
      'welche werte', 'wie hoch ist', 'wie hoch steht', 'was ist eingestellt',
      'stand der einstellung'],
     'stand', None, None),
]

# Grenzen und Raster. Die Raster sind die der Tafel-Regler (index.html) -- ein
# Wert außerhalb wäre auf der Tafel nicht darstellbar, und dann würde die Tafel
# ihn beim nächsten Anfassen stillschweigend korrigieren.
GRENZEN = {
    'tempo':        (0.8, 1.8, 0.05),
    'lautstaerke':  (0.0, 1.5, 0.05),
    'stille_ms':    (600, 10000, 200),
    # Untergrenze 0, und 0 heißt AUS -- Ramzis Regel für jeden Regler dieser
    # Tafel. Dieselben Zahlen wie der Schieber in index.html; ein Wert, den die
    # Tafel nicht darstellen kann, würde beim nächsten Anfassen still
    # zurechtgebogen.
    'mindest_anzeige_sekunden': (0, 120, 1),
    # Auch hier: 0 ist erlaubt und heißt aus -- dann muss er drücken oder
    # meinen Namen sagen. Nach oben 120 Sekunden; wer länger als zwei Minuten
    # Nachlauf will, meint eigentlich „immer an", und das gibt es bewusst nicht.
    # 0 bis 60 in EINER-Schritten -- Ramzis Ansage vom 15.08.2026. Vorher
    # (0, 120, 5): oben doppelt so weit, wie er je braucht, und unten viel zu
    # grob. Seit das Video waehrend des Nachhoerens steht, ist jede Sekunde
    # spuerbar ("muss ich jetzt 7 Sekunden warten, damit mein Video weitergeht,
    # das ist schon echt lang") -- in Fuenferschritten kann er 3 oder 4 gar
    # nicht waehlen. Seine Worte: "ich kann eins machen, ich kann zwei machen,
    # drei vier fuenf sechs sieben acht und so weiter, also einzelne Sekunden
    # aussuchen bis zu 60".
    'gespraech_sekunden': (0, 60, 1),
    # 0 heisst aus: dann entscheidet allein der Stimmenmelder, wie bisher.
    'geraeuschunterdrueckung': (0, 100, 5),
    'orange_seconds':     (5, 3600, 1),
    'red_seconds':        (5, 3600, 1),
    'auto_submit_seconds': (10, 3600, 1),
}

PROBESATZ = 'So klinge ich gerade. Tempo und Lautstärke wie eingestellt.'


def _trifft(bruchstuecke, text):
    return any(b.strip() in text if len(b.strip()) > 3 else f' {b.strip()} ' in f' {text} '
               for b in bruchstuecke)


# ------------------------------------------------------------------- Setzen
def _setze_stimme(schluessel, wert, text, worte):
    """Tempo, Lautstärke, Redepause -- die Werte, die auch auf der Tafel sitzen."""
    einheit = _einheit(text)
    klein, gross, schritt = GRENZEN[schluessel]

    if schluessel == 'stille_ms':
        # Ohne Einheit entscheidet die Größe: "auf 2" sind Sekunden, "auf 2000"
        # sind Millisekunden. Niemand meint 2 Millisekunden Redepause.
        if einheit == 's':
            wert *= 1000
        elif einheit == 'min':
            wert *= 60000
        elif einheit is None and wert <= 60:
            wert *= 1000
        wert = int(_rasten(_grenzen(wert, klein, gross), schritt))
        einstellungen.setze(stille_ms=wert)
        return f'Redepause steht auf {_sprich_dauer(wert / 1000)}.'

    if schluessel == 'geraeuschunterdrueckung':
        if einheit == '%' or wert > 100:
            wert = min(wert, 100)
        wert = int(_rasten(_grenzen(wert, klein, gross), schritt))
        einstellungen.setze(geraeuschunterdrueckung=wert)
        if wert == 0:
            return ('Geräuschunterdrückung ist aus. Ich nehme alles, was der '
                    'Stimmenmelder für Sprache hält.')
        return (f'Geräuschunterdrückung steht auf {wert}. '
                'Leiseres als deinen Zimmerton lasse ich jetzt weg.')

    if schluessel == 'gespraech_sekunden':
        # Immer Sekunden -- "auf eine Minute" darf er trotzdem sagen.
        if einheit == 'min':
            wert *= 60
        elif einheit == 'ms':
            wert /= 1000.0
        wert = int(_rasten(_grenzen(wert, klein, gross), schritt))
        einstellungen.setze(gespraech_sekunden=wert)
        if wert == 0:
            return ('Nachhören ist aus. Sag meinen Namen oder drück '
                    'die Taste, sonst höre ich weg.')
        return (f'Nachhören steht auf {_sprich_dauer(wert)}. '
                'So lange darfst du nach meiner Antwort einfach weiterreden.')

    if schluessel == 'tempo':
        if einheit == '%' or wert > 3:
            wert /= 100.0
        wert = _rasten(_grenzen(wert, klein, gross), schritt)
        einstellungen.setze(tempo=wert)
        return f'Tempo steht auf {_sprich_zahl(wert)}.'

    if schluessel == 'mindest_anzeige_sekunden':
        # Immer Sekunden. "Sichtdauer eine Minute" ist trotzdem erlaubt --
        # niemand rechnet freiwillig um, nur weil die Einheit intern eine
        # andere ist.
        if einheit == 'min':
            wert *= 60
        elif einheit == 'ms':
            wert /= 1000.0
        wert = int(_rasten(_grenzen(wert, klein, gross), schritt))
        einstellungen.setze(mindest_anzeige_sekunden=wert)
        if wert == 0:
            return 'Sichtdauer ist aus, ich räume nichts mehr weg.'
        return f'Sichtdauer steht auf {_sprich_dauer(wert)}.'

    # Lautstärke
    if einheit == '%' or wert > 1.5:
        wert /= 100.0
    wert = _rasten(_grenzen(wert, klein, gross), schritt)
    einstellungen.setze(lautstaerke=wert)
    return f'Lautstärke steht auf {int(round(wert * 100))} Prozent.'


def _setze_timer_zahl(schluessel, wert, text):
    """orange, rot, automatisch abschicken -- alles Sekundenwerte."""
    einheit = _einheit(text)
    if einheit == 'min':
        wert *= 60
    elif einheit == 'ms':
        wert /= 1000.0
    klein, gross, schritt = GRENZEN[schluessel]
    wert = int(_rasten(_grenzen(wert, klein, gross), schritt))
    _yaml_setze(schluessel, wert)
    namen = {'orange_seconds': 'Orange kommt',
             'red_seconds': 'Rot kommt',
             'auto_submit_seconds': 'Abgeschickt wird'}
    return f'{namen[schluessel]} nach {_sprich_dauer(wert)}.'


SCHALTER_NAMEN = {
    'toene': ('Tonzeichen sind an.', 'Tonzeichen sind aus.'),
    'enabled': ('Der Aufnahme-Timer ist an.', 'Der Aufnahme-Timer ist aus.'),
    'auto_submit_enabled': ('Automatisch abschicken ist an.',
                            'Automatisch abschicken ist aus.'),
    'countdown_sounds_enabled': ('Die Countdown-Töne sind an.',
                                 'Die Countdown-Töne sind aus.'),
    'start_sound_enabled': ('Der Startton ist an.', 'Der Startton ist aus.'),
}


def _setze_schalter(ort, schluessel, an):
    if ort == STIMME:
        einstellungen.setze(**{schluessel: an})
    else:
        _yaml_setze(schluessel, an)
    ja, nein = SCHALTER_NAMEN[schluessel]
    return ja if an else nein


def _setze_wahl(ort, schluessel, wert, ansage):
    """Einen von mehreren festen Zuständen setzen.

    Der Unterschied zu `_setze_schalter`: dort gibt es an und aus, hier drei
    Bildschirme, zwei Stimmen oder zwei Meldungsumfänge. Die Ansage kommt aus
    der Tabelle mit, weil sie je Möglichkeit anders lautet -- ein
    zusammengebauter Satz ("X steht auf ipad") klingt nach Maschine.
    """
    if ort == STIMME:
        einstellungen.setze(**{schluessel: wert})
    else:
        _yaml_setze(schluessel, wert)
    return ansage


def _stand():
    """Alles auf einmal ansagen -- er fragt "wie ist das gerade eingestellt"."""
    w = einstellungen.alle()
    teile = [
        f'Tempo {_sprich_zahl(w["tempo"])}',
        f'Lautstärke {int(round(w["lautstaerke"] * 100))} Prozent',
        f'Redepause {_sprich_dauer(w["stille_ms"] / 1000)}',
        ('Geräuschunterdrückung aus' if not w.get('geraeuschunterdrueckung')
         else f'Geräuschunterdrückung {w["geraeuschunterdrueckung"]}'),
        ('Nachhören aus' if not w.get('gespraech_sekunden')
         else f'Nachhören {_sprich_dauer(w["gespraech_sekunden"])}'),
        f'Tonzeichen {"an" if w["toene"] else "aus"}',
        f'Aufnahme-Timer {"an" if _yaml_hole("enabled", True) else "aus"}',
        f'orange nach {_sprich_dauer(_yaml_hole("orange_seconds", 60))}',
        f'rot nach {_sprich_dauer(_yaml_hole("red_seconds", 180))}',
    ]
    if _yaml_hole('auto_submit_enabled', True):
        teile.append(f'abgeschickt nach {_sprich_dauer(_yaml_hole("auto_submit_seconds", 300))}')
    else:
        teile.append('automatisch abschicken aus')
    return '. '.join(teile) + '.'


# -------------------------------------------------------- Relative Änderungen
#
# "mach mal lauter" ohne Zahl. Kostet fast nichts und ist die Formulierung, die
# ihm zuerst über die Lippen geht.
#
# Die Stämme stehen hier ohne Endung: "langsam" trifft auch "langsamer",
# "schnell" auch "schneller". Ramzi hat am 01.08.2026 "reden wir langsam"
# gesagt -- ohne -er -- und der Befehl fiel durch.
# „langzamer" mit Z ist kein Tippfehler, sondern was Qalam am 01.08.2026 aus
# Ramzis „langsamer" gemacht hat -- der Befehl landete deshalb im Chat. Dafür
# gibt es jetzt EINE zentrale Korrektur (verhoerer.py, in _glaette()
# eingehängt) statt eigener Verhör-Varianten in jeder Wortliste hier.
SCHRITTE = {
    'lauter': ('lautstaerke', +0.15), 'leiser': ('lautstaerke', -0.15),
    'schnell': ('tempo', +0.1),
    'langsam': ('tempo', -0.1),
}

# Wörter, die aus einer Beobachtung eine Bitte machen.
#
# Ohne diese Prüfung würde "das ging aber schnell" mein Sprechtempo erhöhen --
# ein Satz über die Welt, den ich als Befehl missverstehe. Genau die Sorte
# Fehlgriff, die schlimmer ist als ein verpasster Befehl: einen verpassten
# beantworte ich richtig, ein falscher verstellt still einen Wert.
AUFFORDERUNG = ('mach', 'stell', 'setz', 'red', 'sprich', 'sprech', 'bitte',
                'mal', 'etwas', 'bisschen', 'kannst du', 'wir', 'geh', 'werd')


def _richtung(text):
    """Steckt eine Richtung ohne Zahl darin -- und ist sie auch gemeint?

    Bei drei Wörtern oder weniger ("lauter bitte", "langsamer") reicht die
    Richtung allein; da redet niemand über die Welt. Darüber braucht es ein
    Wort, das die Äußerung zu einer Bitte macht."""
    if not any(wort in text for wort in SCHRITTE):
        return False
    if len(text.split()) <= 3:
        return True
    return any(w in text for w in AUFFORDERUNG)


def _relativ(text):
    for wort, (schluessel, delta) in SCHRITTE.items():
        if wort not in text:
            continue
        # "mach lauter" ja, "mach die Lautstärke auf 80" nein -- da gilt die Zahl.
        klein, gross, schritt = GRENZEN[schluessel]
        neu = _rasten(_grenzen(einstellungen.hole(schluessel) + delta, klein, gross), schritt)
        einstellungen.setze(**{schluessel: neu})
        if schluessel == 'tempo':
            return f'Tempo steht auf {_sprich_zahl(neu)}.'
        return f'Lautstärke steht auf {int(round(neu * 100))} Prozent.'
    return None


# --------------------------------------------------------------------- Kern
def verstehe(rohtext, ausfuehren=True):
    """Ist das ein Stellschrauben-Befehl? Dann tun und zurücksagen, sonst None.

    None heißt "nicht meine Zuständigkeit" -- der Satz läuft dann weiter durch
    die alten Reflexe und notfalls über die Brücke an Noor. Ein falsches Ja
    wäre hier schlimmer als ein Nein: ein verpasster Befehl landet bei Noor und
    wird richtig beantwortet, ein falsch verstandener verstellt still einen
    Wert, den Ramzi erst merkt, wenn ihn etwas stört.

    `ausfuehren=False` sagt nur, was passieren würde -- dafür ist --probe da.
    """
    text = _glaette(rohtext)
    if not text:
        return None
    worte = text.split()
    if len(worte) > STELL_MAX_WOERTER:
        return None

    # Geht es um seine Musik, geht es NICHT um meine Stimme.
    #
    # "mach die Musik leiser" hätte hier bis zum 01.08.2026 meine eigene
    # Lautstärke gesenkt und seine Musik in Ruhe gelassen -- zwei Dinge auf
    # einmal falsch, und beide leise. Die Musik-Reflexe in assistant.py laufen
    # zwar vorher, kennen aber nur an/aus/weiter/zurück; für "leiser" gibt es
    # dort nichts, also fiele der Satz hierher.
    #
    # Ich lasse ihn stattdessen an mich durchgehen: dann kann ich die
    # Lautstärke der Musik wirklich ändern, statt so zu tun. Ein ehrliches
    # "verstehe ich noch nicht" ist besser als eine falsche Tat.
    if any(w in text for w in ('musik', 'spotify', 'lied', 'song', 'playlist',
                               'video')):
        return None
    # "titel" braucht eine Wortgrenze, alle anderen nicht. Grund: "Untertitel"
    # enthält es -- und damit hat dieser Riegel jeden Untertitel-Befehl
    # verschluckt, ohne eine Spur zu hinterlassen. Gefunden am 07.08.2026 beim
    # ersten Trockenlauf des neuen Anzeigeschirm-Reflexes; ohne den Test wäre
    # er als "geht halt nicht" durchgegangen.
    if re.search(r'\btitel\b', text):
        return None

    # Ein Eintrag, der nur mit dem Stichwort trifft aber keine Zahl und kein
    # An/Aus dazu findet, gibt NICHT auf, sondern lässt den nächsten Eintrag
    # ran (`continue` statt `return None`). Sonst würde ein zufällig früh
    # stehendes Stichwort einen echten Befehl weiter hinten verdecken -- und
    # genau solche stillen Verdeckungen findet hinterher niemand mehr.
    for bruchstuecke, art, ort, schluessel in TABELLE:
        if not _trifft(bruchstuecke, text):
            continue

        if art == 'probe':
            return PROBESATZ
        if art == 'stand':
            return _stand()

        wert = _zahl(worte)
        an = _schalter(text, worte)

        if art == 'beides':
            schalter_schluessel, zahl_schluessel = schluessel
            if wert is not None:
                if not ausfuehren:
                    return (f'[würde {zahl_schluessel} setzen: {wert} '
                            f'({_einheit(text) or "ohne Einheit"})]')
                return _setze_timer_zahl(zahl_schluessel, wert, text)
            if an is None:
                continue
            if not ausfuehren:
                return f'[würde {schalter_schluessel} setzen: {an}]'
            return _setze_schalter(ort, schalter_schluessel, an)

        if art == 'schalter':
            if an is None:
                continue
            if not ausfuehren:
                return f'[würde {schluessel} setzen: {an}]'
            return _setze_schalter(ort, schluessel, an)

        if art == 'wahl':
            feld, moeglichkeiten = schluessel
            for teile, w, ansage in moeglichkeiten:
                if _trifft(teile, text):
                    if not ausfuehren:
                        return f'[würde {feld} setzen: {w}]'
                    return _setze_wahl(ort, feld, w, ansage)
            # Das Stichwort saß, die Möglichkeit fehlt ("Hände" allein). Nicht
            # aufgeben, sondern den nächsten Eintrag ranlassen -- dieselbe
            # Regel wie oben, sonst verdeckt ein Halbtreffer einen echten
            # Befehl weiter hinten.
            continue

        # art == 'zahl'
        if wert is None:
            # "mach mal lauter" / "rede langsamer" -- kein Zahlwert, aber eine
            # klare Richtung.
            if ort == STIMME and _richtung(text):
                if not ausfuehren:
                    return f'[würde {schluessel} relativ verstellen]'
                return _relativ(text)
            # "Lautstärke aus" heißt stumm, "an" heißt wieder voll. Nur hier,
            # nicht bei Tempo -- ein Tempo von 0 gibt es nicht.
            if schluessel == 'lautstaerke' and an is not None:
                if not ausfuehren:
                    return f'[würde lautstaerke setzen: {1.0 if an else 0.0}]'
                einstellungen.setze(lautstaerke=1.0 if an else 0.0)
                return 'Stimme ist stumm.' if not an else 'Lautstärke steht auf 100 Prozent.'
            # "Sichtdauer aus" ist der Weg, mir das Aufräumen ganz aus der Hand
            # zu nehmen -- ohne ihn müsste Ramzi eine Zahl nennen, um etwas
            # abzuschalten. "Sichtdauer an" bringt die Vorgabe zurück.
            if schluessel == 'mindest_anzeige_sekunden' and an is not None:
                neu = 5 if an else 0
                if not ausfuehren:
                    return f'[würde mindest_anzeige_sekunden setzen: {neu}]'
                einstellungen.setze(mindest_anzeige_sekunden=neu)
                return ('Sichtdauer steht auf fünf Sekunden.' if an
                        else 'Sichtdauer ist aus, ich räume nichts mehr weg.')
            continue
        if not ausfuehren:
            return f'[würde {schluessel} setzen: {wert} ({_einheit(text) or "ohne Einheit"})]'
        if ort == STIMME:
            return _setze_stimme(schluessel, wert, text, worte)
        return _setze_timer_zahl(schluessel, wert, text)

    # Kein Stichwort getroffen, aber eine klare Richtung? "rede langsamer".
    if _richtung(text):
        return _relativ(text) if ausfuehren else '[würde relativ verstellen]'
    return None


def ist_stellschraube(rohtext):
    """Für assistant._ist_kurzbefehl: würde das hier greifen?

    Fragt ohne zu ändern -- das Ohr fragt das MITTEN im Satz, um zu entscheiden,
    ob es noch auf eine Denkpause warten muss."""
    try:
        return verstehe(rohtext, ausfuehren=False) is not None
    except Exception:
        return False


# --------------------------------------------------------------------- Probe
#
# Die Sätze, mit denen ich es geprüft habe. Sie stehen absichtlich IM Code und
# nicht in einer Testdatei nebendran: wer die Bruchstücke oben anfasst, sieht
# hier sofort, was weiterhin verstanden werden muss.
PROBEN = [
    'Noor, mach mal meine Redepausen auf 2000 Millisekunden',
    'mach die Redepause auf 10 Sekunden',
    'stell die Redepausen auf zwei Sekunden',
    'Redepause auf anderthalb Sekunden',
    'mach das Tempo auf 1,4',
    'Tempo auf 130 Prozent',
    'rede langsamer',
    'mach mal lauter',
    'Lautstärke auf 80 Prozent',
    'stell die Lautstärke auf 60',
    'mach die Stimme stumm',
    'mach die Tonzeichen aus',
    'Tonzeichen wieder an',
    'die Sekunden für orange auf 90',
    'mach orange auf anderthalb Minuten',
    'stell rot auf 120 Sekunden',
    'mach das automatische Abschicken aus',
    'automatisch abschicken auf 5 Minuten',
    'die Countdown-Töne bitte aus',
    'mach den Startton wieder an',
    'mach den Aufnahme-Timer aus',
    'wie sind die Einstellungen gerade',
    'sag mal was',
    'Probe hören',
    # Ramzis echte Sätze vom 01.08.2026, die im Chat gelandet sind statt zu greifen:
    'reden wir langsam',
    'laut stärker auf 15%',
    'Lautstärke auf 15 Prozent',
    # Diese hier dürfen NICHT greifen:
    'wie spät ist es',
    'mach mal die Musik an',
    'mach die Musik leiser',
    'das ging aber schnell',
    'ich brauche gleich mal einen Timer für den Kuchen im Ofen und zwar zwanzig '
    'Minuten lang, kannst du mir das einstellen',
]


def _probe():
    breite = max(len(s) for s in PROBEN)
    for satz in PROBEN:
        antwort = verstehe(satz, ausfuehren=False)
        print(f'{satz:<{breite}}  ->  {antwort if antwort else "— (geht an Noor)"}')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--probe':
        _probe()
    elif len(sys.argv) > 1:
        satz = ' '.join(sys.argv[1:])
        antwort = verstehe(satz)
        print(antwort if antwort else '— kein Stellschrauben-Befehl')
    else:
        print(__doc__)
