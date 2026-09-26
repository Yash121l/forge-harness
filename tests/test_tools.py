import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from forge.tools import OUTPUT_LIMIT, ToolExecutor


class ToolExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        (self.repo / "hello.py").write_text("hello\nworld\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "initial"], check=True)
        self.events = []
        self.tools = ToolExecutor(self.repo, self.root / "artifacts", lambda kind, **data: self.events.append((kind, data)))

    def tearDown(self):
        self.temp.cleanup()

    def python(self, source):
        return shlex.quote(sys.executable) + " -c " + shlex.quote(source)

    def test_schemas_and_unknown_tool(self):
        schemas = self.tools.schemas()
        self.assertEqual(len(schemas), 6)
        json.dumps(schemas)
        self.assertFalse(self.tools.execute("unlink", {"path": "hello.py"})["ok"])

    def test_traversal_and_secret_paths(self):
        (self.root / "outside").write_text("secret")
        for name in ["../outside", str(self.root / "outside"), ".git/config", ".env", ".env.local", "id_rsa", "keys/key.pem"]:
            with self.subTest(name=name):
                self.assertFalse(self.tools.execute("read_file", {"path": name})["ok"])
                self.assertFalse(self.tools.execute("write_file", {"path": name, "content": "oops"})["ok"])
        self.assertEqual((self.root / "outside").read_text(), "secret")

    def test_symlink_escape(self):
        (self.root / "outside").write_text("secret")
        (self.repo / "link").symlink_to(self.root / "outside")
        (self.repo / "dirlink").symlink_to(self.root, target_is_directory=True)
        self.assertFalse(self.tools.execute("read_file", {"path": "link"})["ok"])
        self.assertFalse(self.tools.execute("write_file", {"path": "dirlink/new", "content": "oops"})["ok"])
        self.assertNotIn("link", self.tools.execute("list_files", {})["files"])
        self.assertFalse((self.root / "new").exists())

    def test_symlink_to_secret(self):
        (self.repo / ".env").write_text("secret")
        (self.repo / "alias").symlink_to(self.repo / ".env")
        self.assertFalse(self.tools.execute("read_file", {"path": "alias"})["ok"])

    def test_read_search_and_list(self):
        self.tools.execute("write_file", {"path": "nested/example.txt", "content": "world.*\nworld\n"})
        result = self.tools.execute("read_file", {"path": "hello.py", "start": 2, "end": 2})
        self.assertEqual(result["content"], "2: world")
        matches = self.tools.execute("search", {"query": "world.*"})["matches"]
        self.assertEqual(matches, [{"path": "nested/example.txt", "line": 1, "text": "world.*"}])
        self.assertTrue(self.tools.execute("search", {"query": "world", "limit": 1})["truncated"])
        self.assertTrue(self.tools.execute("list_files", {"limit": 1})["truncated"])

    def test_ambiguous_edit_does_not_write(self):
        (self.repo / "repeat").write_text("same same")
        for old in ["same", "absent", ""]:
            self.assertFalse(self.tools.execute("edit_file", {"path": "repeat", "old": old, "new": "new"})["ok"])
        self.assertEqual((self.repo / "repeat").read_text(), "same same")
        self.assertTrue(self.tools.execute("edit_file", {"path": "repeat", "old": "same same", "new": "new"})["ok"])
        self.assertEqual((self.repo / "repeat").read_text(), "new")

    def test_output_bounded_and_full_artifact(self):
        result = self.tools.execute("run_command", {"command": self.python("print('x' * 50000)")})
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(result["output"]), OUTPUT_LIMIT)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(len(Path(result["output_artifact"]).read_text()), 50001)
        self.assertEqual(len(self.tools.commands), 1)
        self.assertEqual(result["fingerprint_before"], result["fingerprint_after"])

    def test_command_environment_removes_secrets(self):
        with patch.dict(os.environ, {"AI_API_KEY": "sensitive", "GITHUB_TOKEN": "sensitive", "MY_PASSWORD": "sensitive", "NORMAL_VALUE": "retained"}):
            result = self.tools.execute("run_command", {"command": self.python("import os,json; print(json.dumps({k: os.environ.get(k) for k in ['AI_API_KEY','GITHUB_TOKEN','MY_PASSWORD','NORMAL_VALUE']}))")})
        values = json.loads(result["output"])
        self.assertIsNone(values["AI_API_KEY"])
        self.assertIsNone(values["GITHUB_TOKEN"])
        self.assertIsNone(values["MY_PASSWORD"])
        self.assertEqual(values["NORMAL_VALUE"], "retained")

    def test_timeout_kills_descendant_processes(self):
        marker = self.root / "escaped"
        child = "import time,pathlib; time.sleep(1); pathlib.Path(%r).write_text('bad')" % str(marker)
        command = self.python("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(10)" % child)
        started = time.monotonic()
        result = self.tools.execute("run_command", {"command": command, "timeout": 0.1})
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertLess(time.monotonic() - started, 2)
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_command_mutation_changes_fingerprint(self):
        result = self.tools.execute("run_command", {"command": self.python("from pathlib import Path; Path('new.txt').write_text('new')")})
        self.assertNotEqual(result["fingerprint_before"], result["fingerprint_after"])

    def test_untracked_patch_included_without_staging(self):
        before = self.tools.fingerprint()
        (self.repo / "new.txt").write_text("a new file\n")
        diff = self.tools.diff()
        self.assertIn("+++ b/new.txt", diff)
        self.assertIn("+a new file", diff)
        self.assertNotEqual(before, self.tools.fingerprint())
        status = subprocess.check_output(["git", "-C", str(self.repo), "status", "--porcelain"], text=True)
        self.assertIn("?? new.txt", status)
        subprocess.run(["git", "-C", str(self.repo), "apply", "--check", "--reverse", "-"], input=diff, text=True, check=True, capture_output=True)

    def test_untracked_symlink_and_executable_fingerprint(self):
        before = self.tools.fingerprint()
        (self.repo / "link").symlink_to("../outside")
        self.assertNotEqual(before, self.tools.fingerprint())
        self.assertIn("new file mode 120000", self.tools.diff())
        self.assertIn("+../outside", self.tools.diff())
        (self.repo / "script").write_text("echo hello\n")
        before = self.tools.fingerprint()
        (self.repo / "script").chmod(0o755)
        self.assertNotEqual(before, self.tools.fingerprint())
        self.assertIn("new file mode 100755", self.tools.diff())

    def test_background_children_do_not_escape_successful_commands(self):
        marker = self.root / "background"
        child = "import time,pathlib; time.sleep(0.5); pathlib.Path(%r).write_text('bad')" % str(marker)
        command = self.python("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r])" % child)
        result = self.tools.execute("run_command", {"command": command})
        self.assertEqual(result["exit_code"], 0)
        time.sleep(0.6)
        self.assertFalse(marker.exists())

    def test_staged_changes_and_binary_fingerprint(self):
        (self.repo / "hello.py").write_text("changed\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "hello.py"], check=True)
        self.assertIn("+changed", self.tools.diff())
        (self.repo / "binary").write_bytes(b"\x00a")
        before = self.tools.fingerprint()
        (self.repo / "binary").write_bytes(b"\x00b")
        self.assertNotEqual(before, self.tools.fingerprint())

    def test_empty_repo_fingerprint(self):
        empty = self.root / "empty"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        tool = ToolExecutor(empty, self.root / "empty-artifacts")
        before = tool.fingerprint()
        (empty / "a").write_text("hello\n")
        subprocess.run(["git", "-C", str(empty), "add", "a"], check=True)
        self.assertNotEqual(before, tool.fingerprint())
        self.assertIn("+hello", tool.diff())
        (empty / "a").write_text("final worktree\n")
        diff = tool.diff()
        self.assertNotIn("+hello", diff)
        self.assertIn("+final worktree", diff)
        result = subprocess.run(["git", "-C", str(empty), "apply", "--check", "--reverse", "-"], input=diff, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_artifacts_inside_repo_do_not_dirty_fingerprint(self):
        tools = ToolExecutor(self.repo, self.repo / ".forge")
        before = tools.fingerprint()
        result = tools.execute("run_command", {"command": self.python("print('hello')")})
        self.assertEqual(before, result["fingerprint_after"])
        self.assertNotIn(".forge", "\n".join(tools.execute("list_files", {})["files"]))

    def test_nonzero_command_is_failure_with_evidence(self):
        result = self.tools.execute("run_command", {"command": self.python("import sys; print('failed'); sys.exit(7)")})
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 7)
        self.assertFalse(result["cancelled"])
        self.assertEqual(self.tools.commands[-1]["exit_code"], 7)

    def test_cancellation_stops_process_group_promptly(self):
        cancelled = threading.Event()
        tools = ToolExecutor(self.repo, self.root / "cancel-artifacts", cancelled=cancelled)
        marker = self.root / "cancel-marker"
        child = "import time,pathlib; time.sleep(1); pathlib.Path(%r).write_text('bad')" % str(marker)
        command = self.python("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(10)" % child)
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        started = time.monotonic()
        try:
            result = tools.execute("run_command", {"command": command, "timeout": 20})
        finally:
            timer.cancel()
            timer.join()
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(len(tools.commands), 1)
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_preexisting_cancellation_does_not_spawn(self):
        cancelled = threading.Event()
        cancelled.set()
        tools = ToolExecutor(self.repo, self.root / "cancel-artifacts", cancelled=cancelled)
        result = tools.execute("run_command", {"command": self.python("from pathlib import Path; Path('unexpected').touch()")})
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse((self.repo / "unexpected").exists())
        self.assertEqual(len(tools.commands), 1)

    def test_secret_values_redacted_from_output_artifact_and_evidence(self):
        secret = "test-secret-very-private-123"
        (self.repo / "output.txt").write_text("x" * 65530 + secret + "\n" + secret)
        with patch.dict(os.environ, {"AI_API_KEY": secret}):
            result = self.tools.execute("run_command", {"command": self.python("from pathlib import Path; print(Path('output.txt').read_text()); print(%r)" % secret)})
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, json.dumps(self.tools.commands))
        artifact = Path(result["output_artifact"]).read_text()
        self.assertNotIn(secret, artifact)
        self.assertEqual(artifact.count("[REDACTED]"), 3)
        self.assertEqual(artifact[:65530], "x" * 65530)

    def test_unusual_names_binary_and_symlink_patches_apply(self):
        names = ["a space.txt", 'quote"name', "line\nname", "tab\tname", "empty", ":(glob)*", "-leading"]
        for name in names:
            (self.repo / name).write_text("" if name == "empty" else "new content without newline")
        (self.repo / "blob.bin").write_bytes(b"\x00\xff\x01")
        (self.repo / "outside-link").symlink_to("../outside")
        diff = self.tools.diff()
        result = subprocess.run(["git", "-C", str(self.repo), "apply", "--check", "--reverse", "-"], input=diff, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Also exercise literal pathspecs on an already tracked unusual name.
        subprocess.run(["git", "-C", str(self.repo), "--literal-pathspecs", "add", "--", ":(glob)*"], check=True)
        self.assertIn("new content", self.tools.diff())

    def test_guard_and_argument_validation(self):
        for command in ["git push origin main", "sudo reboot", "rm -rf /", "git reset --hard"]:
            self.assertFalse(self.tools.execute("run_command", {"command": command})["ok"])
        self.assertFalse(self.tools.execute("run_command", {"command": "true", "timeout": 0})["ok"])
        self.assertFalse(self.tools.execute("list_files", {"limit": -1})["ok"])


if __name__ == "__main__":
    unittest.main()
