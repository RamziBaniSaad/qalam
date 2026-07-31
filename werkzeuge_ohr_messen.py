"""Das Ohr nachmessen -- ohne Mikrofon, ohne dass jemand sprechen muss.

WARUM ES DAS GIBT:
    Am 31.07.2026 war die Sprachschicht spuerbar langsam: Ramzi rief "Noor",
    und erst beim dritten Versuch kam der Wach-Ton. Zwei Erklaerungen lagen
    auf dem Tisch (das genaue Modell ist zu langsam / die Warteschlange staut
    sich), und keine war gemessen. Raten hilft hier nicht: beide Erklaerungen
    fuehren zu voellig verschiedenen Umbauten.

    Nachmessen brauchte bis dahin immer Ramzi und ein Mikrofon. Das ist der
    teuerste Messaufbau, den es gibt -- er muss da sein, er muss sprechen, und
    jeder Durchlauf ist anders. Deshalb hier: Piper spricht die Testsaetze
    selbst, und die fertigen Bilder gehen direkt in dieselben Funktionen, die
    auch das echte Mikrofon fuettert. Reproduzierbar, jederzeit, allein.

Aufruf:
    python werkzeuge_ohr_messen.py
    python werkzeuge_ohr_messen.py --nur-flink
"""
import argparse
import os
import sys
import time
import wave

import numpy as np

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, 'src'))

import wake_word                                    # noqa: E402
from wake_word import FRAME_LEN, FRAME_MS, RATE, WECKWORT, Weckwort   # noqa: E402

# Die Saetze, an denen es haengt. Der erste ist der wichtigste: nur der Name,
# nichts dahinter -- genau das, was Ramzi sagt, wenn er auf den Ton wartet.
SAETZE = [
    'Noor.',
    'Noor?',
    'Noor, wie spät ist es?',
    'Noor, welcher Tag ist heute?',
    'Noor, ich wollte dich mal etwas fragen, und zwar geht es um die Verzögerung, '
    'die wir heute den ganzen Tag hatten.',
]

# x_low-Stimmen liefern 16 kHz -- genau das, was das Ohr erwartet. Bei einer
# medium-Stimme (22,05 kHz) muesste erst umgerechnet werden, und eine
# Umrechnung im Messaufbau ist eine Fehlerquelle, die die Messung verfaelscht.
STIMME = 'de_DE-eva_k-x_low'
ORDNER = os.path.join(HIER, 'stimmen', 'messung')


def _wav_bauen(text, ziel):
    """Satz von Piper sprechen lassen und als WAV ablegen."""
    if os.path.exists(ziel):
        return ziel
    from voice_output import nach_wav
    nach_wav(text, ziel, STIMME)
    return ziel


def _frames_lesen(pfad):
    """WAV in dieselben 30-ms-Bilder zerlegen, die die Aufnahmeschleife baut."""
    with wave.open(pfad, 'rb') as f:
        if f.getframerate() != RATE:
            raise SystemExit(f'{pfad}: {f.getframerate()} Hz, gebraucht werden {RATE}')
        rohdaten = f.readframes(f.getnframes())
    proben = np.frombuffer(rohdaten, dtype=np.int16)
    return [proben[i:i + FRAME_LEN] for i in range(0, len(proben) - FRAME_LEN, FRAME_LEN)]


def _stille_anhaengen(frames, sekunden):
    """Nachlaufende Stille -- die Aufnahmeschleife legt sie mit in den Puffer.

    Das ist kein Schoenheitsdetail: der Mitlauscher sieht nur, was die
    Aufnahmeschleife in `_laufend` gelegt hat, und dort landen die Stillebilder
    NICHT. Genau darum geht es in der Messung unten."""
    leer = np.zeros(FRAME_LEN, dtype=np.int16)
    return frames + [leer] * int(sekunden * 1000 / FRAME_MS)


def messen(nur_flink=False):
    os.makedirs(ORDNER, exist_ok=True)
    ohr = Weckwort(lambda *a: None)

    print('Lade Modelle …')
    t0 = time.time()
    _ = ohr.flink
    t_flink = time.time() - t0
    t0 = time.time()
    if not nur_flink:
        _ = ohr.modell
    t_modell = time.time() - t0
    print(f'  Wachmodell ({ohr.flink_name}) geladen in {t_flink:.1f}s')
    print(f'  genaues Modell ({ohr.modell_name}) geladen in {t_modell:.1f}s')
    print()

    gate = wake_word.MINDEST_FRAMES
    print(f'Riegel im Mitlauscher: mindestens {gate} Bilder '
          f'= {gate * FRAME_MS / 1000:.2f}s Ton, sonst wird nicht hingesehen.')
    print()

    for satz in SAETZE:
        name = ''.join(c if c.isalnum() else '_' for c in satz)[:40]
        pfad = _wav_bauen(satz, os.path.join(ORDNER, f'{name}.wav'))
        frames = _frames_lesen(pfad)
        dauer = len(frames) * FRAME_MS / 1000

        # Was der Mitlauscher wirklich sieht: die letzten drei Sekunden,
        # nachlaufende Stille eingeschlossen.
        mit_nachlauf = _stille_anhaengen(frames, wake_word.NACHLAUF_FRAMES * FRAME_MS / 1000)
        schnipsel = mit_nachlauf[-wake_word.BLICK_FRAMES:]

        print(f'--- {satz!r}')
        print(f'    {len(frames)} Bilder = {dauer:.2f}s Sprache, '
              f'Ausschnitt {len(schnipsel)} Bilder'
              f'   {"UNTER dem Riegel -> Mitlauscher sieht NIE hin" if len(schnipsel) < gate else "über dem Riegel"}')
        t0 = time.time()
        vorlaeufig = ohr._hoer_kurz(schnipsel)
        t_kurz = time.time() - t0
        treffer = bool(vorlaeufig and WECKWORT.search(vorlaeufig))
        print(f'    Wachmodell   {t_kurz:5.2f}s -> {vorlaeufig!r}   Name gefunden: {treffer}')

        if not nur_flink:
            gehoert = {}
            ohr.beim_wecken = lambda t, e, _g=gehoert: _g.update(text=t, endgueltig=e)
            ohr.folge_bis = 0.0
            t0 = time.time()
            ohr._pruefe(_stille_anhaengen(frames, 4.0), True)
            t_genau = time.time() - t0
            print(f'    small {t_genau:5.2f}s -> {gehoert.get("text")!r}'
                  f'   Echtzeitfaktor {t_genau / (dauer + 4.0):.2f}')
        print()

    print('So liest man das:')
    print('  * Echtzeitfaktor deutlich unter 1 = das Modell ist NICHT der Engpass.')
    print('  * "UNTER dem Riegel" = der Wach-Ton kann für diesen Ruf gar nicht')
    print('    kommen, egal wie schnell die Rechner sind.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Das Ohr nachmessen, ohne Mikrofon')
    p.add_argument('--nur-flink', action='store_true', help='nur das schnelle Modell')
    messen(**vars(p.parse_args()))
