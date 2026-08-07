"""Die Sprech-Zentrale -- genau ein Faden spricht, alle anderen werfen nur ein.

WARUM ES SIE GIBT (Ramzi, 07.08.2026: "wir hatten jeden Tag Probleme mit
diesem, ich moechte das ein fuer alle Mal wegbekommen"):

Vorher war jeder Sprecher sein eigener Herr -- der Stop-Hook, die Reflexe des
Ohrs, der Briefkasten, "nochmal". Jeder hat fuer sich gewartet und fuer sich
losgeredet, und keiner wusste, was die anderen vorhatten. Daraus folgte alles,
worueber er sich beschwert hat: ein Stopp konnte nicht wirken, weil es keine
Stelle gab, die ALLES abbricht; und ein laengst ueberholter Satz wurde trotzdem
gesprochen, weil ein Satz kein Verfallsdatum hatte.

Also: eine Liste von Auftraegen, in die alle einwerfen, und ein einziger Faden,
der daraus liest.

    {text, rang, eingeworfen, verfaellt, quelle, id}

RANG -- klein ist wichtiger:

    0  Sofort      Stopp-Bestaetigung, Fehler. Verfaellt nie.
    1  Antwort     auf einen Zuruf von ihm (Reflex, Frage).
    2  Zwischen    Zwischenmeldung waehrend der Arbeit (noor-sprich.ps1).
    3  Vorlesen    die Chat-Antwort am Ende eines Zuges.

VERFALLSDATUM -- der eigentliche Gewinn. Ein Zwischenstand von vor zwei Minuten
vorgelesen zu bekommen ist schlimmer als gar keiner: der Inhalt stimmt nicht
mehr, und er kommt genau dann, wenn Ramzi schon weitergemacht hat. Was
verfaellt, ist aber nicht weg -- es wird als UNGESAGT protokolliert und ist
ueber "nochmal" abrufbar (siehe warteschlange.merke).

Warum ein Modul mit Zustand statt einer Klasse: es gibt genau EINE Zentrale je
Prozess, und sie wird von ueberall gerufen (Ohr, Briefkasten, Tastenwache,
Befehlswache). Eine Instanz durchzureichen waere vier Konstruktoren Aufwand fuer
nichts.
"""
import itertools
import threading
import time

RANG_SOFORT = 0
RANG_ANTWORT = 1
RANG_ZWISCHEN = 2
RANG_VORLESEN = 3

# Wie lange ein Auftrag hoechstens gueltig bleibt, je Rang. Die Zahlen kommen
# aus dem, was der Inhalt aushaelt, nicht aus einer runden Vorliebe:
#   Zwischenmeldungen beschreiben einen Arbeitsstand -- der haelt am laengsten.
#   Vorlesen ist die Chat-Antwort; steht sie sowieso im Chat, ist sie nach einer
#     Dreiviertelminute eher Laerm als Nachricht.
#   Eine Antwort auf einen Zuruf ist am kuerzesten haltbar: er hat gefragt und
#     wartet -- kommt sie eine halbe Minute spaeter, hat er die Frage vergessen.
FRIST = {
    RANG_SOFORT: None,          # nie verfallen
    RANG_ANTWORT: 30.0,
    RANG_ZWISCHEN: 90.0,
    RANG_VORLESEN: 45.0,
}

_sperre = threading.RLock()
_auftraege = []
_naechste_id = itertools.count(1)
_laeuft = threading.Event()
_faden = None
_sprecher = None

# Wird bei jedem Stopp hochgezaehlt. Ein laufender oder wartender Auftrag
# vergleicht seinen eigenen Stand damit -- stimmt er nicht mehr, ist er
# ueberholt. Ein Zaehler und kein Schalter, weil ein Schalter zurueckgesetzt
# werden muss und genau dabei das Rennen entsteht: wer ihn setzt, weiss nicht,
# ob der andere Faden ihn schon gesehen hat.
_abbruch_stand = 0


def _frist(rang):
    return FRIST.get(rang, FRIST[RANG_ZWISCHEN])


def einwerfen(text, rang=RANG_ZWISCHEN, quelle=''):
    """Einen Satz in die Zentrale legen. Kehrt sofort zurueck.

    Gibt die Nummer des Auftrags zurueck, oder None bei leerem Text.
    """
    text = (text or '').strip()
    if not text:
        return None
    jetzt = time.time()
    frist = _frist(rang)
    auftrag = {
        'id': next(_naechste_id),
        'text': text,
        'rang': rang,
        'quelle': quelle,
        'eingeworfen': jetzt,
        'verfaellt': None if frist is None else jetzt + frist,
    }
    with _sperre:
        _auftraege.append(auftrag)
    print('[Zentrale] eingeworfen (Rang %d, %s): %r'
          % (rang, quelle or '-', text[:50]), flush=True)
    return auftrag['id']


def _aufraeumen():
    """Verfallene Auftraege wegnehmen -- und sie als UNGESAGT festhalten.

    Das Festhalten ist der Punkt, an dem das Verfallen ertraeglich wird: der
    Satz ist nicht verloren, er ist nur nicht gesprochen worden. "Nochmal" holt
    ihn (siehe noor-nochmal.ps1).
    """
    jetzt = time.time()
    with _sperre:
        weiter, raus = [], []
        for a in _auftraege:
            (raus if a['verfaellt'] and jetzt > a['verfaellt'] else weiter).append(a)
        _auftraege[:] = weiter
    for a in raus:
        _verworfen(a, 'verfallen')


def _verworfen(auftrag, grund):
    print('[Zentrale] %s (Rang %d, %s): %r'
          % (grund, auftrag['rang'], auftrag['quelle'] or '-',
             auftrag['text'][:50]), flush=True)
    try:
        import warteschlange
        warteschlange.merke(auftrag['text'], 'UNGESAGT')
    except Exception:
        pass


def _bestes():
    """Den wichtigsten wartenden Auftrag nehmen -- Rang zuerst, dann Alter."""
    with _sperre:
        if not _auftraege:
            return None
        auftrag = min(_auftraege, key=lambda a: (a['rang'], a['eingeworfen']))
        _auftraege.remove(auftrag)
        return auftrag


def _wartet_besseres_als(rang):
    with _sperre:
        return any(a['rang'] < rang for a in _auftraege)


def _zurueck(auftrag):
    with _sperre:
        _auftraege.append(auftrag)


def anzahl():
    with _sperre:
        return len(_auftraege)


def stoppe_alles(grund='Stopp'):
    """Sofort still sein: laufenden Satz abbrechen UND die Liste leeren.

    Beides gehoert zusammen, und das ist Ramzis eigentliche Beschwerde: einen
    Satz abzubrechen, hinter dem noch vier weitere warten, ist kein Stopp,
    sondern eine Pause. Was hier weggeraeumt wird, landet als UNGESAGT im
    Protokoll -- er kann es mit "nochmal" nachholen.
    """
    global _abbruch_stand
    with _sperre:
        _abbruch_stand += 1
        offen = list(_auftraege)
        _auftraege.clear()
    try:
        if _sprecher is not None:
            _sprecher.stoppe()
    except Exception:
        pass
    # Sofort und nicht erst beim nächsten Durchgang: er hat gestoppt, also
    # soll sein Video in derselben Sekunde weiterlaufen.
    _buehne(False)
    for a in offen:
        _verworfen(a, 'geleert')
    print('[Zentrale] %s -- %d Auftraege verworfen.' % (grund, len(offen)),
          flush=True)
    return len(offen)


def unterbrich(grund='Ramzi hat übernommen'):
    """Nur den LAUFENDEN Satz abbrechen -- was wartet, wartet weiter.

    Der Unterschied zu stoppe_alles() ist Absicht und wichtig: fängt er mitten
    in meinem Satz an zu reden, will er diesen Satz weghaben, nicht alles.
    Würde die Liste dabei geleert, verlöre jede Zwischenmeldung, die während
    seines Satzes eingeht, sofort ihren Sinn -- die Zentrale soll auf ihn
    warten, nicht wegwerfen. Geleert wird nur, wenn er es ausdrücklich sagt
    ("sei still", Stopp-Taste, Stopp-Knopf).
    """
    global _abbruch_stand
    with _sperre:
        _abbruch_stand += 1
    try:
        if _sprecher is not None:
            _sprecher.stoppe()
    except Exception:
        pass
    _buehne(False)
    print('[Zentrale] unterbrochen (%s) -- %d Aufträge warten weiter.'
          % (grund, anzahl()), flush=True)


def _darf_jetzt(auftrag, stand):
    """Warten, bis gesprochen werden darf. Sagt, was aus dem Auftrag wird.

    'los'       -- er ist still, raus damit
    'verfallen' -- zu lange gewartet, der Inhalt taugt nicht mehr
    'zurueck'   -- etwas Wichtigeres ist eingegangen, spaeter nochmal
    'gestoppt'  -- Stopp waehrend des Wartens
    """
    while _laeuft.is_set():
        if stand != _abbruch_stand:
            return 'gestoppt'
        if auftrag['verfaellt'] and time.time() > auftrag['verfaellt']:
            return 'verfallen'
        if _wartet_besseres_als(auftrag['rang']):
            return 'zurueck'
        try:
            import warteschlange
            # `er_ist_fertig` und nicht `not ramzi_ist_dran`: nach seinem
            # letzten Wort gehört eine kurze Ruhe dazu, sonst falle ich in
            # seine Denkpause hinein. Siehe warteschlange.NACHLAUF.
            if warteschlange.er_ist_fertig():
                return 'los'
            if time.time() - auftrag['eingeworfen'] > warteschlange.KAPUTT_NACH:
                print('[Zentrale] Merker hängt seit %.0f s -- ich rede trotzdem.'
                      % warteschlange.KAPUTT_NACH, flush=True)
                return 'los'
        except Exception:
            return 'los'
        time.sleep(0.05)
    return 'gestoppt'


_buehne_gehalten = False


def _buehne(an):
    """Video anhalten bzw. weiterlaufen lassen -- für den GANZEN Redezug.

    Ramzis Befund vom 07.08.2026: "nach einer bestimmten Zeit geht das Video
    einfach weiter, obwohl du gerade noch redest." Das Anhalten hing am
    einzelnen Satz, und sobald mehrere Aufträge hintereinander kommen, lief das
    Video zwischen je zwei Sätzen kurz an.

    Hier gehalten, weil hier der Zug bekannt ist: die Zentrale weiß, ob noch
    etwas wartet, `_sprich()` weiß das nicht. Losgelassen wird erst, wenn die
    Liste leer ist -- oder sofort beim Stopp.
    """
    global _buehne_gehalten
    if an == _buehne_gehalten:
        return
    try:
        import voice_output
        voice_output.buehne_an() if an else voice_output.buehne_aus()
        _buehne_gehalten = an
    except Exception:
        pass


def _lauf():
    while _laeuft.is_set():
        _aufraeumen()
        auftrag = _bestes()
        if auftrag is None:
            _buehne(False)
            time.sleep(0.08)
            continue

        stand = _abbruch_stand
        was = _darf_jetzt(auftrag, stand)
        if was == 'verfallen':
            _verworfen(auftrag, 'verfallen')
            continue
        if was == 'zurueck':
            _zurueck(auftrag)
            continue
        if was == 'gestoppt':
            continue

        _buehne(True)
        try:
            ergebnis = _sprecher.sprich(auftrag['text'])
        except Exception as e:
            print('[Zentrale] Sprechen fehlgeschlagen: %s' % e, flush=True)
            continue

        # Was ist daraus geworden? Der Sprecher sagt es selbst.
        #
        # ABGEBROCHEN heisst: der Satz stand zum Teil im Raum, der Rest fehlt
        # ihm. Aufgeschrieben wird der GANZE Text -- wo genau der Ton
        # abgeschnitten wurde, weiss niemand (der Sprecher gibt Ton an die
        # Karte und stoppt den Strom, es gibt keine Marke "bis hierher
        # gehoert"). Von vorn zu wiederholen ist ehrlich; ein "weiter ab
        # Wort 14" waere geraten. Genau so hat Ramzi es selbst vorgeschlagen.
        if ergebnis == 'ungesagt':
            _verworfen(auftrag, 'nicht gesprochen')
        elif ergebnis == 'abgebrochen' or stand != _abbruch_stand:
            print('[Zentrale] abgebrochen: %r' % auftrag['text'][:50], flush=True)
            try:
                import warteschlange
                warteschlange.merke(auftrag['text'], 'ABGEBROCHEN')
            except Exception:
                pass


def starte(sprecher):
    """Die Zentrale in Gang setzen. Nur der Assistent ruft das."""
    global _faden, _sprecher
    _sprecher = sprecher
    # ERST den Schalter, DANN nachsehen, ob schon jemand laeuft.
    #
    # Andersherum gab es eine Luecke, die der Selbsttest gefunden hat: wird
    # starte() gerufen, waehrend ein alter Faden gerade noch einen Satz zu Ende
    # spricht, sah die Pruefung "lebt noch" und kehrte zurueck -- ohne den
    # Schalter je gesetzt zu haben. Der alte Faden lief danach aus, ein neuer
    # kam nie, und die Zentrale war lautlos tot. In dieser Reihenfolge macht
    # der alte Faden einfach mit dem neuen Sprecher weiter.
    _laeuft.set()
    if _faden is not None and _faden.is_alive():
        return _faden
    _faden = threading.Thread(target=_lauf, name='sprechzentrale', daemon=True)
    _faden.start()
    return _faden


def beenden():
    _laeuft.clear()
