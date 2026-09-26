"""Repository tools for a trusted local coding agent.

File tools enforce repository boundaries. Shell commands are NOT sandboxed; run
untrusted projects in a disposable container or VM. Command guards are advisory.
"""
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from threading import Event
import uuid
from typing import Callable, Optional


OUTPUT_LIMIT = 24000
FILE_LIMIT = 2 * 1024 * 1024
_SECRET_ENV = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE)", re.I)
_SECRET_FILES = {".git", ".ssh", ".aws", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}


class ToolExecutor:
    def __init__(self, repo: Path, artifact_dir: Path, emit: Optional[Callable] = None, cancelled: Optional[Event] = None):
        self.repo = Path(repo).resolve()
        if not self.repo.is_dir():
            raise ValueError("Repository directory does not exist")
        self.artifact_dir = Path(artifact_dir).resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.emit = emit or (lambda event_type, **data: None)
        self.commands = []
        self.cancelled = cancelled or Event()

    @staticmethod
    def schemas():
        specs = [
            ("list_files", "List repository files recursively.", {"path": {"type": "string"}, "limit": {"type": "integer"}}, []),
            ("search", "Find literal text in repository files; returns numbered matching lines.", {"query": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
            ("read_file", "Read a UTF-8 file with 1-based line numbers, inclusive range.", {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
            ("edit_file", "Replace an exact string that occurs exactly once in a file.", {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, ["path", "old", "new"]),
            ("write_file", "Create or replace a UTF-8 file in the repository.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            ("run_command", "Run a trusted local shell command from the repository. This is not a security sandbox. Output is capped; full output is saved as an artifact.", {"command": {"type": "string"}, "timeout": {"type": "number"}}, ["command"]),
        ]
        return [{"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}} for name, description, properties, required in specs]

    @staticmethod
    def _denied(parts):
        return any(p.lower() in _SECRET_FILES or p.lower() == ".env" or p.lower().startswith(".env.") or p.lower().endswith((".pem", ".key", ".p12", ".pfx")) for p in parts)

    def _path(self, path):
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a nonempty string")
        given = Path(path)
        if self._denied(given.parts):
            raise ValueError("Access to Git internals or secret files is denied")
        target = (self.repo / given).resolve()
        try:
            relative = target.relative_to(self.repo)
        except ValueError:
            raise ValueError("Path escapes the repository")
        if self._denied(relative.parts):
            raise ValueError("Access to Git internals or secret files is denied")
        return target

    def _files(self, path):
        base = self._path(path)
        if base.is_file():
            yield base
            return
        if not base.is_dir():
            raise ValueError("Path does not exist")
        for root, dirs, files in os.walk(str(base), followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not self._denied([d]) and not (Path(root) / d).is_symlink() and (Path(root) / d).resolve() != self.artifact_dir)
            for name in sorted(files):
                try:
                    file = self._path(str(Path(root) / name))
                except ValueError:
                    continue
                if file.is_file() and self.artifact_dir not in file.parents:
                    yield file

    @staticmethod
    def _limit(value, maximum):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("limit must be a positive integer")
        return min(value, maximum)

    @staticmethod
    def _read(path):
        if path.stat().st_size > FILE_LIMIT:
            raise ValueError("File exceeds 2 MiB read/edit limit")
        content = path.read_text(encoding="utf-8")
        if "\x00" in content:
            raise ValueError("Binary files are not supported by text tools")
        return content

    def execute(self, name, args):
        try:
            if name not in {s["function"]["name"] for s in self.schemas()}:
                raise ValueError("Unknown tool: " + str(name))
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be an object")
            result = getattr(self, "_" + name)(**args)
            result.setdefault("ok", True)
        except (ValueError, TypeError, OSError, UnicodeError, subprocess.SubprocessError) as error:
            result = {"ok": False, "error": str(error)}
        self.emit("tool_result", tool=name, result=result)
        return result

    def _list_files(self, path=".", limit=200):
        limit = self._limit(limit, 1000)
        files = []
        for file in self._files(path):
            files.append(str(file.relative_to(self.repo)))
            if len(files) > limit:
                break
        return {"files": files[:limit], "truncated": len(files) > limit}

    def _search(self, query, path=".", limit=50):
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a nonempty literal string")
        limit = self._limit(limit, 200)
        matches = []
        for file in self._files(path):
            try:
                content = self._read(file)
            except (OSError, UnicodeError, ValueError):
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query in line:
                    matches.append({"path": str(file.relative_to(self.repo)), "line": number, "text": line[:2000]})
                    if len(matches) > limit:
                        return {"matches": matches[:limit], "truncated": True}
        return {"matches": matches, "truncated": False}

    def _read_file(self, path, start=1, end=200):
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise ValueError("Use a valid 1-based inclusive line range")
        lines = self._read(self._path(path)).splitlines()
        selected = lines[start - 1:min(end, start + 499)]
        output = "\n".join("%d: %s" % (number, line) for number, line in enumerate(selected, start))
        return {"path": path, "content": output[:OUTPUT_LIMIT], "total_lines": len(lines), "truncated": len(output) > OUTPUT_LIMIT or end >= start + 500}

    def _edit_file(self, path, old, new):
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("old must be nonempty and new must be a string")
        target = self._path(path)
        content = self._read(target)
        count = content.count(old)
        if count != 1:
            raise ValueError("Expected exactly one match; found %d. Read the file and include more context." % count)
        target.write_text(content.replace(old, new, 1), encoding="utf-8")
        return {"path": path, "replacements": 1}

    def _write_file(self, path, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content.encode("utf-8")) > FILE_LIMIT:
            raise ValueError("File exceeds 2 MiB write limit")
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path, "bytes": len(content.encode("utf-8"))}

    @staticmethod
    def _guard_command(command):
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a nonempty string")
        patterns = [r"\bgit\b[^\n;&|]*\bpush\b", r"\bgit\s+(?:reset\s+--hard|clean\b)", r"\b(?:sudo|shutdown|reboot|mkfs(?:\.\w+)?|diskutil)\b", r"\brm\s+[^\n;&|]*(?:\s/\s*(?:$|[;&|])|\s/(?:Users|home|etc|usr|var)(?:/|\s)|\s~(?:/|\s|$))", r"\bdd\s+[^\n;&|]*\bof=/dev/"]
        if any(re.search(pattern, command) for pattern in patterns):
            raise ValueError("Command rejected by best-effort destructive-command guard")

    def _run_command(self, command, timeout=60):
        self._guard_command(command)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("timeout must be greater than 0 and at most 300 seconds")
        before = self.fingerprint()
        artifact = self.artifact_dir / ("command-" + uuid.uuid4().hex + ".log")
        secret_values = sorted({value for key, value in os.environ.items() if value and _SECRET_ENV.search(key)}, key=len, reverse=True)
        env = {key: value for key, value in os.environ.items() if not _SECRET_ENV.search(key)}
        started = time.monotonic()
        deadline = started + timeout
        timed_out = False
        cancelled = self.cancelled.is_set()
        exit_code = None
        with artifact.open("wb") as output_file:
            if not cancelled:
                process = subprocess.Popen(command, shell=True, cwd=str(self.repo), env=env, stdout=output_file, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    while process.poll() is None:
                        if self.cancelled.is_set():
                            cancelled = True
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            break
                        try:
                            process.wait(timeout=min(0.1, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    self._terminate_group(process)
                exit_code = process.returncode
        self._redact_artifact(artifact, secret_values)
        size = artifact.stat().st_size
        with artifact.open("rb") as output_file:
            output = output_file.read(OUTPUT_LIMIT).decode("utf-8", errors="replace")
        for secret in secret_values:
            command = command.replace(secret, "[REDACTED]")
        result = {"ok": exit_code == 0 and not timed_out and not cancelled, "command": command, "exit_code": exit_code, "output": output, "duration": round(time.monotonic() - started, 3), "timed_out": timed_out, "cancelled": cancelled, "output_truncated": size > OUTPUT_LIMIT, "output_artifact": str(artifact), "fingerprint_before": before, "fingerprint_after": self.fingerprint()}
        self.commands.append(dict(result))
        self.emit("command", **result)
        return result

    @staticmethod
    def _redact_artifact(artifact, secret_values):
        if not secret_values:
            return
        secrets = [value.encode("utf-8") for value in secret_values]
        pattern = re.compile(b"|".join(re.escape(value) for value in secrets))
        overlap = max(map(len, secrets)) - 1
        temporary = artifact.with_suffix(".redacted")
        with artifact.open("rb") as source, temporary.open("wb") as destination:
            buffer = b""
            while True:
                chunk = source.read(65536)
                buffer += chunk
                safe_end = max(0, len(buffer) - overlap) if chunk else len(buffer)
                consumed = 0
                for match in pattern.finditer(buffer):
                    if match.start() >= safe_end:
                        break
                    destination.write(buffer[consumed:match.start()])
                    destination.write(b"[REDACTED]")
                    consumed = match.end()
                safe_end = max(safe_end, consumed)
                destination.write(buffer[consumed:safe_end])
                buffer = buffer[safe_end:]
                if not chunk:
                    break
        temporary.replace(artifact)

    @staticmethod
    def _terminate_group(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        # The shell may exit while a descendant ignores SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def _git(self, *args):
        return subprocess.run(["git", "--literal-pathspecs", "-c", "core.quotePath=false", *args], cwd=str(self.repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

    def diff(self):
        has_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(self.repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        chunks = []
        if has_head:
            names = self._git("diff", "HEAD", "--name-only", "-z").decode("utf-8", errors="surrogateescape").split("\x00")
            names = sorted({name for name in names if name and not self._denied(Path(name).parts) and not self._artifact_path(name)})
            if names:
                chunks.append(self._git("diff", "HEAD", "--no-ext-diff", "--no-textconv", "--binary", "--", *names).decode("utf-8", errors="surrogateescape"))
        new_paths = self._git("ls-files", "--others", "--exclude-standard", "-z").decode("utf-8", errors="surrogateescape").split("\x00")
        if not has_head:
            new_paths += self._git("ls-files", "--cached", "-z").decode("utf-8", errors="surrogateescape").split("\x00")
        for name in sorted({name for name in new_paths if name}):
            if self._denied(Path(name).parts) or self._artifact_path(name):
                continue
            target = self.repo / name
            if not target.is_symlink():
                try:
                    target = self._path(name)
                except ValueError:
                    continue
                if not target.is_file():
                    continue
            result = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--binary", "--", "/dev/null", name], cwd=str(self.repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode not in (0, 1):
                raise ValueError("Cannot create patch for " + name + ": " + result.stderr.decode("utf-8", errors="replace"))
            chunks.append(result.stdout.decode("utf-8", errors="surrogateescape"))
        return "".join(chunks)

    def _artifact_path(self, name):
        target = (self.repo / name).resolve()
        return target == self.artifact_dir or self.artifact_dir in target.parents

    def fingerprint(self):
        return hashlib.sha256(self.diff().encode("utf-8", errors="surrogateescape")).hexdigest()
