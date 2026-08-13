"""Lachen im TON erkennen -- nicht in den Wörtern.

WARUM ES DIESE DATEI GIBT. Ramzi am 08.08.2026, nachmittags, wörtlich: „Ich
habe gerade übrigens gelacht. Aber das kannst du hier nicht raushören." Er muss
sonst „haha" dazuschreiben, damit ich merke, dass etwas lustig gemeint war.

Und der Weg über die Wörter ist NACHGEMESSEN keiner. Genau diese Äußerung steht
in `ohr.log` (17066, 18:57:07) -- Whisper hat aus dem Lachen darin nichts
gemacht: kein „haha", kein „(lacht)", gar nichts. Ein Spracherkenner ist darauf
trainiert, Wörter zu liefern, und wirft alles weg, was keins ist. Wer Lachen
hören will, muss den Ton ansehen, nicht die Abschrift.

## Was hier läuft

CED-tiny (`modelle/sherpa-onnx-ced-...`), ein auf AudioSet trainierter
Geräusch-Erkenner mit 527 Klassen, darunter sechs verschiedene Arten von Lachen.
Über sherpa-onnx auf der CPU.

DIE GRAFIKKARTE BLEIBT UNANGETASTET, und das ist keine Nebensache: über 7 von 8
GB stürzt Ramzis Rechner bis zur Taskleiste ab (siehe
`memory/reference_ramzi_machine_constraints.md`). Das Ohr belegt dort schon
gut 3 GB.

## Warum CED und nicht das offensichtliche AST

Gemessen am 08.08.2026 nachts, auf genau diesem Rechner, nicht geschätzt:

    AST (86,6 Mio Parameter, das bekannte AudioSet-Modell)
        int8, 8 Kerne  ->  0,56 s  je Durchgang
    CED (dieselbe Aufgabe, sherpa-onnx)
        base, 4 Kerne  ->  0,23 s  für 15 s Ton, also ALLE Fenster zusammen

AST hätte pro Äußerung acht von zwölf Kernen belegt, während Ramzi spielt --
und die halbe Sekunde hätte genau die Zeit zurückgeholt, die am 07.08. aus der
Brücke herausgearbeitet wurde (2,50 -> 2,00 s).

## Warum das GROSSE CED, obwohl das kleine hundertmal billiger ist

Weil das kleine Ramzis Lachen nicht hört. Das ist der teuerste Befund dieser
Nacht, und er kam erst heraus, als er selbst widersprochen hat.

Zuerst lief hier CED-tiny (5,5 Mio, 12–18 ms). Auf den Testtönen des Modells
sah das glänzend aus: Lachen 0,79, alles andere unter 0,01. An Ramzis eigenem
Lachen kam es auf **0,043** -- und seine 129 ganz normalen Sätze im Protokoll
lagen bei bis zu 0,02. Sein Lachen lag also MITTEN im Rauschen des Redens; mit
diesem Modell war keine Schwelle zu ziehen, egal welche.

Meine erste Erklärung war falsch: ich hielt sein Lachen für gestellt. Sein
Widerspruch war der entscheidende Hinweis -- er lacht wirklich so. Kein
Lachanfall, sondern ein kurzes, leises Lachen mitten im Reden. AudioSet lernt
Lachen aus YouTube-Gelächter; genau diese leise Sorte ist dort die Ausnahme.

An SEINEM Lachen gemessen, nicht an den Testtönen:

    Modell     sein Lachen   sein Reden
    tiny            0,043       0,000
    mini            0,044       0,000
    small           0,026       0,000
    base            0,132       0,000     <- und nur mit kurzen Fenstern

Größer allein reicht nicht (small ist schlechter als tiny). Es ist die
KOMBINATION aus dem großen Modell und einem kurzen Fenster: sein Lachen dauert
weniger als eine Sekunde und ersäuft in einem 2-s-Fenster.

**Eine Kaskade wurde gebaut und wieder verworfen** -- tiny sucht die Stelle,
base urteilt darüber. Sie war schnell (136 ms), lieferte aber nur 0,070 statt
0,132: tiny sortiert das entscheidende Fenster nicht nach oben, auch nicht
unter die ersten acht. Es taugt eben nicht einmal zum Finden. Gemessen,
verworfen, hier notiert -- damit es niemand noch einmal versucht.

## Die Fenster -- der Punkt, an dem eine naive Fassung scheitert

Das Modell bewertet einen ganzen Schnipsel auf einmal. Wirft man ihm 15
Sekunden Rede mit einem kurzen Lacher darin hin, verdünnt sich das Lachen -- und
der Einbau sähe aus wie „erkennt nichts". Derselbe Testton, einmal am Stück und
einmal in Fenstern gemessen:

    ganzer Schnipsel am Stück   ->  0,48
    in 2-s-Fenstern, bestes     ->  0,79

Es wird also in Fenstern geschaut und das höchste genommen. Größe und Sprung
sind an SEINEM Lachen ausgemessen (base, 4 Kerne):

    Fenster/Sprung    sein Lachen   sein Reden   max. Gegenprobe   15 s Ton
    1,0 s / 0,25 s          0,132        0,000             0,003     945 ms
    1,0 s / 0,50 s          0,132        0,000             0,002     425 ms
    1,0 s / 1,00 s          0,132        0,000             0,002     226 ms
    0,75 s / 0,25 s         0,117        0,005             0,006     620 ms
    0,75 s / 0,50 s         0,093        0,000             0,006     313 ms

**Überlappung bringt nichts.** Der engste Sprung liefert exakt denselben Wert
wie gar keine Überlappung und kostet das Vierfache. Genommen ist deshalb
1,0 s ohne Überlappung: 226 ms für eine 15 s lange Äußerung, und die läuft
neben Whisper her, das auf der Grafikkarte rechnet.

Gegenproben bei dieser Einstellung: Gesang, Sprache, Katze, Sirene je 0,000,
weinendes Baby 0,002. Fremdes lautes Lachen 0,83.

## DIESE ERKENNUNG IST AUS (13.08.2026) -- und hier steht, warum

`lach_schwelle` steht auf **0**. Damit steigt `pruefe()` ganz oben aus: kein
Modell wird geladen, keine Rechenzeit verbraucht. Der Regler bleibt liegen, die
Erkennung kann jederzeit zurückkommen -- aber nicht durch eine andere Zahl.

Ramzis Befund vom 13.08.2026, und er ist eindeutig: „nicht einmal richtig
erkannt und alle Erkennungen bis jetzt waren falsch. Also wir haben
hundertprozentige falsche Events und das über einen längeren Zeitraum."

Nachgezählt in `ohr.log`, nicht geglaubt:

    Messungen insgesamt                3091
    davon über der Schwelle 0,06         56
    davon richtig (seine Auskunft)        0

**Warum keine andere Schwelle hilft.** Seine aufgehobene Lachprobe vom
08.08.2026 noch einmal durch das große Modell geschickt, diesmal mit ALLEN
Lach-Klassen einzeln statt nur der höchsten:

    sein echtes Lachen      Snicker            0,132   <- die Spitze
                            Chuckle, chortle   0,118
                            Laughter           0,037
                            Giggle             0,015
    sein normales Reden     keine Lach-Klasse überhaupt unter den Top 20

Und die 56 Fehltreffer aus dem Betrieb, nach Klasse:

    Snicker      ~50        hinauf bis 0,60
    Laughter       2
    Giggle         2

Damit liegt **sein echtes Lachen mit 0,13 MITTEN in dem, was sein Zimmer
ohnehin produziert** -- die Fehltreffer bei 0,15, 0,22, 0,47, 0,54 und 0,60
sind alle LAUTER als sein Lachen. Eine Schwelle, die ihn hört, lässt sie alle
durch; eine, die sie sperrt, sperrt ihn mit. Dazwischen ist nichts.

**Nach Klassen zu filtern hilft auch nicht.** Das ist die naheliegende Idee,
weil „Snicker" fast alle Fehltreffer stellt -- aber „Snicker" ist eben auch die
Klasse, in der SEIN Lachen am höchsten steht. Genau die Trennung, auf die man
setzen würde, gibt es nicht.

**Woher die Fehltreffer kommen.** Fast alle sitzen auf Abschnitten, in denen
Whisper KEIN einziges Wort gehört hat (`gehoert: ''`) -- also auf Spiel-,
Anime- und Musikton aus dem Zimmer. Zwischen 13:04 und 13:23 stehen rund
dreißig Treffer am Stück, keiner mit Sprache dabei. Ein Vorfilter „nur prüfen,
wenn auch Wörter erkannt wurden" räumte etwa vier Fünftel davon ab -- die
übrigen waren aber ebenfalls falsch („Ich lach nicht." kam auf 0,080), und an
der Überlappung oben ändert er nichts.

## Der eigentliche Fehler, damit ihn niemand wiederholt

Die alte Begründung stand hier als „Faktor 20 nach beiden Seiten": sein Lachen
0,132, sein Reden 0,000, höchste Gegenprobe 0,002. Jede einzelne Zahl stimmt.

Die **Gegenprobe** war trotzdem die falsche. Gemessen wurde gegen Gesang,
Sprache, Katze, Sirene und ein weinendes Baby -- gegen ausgesuchte
Beispieltöne also. NICHT gemessen wurde gegen das, was bei ihm tatsächlich im
Raum läuft, während er redet. Da liegt der Fehler.

Es ist derselbe Merksatz, der weiter oben schon steht -- „an fremden Testtönen
zu messen beweist nichts über Ramzi". Beim Lachen war er beherzigt, bei der
Gegenprobe vergessen. **Beide Seiten müssen aus seinem Zimmer kommen, nicht
nur die eine.**

## Was es bräuchte, damit das je wieder angeht

Nicht eine Zahl, sondern Material: Aufnahmen aus SEINEM Zimmer, während Spiel
oder Anime läuft und er NICHT lacht, als Negativ-Satz -- dazu mehrere echte
Lacher von ihm. Erst daran ließe sich prüfen, ob überhaupt ein Merkmal die
beiden trennt: ein anderes Modell, ein Profil über mehrere Klassen statt nur
der Spitze, oder die Kopplung an erkannte Sprache. Ohne dieses Material ist
jede weitere Schwelle geraten -- und geraten haben wir jetzt zweimal.

`werkzeuge/noor-lachprobe.ps1` nimmt auf und hebt die Dateien in
`qalam/lachproben/` auf. Das ist der Weg dorthin, und er kostet Ramzi einmal
Zeit statt bei jeder Nachjustierung erneut.

## Weich verdrahtet

Fehlt das Modell, fehlt sherpa-onnx, geht irgendetwas schief: dann liefert
`pruefe()` einfach `None` und das Ohr arbeitet weiter wie vorher. Eine Anzeige
darf nie den Dienst umbringen, über den sie nur berichtet.
"""
import os
import threading
import time

import numpy as np

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELLE = os.path.join(PROJEKT, 'modelle')

# Gearbeitet wird mit dem GROSSEN Modell; `tiny` bleibt nur als Rückfall, falls
# `base` fehlt. Warum nicht umgekehrt, steht im Kopf dieser Datei.
SUCHER = 'tiny'
RICHTER = 'base'

# Vier Kerne sind vertretbar, weil Whisper bei Ramzi auf der GRAFIKKARTE
# rechnet (`cuda/float16` im Protokoll) -- die CPU steht während der
# Transkription ohnehin still, und genau dann läuft dieser Faden.
KERNE = {SUCHER: 1, RICHTER: 4}

RATE = 16000

# Die Lach-Familie aus AudioSet. „Baby laughter" (17) fehlt mit Absicht: das
# ist ein Kleinkind-Lachen und würde bei Videos anschlagen, nicht bei Ramzi.
LACH_KLASSEN = {
    'Laughter',
    'Giggle',
    'Snicker',
    'Belly laugh',
    'Chuckle, chortle',
}

# Fenster und Sprung -- beide GEMESSEN, nicht gewählt. Tabelle oben.
# Ohne Überlappung, und das ist kein Sparen an der falschen Stelle: engere
# Sprünge haben an Ramzis Lachen KEINEN besseren Wert gebracht (0,132 bei
# 0,25 s wie bei 1,0 s Sprung), nur das Vierfache an Rechenzeit.
FENSTER_S = 1.0
SPRUNG_S = 1.0

# 0 = AUS. Seit dem 13.08.2026, und die Begründung steht ausführlich im Kopf
# dieser Datei: sein Lachen (0,13) und sein Zimmer im Hintergrund (bis 0,60)
# liegen in derselben Klasse übereinander, es gibt keine trennende Zahl.
#
# Wirksam ist ohnehin der Wert aus `noor-einstellungen.json`; diese Zeile gilt
# nur für einen Rechner ohne eigene Datei. Beide stehen jetzt auf 0.
SCHWELLE_VORGABE = 0.0

# Wie viele Klassen sich das Modell je Fenster abholen soll. 20 ist reichlich:
# steht Lachen nicht unter den zwanzig wahrscheinlichsten Geräuschen, war es
# keins. Mehr kostet nichts, aber es hilft auch nichts.
OBEN = 20

_tagger = {}
_versucht = set()
_schloss = threading.Lock()


def _ordner(groesse):
    return os.path.join(MODELLE,
                        f'sherpa-onnx-ced-{groesse}-audio-tagging-2024-04-19')


def _hole_tagger(groesse):
    """Ein Modell laden -- einmal, beim ersten Gebrauch, und nie wieder.

    NICHT beim Import: der Import läuft im Startpfad des Ohrs, und ein Ohr,
    das wegen eines Geräusch-Erkenners später zuhört, ist ein schlechter
    Tausch.

    `groesse` ist 'tiny' oder 'base' -- siehe die Kaskade in `pruefe()`.
    """
    with _schloss:
        if groesse in _tagger:
            return _tagger[groesse]
        if groesse in _versucht:
            return None
        _versucht.add(groesse)
        ordner = _ordner(groesse)
        modell = os.path.join(ordner, 'model.int8.onnx')
        etiketten = os.path.join(ordner, 'class_labels_indices.csv')
        if not (os.path.exists(modell) and os.path.exists(etiketten)):
            print(f'[Lachen] Modell {groesse} fehlt unter {ordner} -- '
                  f'{"Lacherkennung bleibt aus" if groesse == SUCHER else "nur der Sucher läuft"}.')
            return None
        try:
            import sherpa_onnx
            t = sherpa_onnx.AudioTagging(
                sherpa_onnx.AudioTaggingConfig(
                    model=sherpa_onnx.AudioTaggingModelConfig(
                        ced=modell, num_threads=KERNE[groesse], provider='cpu'),
                    labels=etiketten, top_k=OBEN))
            _tagger[groesse] = t
            print(f'[Lachen] CED-{groesse} geladen.')
            return t
        except Exception as e:
            print(f'[Lachen] Modell {groesse} nicht ladbar: {e}')
            return None


def _fenster(audio):
    """Den Ton in überlappende Stücke schneiden.

    Kürzer als ein Fenster: dann eben am Stück. Ein 0,8-s-Lacher ist genau der
    Fall, den wir NICHT verpassen wollen.
    """
    breite = int(FENSTER_S * RATE)
    sprung = int(SPRUNG_S * RATE)
    if len(audio) <= breite:
        return [audio]
    stuecke = []
    anfang = 0
    while anfang + breite <= len(audio):
        stuecke.append(audio[anfang:anfang + breite])
        anfang += sprung
    # Der Rest am Ende darf nicht unter den Tisch fallen -- ein Lacher ganz zum
    # Schluss ist eher die Regel als die Ausnahme (er sagt etwas und lacht
    # danach).
    if anfang < len(audio) - int(0.3 * RATE):
        stuecke.append(audio[-breite:])
    return stuecke


def _hoechster(tagger, stueck):
    """Der höchste Wert aus der Lach-Familie für EIN Fenster."""
    strom = tagger.create_stream()
    strom.accept_waveform(RATE, stueck)
    bester, name = 0.0, ''
    for ereignis in tagger.compute(strom):
        if ereignis.name in LACH_KLASSEN and ereignis.prob > bester:
            bester, name = float(ereignis.prob), ereignis.name
    return bester, name


def pruefe(audio):
    """Steckt in diesem Ton ein Lachen?

    `audio`: 1-D float32 zwischen -1 und 1, 16 kHz -- genau die Form, die
    `wake_word._pruefe` ohnehin schon gebaut hat. Absichtlich dieselbe: den Ton
    ein zweites Mal umzurechnen wäre Arbeit für nichts.

    Zurück kommt `(wert, name, sekunden)` oder `None`, wenn nichts zu holen war.
    Der WERT ist der höchste Lach-Wert über alle Fenster, NAME die Klasse, die
    ihn geliefert hat (Kichern liest sich anders als Bauchlachen), SEKUNDEN die
    gebrauchte Rechenzeit fürs Protokoll.

    Es wird NICHT hier über die Schwelle entschieden. Diese Funktion misst, der
    Aufrufer urteilt -- sonst steht die Schwelle an zwei Stellen und läuft
    auseinander.
    """
    # NULL HEISST AUS, und zwar hier oben und nicht erst beim Vergleich.
    # Ramzis Regel für jeden Regler (siehe
    # `memory/feedback_regler_null_ist_aus.md`): bei 0 muss die Sache GANZ aus
    # sein, nicht nur wirkungslos. Stünde die Prüfung nur unten beim Schwellen-
    # Vergleich, liefe das Modell weiter mit und verbrauchte Rechenzeit für ein
    # Ergebnis, das niemand ansieht -- ein Regler, der auf 0 noch arbeitet, ist
    # keiner.
    if schwelle() <= 0:
        return None
    # Erst das große Modell, und nur wenn das fehlt das kleine. Ein fehlendes
    # `base` darf die Erkennung verschlechtern, aber nicht abstellen -- ein
    # frisch geklonter Rechner soll nicht stumm dastehen. Umgekehrt würde das
    # kleine hier sinnlos mitgeladen.
    tagger = _hole_tagger(RICHTER) or _hole_tagger(SUCHER)
    if tagger is None or audio is None or len(audio) < int(0.3 * RATE):
        return None
    start = time.time()
    try:
        bester, bester_name = 0.0, ''
        for stueck in _fenster(np.ascontiguousarray(audio, dtype=np.float32)):
            wert, name = _hoechster(tagger, stueck)
            if wert > bester:
                bester, bester_name = wert, name
    except Exception as e:
        print(f'[Lachen] Prüfung fehlgeschlagen: {e}')
        return None
    return bester, bester_name, time.time() - start


def schwelle():
    """Ab welchem Wert es als Lachen gilt -- aus Ramzis Einstellungen.

    Über `einstellungen`, damit er sie per Sprache verstellen kann („mach die
    Lachschwelle auf 0,5"), ohne dass jemand eine Datei aufmacht. Fehlt der
    Eintrag, gilt der Startwert oben.
    """
    try:
        import einstellungen
        wert = einstellungen.hole('lach_schwelle')
        if wert is not None:
            return float(wert)
    except Exception:
        pass
    return SCHWELLE_VORGABE


def marker(name=''):
    """Wie das Lachen im Text auftaucht.

    Runde Klammern und kleingeschrieben, also die Form, in der Untertitel und
    Drehbücher so etwas seit jeher notieren -- das liest sich für Ramzi im
    Streifen richtig, und es kommt bei mir als das an, was es ist: eine
    Regieanweisung zum Satz, kein gesprochenes Wort.

    `normalisiere()` im Assistenten macht daraus „lacht", und das kollidiert
    mit keinem Reflex-Bruchstück -- nachgesehen, nicht gehofft.
    """
    if name in ('Giggle', 'Snicker'):
        return '(kichert)'
    if name == 'Belly laugh':
        return '(lacht laut)'
    return '(lacht)'
