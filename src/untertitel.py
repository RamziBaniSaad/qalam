"""Untertitel — man sieht, was gesagt wurde.

Ein schmaler Streifen unten mittig auf dem Hauptbildschirm. Er zeigt zwei
Dinge:

    was Ramzi gesagt hat   -- damit er sieht, WIE es angekommen ist
    was Noor sagt          -- damit gesprochene Antworten mitlesbar sind

Das Erste ist das Wichtigere. Wenn ein Befehl danebengeht, ist die Frage immer
"habe ich es falsch gesagt oder hat sie es falsch gehört" -- und ohne Untertitel
ist das nicht zu beantworten.

WARUM EIN EIGENER PROZESS, DER EINE DATEI LIEST:
    Die Beschriftung kommt aus zwei ganz verschiedenen Richtungen -- aus dem
    Ohr (Python) und aus dem Stop-Hook (PowerShell). Eine Datei ist das
    einzige, was beide ohne Verrenkung erreichen. Dieselbe Bauart wie die
    Tafel, und die läuft seit gestern ohne Zutun.

Start:
    pythonw src/untertitel.py

Von außen beschriften:
    python -c "import untertitel; untertitel.zeige('Hallo', 'noor')"
    powershell: die JSON-Datei schreiben (siehe DATEI unten)
"""
import json
import os
import re
import sys
import tempfile
import time

DATEI = os.path.join(tempfile.gettempdir(), 'noor-untertitel.json')

# Wie lange der Streifen stehen bleibt, wenn nichts Neues kommt.
#
# Bewusst LANG. Vorher waren es 7 Sekunden, und Ramzi hat sofort gemerkt, wie
# schlecht das aussieht: "das kommt und geht, das sieht scheiße aus" -- der
# Streifen verschwand mitten im Reden und tauchte beim nächsten Stück wieder
# auf. Er soll stehen bleiben, bis der nächste Text ihn ersetzt. Die drei
# Minuten sind nur ein Sicherheitsnetz, damit nach Feierabend nicht stundenlang
# ein alter Satz auf dem Bildschirm klebt.
HALTEZEIT = 180.0

# Wie viele Sätze gleichzeitig zu sehen sind.
#
# Ramzis zweiter Einwand: "das sieht sehr voll aus, sehr viel Text auf einmal".
# Stimmt -- bei einer langen Äußerung kam der ganze Block. Jetzt stehen nur die
# letzten Sätze da, der Rest rutscht raus. Das ist auch das, was er als
# "Satz für Satz" beschrieben hat.
SAETZE_SICHTBAR = 2


def zeige(text, wer='noor'):
    """Einen Satz auf die Untertitel legen. Kostet nichts und blockiert nicht."""
    try:
        with open(DATEI, 'w', encoding='utf-8') as f:
            json.dump({'text': text, 'wer': wer, 'zeit': time.time()},
                      f, ensure_ascii=False)
    except OSError:
        pass


def loesche():
    try:
        os.remove(DATEI)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Ab hier die Anzeige. Wird nur gebraucht, wenn diese Datei gestartet wird --
# `zeige()` oben soll importierbar bleiben, ohne dass Qt geladen wird.
# --------------------------------------------------------------------------
def main():
    from PyQt5.QtCore import Qt, QRectF, QTimer
    from PyQt5.QtGui import QFont, QPainter, QBrush, QColor, QPainterPath, QFontMetrics
    from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout

    HG        = QColor(18, 18, 20, 238)
    RAND      = QColor(255, 255, 255, 26)
    FARBE_ICH = '#F2F2F2'     # was ich sage
    FARBE_ER  = '#8FC7FF'     # was Ramzi sagt -- kühler, sofort unterscheidbar
    FARBE_WER = '#8A8F98'

    BREITE = 860
    RAND_UNTEN = 90

    class Streifen(QWidget):
        def __init__(self):
            super().__init__()
            # Nie den Fokus nehmen: der Streifen darf niemals dem Eingabefeld
            # die Tastatur wegschnappen, sonst tippt Ramzi ins Leere.
            flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
            flags |= Qt.Tool if sys.platform != 'darwin' else Qt.SubWindow
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)

            aussen = QVBoxLayout(self)
            aussen.setContentsMargins(26, 16, 26, 18)
            aussen.setSpacing(4)

            self.wer = QLabel('')
            self.wer.setFont(QFont('Segoe UI Semibold', 9))
            self.wer.setStyleSheet(f'color: {FARBE_WER}; background: transparent; letter-spacing: 1px;')

            self.text = QLabel('')
            self.text.setFont(QFont('Segoe UI', 17))
            self.text.setWordWrap(True)
            self.text.setStyleSheet(f'color: {FARBE_ICH}; background: transparent;')
            self.text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            aussen.addWidget(self.wer)
            aussen.addWidget(self.text)

            self.setFixedWidth(BREITE)
            self._stand = None

            self.uhr = QTimer(self)
            self.uhr.timeout.connect(self.nachsehen)
            self.uhr.start(150)

        def paintEvent(self, _e):
            weg = QPainterPath()
            weg.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)
            maler = QPainter(self)
            maler.setRenderHint(QPainter.Antialiasing)
            maler.fillPath(weg, QBrush(HG))
            maler.strokePath(weg, RAND)

        def platziere(self):
            """Unten mittig auf dem Hauptbildschirm -- dort schaut man ohnehin
            hin, wenn man gerade spricht."""
            geo = QApplication.primaryScreen().geometry()
            self.adjustSize()
            self.setFixedWidth(BREITE)
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + geo.height() - self.height() - RAND_UNTEN
            self.move(x, y)

        def nachsehen(self):
            try:
                mtime = os.path.getmtime(DATEI)
            except OSError:
                if self.isVisible():
                    self.hide()
                return

            if mtime != self._stand:
                self._stand = mtime
                try:
                    with open(DATEI, encoding='utf-8') as f:
                        d = json.load(f)
                except Exception:
                    return
                self.setze(d.get('text', ''), d.get('wer', 'noor'), d.get('zeit', 0))

            # Abgelaufen? Dann weg damit, statt einen alten Satz stehen zu lassen.
            if self.isVisible() and time.time() - (self._zeit or 0) > HALTEZEIT:
                self.hide()

        _zeit = 0

        def setze(self, text, wer, zeit):
            text = ' '.join((text or '').split())
            self._zeit = zeit or time.time()
            if not text:
                self.hide()
                return
            # Nur die letzten Sätze zeigen. Bei einer langen Äußerung käme sonst
            # der ganze Block auf einmal, und der Streifen wird zur Textwand.
            saetze = [s for s in re.split(r'(?<=[.!?…])\s+', text) if s.strip()]
            if len(saetze) > SAETZE_SICHTBAR:
                text = ' '.join(saetze[-SAETZE_SICHTBAR:])
            ich = (wer or 'noor').lower() != 'ramzi'
            self.wer.setText('NOOR' if ich else 'RAMZI')
            self.text.setStyleSheet(
                f'color: {FARBE_ICH if ich else FARBE_ER}; background: transparent;')
            self.text.setText(text)
            self.platziere()
            if not self.isVisible():
                self.show()
            self.raise_()

    app = QApplication(sys.argv)
    # Ohne das beendet sich die App, sobald der Streifen einmal versteckt wird.
    app.setQuitOnLastWindowClosed(False)
    s = Streifen()
    s.platziere()
    # Unter pythonw gibt es keine Konsole: sys.stdout ist dort None, und ein
    # schlichtes print() beendet den Prozess mit einem Fehler, bevor das Fenster
    # je erscheint. Genau daran ist der erste Start gescheitert.
    if sys.stdout is not None:
        print('[Untertitel] bereit')
        sys.stdout.flush()
    sys.exit(app.exec_())


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--sage':
        zeige(' '.join(sys.argv[2:]) or 'Probe', 'noor')
    else:
        main()
