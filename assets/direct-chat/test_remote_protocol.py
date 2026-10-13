"""Offline protocol checks; no SSH, credentials or model invocation."""
import io
import json
import contextlib
import os
from pathlib import Path
import queue
import shutil
import socket
import threading
import unittest
from unittest import mock
import uuid

import mcp_stdio
import remote_runner as runner


class TestPath(type(Path())):
    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        # Windows sandbox ACLs cannot exercise POSIX 0700; production runs on Linux.
        return super().mkdir(mode=mode if os.name == 'posix' else 0o777, parents=parents, exist_ok=exist_ok)


@contextlib.contextmanager
def temporary_root():
    parent = TestPath(__file__).resolve().parent
    # The transcript test includes an encoded copy of this path, so keep it short
    # enough for Windows' legacy 260-character path limit in nested release dirs.
    path = parent / ('protocol-test-' + uuid.uuid4().hex[:8])
    path.mkdir()
    try:
        yield path
    finally:
        assert path.resolve().parent == parent and path.name.startswith('protocol-test-')
        shutil.rmtree(path)


def request():
    return dict(request_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()), resume=False,
                prompt='Write one test file', workspace_label='offline-test', timeout_seconds=900,
                tools=[dict(name='read_file', inputSchema={'type': 'object', 'properties': {}})])


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        mock.patch.multiple(runner, BINARY=Path('/test/claude'), PROJECTS=Path('/test/projects'),
                            REMOTE_HOME='/test/home', MODEL='test-model-id', EFFORT='high',
                            MAX_TIMEOUT=900).start()
        self.addCleanup(mock.patch.stopall)

    def test_request_validation_and_tool_boundary(self):
        value = runner.validate_request(request())
        self.assertEqual(value['timeout_seconds'], 900)
        for field, bad in [('request_id', '../escape'), ('timeout_seconds', True), ('timeout_seconds', 901), ('resume', 1)]:
            changed = dict(value, **{field: bad})
            with self.assertRaises((ValueError, TypeError, AttributeError)):
                runner.validate_request(changed)
        value['tools'][0]['name'] = 'read_environment'
        with self.assertRaises(ValueError):
            runner.validate_request(value)

    def test_duplicate_is_read_only_and_changed_request_rejected(self):
        with temporary_root() as root:
            value = request()
            directory, fresh = runner.register(value, root)
            self.assertTrue(fresh)
            before = (directory / 'request.json').read_bytes()
            self.assertFalse(runner.register(value, root)[1])
            with self.assertRaises(ValueError):
                runner.register(dict(value, prompt='different'), root)
            self.assertEqual((directory / 'request.json').read_bytes(), before)
            self.assertEqual(runner.status(value['request_id'], root)['state'], 'queued')

    def test_public_stream_excludes_thinking_tools_and_duplicate_assistant(self):
        filt = runner.PublicText()
        def event(value):
            return {'type': 'stream_event', 'event': value}
        filt.accept(event({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': 'PRIVATE'}}))
        filt.accept(event({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'PRIVATE'}}))
        self.assertEqual(filt.accept(event({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}})), '')
        self.assertEqual(filt.accept(event({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'hello'}})), 'hello')
        self.assertEqual(filt.accept({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'hello'}]}}), '')
        self.assertEqual(filt.text, 'hello')
        self.assertNotIn('PRIVATE', json.dumps(filt.__dict__))

    def test_final_result_fallback_and_args(self):
        filt = runner.PublicText()
        self.assertEqual(filt.accept({'type': 'result', 'result': 'fallback', 'is_error': False}), 'fallback')
        value = request()
        args = runner.build_args(value, 'test-model-id', Path('mcp.json'))
        self.assertEqual(args[0], str(Path('/test/claude')))
        self.assertEqual(args[args.index('--effort') + 1], 'high')
        self.assertNotIn('--safe-mode', args)
        self.assertNotIn('--no-session-persistence', args)
        self.assertEqual(args[args.index('--tools') + 1], '')
        self.assertEqual(args[args.index('--allowedTools') + 1], 'mcp__laptop__*')
        self.assertEqual(args[args.index('--system-prompt-snapshot') + 1], 'off')
        self.assertIn('AGENTS.md and CLAUDE.md', args[args.index('--append-system-prompt') + 1])
        self.assertIn('--session-id', args)
        value['resume'] = True
        self.assertIn('--resume', runner.build_args(value, 'test-model-id', Path('mcp.json')))

    def test_mcp_discovery_and_full_tool_result(self):
        tools = request()['tools']
        calls = []
        result = {'content': [{'type': 'text', 'text': 'authorized file'}], 'isError': False}
        def relay(name, arguments):
            calls.append((name, arguments))
            return result
        init = mcp_stdio.handle({'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}}, tools, relay)
        self.assertEqual(init['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(mcp_stdio.handle({'id': 2, 'method': 'tools/list'}, tools, relay)['result']['tools'], tools)
        self.assertIsNone(mcp_stdio.handle({'method': 'notifications/initialized'}, tools, relay))
        response = mcp_stdio.handle({'id': 3, 'method': 'tools/call', 'params': {'name': 'read_file', 'arguments': {'path': 'sample'}}}, tools, relay)
        self.assertEqual(response['result'], result)
        self.assertEqual(calls, [('read_file', {'path': 'sample'})])
        denied = mcp_stdio.handle({'id': 4, 'method': 'tools/call', 'params': {'name': 'shell'}}, tools, relay)
        self.assertIn('error', denied)

    def test_input_result_routing_cancel_and_eof(self):
        with temporary_root() as root:
            value = request()
            directory, _ = runner.register(value, root)
            incoming = {'content': [{'type': 'text', 'text': 'done'}], 'isError': True}
            stream = io.StringIO(json.dumps({'type': 'tool_result', 'call_id': 'abc', 'result': incoming}) + '\n' + json.dumps({'type': 'cancel'}) + '\n')
            task = runner.Runner(value, directory, stream, io.StringIO(), root)
            task.pending['abc'] = queue.Queue(maxsize=1)
            task.read_input()
            self.assertEqual(task.pending['abc'].get_nowait(), incoming)
            self.assertEqual(task.cancel_reason, 'cancelled_by_user')
            task = runner.Runner(value, directory, io.StringIO(''), io.StringIO(), root)
            task.read_input()
            self.assertEqual(task.cancel_reason, 'client_disconnected')

    def test_socket_tool_call_preserves_full_result(self):
        with temporary_root() as root:
            value = request()
            directory, _ = runner.register(value, root)
            result = {'content': [{'type': 'text', 'text': 'file contents'}], 'isError': False, 'structuredContent': {'size': 13}}
            class ClientOutput(io.StringIO):
                def write(self, line):
                    event = json.loads(line)
                    if event.get('type') == 'tool_call':
                        task.pending[event['call_id']].put(result)
                    return super().write(line)
            output = ClientOutput()
            task = runner.Runner(value, directory, io.StringIO(''), output, root)
            server, client = socket.socketpair()
            thread = threading.Thread(target=task.handle_tool, args=(server,), daemon=True)
            thread.start()
            with client:
                client.settimeout(3)
                client.sendall(b'{"name":"read_file","arguments":{"path":"demo.txt"}}\n')
                with client.makefile('r', encoding='utf-8') as handle:
                    self.assertEqual(json.loads(handle.readline()), result)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            event = json.loads(output.getvalue())
            self.assertEqual(event['name'], 'read_file')
            self.assertEqual(event['arguments'], {'path': 'demo.txt'})
            self.assertEqual(task.pending, {})

    def test_failed_first_attempt_can_start_sid_without_resuming_missing_transcript(self):
        with temporary_root() as root, mock.patch.object(runner, 'PROJECTS', root / 'projects'):
            old = request()
            directory, _ = runner.register(old, root)
            runner.save(directory / 'receipt.json', {'state': 'failed', 'resume_available': False})
            marker = root / 'sessions' / old['session_id'] / 'session.json'
            # Legacy markers did not have an attempted/resume_available field.
            runner.save(marker, {'latest_request': old['request_id'], 'session_id': old['session_id']})
            current = dict(old, request_id=str(uuid.uuid4()), resume=True, prompt='New user request')
            with self.assertRaisesRegex(RuntimeError, 'direct_session_missing_cannot_resume'):
                runner.select_resume(current, marker, root)
            current['resume'] = False
            self.assertFalse(runner.select_resume(current, marker, root))
            runner.save(directory / 'receipt.json', {'state': 'completed', 'resume_available': False})
            with self.assertRaisesRegex(RuntimeError, 'direct_session_missing_cannot_resume'):
                runner.select_resume(current, marker, root)
            runner.save(directory / 'receipt.json', {'state': 'failed'})
            with self.assertRaisesRegex(RuntimeError, 'direct_session_missing_cannot_resume'):
                runner.select_resume(current, marker, root)
            runner.save(directory / 'receipt.json', {'state': 'started'})
            with self.assertRaisesRegex(RuntimeError, 'direct_previous_attempt_unresolved'):
                runner.select_resume(current, marker, root)

    def test_completed_history_loss_or_changed_home_never_restarts_session(self):
        for lost_by in ('deleted_transcript', 'changed_home'):
            with self.subTest(lost_by=lost_by), temporary_root() as root, \
                    mock.patch.object(runner, 'PROJECTS', root / 'projects'):
                value = request()
                directory, _ = runner.register(value, root)
                marker = root / 'sessions' / value['session_id'] / 'session.json'
                runner.save(marker, {'latest_request': value['request_id'],
                                     'resume_available_at_start': False})
                transcript = runner.transcript_path(value['session_id'], root)
                transcript.parent.mkdir(parents=True)
                transcript.write_text('{"type":"user"}\n', encoding='utf-8')
                task = runner.Runner(value, directory, io.StringIO(''), io.StringIO(), root)
                task.proc = mock.Mock()
                task.proc.poll.return_value = 0
                task.filter.metadata = {'is_error': False, 'session_id': value['session_id'],
                                        'modelUsage': {task.model: {}}}
                self.assertTrue(task.finish()['ok'])
                before = (directory / 'receipt.json').read_bytes()
                if lost_by == 'deleted_transcript':
                    transcript.unlink()
                else:
                    runner.PROJECTS = root / 'other-home' / '.claude' / 'projects'
                current = dict(value, request_id=str(uuid.uuid4()), resume=True)
                with self.assertRaisesRegex(RuntimeError, 'direct_session_missing_cannot_resume'):
                    runner.select_resume(current, marker, root)
                # A failed recovery must not allow the next local request to reset history.
                with self.assertRaisesRegex(RuntimeError, 'direct_session_missing_cannot_resume'):
                    runner.select_resume(dict(current, resume=False), marker, root)
                self.assertEqual((directory / 'receipt.json').read_bytes(), before)

    def test_real_transcript_is_authoritative_and_available_in_done(self):
        with temporary_root() as root, mock.patch.object(runner, 'PROJECTS', root / 'projects'):
            value = request()
            directory, _ = runner.register(value, root)
            marker = root / 'sessions' / value['session_id'] / 'session.json'
            self.assertFalse(runner.select_resume(value, marker, root))
            path = runner.transcript_path(value['session_id'], root)
            path.parent.mkdir(parents=True)
            path.write_text('{"type":"user"}\n', encoding='utf-8')
            self.assertTrue(runner.resume_available(value['session_id'], root))
            self.assertTrue(runner.select_resume(dict(value, resume=True), marker, root))
            with self.assertRaisesRegex(RuntimeError, 'direct_session_exists_use_resume'):
                runner.select_resume(value, marker, root)
            task = runner.Runner(value, directory, io.StringIO(''), io.StringIO(), root)
            task.proc = mock.Mock()
            task.proc.poll.return_value = 0
            task.filter.metadata = {'is_error': False, 'session_id': value['session_id'], 'modelUsage': {task.model: {}}}
            result = task.finish('client_disconnected')
            self.assertTrue(result['ok'])
            self.assertTrue(result['resume_available'])
            self.assertTrue(result['connection_closed_after_result'])
            self.assertEqual(runner.status(value['request_id'], root)['state'], 'completed')

    def test_null_model_usage_fails_without_crashing_or_false_success(self):
        with temporary_root() as root, mock.patch.object(runner, 'PROJECTS', root / 'projects'):
            value = request()
            directory, _ = runner.register(value, root)
            task = runner.Runner(value, directory, io.StringIO(''), io.StringIO(), root)
            task.proc = mock.Mock()
            task.proc.poll.return_value = 0
            task.filter.metadata = {'is_error': False, 'session_id': value['session_id'], 'modelUsage': None}
            result = task.finish()
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'requested_model_not_reported')
            self.assertEqual(result['reported_models'], [])
            self.assertFalse(result['resume_available'])


if __name__ == '__main__':
    unittest.main()
