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

## Warum dieses Modell und nicht das offensichtliche

Gemessen am 08.08.2026 nachts, auf genau diesem Rechner, nicht geschätzt:

    AST (86,6 Mio Parameter, das bekannte AudioSet-Modell)
        voll,  8 Kerne  ->  0,84 s
        int8,  8 Kerne  ->  0,56 s
    CED-tiny (5,5 Mio Parameter, hier eingebaut)
        int8,  1 Kern   ->  0,018 s
        int8,  4 Kerne  ->  0,012 s

Rund hundertmal billiger. Das ist der Unterschied zwischen „läuft nebenbei mit"
und „frisst acht von zwölf Kernen, während er zockt" -- und die halbe Sekunde
hätte genau die Zeit zurückgeholt, die wir am 07.08. aus der Brücke
herausgeholt haben (2,50 -> 2,00 s).

## Die Fenster -- der Punkt, an dem eine naive Fassung scheitert

Das Modell bewertet einen ganzen Schnipsel auf einmal. Wirft man ihm 15
Sekunden Rede mit einem kurzen Lacher darin hin, verdünnt sich das Lachen -- und
der Einbau sähe aus wie „erkennt nichts". Derselbe Testton, einmal am Stück und
einmal in Fenstern gemessen:

    ganzer Schnipsel am Stück   ->  0,48
    in 2-s-Fenstern, bestes     ->  0,79

Es wird also in Fenstern geschaut und das höchste genommen. Größe und Sprung
sind ausgemessen, nicht geschätzt (08.08.2026, Testtöne des Modells):

    Fenstergröße        1,0 s   1,5 s   2,0 s   3,0 s
    Lachen              0,663   0,723   0,789   0,756
    alles andere        0,000   0,000  ≤0,004  ≤0,004

    Sprung (bei 2 s)    1,00 s  0,50 s  0,25 s
    Lachen              0,789   0,794   0,830
    Katze               0,002   0,006   0,056
    Baby weint          0,006   0,007   0,104

2 s trifft am besten -- kürzere Fenster nehmen dem Modell den Zusammenhang,
längere verdünnen wieder. Beim Sprung ist 0,25 s trügerisch: das Lachen steigt,
aber Katze und weinendes Baby steigen MIT, und ein Erkenner, der bei fremden
Geräuschen mitzieht, wird bei Ramzis Videoton irgendwann falsch anschlagen.
0,5 s holt fast denselben Gewinn ohne diesen Nebeneffekt.

Kosten bei 10 s Ton: rund 170 ms. Immer noch ein Bruchteil eines einzigen
Durchgangs des großen Modells.

## Was hier bewusst NICHT entschieden ist

Die Schwelle. Der Abstand ist zwar groß -- Lachen 0,79 gegen Gesang 0,010,
Sprache 0,000, Sirene 0,000 --, aber das sind FREMDE Stimmen aus den Testtönen
des Modells. Wo Ramzis eigenes Lachen landet und wo sein Räuspern, sein Seufzen
und sein Fluchen im Spiel, weiß niemand, bevor es jemand an ihm gemessen hat.
Deshalb:

  * `SCHWELLE_VORGABE` ist ein Startwert, keine Wahrheit,
  * jede Prüfung landet mit ihrer Zahl im Protokoll,
  * und `werkzeuge/noor-lachprobe.ps1` zeigt die Zahl sofort.

Erst danach wird die Schwelle festgelegt. Ein geratener Wert, der still daneben
liegt, wäre genau die Sorte Fehler, die hier schon mehrfach Abende gekostet hat.

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
MODELL_ORDNER = os.path.join(PROJEKT, 'modelle',
                             'sherpa-onnx-ced-tiny-audio-tagging-2024-04-19')

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
FENSTER_S = 2.0
SPRUNG_S = 0.5

# Startwert, kein Messergebnis. Siehe oben.
SCHWELLE_VORGABE = 0.35

# Wie viele Klassen sich das Modell je Fenster abholen soll. 20 ist reichlich:
# steht Lachen nicht unter den zwanzig wahrscheinlichsten Geräuschen, war es
# keins. Mehr kostet nichts, aber es hilft auch nichts.
OBEN = 20

_tagger = None
_tagger_versucht = False
_schloss = threading.Lock()


def _hole_tagger():
    """Das Modell laden -- einmal, beim ersten Gebrauch, und nie wieder.

    NICHT beim Import: der Import läuft im Startpfad des Ohrs, und ein Ohr,
    das wegen eines Geräusch-Erkenners eine Sekunde später zuhört, ist ein
    schlechter Tausch. Das Laden dauert gemessen ~0,1 s, aber die Regel gilt
    unabhängig davon.
    """
    global _tagger, _tagger_versucht
    with _schloss:
        if _tagger is not None or _tagger_versucht:
            return _tagger
        _tagger_versucht = True
        modell = os.path.join(MODELL_ORDNER, 'model.int8.onnx')
        etiketten = os.path.join(MODELL_ORDNER, 'class_labels_indices.csv')
        if not (os.path.exists(modell) and os.path.exists(etiketten)):
            print(f'[Lachen] Modell fehlt unter {MODELL_ORDNER} -- '
                  f'Lacherkennung bleibt aus.')
            return None
        try:
            import sherpa_onnx
            _tagger = sherpa_onnx.AudioTagging(
                sherpa_onnx.AudioTaggingConfig(
                    model=sherpa_onnx.AudioTaggingModelConfig(
                        ced=modell,
                        # EIN Kern. Gemessen kostet ein Fenster damit 18 ms
                        # statt 12 ms bei vier -- die 6 ms sind es nicht wert,
                        # Ramzi drei Kerne wegzunehmen, während er spielt.
                        num_threads=1,
                        provider='cpu'),
                    labels=etiketten,
                    top_k=OBEN))
            print('[Lachen] CED-tiny geladen -- ich höre jetzt auch den Ton.')
        except Exception as e:
            print(f'[Lachen] Modell nicht ladbar: {e}')
            _tagger = None
        return _tagger


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
    tagger = _hole_tagger()
    if tagger is None or audio is None or len(audio) < int(0.3 * RATE):
        return None
    start = time.time()
    bester = 0.0
    bester_name = ''
    try:
        for stueck in _fenster(np.ascontiguousarray(audio, dtype=np.float32)):
            strom = tagger.create_stream()
            strom.accept_waveform(RATE, stueck)
            for ereignis in tagger.compute(strom):
                if ereignis.name in LACH_KLASSEN and ereignis.prob > bester:
                    bester = float(ereignis.prob)
                    bester_name = ereignis.name
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
