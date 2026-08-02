"""Messung statt Vermutung: warum reagiert die Tastenkombination auf macOS nicht?

Ramzi am 02.08.2026: die Rechte sind in den Systemeinstellungen gesetzt, die
Tastenkombination tut trotzdem nichts. Damit ist die Frage nicht mehr "fehlt
ein Recht", sondern **auf wen bucht macOS das Recht** -- und das kann man
messen, statt es zu raten. Beim letzten Anlauf im Juli ist genau daran viel
Zeit verbrannt worden, weil beides gleichzeitig vermutet wurde.

Die Messung trennt zwei Faelle, die sich von aussen gleich anfuehlen:

  A) Es kommt GAR KEIN Tastendruck an   -> macOS liefert nicht. Dann liegt es
     an der Rechte-Zuordnung (welcher Prozess gilt als verantwortlich), nicht
     am Code.
  B) Tastendruecke kommen an, aber die Kombination loest nicht aus
     -> das Liefern klappt, die Erkennung nicht. Ganz anderer Fehler.

WICHTIG -- warum das hier und nicht im Bundle steht: die Signatur der App
haengt nur am Startskript in Qalam.app. `src/` liegt ausserhalb. Diese Datei
kann also geaendert werden, ohne die erteilten Rechte zu zerstoeren; ein
Neubau des Bundles wuerde sie ungueltig machen.

ANGESCHALTET WIRD NUR AUF ANSAGE. Ein dauerhaft mitlaufendes Tastenprotokoll
waere ein Mitschneider, und so etwas laesst man nicht "vorsichtshalber" an.
Es laeuft nur, wenn die Markerdatei ~/.qalam-diagnose existiert, hoert nach
90 Sekunden von selbst auf und schreibt Tastennamen, keine Texte.
"""
import os
import sys
import threading
import time
from datetime import datetime

LOG = os.path.expanduser('~/Library/Logs/qalam-mac.log')
MARKER = os.path.expanduser('~/.qalam-diagnose')
DAUER = 90


def _schreib(zeile):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write('%s  %s\n' % (datetime.now().strftime('%H:%M:%S'), zeile))
    except OSError:
        pass


def _wer_bin_ich():
    """Wie sich der Prozess gegenueber macOS ausweist -- das ist der Kern."""
    zeilen = ['--- Start -------------------------------------------------']
    zeilen.append('ausfuehrbare Datei : %s' % sys.executable)
    zeilen.append('Arbeitsverzeichnis : %s' % os.getcwd())
    try:
        from Foundation import NSBundle
        b = NSBundle.mainBundle()
        zeilen.append('Bundle-Pfad        : %s' % b.bundlePath())
        zeilen.append('Bundle-Kennung     : %s' % b.bundleIdentifier())
    except Exception as e:
        zeilen.append('Bundle             : nicht lesbar (%s)' % e)
    try:
        import HIServices
        zeilen.append('AXIsProcessTrusted : %s' % HIServices.AXIsProcessTrusted())
    except Exception as e:
        zeilen.append('AXIsProcessTrusted : Fehler (%s)' % e)
    try:
        from utils import ConfigManager
        zeilen.append('eingestellte Taste : %s' % ConfigManager.get_config_value(
            'recording_options', 'activation_key'))
    except Exception as e:
        zeilen.append('eingestellte Taste : nicht lesbar (%s)' % e)
    return zeilen


def _tastenprobe():
    """Kommt ueberhaupt etwas an? Ein eigener, roher Listener neben dem echten."""
    getroffen = {'anzahl': 0}
    try:
        from pynput import keyboard
    except Exception as e:
        _schreib('Tastenprobe: pynput nicht ladbar (%s)' % e)
        return

    def bei_druck(taste):
        getroffen['anzahl'] += 1
        if getroffen['anzahl'] <= 25:      # nur die ersten, kein Mitschnitt
            _schreib('  Taste gesehen: %r' % (taste,))

    try:
        lauscher = keyboard.Listener(on_press=bei_druck)
        lauscher.start()
        _schreib('Tastenprobe laeuft %d s -- jetzt Tasten druecken.' % DAUER)
        time.sleep(DAUER)
        lauscher.stop()
    except Exception as e:
        _schreib('Tastenprobe: Listener gescheitert (%s)' % e)
        return

    if getroffen['anzahl'] == 0:
        _schreib('ERGEBNIS: KEIN einziger Tastendruck angekommen -> macOS '
                 'liefert nichts. Fall A: Rechte-Zuordnung, nicht der Code.')
    else:
        _schreib('ERGEBNIS: %d Tastendruecke angekommen -> macOS liefert. '
                 'Fall B: die Erkennung der Kombination ist der Fehler.'
                 % getroffen['anzahl'])


def starte_wenn_gewuenscht():
    """Aus main.py aufgerufen. Tut nichts, solange die Markerdatei fehlt."""
    if sys.platform != 'darwin' or not os.path.exists(MARKER):
        return
    for z in _wer_bin_ich():
        _schreib(z)
    threading.Thread(target=_tastenprobe, daemon=True).start()
