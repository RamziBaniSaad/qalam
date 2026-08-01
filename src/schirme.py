"""Welcher Bildschirm ist wessen -- für Qt-Fenster.

Ramzis Auftrag vom 02.08.2026 nachts: Untertitel und das Aufnahme-Fenster
sollen auf JEDEM der drei Bildschirme laufen können, nicht fest auf seinem. Der
Anlass ist handfest: im Vollbildspiel sieht er seinen eigenen Schirm nicht --
und dann ist "wichtige Anzeigen kommen auf Ramzis Schirm" genau falsch herum.

Die Rollen sind dieselben wie in `noor-schirme.ps1`, nur hier für Qt
nachgebildet statt über die Windows-API:

  RAMZI  der primäre, sein großer -- da arbeitet er
  IPAD   der virtuelle (spacedesk), hochkant
  ICH    der übrige -- da liegt die Tafel

**Wie der iPad erkannt wird:** an seinem Hochformat. Qt sagt nicht, ob ein
Bildschirm virtuell ist; der spacedesk-Schirm ist aber der einzige, der höher
als breit ist (1640x2360). Das ist eine Annahme über Ramzis Aufbau und keine
allgemeine Wahrheit -- deshalb steht sie hier an einer Stelle und nicht
verstreut, und sie fällt weich aus: wird nichts Passendes gefunden, kommt der
primäre Schirm zurück. Ein Fenster am falschen Platz ist ärgerlich, ein Fenster
das gar nicht erscheint ist ein Ausfall.
"""


def alle(QApplication):
    return list(QApplication.screens() or [])


def schirm(QApplication, rolle):
    """QScreen für 'ramzi', 'ich' oder 'ipad'. Fällt auf den primären zurück."""
    primaer = QApplication.primaryScreen()
    schirme = alle(QApplication)
    rolle = (rolle or 'ich').strip().lower()

    if rolle == 'ramzi' or not schirme:
        return primaer

    andere = [s for s in schirme if s is not primaer]
    hochkant = [s for s in andere
                if s.geometry().height() > s.geometry().width()]

    if rolle == 'ipad':
        return hochkant[0] if hochkant else primaer

    # 'ich' -- der übrige, also nicht der primäre und nicht der hochkante.
    quer = [s for s in andere if s not in hochkant]
    return quer[0] if quer else (andere[0] if andere else primaer)


def gewaehlter(QApplication):
    """Der Schirm, den Ramzi für Untertitel und Aufnahme eingestellt hat."""
    try:
        import einstellungen
        rolle = einstellungen.hole('anzeige_schirm')
    except Exception:
        rolle = 'ich'
    return schirm(QApplication, rolle)
