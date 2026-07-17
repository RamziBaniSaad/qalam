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
COUNTDOWN_SOUNDS = {10: 'cd_10.wav', 5: 'cd_5.wav', 4: 'cd_4.wav',
                    3: 'cd_3.wav', 2: 'cd_2.wav', 1: 'cd_1.wav'}


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
        """Fenster unten-mittig positionieren und anzeigen."""
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
        """Config-Wert aus dem Abschnitt recording_timer holen, mit Fallback."""
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

    def _play_countdown_sound(self, remaining):
        """Feste Countdown-Beeps: 10 s, dann 5/4/3/2/1 s. Plattformübergreifend:
        Windows -> winsound, macOS -> afplay, Linux -> aplay/paplay."""
        if remaining in COUNTDOWN_SOUNDS and remaining != self._last_sound_remaining:
            self._last_sound_remaining = remaining
            path = os.path.join('assets', COUNTDOWN_SOUNDS[remaining])
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
                print(f"[Qalam] Countdown-Sound-Fehler: {e}")
    # ----------------------------------------------------------------------

    @pyqtSlot(str, bool)
    def updateStatus(self, status, use_llm=False):
        """Status-Fenster aktualisieren."""
        if status == 'recording':
            self.startElapsedTimer()
            self.status_label.setText('Aufnahme')
            self.status_label.setStyleSheet(f"color: {COL_TEXT}; background: transparent;")
            self.show()

        elif status == 'transcribing':
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
