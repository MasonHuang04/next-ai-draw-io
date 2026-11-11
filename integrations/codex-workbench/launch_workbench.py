"""Open the local workbench without inheriting Electron's Node-only mode."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
distribution = root / 'node_modules' / 'electron' / 'dist'
if sys.platform == 'win32':
    executable = distribution / 'electron.exe'
elif sys.platform == 'darwin':
    executable = distribution / 'Electron.app/Contents/MacOS/Electron'
else:
    executable = distribution / 'electron'
if not executable.is_file():
    raise SystemExit('Install this integration first: npm ci')
environment = os.environ.copy()
environment.pop('ELECTRON_RUN_AS_NODE', None)
environment['DRAWIO_PYTHON'] = sys.executable
options = {}
if sys.platform == 'win32':
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 1
    options['startupinfo'] = startup
subprocess.Popen([str(executable), str(root)], cwd=root, env=environment, **options)
