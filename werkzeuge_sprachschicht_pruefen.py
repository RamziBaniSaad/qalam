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
def warte_bis_ich_still_bin(hoechstens=120.0):
    """Erst messen, wenn ich wirklich nichts sage.

    Am 15.08.2026 hat diese Pruefung fuenf Fehler gemeldet, die es nicht gab:
    sie lief, waehrend ich gerade selbst einen Satz sprach -- die Klammer hielt
    also voellig zu Recht. Eine Pruefung, die den eigenen Messaufbau nicht
    kennt, ist schlimmer als keine: sie schickt einen auf die Suche nach einem
    Fehler, der gar nicht da ist.
    """
    ende = time.time() + hoechstens
    gewartet = False
    while w.noor_hat_das_wort(nachhall=w.NACHHALL_SEK):
        if time.time() > ende:
            print('  ABBRUCH: ich rede seit zwei Minuten -- so ist nichts zu '
                  'messen.')
            sys.exit(2)
        gewartet = True
        time.sleep(0.5)
    if gewartet:
        print('(gewartet, bis ich selbst still war)')


def still():
    """Die Zustandslogik allein -- kein Ton, kein Ohr noetig."""
    warte_bis_ich_still_bin()
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


def laut_echo():
    """Der Fall, der zweimal durchgefallen ist: hoere ich mich selbst? (F1)

    Ramzis Test 1 am 15.08.2026. Zweimal sind meine eigenen Saetze als SEINE
    Aeusserung durchgegangen -- beim zweiten Mal 754 Zeichen an Claude. Der
    Fehler war nur mit laufender Musik zu sehen, weil sie den Stimmenmelder
    wachhaelt und die Aeusserung deshalb schon offen war, bevor ich anfing.

    Deshalb prueft das hier MIT Musik, und deshalb steht es ueberhaupt hier:
    was zweimal durchgefallen ist, darf nie wieder ungeprueft bleiben.
    """
    print('== Hoere ich mich selbst? (Test 1) ==')
    protokoll = os.path.join(HIER, 'ohr.log')
    if not os.path.exists(protokoll):
        print('  ABBRUCH: kein ohr.log -- laeuft das Ohr?')
        _fehler.append('ohr.log fehlt')
        return

    def zeilen():
        with open(protokoll, encoding='utf-8', errors='replace') as f:
            return f.readlines()

    vorher = len(zeilen())

    wav = os.path.join(HIER, 'assets', 'noor_wach.wav')
    ps = ('$p = New-Object System.Media.SoundPlayer "%s"; '
          '1..200 | ForEach-Object { $p.PlaySync() }' % wav)
    musik = subprocess.Popen(
        ['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    text = ('Das ist die Selbstpruefung der Sprachschicht. Ich rede jetzt eine '
            'Weile am Stueck, damit mein eigenes Mikrofon genug von mir '
            'zurueckbekommt. Waehrenddessen laeuft absichtlich ein Geraeusch '
            'mit, denn genau das hat den Fehler damals sichtbar gemacht. Kein '
            'einziger dieser Saetze darf als Aeusserung von Ramzi gelten, und '
            'vor allem darf nichts davon an Claude gehen.')
    p = subprocess.Popen([sys.executable,
                          os.path.join(HIER, 'src', 'voice_output.py'), text],
                         cwd=HIER, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    p.wait(timeout=180)
    musik.terminate()
    # Das genaue Modell wertet erst nach seiner Redepause aus -- abwarten,
    # sonst zaehle ich ein Protokoll, das noch gar nicht geschrieben ist.
    time.sleep(9)

    neu = zeilen()[vorher:]
    gehoert = [z for z in neu if "gehoert: '" in z and "gehoert: ''" not in z]
    echo = [z for z in neu if 'mein Echo' in z]
    weiter = [z for z in neu if 'gebe weiter' in z]
    print('   %d Bloecke mit Text gehoert, %d als mein Echo verworfen, '
          '%d an Claude uebergeben' % (len(gehoert), len(echo), len(weiter)))
    for z in echo[:4]:
        print('   ' + z.strip()[:120])
    pruefe('nichts davon ging an Claude', len(weiter), 0)
    pruefe('ich habe mich selbst gehoert (sonst war es kein Test)',
           len(gehoert) > 0, True)
    pruefe('und alles davon verworfen', len(echo) >= len(gehoert), True)


def laut_zucken():
    """Zuckt die Musik zwischen zwei meiner Saetze kurz hoch? (Test 13)

    Der Fall, fuer den in der Waechterin der Nachhall steht: zwischen zwei
    Auftraegen ist die Liste schon leer und der naechste Satz noch nicht
    angemeldet. Fehlt dort etwas, geht die Musik in jeder Luecke kurz hoch --
    genau das Zucken, das Ramzi frueher im Video gesehen hat.

    Deshalb DREI getrennte Auftraege und nicht einer: bei einem einzigen gibt
    es die Luecke gar nicht, und der Test waere ein Selbstbetrug.
    """
    print('== Zuckt die Musik zwischen zwei Saetzen? ==')
    import lautstaerke
    sprich = os.path.join(os.path.expanduser('~'), 'noor', 'werkzeuge',
                          'noor-sprich.ps1')
    if not os.path.exists(sprich):
        print('  uebersprungen: noor-sprich.ps1 nicht gefunden')
        return

    wav = os.path.join(HIER, 'assets', 'noor_wach.wav')
    ps = ('$p = New-Object System.Media.SoundPlayer "%s"; '
          '1..200 | ForEach-Object { $p.PlaySync() }' % wav)
    musik = subprocess.Popen(
        ['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    time.sleep(3)

    for satz in ('Erster Satz von drei, das ist die Zuck-Pruefung.',
                 'Zweiter Satz, und zwischen den Saetzen liegt die Luecke.',
                 'Dritter und letzter Satz dieser Pruefung.'):
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy',
                        'Bypass', '-File', sprich, '-Text', satz],
                       capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    begonnen = time.time()
    while time.time() - begonnen < 25 and not lautstaerke.gedaempft():
        time.sleep(0.1)
    if not lautstaerke.gedaempft():
        musik.terminate()
        pruefe('es wurde ueberhaupt gedaempft', False, True)
        return

    zucker = punkte = leise = 0
    war_laut = False
    start = time.time()
    while time.time() - start < 90:
        ich_rede = w.noor_hat_das_wort(nachhall=w.NACHHALL_SEK)
        gedaempft = lautstaerke.gedaempft()
        if ich_rede:
            punkte += 1
            if gedaempft:
                leise += 1
            elif not war_laut:
                zucker += 1
                war_laut = True
        if gedaempft:
            war_laut = False
        if not ich_rede and time.time() - start > 5 and w.noor_still_seit() > 6:
            break
        time.sleep(0.1)
    musik.terminate()

    print('   %d Messpunkte waehrend ich redete, davon %d mit leiser Musik'
          % (punkte, leise))
    pruefe('der Redezug war lang genug zum Messen', punkte > 20, True)
    pruefe('kein Zucken zwischen den Saetzen', zucker, 0)


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
        laut_echo()
        laut_zucken()
        laut_musik()
    else:
        print('(--laut fuer Sprech- und Musikprobe -- braucht ein laufendes Ohr)')
    print()
    print('FEHLER: %d %s' % (len(_fehler), _fehler or ''))
    sys.exit(1 if _fehler else 0)
