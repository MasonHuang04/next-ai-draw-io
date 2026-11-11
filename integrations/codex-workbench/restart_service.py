"""Restart this project's idle local backend after editing Python sources."""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT=pathlib.Path(__file__).resolve().parent
STATE=ROOT/'state'
STATE.mkdir(exist_ok=True)
PORT=int(os.environ.get('CODEX_WORKBENCH_PORT','61429'))
BASE=f'http://127.0.0.1:{PORT}'

def health():
    try:
        with urllib.request.urlopen(BASE+'/health',timeout=1) as response:
            return json.load(response)
    except OSError:
        return None

current=health()
if current:
    if current.get('projectRoot')!=str(ROOT):
        raise SystemExit(f'Another workbench or an older service is using port {PORT}.')
    token=(STATE/'token.txt').read_text(encoding='utf-8').strip()
    request=urllib.request.Request(BASE+'/api/shutdown',data=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        urllib.request.urlopen(request,timeout=5).close()
    except urllib.error.HTTPError as error:
        print(json.load(error).get('error','Cannot stop the running service.'))
        raise SystemExit(1)
    for _ in range(30):
        if health() is None:
            break
        time.sleep(.2)
with (STATE/'server.log').open('ab') as log:
    subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,stdin=subprocess.DEVNULL,
        stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
for _ in range(80):
    state=health()
    if state and state.get('ready'):
        print('Workbench service restarted. Reload the window to load UI changes.')
        break
    time.sleep(.25)
else:
    raise SystemExit('The service did not become ready. Check state/server.log.')
