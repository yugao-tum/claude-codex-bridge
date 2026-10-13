"""Portable configuration/deployment checks; no SSH or model process runs."""
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import Mock, patch

import app
import config
import deploy
import remote_runner


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='.test-config-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.value = json.loads((config.BASE / 'config.example.json').read_text(encoding='utf-8'))
        self.value.update(workspace_root=str(self.base / 'project'),
                          default_workspace=str(self.base / 'project' / 'scratch'),
                          model='test-model-id', effort='high', timeout_seconds=123,
                          ssh_argv=['never-run-ssh', '-T', 'example-host'],
                          remote_prefix=['docker', 'exec', '-i', 'example-container', 'python3'])
        self.addCleanup(patch.stopall)
        patch.object(config, 'BASE', self.base / 'runtime').start()

    def load(self, value=None):
        path = self.base / 'config.json'
        path.write_text(json.dumps(self.value if value is None else value), encoding='utf-8')
        return config.load_config(path)

    def test_fields_reach_connection_model_home_and_budget(self):
        settings = self.load()
        with patch.multiple(app, CONFIG=None, PROJECT=None, DEFAULT_WORKSPACE=None):
            app.configure(settings)
            self.assertEqual(app.PROJECT, self.base / 'project')
            self.assertEqual(app.DEFAULT_WORKSPACE, self.base / 'project' / 'scratch')
            argv = app.remote_argv('status', 'request-id')
            self.assertEqual(argv[:-1], self.value['ssh_argv'])
            self.assertEqual(shlex.split(argv[-1]), self.value['remote_prefix'] + [
                self.value['remote_root'] + '/remote_runner.py', 'status', 'request-id'])
        with patch.multiple(remote_runner, BINARY=None, PROJECTS=None, REMOTE_HOME=None,
                            MODEL=None, EFFORT=None, MAX_TIMEOUT=900):
            remote_runner.configure(config.remote_config(settings))
            self.assertEqual(remote_runner.MODEL, 'test-model-id')
            self.assertEqual(remote_runner.EFFORT, 'high')
            self.assertEqual(remote_runner.PROJECTS, Path(settings['remote_home']) / '.claude/projects')
            self.assertEqual(remote_runner.BINARY, Path(settings['remote_claude_binary']))
            self.assertEqual(remote_runner.MAX_TIMEOUT, 123)
        self.assertEqual(settings['port'], 8769)

    def test_rejects_placeholder_invalid_roots_and_budget(self):
        for field, bad in [('model', 'REPLACE_WITH_EXACT_AVAILABLE_MODEL_ID'),
                           ('default_workspace', str(self.base / 'outside')),
                           ('remote_root', '/'), ('remote_root', '/home/claude'),
                           ('remote_root', '/home/claude/.claude/projects/other-session'),
                           ('remote_root', '/home/claude/' + 'x' * 80),
                           ('remote_claude_binary', 'claude'), ('effort', 'ultra'),
                           ('port', True), ('timeout_seconds', 901), ('ssh_argv', ['ssh', '-n', 'host'])]:
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                self.load(dict(self.value, **{field: bad}))

    def test_deploy_payload_has_no_local_paths_or_authentication(self):
        settings = self.load()
        payload = deploy.deployment_payload(settings)
        self.assertEqual(set(payload['files']), {'config.py', 'remote_runner.py', 'mcp_stdio.py', 'runtime-config.json'})
        remote = json.loads(payload['files']['runtime-config.json'])
        self.assertEqual(set(remote), set(config.REMOTE_FIELDS))
        self.assertNotIn('ssh_argv', remote)
        self.assertNotIn('workspace_root', remote)
        self.assertEqual(remote['model'], 'test-model-id')
        compile(deploy.BOOTSTRAP, '<offline-deploy-bootstrap>', 'exec')

    def test_deploy_verifies_all_receipts_and_uses_configured_transport(self):
        settings = self.load()
        payload = deploy.deployment_payload(settings)
        receipt = {'root': settings['remote_root'], 'sha256': {
            key: hashlib.sha256(value.encode('utf-8')).hexdigest()
            for key, value in payload['files'].items()}}
        with patch.object(deploy, 'BASE', self.base), \
                patch.object(deploy, 'deployment_payload', return_value=payload), \
                patch.object(deploy.subprocess, 'run', return_value=Mock(
                    returncode=0, stdout=json.dumps(receipt))) as call:
            self.assertEqual(deploy.deploy(settings), receipt)
            argv = call.call_args.args[0]
            self.assertEqual(argv[:-1], settings['ssh_argv'])
            self.assertEqual(shlex.split(argv[-1])[:-2], settings['remote_prefix'])
            self.assertEqual(json.loads(call.call_args.kwargs['input']), payload)
            receipt['sha256']['runtime-config.json'] = 'wrong'
            call.return_value.stdout = json.dumps(receipt)
            with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                deploy.deploy(settings)


if __name__ == '__main__':
    unittest.main()
