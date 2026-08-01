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

# Wie lange eine Anzeige noch stehen bleibt, NACHDEM sie zu Ende gesprochen ist.
#
# Ramzis Regel dazu, und sie ist richtig herum gedacht: "lieber ein bisschen
# länger als kürzer." Das letzte Wort ist gerade verklungen, wenn das Auge es
# erreicht -- ohne Nachlauf verschwände die Zeile im selben Moment.
#
# Nur die LETZTE Anzeige lebt davon wirklich: alle anderen werden ohnehin von
# der nächsten abgelöst, bevor der Nachlauf zählt.
NACHLAUF = 1.2


# --- Einteilen: wie viel steht gleichzeitig da? ----------------------------
#
# Hier und nicht in voice_output.py, weil es seit dem 01.08.2026 BEIDE Seiten
# betrifft: was ich sage und was Ramzi sagt. Zwei Fassungen derselben Regel
# würden auseinanderlaufen, und dann sähen seine Untertitel wieder anders aus
# als meine -- genau das wollte er ja loswerden.
SAETZE_PRO_ANZEIGE = 2
ZEICHEN_PRO_ANZEIGE = 120


def einteilen(text, erste_kuerzer=False):
    """Text in Anzeigen bündeln -- Ramzis Hybrid aus Sätzen UND Länge.

    Sein Wunsch, wortgetreu: "normalerweise zwei Sätze, aber gleichzeitig
    abhängig von der Zeit -- wenn du einen langen Satz hast, der so viel Zeit
    braucht wie zwei, dann nimmst du nur den."

    `erste_kuerzer` gilt nur beim Sprechen: dort muss die erste Anzeige fertig
    erzeugt sein, bevor der erste Ton läuft, und eine kurze erste Anzeige macht
    den Anfang schneller. Für gehörten Text ergibt das keinen Sinn -- da ist
    der Ton längst vorbei.
    """
    saetze = [s for s in re.split(r'(?<=[.!?…])\s+', ' '.join((text or '').split()))
              if s.strip()]
    if not saetze:
        return []
    anzeigen, aktuell, zeichen = [], [], 0
    for satz in saetze:
        erste = erste_kuerzer and not anzeigen
        grenze_saetze = 1 if erste else SAETZE_PRO_ANZEIGE
        grenze_zeichen = 60 if erste else ZEICHEN_PRO_ANZEIGE
        if aktuell and (len(aktuell) >= grenze_saetze
                        or zeichen + len(satz) > grenze_zeichen):
            anzeigen.append(' '.join(aktuell))
            aktuell, zeichen = [], 0
        aktuell.append(satz)
        zeichen += len(satz)
    if aktuell:
        anzeigen.append(' '.join(aktuell))

    # Whisper setzt bei durchgehendem Reden oft GAR KEINE Satzzeichen. Dann ist
    # alles ein einziger Satz, die Zwei-Satz-Grenze greift nie, und Ramzi
    # bekommt den ganzen Block als Wand -- genau das, was er als "mal fünf Sätze
    # auf einmal" beschrieben hat. Also notfalls hart nach Wörtern trennen.
    #
    # Getrennt wird NUR, wo Whisper gar keine Satzzeichen geliefert hat. Wo
    # echte Sätze stehen, bleibt der Satz heil -- ihn nach Zeichen zu zerhacken
    # wäre schlimmer als eine Zeile zu viel.
    fein = []
    for a in anzeigen:
        if re.search(r'[.!?…]', a):
            fein.append(a)
            continue
        while len(a) > ZEICHEN_PRO_ANZEIGE:
            worte, teil = a.split(), []
            while worte and len(' '.join(teil + worte[:1])) <= ZEICHEN_PRO_ANZEIGE:
                teil.append(worte.pop(0))
            if not teil:
                break
            fein.append(' '.join(teil))
            a = ' '.join(worte)
        if a:
            fein.append(a)
    return fein


def sweep_zeiten(worte, ab_index):
    """Zeiten für Ramzis Seite: die NEU dazugekommenen Wörter leuchten auf.

    Warum nicht dasselbe wie bei mir: bei mir hebe ich das Wort hervor, das
    GERADE klingt -- das kann ich, weil ich den Ton selbst erzeuge. Bei ihm
    entsteht der Text erst, NACHDEM er gesprochen hat. Ein "aktuelles Wort"
    gibt es dort nicht mehr; es wäre immer ein bis drei Sekunden zu spät und
    damit gelogen.

    Was ehrlich ist und dasselbe Gefühl gibt: die Wörter, die gerade erst
    verstanden wurden, laufen einmal von links nach rechts durch. Er sieht
    dadurch genau, was neu angekommen ist -- und dass ich noch zuhöre.

    Die Wörter lösen sich ab, statt gleichzeitig zu leuchten: der Streifen
    zeichnet je Zeile ein aktives Wort, und daran hängt auch das Ausweichen der
    Nachbarn. Ein Durchlauf bleibt unter einer Sekunde.
    """
    n = max(0, len(worte) - ab_index)
    je = min(0.13, 0.9 / n) if n else 0.0
    zeiten, k = [], 0
    for i, w in enumerate(worte):
        if i < ab_index:
            zeiten.append({'w': w, 'ab': 0.0, 'd': 0.0})
        else:
            zeiten.append({'w': w, 'ab': round(k * je, 3), 'd': round(je * 0.95, 3)})
            k += 1
    return zeiten


def lesezeit(text):
    """Wie lange ein fertiger Satz stehen bleiben soll, damit man ihn liest.

    Ramzis Regel dazu bleibt gültig: lieber zu lang als zu kurz.
    """
    n = len([w for w in (text or '').split() if w])
    return max(2.5, 0.32 * n)


def zeige(text, wer='noor', worte=None, start=None, dauer=None, offen=False):
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
    if offen:
        # "Er redet noch" -- der Streifen darf nicht ausblenden, egal was für
        # eine Haltezeit eingestellt ist. Genau daran lag Ramzis "manchmal ist
        # der Untertitel einfach weg": er dachte mitten im Satz nach, es kam
        # eine Runde lang nichts Neues, und die Haltezeit lief ab.
        d['offen'] = True
    if dauer:
        # Wie lange DIESE Anzeige zu hören ist. Ramzis Einwand vom 01.08.2026:
        # "deine Länge, die du einschätzt, ist immer zu kurz." Sie war gar keine
        # Einschätzung von mir -- sie war SEIN Regler, und der galt bisher auch
        # für meine Untertitel. Steht die Dauer hier, rechnet der Streifen sie
        # selbst aus und der Regler bleibt für seine eigenen Sätze zuständig.
        d['dauer'] = dauer
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
            """Unten mittig auf dem eingestellten Bildschirm.

            Welcher das ist, entscheidet `anzeige_schirm` (Vorgabe: mein
            Schirm). Ramzis Grund vom 02.08.2026: sitzt er im Vollbildspiel,
            ist sein eigener Schirm der EINZIGE, den er nicht sehen kann --
            eine Anzeige, die dort erscheint, ist dann wertlos.

            Bei jedem Platzieren neu gefragt, nicht einmal beim Start: sonst
            müsste er den Untertitel neu starten, um den Schirm zu wechseln.
            """
            try:
                import schirme
                geo = schirme.gewaehlter(QApplication).geometry()
            except Exception:
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
                           d.get('worte'), d.get('start'), d.get('dauer'),
                           d.get('offen', False))

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
            #
            # ZWEI Uhren, und der Unterschied ist Ramzis Einwand vom 01.08.2026.
            # Bisher galt sein Regler für alles -- auch für meine Untertitel.
            # Stand er auf vier Sekunden und eine Anzeige war sechs Sekunden zu
            # hören, verschwand sie mitten im Satz. Für ihn sah das aus, als
            # schätzte ich die Länge zu kurz; in Wahrheit habe ich sie gar nicht
            # geschätzt.
            #
            # Jetzt gilt: bringt eine Anzeige ihre eigene Dauer mit, steht sie
            # genau so lange, wie sie zu hören ist, plus Nachlauf. Damit endet
            # sie von selbst erst, wenn die nächste anfängt -- genau das, was er
            # vorgeschlagen hat. Sein Regler bleibt für Text ohne Dauer
            # zuständig, also für seine eigenen Sätze.
            if self.isVisible():
                if haltezeit <= 0:
                    self.hide()                       # Regler auf 0 = ganz aus
                elif self._offen:
                    # Er redet noch. Nur ein Notausgang, falls das Ohr stirbt,
                    # während es "offen" stehen gelassen hat -- sonst bliebe der
                    # Streifen für immer stehen, und eine Anzeige, die nicht
                    # mehr weggeht, ist genauso kaputt wie eine, die zu früh geht.
                    if time.time() - (self._zeit or 0) > 90:
                        self.hide()
                elif self._dauer:
                    if time.time() > (self._start or self._zeit) + self._dauer + NACHLAUF:
                        self.hide()
                elif time.time() - (self._zeit or 0) > haltezeit:
                    self.hide()

        _zeit = 0
        _dauer = None
        _start = None
        _offen = False

        def setze(self, text, wer, zeit, worte=None, start=None, dauer=None,
                  offen=False):
            text = ' '.join((text or '').split())
            self._zeit = zeit or time.time()
            self._dauer = dauer
            self._start = start
            self._offen = offen
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
