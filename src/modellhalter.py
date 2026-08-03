"""Das Diktat-Modell nur dann auf der Karte halten, wenn es gebraucht wird.

Der Anlass ist Ramzis Rechnung vom 03.08.2026: `large-v3-turbo` liegt mit
**2084 MB** auf der Grafikkarte, und zwar von der Anmeldung bis zum
Herunterfahren -- auch wenn seit Stunden niemand diktiert hat. Zusammen mit dem
Ohr und der Grundlast stand die Karte bei 6,1 von 8 GB. Das ist unter seiner
harten Grenze (7 GB, siehe `wake_word._platz_auf_karte`), aber es lässt für ein
Spiel keine zwei Gigabyte mehr übrig.

Der Handel, den dieses Modul macht:

* Geladen wird **beim Tastendruck**, nicht beim Start. Das Laden läuft in einem
  eigenen Faden los, WÄHREND Ramzi schon spricht -- die Wartezeit fällt also
  nicht auf ihn, sondern in die Zeit, in der er ohnehin redet.
* Entladen wird nach einer Ruhezeit ohne Diktat. Zwei Werte, weil zwei Lagen:
  im Alltag darf es großzügig sein (Vorgabe 3 Minuten, sonst lädt es bei jedem
  zweiten Satz neu), im Spiel muss es knapp sein (Vorgabe 10 Sekunden).
* **Startet ein Spiel, während das Modell nur wartet, geht es sofort weg.**
  Ramzis ausdrückliche Ansage: er will nicht erst die Ruhezeit abwarten, bis
  seine Karte frei ist.

Beide Zeiten stehen in `noor-einstellungen.json` und werden bei jeder Runde neu
gelesen -- ein verschobener Regler auf der Tafel wirkt sofort, ohne Neustart.

DIE SICHERHEITSSCHRAUBE, die vorher nicht nötig war: früher lud das Modell beim
Anmelden, also zu einem Zeitpunkt, an dem die Karte verlässlich leer war. Jetzt
lädt es zu einem beliebigen Zeitpunkt -- unter Umständen mitten im Spiel, wenn
schon 5 GB belegt sind. Deshalb wird vor JEDEM Laden nachgesehen, wie viel frei
ist, und im Zweifel landet das Modell auf der CPU. Langsam ist ärgerlich,
abgestürzt ist teuer.
"""
import ctypes
import gc
import os
import threading
import time

PROTOKOLL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'modell.log')

# Was das Modell auf der Karte kostet, am 03.08.2026 gemessen: `large-v3-turbo`
# in **int8_float16** braucht **1028 MB**. In float16 wären es 2069 gewesen --
# umgestellt, weil Ramzis Hintergrund (Wallpaper Engine) bei 6,6 GB
# verschwunden ist und dieses Gigabyte der größte Posten war, an dem sich ohne
# Verlust drehen ließ: derselbe Testton ergab **Wort für Wort denselben Text**,
# das Rechnen dauerte 1,31 statt 1,06 s.
KOSTET_MB = 1030
GRENZE_MB = 7000
# KEIN zusätzlicher Puffer, und das ist eine Korrektur aus der Messung, nicht
# Leichtsinn. Erst standen hier die 450 MB, mit denen das Ohr rechnet -- damit
# wäre das Modell IM NORMALFALL auf der CPU gelandet, also genau dann, wenn
# Platz da war. Die 7000 SIND der Sicherheitsabstand: die Karte hat 8151 MB,
# unter der Grenze liegen also 1,1 GB Luft. Ein zweiter Puffer darüber macht
# aus Vorsicht eine Fehlfunktion.
#
# Mit der Quantisierung ist es nicht mehr knapp: Ohr plus Grundlast liegen bei
# 4750-4900 MB, dazu 1030 macht knapp 5900. Das ist über ein Gigabyte unter der
# Grenze -- und, wichtiger, unter den 6,6 GB, bei denen Windows Ramzis
# Hintergrund abgeräumt hat. Der nächste Hebel wäre das Ohr mit seinen 3,2 GB,
# nicht dieser Wert.

# Der Zustand, den Windows meldet, wenn etwas den ganzen Schirm belegt.
# QUNS_BUSY (2), QUNS_RUNNING_D3D_FULL_SCREEN (3), QUNS_APP (7) aus shellapi.h.
# Das ist der zuverlässige Teil der Spielerkennung: er braucht keine Liste und
# greift auch bei einem Spiel, das ich nicht kenne.
_VOLLBILD = (2, 3, 7)

# Der unzuverlässige, aber nötige Teil: ein Spiel im randlosen Fenster meldet
# kein Vollbild. Namen ohne `.exe`, klein geschrieben. Ergänzen ist billig.
SPIELE = (
    'fortniteclient-win64-shipping',
    'fortniteclient-win64-shipping_be',
    'fortniteclient-win64-shipping_eac',
    'javaw',                    # Minecraft (Java)
    'minecraft',
    'minecraft.windows',        # Bedrock
    'valorant-win64-shipping',
    'gta5', 'gtav', 'gta5_enhanced',
    'rocketleague',
    'cs2',
    'r5apex',                   # Apex Legends
)


def _vollbild_laeuft():
    """Belegt gerade etwas den ganzen Schirm? Kostet einen Aufruf, keine Liste."""
    try:
        zustand = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(zustand)) != 0:
            return False
        return zustand.value in _VOLLBILD
    except Exception:
        return False


def _spiel_prozess():
    """Läuft ein Spiel aus der Liste? Teurer als der Blick oben, also seltener."""
    try:
        import psutil
        for p in psutil.process_iter(['name']):
            name = (p.info.get('name') or '').lower()
            if name.endswith('.exe'):
                name = name[:-4]
            if name in SPIELE:
                return True
    except Exception:
        pass
    return False


def _belegt_mb():
    """Wie viel der Karte gerade belegt ist. None, wenn nicht messbar."""
    import subprocess
    try:
        roh = subprocess.check_output(
            [os.path.join(os.environ.get('WINDIR', r'C:\Windows'),
                          'System32', 'nvidia-smi.exe'),
             '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
            text=True, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
        return int(roh.splitlines()[0])
    except Exception:
        return None


def _notiz(text):
    """Ein Protokoll, weil Qalam ohne Konsole läuft.

    Ohne das wäre "wie lange dauert das Laden eigentlich" eine Schätzung. Es ist
    genau die Frage, die Ramzi gestellt hat, und sie soll sich nachlesen lassen,
    statt jedes Mal neu gemessen zu werden.
    """
    try:
        with open(PROTOKOLL, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")}  {text}\n')
    except OSError:
        pass


class Modellhalter:
    """Hält das Diktat-Modell -- oder eben gerade nicht.

    Nach außen sieht es aus wie das Modell selbst: `hole()` gibt dasselbe
    Wertepaar zurück, das `create_local_model()` liefert. Wer es benutzt, merkt
    vom Kommen und Gehen nichts, außer dass der erste Griff nach einer Pause
    ein paar Sekunden braucht.
    """

    def __init__(self, bauen):
        self._bauen = bauen
        self._modell = None
        # Schützt das Laden und Entladen gegeneinander. Nicht die Transkription
        # selbst -- die dauert Sekunden, und solange dürfte die Wache nicht
        # blockiert sein.
        self._schloss = threading.RLock()
        self._zaehler_schloss = threading.Lock()
        self._benutzer = 0
        self._zuletzt = time.monotonic()
        self._stop = threading.Event()
        self._wache = None
        self._prozess_zeit = 0.0
        self._prozess_spiel = False
        self._spiel_vorher = False

    # ------------------------------------------------------------------ Werte
    @staticmethod
    def _sekunden(name, vorgabe):
        try:
            from einstellungen import hole
            wert = hole(name)
            return vorgabe if wert is None else int(wert)
        except Exception:
            return vorgabe

    def _ruhe_sekunden(self):
        """Ruhezeit im Alltag. 0 heißt: nie entladen (Regler ganz links = aus)."""
        return self._sekunden('qalam_ruhe_sekunden', 180)

    def _spiel_sekunden(self):
        """Ruhezeit im Spiel. 0 heißt: sofort weg, sobald das Diktat fertig ist."""
        return self._sekunden('qalam_spiel_sekunden', 10)

    # ------------------------------------------------------------------ Spiel
    def spiel_laeuft(self):
        if _vollbild_laeuft():
            return True
        # Die Prozessliste ist teurer als der Blick auf den Vollbildzustand,
        # also nur alle fünf Sekunden. Ein Spiel startet nicht in Millisekunden.
        jetzt = time.monotonic()
        if jetzt - self._prozess_zeit >= 5.0:
            self._prozess_zeit = jetzt
            self._prozess_spiel = _spiel_prozess()
        return self._prozess_spiel

    # ------------------------------------------------------------------ Modell
    def hole(self):
        """Das Modell -- lädt es, wenn es gerade nicht da ist. Blockiert."""
        with self._schloss:
            if self._modell is None:
                self._lade()
            self._zuletzt = time.monotonic()
            return self._modell

    def wecke(self):
        """Laden anstoßen, ohne zu warten. Beim Tastendruck aufgerufen.

        Der ganze Sinn der Übung: der Ladevorgang läuft, während Ramzi spricht.
        Bis er fertig ist, steht das Modell -- er wartet also auf nichts.
        """
        self._zuletzt = time.monotonic()
        if self._modell is not None:
            return
        threading.Thread(target=self._still_laden, daemon=True).start()

    def _still_laden(self):
        try:
            self.hole()
        except Exception as e:
            _notiz(f'Vorladen fehlgeschlagen: {e}')

    def _lade(self):
        """Immer mit Blick auf die Karte. Siehe die Sicherheitsschraube oben."""
        belegt = _belegt_mb()
        erzwinge = None
        if belegt is not None and belegt + KOSTET_MB > GRENZE_MB:
            erzwinge = ('cpu', 'int8')
            _notiz(f'{belegt} MB belegt -- Modell weicht auf die CPU aus.')

        start = time.monotonic()
        self._modell = self._bauen(erzwinge)
        dauer = time.monotonic() - start
        wo = 'CPU' if erzwinge else 'Karte'
        _notiz(f'geladen auf {wo} in {dauer:.1f} s'
               + (f' (vorher {belegt} MB belegt)' if belegt is not None else ''))

    def entlade(self, grund=''):
        """Weg von der Karte. Gibt zurück, ob wirklich etwas entladen wurde."""
        with self._schloss:
            if self._modell is None:
                return False
            start = time.monotonic()
            try:
                # ctranslate2 kann das ausdrücklich, statt sich auf den
                # Aufräumer zu verlassen. Wenn nicht, tut es das Loslassen
                # unten auch -- der Speicher hängt am Objekt, nicht an Python.
                self._modell[1].model.unload_model()
            except Exception:
                pass
            self._modell = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            _notiz(f'entladen in {time.monotonic() - start:.1f} s'
                   + (f' ({grund})' if grund else ''))
            return True

    @property
    def geladen(self):
        return self._modell is not None

    # ------------------------------------------------------------- Benutzung
    def an(self):
        with self._zaehler_schloss:
            self._benutzer += 1

    def ab(self):
        with self._zaehler_schloss:
            self._benutzer = max(0, self._benutzer - 1)
            # Die Ruhezeit beginnt, wenn das Diktat FERTIG ist, nicht wenn es
            # anfing. Sonst könnte ein langer Satz das Modell wegwerfen, kaum
            # dass er getippt ist.
            self._zuletzt = time.monotonic()

    # ----------------------------------------------------------------- Wache
    def starte_wache(self):
        if self._wache and self._wache.is_alive():
            return
        self._stop.clear()
        self._wache = threading.Thread(target=self._schleife, daemon=True)
        self._wache.start()

    def stoppe_wache(self):
        self._stop.set()

    def _schleife(self):
        while not self._stop.wait(1.0):
            try:
                self._runde()
            except Exception as e:
                _notiz(f'Wache: {e}')

    def _runde(self):
        spiel = self.spiel_laeuft()
        gerade_gestartet = spiel and not self._spiel_vorher
        self._spiel_vorher = spiel

        if self._modell is None:
            return
        with self._zaehler_schloss:
            if self._benutzer > 0:
                return

        if gerade_gestartet:
            self.entlade('Spiel gestartet')
            return

        grenze = self._spiel_sekunden() if spiel else self._ruhe_sekunden()
        if not spiel and grenze <= 0:
            return                       # 0 im Alltag heißt: nie entladen
        if time.monotonic() - self._zuletzt >= grenze:
            self.entlade('Spiel, Ruhezeit' if spiel else 'Ruhezeit')
