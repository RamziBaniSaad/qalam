import os
import sys
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
ohr = None
ohr_log = None
try:
    # Unter pythonw gibt es keine Konsole: ein Fehler beim Laden der Modelle
    # verschwindet spurlos, der Prozess laeuft weiter und hoert trotzdem nichts.
    # Genau diese Sorte Fehler ist teuer, also bekommt das Ohr ein Protokoll.
    ohr_log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ohr.log'),
                   'w', encoding='utf-8', buffering=1)
    ohr = subprocess.Popen([sys.executable, '-u', os.path.join('src', 'assistant.py')],
                           stdout=ohr_log, stderr=subprocess.STDOUT)
    print('Weckwort laeuft mit (Protokoll: ohr.log).')
except Exception as e:
    print(f'Weckwort nicht gestartet: {e}')

try:
    subprocess.run([sys.executable, os.path.join('src', 'main.py')])
finally:
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
