import os
import sys
import time
from pathlib import Path
import subprocess
from dotenv import load_dotenv
import glob

def set_cuda_paths():
    """Set up CUDA paths for GPU support."""
    try:
        # Find all CUDA installations in a version-agnostic way
        cuda_base_path = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
        if os.path.exists(cuda_base_path):
            # Get all v12.x folders, sorted by version number (newest first)
            cuda_versions = glob.glob(os.path.join(cuda_base_path, "v12.*"))
            cuda_versions.sort(key=lambda x: [int(n) for n in x.split('v')[1].split('.')], reverse=True)
            
            if cuda_versions:
                system_cuda_path = cuda_versions[0]  # Use the newest version
                cuda_version = os.path.basename(system_cuda_path)[1:]  # Remove 'v' prefix
                print(f'Found system CUDA version: {cuda_version}')
                
                # Use system CUDA
                paths_to_add = [
                    os.path.join(system_cuda_path, 'bin'),
                    os.path.join(system_cuda_path, 'libnvvp'),
                ]
                
                # Add cuDNN paths if present
                cudnn_path = os.path.join(system_cuda_path, 'cudnn')
                if os.path.exists(cudnn_path):
                    paths_to_add.append(os.path.join(cudnn_path, 'bin'))
                
                print(f'Using system CUDA from: {system_cuda_path}')
            else:
                print('No CUDA 12.x installation found in system')
                return check_bundled_cuda()
        else:
            print('NVIDIA CUDA Toolkit folder not found')
            return check_bundled_cuda()
            
        # Update environment variables
        env_vars = ['CUDA_PATH', f'CUDA_PATH_V{cuda_version.replace(".", "_")}', 'PATH']
        for env_var in env_vars:
            current_value = os.environ.get(env_var, '')
            new_value = os.pathsep.join(paths_to_add + [current_value] if current_value else paths_to_add)
            os.environ[env_var] = new_value
            
        print('CUDA paths set up successfully')
        
    except Exception as e:
        print(f'Error setting up CUDA paths: {e}')
        print('Falling back to CPU mode')

def check_bundled_cuda():
    """Check for bundled CUDA in virtual environment."""
    venv_base = Path(sys.executable).parent.parent
    nvidia_base_path = venv_base / 'Lib' / 'site-packages' / 'nvidia'
    
    if not nvidia_base_path.exists():
        print('No CUDA installation found (neither system nor bundled), using CPU mode')
        return
        
    cuda_path = nvidia_base_path / 'cuda_runtime' / 'bin'
    cublas_path = nvidia_base_path / 'cublas' / 'bin'
    cudnn_path = nvidia_base_path / 'cudnn' / 'bin'
    
    if not all(p.exists() for p in [cuda_path, cublas_path, cudnn_path]):
        print('Some bundled CUDA components missing, using CPU mode')
        return
        
    paths_to_add = [str(cuda_path), str(cublas_path), str(cudnn_path)]
    print('Using bundled CUDA from virtual environment')
    
    # Update environment variables
    env_vars = ['CUDA_PATH', 'CUDA_PATH_V12_4', 'PATH']
    for env_var in env_vars:
        current_value = os.environ.get(env_var, '')
        new_value = os.pathsep.join(paths_to_add + [current_value] if current_value else paths_to_add)
        os.environ[env_var] = new_value

# Set CUDA paths before anything else
set_cuda_paths()

print('Starting Qalam...')
load_dotenv()

# --------------------------------------------------------------------------
# Das Ohr gehört dazu.
#
# Ramzi hat zwei Wege, mit Noor zu reden: die Tastenkombination (Diktat) und
# ihren Namen (Weckwort). Beide gehören zu Qalam, also darf nicht der eine
# starten und der andere nicht -- genau das war bis zum 31.07.2026 der Fall:
# assistant.py existierte, aber niemand hat es je gestartet. Auf dem Dashboard
# stand deshalb dauerhaft "Weckwort aus", und "Noor" zu rufen half nicht.
#
# Ein eigener Prozess und nicht in main.py hinein: main.py ist die Qt-App mit
# dem Tray-Symbol; ein zweiter dauerhafter Audiostrom darin würde ihre
# Ereignisschleife und das Diktat aneinanderbinden. Getrennte Prozesse teilen
# sich das Mikrofon unter Windows problemlos, und der Riegel .aufnahme.lock
# sorgt dafür, dass das Ohr während eines Diktats schweigt.
#
# Der Umschalter (toggle_tools.vbs) beendet alles, dessen Befehlszeile "qalam"
# enthält -- das trifft diesen Prozess mit, ohne dass dort etwas zu ändern ist.
# --------------------------------------------------------------------------
IST_WINDOWS = sys.platform == 'win32'
HIER = os.path.dirname(os.path.abspath(__file__))


def im_projekt(*teile):
    """Absoluter Pfad ins Projekt -- und das ist kein Schoenheitsfehler.

    Der Umschalter (toggle_tools.vbs) beendet alles, dessen Befehlszeile das
    Wort "qalam" enthaelt. Bei einem relativen Aufruf wie `src\\assistant.py`
    steht "qalam" aber nur im Pfad der venv-Huelle -- der ECHTE Python-Prozess
    dahinter laeuft unter dem System-Python und heisst dann schlicht
    `pythonw.exe src\\assistant.py`. Der wurde vom Umschalter nie getroffen.

    Folge, am 31.07.2026 am Geraet gemessen: jedes Aus-und-wieder-An liess ein
    Ohr und einen Untertitel-Streifen zurueck und startete neue dazu. Zwei Ohren
    streiten sich um dasselbe Mikrofon -- danach hoerte keines mehr etwas, und
    Ramzi hat dreissigmal vergeblich meinen Namen gerufen.

    Mit dem absoluten Pfad steht "qalam" in JEDER Befehlszeile, und der
    Umschalter trifft alles.
    """
    return os.path.join(HIER, *teile)

# Auf macOS laeuft NUR das Diktat.
#
# Ramzis Entscheidung vom 31.07.2026: Ohr, Bruecke und Fenstersteuerung sind
# tief in Windows verdrahtet (AttachThreadInput, win32clipboard,
# Fensterklassen). Am MacBook arbeitet er zwei Tage im Monat und wuerde die
# Sprachschicht dort praktisch nie brauchen -- was er dort braucht, ist Qalam
# mit der Tastenkombination. Soll ich dort mal ein Fenster oeffnen, sagt er es
# mir einfach; das kostet ein paar Tokens, aber vielleicht einmal im Monat.
#
# KEINE getrennten Zweige im Repo dafuer: zwei Zweige, die sich dauerhaft
# unterscheiden, driften auseinander, jeder Fehler muss zweimal behoben werden
# und irgendwann laesst sich nichts mehr zusammenfuehren. Ein Zweig, und die
# Unterschiede stehen an genau einer Stelle als Bedingung -- hier.
ohr = None
ohr_log = None
schrift = None


def _schon_da():
    """Läuft main.py schon? Dann gibt es hier keine zweite.

    Ramzis Befund vom 06.08.2026 nachts: „es laufen irgendwie zwei Instanzen von
    Qalam." Gemessen und bestätigt — main.py lief zweimal, einmal aus dem
    Windows-Autostart (launcher_hidden.vbs) und einmal als Kind von hier. Zwei
    Tray-Symbole, zwei Anwärter auf dieselbe Tastenkombination.

    Es ist dieselbe Falle wie am 31.07., nur eine Ebene höher: damals stritten
    zwei Ohren um dasselbe Mikrofon, und danach hörte keines mehr etwas.

    GEFRAGT WIRD, BEVOR IRGENDETWAS STARTET. Das ist keine Kosmetik: wird das
    Ohr vorher aufgemacht und danach festgestellt, dass schon eine Instanz
    läuft, dann wartet dieser Prozess auf die andere -- und nimmt beim
    Aufräumen sein eigenes, frisch gestartetes Ohr mit. Genau so ist Qalam am
    07.08.2026 um 02:39 stumm geworden: ein Neustart, bei dem die alte Instanz
    noch eine Sekunde am Sterben war, hat die neue mit in den Tod gerissen.

    WARTEN und nicht überspringen: der Aufruf unten bestimmt die Lebensdauer
    dieses Prozesses — endet er, räumt der finally-Zweig Ohr und Untertitel mit
    weg. Kehrte ich hier einfach zurück, wären beide sofort wieder tot.

    Rückgabe: die Prozesse, auf die zu warten ist (leer = ich starte selbst).
    Die venv-Hülle startet den Basis-Interpreter als Kind, es sind also
    typischerweise ZWEI Einträge für EIN Programm — für die Wartefrage
    unerheblich.
    """
    try:
        import psutil
    except Exception:
        return []
    ziel = os.path.join('src', 'main.py').lower()
    treffer = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['pid'] == os.getpid():
                continue
            # NUR echte Python-Prozesse. Der Vergleich lief vorher gegen JEDE
            # Befehlszeile, in der die beiden Wörter vorkommen -- und damit
            # gegen jede Shell, die gerade etwas über Qalam tut. Am 07.08.2026
            # hat sich Qalam genau daran selbst blockiert: ein Neustartskript
            # nannte in seiner eigenen Befehlszeile den Pfad `src\main.py`,
            # run.py hielt dieses Skript für eine laufende Instanz, wartete auf
            # sein Ende -- und nahm dabei das eben gestartete Ohr mit.
            # Ein Prozess, der ÜBER Qalam redet, IST kein Qalam.
            if not (p.info['name'] or '').lower().startswith('python'):
                continue
            zeile = ' '.join(p.info['cmdline'] or []).lower()
            if ziel in zeile and 'qalam' in zeile:
                treffer.append(p)
        except Exception:
            continue
    return treffer


_laufende = _schon_da()

try:
    # Unter pythonw gibt es keine Konsole: ein Fehler beim Laden der Modelle
    # verschwindet spurlos, der Prozess laeuft weiter und hoert trotzdem nichts.
    # Genau diese Sorte Fehler ist teuer, also bekommt das Ohr ein Protokoll.
    if IST_WINDOWS and not _laufende:
        # Anhaengen, nicht ueberschreiben. Das Protokoll ist der einzige Ort, an
        # dem steht, wie lange die Modelle wirklich brauchen -- und am
        # 31.07.2026 hat genau ein Neustart des Ohrs die Messwerte fast
        # gekostet, die die Verzoegerung erklaeren sollten. Ein Protokoll, das
        # ein Neustart loescht, ist bei einem Fehler, der nur manchmal auftritt,
        # nutzlos.
        ohr_log = open(im_projekt('ohr.log'),
                       'a', encoding='utf-8', buffering=1)
        ohr_log.write(f'\n===== Ohr gestartet {time.strftime("%Y-%m-%d %H:%M:%S")} =====\n')
        ohr = subprocess.Popen([sys.executable, '-u', im_projekt('src', 'assistant.py')],
                               stdout=ohr_log, stderr=subprocess.STDOUT)
        print('Weckwort laeuft mit (Protokoll: ohr.log).')
    elif _laufende:
        print('Weckwort ausgelassen -- Qalam laeuft schon, das Ohr auch.')
    else:
        print('Weckwort ausgelassen -- nur unter Windows.')
except Exception as e:
    print(f'Weckwort nicht gestartet: {e}')

# Die Untertitel gehoeren dazu: sie zeigen, was gehoert und was gesagt wurde.
# Eigener Prozess, weil auch der Stop-Hook (PowerShell) sie beschriften koennen
# muss -- die beiden teilen sich eine Datei, keinen Kanal.
try:
    if IST_WINDOWS and not _laufende:
        schrift = subprocess.Popen([sys.executable, im_projekt('src', 'untertitel.py')])
        print('Untertitel laufen mit.')
except Exception as e:
    print(f'Untertitel nicht gestartet: {e}')

try:
    if _laufende:
        print(f'Qalam läuft schon ({len(_laufende)} Prozess(e)) -- '
              f'ich starte keine zweite und warte.')
        import psutil
        psutil.wait_procs(_laufende)
    else:
        subprocess.run([sys.executable, im_projekt('src', 'main.py')])
finally:
    if schrift and schrift.poll() is None:
        schrift.terminate()
        try:
            schrift.wait(timeout=5)
        except Exception:
            schrift.kill()
    # Ohne das bliebe das Ohr allein zurueck, wenn Qalam ueber das Tray-Symbol
    # beendet wird -- ein unsichtbarer Prozess, der weiter am Mikrofon haengt.
    if ohr and ohr.poll() is None:
        ohr.terminate()
        try:
            ohr.wait(timeout=5)
        except Exception:
            ohr.kill()
    if ohr_log:
        ohr_log.close()
