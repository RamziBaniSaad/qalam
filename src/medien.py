"""Videos anhalten statt leiser stellen -- und danach genau die wieder starten.

RAMZIS UNTERSCHEIDUNG (15.08.2026, 21:50), und sie ist der ganze Grund fuer
dieses Modul:

    "Bei Musik ist das egal, da reicht leiser. Bei Videos will ich alles
    sehen -- die sollen gestoppt werden statt leise zu sein."

Ein leiser gestelltes Video laeuft weiter, also verpasst er genau die Sekunden,
in denen wir reden. Bei Spotify ist das gleichgueltig, ein Lied laeuft eben
weiter.

UND ES LOEST EIN ZWEITES PROBLEM MIT, das ist sein eigener Gedanke: ein
gestopptes Video macht keinen Ton, also kann auch nichts davon ins Mikrofon
gelangen. Vorher stand im Protokoll immer wieder Text aus seinem YouTube-Video,
als haette er ihn gesagt ("Es ist auch so New York Tech Week..."), und die
Geraeuschunterdrueckung konnte das prinzipiell nicht sauber trennen -- sie
vergleicht nur Lautstaerke, nicht die Quelle. Zwei Probleme, eine Aenderung.

WIE: ueber Windows' eigene Medien-Steuerung (dieselbe, die die
Play-Pause-Taste auf der Tastatur bedient). Sie kennt jede Sitzung einzeln --
gemessen am 15.08.2026:

    Spotify.exe   pausiert   play=True    | Friction
    Chrome        spielt     pause=True   | Life as a Software Engineer in NYC

Damit ist Chrome gezielt anzuhalten, ohne Spotify anzufassen. Die
Play-Pause-TASTE waere der falsche Weg gewesen: sie trifft, was Windows gerade
fuer die aktuelle Sitzung haelt, und das kann genauso gut Spotify sein.

WAS ES BEWUSST NICHT TUT: etwas fortsetzen, das es nicht selbst angehalten hat.
Hat Ramzi sein Video von Hand pausiert, bleibt es pausiert. Derselbe Grundsatz
wie beim Lautstaerke-Merker -- und dort hat genau dessen Fehlen heute Abend
schon einmal weh getan (ein toter Eintrag im Merker hat die Musik dauerhaft
fuer "schon gedaempft" gehalten).
"""
import asyncio
import json
import os
import tempfile
import threading
import time

# Wer als "Video" gilt und deshalb angehalten wird statt leiser gestellt.
#
# Kleingeschrieben verglichen und als Teilzeichenkette, weil Windows die
# Kennungen unterschiedlich schreibt: der Chrome aus dem Store meldet sich
# anders als der installierte. Spotify steht ABSICHTLICH nicht hier --
# Musik soll leiser werden, nicht stehenbleiben.
VIDEO_APPS = ('chrome', 'msedge', 'edge', 'firefox', 'brave', 'opera',
              'vivaldi', 'zen')

# Angehalten wird nur, was laenger als das her ist -- sonst haelt ein Aufruf,
# der zweimal kurz nacheinander kommt, dasselbe Video zweimal an und merkt es
# beim zweiten Mal nicht mehr als "von mir angehalten".
_MERKER = os.path.join(tempfile.gettempdir(), 'noor_video_angehalten.json')

# Laenger darf keine Abfrage dauern. Die Medien-Steuerung antwortet sonst in
# Sekundenbruchteilen; haengt sie doch einmal, darf sie NICHT den Faden
# aufhalten, der gerade Ramzis Musik daempfen will.
_FRIST = 2.0

_sperre = threading.RLock()


def _lies_merker():
    try:
        with open(_MERKER, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _schreib_merker(liste):
    try:
        with open(_MERKER, 'w', encoding='utf-8') as f:
            json.dump(list(liste), f)
    except Exception:
        pass


def _ist_video(kennung):
    k = (kennung or '').lower()
    return any(app in k for app in VIDEO_APPS)


async def _sitzungen():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )
    mgr = await Manager.request_async()
    return list(mgr.get_sessions() or [])


async def _anhalten():
    """Jede spielende Video-Sitzung anhalten. Gibt die Kennungen zurueck."""
    angehalten = []
    for s in await _sitzungen():
        kennung = s.source_app_user_model_id
        if not _ist_video(kennung):
            continue                    # Spotify und Co. bleiben unberuehrt
        try:
            info = s.get_playback_info()
            # 4 = spielt. Nur was WIRKLICH laeuft, wird angehalten -- sonst
            # wuerden wir gleich ein von Hand pausiertes Video "wieder"
            # starten, das er selbst angehalten hat.
            if int(info.playback_status) != 4:
                continue
            if not info.controls.is_pause_enabled:
                continue
            if await s.try_pause_async():
                angehalten.append(kennung)
        except Exception:
            continue                    # eine stoerrische Sitzung darf den
            #                             Rest nicht mitreissen
    return angehalten


async def _fortsetzen(kennungen):
    """Genau die wieder starten, die wir selbst angehalten haben."""
    wieder = []
    for s in await _sitzungen():
        kennung = s.source_app_user_model_id
        if kennung not in kennungen:
            continue
        try:
            info = s.get_playback_info()
            if not info.controls.is_play_enabled:
                continue
            if await s.try_play_async():
                wieder.append(kennung)
        except Exception:
            continue
    return wieder


def _laufen(coro, standard):
    """Eine Abfrage mit Frist ausfuehren -- und lieber aufgeben als haengen."""
    ergebnis = [standard]

    def arbeit():
        try:
            ergebnis[0] = asyncio.run(coro())
        except Exception as e:
            print('[Medien] fehlgeschlagen: %s' % e, flush=True)

    f = threading.Thread(target=arbeit, daemon=True)
    f.start()
    f.join(_FRIST)
    if f.is_alive():
        print('[Medien] Abfrage haengt laenger als %.0fs -- aufgegeben'
              % _FRIST, flush=True)
        return standard
    return ergebnis[0]


def videos_anhalten():
    """Laufende Videos anhalten. Mehrfach aufrufbar, tut dann nichts mehr."""
    with _sperre:
        if _lies_merker():
            return []                   # haelt schon, nichts zu tun
        angehalten = _laufen(_anhalten, [])
        if angehalten:
            _schreib_merker(angehalten)
            print('[%s] [Medien] angehalten: %s'
                  % (time.strftime('%H:%M:%S'), ', '.join(angehalten)),
                  flush=True)
        return angehalten


def videos_fortsetzen():
    """Genau die Videos wieder starten, die wir selbst angehalten haben."""
    with _sperre:
        kennungen = _lies_merker()
        if not kennungen:
            return []
        # ZUERST den Merker leeren, dann fortsetzen.
        #
        # Anders herum waere er bei einem Fehler mitten drin fuer immer voll --
        # und ein voller Merker heisst "haelt schon an", also wuerde nie wieder
        # etwas angehalten. Genau diese Falle ist heute Abend schon zweimal
        # zugeschnappt (Tasten-Merker, Lautstaerke-Merker).
        _schreib_merker([])
        wieder = _laufen(lambda: _fortsetzen(kennungen), [])
        print('[%s] [Medien] fortgesetzt: %s'
              % (time.strftime('%H:%M:%S'),
                 ', '.join(wieder) if wieder else '(nichts mehr da)'),
              flush=True)
        return wieder


def haelt_an():
    """Halten wir gerade etwas an?"""
    with _sperre:
        return bool(_lies_merker())


def vergiss():
    """Merker leeren, ohne fortzusetzen -- fuer den Start des Ohrs.

    Ein Merker aus einer abgestuerzten Sitzung wuerde sonst dafuer sorgen, dass
    nie wieder etwas angehalten wird.
    """
    with _sperre:
        if _lies_merker():
            print('[Medien] alter Merker aus einer frueheren Sitzung verworfen',
                  flush=True)
        _schreib_merker([])


if __name__ == '__main__':
    import sys
    was = sys.argv[1] if len(sys.argv) > 1 else 'zeigen'
    if was == 'an':
        print(videos_anhalten())
    elif was == 'auf':
        print(videos_fortsetzen())
    else:
        async def _zeigen():
            for s in await _sitzungen():
                k = s.source_app_user_model_id
                i = s.get_playback_info()
                print('%-30s Zustand %s  Video: %s'
                      % (k, int(i.playback_status), _ist_video(k)))
        asyncio.run(_zeigen())
