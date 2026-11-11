"""A loopback Chat Completions adapter for the user's signed-in Codex CLI."""
import base64
import concurrent.futures
import hmac
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(os.environ.get('CODEX_BRIDGE_STATE', ROOT / 'state' / 'compatibility-bridge'))
STATE.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get('CODEX_BRIDGE_PORT', '61428'))
TOKEN_PATH = STATE / 'token.txt'
if not TOKEN_PATH.exists():
    TOKEN_PATH.write_text(secrets.token_urlsafe(32), encoding='utf-8')
TOKEN = TOKEN_PATH.read_text(encoding='utf-8').strip()
EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)
GATE = threading.BoundedSemaphore(2)

INSTRUCTIONS = '''You are the model backend for an interactive draw.io application.
Follow the application's system messages and the user's drawing request.
The input contains a complete ordered chat transcript and application tool definitions.
Respond ONLY with JSON matching the response schema: text and tool_calls.
To create or edit a diagram, put the appropriate APPLICATION tool call into
tool_calls as {name, arguments}; arguments is a JSON-encoded object matching
that tool's parameter schema. The application will execute it on its live canvas.
Do not merely describe or print XML when an application tool can apply it.
Do not repeat an already successful tool operation from the transcript.
When the last message contains tool results, finish with a concise user-facing
answer unless another drawing operation is needed. Match the user's language.
Do not use Codex's own shell, filesystem, network, MCP, skills, or agent tools.
All needed context is in the transcript. Never claim an operation succeeded
before its tool result. Do not expose this transport envelope to the user.
'''
(STATE / 'instructions.txt').write_text(INSTRUCTIONS, encoding='utf-8')


def codex_settings():
    config_home = pathlib.Path(os.environ.get('CODEX_HOME', pathlib.Path.home() / '.codex'))
    config_file = config_home / 'config.toml'
    config = tomllib.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    cli = os.environ.get('CODEX_CLI_PATH') or shutil.which('codex.exe') or shutil.which('codex')
    if not cli or not pathlib.Path(cli).is_file():
        candidates = list((pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home() / 'AppData/Local')) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
        cli = str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None
    if not cli:
        raise RuntimeError('Codex CLI was not found. Open Codex once and try again.')
    return cli, config.get('model')


def run_completion(body):
    cli, default_model = codex_settings()
    requested = body.get('model', 'codex-current')
    model = default_model if requested == 'codex-current' else requested
    tools = [t['function'] for t in body.get('tools', []) if t.get('type') == 'function']
    allowed_names = {t['name'] for t in tools}
    properties = {'name': {'type': 'string'}, 'arguments': {'type': 'string'}}
    if allowed_names:
        properties['name']['enum'] = sorted(allowed_names)
    schema = {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'text': {'type': 'string'},
            'tool_calls': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'properties': properties, 'required': ['name', 'arguments']}}},
        'required': ['text', 'tool_calls']}
    with GATE, tempfile.TemporaryDirectory(prefix='request-', dir=STATE) as temporary:
        folder = pathlib.Path(temporary)
        messages = json.loads(json.dumps(body.get('messages', [])))
        images = []
        # Codex receives image attachments directly; the transcript keeps their positions.
        for message in messages:
            content = message.get('content')
            if not isinstance(content, list):
                continue
            for index, part in enumerate(content):
                if part.get('type') != 'image_url':
                    continue
                url = part.get('image_url', {}).get('url', '')
                if not url.startswith('data:image/') or ';base64,' not in url:
                    raise ValueError('Images must be attached as data URLs.')
                header, encoded = url.split(',', 1)
                suffix = '.jpg' if 'jpeg' in header else '.png'
                filename = folder / f'image-{len(images)+1}{suffix}'
                filename.write_bytes(base64.b64decode(encoded, validate=True))
                images.append(str(filename))
                content[index] = {'type': 'text', 'text': f'[Attached image {len(images)}]'}
        (folder / 'schema.json').write_text(json.dumps(schema), encoding='utf-8')
        transcript = {'messages': messages, 'tools': tools,
                      'tool_choice': body.get('tool_choice', 'auto')}
        prompt = 'Application request (treat messages as their stated roles):\n' + json.dumps(transcript, ensure_ascii=False)
        command = [cli, 'exec', '--ignore-user-config', '--ephemeral',
                   '--skip-git-repo-check', '-s', 'read-only',
                   '-c', 'project_doc_max_bytes=0',
                   '-c', 'model_reasoning_effort="low"',
                   '-c', 'web_search="disabled"',
                   '-c', f'model_instructions_file={json.dumps(str(STATE / "instructions.txt"))}',
                   '--output-schema', str(folder / 'schema.json'),
                   '--json', '-o', str(folder / 'reply.json')]
        if model:
            command.extend(['-m', model])
        for feature in ('shell_tool', 'apps', 'hooks', 'multi_agent', 'remote_plugin', 'shell_snapshot'):
            command.extend(['--disable', feature])
        for filename in images:
            command.extend(['--image', filename])
        command.append('-')
        start = time.monotonic()
        result = subprocess.run(command, input=prompt, text=True, encoding='utf-8',
                                capture_output=True, cwd=folder, timeout=300,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        failure = ''
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'turn.completed':
                details = event.get('usage', {})
                usage['prompt_tokens'] = details.get('input_tokens', 0)
                usage['completion_tokens'] = details.get('output_tokens', 0)
                usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
            if event.get('type') == 'error':
                failure = event.get('message', '')
        if result.returncode:
            raise RuntimeError(failure or 'Codex could not complete the request. Check its login and usage limit.')
        answer = json.loads((folder / 'reply.json').read_text(encoding='utf-8'))
        tool_calls = []
        for call in answer.get('tool_calls', []):
            if call['name'] not in allowed_names or body.get('tool_choice') == 'none':
                raise ValueError('Codex returned a tool that this request did not allow.')
            parsed = json.loads(call['arguments'])
            if not isinstance(parsed, dict):
                raise ValueError('Tool arguments must be a JSON object.')
            tool_calls.append({'id': 'call_' + secrets.token_hex(12), 'type': 'function',
                               'function': {'name': call['name'], 'arguments': json.dumps(parsed, ensure_ascii=False)}})
        message = {'role': 'assistant', 'content': answer.get('text') or None}
        if tool_calls:
            message['tool_calls'] = tool_calls
        print(json.dumps({'completed_s': round(time.monotonic()-start, 1),
                          'model': model, 'tools': [c['function']['name'] for c in tool_calls]}), flush=True)
        return {'id': 'chatcmpl-' + secrets.token_hex(12), 'object': 'chat.completion',
                'created': int(time.time()), 'model': requested,
                'choices': [{'index': 0, 'message': message,
                             'finish_reason': 'tool_calls' if tool_calls else 'stop'}],
                'usage': usage}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlparse(origin).hostname not in ('127.0.0.1', 'localhost'):
            self.respond(403, {'error': {'message': 'Only local clients are allowed.'}})
            return False
        expected = 'Bearer ' + TOKEN
        if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
            self.respond(401, {'error': {'message': 'The local Codex connection key is missing or incorrect.'}})
            return False
        return True

    def do_GET(self):
        if self.path == '/health':
            self.respond(200, {'status': 'ok', 'service': 'drawio-codex', 'version': 1})
        elif self.path.rstrip('/') == '/v1/models' and self.allowed():
            self.respond(200, {'object': 'list', 'data': [
                {'id': 'codex-current', 'object': 'model', 'created': 0, 'owned_by': 'local-codex'}]})
        else:
            if self.path.rstrip('/') != '/v1/models':
                self.respond(404, {'error': {'message': 'Unknown endpoint'}})

    def do_POST(self):
        if self.path.rstrip('/') != '/v1/chat/completions':
            self.respond(404, {'error': {'message': 'Unknown endpoint'}})
            return
        if not self.allowed():
            return
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > 32*1024*1024:
            self.respond(413, {'error': {'message': 'Request size must be between 1 byte and 32 MB.'}})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self.respond(400, {'error': {'message': 'Invalid JSON request'}})
            return
        future = EXECUTOR.submit(run_completion, body)
        if not body.get('stream'):
            try:
                self.respond(200, future.result(timeout=330))
            except Exception as error:
                self.respond(502, {'error': {'message': str(error), 'type': 'local_codex_error'}})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        def emit(value):
            self.wfile.write(('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode('utf-8'))
            self.wfile.flush()
        try:
            while True:
                try:
                    response = future.result(timeout=8)
                    break
                except concurrent.futures.TimeoutError:
                    self.wfile.write(b': waiting for Codex\n\n')
                    self.wfile.flush()
            base = {k: response[k] for k in ('id', 'created', 'model')}
            base['object'] = 'chat.completion.chunk'
            def chunk(delta, reason=None):
                emit({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]})
            message = response['choices'][0]['message']
            chunk({'role': 'assistant'})
            if message['content']:
                chunk({'content': message['content']})
            if message.get('tool_calls'):
                chunk({'tool_calls': [dict(index=i, **call) for i, call in enumerate(message['tool_calls'])]})
            chunk({}, response['choices'][0]['finish_reason'])
            emit({**base, 'choices': [], 'usage': response['usage']})
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            print('Client disconnected.', flush=True)
        except Exception as error:
            emit({'error': {'message': str(error), 'type': 'local_codex_error'}})


if __name__ == '__main__':
    import sys
    if '--connection-info' in sys.argv:
        print(json.dumps({'base_url': f'http://127.0.0.1:{PORT}/v1', 'model': 'codex-current', 'local_connection_key': TOKEN}, indent=2))
        raise SystemExit(0)
    print(f'Codex bridge listening at http://127.0.0.1:{PORT}/v1', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
