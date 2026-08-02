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

# Aelter als das, und der Merker gehoert zu einem Lauf, der abgestuerzt ist.
# Dann nichts fortsetzen: lieber ein stehendes Video als eines, das Stunden
# spaeter von allein losredet.
HOECHSTALTER = 300

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


def anhalten():
    try:
        return asyncio.run(_anhalten())
    except Exception:
        return []


def fortsetzen():
    try:
        return asyncio.run(_fortsetzen())
    except Exception:
        return []


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
