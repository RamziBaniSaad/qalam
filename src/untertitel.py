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

# Wie lange der Streifen stehen bleibt, wenn nichts Neues kommt -- kommt aus
# den Einstellungen (Tafel-Regler "Untertitel", 0-30 s), Standard 10 s.
#
# Erster Versuch war 7 Sekunden fest verdrahtet, und Ramzi hat sofort gemerkt,
# wie schlecht das aussieht: "das kommt und geht, das sieht scheiße aus" -- der
# Streifen verschwand mitten im Reden. 0 Sekunden heißt: Untertitel ganz aus,
# der Regler ist damit gleichzeitig der Schalter.
def _haltezeit():
    try:
        from einstellungen import hole
        return float(hole('untertitel_sekunden'))
    except Exception:
        return 10.0

# Wie viele Sätze gleichzeitig zu sehen sind.
#
# Ramzis zweiter Einwand: "das sieht sehr voll aus, sehr viel Text auf einmal".
# Stimmt -- bei einer langen Äußerung kam der ganze Block. Jetzt stehen nur die
# letzten Sätze da, der Rest rutscht raus. Das ist auch das, was er als
# "Satz für Satz" beschrieben hat.
SAETZE_SICHTBAR = 2


def zeige(text, wer='noor', worte=None, start=None):
    """Einen Satz auf die Untertitel legen. Kostet nichts und blockiert nicht.

    `worte` ist der Zusatz für MEINE eigenen Untertitel (Ramzis Wunsch vom
    01.08.2026): eine Liste [{'w': Wort, 'ab': Sekunden, 'd': Dauer}, ...],
    dazu `start` als Zeitpunkt, ab dem gerechnet wird. Damit kann der Streifen
    das gerade gesprochene Wort hervorheben, ohne dass ihm jemand dauernd
    zuruft -- er bekommt EIN Paket und läuft dann allein.

    Beides ist optional. Wer nur Text schickt (der Sprech-Hook aus PowerShell,
    das Ohr für Ramzis eigene Sätze), bekommt genau die Anzeige von vorher.
    """
    d = {'text': text, 'wer': wer, 'zeit': time.time()}
    if worte:
        d['worte'] = worte
        d['start'] = start if start is not None else time.time()
    try:
        with open(DATEI, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
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
    import math

    from PyQt5.QtCore import Qt, QRectF, QTimer
    from PyQt5.QtGui import QFont, QPainter, QBrush, QColor, QPainterPath, QFontMetrics
    from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout

    HG        = QColor(18, 18, 20, 238)
    RAND      = QColor(255, 255, 255, 26)
    FARBE_ICH = QColor('#F2F2F2')     # was ich sage
    FARBE_ER  = QColor('#8FC7FF')     # was Ramzi sagt -- kühler, sofort unterscheidbar
    FARBE_WER = '#8A8F98'

    # Das gerade gesprochene Wort. Ramzis Wahl vom 01.08.2026 aus drei
    # Stärken: dezent. 15 Prozent größer und aufgehellt, weich hoch und weich
    # wieder runter -- genug zum Mitlesen, ohne dass die Zeile bei jedem Wort
    # hüpft. Bei 860 px Streifenbreite wird mehr sofort unruhig.
    WACHSTUM = 0.15
    RUHIG_ANTEIL = 0.78       # so hell sind die übrigen Wörter, damit das
                              # aktive überhaupt heraussticht
    # Platz am linken und rechten Rand, in den die Nachbarn ausweichen dürfen.
    # Ein Wort von 130 px wächst um 15 Prozent, das sind knapp 10 px je Seite;
    # 14 px reichen also auch für das längste Wort, das hier je steht.
    SEITENLUFT = 14

    BREITE = 860
    RAND_UNTEN = 90

    class Zeile(QWidget):
        """Der Textbereich -- zeichnet WÖRTER, nicht einen Block.

        Warum kein QLabel mehr: ein QLabel kann ein einzelnes Wort darin nicht
        hervorheben. Für den Effekt, den Ramzi wollte (das gesprochene Wort
        wird kurz größer), muss jedes Wort einzeln gezeichnet werden.

        Der Umbruch wird EINMAL beim Setzen berechnet und danach nur noch
        gezeichnet. Das ist der Grund, warum die Bewegung nichts kostet: pro
        Bild werden ~15 fertige Positionen gemalt, es wird nichts neu gemessen.

        Das aktive Wort wird um seinen eigenen Mittelpunkt vergrößert. Dadurch
        rückt kein Nachbar zur Seite und die Zeile bleibt ruhig -- genau die
        Eigenschaft, die die dezente Variante ausmacht.
        """

        def __init__(self, breite):
            super().__init__()
            self.setAttribute(Qt.WA_TranslucentBackground)
            self._breite = breite
            self._schrift = QFont('Segoe UI', 17)
            self._farbe = FARBE_ICH
            self._zeilen = []        # [[(wort, x, ab, dauer)], ...]
            self._hoehe = 0
            self._start = None       # None = keine Zeitangaben, nichts leuchtet

        def setze(self, text, farbe, worte=None, start=None):
            self._farbe = farbe
            self._start = start
            mass = QFontMetrics(self._schrift)
            zeilenhoehe = int(mass.height() * 1.32)

            # Wörter samt Zeiten. Ohne Zeiten (Ramzis eigene Sätze, der
            # Sprech-Hook) wird derselbe Weg gegangen, nur ohne Hervorhebung --
            # ein Codeweg statt zwei, damit nicht einer davon verrottet.
            if worte:
                stuecke = [(w.get('w', ''), w.get('ab', 0.0), w.get('d', 0.0))
                           for w in worte if w.get('w')]
            else:
                stuecke = [(w, 0.0, 0.0) for w in text.split() if w]

            # Links und rechts bleibt ein Streifen frei, in den die Nachbarn des
            # wachsenden Wortes ausweichen dürfen, ohne aus dem Kasten zu
            # laufen. Siehe paintEvent -- das ist die Hälfte der Lösung für
            # Ramzis Einwand, dass ein großes Wort an seinen Nachbarn klebt.
            aktuell, x = [], SEITENLUFT
            leer = mass.horizontalAdvance(' ')
            nutzbar = self._breite - 2 * SEITENLUFT
            self._zeilen = []
            for wort, ab, dauer in stuecke:
                b = mass.horizontalAdvance(wort)
                if aktuell and x - SEITENLUFT + b > nutzbar:
                    self._zeilen.append(aktuell)
                    aktuell, x = [], SEITENLUFT
                aktuell.append((wort, x, ab, dauer))
                x += b + leer
            if aktuell:
                self._zeilen.append(aktuell)

            self._hoehe = max(zeilenhoehe, len(self._zeilen) * zeilenhoehe)
            self._zeilenhoehe = zeilenhoehe
            self.setFixedHeight(self._hoehe)
            self.updateGeometry()
            self.update()

        def laeuft_noch(self):
            """Ist gerade Bewegung zu zeichnen? Bestimmt den Takt der Uhr."""
            if self._start is None:
                return False
            vergangen = time.time() - self._start
            ende = max((ab + d for zeile in self._zeilen for _, _, ab, d in zeile),
                       default=0.0)
            return vergangen <= ende + 0.2

        def paintEvent(self, _e):
            if not self._zeilen:
                return
            maler = QPainter(self)
            maler.setRenderHint(QPainter.Antialiasing)
            maler.setRenderHint(QPainter.TextAntialiasing)
            maler.setFont(self._schrift)
            mass = QFontMetrics(self._schrift)

            vergangen = None if self._start is None else time.time() - self._start
            ruhig = QColor(self._farbe)
            ruhig.setAlphaF(RUHIG_ANTEIL)

            for nr, zeile in enumerate(self._zeilen):
                grund = nr * self._zeilenhoehe + mass.ascent()

                # Erst suchen, WELCHES Wort dieser Zeile gerade wächst und wie
                # weit es dabei über seinen eigenen Platz hinausragt.
                #
                # Ramzis Einwand vom 01.08.2026: "das Wort wird so groß, dass
                # man keinen Abstand mehr sieht -- das klebt dann an den Wörtern
                # links und rechts." Er hat recht, und mein Entwurf war schuld:
                # ein Wortzwischenraum ist rund 5 px, ein langes Wort wächst
                # aber um knapp 10 px je Seite. Es MUSSTE kleben.
                #
                # Die Lösung, die weder klebt noch hüpft: die Nachbarn weichen
                # um genau den Überstand aus -- die links nach links, die rechts
                # nach rechts. Damit bleibt der sichtbare Abstand konstant,
                # egal wie groß das Wort gerade ist. Der Umbruch bleibt davon
                # unberührt (er ist bei Größe 1,0 berechnet), es kann also
                # nichts in die nächste Zeile rutschen -- genau das wäre das
                # Hüpfen, das er nicht wollte.
                aktiv_i, staerke_aktiv, ueberstand = -1, 0.0, 0.0
                for i, (wort, x, ab, dauer) in enumerate(zeile):
                    if vergangen is not None and dauer > 0 and ab <= vergangen < ab + dauer:
                        aktiv_i = i
                        staerke_aktiv = math.sin(math.pi * (vergangen - ab) / dauer)
                        ueberstand = (WACHSTUM * staerke_aktiv
                                      * mass.horizontalAdvance(wort) / 2.0)
                        break

                for i, (wort, x, ab, dauer) in enumerate(zeile):
                    if aktiv_i < 0 or i == aktiv_i:
                        versatz = 0.0
                    elif i < aktiv_i:
                        versatz = -ueberstand
                    else:
                        versatz = ueberstand
                    xx = x + versatz

                    if i != aktiv_i:
                        maler.setPen(ruhig)
                        maler.drawText(int(round(xx)), grund, wort)
                        continue

                    # sin(pi*p) ist genau die Kurve, die Ramzi beschrieben hat:
                    # weich hoch bis zur Mitte, weich wieder runter -- kein
                    # Sprung an den Enden.
                    maler.save()
                    mass_faktor = 1 + WACHSTUM * staerke_aktiv
                    mitte_x = xx + mass.horizontalAdvance(wort) / 2.0
                    mitte_y = grund - mass.ascent() / 3.0
                    maler.translate(mitte_x, mitte_y)
                    maler.scale(mass_faktor, mass_faktor)
                    maler.translate(-mitte_x, -mitte_y)
                    hell = QColor(self._farbe)
                    hell.setAlphaF(RUHIG_ANTEIL + (1.0 - RUHIG_ANTEIL) * staerke_aktiv)
                    maler.setPen(hell)
                    maler.drawText(int(round(xx)), grund, wort)
                    maler.restore()

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

            self.text = Zeile(BREITE - 26 - 26)

            aussen.addWidget(self.wer)
            aussen.addWidget(self.text)

            self.setFixedWidth(BREITE)
            self._stand = None

            # Zwei Takte, und das ist der ganze Leistungstrick: 150 ms im
            # Ruhezustand (nur nachsehen, ob sich die Datei geändert hat), 33 ms
            # nur solange wirklich Bewegung zu zeichnen ist. Der schnelle Takt
            # läuft also ausschließlich, während ich spreche.
            self.TAKT_RUHE = 150
            self.TAKT_BEWEGT = 33
            self.uhr = QTimer(self)
            self.uhr.timeout.connect(self.nachsehen)
            self.uhr.start(self.TAKT_RUHE)

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

            haltezeit = _haltezeit()

            if mtime != self._stand:
                self._stand = mtime
                if haltezeit <= 0:
                    # Regler auf 0 = Untertitel aus. Nicht mal anzeigen.
                    self.hide()
                    return
                try:
                    with open(DATEI, encoding='utf-8') as f:
                        d = json.load(f)
                except Exception:
                    return
                self.setze(d.get('text', ''), d.get('wer', 'noor'), d.get('zeit', 0),
                           d.get('worte'), d.get('start'))

            # Läuft gerade eine Wort-Bewegung? Dann schnell nachzeichnen, sonst
            # zurück in den Ruhetakt. Ohne das Zurückschalten liefe der Streifen
            # für immer mit 30 Bildern pro Sekunde weiter, obwohl sich nichts
            # mehr bewegt -- Last, die niemand sieht, ist die schlechteste Sorte.
            bewegt = self.isVisible() and self.text.laeuft_noch()
            gewuenscht = self.TAKT_BEWEGT if bewegt else self.TAKT_RUHE
            if self.uhr.interval() != gewuenscht:
                self.uhr.setInterval(gewuenscht)
            if bewegt:
                self.text.update()

            # Abgelaufen, oder gerade erst ausgeschaltet? Dann weg damit,
            # statt einen alten Satz stehen zu lassen.
            if self.isVisible() and (haltezeit <= 0 or time.time() - (self._zeit or 0) > haltezeit):
                self.hide()

        _zeit = 0

        def setze(self, text, wer, zeit, worte=None, start=None):
            text = ' '.join((text or '').split())
            self._zeit = zeit or time.time()
            if not text:
                self.hide()
                return
            ich = (wer or 'noor').lower() != 'ramzi'
            if not worte:
                # Nur die letzten Sätze zeigen. Bei einer langen Äußerung käme
                # sonst der ganze Block auf einmal und der Streifen wird zur
                # Textwand. Gilt nur für Text OHNE Zeitangaben: was ich selbst
                # spreche, ist beim Absenden schon passend eingeteilt -- da
                # nachträglich zu kürzen würde Wörter verschlucken, die gerade
                # zu hören sind.
                saetze = [s for s in re.split(r'(?<=[.!?…])\s+', text) if s.strip()]
                if len(saetze) > SAETZE_SICHTBAR:
                    text = ' '.join(saetze[-SAETZE_SICHTBAR:])
            self.wer.setText('NOOR' if ich else 'RAMZI')
            self.text.setze(text, FARBE_ICH if ich else FARBE_ER, worte, start)
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
