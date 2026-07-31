"""Die Tonzeichen bauen.

Ramzis Wunsch (31.07.2026): jede Handlung soll ein eigenes, wiedererkennbares
Geräusch haben -- er will am Ton hören, was passiert ist, ohne hinzusehen.

Bewusst selbst erzeugt und nicht irgendwo geholt: so gehören alle Töne
zusammen (gleiche Hüllkurve, gleiche Lautstärke, gleiche Tonleiter), und es
hängt keine fremde Lizenz daran.

Die Sprache der Töne, damit sie ohne Nachdenken lesbar ist:

    aufwärts   = etwas geht auf / beginnt
    abwärts    = etwas geht zu / endet
    zwei kurze = angenommen, ich mache das lokal
    drei       = geht an Noor, dauert einen Moment

Einmal laufen lassen:  python werkzeuge_toene_bauen.py
"""
import math
import os
import struct
import wave

HIER = os.path.dirname(os.path.abspath(__file__))
ZIEL = os.path.join(HIER, 'assets')
RATE = 44100


def ton(frequenz, dauer, lautstaerke=0.35):
    """Ein einzelner Ton mit weichen Rändern.

    Die Ränder sind der ganze Trick: ein Sinus, der abrupt anfängt, knackt
    hörbar. 12 ms Ein- und Ausblenden machen aus einem Piepser einen Klang."""
    n = int(RATE * dauer)
    flanke = int(RATE * 0.012)
    daten = []
    for i in range(n):
        h = math.sin(2 * math.pi * frequenz * i / RATE)
        # zweite Harmonische leise dazu -- klingt weniger nach Testgenerator
        h += 0.18 * math.sin(4 * math.pi * frequenz * i / RATE)
        if i < flanke:
            h *= i / flanke
        elif i > n - flanke:
            h *= (n - i) / flanke
        daten.append(h * lautstaerke / 1.18)
    return daten


def stille(dauer):
    return [0.0] * int(RATE * dauer)


def schreibe(name, stuecke):
    pfad = os.path.join(ZIEL, name)
    daten = []
    for s in stuecke:
        daten.extend(s)
    with wave.open(pfad, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(b''.join(
            struct.pack('<h', max(-32767, min(32767, int(w * 32767)))) for w in daten))
    print(f'  {name}')


# Eine Tonleiter statt zufälliger Frequenzen: die Töne klingen dann wie eine
# Familie und nicht wie Geräte aus verschiedenen Jahrzehnten.
A4, C5, D5, E5, G5, A5, C6, E6 = 440, 523, 587, 659, 784, 880, 1047, 1319


def main():
    os.makedirs(ZIEL, exist_ok=True)
    print('Baue Tonzeichen nach assets/:')

    # "Ich höre dich" -- das wichtigste. Kurz und hell, damit Ramzi sofort
    # weiterreden kann, statt zu warten und sich zu fragen.
    schreibe('noor_wach.wav', [ton(A5, 0.07), stille(0.02), ton(E6, 0.10)])

    # "Verstanden, mache ich selbst" -- zwei zufriedene kurze Töne.
    schreibe('noor_reflex.wav', [ton(E5, 0.06), stille(0.015), ton(A5, 0.09)])

    # "Das geht an Noor" -- drei aufsteigende, klingt nach unterwegs sein.
    schreibe('noor_bruecke.wav', [ton(C5, 0.06), stille(0.01), ton(E5, 0.06),
                                  stille(0.01), ton(G5, 0.11)])

    # "Habe dich nicht verstanden" -- abwärts, ohne unfreundlich zu sein.
    schreibe('noor_nichts.wav', [ton(E5, 0.09), stille(0.02), ton(C5, 0.13)])

    # Fenster auf / zu.
    schreibe('noor_fenster_auf.wav', [ton(D5, 0.06), stille(0.01), ton(A5, 0.10)])
    schreibe('noor_fenster_zu.wav', [ton(A5, 0.06), stille(0.01), ton(D5, 0.11)])

    # Video -- tiefer und breiter, hebt sich vom normalen Fenster ab.
    schreibe('noor_video.wav', [ton(C5, 0.08), stille(0.015), ton(G5, 0.08),
                                stille(0.015), ton(C6, 0.13)])

    # Musik -- verspielter, drei Stufen aufwärts.
    schreibe('noor_musik.wav', [ton(G5, 0.05), stille(0.01), ton(C6, 0.05),
                                stille(0.01), ton(E6, 0.12)])

    print('fertig.')


if __name__ == '__main__':
    main()
