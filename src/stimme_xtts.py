"""Meine Stimme mit XTTS-v2 -- Sprecher Ludvig, auf der Grafikkarte.

Warum es diese Datei gibt und nicht einfach ein Modellwechsel in
`voice_output.py`: XTTS hat eine Eigenheit, die Piper nicht hat -- es
**halluziniert**. Es schiebt gelegentlich ein bis sechs Sekunden erfundene
Sprache ein ("pumpt kein neneralaneur, mutbar beim non-dengel"). Am 02.08.2026
in 240 gemessenen Proben eingekreist; die Ursache und alle Zahlen stehen in
`noor/werkzeuge/stimme-regeln.md`. Kurzfassung:

  * XTTS zerlegt den Text intern am Satzende. Ist das ERSTE Stueck kurz, faengt
    es an zu fabulieren: erster Satz unter 10 Zeichen -> nur 21 % sauber, kein
    fruehes Satzende -> 85 %.
  * Deshalb wird der Text hier VORHER umgebaut: fruehe Satzenden werden zu
    Kommas, Zahlen und Einheiten ausgeschrieben.
  * Und deshalb wird NICHT in Einzelsaetze zerlegt -- das war meine erste
    Vermutung und sie war genau verkehrt herum: Zerlegen macht aus einem
    sicheren langen Aufruf mehrere gefaehrliche kurze.

Mit diesen Regeln kam Ludvig in der Messung auf 30 von 30 sauberen Ausgaben --
als einzige Stimme ohne einen Ausrutscher.

Auf der Karte rechnet XTTS schneller, als man zuhoert (Echtzeitfaktor 0,88).
Deshalb wird stueckweise geliefert: erster Ton nach etwa 0,7 s statt nach 4-5 s
am Stueck. Auf der CPU waere der Faktor 2,2 -- dort wuerde es nach jedem Wort
haengen, also wird dort gar nicht erst gestroemt.
"""
import os
import re
import threading

os.environ.setdefault('COQUI_TOS_AGREED', '1')

SPRECHER = 'Ludvig Milivoj'
TEMPO = 1.15
RATE = 24000

# Belegung gemessen: Modell 1838 MB, Spitze im Betrieb 2256 MB. Mit Reserve.
BRAUCHT_MB = 2600
# Ramzis harte Grenze: nie ueber 7 der 8 GB, sonst stuerzt der Rechner ab.
GRENZE_MB = 7000

# Sekunden je Zeichen bei Tempo 1,15 -- aus den sauberen Messungen vom
# 02.08.2026. Nur fuer die Untertitel-Einteilung, nicht fuer die Tonerzeugung.
JE_ZEICHEN = 0.075

_modell = None
_sprecher_daten = None
_geraet = None
_sperre = threading.Lock()


# --- Textaufbereitung -------------------------------------------------------

_EINHEITEN = [
    ('GHz', 'Gigahertz'), ('GB', 'Gigabyte'), ('MB', 'Megabyte'),
    ('KB', 'Kilobyte'), ('TB', 'Terabyte'), ('Hz', 'Hertz'),
    ('CPU', 'C P U'), ('GPU', 'G P U'), ('ms', 'Millisekunden'),
]

_EINER = ['null', 'eins', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben',
          'acht', 'neun', 'zehn', 'elf', 'zwölf', 'dreizehn', 'vierzehn',
          'fünfzehn', 'sechzehn', 'siebzehn', 'achtzehn', 'neunzehn']
_ZEHNER = ['', '', 'zwanzig', 'dreißig', 'vierzig', 'fünfzig', 'sechzig',
           'siebzig', 'achtzig', 'neunzig']


def _zahlwort(n):
    n = int(n)
    if n < 20:
        return _EINER[n]
    if n < 100:
        z, e = divmod(n, 10)
        return _ZEHNER[z] if e == 0 else '%sund%s' % (_EINER[e], _ZEHNER[z])
    if n < 1000:
        h, r = divmod(n, 100)
        wort = '%shundert' % ('ein' if h == 1 else _EINER[h])
        return wort if r == 0 else wort + _zahlwort(r)
    # Groesseres bleibt stehen: lieber eine Ziffer vorlesen lassen als eine
    # falsche Uebersetzung erfinden.
    return str(n)


def vorbereiten(text, mindest=40):
    """Text so umbauen, dass XTTS nicht ins Fabulieren geraet.

    Zwei Griffe, beide gemessen:
      1. Jedes Satzende, das vor Zeichen `mindest` liegt, wird zu einem Komma --
         der Sinn bleibt, die Sprechpause auch (XTTS pausiert am Komma hoerbar).
      2. Zahlen und Einheiten werden ausgeschrieben, sonst liest das Modell
         Satzzeichen mit vor ("Fertig, Punkt.").
    """
    text = ' '.join((text or '').split())
    if not text:
        return ''

    # 1 -- fruehe Satzenden entschaerfen. Von vorne, weil sich die Positionen
    # beim Ersetzen nicht verschieben (ein Zeichen gegen ein Zeichen).
    zeichen = list(text)
    for m in re.finditer(r'[.!?](?=\s+[A-ZÄÖÜ])', text):
        if m.start() < mindest:
            zeichen[m.start()] = ','
            # Der Grossbuchstabe danach wird klein -- sonst liest es sich wie
            # ein Satzanfang und XTTS betont ihn auch so.
            nach = m.end()
            while nach < len(zeichen) and zeichen[nach].isspace():
                nach += 1
            if nach < len(zeichen):
                zeichen[nach] = zeichen[nach].lower()
    text = ''.join(zeichen)

    # 2 -- Zahlen und Einheiten
    text = re.sub(r'(\d+),(\d+)',
                  lambda m: '%s Komma %s' % (_zahlwort(m.group(1)),
                                             ' '.join(_zahlwort(z)
                                                      for z in m.group(2))),
                  text)
    text = re.sub(r'\b(\d{1,3})\b', lambda m: _zahlwort(m.group(1)), text)
    for kurz, lang in _EINHEITEN:
        text = re.sub(r'\b%s\b' % re.escape(kurz), lang, text)
    return text


# --- Modell -----------------------------------------------------------------

def _freier_platz_mb():
    try:
        import torch
        if not torch.cuda.is_available():
            return 0.0, 0.0
        frei, gesamt = torch.cuda.mem_get_info()
        return frei / 1024 / 1024, gesamt / 1024 / 1024
    except Exception:
        return 0.0, 0.0


def geraet_waehlen():
    """Karte, wenn Platz ist -- sonst gar nicht.

    Die CPU ist ausdruecklich KEIN Rueckfall fuer XTTS: dort dauert ein Satz
    rund elf Sekunden. Ist die Karte voll (Spiel, viele Fenster), spricht
    lieber Piper sofort als XTTS nach einer halben Minute.
    """
    frei, gesamt = _freier_platz_mb()
    if not gesamt:
        return None
    belegt = gesamt - frei
    if frei < BRAUCHT_MB or belegt + BRAUCHT_MB > GRENZE_MB:
        return None
    return 'cuda'


def bereit():
    """Ist das Modell WIRKLICH schon geladen?

    Bewusst streng: "es waere Platz da" reicht nicht. Das Laden dauert 16 s,
    und wer beim ersten Satz danach wartet, ist Ramzi. Solange geladen wird,
    spricht Piper -- eine schlichtere Stimme sofort ist besser als die schoene
    nach einer Viertelminute. Ausserdem haengt die Abtastrate des Tonstroms an
    dieser Entscheidung; sie muss feststehen, bevor der Strom aufgeht.
    """
    return _modell is not None


def vorwaermen():
    """Im Hintergrund laden, damit der zweite Satz schon Ludvig ist.

    Wird beim Start der Sprachausgabe angestossen. Faellt es durch (kein Platz
    auf der Karte, kein CUDA), passiert nichts weiter -- `bereit()` bleibt
    dann einfach falsch und Piper spricht.
    """
    if _modell is not None:
        return

    def _lauf():
        try:
            _laden()
        except Exception as e:
            print('[Stimme] XTTS liess sich nicht laden: %s' % e, flush=True)

    threading.Thread(target=_lauf, daemon=True).start()


def _laden():
    global _modell, _sprecher_daten, _geraet
    with _sperre:
        if _modell is not None:
            return _modell
        geraet = geraet_waehlen()
        if geraet is None:
            return None
        from TTS.api import TTS
        tts = TTS('tts_models/multilingual/multi-dataset/xtts_v2',
                  progress_bar=False).to(geraet)
        m = tts.synthesizer.tts_model
        _sprecher_daten = tuple(m.speaker_manager.speakers[SPRECHER].values())
        _modell, _geraet = m, geraet
        return _modell


# --- Wortzeiten: mein eigenes Ohr hoert meinen eigenen Mund ab --------------

_ohr = None

# Klein und nur zum Ausmessen: erkannt werden muss hier nichts, der Text steht
# ja fest -- gebraucht werden allein die Zeitpunkte. `base` kostet ~250 MB auf
# der Karte und braucht fuer einen gesprochenen Satz Bruchteile einer Sekunde.
OHR_MODELL = 'base'
OHR_BRAUCHT_MB = 400


def _ohr_holen():
    global _ohr
    if _ohr is not None:
        return _ohr
    frei, gesamt = _freier_platz_mb()
    if not gesamt or frei < OHR_BRAUCHT_MB or \
            (gesamt - frei) + OHR_BRAUCHT_MB > GRENZE_MB:
        return None
    from faster_whisper import WhisperModel
    _ohr = WhisperModel(OHR_MODELL, device='cuda', compute_type='float16')
    return _ohr


def wortzeiten(ton, rate=RATE):
    """Echte Zeitpunkte je Wort aus dem erzeugten Ton.

    Ramzis Highlight war die mitlaufende Wort-Hervorhebung, und sie ging beim
    Wechsel auf XTTS verloren: bei Piper liess sie sich aus der Zeichenzahl
    schaetzen, weil Piper gleichmaessig spricht. XTTS dehnt und pausiert je nach
    Satz -- die Schaetzung sprang daneben, er hat es sofort gesehen.

    Statt besser zu raten wird jetzt gemessen: der fertige Ton geht durch die
    Spracherkennung, und die liefert zu jedem Wort Anfang und Ende. Das ist
    dieselbe Idee wie beim Zurueckhoeren gegen das Kauderwelsch -- ich pruefe
    mein Ergebnis, statt es vorherzusagen.

    Gibt [] zurueck, wenn kein Platz auf der Karte ist. Dann bleibt die
    Hervorhebung eben aus; eine falsche waere schlimmer.
    """
    import numpy as np
    m = _ohr_holen()
    if m is None:
        return []
    try:
        stuecke_, _ = m.transcribe(ton.astype(np.float32) / 32768.0,
                                   language='de', beam_size=1,
                                   word_timestamps=True)
        aus = []
        for s in stuecke_:
            for w in (s.words or []):
                aus.append({'w': w.word.strip(), 'ab': float(w.start),
                            'd': max(0.05, float(w.end - w.start))})
        return aus
    except Exception:
        return []


# --- Sprechen ---------------------------------------------------------------

def stuecke(text, anzeigen=None, tempo=None):
    """Erzeugt den Ton stueckweise. Liefert (anzeigetext, int16-Feld).

    Der GANZE Text geht in EINEM Aufruf raus -- Zerlegen ist die Ursache des
    Kauderwelschs. Geliefert wird trotzdem stueckweise, weil XTTS auf der Karte
    schneller rechnet, als man zuhoert: erster Ton nach ~0,7 s.

    Der Anzeigetext haengt nur am ERSTEN Stueck; danach kommt ein leerer, damit
    der Untertitel-Streifen stehen bleibt statt zu springen. Das ist Ramzis
    Befund vom 02.08.2026: "die Sprünge passen nicht mehr mit den Worten, weil
    du das anders aussprichst." Er hat recht, und die Ursache war meine
    Schaetzung -- ich habe die Tondauer je Anzeige aus der ZEICHENZAHL
    gerechnet. Bei Piper ging das durch, weil es gleichmaessig spricht; XTTS
    dehnt und pausiert je nach Satz. Eine falsche Hervorhebung ist schlimmer
    als gar keine, also faellt sie hier weg, bis es echte Wortzeiten gibt.

    Angezeigt wird der URSPRUENGLICHE Text, nicht der umgebaute: Ramzi soll
    lesen, was ich sagen wollte, nicht meine Kommas fuers Modell.
    """
    import numpy as np

    m = _laden()
    if m is None:
        return
    tempo = TEMPO if tempo is None else tempo
    fuers_modell = vorbereiten(text)
    if not fuers_modell:
        return

    lat, spk = _sprecher_daten
    teile = [np.clip(s.detach().cpu().numpy(), -1.0, 1.0)
             for s in m.inference_stream(fuers_modell, 'de', lat, spk,
                                         speed=tempo)]
    if not teile:
        return
    ganz = (np.concatenate(teile) * 32767).astype(np.int16)

    # Erst alles erzeugen, DANN aufteilen -- und zwar an der echten Gesamtdauer.
    #
    # Zwei Anlaeufe davor waren falsch, beide von Ramzi am Geraet widerlegt:
    # zuerst habe ich die Dauer je Anzeige aus der Zeichenzahl GESCHAETZT (die
    # Wort-Hervorhebung sprang daneben), dann den ganzen Text als EINE Anzeige
    # geschickt -- da zeigte der Streifen nur, was in eine Zeile passt, und der
    # Rest fiel weg. Der Zeichenanteil an einer GEMESSENEN Gesamtdauer stimmt
    # dagegen: er kann sich innerhalb einer Anzeige verschieben, aber die
    # Summe passt, und keine Anzeige geht verloren.
    #
    # Der Preis ist ehrlich: der erste Ton kommt jetzt nach der vollen
    # Rechenzeit (~4-5 s) statt nach 0,7 s. Ein Untertitel, der fehlt, ist
    # schlimmer als eine Ansage, die etwas spaeter anfaengt.
    anzeigen = [a for a in (anzeigen or []) if a and a.strip()]
    if not anzeigen:
        anzeigen = [' '.join((text or '').split())]

    gemessen = wortzeiten(ganz)

    # Kurze Aeusserungen noch einmal wuerfeln, wenn sie zu lang geraten sind.
    #
    # Unter ~40 Zeichen ist XTTS unzuverlaessig (gemessen: "Fertig." 2 von 12
    # sauber). Das laesst sich hier billig abfangen, weil der Ton ohnehin schon
    # durch die Erkennung geht: kommen deutlich mehr Woerter zurueck, als im
    # Text stehen, hat das Modell dazugedichtet -- dann neu erzeugen. Ein
    # kurzer Satz kostet dabei nur ein bis zwei Sekunden.
    soll_woerter = len(fuers_modell.split())
    versuche = 0
    while (len(fuers_modell) < 40 and gemessen and versuche < 2
           and len(gemessen) > soll_woerter + 2):
        versuche += 1
        teile = [np.clip(s.detach().cpu().numpy(), -1.0, 1.0)
                 for s in m.inference_stream(fuers_modell, 'de', lat, spk,
                                             speed=tempo)]
        if not teile:
            break
        ganz = (np.concatenate(teile) * 32767).astype(np.int16)
        gemessen = wortzeiten(ganz)
    zaehler = [len(a.split()) for a in anzeigen]

    if gemessen and sum(zaehler) and len(gemessen) >= sum(zaehler) * 0.7:
        # Echte Wortgrenzen: geschnitten wird dort, wo das naechste Wort
        # anfaengt, und die Hervorhebung bekommt gemessene Zeiten.
        i = 0
        ab_probe = 0
        for nr, a in enumerate(anzeigen):
            n = min(zaehler[nr], len(gemessen) - i)
            meine = gemessen[i:i + n]
            i += n
            if nr == len(anzeigen) - 1 or i >= len(gemessen):
                bis_probe = len(ganz)
            else:
                bis_probe = min(len(ganz),
                                int(gemessen[i]['ab'] * RATE))
            if bis_probe <= ab_probe or not meine:
                continue
            null = meine[0]['ab']
            worte = [{'w': w['w'], 'ab': w['ab'] - null, 'd': w['d']}
                     for w in meine]
            yield a, ganz[ab_probe:bis_probe], worte
            ab_probe = bis_probe
        return

    # Rueckfall ohne Messung: nach Zeichenanteil, ohne Hervorhebung.
    gesamt_zeichen = sum(len(a) for a in anzeigen) or 1
    ab = 0
    for nr, a in enumerate(anzeigen):
        bis = len(ganz) if nr == len(anzeigen) - 1 \
            else ab + int(len(a) / gesamt_zeichen * len(ganz))
        if bis > ab:
            yield a, ganz[ab:bis], []
        ab = bis

    # Geholten Kartenspeicher wieder hergeben.
    #
    # PyTorch behaelt einmal angeforderten Speicher als Vorrat -- fuer ein
    # Training ist das richtig, hier ist es gefaehrlich: die Belegung waechst
    # mit jedem gesprochenen Satz weiter, obwohl das Modell gleich gross
    # bleibt. Ramzi hat es am 02.08.2026 live gesehen, 7,4 von 8 GB waehrend
    # eines Diktats -- ein Schritt vor der Grenze, hinter der sein Rechner
    # abstuerzt. Das Freigeben kostet wenige Millisekunden und passiert NACH
    # dem letzten Stueck, stoert also keinen laufenden Satz.
    _aufraeumen()


def _aufraeumen():
    try:
        import torch
        if _geraet == 'cuda':
            torch.cuda.empty_cache()
    except Exception:
        pass


def belegung_mb():
    """Wie viel liegt gerade auf der Karte -- fuer Protokoll und Tafel."""
    frei, gesamt = _freier_platz_mb()
    return (gesamt - frei) if gesamt else 0.0
