"""Local desktop workbench: official Codex history, shared queue and draw.io canvas."""
import hashlib
import json
import mimetypes
import os
import pathlib
import secrets
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from codex_rpc import CodexRPC
from approvals import make_response, SUPPORTED_METHODS

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / 'state'
STATE.mkdir(exist_ok=True)
WORKSPACE = ROOT / 'workspace'
WORKSPACE.mkdir(exist_ok=True)
PORT = int(os.environ.get('CODEX_WORKBENCH_PORT', '61429'))
TOKEN_FILE = STATE / 'token.txt'
if not TOKEN_FILE.exists():
    TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding='utf-8')
TOKEN = TOKEN_FILE.read_text(encoding='utf-8').strip()
def drawio_assets():
    configured = os.environ.get('DRAWIO_ASSETS')
    if configured:
        return pathlib.Path(configured).expanduser().resolve()
    candidates = [ROOT.parent.parent / 'public' / 'drawio',
                  pathlib.Path.home() / 'AppData/Local/Programs/Next AI Draw.io/resources/standalone/public/drawio']
    return next((candidate for candidate in candidates if (candidate/'index.html').is_file()), candidates[0])

DRAWIO = drawio_assets()
EMPTY = '<mxfile><diagram id="page-1" name="第 1 页"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>'


class Conflict(Exception):
    pass


class Workbench:
    def __init__(self):
        self.reader = CodexRPC(STATE / 'reader', lean=True)
        self.lock = threading.RLock()
        self.jobs = {}
        self.live = {}
        self.pending = {}
        self.changes = threading.Condition()
        self.revisions = {}
        self.reader_lock = threading.Lock()
        self.settings_path = STATE / 'settings.json'
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.settings = {'project': str(WORKSPACE), 'bindings': {}, 'selectedThread': None}

    def pulse(self, thread_id):
        with self.changes:
            self.revisions[thread_id] = self.revisions.get(thread_id, 0) + 1
            self.changes.notify_all()

    def event_state(self, thread_id):
        with self.lock:
            job = self.jobs.get(thread_id, {})
            worker = job.get('worker')
            return {'threadId': thread_id, 'revision': self.revisions.get(thread_id, 0),
                    'live': dict(self.live.get(thread_id, {})), 'job': job.get('status'),
                    'jobError': job.get('error'),
                    'canInterrupt': isinstance(worker, CodexRPC) and bool(job.get('turnId')),
                    'approvals': [dict(value) for value in self.pending.values() if value['threadId'] == thread_id]}

    def rpc(self, method, params=None):
        with self.reader_lock:
            if self.reader.closed.is_set() or self.reader.proc.poll() is not None:
                self.reader = CodexRPC(STATE / 'reader', lean=True)
            client = self.reader
        return client.call(method, params)

    def persist(self):
        with self.lock:
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.settings_path)

    def thread_list(self, cursor=None):
        params = {'limit': 50}
        if cursor:
            params['cursor'] = cursor
        result = self.rpc('thread/list', params)
        unique = {}
        for item in result['data']:
            unique.setdefault(item['id'], {k: item.get(k) for k in ('id','name','preview','cwd','createdAt','updatedAt','recencyAt','status')})
        return {'data': list(unique.values()), 'nextCursor': result.get('nextCursor')}

    def snapshot(self, thread_id, cursor=None):
        params = {'threadId': thread_id, 'limit': 30, 'sortDirection': 'desc', 'itemsView': 'full'}
        if cursor:
            params['cursor'] = cursor
        result = self.rpc('thread/turns/list', params)
        queued = self.rpc('thread/queue/list', {'threadId': thread_id})
        with self.lock:
            persisted = {item['id'] for turn in result['data'] if turn.get('status') != 'inProgress' for item in turn.get('items', [])
                         if item.get('type') == 'agentMessage' and item.get('text')}
            for item_id in persisted:
                self.live.get(thread_id, {}).pop(item_id, None)
            return {**result, 'queue': queued.get('data', []),
                    **self.event_state(thread_id)}

    def select(self, thread_id):
        thread = self.rpc('thread/read', {'threadId': thread_id})['thread']
        with self.lock:
            self.settings['selectedThread'] = thread_id
            self.settings['project'] = thread.get('cwd') or self.settings['project']
            self.persist()
        return {'thread': thread, 'canvas': self.canvas(thread_id)}

    def create_thread(self, cwd):
        cwd = pathlib.Path(cwd).resolve()
        if not cwd.is_dir():
            raise ValueError('项目文件夹不存在。')
        writer = CodexRPC(STATE / 'new-thread', lean=True)
        try:
            result = writer.call('thread/start', {'cwd': str(cwd), 'sandbox': 'workspace-write',
                'approvalPolicy': 'on-request', 'serviceName': 'drawio_codex_workbench'})
            thread_id = result['thread']['id']
            writer.call('thread/name/set', {'threadId': thread_id, 'name': '新绘图对话'})
        finally:
            writer.close()
        return self.select(thread_id)

    def drawing_path(self, thread_id):
        with self.lock:
            bound = self.settings['bindings'].get(thread_id)
            if bound:
                return pathlib.Path(bound)
        thread = self.rpc('thread/read', {'threadId': thread_id})['thread']
        folder = pathlib.Path(thread.get('cwd') or WORKSPACE) / '.drawio-codex'
        path = folder / (str(uuid.UUID(thread_id)) + '.drawio')
        with self.lock:
            self.settings['bindings'][thread_id] = str(path)
            self.persist()
        return path

    def canvas(self, thread_id):
        path = self.drawing_path(thread_id)
        xml = path.read_text(encoding='utf-8-sig') if path.exists() else EMPTY
        return {'path': str(path), 'xml': xml, 'version': hashlib.sha256(xml.encode('utf-8')).hexdigest()}

    def save_canvas(self, thread_id, xml, expected, new_path=None):
        if len(xml) > 16*1024*1024:
            raise ValueError('图纸超过 16 MB。')
        document = ET.fromstring(xml)
        if document.tag not in ('mxfile', 'mxGraphModel'):
            raise ValueError('文件不是有效的 draw.io 图纸。')
        with self.lock:
            current = self.canvas(thread_id)
            if expected and current['version'] != expected:
                raise Conflict('图纸已在另一处修改，请先载入外部修改。')
            path = pathlib.Path(new_path).resolve() if new_path else self.drawing_path(thread_id)
            if path.suffix.lower() not in ('.drawio', '.xml'):
                raise ValueError('请选择 .drawio 文件。')
            if not new_path:
                path.parent.mkdir(parents=True, exist_ok=True)
            if not path.parent.is_dir():
                raise ValueError('保存文件夹不存在。')
            temporary = path.with_name(path.name + '.workbench.tmp')
            temporary.write_text(xml, encoding='utf-8')
            os.replace(temporary, path)
            if new_path:
                self.settings['bindings'][thread_id] = str(path)
                self.persist()
            return self.canvas(thread_id)

    def open_canvas(self, thread_id, path):
        drawing = pathlib.Path(path).resolve()
        xml = drawing.read_text(encoding='utf-8-sig')
        if ET.fromstring(xml).tag not in ('mxfile', 'mxGraphModel'):
            raise ValueError('文件不是有效的 draw.io 图纸。')
        with self.lock:
            self.settings['bindings'][thread_id] = str(drawing)
            self.persist()
        return self.canvas(thread_id)

    def send(self, thread_id, text, attach, selection):
        text = text.strip()
        if not text:
            raise ValueError('请输入消息。')
        message = text
        if attach:
            canvas = self.canvas(thread_id)
            path = pathlib.Path(canvas['path'])
            if not path.exists():
                self.save_canvas(thread_id, canvas['xml'], canvas['version'])
            message += ('\n\n<drawio_workbench_context>\n当前可编辑图纸：' + str(path) +
                '\n画布已保存，可能包含用户刚刚手动修改的内容。修改前请读取最新版文件；'
                '需要改图时直接修改此 .drawio 文件，保留无关内容与已有 cell id，工作台会同步刷新。'
                '不要通过浏览器或另一个应用创建替代图纸。不要只在回答中打印 XML。'
                '\n当前选区：' + (selection or '未选中图形') + '\n</drawio_workbench_context>')
        request_id = str(uuid.uuid4())
        queued = self.rpc('thread/queue/add', {'threadId': thread_id,
            'clientUserMessageId': request_id, 'input': [{'type': 'text', 'text': message}]})
        self.ensure_running(thread_id)
        self.pulse(thread_id)
        return queued

    def ensure_running(self, thread_id):
        with self.lock:
            job = self.jobs.get(thread_id)
            if job and job.get('worker'):
                return
            self.live.pop(thread_id, None)
            self.jobs[thread_id] = {'status': 'starting', 'worker': True}
        threading.Thread(target=self._run_queue, args=(thread_id,), daemon=True).start()

    def _run_queue(self, thread_id):
        worker = None
        try:
            worker = CodexRPC(STATE / ('writer-' + thread_id), lean=True)
            try:
                worker.call('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
            except RuntimeError as error:
                if 'active writer' in str(error):
                    # The Desktop owns this conversation. Its watcher consumes the same queue.
                    with self.lock:
                        self.jobs[thread_id] = {'status': 'desktop', 'worker': False}
                    self.pulse(thread_id)
                    return
                raise
            with self.lock:
                self.jobs[thread_id] = {'status': 'running', 'worker': worker}
            self.pulse(thread_id)
            # Resuming can automatically consume the queue before queue/list returns.
            # Keep the writer alive until both the active turn and the queue are empty.
            offset = 0
            deadline = time.monotonic() + 600
            quiet_checks = 0
            while time.monotonic() < deadline:
                events = worker.events[offset:]
                offset += len(events)
                for event in events:
                    method = event.get('method')
                    params = event.get('params', {})
                    if method == 'turn/started':
                        with self.lock:
                            self.jobs[thread_id]['turnId'] = params['turn']['id']
                    if method == 'turn/completed':
                        with self.lock:
                            self.jobs[thread_id].pop('turnId', None)
                    if method == 'item/agentMessage/delta':
                        item_id = params.get('itemId', '')
                        with self.lock:
                            live = self.live.setdefault(thread_id, {})
                            live[item_id] = live.get(item_id, '') + params.get('delta', '')
                    if method == 'item/completed':
                        item = params.get('item', {})
                        if item.get('type') == 'agentMessage' and item.get('text'):
                            with self.lock:
                                self.live.setdefault(thread_id, {})[item['id']] = item['text']
                    if method == 'serverRequest/resolved':
                        with self.lock:
                            self.pending.pop(f'{thread_id}:{params.get("requestId")}', None)
                    if 'id' in event:
                        if method not in SUPPORTED_METHODS:
                            worker.reject(event['id'], 'This local client does not support: ' + str(method))
                            continue
                        with self.lock:
                            self.pending[f'{thread_id}:{event["id"]}'] = {
                                'threadId': thread_id, 'requestId': event['id'],
                                'method': method, 'params': params}
                if events:
                    self.pulse(thread_id)
                status = worker.call('thread/read', {'threadId': thread_id})['thread']['status']['type']
                queued = worker.call('thread/queue/list', {'threadId': thread_id})['data']
                if status == 'active':
                    quiet_checks = 0
                elif queued:
                    quiet_checks = 0
                    # The owner also starts queued work automatically. A racing start is harmless.
                    try:
                        worker.call('thread/queue/start', {'threadId': thread_id})
                    except RuntimeError as error:
                        if 'active' not in str(error) and 'queued' not in str(error):
                            raise
                else:
                    quiet_checks += 1
                    if quiet_checks >= 3:
                        break
                time.sleep(0.3)
            else:
                raise TimeoutError('对话等待时间过长，可重新打开会话查看状态。')
            with self.lock:
                self.jobs[thread_id] = {'status': 'idle', 'worker': False}
            self.pulse(thread_id)
        except Exception as error:
            with self.lock:
                self.jobs[thread_id] = {'status': 'error', 'error': str(error), 'worker': False}
            print('Conversation worker:', str(error), flush=True)
            self.pulse(thread_id)
        finally:
            if worker:
                worker.close()
            with self.lock:
                for key in [key for key,value in self.pending.items() if value['threadId'] == thread_id]:
                    self.pending.pop(key, None)
            self.pulse(thread_id)

    def approve(self, thread_id, request_id, accept, answers=None, content=None):
        key = f'{thread_id}:{request_id}'
        with self.lock:
            pending = self.pending.get(key)
            worker = self.jobs.get(thread_id, {}).get('worker')
            if pending is None or not isinstance(worker, CodexRPC):
                raise Conflict('该请求已结束或已在其他窗口处理。')
        result = make_response(pending, accept, answers, content)
        worker.answer(request_id, result)
        with self.lock:
            self.pending.pop(key, None)
        self.pulse(thread_id)

    def interrupt(self, thread_id):
        with self.lock:
            job = self.jobs.get(thread_id, {})
            worker, turn_id = job.get('worker'), job.get('turnId')
        if not isinstance(worker, CodexRPC) or not turn_id:
            raise ValueError('这条会话当前由 Codex Desktop 执行，请在 Desktop 中停止。')
        result = worker.call('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
        self.pulse(thread_id)
        return result


APP = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def handle_one_request(self):
        try:
            return super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, *_):
        pass

    def response(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        origin = self.headers.get('Origin')
        if origin and origin != f'http://127.0.0.1:{PORT}':
            raise PermissionError('只允许工作台自身连接。')
        if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN):
            raise PermissionError('本地连接已失效，请重新打开工作台。')

    def event_stream(self, thread_id):
        uuid.UUID(thread_id)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.connection.settimeout(20)
        last_revision = -1
        heartbeat = time.monotonic()
        try:
            while True:
                state = APP.event_state(thread_id)
                if state['revision'] != last_revision:
                    data = json.dumps(state, ensure_ascii=False)
                    self.wfile.write(('event: state\ndata: ' + data + '\n\n').encode('utf-8'))
                    self.wfile.flush()
                    last_revision = state['revision']
                    heartbeat = time.monotonic()
                elif time.monotonic() - heartbeat >= 5:
                    self.wfile.write(b': connected\n\n')
                    self.wfile.flush()
                    heartbeat = time.monotonic()
                with APP.changes:
                    APP.changes.wait(timeout=.25)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError):
            return

    def do_GET(self):
        route = urllib.parse.urlparse(self.path)
        if route.path == '/health':
            self.response(200, {'service': 'drawio-codex-workbench', 'ready': APP is not None, 'projectRoot': str(ROOT), 'apiVersion': 2})
            return
        if route.path.startswith('/api/'):
            try:
                self.authorized()
                if APP is None:
                    self.response(503, {'error': '正在连接 Codex…'})
                    return
                params = urllib.parse.parse_qs(route.query)
                get = lambda name: params.get(name, [None])[0]
                if route.path == '/api/events':
                    self.event_stream(get('threadId'))
                elif route.path == '/api/capabilities':
                    self.response(200, {'workbench': {'stream': True, 'approvals': True, 'interrupt': True},
                        'desktop': {'sharedHistory': True, 'sharedQueue': True, 'externalStream': False, 'externalApprovals': False},
                        'message': '当前 Desktop 使用独立 stdio 连接；侧栏通过官方历史和队列接口协作。'})
                elif route.path == '/api/settings':
                    self.response(200, APP.settings)
                elif route.path == '/api/threads':
                    self.response(200, APP.thread_list(get('cursor')))
                elif route.path == '/api/snapshot':
                    self.response(200, APP.snapshot(get('threadId'), get('cursor')))
                elif route.path == '/api/canvas':
                    self.response(200, APP.canvas(get('threadId')))
                else:
                    self.response(404, {'error': 'Unknown API endpoint'})
            except Exception as error:
                self.response(403 if isinstance(error, PermissionError) else 500, {'error': str(error)})
            return
        if route.path.startswith('/drawio/'):
            base, relative = DRAWIO, urllib.parse.unquote(route.path[len('/drawio/'):])
        else:
            base = ROOT / 'ui'
            relative = urllib.parse.unquote(route.path.lstrip('/')) or 'index.html'
        file = (base / relative).resolve()
        if not file.is_relative_to(base.resolve()) or not file.is_file():
            self.response(404, {'error': 'File not found'})
            return
        data = file.read_bytes()
        mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') or mime in ('application/javascript', 'application/json') else ''))
        self.send_header('Content-Length', str(len(data)))
        if not route.path.startswith('/drawio/'):
            self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            self.authorized()
            length = int(self.headers.get('Content-Length', '0'))
            if length > 20*1024*1024:
                raise ValueError('内容过大。')
            body = json.loads(self.rfile.read(length))
            thread_id = body.get('threadId')
            if self.path == '/api/select':
                result = APP.select(thread_id)
            elif self.path == '/api/new':
                result = APP.create_thread(body['cwd'])
            elif self.path == '/api/send':
                result = APP.send(thread_id, body['text'], body.get('attach', True), body.get('selection', ''))
            elif self.path == '/api/canvas':
                result = APP.save_canvas(thread_id, body['xml'], body.get('version'), body.get('path'))
            elif self.path == '/api/open':
                result = APP.open_canvas(thread_id, body['path'])
            elif self.path == '/api/approval':
                APP.approve(thread_id, body['requestId'], body.get('accept'), body.get('answers'), body.get('content'))
                result = {'ok': True}
            elif self.path == '/api/interrupt':
                result = APP.interrupt(thread_id)
            elif self.path == '/api/project':
                path = pathlib.Path(body['cwd']).resolve()
                if not path.is_dir():
                    raise ValueError('文件夹不存在。')
                with APP.lock:
                    APP.settings['project'] = str(path)
                    APP.persist()
                result = {'cwd': str(path)}
            elif self.path == '/api/shutdown':
                with APP.lock:
                    if any(job.get('worker') for job in APP.jobs.values()):
                        raise Conflict('工作台仍在执行对话，请等当前任务完成后再重启开发服务。')
                self.response(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self.response(404, {'error': 'Unknown API endpoint'})
                return
            self.response(200, result)
        except Conflict as error:
            self.response(409, {'error': str(error)})
        except Exception as error:
            self.response(403 if isinstance(error, PermissionError) else 500, {'error': str(error)})


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    def initialize():
        global APP
        APP = Workbench()
        print('Codex workbench ready.', flush=True)
    threading.Thread(target=initialize, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if APP:
            APP.reader.close()
