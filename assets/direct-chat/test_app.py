"""Offline chat-state tests. All storage is temporary; SSH and tools are mocked."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app


class DeferredThread:
    pending = []

    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.pending.append(self)

    def run(self):
        self.target(*self.args)


class ChatTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=test_root, prefix='.test-app-')
        assert Path(self.temp.name).resolve().is_relative_to(test_root)
        self.addCleanup(self.temp.cleanup)
        project = Path(self.temp.name) / 'project'
        base = project / 'direct-app'
        base.mkdir(parents=True)
        self.addCleanup(patch.stopall)
        patch.multiple(app, PROJECT=project, BASE=base, DATA=base / 'state',
                       DEFAULT_WORKSPACE=project / 'workspace', CONFIG={
                           'ssh_argv': ['never-run-ssh'], 'remote_prefix': ['python3'],
                           'remote_root': '/test/direct', 'timeout_seconds': 321}).start()
        # Directory access is covered separately; this suite isolates the state machine.
        patch.object(app, 'valid_workspace', return_value=project / 'workspace').start()
        DeferredThread.pending = []
        patch.object(app.threading, 'Thread', DeferredThread).start()
        self.popen = patch.object(app.subprocess, 'Popen',
                                 side_effect=AssertionError('SSH forbidden in offline tests')).start()
        self.run = patch.object(app.subprocess, 'run',
                               side_effect=AssertionError('SSH forbidden in offline tests')).start()
        self.execute = patch.object(app, 'execute',
                                    return_value={'content': [], 'isError': False}).start()
        self.chat = app.Chat()

    def tool_event(self):
        return {'type': 'tool_call', 'call_id': 'command-1', 'name': 'run_command',
                'arguments': {'command': 'Write-Output hello', 'cwd': '.', 'timeout_seconds': 10}}

    def test_second_send_is_rejected_without_another_request(self):
        self.chat.send('first')
        original = self.chat.state['request_id']
        with self.assertRaises(ValueError):
            self.chat.send('second')
        self.assertEqual(self.chat.state['request_id'], original)
        self.assertEqual(len(DeferredThread.pending), 1)
        self.assertEqual(len(list(app.DATA.glob('*.request.json'))), 1)
        self.popen.assert_not_called()
        self.assertEqual(DeferredThread.pending[0].args[0]['timeout_seconds'], 321)

    def test_command_without_approval_never_executes(self):
        self.chat.state['status'] = 'running'
        self.chat.write_remote = Mock()
        with patch.object(self.chat.approval_event, 'wait', return_value=False):
            self.chat.tool(self.tool_event())
        self.execute.assert_not_called()
        self.assertTrue(self.chat.write_remote.call_args.args[0]['result']['isError'])
        self.assertIsNone(self.chat.state['approval'])

    def test_denied_command_never_executes(self):
        self.chat.state['status'] = 'running'
        self.chat.write_remote = Mock()

        def deny(_timeout):
            self.chat.approve('command-1', False)
            return True

        with patch.object(self.chat.approval_event, 'wait', side_effect=deny):
            self.chat.tool(self.tool_event())
        self.execute.assert_not_called()

    def test_approval_is_single_use_and_rejects_non_boolean(self):
        self.chat.state['status'] = 'running'
        self.chat.write_remote = Mock()

        def allow(_timeout):
            with self.assertRaises(ValueError):
                self.chat.approve('command-1', 'false')
            self.chat.approve('command-1', True)
            with self.assertRaises(ValueError):
                self.chat.approve('command-1', True)
            return True

        with patch.object(self.chat.approval_event, 'wait', side_effect=allow):
            self.chat.tool(self.tool_event())
        self.execute.assert_called_once()
        self.assertEqual(self.execute.call_args.kwargs['base_root'], app.PROJECT)

    def prepare_recovery(self):
        self.chat.send('original prompt')
        self.chat.state['status'] = 'unknown'
        self.chat.turn_running = False
        self.run.side_effect = None
        self.run.return_value = Mock(returncode=0, stderr='', stdout=json.dumps({
            'state': 'completed', 'receipt': {'type': 'done', 'ok': True},
            'events': [{'type': 'text_delta', 'text': 'original answer'}]}))
        return self.chat.state['request_id']

    def test_first_turn_resume_state_follows_remote_confirmation(self):
        self.chat.send('first')
        self.chat.consume({'type': 'ready', 'resume_available': False})
        self.assertFalse(self.chat.state['session_started'])
        self.chat.consume({'type': 'done', 'ok': False, 'resume_available': False})
        self.assertFalse(self.chat.state['session_started'])
        self.chat.consume({'type': 'done', 'ok': True, 'resume_available': True})
        self.assertTrue(self.chat.state['session_started'])

    def test_recovery_queries_original_id_without_model_resubmit(self):
        original = self.prepare_recovery()
        self.chat.recover()
        DeferredThread.pending[-1].run()
        self.run.assert_called_once()
        command = self.run.call_args.args[0][-1]
        self.assertIn(' status ' + original, command)
        self.assertNotIn(' start', command)
        self.assertEqual(self.chat.state['request_id'], original)
        self.assertEqual(self.chat.state['messages'][-1]['text'], 'original answer')
        self.assertTrue(self.chat.state['session_started'])
        self.assertEqual(len(list(app.DATA.glob('*.request.json'))), 1)
        self.popen.assert_not_called()

    def test_second_recovery_is_rejected_while_first_is_in_flight(self):
        self.prepare_recovery()
        self.chat.recover()
        count = len(DeferredThread.pending)
        with self.assertRaises(ValueError):
            self.chat.recover()
        self.assertEqual(len(DeferredThread.pending), count)

    def test_stop_can_cancel_connecting_before_process_creation(self):
        self.chat.send('cancel before connecting')
        self.chat.stop()
        self.assertTrue(self.chat.cancel_event.is_set())

    def test_old_turn_cannot_clear_new_turn_process(self):
        self.chat.send('first')
        payload = DeferredThread.pending[0].args[0]
        next_proc = Mock()
        accepted = []

        def stream():
            yield json.dumps({'type': 'done', 'ok': True}) + '\n'
            try:
                self.chat.send('second')
            except ValueError:
                return
            accepted.append(True)
            self.chat.proc = next_proc

        first_proc = Mock(stdin=io.StringIO(), stdout=stream(), stderr=[])
        first_proc.poll.return_value = None
        self.popen.side_effect = None
        self.popen.return_value = first_proc
        self.chat.turn(payload)
        if accepted:
            self.assertIs(self.chat.proc, next_proc)
        else:
            self.assertIsNone(self.chat.proc)

    def test_old_connection_watcher_cannot_cancel_new_turn(self):
        self.chat.send('first')
        payload = DeferredThread.pending[0].args[0]
        proc = Mock(stdin=io.StringIO(), stdout=[json.dumps({'type': 'done', 'ok': True})], stderr=[])
        proc.poll.return_value = None
        self.popen.side_effect = None
        self.popen.return_value = proc
        self.chat.turn(payload)
        watchers = [t for t in DeferredThread.pending if t.target.__name__ == 'connection_ended']
        self.assertEqual(len(watchers), 1)
        self.chat.send('second')
        watchers[0].run()
        self.assertFalse(self.chat.cancel_event.is_set())

    def test_buffered_file_tool_after_disconnect_does_not_execute(self):
        self.chat.state['status'] = 'running'
        self.chat.cancel_event.set()
        self.chat.write_remote = Mock()
        self.chat.tool({'type': 'tool_call', 'call_id': 'file-after-disconnect',
                        'name': 'write_file', 'arguments': {'path': 'out.txt', 'content': 'x'}})
        self.execute.assert_not_called()

    def handler_class(self):
        captured = {}
        original_open = Path.open

        def open_file(path, *args, **kwargs):
            if path.name == 'server.lock':
                return Mock(read=Mock(return_value=b'0'))
            return original_open(path, *args, **kwargs)

        def server_factory(address, handler):
            captured['address'], captured['handler'] = address, handler
            return Mock()

        with patch.object(app, 'Chat', return_value=self.chat), \
                patch.object(app, 'ThreadingHTTPServer', side_effect=server_factory), \
                patch.object(app.msvcrt, 'locking'), patch.object(Path, 'open', open_file), \
                patch('builtins.print'):
            app.serve(18769)
        return captured['handler']

    def handler(self, handler_class, headers=None, body=b'{}'):
        handler = object.__new__(handler_class)
        handler.headers = {'Host': '127.0.0.1:18769', 'Origin': 'http://127.0.0.1:18769',
                           'X-Direct-Token': self.chat.token, 'Content-Length': str(len(body)),
                           'Content-Type': 'application/json'}
        handler.headers.update(headers or {})
        handler.path = '/api/send'
        handler.rfile = io.BytesIO(body)
        handler.json_response = Mock()
        return handler

    def test_origin_host_token_and_body_size_reject_before_read(self):
        handler_class = self.handler_class()
        for headers, expected in [({'Origin': 'https://other.invalid'}, 403),
                                  ({'Host': 'other.invalid:18769'}, 403),
                                  ({'X-Direct-Token': 'wrong'}, 403),
                                  ({'Content-Length': '262145'}, 400),
                                  ({'Content-Length': '-1'}, 400),
                                  ({'Content-Type': 'text/plain'}, 400)]:
            with self.subTest(headers=headers):
                handler = self.handler(handler_class, headers)
                handler.rfile = Mock()
                handler.do_POST()
                self.assertEqual(handler.json_response.call_args.args[1], expected)
                handler.rfile.read.assert_not_called()
        good = self.handler(handler_class)
        self.assertTrue(good.authorized())
        self.run.assert_not_called()
        self.popen.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
