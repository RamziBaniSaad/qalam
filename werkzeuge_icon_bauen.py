"""Das Qalam-Icon bauen -- Ersatz für das geerbte WhisperWriter-"W".

Ramzis Auftrag (31.07.2026): "Ich möchte überall, wo Whisperwriter drauf
steht, das einfach weg haben ... zumindest das Aussehen von der App und das
Icon muss dazugehören."

Der Reed-Stift (das "Qalam") aus dem unbenutzt herumliegenden `pencil.png`
(schwarz auf transparent, aus WhisperWriter übrig, nirgends im Code
verwendet) -- neu eingefärbt und auf einen dunklen, abgerundeten Untergrund
gesetzt, in der Palette, die im Rest des Produkts schon steht:

    Hintergrund  #121214  -- dieselbe Farbe wie der Untertitel-Streifen
    Stift        #F2F2F2  -- FARBE_ICH aus untertitel.py, "was ich sage"
    Federstrich  #8FC7FF  -- FARBE_ER aus untertitel.py, Ramzis Farbe

Ein Icon, das dieselben zwei Farben trägt wie der Streifen, den Ramzi jeden
Tag ansieht -- nicht zufällig gewählt, sondern damit die ganze Sprachschicht
wie ein Produkt aussieht, nicht wie drei zusammengesteckte Werkzeuge.

Aufruf:
    python werkzeuge_icon_bauen.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HIER = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HIER, 'assets')

HINTERGRUND = (18, 18, 20, 255)      # #121214, wie untertitel.py::HG (ohne Transparenz)
STIFT = (242, 242, 242, 255)         # #F2F2F2, FARBE_ICH
FEDERSTRICH = (143, 199, 255, 255)   # #8FC7FF, FARBE_ER


def _abgerundetes_quadrat(groesse, radius_anteil=0.22):
    """Der Untergrund: dunkles, abgerundetes Quadrat -- dieselbe Form, die
    Windows für App-Icons erwartet (kein hartes Rechteck, keine Kreisscheibe)."""
    bild = Image.new('RGBA', (groesse, groesse), (0, 0, 0, 0))
    maske = Image.new('L', (groesse, groesse), 0)
    zeichner = ImageDraw.Draw(maske)
    radius = int(groesse * radius_anteil)
    zeichner.rounded_rectangle([0, 0, groesse - 1, groesse - 1], radius=radius, fill=255)
    hg = Image.new('RGBA', (groesse, groesse), HINTERGRUND)
    bild.paste(hg, (0, 0), maske)
    return bild


def _eingefaerbter_stift(groesse):
    """pencil.png -- dieselbe Silhouette, aber in der Produktfarbe statt
    Schwarz, und beschnitten auf den tatsächlichen Bildinhalt (das Original
    hat ringsum viel leeren Rand)."""
    quelle = Image.open(os.path.join(ASSETS, 'pencil.png')).convert('RGBA')
    alpha = quelle.split()[3]
    kasten = alpha.getbbox()
    if kasten:
        quelle = quelle.crop(kasten)
        alpha = alpha.crop(kasten)

    eingefaerbt = Image.new('RGBA', quelle.size, (0, 0, 0, 0))
    voll = Image.new('RGBA', quelle.size, STIFT)
    eingefaerbt.paste(voll, (0, 0), alpha)

    # Auf einen guten Anteil der Zielgröße skalieren, mit Rand -- ein Icon,
    # das bis zum Rand reicht, wirkt auf einem Taskleisten-Symbol abgeschnitten.
    ziel = int(groesse * 0.62)
    verhaeltnis = ziel / max(eingefaerbt.size)
    neu = (max(1, int(eingefaerbt.width * verhaeltnis)), max(1, int(eingefaerbt.height * verhaeltnis)))
    return eingefaerbt.resize(neu, Image.LANCZOS)


def _federstrich(groesse):
    """Ein kurzer, geschwungener Strich -- die Tinte, die der Stift
    hinterlässt. In Ramzis Farbe: derselbe Blauton wie sein eigener Text im
    Untertitel-Streifen."""
    strich = Image.new('RGBA', (groesse, groesse), (0, 0, 0, 0))
    zeichner = ImageDraw.Draw(strich)
    breite = max(2, groesse // 26)
    punkte = [
        (groesse * 0.20, groesse * 0.82),
        (groesse * 0.34, groesse * 0.74),
        (groesse * 0.44, groesse * 0.80),
        (groesse * 0.58, groesse * 0.68),
    ]
    zeichner.line(punkte, fill=FEDERSTRICH, width=breite, joint='curve')
    for p in (punkte[0], punkte[-1]):
        zeichner.ellipse([p[0] - breite / 2, p[1] - breite / 2,
                          p[0] + breite / 2, p[1] + breite / 2], fill=FEDERSTRICH)
    return strich


def baue(groesse=800):
    bild = _abgerundetes_quadrat(groesse)

    strich = _federstrich(groesse)
    bild.alpha_composite(strich)

    stift = _eingefaerbter_stift(groesse)
    # Mittig, mit leichtem Versatz nach oben-links -- der Federstrich liegt
    # unten rechts, die Spitze des Stifts soll optisch dorthin zeigen.
    x = (groesse - stift.width) // 2 - int(groesse * 0.03)
    y = (groesse - stift.height) // 2 - int(groesse * 0.05)
    bild.alpha_composite(stift, (x, y))

    # Auf das Vierfache bauen und dann herunterskalieren -- weiches
    # Antialiasing statt harter Pixelkanten am rundenen Rechteck.
    return bild


def main():
    gross = baue(1600).resize((800, 800), Image.LANCZOS)
    ziel_png = os.path.join(ASSETS, 'qalam-logo.png')
    gross.save(ziel_png)
    print(f'geschrieben: {ziel_png}')

    # .ico mit mehreren Größen -- Windows sucht sich die passende selbst
    # (Taskleiste, Alt+Tab, Datei-Explorer brauchen unterschiedliche).
    groessen = [256, 128, 64, 48, 32, 16]
    stufen = [gross.resize((g, g), Image.LANCZOS) for g in groessen]
    ziel_ico = os.path.join(ASSETS, 'qalam-logo.ico')
    stufen[0].save(ziel_ico, format='ICO',
                   sizes=[(g, g) for g in groessen])
    print(f'geschrieben: {ziel_ico}  ({groessen})')


if __name__ == '__main__':
    main()
