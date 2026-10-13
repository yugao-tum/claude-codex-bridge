"""Workspace file tools and explicitly approved PowerShell execution.

run_command does not approve a command or provide an OS sandbox. The caller must
show and approve the exact command, cwd and timeout before calling execute().
An approved command runs with the Windows account's permissions, beyond cwd.
"""

import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid


RUNTIME_ROOT = Path(__file__).resolve().parent
STATE_ROOT = RUNTIME_ROOT / 'state'
MAX_FILE_BYTES = 200 * 1024
MAX_OUTPUT_BYTES = 32 * 1024
PROTECTED_PARTS = {".git", ".codex", ".agents", ".ssh", ".aws", ".env"}
SECRET_NAME = re.compile(
    r"(^\.env(?:\.|$)|credential|secret|(?:^|[._-])(?:token|api[_-]?key|private[_-]?key)(?:[._-]|$)"
    r"|^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.|$)|\.(?:pem|key|pfx|p12|ppk)$)", re.I
)
SECRET_CONTENT = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
    r"|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"
)


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }}


TOOLS = [
    _tool("read_file", "Read one permitted UTF-8 file, at most 200 KiB.",
          {"path": {"type": "string", "description": "Path relative to the selected workspace."}}, ["path"]),
    _tool("write_file", "Write one permitted UTF-8 file, at most 200 KiB; preserve overwritten bytes in a protected backup.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _tool("list_files", "List one permitted directory without recursion; protected entries are omitted.",
          {"path": {"type": "string", "default": "."}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 200}}),
    _tool("run_command", "Requires prior approval of the exact command, cwd and timeout in the local app. Runs PowerShell with the Windows account's permissions; cwd is NOT an OS sandbox.",
          {"command": {"type": "string"}, "cwd": {"type": "string", "default": "."},
           "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 60}}, ["command"]),
]


def _plain_path(path):
    """Reject reparse points before resolve can conceal their original location."""
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("Symbolic links and reparse points are not permitted.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError("Hard-linked files are not permitted.")


def _policy(path, base):
    try:
        parts = tuple(part.casefold() for part in path.relative_to(base).parts)
    except ValueError:
        raise ValueError("Path is outside the permitted project root.") from None
    if any(part in PROTECTED_PARTS or SECRET_NAME.search(part) for part in parts):
        raise ValueError("Protected or sensitive paths are not accessible.")
    if path.is_relative_to(RUNTIME_ROOT) or path.is_relative_to(STATE_ROOT):
        raise ValueError("Bridge programs, state and remote connection files are protected.")


def _workspace(workspace, base_root):
    base = Path(base_root).absolute()
    root = Path(workspace).absolute()
    _plain_path(base)
    _plain_path(root)
    base = base.resolve(strict=True)
    root = root.resolve(strict=True)
    _policy(root, base)
    if not root.is_dir():
        raise ValueError("Selected workspace must be an existing directory.")
    return root, base


def validate_workspace(workspace, base_root):
    return _workspace(workspace, base_root)[0]


def _path(value, root, base):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Path must be a nonempty relative string.")
    win = PureWindowsPath(value)
    parts = value.replace("\\", "/").split("/")
    if win.anchor or win.drive or value.startswith("/") or ":" in value or ".." in parts:
        raise ValueError("Absolute paths, parent traversal and NTFS alternate streams are not permitted.")
    for part in parts:
        if part in ("", "."):
            continue
        if part.endswith((" ", ".")) or re.search(r'[<>"|?*\x00-\x1f]', part):
            raise ValueError("Ambiguous Windows path names are not permitted.")
        if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I):
            raise ValueError("Windows device names are not permitted.")
    target = root.joinpath(*parts)
    _plain_path(target)
    target = target.resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError("Path is outside the selected workspace.")
    _policy(target, base)
    return target


def _bounded_bytes(path):
    _plain_path(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds the 200 KiB limit.")
    return data


def _write(target, content, base):
    if not isinstance(content, str) or "\x00" in content:
        raise ValueError("content must be UTF-8 text without NUL characters.")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Content exceeds the 200 KiB limit.")
    if SECRET_CONTENT.search(content):
        raise ValueError("Content resembles a private key or credential; it was not written.")
    _plain_path(target)
    backup_id = None
    if target.exists():
        old = _bounded_bytes(target)
        backup_dir = STATE_ROOT / "backups"
        _plain_path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        _plain_path(backup_dir)
        backup_id = uuid.uuid4().hex
        with (backup_dir / (backup_id + ".bak")).open("xb") as backup:
            backup.write(old)
            backup.flush()
            os.fsync(backup.fileno())
    target.parent.mkdir(parents=True, exist_ok=True)
    _plain_path(target)
    fd, temporary = tempfile.mkstemp(prefix=".claude-write-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _plain_path(target)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"bytes": len(data), "overwritten": backup_id is not None, "backup_id": backup_id}


def _output(stream):
    stream.seek(0, os.SEEK_END)
    truncated = stream.tell() > MAX_OUTPUT_BYTES
    stream.seek(0)
    return stream.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace"), truncated


def _run(command, cwd, timeout, base, cancel_event=None):
    """Caller approval is mandatory; binding cwd is not permission isolation."""
    if not isinstance(command, str) or not command.strip() or "\x00" in command or len(command) > 16000:
        raise ValueError("command must be nonempty text of at most 16000 characters.")
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("timeout_seconds must be an integer from 1 to 300.")
    powershell = shutil.which("powershell.exe")
    if os.name != "nt" or not powershell:
        raise ValueError("Windows PowerShell is unavailable.")
    # The encoding prelude only fixes captured output; the approved command is unchanged.
    prelude = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $OutputEncoding = [Console]::OutputEncoding;\n"
    argv = [powershell, "-NoProfile", "-NonInteractive", "-Command", prelude + command]
    start = time.monotonic()
    timed_out = False
    cancelled = False
    output_dir = STATE_ROOT / "command-output"
    _plain_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _plain_path(output_dir)
    with tempfile.TemporaryFile(dir=output_dir) as stdout, tempfile.TemporaryFile(dir=output_dir) as stderr:
        if cancel_event is not None and cancel_event.is_set():
            return {"cwd": str(cwd), "exit_code": None, "timed_out": False, "cancelled": True,
                    "elapsed_seconds": 0, "stdout": "", "stderr": "",
                    "stdout_truncated": False, "stderr_truncated": False}
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                timed_out = True
                break
            try:
                proc.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                pass
        if timed_out or cancelled:
            if proc.poll() is None:
                taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "taskkill.exe"
                killed = subprocess.run([str(taskkill), "/PID", str(proc.pid), "/T", "/F"],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=15,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                if killed.returncode and proc.poll() is None:
                    proc.kill()
            proc.wait(timeout=15)
        out, out_cut = _output(stdout)
        err, err_cut = _output(stderr)
    return {"cwd": str(cwd), "exit_code": proc.returncode, "timed_out": timed_out,
            "cancelled": cancelled,
            "elapsed_seconds": round(time.monotonic() - start, 3), "stdout": out, "stderr": err,
            "stdout_truncated": out_cut, "stderr_truncated": err_cut}


def execute(name, args, workspace: Path, *, base_root: Path, cancel_event=None):
    """Return an MCP tool result; base_root comes from local app configuration.

    The application MUST obtain prior approval for the exact run_command command,
    cwd and timeout. This function does not replace that approval, restrict what
    an approved command can access, or provide an OS sandbox. File tools enforce
    the selected workspace. Never expose base_root as a user/tool parameter.
    cancel_event is an optional threading.Event owned by the caller; setting it
    cancels only the current command's process tree. File operations are immediate.
    """
    try:
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object.")
        schema = next((tool["inputSchema"] for tool in TOOLS if tool["name"] == name), None)
        if schema is None:
            raise ValueError("Unknown tool.")
        if set(args) - schema["properties"].keys() or any(key not in args for key in schema["required"]):
            raise ValueError("Missing or unsupported tool arguments.")
        root, base = _workspace(workspace, base_root)
        target = _path(args.get("cwd" if name == "run_command" else "path", "."), root, base)
        if name == "read_file":
            data = _bounded_bytes(target)
            result = {"path": str(target.relative_to(root)), "bytes": len(data), "content": data.decode("utf-8")}
        elif name == "write_file":
            result = {"path": str(target.relative_to(root)), **_write(target, args["content"], base)}
        elif name == "list_files":
            limit = args.get("limit", 200)
            if type(limit) is not int or not 1 <= limit <= 200:
                raise ValueError("limit must be an integer from 1 to 200.")
            entries = []
            for entry in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
                try:
                    checked = _path(str(entry.relative_to(root)), root, base)
                except (ValueError, OSError):
                    continue
                entries.append({"name": checked.name, "type": "directory" if checked.is_dir() else "file"})
                if len(entries) > limit:
                    break
            result = {"path": str(target.relative_to(root)), "entries": entries[:limit], "truncated": len(entries) > limit}
        else:
            if not target.is_dir():
                raise ValueError("cwd must be an existing directory.")
            result = _run(args["command"], target, args.get("timeout_seconds", 60), base, cancel_event)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        # Do not echo a supplied command, secret content, or an unrestricted path in errors.
        detail = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError) else type(exc).__name__
        return {"content": [{"type": "text", "text": json.dumps({"error": detail}, ensure_ascii=False)}], "isError": True}
