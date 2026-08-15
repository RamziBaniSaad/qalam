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

# --- Der letzte Abbruch, damit ein Fehlalarm sich selbst heilen kann --------
#
# Ramzis Vorschlag vom 08.08.2026: bricht mein eigener Lautsprecher meinen Satz
# ab (das Ohr hielt mein Echo fuer seine Uebernahme), erkennt die Bruecke das
# Stueck kurz darauf als mein Echo und verwirft es still. Genau dann steht
# fest, dass der Stopp ein Fehlalarm war -- und genau dann soll der Rest
# zurueckkommen, statt dass Ramzi mich jedes Mal von Hand anstoesst.
#
# Hier gemerkt und nicht aus noor-gesagt.log gelesen: die Zentrale weiss den
# Rest ohnehin (sie schreibt ihn gleich daneben als ABGEBROCHEN ins Protokoll),
# und ein zweiter Leser derselben Sache waere eine zweite Wahrheit.
#
# `erlaubt` ist der wichtige Teil und trennt zwei Abbrueche, die gleich
# aussehen: `unterbrich()` heisst "Ramzi hat uebernommen" -- eine Vermutung,
# die sich als Fehlalarm herausstellen kann. `stoppe_alles()` heisst, er hat
# ausdruecklich Ruhe verlangt (Zuruf, Stopp-Taste, Stopp-Knopf). Was er
# ausdruecklich gestoppt hat, wird NIE von selbst wieder aufgenommen.
_ABBRUCH_FRIST = 25.0
_letzter_abbruch = {'zeit': 0.0, 'rest': '', 'erlaubt': False}


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


def _darf_reden():
    """Steht der Schalter "Reden" auf der Tafel an?

    Fehlt die Einstellung oder klemmt das Lesen, wird geredet. Ein Werkzeug,
    das bei einem Lesefehler verstummt, sieht aus wie ein kaputtes Ohr -- und
    genau danach hat Ramzi schon mehrfach gesucht.
    """
    try:
        import einstellungen
        return bool(einstellungen.hole('reden'))
    except Exception:
        return True


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
        # Er hat ausdruecklich Ruhe verlangt -- hier gibt es nichts von selbst
        # weiterzureden. Siehe _letzter_abbruch.
        _letzter_abbruch['erlaubt'] = False
        _letzter_abbruch['rest'] = ''
        offen = list(_auftraege)
        _auftraege.clear()
    # UND den Briefkasten -- sonst ist der Stopp nur halb.
    #
    # Ramzis Befund vom 08.08.2026: er rief "stopp", der laufende Satz brach
    # ab, und ich machte mit dem naechsten weiter; erst das zweite "stopp"
    # wirkte. Die Liste hier oben war naemlich leer, die noch ungelesenen
    # Zettel lagen aber in qalam/.sprechpost und wurden gleich darauf
    # abgeholt. Nach "nochmal" liegen dort regelmaessig mehrere.
    nachpost = 0
    try:
        import sprechpost
        nachpost = sprechpost.leeren()
    except Exception:
        pass
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
    print('[Zentrale] %s -- %d Auftraege verworfen, %d Zettel aus dem Kasten.'
          % (grund, len(offen), nachpost), flush=True)
    # Bewusst nur die Auftraege: das ist die Zahl, die der Aufrufer meint
    # ("wie viel habe ich dir weggenommen"). Die Zettel sind Aufraeumarbeit.
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
        # Eine Vermutung, kein Befehl: stellt sich gleich heraus, dass das
        # meine eigene Stimme war, holt die Bruecke den Rest zurueck.
        _letzter_abbruch['erlaubt'] = True
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


# Wer sagt, ob das Gespraechsfenster noch offen ist? Das Ohr, und nur das --
# dort liegt `folge_bis`, und dort wird es auch verlaengert, solange sein Satz
# laeuft. Die Zentrale fragt nach, statt die Frist selbst mitzurechnen: zwei
# Uhren fuer denselben Zustand laufen frueher oder spaeter auseinander, und
# dann waere die Musik leise, obwohl das Fenster zu ist (oder umgekehrt).
#
# Eingehaengt von assistant.py, sobald das Ohr steht. Fehlt der Haken -- etwa
# beim Start oder in einem Testlauf --, gilt "kein Fenster offen"; dann
# verhaelt sich die Buehne wie vorher, statt haengenzubleiben.
gespraech_offen = None


def _gespraech_offen():
    try:
        return bool(gespraech_offen and gespraech_offen())
    except Exception:
        return False


def _lauf():
    while _laeuft.is_set():
        _aufraeumen()
        auftrag = _bestes()
        if auftrag is None:
            # DIE BUEHNE BLEIBT UNTEN, SOLANGE ER NOCH ANTWORTEN DARF.
            #
            # Ramzis Idee vom 15.08.2026, und sie ist besser als das, was ich
            # vorgeschlagen haette: nicht die Musik in dem Moment leise machen,
            # in dem er zu reden anfaengt, sondern sie leise LASSEN, bis das
            # Gespraechsfenster zu ist.
            #
            # Zwei Dinge auf einmal, und beide zaehlen:
            #
            #   * Er hoert, ob er noch reden darf. Musik leise = das Fenster
            #     ist offen. Musik wieder laut = vorbei, jetzt braucht es
            #     wieder meinen Namen oder die Taste. Ein Zustand, der bisher
            #     nur auf der Tafel stand, ist damit hoerbar -- und zwar genau
            #     dort, wo er ohnehin hinhoert.
            #   * Musik nebenbei wird benutzbar. Sein Mikrofon hoert die
            #     Lautsprecher mit; bleibt die Musik waehrend des ganzen
            #     Fensters leise, kommt sein Satz durch, ohne dass er etwas
            #     anfassen muss. Danach wird es von selbst wieder laut.
            #
            # Faengt er wirklich an zu reden, uebernimmt die Redepause: das
            # Fenster laeuft dann nicht mehr ab, solange sein Satz laeuft
            # (`satz_laeuft` in wake_word), und die Buehne bleibt entsprechend
            # unten. Genau das wollte er: "dann gilt ja die Redepause".
            if not _gespraech_offen():
                _buehne(False)
            time.sleep(0.08)
            continue

        # DARF ICH UEBERHAUPT REDEN? Der Schalter auf der Tafel.
        #
        # Hier und nicht in `einwerfen()`: wer einwirft, soll nicht wissen
        # muessen, ob gerade jemand zuhoert. Und hier und nicht im Sprecher,
        # damit es EINE Stelle bleibt -- das war der ganze Grund fuer die
        # Zentrale.
        #
        # Verworfen und nicht aufgestaut: kommt Ramzi nach zwanzig Minuten
        # zurueck, ist ein Schwall alter Zwischenstaende schlimmer als Stille.
        # Als UNGESAGT steht alles im Protokoll, "nochmal" holt es.
        if not _darf_reden():
            _verworfen(auftrag, 'stumm geschaltet')
            _buehne(False)
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
        # ihm. Aufgeschrieben wird NUR der Rest -- also ab dem Satz, der beim
        # Stopp gerade lief. Alles davor steht schon einzeln als GESAGT da,
        # weil der Sprecher jeden Teilsatz vermerkt, waehrend er ihn spricht.
        #
        # Vorher stand hier der ganze Auftragstext, und das war Ramzis Befund
        # vom 08.08.2026: "nochmal" las ihm alles von vorn vor, auch das schon
        # Gehoerte -- zwangslaeufig, denn es nimmt die Nicht-GESAGT-Zeilen, und
        # die enthielten den kompletten Text ein zweites Mal.
        if ergebnis == 'ungesagt':
            _verworfen(auftrag, 'nicht gesprochen')
        elif ergebnis == 'abgebrochen' or stand != _abbruch_stand:
            rest = getattr(_sprecher, '_nicht_gesagt', '') or auftrag['text']
            print('[Zentrale] abgebrochen, es fehlt: %r' % rest[:50], flush=True)
            try:
                import warteschlange
                warteschlange.merke(rest, 'ABGEBROCHEN')
            except Exception:
                pass
            # Und denselben Rest zum Abholen bereitlegen -- fuer den Fall, dass
            # sich der Abbruch gleich als mein eigenes Echo herausstellt.
            with _sperre:
                if _letzter_abbruch['erlaubt']:
                    _letzter_abbruch['zeit'] = time.time()
                    _letzter_abbruch['rest'] = rest
                    # Der Rang des Auftrags gehoert mit: was als Antwort auf
                    # einen Zuruf begonnen hat, soll auch als Antwort
                    # weitergehen und nicht hinter einer Zwischenmeldung warten.
                    _letzter_abbruch['rang'] = auftrag['rang']


# Die Bremse gegen die Rueckkopplung. Was ich nachspreche, geht wieder ins
# Mikrofon -- ohne Zaehlung koennte sich das aufschaukeln. Zweimal in einer
# Minute reicht: wer beim dritten Anlauf wieder abgewuergt wird, hat kein
# Echo-Problem mehr, sondern ein anderes, und dann ist Stille ehrlicher.
_WEITER_FENSTER = 60.0
_WEITER_HOECHSTENS = 2
_weiter_versuche = []


def weiterreden_nach_fehlalarm(woher=''):
    """Den abgebrochenen Rest zurueckholen -- der Anrufer hat den Fehlalarm
    bereits festgestellt.

    Es gibt ZWEI Stellen, an denen sich ein Abbruch nachtraeglich als mein
    eigenes Echo herausstellt, und beide brauchen dieselbe Reaktion:

      * die Bruecke, wenn ein fertiger Auftrag als mein Echo verworfen wird
      * der Assistent, wenn ueberhaupt nichts abgeschickt wird -- das ist der
        Fall, den der Stoppwort-Pfad im Ohr erzeugt: der wirkt auch AUSSERHALB
        eines Gespraechs, und dann sammelt niemand einen Satz, den die Bruecke
        pruefen koennte. Genau so bin ich mich am 08.08.2026 zweimal selbst
        losgeworden.

    Deshalb steht das Handeln hier und nicht bei einem der beiden Anrufer.
    Gibt zurueck, ob wirklich weitergeredet wird.
    """
    try:
        import einstellungen
        if not einstellungen.hole('echo_weiterreden'):
            return False
    except Exception:
        return False
    jetzt = time.time()
    _weiter_versuche[:] = [t for t in _weiter_versuche
                           if jetzt - t < _WEITER_FENSTER]
    if len(_weiter_versuche) >= _WEITER_HOECHSTENS:
        print('[Zentrale] Fehlalarm (%s), aber schon %dx weitergeredet -- '
              'ich bleibe still.' % (woher or '-', _WEITER_HOECHSTENS),
              flush=True)
        return False
    rest, rang = hole_abbruch()
    if not rest:
        return False
    _weiter_versuche.append(jetzt)
    einwerfen(rest, rang=rang, quelle='weiterreden')
    print('[Zentrale] Fehlalarm (%s) -- ich rede weiter: %r'
          % (woher or '-', rest[:60]), flush=True)
    return True


def hole_abbruch(hoechstens=_ABBRUCH_FRIST):
    """Den Rest eines Satzes, der eben faelschlich abgebrochen wurde.

    Gibt `(text, rang)` zurueck -- oder `(None, None)`, wenn es nichts zu holen
    gibt. Der Rest wird dabei VERBRAUCHT, und das ist die Bremse gegen die
    Schleife: rede ich weiter, kommt dieselbe Stimme wieder ins Mikrofon, wird
    wieder als Echo erkannt -- und ohne das Verbrauchen wuerde ich mir denselben
    Satz endlos selbst vorlesen.

    Die Frist ist die zweite Bremse: was vor einer halben Minute abgebrochen
    wurde, hat mit dem Echo, das jetzt ankommt, nichts mehr zu tun.
    """
    with _sperre:
        rest = _letzter_abbruch['rest']
        if not rest or time.time() - _letzter_abbruch['zeit'] > hoechstens:
            return None, None
        _letzter_abbruch['rest'] = ''
        return rest, _letzter_abbruch.get('rang', RANG_ZWISCHEN)


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
