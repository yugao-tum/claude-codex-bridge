"""Local chat and approved laptop tools for an independent remote Claude session."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import msvcrt
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import load_config
from local_tools import TOOLS, execute, validate_workspace

BASE = Path(__file__).resolve().parent
DATA = BASE / 'state'
PROJECT = DEFAULT_WORKSPACE = None
CONFIG = None
TERMINAL = {'idle', 'completed', 'failed', 'stopped', 'cancelled'}


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def remote_argv(*args):
    remote = CONFIG['remote_root'] + '/remote_runner.py'
    return CONFIG['ssh_argv'] + [shlex.join(CONFIG['remote_prefix'] + [remote, *args])]


def configure(value):
    global CONFIG, PROJECT, DEFAULT_WORKSPACE
    CONFIG = value
    PROJECT = value['workspace_root']
    DEFAULT_WORKSPACE = value['default_workspace']


def valid_workspace(value):
    return validate_workspace(value, PROJECT)


class Chat:
    def __init__(self):
        if CONFIG is None:
            raise RuntimeError('Load config.json before starting the chat.')
        DATA.mkdir(parents=True, exist_ok=True)
        DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.input_lock = threading.Lock()
        self.approval_event = threading.Event()
        self.approval_answer = False
        self.cancel_event = threading.Event()
        self.proc = None
        self.turn_running = False
        self.finished_status = None
        self.stopping = False
        self.token = secrets.token_urlsafe(32)
        self.state_file = DATA / 'state.json'
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text(encoding='utf-8'))
            if self.state['status'] not in TERMINAL or self.turn_running:
                self.state['status'] = 'unknown'
                self.state['error'] = '上次连接中断。请先恢复原请求状态，不会自动重发。'
            self.state['approval'] = None
        else:
            self.state = dict(status='idle', model='连接后显示型号标签',
                              session_id=str(uuid.uuid4()), workspace=str(DEFAULT_WORKSPACE),
                              messages=[], activity=[], approval=None, error=None,
                              request_id=None, session_started=False)
        self.persist()

    def persist(self):
        save_json(self.state_file, self.state)

    def event(self, event):
        entry = {'time': now(), 'request_id': self.state.get('request_id'), **event}
        with (DATA / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            f.flush()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def workspace(self, path):
        new = valid_workspace(path)
        with self.lock:
            if self.state['status'] not in TERMINAL or self.turn_running:
                raise ValueError('当前请求尚未结束，请先收取或停止。')
            self.state['workspace'] = str(new)
            self.persist()

    def send(self, text):
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 80000:
            raise ValueError('请输入消息；单条消息不能超过 80KB。')
        with self.lock:
            if self.state['status'] not in TERMINAL:
                raise ValueError('已有请求进行中或状态未确认，不重复提交。')
            valid_workspace(self.state['workspace'])
            self.stopping = False
            self.cancel_event.clear()
            self.turn_running = True
            self.finished_status = None
            self.state.update(status='connecting', error=None, approval=None,
                              request_id=str(uuid.uuid4()))
            self.state['messages'].append({'role': 'user', 'text': text, 'time': now()})
            self.state['messages'].append({'role': 'assistant', 'text': '', 'time': now()})
            self.state['activity'] = []
            payload = dict(request_id=self.state['request_id'], session_id=self.state['session_id'],
                           resume=self.state['session_started'], prompt=text, tools=TOOLS,
                           workspace_label=self.state['workspace'], timeout_seconds=CONFIG['timeout_seconds'])
            save_json(DATA / (self.state['request_id'] + '.request.json'), payload)
            self.event({'type': 'user_message', 'text': text})
            self.persist()
        threading.Thread(target=self.turn, args=(payload,), daemon=True).start()

    def write_remote(self, message):
        with self.input_lock:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
                self.proc.stdin.flush()
            else:
                raise RuntimeError('远端连接已结束。')

    def approve(self, approval_id, allow):
        if not isinstance(allow, bool):
            raise ValueError('审批结果无效。')
        with self.lock:
            current = self.state['approval']
            if not current or current['id'] != approval_id:
                raise ValueError('这项审批已结束或已更新。')
            self.approval_answer = allow
            self.event({'type': 'user_approval', 'id': approval_id, 'allow': allow})
            self.state['approval'] = None
            self.state['status'] = 'running'
            self.persist()
            self.approval_event.set()

    def stop(self):
        with self.lock:
            if self.state['status'] in TERMINAL:
                return
            if not self.proc and not self.turn_running:
                raise ValueError('当前连接状态未知，请先恢复状态。')
            self.stopping = True
            self.cancel_event.set()
            self.approval_answer = False
            self.state.update(status='stopping', approval=None)
            self.approval_event.set()
            self.persist()
        if self.proc and self.proc.poll() is None:
            self.write_remote({'type': 'cancel'})

    def tool(self, event):
        name, args = event['name'], event.get('arguments', {})
        call_id = event['call_id']
        with self.lock:
            workspace = valid_workspace(self.state['workspace'])
            summary = args.get('command') if name == 'run_command' else args.get('path', '.')
            activity = dict(id=call_id, name=name, status='running', summary=str(summary or ''))
            self.state['activity'].append(activity)
            self.event({'type': 'tool_requested', 'name': name, 'id': call_id, 'summary': summary})
            self.persist()
        with self.lock:
            allowed = not self.stopping and not self.cancel_event.is_set()
            needs_approval = name == 'run_command' and allowed
            if needs_approval:
                self.approval_event.clear()
                self.approval_answer = False
                self.state.update(status='awaiting_approval', approval=dict(
                    id=call_id, command=args.get('command', ''),
                    cwd=str(workspace / args.get('cwd', '.')), timeout_seconds=args.get('timeout_seconds', 60)))
                self.persist()
        if needs_approval:
            answered = self.approval_event.wait(600)
            allowed = answered and self.approval_answer and not self.stopping
            with self.lock:
                if self.state.get('approval') and self.state['approval']['id'] == call_id:
                    self.state['approval'] = None
                if not self.stopping:
                    self.state['status'] = 'running'
        if allowed and not self.cancel_event.is_set():
            try:
                result = execute(name, args, workspace, base_root=PROJECT, cancel_event=self.cancel_event)
            except Exception as exc:
                result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
        else:
            result = {'content': [{'type': 'text', 'text': '用户未批准、审批到期或任务已停止。不要换方式执行。'}], 'isError': True}
        with self.lock:
            activity['status'] = 'failed' if result.get('isError') else 'completed'
            self.event({'type': 'tool_result', 'name': name, 'id': call_id, 'result': result})
            self.persist()
        self.write_remote({'type': 'tool_result', 'call_id': call_id, 'result': result})

    def consume(self, event):
        kind = event.get('type')
        if kind == 'tool_call':
            self.tool(event)
            return
        with self.lock:
            if kind == 'ready':
                self.state.update(status='running', model=event.get('model', self.state['model']))
                if isinstance(event.get('resume_available'), bool):
                    self.state['session_started'] = event['resume_available']
            elif kind == 'text_delta':
                self.state['messages'][-1]['text'] += event.get('text', '')
            elif kind == 'done':
                if not self.state['messages'][-1]['text'] and event.get('result'):
                    self.state['messages'][-1]['text'] = event['result']
                self.finished_status = ('stopped' if self.stopping else 'completed' if event.get('ok') else 'failed')
                self.state.update(status='finishing', error=event.get('error'), approval=None)
                if isinstance(event.get('resume_available'), bool):
                    self.state['session_started'] = event['resume_available']
                elif event.get('ok') is True:
                    self.state['session_started'] = True
                if event.get('model'):
                    self.state['model'] = event['model']
            self.event(event)
            self.persist()

    def turn(self, payload):
        done = False
        errors = []
        proc = None
        try:
            if self.cancel_event.is_set():
                with self.lock:
                    self.finished_status = 'stopped'
                    done = True
                return
            proc = subprocess.Popen(remote_argv('start'), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                    bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
            with self.lock:
                self.proc = proc
            def drain():
                for line in proc.stderr:
                    errors.append(line.rstrip())
                    del errors[:-8]
            threading.Thread(target=drain, daemon=True).start()
            def connection_ended():
                proc.wait()
                with self.lock:
                    if self.proc is proc and self.state.get('request_id') == payload['request_id']:
                        self.cancel_event.set()
                        self.approval_event.set()
            threading.Thread(target=connection_ended, daemon=True).start()
            self.write_remote(payload)
            if self.cancel_event.is_set():
                self.write_remote({'type': 'cancel'})
            for line in proc.stdout:
                if not line.strip():
                    continue
                event = json.loads(line)
                self.consume(event)
                if event.get('type') == 'done':
                    done = True
            proc.wait(timeout=10)
            if not done:
                raise RuntimeError('连接结束但未收到终态；请恢复原请求状态。' +
                                   ('\n' + '\n'.join(errors)[-1000:] if errors else ''))
        except Exception as exc:
            with self.lock:
                if not done:
                    self.state.update(status='unknown', error=str(exc), approval=None)
                    self.event({'type': 'transport_error', 'error': str(exc)})
                    self.persist()
        finally:
            if proc:
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
                if proc.poll() is None:
                    proc.terminate()
            with self.lock:
                if self.proc is proc:
                    self.proc = None
                if self.state.get('request_id') == payload['request_id']:
                    self.turn_running = False
                    if done and self.finished_status:
                        self.state['status'] = self.finished_status
                    self.persist()

    def recover(self):
        with self.lock:
            if self.turn_running or self.state['status'] == 'recovering' or (self.proc and self.proc.poll() is None):
                raise ValueError('连接仍在运行，当前状态已实时显示。')
            request_id = self.state.get('request_id')
            if not request_id:
                return
            uuid.UUID(request_id)
            self.state.update(status='recovering', error=None, approval=None)
            self.persist()
        def run():
            try:
                p = subprocess.run(remote_argv('status', request_id), capture_output=True, text=True,
                                   encoding='utf-8', timeout=55, creationflags=subprocess.CREATE_NO_WINDOW)
                if p.returncode:
                    raise RuntimeError('状态连接失败：' + p.stderr[-800:])
                result = json.loads(p.stdout)
                receipt = result.get('receipt')
                events = result.get('events', [])
                text = ''.join(x.get('text', '') for x in events if x.get('type') == 'text_delta')
                with self.lock:
                    if self.state.get('request_id') != request_id:
                        return
                    if text and self.state['messages'] and self.state['messages'][-1]['role'] == 'assistant':
                        self.state['messages'][-1]['text'] = text
                    if receipt and receipt.get('type') == 'done':
                        self.state.update(status='completed' if receipt.get('ok') else 'failed',
                                          error=receipt.get('error'))
                        if isinstance(receipt.get('resume_available'), bool):
                            self.state['session_started'] = receipt['resume_available']
                        elif receipt.get('ok') is True:
                            self.state['session_started'] = True
                    else:
                        self.state.update(status='unknown', error='原请求尚无确认终态；没有重新发送。')
                    self.event({'type': 'recovery', 'state': result.get('state')})
                    self.persist()
            except Exception as exc:
                with self.lock:
                    self.state.update(status='unknown', error=str(exc))
                    self.persist()
        threading.Thread(target=run, daemon=True).start()


def serve(port):
    DATA.mkdir(parents=True, exist_ok=True)
    instance = (DATA / 'server.lock').open('a+b')
    instance.seek(0)
    if instance.read(1) == b'':
        instance.write(b'0')
        instance.flush()
    instance.seek(0)
    msvcrt.locking(instance.fileno(), msvcrt.LK_NBLCK, 1)
    chat = Chat()
    origin = f'http://127.0.0.1:{port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def json_response(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self, token=True):
            return (self.headers.get('Host') == f'127.0.0.1:{port}'
                    and self.headers.get('Origin', origin) == origin
                    and (not token or secrets.compare_digest(self.headers.get('X-Direct-Token', ''), chat.token)))

        def do_GET(self):
            if not self.authorized(token=self.path != '/'):
                return self.json_response({'ok': False, 'error': '请求来源不符。'}, 403)
            if self.path == '/':
                body = (BASE / 'index.html').read_text(encoding='utf-8').replace('__CSRF_TOKEN__', chat.token).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == '/api/state':
                self.json_response(chat.snapshot())
            else:
                self.json_response({'error': 'Not found'}, 404)

        def do_POST(self):
            if not self.authorized():
                return self.json_response({'ok': False, 'error': '请求来源或凭证不符。'}, 403)
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 262144 or 'application/json' not in self.headers.get('Content-Type', ''):
                    raise ValueError('请求格式或大小不符。')
                data = json.loads(self.rfile.read(n))
                if not isinstance(data, dict):
                    raise ValueError('请求必须是 JSON 对象。')
                if self.path == '/api/send':
                    chat.send(data['text'])
                elif self.path == '/api/workspace':
                    chat.workspace(data['path'])
                elif self.path == '/api/approval':
                    chat.approve(data['id'], data['allow'])
                elif self.path == '/api/stop':
                    chat.stop()
                elif self.path == '/api/recover':
                    chat.recover()
                else:
                    return self.json_response({'ok': False, 'error': 'Not found'}, 404)
                self.json_response({'ok': True})
            except (KeyError, ValueError, RuntimeError, OSError) as exc:
                self.json_response({'ok': False, 'error': str(exc)}, 400)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    save_json(DATA / 'server.json', {'pid': os.getpid(), 'started_at': now(), 'url': origin,
                                   'program': str(BASE / 'app.py')})
    print(origin, flush=True)
    server.serve_forever()


if __name__ == '__main__':
    configure(load_config())
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=CONFIG['port'])
    serve(parser.parse_args().port)
