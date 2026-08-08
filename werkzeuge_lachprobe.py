"""Lach-Probe: einmal ins Mikrofon lachen und die Zahl sehen.

WOFUER. Die Schwelle in `src/lachen.py` ist ein Startwert, kein Messergebnis --
wo Ramzis eigenes Lachen liegt und wo sein normales Reden, weiss niemand, bevor
es jemand gemessen hat. Dieses Werkzeug liefert genau diese Messung, ohne dass
er dafuer eine Datei aufmacht oder eine Protokollzeile suchen muss.

Aufruf ueber `noor\\werkzeuge\\noor-lachprobe.ps1`, oder direkt:

    venv\\Scripts\\python.exe werkzeuge_lachprobe.py            # 6 s aufnehmen
    venv\\Scripts\\python.exe werkzeuge_lachprobe.py 10         # 10 s aufnehmen
    venv\\Scripts\\python.exe werkzeuge_lachprobe.py datei.wav  # aus einer Datei

DAS VERFAHREN, und es ist absichtlich zweigeteilt: einmal lachen, einmal ganz
normal reden. Eine Zahl allein sagt nichts -- 0,42 ist hoch, wenn Reden bei 0,05
liegt, und wertlos, wenn Reden bei 0,40 liegt. Erst der ABSTAND zwischen beiden
sagt, wo die Schwelle hingehoert.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

import numpy as np                    # noqa: E402

import lachen                         # noqa: E402

RATE = lachen.RATE


def nimm_auf(sekunden):
    """Vom Mikrofon aufnehmen, mit Countdown davor.

    Der Countdown ist nicht Zierde: ohne ihn druecke ich auf Start und Ramzi
    lacht in die Sekunde, in der die Aufnahme noch gar nicht laeuft.

    UND ICH HALTE SOLANGE DEN MUND. Sonst misst diese Probe am Ende MEINE
    Stimme mit -- sein Lautsprecher steht neben seinem Mikrofon, und ein Satz
    von mir mitten in seinen sechs Sekunden macht die Messung wertlos, ohne
    dass man es der Zahl ansieht. Genau das waere die Sorte Fehler, gegen die
    diese Probe ueberhaupt gebaut ist.

    Der Weg dafuer ist schon da: `warteschlange.redet_merken(True)` ist die
    Markierung "Ramzi hat gerade das Wort", und meine Sprech-Zentrale wartet
    darauf. Sie verfaellt von selbst (REDET_ALTER), muss also aufgefrischt
    werden -- und kann bei einem Absturz dieser Probe nicht dauerhaft
    haengenbleiben. Deshalb Auffrischen statt einmal Setzen.
    """
    import sounddevice as sd

    import warteschlange

    schluss = threading.Event()

    def _platz_halten():
        while not schluss.is_set():
            warteschlange.redet_merken(True)
            schluss.wait(0.3)

    wache = threading.Thread(target=_platz_halten, daemon=True)
    wache.start()
    try:
        for rest in (3, 2, 1):
            print(f'   {rest} ...', flush=True)
            time.sleep(0.7)
        print(f'\n>>> JETZT -- {sekunden:.0f} Sekunden <<<\n', flush=True)
        daten = sd.rec(int(sekunden * RATE), samplerate=RATE, channels=1,
                       dtype='float32')
        sd.wait()
    finally:
        schluss.set()
        warteschlange.redet_merken(False)
    return np.ascontiguousarray(daten[:, 0])


PROBEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lachproben')


def sichere(audio, was):
    """Die Aufnahme aufheben -- der wichtigste Teil dieses Werkzeugs.

    ERST OHNE DAS GEBAUT, und der erste Lauf hat sofort gezeigt, warum das
    falsch war: Ramzis Lachen kam auf 0,034, wo die Testdatei 0,79 erreicht.
    Damit stand die Frage im Raum, ob das kleine Modell zu klein ist, ob sein
    Mikrofon zu leise ist oder ob ein Lachen auf Kommando einfach keins ist --
    und die einzige Art, das zu beantworten, waere gewesen, ihn zwanzigmal
    lachen zu lassen, einmal je Einstellung.

    Mit einer gespeicherten Aufnahme lacht er EINMAL, und alles Weitere --
    andere Modellgroessen, andere Fenster, Normalisierung -- probiere ich
    danach allein durch. Sein Ohr ist die knappe Ressource, nicht meine
    Rechenzeit.
    """
    try:
        import soundfile as sf
        os.makedirs(PROBEN, exist_ok=True)
        pfad = os.path.join(PROBEN, f'{was}-{time.strftime("%Y%m%d-%H%M%S")}.wav')
        sf.write(pfad, audio, RATE)
        return pfad
    except Exception as e:
        print(f'  (konnte die Aufnahme nicht sichern: {e})')
        return None


def zeige(audio, was):
    """Den Ton durchs Modell schicken und die Zahlen hinschreiben."""
    # Der PEGEL gehoert dazu und nicht in eine Fussnote: ist die Aufnahme zu
    # leise, sagt die Lach-Zahl nichts ueber das Modell aus, sondern ueber das
    # Mikrofon. Zwei Messwerte, die man verwechseln kann, sind schlimmer als
    # einer -- also stehen beide da.
    spitze = float(np.abs(audio).max()) if len(audio) else 0.0
    effektiv = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    hinweis = '  << sehr leise' if spitze < 0.05 else ''
    print(f'{was}: Pegel Spitze {spitze:.3f}, Effektivwert {effektiv:.4f}{hinweis}')

    fund = lachen.pruefe(audio)
    grenze = lachen.schwelle()
    if fund is None:
        print(f'{was}: nichts gemessen -- laeuft das Modell? '
              f'(Schwelle steht auf {grenze}; 0 heisst aus)')
        return None
    wert, klasse, dauer = fund
    urteil = 'ueber der Schwelle -> ' + lachen.marker(klasse) if wert >= grenze \
        else 'unter der Schwelle -- kein Marker'
    print(f'{was}: {wert:.3f}  ({klasse or "nichts aus der Lach-Familie"}, '
          f'{dauer * 1000:.0f} ms Rechenzeit)')
    print(f'{"":>{len(was)}}  {urteil}')
    return wert


def rate(lach_wert, red_wert):
    """Aus den zwei Messungen einen Schwellenvorschlag machen.

    Die Mitte zwischen beiden, mit etwas Abstand nach unten: ein verpasstes
    Lachen faellt niemandem auf, ein erfundenes schon. Genau deshalb liegt der
    Vorschlag naeher am Lachen als am Reden.
    """
    if lach_wert is None or red_wert is None:
        return
    if lach_wert <= red_wert:
        print('\nDas Modell hat beim Reden genauso stark angeschlagen wie beim '
              'Lachen. So ist keine Schwelle zu ziehen -- bitte nochmal, und '
              'beim Lachen wirklich lachen.')
        return

    # EINE UNTERGRENZE, und sie ist wichtiger als die Rechnung darunter.
    #
    # Im ersten echten Lauf kam Ramzis Lachen auf 0,034 und sein Reden auf
    # 0,000. Der Abstand war also "sauber" -- und der brave Vorschlag daraus
    # lautete 0,02. Das ist Unsinn: 0,02 liegt im Rauschen, und eine Schwelle
    # im Rauschen meldet Lachen bei Huesteln, Stuhlknarzen und Videoton. Sie
    # ist schlimmer als gar keine Erkennung, weil man ihr dann nicht mehr
    # glauben kann.
    #
    # Ein Verhaeltnis allein reicht als Kriterium also nicht; es braucht auch
    # einen absoluten Boden. Auf den Testtoenen erreicht echtes Lachen 0,79 --
    # 0,15 ist danach schon sehr grosszuegig.
    if lach_wert < 0.15:
        print(f'\nDein Lachen kommt nur auf {lach_wert:.3f}. Das ist zu wenig, '
              f'um daraus eine Schwelle zu machen --')
        print('zum Vergleich erreicht der Testton des Modells 0,79. Hier waere '
              'jede Schwelle im Rauschen,')
        print('und die meldete dann auch Huesteln und Videoton als Lachen. '
              'Kein Vorschlag.')
        print('Die Aufnahme ist gesichert; damit gehe ich der Ursache nach '
              '(Pegel, Modellgroesse, Fenster).')
        return

    vorschlag = red_wert + (lach_wert - red_wert) * 0.55
    print(f'\nAbstand: {lach_wert - red_wert:.3f}')
    print(f'Vorschlag fuer die Schwelle: {vorschlag:.2f}')
    print(f'Setzen mit:  venv\\Scripts\\python.exe -m src.einstellungen '
          f'lach_schwelle={vorschlag:.2f}')


if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else ''

    if arg.lower().endswith('.wav'):
        import soundfile as sf
        daten, rate_datei = sf.read(arg, always_2d=True, dtype='float32')
        if rate_datei != RATE:
            print(f'Achtung: Datei hat {rate_datei} Hz, das Modell erwartet '
                  f'{RATE} Hz.')
        zeige(np.ascontiguousarray(daten[:, 0]), os.path.basename(arg))
        sys.exit(0)

    dauer = float(arg) if arg else 6.0

    print('=' * 62)
    print('  LACH-PROBE -- zwei Durchgaenge, damit der Abstand sichtbar wird')
    print('=' * 62)
    print(f'\nSchwelle steht gerade auf {lachen.schwelle()}\n')

    print('DURCHGANG 1 von 2:  bitte LACHEN.')
    ton_lach = nimm_auf(dauer)
    pfad_lach = sichere(ton_lach, 'lachen')
    lach = zeige(ton_lach, 'Lachen ')

    print('\nDURCHGANG 2 von 2:  bitte ganz normal REDEN (irgendein Satz).')
    ton_reden = nimm_auf(dauer)
    pfad_reden = sichere(ton_reden, 'reden')
    reden = zeige(ton_reden, 'Reden  ')

    rate(lach, reden)

    if pfad_lach or pfad_reden:
        print(f'\nAufnahmen liegen in: {PROBEN}')
        print('Damit probiere ich die Einstellungen durch, ohne dass du '
              'nochmal lachen musst.')
