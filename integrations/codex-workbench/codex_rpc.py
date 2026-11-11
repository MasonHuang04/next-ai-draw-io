"""Line-delimited JSON-RPC client for the official local Codex app-server."""
import json
import os
import pathlib
import queue
import shutil
import subprocess
import threading
import time
import tomllib


def cli_path():
    candidate = os.environ.get('CODEX_CLI_PATH') or shutil.which('codex.exe') or shutil.which('codex')
    if candidate and pathlib.Path(candidate).is_file():
        return candidate
    candidates = list((pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home() / 'AppData/Local')) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
    if not candidates:
        raise RuntimeError('Codex CLI is not installed.')
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


class CodexRPC:
    def __init__(self, state_dir, lean=False, proxy=False):
        self.state_dir = pathlib.Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._pending = {}
        self._write_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._counter = 0
        self.events = []
        self.event_condition = threading.Condition()
        self.server_requests = {}
        self.closed = threading.Event()
        self.proc = None
        command = [cli_path(), 'app-server']
        if proxy:
            command += ['proxy']
        if lean:
            home = pathlib.Path(os.environ.get('CODEX_HOME', pathlib.Path.home() / '.codex'))
            config_file = home / 'config.toml'
            config = tomllib.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
            for name in config.get('mcp_servers', {}):
                command += ['-c', f'mcp_servers.{name}.enabled=false']
            for feature in ('apps', 'hooks', 'multi_agent', 'remote_plugin', 'shell_snapshot'):
                command += ['--disable', feature]
        self.stderr = (self.state_dir / 'codex-server.log').open('a', encoding='utf-8')
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.stderr, text=True, encoding='utf-8', bufsize=1,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._reader, daemon=True).start()
        self.initialized = self.call('initialize', {
            'clientInfo': {'name': 'drawio_codex_workbench', 'title': 'Drawio Codex Workbench', 'version': '0.2.0'},
            'capabilities': {'experimentalApi': True}}, timeout=40)
        self.notify('initialized', {})

    def _reader(self):
        try:
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if 'id' in message and 'method' not in message:
                    future = self._pending.get(message['id'])
                    if future:
                        future.put(message)
                else:
                    if message.get('method') == 'serverRequest/resolved':
                        self.server_requests.pop(str(message.get('params',{}).get('requestId')), None)
                    if 'id' in message:
                        self.server_requests[str(message['id'])] = message
                    with self.event_condition:
                        self.events.append(message)
                        self.event_condition.notify_all()
        finally:
            self.closed.set()
            with self.event_condition:
                self.event_condition.notify_all()
            error = {'error': {'message': 'Codex app-server connection closed.'}}
            for future in list(self._pending.values()):
                future.put(error)

    def _send(self, message):
        with self._write_lock:
            if self.proc.poll() is not None:
                raise RuntimeError('Codex app-server exited.')
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()

    def call(self, method, params=None, timeout=60):
        with self._counter_lock:
            self._counter += 1
            request_id = self._counter
            future = queue.Queue()
            self._pending[request_id] = future
        try:
            self._send({'id': request_id, 'method': method, 'params': params or {}})
            response = future.get(timeout=timeout)
            if 'error' in response:
                raise RuntimeError(json.dumps(response['error'], ensure_ascii=False))
            return response.get('result', {})
        except queue.Empty:
            raise TimeoutError(f'Codex timed out: {method}')
        finally:
            self._pending.pop(request_id, None)

    def notify(self, method, params):
        self._send({'method': method, 'params': params})

    def answer(self, request_id, result):
        original = self.server_requests.pop(str(request_id), None)
        if original is None:
            raise RuntimeError('该请求已经结束或已由其他窗口处理。')
        self._send({'id': original['id'], 'result': result})

    def reject(self, request_id, message):
        original = self.server_requests.pop(str(request_id), None)
        if original is not None:
            self._send({'id': original['id'], 'error': {'code':-32601,'message':message}})

    def wait_for(self, method, thread_id=None, after=0, timeout=120):
        deadline = time.monotonic() + timeout
        with self.event_condition:
            while True:
                for index, event in enumerate(self.events[after:], start=after):
                    if event.get('method') == method and (not thread_id or event.get('params', {}).get('threadId') == thread_id):
                        return index, event
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(method)
                self.event_condition.wait(min(remaining, 10))

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        self.closed.set()
        self.stderr.close()
