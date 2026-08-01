"""Ein Tonzeichen abspielen -- mit Lautstärke.

Warum es dieses Modul gibt (Ramzis Auftrag vom 01.08.2026 nachts): jeder Ton
soll seinen EIGENEN Regler bekommen, 0 bis 100 Prozent, und 0 % heißt aus.
Statt „Knopf an/aus plus Skala" nur noch eine Angabe -- die beiden konnten sich
widersprechen ("Ton an" und "Lautstärke 0" gleichzeitig), eine Angabe kann das
nicht.

DIE HÜRDE, DIE DEN GANZEN BAUWEG BESTIMMT: `winsound.PlaySound` kennt keine
Lautstärke. Es spielt eine WAV-Datei genau so laut, wie sie geschrieben ist.
Ein anderer Abspieler ist keine Option -- `sounddevice` kostet rund eine
Sekunde Import (steht in voice_output.py), und das merkt man bei einem Ton, der
im Moment einer Aktion kommen soll; PowerShell bekommt auf Ramzis Rechner
überhaupt keinen Ton heraus (siehe noor-ton-abspielen.ps1).

Also wird die Lautstärke IN DIE DATEI gerechnet: beim ersten Mal je Stufe
werden die Samples einmal skaliert und unter %TEMP%\\noor-toene abgelegt.
Abgespielt wird danach derselbe bewiesene winsound-Weg wie vorher, zur
Spielzeit ohne jeden Zusatzaufwand. Ein Zwischenspeicher im Temp-Ordner ist
richtig: die Dateien sind jederzeit neu berechenbar, sie gehören nicht ins Repo.

BEI 0 % WIRD NICHT ABGESPIELT, nicht "lautlos abgespielt". Ramzis Instinkt, und
der Grund ist größer als er dachte: von PowerShell aus startet jeder Ton einen
eigenen python.exe. Der Prozessstart ist das Teuerste an der ganzen Kette --
bei 0 % wird er ganz gespart, statt nur die Wiedergabe still zu machen.

Aufruf aus Python:
    import ton
    ton.spiele('noor_wach.wav')

Aufruf von außen (so kommt PowerShell an einen lauten Ton):
    python src/ton.py noor_arbeitet.wav
"""
import os
import sys
import wave
from array import array

HIER = os.path.dirname(os.path.abspath(__file__))
PROJEKT = os.path.dirname(HIER)
ASSETS = os.path.join(PROJEKT, 'assets')
LAGER = os.path.join(os.environ.get('TEMP') or PROJEKT, 'noor-toene')

if HIER not in sys.path:
    sys.path.insert(0, HIER)
import einstellungen  # noqa: E402  (erst nach sys.path -- eigener Prozess)


# Welche Datei an welchem Regler hängt.
#
# Der Schlüssel ist der DATEINAME, nicht ein neuer Kurzname: alle bestehenden
# Aufrufe heißen schon `ton('noor_wach.wav')`, und die sollen unverändert
# weiterlaufen. Ein zweites Namenssystem wäre nur eine Stelle mehr, an der
# etwas auseinanderlaufen kann.
#
# Mehrere Dateien dürfen auf denselben Regler zeigen -- "Fenster geöffnet" ist
# für Ramzi eine Sache, auch wenn dahinter drei Klänge liegen (Fenster, Video,
# Musik). Ein Regler je Klang wäre eine Liste, die niemand mehr überblickt.
KATALOG = {
    # -- Sprachchat / Steuerung
    'noor_arbeitet.wav':    'arbeitet',
    'noor_wach.wav':        'wach',
    'noor_bruecke.wav':     'bruecke',
    'noor_pause_an.wav':    'pause_an',
    'noor_pause_aus.wav':   'pause_aus',
    'noor_nichts.wav':      'nichts',
    'noor_reflex.wav':      'reflex',
    'noor_fenster_auf.wav': 'fenster_auf',
    'noor_video.wav':       'fenster_auf',
    'noor_musik.wav':       'fenster_auf',
    'noor_fenster_zu.wav':  'fenster_zu',
    # -- Qalam (Aufnahme)
    'rec_start.wav':        'aufnahme_start',
    'beep.wav':             'aufnahme_ende',
    'tool_on.wav':          'aufnahme_start',
    'tool_off.wav':         'aufnahme_ende',
}
# Die Countdown-Klänge (cd_1.wav ... cd_60.wav) hängen alle an einem Regler.
COUNTDOWN = 'countdown'

# Auf welches Vielfache die Prozentzahl gerundet wird, bevor eine Datei dafür
# entsteht. Ohne das Runden gäbe es bis zu 101 Zwischenspeicher-Dateien je Ton;
# mit 5er-Schritten sind es 21, und 5 % Lautstärkeunterschied hört niemand.
STUFE = 5


def regler(datei):
    """Welcher Regler gilt für diese Datei."""
    name = os.path.basename(datei)
    if name.startswith('cd_'):
        return COUNTDOWN
    return KATALOG.get(name)


def anteil(datei):
    """Wie laut dieser Ton JETZT sein soll -- 0.0 bis 1.0.

    Zwei Ebenen, genau wie auf der Tafel: der Hauptregler (`lautstaerke`) gilt
    für alles Hörbare gleichzeitig, der Einzelregler ist ein Anteil davon.

    Der Hauptregler darf bis 1.5 gehen, weil die STIMME sich digital verstärken
    lässt. Für einen fertigen Klang gilt das nicht -- die Samples liegen schon
    nahe am Anschlag, und mehr als 1.0 wäre nur Übersteuerung. Deshalb hier
    gedeckelt, statt den Regler für Ramzi zu beschneiden.
    """
    w = einstellungen.alle()

    # `toene` bleibt die harte Stummschaltung für ALLE Tonzeichen -- das ist der
    # Weg, über den "Tonzeichen aus" per Sprache läuft (stellschrauben.py).
    # Absichtlich NICHT als zweiter Schalter auf der Tafel: dort gibt es nur
    # noch Regler, sonst wäre der Widerspruch von oben wieder da.
    if not w.get('toene', True):
        return 0.0

    haupt = min(float(w.get('lautstaerke') or 0.0), 1.0)
    name = regler(datei)
    if name is None:
        # Ein Klang, den noch niemand eingetragen hat. Er soll trotzdem zu hören
        # sein -- ein unbekannter Ton, der still bleibt, wäre ein Fehler, den
        # man nicht findet. Der Hauptregler gilt, mehr weiß ich nicht.
        return max(0.0, min(1.0, haupt))

    einzel = float(w.get('laut_' + name, 100)) / 100.0
    return max(0.0, min(1.0, haupt * einzel))


# ---------------------------------------------------------- Lautstärke rechnen
def _skaliert(quelle, prozent):
    """Pfad zu einer Fassung von `quelle`, die `prozent` laut ist.

    Bei 100 % wird die Originaldatei zurückgegeben -- kein Kopieren, kein
    Rechnen, kein Zwischenspeicher. Der häufigste Fall ist damit gratis.
    """
    if prozent >= 100:
        return quelle
    ziel = os.path.join(
        LAGER, f'{os.path.splitext(os.path.basename(quelle))[0]}@{prozent}.wav')
    try:
        # Neu rechnen, wenn es die Fassung nicht gibt ODER das Original neuer
        # ist. Ohne den Zeitvergleich würde ein ausgetauschter Klang für immer
        # in seiner alten Fassung weiterspielen -- eine stille Veraltung, und
        # die ist schlimmer als ein sichtbarer Fehler.
        if (os.path.exists(ziel)
                and os.path.getmtime(ziel) >= os.path.getmtime(quelle)):
            return ziel

        with wave.open(quelle, 'rb') as q:
            kanaele, breite, rate = q.getnchannels(), q.getsampwidth(), q.getframerate()
            rohdaten = q.readframes(q.getnframes())

        faktor = prozent / 100.0
        if breite == 2:
            werte = array('h')
            werte.frombytes(rohdaten)
            for i, v in enumerate(werte):
                werte[i] = int(v * faktor)
            neu = werte.tobytes()
        elif breite == 1:
            # 8-Bit-WAV ist vorzeichenlos, die Ruhe liegt bei 128 -- ohne diese
            # Mitte zu rechnen käme kein leiserer Ton heraus, sondern ein
            # verzerrter.
            werte = array('B')
            werte.frombytes(rohdaten)
            for i, v in enumerate(werte):
                werte[i] = int(128 + (v - 128) * faktor)
            neu = werte.tobytes()
        else:
            # Ein Format, das ich hier nicht rechnen kann. Lieber laut als
            # stumm: der Ton kommt, nur ungedämpft.
            return quelle

        os.makedirs(LAGER, exist_ok=True)
        # Erst daneben schreiben, dann umbenennen. Sonst kann winsound eine
        # halbfertige Datei erwischen, wenn zwei Töne gleichzeitig kommen.
        vorlaeufig = ziel + f'.{os.getpid()}.tmp'
        with wave.open(vorlaeufig, 'wb') as z:
            z.setnchannels(kanaele)
            z.setsampwidth(breite)
            z.setframerate(rate)
            z.writeframes(neu)
        os.replace(vorlaeufig, ziel)
        return ziel
    except Exception:
        return quelle


# ------------------------------------------------------------------- Abspielen
def spiele(datei, warten=False):
    """Ton abspielen. Bei 0 % gar nichts tun.

    `warten` ist keine Geschmacksfrage, sondern der Unterschied zwischen hörbar
    und stumm. `SND_ASYNC` gibt sofort zurück, und Windows spielt weiter --
    solange der Prozess lebt. Ein kurzlebiger Aufruf (`python ton.py ...`, so
    kommt PowerShell an seine Töne) ist danach in wenigen Millisekunden fertig
    und beendet den Ton mit sich. Gemessen am 02.08.2026 um 00:17: die skalierte
    Datei entstand, aber zu hören war nichts.

    Also: im laufenden Prozess (Ohr, Aufnahme-Fenster) asynchron -- dort darf
    nichts auf einen Klang warten. Von außen aufgerufen synchron, denn dort ist
    das Warten die einzige Möglichkeit, den Ton überhaupt zu Ende zu bringen.
    """
    laut = anteil(datei)
    if laut <= 0.0:
        return False

    pfad = datei if os.path.isabs(datei) else os.path.join(ASSETS, datei)
    if not os.path.exists(pfad):
        return False

    prozent = max(STUFE, int(round(laut * 100 / STUFE)) * STUFE)
    try:
        import winsound
        marken = winsound.SND_FILENAME
        if not warten:
            marken |= winsound.SND_ASYNC
        winsound.PlaySound(_skaliert(pfad, prozent), marken)
        return True
    except Exception:
        return False


if __name__ == '__main__':
    # Von PowerShell aus so aufgerufen. Ohne Argument: zeigen, wie laut welcher
    # Ton gerade wäre -- damit sich ein "warum ist der still?" beantworten
    # lässt, ohne im Code zu suchen.
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            spiele(arg, warten=True)
    else:
        w = einstellungen.alle()
        print(f'Hauptregler {w.get("lautstaerke")}   toene={w.get("toene")}')
        for name in sorted(set(KATALOG.values()) | {COUNTDOWN}):
            beispiel = next((d for d, r in KATALOG.items() if r == name),
                            'cd_5.wav')
            print(f'  {name:16s} {w.get("laut_" + name, 100):3d} %'
                  f'  -> effektiv {anteil(beispiel) * 100:5.1f} %')
