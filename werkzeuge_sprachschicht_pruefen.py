"""Prueft das Fundament der Sprachschicht -- wer hat gerade das Wort.

WARUM ES DAS GIBT: an diesem System wird jeden Tag etwas geaendert, und am
15.08.2026 haben sechs ungepruefte Anlaeufe mehr zerstoert als gebracht. Die
teuerste Lehre war nicht inhaltlich, sondern methodisch -- MESSEN SCHLAEGT
UEBERLEGEN. Fuenf geratene Reparaturen lagen daneben, eine Messung traf sofort.

Also steht das Messen jetzt hier und nicht in einem Wegwerf-Skript.

    python werkzeuge_sprachschicht_pruefen.py           still, ohne Ton
    python werkzeuge_sprachschicht_pruefen.py --laut    mit echtem Sprechen
                                                        und echter Musik

Der stille Lauf prueft die Zustandslogik und braucht nichts Laufendes. Der
laute prueft die ganze Kette und setzt ein LAUFENDES OHR voraus (sonst gibt es
keine Waechterin, die die Musik zurueckstellt).
"""
import os
import subprocess
import sys
import threading
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, 'src'))

import warteschlange as w        # noqa: E402
import voice_output as vo        # noqa: E402

_fehler = []


def pruefe(name, ist, soll):
    ok = ist == soll
    print(('  OK   ' if ok else '  FEHL ') + f'{name}: ist={ist} soll={soll}')
    if not ok:
        _fehler.append(name)


# --------------------------------------------------------------------------
def still():
    """Die Zustandslogik allein -- kein Ton, kein Ohr noetig."""
    print('== Die Klammer um meinen Redezug ==')
    for datei in (w.REDEZUG, w.WAECHTER):
        try:
            os.remove(datei)
        except OSError:
            pass
    pruefe('kalt: niemand redet', w.noor_hat_das_wort(), False)

    w.noor_redezug_herzschlag()
    pruefe('ein Herzschlag zaehlt sofort', w.noor_hat_das_wort(), True)
    time.sleep(1.2)
    pruefe('und verfaellt von selbst', w.noor_hat_das_wort(), False)
    pruefe('Nachhall haelt laenger',
           w.noor_hat_das_wort(nachhall=w.NACHHALL_SEK), True)
    time.sleep(2.5)
    pruefe('auch der Nachhall endet',
           w.noor_hat_das_wort(nachhall=w.NACHHALL_SEK), False)

    print('== Zwei Sprecher gleichzeitig ==')
    e1, e2 = threading.Event(), threading.Event()
    vo._redezug_starten(e1)
    vo._redezug_starten(e2)
    time.sleep(0.4)
    pruefe('beide halten', w.noor_hat_das_wort(), True)
    e2.set()
    time.sleep(0.6)
    pruefe('einer geht, der andere haelt', w.noor_hat_das_wort(), True)
    e1.set()
    time.sleep(1.3)
    pruefe('beide losgelassen', w.noor_hat_das_wort(), False)

    print('== Der Deckel: nichts darf ewig halten ==')
    e3 = threading.Event()
    vo._redezug_starten(e3, hoechstens=0.5)
    time.sleep(0.2)
    pruefe('laeuft', w.noor_hat_das_wort(), True)
    time.sleep(1.8)
    pruefe('Deckel greift', w.noor_hat_das_wort(), False)
    e3.set()

    print('== Die Waechterin meldet sich ==')
    pruefe('laeuft ein Ohr?', w.waechter_lebt(), True)


# --------------------------------------------------------------------------
def laut_reden():
    """Ein echter Satz: schliesst die Klammer wirklich jede Luecke?"""
    print('== Ein echter Satz, gemessen ==')
    text = ('Das ist die Pruefung der Sprachschicht. Ich rede ein paar '
            'Sekunden am Stueck, damit sich messen laesst, ob mich das Ohr '
            'in jeder Luecke als sprechend erkennt.')
    p = subprocess.Popen([sys.executable,
                          os.path.join(HIER, 'src', 'voice_output.py'), text],
                         cwd=HIER, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    klammer_ja = alt_nein = punkte = 0
    begonnen = time.time()
    while p.poll() is None and time.time() - begonnen < 90:
        punkte += 1
        if w.noor_hat_das_wort():
            klammer_ja += 1
            if not w.noor_spricht_gerade():
                alt_nein += 1
        time.sleep(0.1)
    print('   Redezug: %.1f s, davon %.1f s, in denen die ALTE Auskunft '
          '"ich rede nicht" gesagt haette.' % (klammer_ja * 0.1, alt_nein * 0.1))
    pruefe('die Klammer hat gehalten', klammer_ja > 5, True)
    pruefe('sie hat echte Luecken geschlossen', alt_nein > 0, True)
    time.sleep(1.5)
    pruefe('und wieder losgelassen', w.noor_hat_das_wort(), False)


def laut_musik():
    """Ist die Musik die Anzeige fuers offene Fenster? (F3/F4)"""
    print('== Musik als Anzeige ==')
    import einstellungen
    import lautstaerke
    fenster = float(einstellungen.hole('folge_sekunden') or 5)

    wav = os.path.join(HIER, 'assets', 'noor_wach.wav')
    ps = ('$p = New-Object System.Media.SoundPlayer "%s"; '
          '1..80 | ForEach-Object { $p.PlaySync() }' % wav)
    quelle = subprocess.Popen(
        ['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    da = False
    for _ in range(40):
        for s in lautstaerke._sitzungen():
            try:
                if s.Process and s.Process.pid == quelle.pid:
                    da = True
            except Exception:
                pass
        if da:
            break
        time.sleep(0.25)
    pruefe('fremde Tonquelle laeuft', da, True)
    if not da:
        quelle.terminate()
        return

    # Ein ausdruecklicher Stopp -- derselbe Weg wie der Knopf auf der Tafel.
    befehle = os.path.join(HIER, '.befehle')
    os.makedirs(befehle, exist_ok=True)
    with open(os.path.join(befehle, 'stopp.teil'), 'w') as f:
        f.write('')
    os.replace(os.path.join(befehle, 'stopp.teil'),
               os.path.join(befehle, 'stopp.cmd'))
    t0 = time.time()

    leise_ab = laut_ab = None
    while time.time() - t0 < fenster + 9:
        gedaempft = lautstaerke.gedaempft()
        if gedaempft and leise_ab is None:
            leise_ab = time.time() - t0
        if leise_ab is not None and not gedaempft and laut_ab is None:
            laut_ab = time.time() - t0
            break
        time.sleep(0.1)
    quelle.terminate()

    print('   leise ab %s, wieder laut ab %s (Fenster: %.0f s)'
          % ('%.1f s' % leise_ab if leise_ab else 'NIE',
             '%.1f s' % laut_ab if laut_ab else 'NIE', fenster))
    pruefe('leise, sobald gestoppt wird (F4)',
           leise_ab is not None and leise_ab < 1.5, True)
    pruefe('leise, solange sein Fenster offen ist (F3)',
           laut_ab is not None and laut_ab > fenster - 1.0, True)
    pruefe('aber nicht laenger (keine haengende Daempfung)',
           laut_ab is not None and laut_ab < fenster + 5.0, True)
    if lautstaerke.gedaempft():
        print('   Merker lag noch -- von Hand zurueckgestellt:',
              lautstaerke.zuruecksetzen(), 'Programme')


# --------------------------------------------------------------------------
if __name__ == '__main__':
    still()
    if '--laut' in sys.argv:
        laut_reden()
        laut_musik()
    else:
        print('(--laut fuer Sprech- und Musikprobe -- braucht ein laufendes Ohr)')
    print()
    print('FEHLER: %d %s' % (len(_fehler), _fehler or ''))
    sys.exit(1 if _fehler else 0)
