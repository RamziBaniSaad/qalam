import sys
import os
import time
import subprocess
from PyQt5.QtCore import Qt, QRectF, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtGui import QFont, QPainter, QBrush, QColor, QPainterPath
from PyQt5.QtWidgets import QApplication, QLabel, QHBoxLayout

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager

# winsound = Windows-native, zuverlaessige Sound-Wiedergabe (kein GC-Problem wie audioplayer).
try:
    import winsound
except ImportError:
    winsound = None

# --- Dunkles, modernes Theme ---
COL_BG = QColor(24, 24, 27, 236)   # fast schwarz, leicht transparent
COL_TEXT = "#E6E6E6"
COL_SUBTLE = "#9AA0A6"
COL_GREEN = "#3ECF5A"
COL_ORANGE = "#F5A623"
COL_RED = "#E5484D"

APP_NAME = "Qalam"  # Produktname

# Countdown-Sounds bei FESTEN Restsekunden (unabhaengig von auto_submit_seconds):
# einmal bei 10 s, dann jede der letzten 5 Sekunden (5..1), aufsteigende Tonhoehe.
def _weckwort_sperre(an):
    """Weckwort waehrend einer laufenden Aufnahme stumm schalten.

    Bewusst weich verdrahtet: laeuft kein Weckwort-Dienst, passiert nichts.
    Qalam soll auch ohne ihn ganz normal funktionieren."""
    try:
        from wake_word import aufnahme_beginnt, aufnahme_endet
        aufnahme_beginnt() if an else aufnahme_endet()
    except Exception:
        pass


START_SOUND = 'rec_start.wav'
COUNTDOWN_SOUNDS = {10: 'cd_10.wav', 5: 'cd_5.wav', 4: 'cd_4.wav',
                    3: 'cd_3.wav', 2: 'cd_2.wav', 1: 'cd_1.wav'}


# --- Die Timer-Werte frisch von der Platte --------------------------------
#
# Warum das hier stehen muss (01.08.2026): Ramzi kann diese Werte jetzt SPRECHEN
# -- "mach das automatische Abschicken auf 5 Minuten". Das erledigt
# src/stellschrauben.py im Prozess des Assistenten, und der ist ein ANDERER
# Prozess als Qalam. Ein set_config_value() dort käme hier also nie an;
# ConfigManager hält seine Werte pro Prozess im Speicher und liest die Datei nur
# beim Start. Ohne dieses Nachlesen müsste Ramzi Qalam neu starten, damit ein
# gesprochener Wert gilt -- und damit wäre der ganze Sinn weg.
#
# Bewusst NUR dieser Abschnitt und bewusst NICHT ConfigManager.reload_config():
# ein voller Neuaufbau würde auch alles andere ersetzen, unter anderem
# Änderungen, die im Einstellungsfenster noch nicht gespeichert sind. Was hier
# nachgelesen wird, ist genau das, was von außen gesprochen werden kann.
_CONFIG_DATEI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'config.yaml')
_timer_stand = {'zeit': None, 'werte': {}}


def _timer_von_platte():
    """recording_timer aus config.yaml, neu gelesen nur wenn die Datei sich
    geändert hat. Der Aufruf passiert einmal pro Sekunde während einer Aufnahme
    -- ein Blick auf den Zeitstempel kostet dabei nichts."""
    try:
        zeit = os.path.getmtime(_CONFIG_DATEI)
    except OSError:
        return _timer_stand['werte']
    if zeit != _timer_stand['zeit']:
        try:
            import yaml
            with open(_CONFIG_DATEI, encoding='utf-8') as f:
                daten = yaml.safe_load(f) or {}
            _timer_stand['werte'] = daten.get('recording_timer') or {}
            _timer_stand['zeit'] = zeit
        except Exception:
            pass        # kaputte oder halb geschriebene Datei: beim alten Wert bleiben
    return _timer_stand['werte']


class StatusWindow(BaseWindow):
    statusSignal = pyqtSignal(str, bool)
    closeSignal = pyqtSignal()
    autoSubmitSignal = pyqtSignal()  # Auto-Submit-Zeit erreicht

    def __init__(self):
        """Initialize the status window."""
        super().__init__(APP_NAME, 300, 72)
        # Titelleiste der BaseWindow ausblenden -> minimalistischer Balken
        if hasattr(self, 'title_bar'):
            self.title_bar.hide()
        self.initStatusUI()
        self.statusSignal.connect(self.updateStatus)

        # Aufnahme-Timer: Dauer + Farbe + Countdown/Auto-Submit
        self.elapsed_timer = QTimer()
        self.elapsed_timer.timeout.connect(self.updateElapsed)
        self.record_start = None
        self._last_sound_remaining = None

    def initStatusUI(self):
        """Minimalistische Zeile: Farbkreis + Status + Timer."""
        # macOS: Qt.Tool-Fenster sind nur sichtbar, wenn die EIGENE App gerade die
        # aktive App ist. Beim Diktieren ist aber eine andere App vorn -> das Overlay
        # erschiene nie. Daher auf macOS ohne Qt.Tool, dafür fokus-neutral (klaut dem
        # Textfeld nicht den Fokus). Windows/Linux behalten Qt.Tool (aus der Taskleiste).
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if sys.platform == 'darwin':
            flags |= Qt.WindowDoesNotAcceptFocus
        else:
            flags |= Qt.Tool
        self.setWindowFlags(flags)

        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(10)

        # Farbkreis (gruen -> orange -> rot)
        self.timer_dot = QLabel()
        self.timer_dot.setFixedSize(12, 12)
        self._set_dot(COL_GREEN)

        # Status-Text (deutsch)
        self.status_label = QLabel('Aufnahme')
        self.status_label.setFont(QFont('Segoe UI Semibold', 12))
        self.status_label.setStyleSheet(f"color: {COL_TEXT}; background: transparent;")

        # Timer mm:ss
        self.timer_label = QLabel('00:00')
        self.timer_label.setFont(QFont('Consolas', 13))
        self.timer_label.setStyleSheet(f"color: {COL_TEXT}; background: transparent;")

        row.addStretch(1)
        row.addWidget(self.timer_dot)
        row.addWidget(self.status_label)
        row.addSpacing(6)
        row.addWidget(self.timer_label)
        row.addStretch(1)

        self.main_layout.addLayout(row)

    def paintEvent(self, event):
        """Dunkler, abgerundeter Hintergrund (ueberschreibt das weisse Base-Design)."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 16, 16)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(COL_BG))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

    def show(self):
        """Fenster unten-mittig positionieren und anzeigen.

        Ausser Ramzi hat `bild_fenster_aus` gesetzt -- dann bleibt es weg. Er
        hält die Einstellung selbst für eine, die niemand braucht; sie ist da,
        weil sie fast nichts kostet und in einem verkauften Produkt fehlen
        würde. Der Countdown-Ton kommt weiterhin, nur eben ohne Bild -- wer das
        Fenster nicht sehen will, will nicht zwangsläufig auch nichts hören.
        """
        try:
            import einstellungen
            if einstellungen.hole('bild_fenster_aus'):
                return
        except Exception:
            pass

        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        x = (geo.width() - self.width()) // 2
        y = geo.height() - self.height() - 120
        self.move(x, y)
        # Ohne Fokus-Klau anzeigen (der Text soll im aktiven Fenster landen)
        # und über andere Fenster heben.
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        super().show()
        self.raise_()

    def closeEvent(self, event):
        """Close-Signal senden."""
        self.elapsed_timer.stop()
        self.closeSignal.emit()
        super().closeEvent(event)

    # ---- Aufnahme-Timer + Countdown --------------------------------------
    def _timer_cfg(self, key, default):
        """Config-Wert aus dem Abschnitt recording_timer holen, mit Fallback.

        Erst die Datei, dann der Speicher: die Datei ist die einzige Stelle, die
        ein anderer Prozess erreichen kann (siehe _timer_von_platte). Steht der
        Wert dort nicht, gilt weiter, was ConfigManager beim Start geladen hat."""
        val = _timer_von_platte().get(key)
        if val is None:
            val = ConfigManager.get_config_value('recording_timer', key)
        return default if val is None else val

    def _set_dot(self, color):
        self.timer_dot.setStyleSheet(f"background-color: {color}; border-radius: 6px;")

    def startElapsedTimer(self):
        """Beim Aufnahmestart: Timer starten (falls aktiviert)."""
        if self._timer_cfg('enabled', True):
            self.record_start = time.time()
            self._last_sound_remaining = None
            self.timer_label.setText('00:00')
            self._set_dot(COL_GREEN)
            self.timer_dot.show()
            self.timer_label.show()
            self.elapsed_timer.start(1000)
        else:
            self.timer_dot.hide()
            self.timer_label.hide()

    def stopElapsedTimer(self):
        """Timer stoppen."""
        self.elapsed_timer.stop()
        self.record_start = None

    def updateElapsed(self):
        """Sekuendlich: mm:ss + Farbkreis + Countdown-Sounds + Auto-Submit."""
        if self.record_start is None:
            return
        elapsed = int(time.time() - self.record_start)
        mm, ss = divmod(elapsed, 60)
        self.timer_label.setText(f"{mm:02d}:{ss:02d}")

        orange = self._timer_cfg('orange_seconds', 60)
        red = self._timer_cfg('red_seconds', 180)
        if elapsed >= red:
            self._set_dot(COL_RED)
        elif elapsed >= orange:
            self._set_dot(COL_ORANGE)
        else:
            self._set_dot(COL_GREEN)

        # Auto-Submit + Countdown-Sounds
        if self._timer_cfg('auto_submit_enabled', True):
            submit_at = self._timer_cfg('auto_submit_seconds', 300)
            remaining = submit_at - elapsed
            if self._timer_cfg('countdown_sounds_enabled', True):
                self._play_countdown_sound(remaining)
            if elapsed >= submit_at:
                self.elapsed_timer.stop()
                self.autoSubmitSignal.emit()

    def _play_sound(self, filename):
        """Eine WAV-Datei aus assets/ abspielen. Plattformübergreifend:
        Windows -> winsound, macOS -> afplay, Linux -> aplay/paplay."""
        # Auf Windows über ton.py: dort hängt jeder Klang an seinem eigenen
        # Regler (Ramzis Feedback-Auftrag vom 01.08.2026), und dort wird die
        # Lautstärke in die Datei gerechnet, weil winsound keine kennt. Bei
        # 0 % kommt gar nichts -- deshalb `return` und nicht weiterfallen.
        #
        # Nur Windows: ton.py steht auf winsound auf. Mac und Linux behalten den
        # Weg von vorher, dort gibt es die Sprachschicht ohnehin nicht (siehe
        # project_noor_windows_macos im Gedächtnis).
        if winsound:
            try:
                import ton as tonmodul
                tonmodul.spiele(filename)
                return
            except Exception as e:
                print(f"[Qalam] ton.py nicht erreichbar ({filename}): {e}")

        path = os.path.join('assets', filename)
        if not os.path.exists(path):
            return
        try:
            if winsound:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif sys.platform == 'darwin':
                subprocess.Popen(['afplay', path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Linux: erst paplay (PulseAudio), sonst aplay (ALSA).
                for player in (['paplay', path], ['aplay', path]):
                    try:
                        subprocess.Popen(player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except FileNotFoundError:
                        continue
        except Exception as e:
            print(f"[Qalam] Sound-Fehler ({filename}): {e}")

    def _play_start_sound(self):
        """Kurzes Signal, wenn die Aufnahme losläuft.

        Damit Ramzi weiterreden kann, ohne auf den Bildschirm zu sehen -- gerade
        beim Auto-Submit, wo die nächste Aufnahme von selbst anspringt."""
        if not self._timer_cfg('start_sound_enabled', True):
            return
        self._play_sound(START_SOUND)

    def _play_countdown_sound(self, remaining):
        """Feste Countdown-Beeps: 10 s, dann 5/4/3/2/1 s."""
        if remaining in COUNTDOWN_SOUNDS and remaining != self._last_sound_remaining:
            self._last_sound_remaining = remaining
            self._play_sound(COUNTDOWN_SOUNDS[remaining])
    # ----------------------------------------------------------------------

    @pyqtSlot(str, bool)
    def updateStatus(self, status, use_llm=False):
        """Status-Fenster aktualisieren."""
        if status == 'recording':
            # Weckwort taub stellen: Ramzi sagt "Noor" andauernd, waehrend er
            # diktiert -- ohne das wuerde ich mitten im Diktat aufwachen.
            _weckwort_sperre(True)
            self._play_start_sound()
            self.startElapsedTimer()
            self.status_label.setText('Aufnahme')
            self.status_label.setStyleSheet(f"color: {COL_TEXT}; background: transparent;")
            self.show()

        elif status == 'transcribing':
            _weckwort_sperre(False)
            self.stopElapsedTimer()
            self._set_dot(COL_SUBTLE)
            self.status_label.setText('Schreibe …')
            self.status_label.setStyleSheet(f"color: {COL_SUBTLE}; background: transparent;")

        elif status == 'processing_llm_cleanup':
            self._set_dot(COL_SUBTLE)
            self.status_label.setText('Räume auf …')
            self.status_label.setStyleSheet(f"color: {COL_SUBTLE}; background: transparent;")

        elif status == 'processing_llm_instruction':
            self._set_dot(COL_SUBTLE)
            self.status_label.setText('Verarbeite …')
            self.status_label.setStyleSheet(f"color: {COL_SUBTLE}; background: transparent;")

        if status in ('idle', 'error', 'cancel'):
            self.stopElapsedTimer()
            self.close()


if __name__ == '__main__':
    app = QApplication(sys.argv)

    status_window = StatusWindow()
    status_window.statusSignal.emit('recording', False)

    # Simulate status updates
    QTimer.singleShot(3000, lambda: status_window.statusSignal.emit('transcribing', False))
    QTimer.singleShot(6000, lambda: status_window.statusSignal.emit('idle', False))

    sys.exit(app.exec_())
