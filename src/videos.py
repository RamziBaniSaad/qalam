"""Videos anhalten, solange Noor redet -- statt sie nur leiser zu machen.

Ramzis Auftrag vom 02.08.2026: laeuft ein YouTube-Video, ein Anime oder ein
Film, soll es **stehenbleiben**, wenn ich spreche. Daempfen reicht dort nicht:
das Bild laeuft weiter, und er verpasst genau die Sekunden, die ich zurede.
Musik ist der ausdrueckliche Gegenfall -- Spotify soll weiter nur leiser
werden, nicht anhalten.

## Warum nach APP unterschieden wird und nicht nach der Art

Windows fuehrt fuer jede abspielende Anwendung eine Medien-Sitzung, und die
meldet auch eine Art (Musik / Video / Bild). Der naheliegende Weg waere,
danach zu gehen. **Gemessen auf Ramzis Rechner am 02.08.2026 taugt das nicht:**

    Spotify.exe   Status=5 (spielt)  Art=1 (Musik)
    Chrome        Status=4 (Pause)   Art=1 (Musik)   <- ein YouTube-VIDEO

Chrome meldet ein Video als Musik. Haette ich nach der Art gebaut, waere
niemals ein Video angehalten worden -- und zwar lautlos, ohne Fehler.

Also nach App: alles, was gerade spielt, wird angehalten -- ausser den
Programmen in NUR_DAEMPFEN. Das ist die ehrlichere Regel, weil sie genau eine
Annahme hat, und die steht sichtbar in einer Liste.

## Was zurueckkommt, ist nur, was ich selbst angehalten habe

Beim Anhalten wird mitgeschrieben, WELCHE Apps es betraf. Fortgesetzt wird
nur diese Liste. Hat Ramzi selbst etwas pausiert, bevor ich sprach, bleibt es
pausiert -- ich starte ihm nichts, was er absichtlich angehalten hat.

## Warum eigener Prozess

Dieselbe Begruendung wie in lautstaerke.py: die Medien-Schnittstelle von
Windows kommt ueber COM/WinRT ins Haus, und genau diese Sorte Aufruf hat im
Juli schon einmal das Ohr getoetet. Sie hat im Ohr-Prozess nichts verloren.

    pythonw videos.py --anhalten     hält an, merkt sich was
    pythonw videos.py --fortsetzen   setzt genau das wieder fort
"""
import asyncio
import json
import os
import sys
import time

# Programme, die NIE angehalten werden -- die werden nur leiser (lautstaerke.py).
# Klein geschrieben verglichen, Windows meldet die Schreibweise nicht stabil.
#
# SPOTIFY LUEGT, und das ist der zweite Grund fuer diese Liste. Gemessen am
# 02.08.2026, waehrend Ramzis Musik nachweislich stand:
#
#     Medien-Sitzung sagt   Spotify: Status=5 (spielt)
#     echter Tonpegel sagt  Spotify: 0.0000  (still)   Chrome: 0.1936
#
# Spotify meldet dem System also weiter "spielt", obwohl es pausiert ist. Wer
# diese Liste eines Tages fuer ueberfluessig haelt, weil "der Status reicht
# doch": nein, tut er nicht. Ramzis Musik wuerde bei jedem Satz von mir
# angehalten, den er gar nicht hoert.
NUR_DAEMPFEN = {'spotify.exe'}

MERKER = os.path.join(os.environ.get('TEMP', '.'), 'noor-videos-angehalten.json')

# Was hier wirklich passiert ist -- eine Zeile je Aufruf.
#
# DER GRUND IST EIN FEHLER VOM 15.08.2026: das Anhalten lief in einem eigenen
# Prozess, und dessen Ausgabe ging nach DEVNULL. Ob ein Video angehalten wurde,
# war damit NIRGENDS ablesbar; ich habe zwei Abende lang geraten statt gelesen.
# Ein Vorgang, den man nicht nachsehen kann, ist ein Vorgang, ueber den man sich
# streiten muss.
PROTOKOLL = os.path.join(os.environ.get('TEMP', '.'), 'noor-videos.log')

# Aelter als das, und der Merker gehoert zu einem Lauf, der abgestuerzt ist.
# Dann nichts fortsetzen: lieber ein stehendes Video als eines, das Stunden
# spaeter von allein losredet.
#
# 1800 STATT 300 SEIT DEM 15.08.2026: seit das Video bis zum Ende von Ramzis
# Gespraechsfenster steht (und nicht mehr nur waehrend meines Satzes), kann die
# Spanne lang werden -- eine lange Antwort plus sein Fenster. Lief die Uhr ab,
# haette `fortsetzen` still nichts getan und sein Video waere stehengeblieben.
# Genau der Fehler, den diese Datei an anderer Stelle als den schlimmeren
# bezeichnet. Gegen den abgestuerzten Lauf sichert jetzt die Notbremse in
# voice_output, die nicht auf die Uhr schaut, sondern darauf, ob es den
# Waechter ueberhaupt gibt.
HOECHSTALTER = 1800

SPIELT = 5   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
PAUSE = 4    # ... .PAUSED

# Ab diesem Pegel gilt ein Programm als "macht wirklich Ton". Klein genug für
# leise Stellen, groß genug für das Grundrauschen einer stillen Sitzung.
PEGEL_SCHWELLE = 0.0005

# Mehrfach messen: in einer Sprechpause im Video steht der Pegel für einen
# Moment auf null. Ein einziger Blick würde daraus "spielt nicht" machen.
MESSUNGEN = 6
MESSABSTAND = 0.03


def _tonquellen():
    """Welche Programme machen JETZT wirklich Ton -- gemessen, nicht gefragt.

    DAS IST DER KERN DIESER DATEI, und er ist teuer bezahlt. Die Medien-
    Sitzungen von Windows melden einen Status, und der ist in BEIDE Richtungen
    unzuverlässig -- beides am 02.08.2026 an Ramzis Rechner gemessen:

        Spotify stand still  -> meldete Status=5 (spielt)
        Chrome spielte ein Video, Pegel 0.19 -> meldete Status=4 (Pause)

    Hätte ich nach dem Status gebaut, wäre das Anhalten sporadisch ausgefallen,
    ohne Fehlermeldung, ohne Muster -- die schlimmste Sorte Fehler. Der
    Tonpegel dagegen kommt aus der Audio-Schicht selbst und kann nicht lügen:
    entweder es kommen Samples am Ausgabegerät an oder nicht.

    Zurück kommen kleingeschriebene Namen ohne '.exe', damit sie zu den Namen
    der Medien-Sitzungen passen ('chrome.exe' -> 'chrome' -> 'Chrome').
    """
    try:
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    except Exception:
        return None            # ohne Messung: None heißt "weiß ich nicht"
    try:
        sitzungen = [s for s in AudioUtilities.GetAllSessions() if s.Process]
    except Exception:
        return None
    laut = set()
    for _ in range(MESSUNGEN):
        for s in sitzungen:
            try:
                if s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() > PEGEL_SCHWELLE:
                    laut.add(_schlicht(s.Process.name()))
            except Exception:
                continue
        time.sleep(MESSABSTAND)
    return laut


def _schlicht(name):
    """'Spotify.exe' und 'Spotify' auf denselben Nenner bringen."""
    name = (name or '').strip().lower()
    return name[:-4] if name.endswith('.exe') else name


async def _sitzungen():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Verwalter)
    verwalter = await Verwalter.request_async()
    return list(verwalter.get_sessions())


def _name(sitzung):
    try:
        return (sitzung.source_app_user_model_id or '').strip()
    except Exception:
        return ''


async def _anhalten():
    laut = _tonquellen()
    angehalten = []
    for s in await _sitzungen():
        name = _name(s)
        if not name or _schlicht(name) in {_schlicht(n) for n in NUR_DAEMPFEN}:
            continue
        try:
            if laut is None:
                # Messung nicht verfuegbar -- dann eben die Statusmeldung, sie
                # ist besser als nichts.
                spielt = s.get_playback_info().playback_status == SPIELT
            else:
                spielt = _schlicht(name) in laut
            if not spielt:
                continue
            if await s.try_pause_async():
                angehalten.append(name)
        except Exception:
            continue          # eine stoerrische App darf die anderen nicht aufhalten
    if angehalten:
        with open(MERKER, 'w', encoding='utf-8') as f:
            json.dump({'apps': angehalten, 'zeit': time.time()}, f)
    return angehalten


async def _fortsetzen():
    try:
        with open(MERKER, encoding='utf-8') as f:
            stand = json.load(f)
    except (OSError, ValueError):
        return []
    try:
        os.remove(MERKER)
    except OSError:
        pass
    if time.time() - stand.get('zeit', 0) > HOECHSTALTER:
        return []

    wollen = {a.lower() for a in stand.get('apps', [])}
    fortgesetzt = []
    for s in await _sitzungen():
        name = _name(s)
        if name.lower() not in wollen:
            continue
        try:
            # BEWUSST ohne Statuspruefung: der Status luegt (siehe
            # _tonquellen), und ein Video, das faelschlich stehenbleibt, ist
            # der schlimmere Fehler -- Ramzi saesse vor einem Bild, das nicht
            # weitergeht, und wuesste nicht warum. Der Preis dafuer: hat er
            # waehrend meines Satzes selbst pausiert, laeuft es wieder an.
            if await s.try_play_async():
                fortgesetzt.append(name)
        except Exception:
            continue
    return fortgesetzt


def _notiz(text):
    try:
        with open(PROTOKOLL, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%H:%M:%S")} {text}\n')
    except OSError:
        pass


def anhalten():
    try:
        apps = asyncio.run(_anhalten())
        _notiz(f'anhalten -> {apps or "nichts (es spielte nichts)"}')
        return apps
    except Exception as e:
        _notiz(f'anhalten GESCHEITERT: {e}')
        return []


def fortsetzen():
    try:
        apps = asyncio.run(_fortsetzen())
        _notiz(f'fortsetzen -> {apps or "nichts (kein Merker)"}')
        return apps
    except Exception as e:
        _notiz(f'fortsetzen GESCHEITERT: {e}')
        return []


def haengt_an():
    """Steht gerade etwas, das ICH angehalten habe?

    Der Waechter im Assistenten fragt das, bevor er aufgibt: waere er nur fuer
    die Lautstaerke zustaendig, wuerde er an einem Abend ohne Musik gar nicht
    erst hinschauen -- und Ramzis Video bliebe stehen."""
    return os.path.exists(MERKER)


def anstossen(an):
    """Anhalten/Fortsetzen in einem EIGENEN PROZESS -- der einzige erlaubte Weg.

    Diese Datei redet ueber COM/WinRT mit Windows, und genau diese Sorte Aufruf
    hat am 31.07.2026 das Ohr getoetet (Begruendung in lautstaerke.py). Sie darf
    im Ohr-Prozess nicht laufen, auch nicht in einem Faden.

    Am 15.08.2026 hat mich derselbe Punkt ein zweites Mal erwischt, nur
    andersherum: aus dem Ohr heraus kam die Medien-Abfrage gar nicht zurueck --
    `Abfrage haengt laenger als 2s`, weil dort COM schon in einem anderen Modus
    offen ist. Ein frischer Prozess hat beides Problem nicht.

    Rueckgabe: der Prozess, damit der Aufrufer bei Bedarf abwarten kann."""
    import subprocess
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__),
         '--anhalten' if an else '--fortsetzen'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


if __name__ == '__main__':
    if '--anhalten' in sys.argv:
        print('angehalten:', anhalten())
    elif '--fortsetzen' in sys.argv:
        print('fortgesetzt:', fortsetzen())
    else:
        # Ohne Schalter nur zeigen, was laeuft -- aendert nichts.
        async def zeigen():
            for s in await _sitzungen():
                i = s.get_playback_info()
                print('%-28s Status=%s Art=%s' % (
                    _name(s), i.playback_status, i.playback_type))
        asyncio.run(zeigen())
