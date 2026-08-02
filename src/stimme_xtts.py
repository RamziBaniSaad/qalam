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


# --- Sprechen ---------------------------------------------------------------

def stuecke(text, anzeigen, tempo=None):
    """Erzeugt den Ton und liefert ihn passend zu den Untertitel-Anzeigen.

    Erzeugt wird der GANZE Text in einem Aufruf (Regel: nicht zerlegen). Die
    Anzeigen bekommen daraus Scheiben, deren Laenge aus ihrem Zeichenanteil
    geschaetzt wird -- das reicht fuer die Wort-Hervorhebung und kostet nichts.
    Was am Ende uebrig bleibt, haengt an der letzten Anzeige.

    Liefert (anzeige, int16-Feld). Wirft nichts weiter als das, was das Modell
    selbst wirft -- der Aufrufer faellt dann auf Piper zurueck.
    """
    import numpy as np

    m = _laden()
    if m is None:
        return
    tempo = TEMPO if tempo is None else tempo
    fertig = vorbereiten(text)
    if not fertig:
        return

    anzeigen = [a for a in (anzeigen or []) if a.strip()] or [fertig]
    # Sollmenge je Anzeige in Samples. JE_ZEICHEN ist bei Tempo 1,15 gemessen;
    # ein anderes Tempo streckt oder staucht entsprechend.
    soll = [max(1, int(len(a) * JE_ZEICHEN * (TEMPO / max(tempo, 0.1)) * RATE))
            for a in anzeigen]

    puffer = np.zeros(0, dtype=np.int16)
    lat, spk = _sprecher_daten
    i = 0
    for stueck in m.inference_stream(fertig, 'de', lat, spk, speed=tempo):
        ton = stueck.detach().cpu().numpy()
        ton = np.clip(ton, -1.0, 1.0)
        puffer = np.concatenate([puffer, (ton * 32767).astype(np.int16)])
        while i < len(anzeigen) - 1 and len(puffer) >= soll[i]:
            yield anzeigen[i], puffer[:soll[i]]
            puffer = puffer[soll[i]:]
            i += 1
    if len(puffer):
        yield anzeigen[i], puffer
