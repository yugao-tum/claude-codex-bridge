import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch
import uuid

import local_tools as tools


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        # Keep nested release test paths short and inherit the test folder ACL.
        self.base = Path(__file__).resolve().parent / ('claude-tools-test-' + uuid.uuid4().hex[:8])
        self.base.mkdir()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.runtime = self.base / 'direct-app'
        self.state = self.runtime / 'state'
        patch.multiple(tools, RUNTIME_ROOT=self.runtime, STATE_ROOT=self.state).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        assert self.base.resolve().parent == Path(__file__).resolve().parent
        assert self.base.name.startswith('claude-tools-test-')
        shutil.rmtree(self.base)

    def call(self, name, args, workspace=None, cancel_event=None):
        result = tools.execute(name, args, workspace or self.workspace, base_root=self.base, cancel_event=cancel_event)
        return result, json.loads(result["content"][0]["text"])

    def test_normal_write_read_backup_and_nonrecursive_list(self):
        result, _ = self.call("write_file", {"path": "src/hello.py", "content": "print('你好')\n"})
        self.assertNotIn("isError", result)
        _, data = self.call("read_file", {"path": "src/hello.py"})
        self.assertEqual(data["content"], "print('你好')\n")
        _, data = self.call("write_file", {"path": "src/hello.py", "content": "print('new')\n"})
        self.assertTrue(data["overwritten"])
        backup = self.state / "backups" / (data["backup_id"] + ".bak")
        self.assertEqual(backup.read_text(encoding="utf-8"), "print('你好')\n")
        _, listing = self.call("list_files", {})
        self.assertEqual(listing["entries"], [{"name": "src", "type": "directory"}])
        result, _ = self.call("read_file", {"path": str(backup.relative_to(self.base))}, self.base)
        self.assertTrue(result["isError"])

    def test_relative_path_and_windows_name_boundaries(self):
        for value in ("../outside.txt", "src/../../outside.txt", "D:/outside.txt", "D:relative.txt",
                      "\\\\server\\share\\x", "/outside.txt", "x.txt:secret", "trailing. ", "NUL", "CON.txt"):
            with self.subTest(path=value):
                result, _ = self.call("write_file", {"path": value, "content": "safe"})
                self.assertTrue(result["isError"])
        outside = self.base.parent / (self.base.name + "-outside")
        result, _ = self.call("list_files", {}, outside)
        self.assertTrue(result["isError"])

    def test_secret_and_program_paths_are_blocked(self):
        for path in (".git/config", ".codex/config.toml", ".ssh/id_ed25519", ".aws/config", ".env",
                     ".env.local", "client.pem", "credentials.json", "api_key.txt", "private-key.txt",
                     "direct-app/config.json", "direct-app/app.py", ".agents/private.txt"):
            with self.subTest(path=path):
                result, _ = self.call("write_file", {"path": path, "content": "safe"}, self.base)
                self.assertTrue(result["isError"])
        result, _ = self.call("write_file", {"path": "ordinary.txt", "content": "-----BEGIN PRIVATE KEY-----\nabc"})
        self.assertTrue(result["isError"])
        (self.workspace / ".env").write_text("private")
        (self.workspace / "visible.txt").write_text("public")
        _, listing = self.call("list_files", {})
        self.assertEqual([row["name"] for row in listing["entries"]], ["visible.txt"])

    def test_symlink_and_hardlink_are_blocked(self):
        source = self.base / "outside.txt"
        source.write_text("outside")
        hardlink = self.workspace / "hard.txt"
        try:
            os.link(source, hardlink)
        except OSError:
            pass
        else:
            result, _ = self.call("read_file", {"path": "hard.txt"})
            self.assertTrue(result["isError"])
        link = self.workspace / "link.txt"
        try:
            link.symlink_to(source)
        except OSError:
            if os.name == "nt":
                junction = self.workspace / "junction"
                proc = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(self.base)],
                                      capture_output=True)
                if proc.returncode == 0:
                    try:
                        result, _ = self.call("read_file", {"path": "junction/outside.txt"})
                        self.assertTrue(result["isError"])
                    finally:
                        junction.rmdir()
        else:
            result, _ = self.call("read_file", {"path": "link.txt"})
            self.assertTrue(result["isError"])

    def test_file_limit_utf8_and_argument_validation(self):
        result, _ = self.call("write_file", {"path": "large.txt", "content": "x" * (tools.MAX_FILE_BYTES + 1)})
        self.assertTrue(result["isError"])
        (self.workspace / "large.txt").write_bytes(b"x" * (tools.MAX_FILE_BYTES + 1))
        result, _ = self.call("read_file", {"path": "large.txt"})
        self.assertTrue(result["isError"])
        (self.workspace / "binary.txt").write_bytes(b"\xff")
        result, _ = self.call("read_file", {"path": "binary.txt"})
        self.assertTrue(result["isError"])
        for name, args in (("missing", {}), ("read_file", {}), ("list_files", {"limit": 201}),
                           ("list_files", {"limit": True}), ("read_file", {"path": "x", "base_root": "C:/"})):
            result, _ = self.call(name, args)
            self.assertTrue(result["isError"])

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
    def test_approved_command_output_limit_and_timeout(self):
        # Test calls stand in for prior app approval; no business commands run.
        result, data = self.call("run_command", {"command": "Write-Output 'hello'; Write-Output ('x' * 40000)", "timeout_seconds": 10})
        self.assertNotIn("isError", result)
        self.assertEqual(data["exit_code"], 0)
        self.assertTrue(data["stdout"].startswith("hello"))
        self.assertTrue(data["stdout_truncated"])
        self.assertLessEqual(len(data["stdout"].encode()), tools.MAX_OUTPUT_BYTES)
        result, data = self.call("run_command", {"command": "Start-Sleep -Seconds 10", "timeout_seconds": 1})
        self.assertNotIn("isError", result)
        self.assertTrue(data["timed_out"])
        self.assertFalse(data["cancelled"])
        self.assertIsInstance(data["exit_code"], int)
        self.assertLess(data["elapsed_seconds"], 8)
        for args in ({"command": "Write-Output 'x'", "cwd": ".."},
                     {"command": "Write-Output 'x'", "timeout_seconds": 301}):
            result, _ = self.call("run_command", args)
            self.assertTrue(result["isError"])

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
    def test_approved_command_cancellation(self):
        cancel = threading.Event()
        timer = threading.Timer(0.5, cancel.set)
        timer.start()
        try:
            result, data = self.call("run_command", {"command": "Start-Sleep -Seconds 10", "timeout_seconds": 20}, cancel_event=cancel)
        finally:
            timer.cancel()
        self.assertNotIn("isError", result)
        self.assertTrue(data["cancelled"])
        self.assertFalse(data["timed_out"])
        self.assertLess(data["elapsed_seconds"], 8)
        result, data = self.call("run_command", {"command": "Write-Output 'must not run'"}, cancel_event=cancel)
        self.assertTrue(data["cancelled"])
        self.assertIsNone(data["exit_code"])


if __name__ == "__main__":
    unittest.main()
