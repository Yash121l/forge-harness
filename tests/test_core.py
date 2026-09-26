"""End-to-end harness tests with real, disposable Git repositories."""
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from forge.core import Run
from forge.demo import CASES, create_fixture, demo_run, test_command as fixture_test_command


class ActionProvider:
    def __init__(self, actions, token_usage=0):
        self.actions = actions
        self.position = 0
        self.token_usage = token_usage
        self.received = []

    def complete(self, messages, tools):
        self.received.append(messages)
        name, arguments = self.actions[min(self.position, len(self.actions) - 1)]
        self.position += 1
        return {"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_%d" % self.position, "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}]},
            "input_tokens": self.token_usage, "output_tokens": 0}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, case="intervals"):
        repo = self.root / ("source-" + case)
        create_fixture(repo, case)
        return repo

    def run_actions(self, repo, actions, **kwargs):
        provider = ActionProvider(actions, kwargs.pop("token_usage", 0))
        run = Run(repo, CASES["intervals"]["issue"], root=self.runs,
                  test_command=fixture_test_command(), provider=provider,
                  max_steps=len(actions), **kwargs)
        return run, run.execute(), provider

    def test_all_scripted_demos_verify_and_preserve_source_repositories(self):
        for case in CASES:
            with self.subTest(case=case):
                run = demo_run(root=self.runs, case=case)
                original = (run.repo / "solution.py").read_text()
                head = subprocess.check_output(["git", "-C", str(run.repo), "rev-parse", "HEAD"], text=True).strip()
                result = run.execute()
                self.assertEqual(result["status"], "verified", result["summary"])
                self.assertTrue(result["demo"])
                self.assertTrue(result["verification"]["passed"])
                self.assertFalse(result["verification"]["baseline"]["ok"])
                self.assertIn("solution.py", result["patch"])
                self.assertEqual((run.repo / "solution.py").read_text(), original)
                self.assertEqual(subprocess.check_output(["git", "-C", str(run.repo), "status", "--porcelain"], text=True), "")
                self.assertEqual(subprocess.check_output(["git", "-C", str(run.repo), "rev-parse", "HEAD"], text=True).strip(), head)
                self.assertEqual((run.directory / "patch.diff").read_text(), result["patch"])
                self.assertEqual(json.loads((run.directory / "report.json").read_text())["status"], "verified")
                self.assertIn("Scripted fixture, no model inference", (run.directory / "report.md").read_text())

    def test_finish_refuses_when_there_is_no_patch(self):
        repo = self.fixture()
        run, result, _ = self.run_actions(repo, [("finish", {"summary": "done"})])
        self.assertNotEqual(result["status"], "verified")
        self.assertEqual(result["patch"], "")
        finish = [e for e in run.events if e["type"] == "action_result" and e.get("name") == "finish"][-1]
        self.assertFalse(finish["result"]["ok"])
        self.assertIn("No patch", finish["result"]["error"])

    def test_finish_refuses_failing_final_test(self):
        repo = self.fixture()
        actions = [("edit_file", {"path": "solution.py", "old": CASES["intervals"]["source"],
                                 "new": "def merge_intervals(intervals):\n    return []\n"}),
                   ("finish", {"summary": "incorrect fix"})]
        run, result, _ = self.run_actions(repo, actions)
        self.assertNotEqual(result["status"], "verified")
        self.assertTrue(result["patch"])
        self.assertFalse(result["verification"]["passed"])
        finish = [e for e in run.events if e["type"] == "action_result" and e.get("name") == "finish"][-1]
        self.assertFalse(finish["result"]["ok"])

    def test_finish_rechecks_after_verified_patch_is_mutated(self):
        repo = self.fixture()
        actions = [("edit_file", {"path": "solution.py", "old": CASES["intervals"]["source"],
                                 "new": CASES["intervals"]["fixed"]}),
                   ("verify", {}),
                   ("edit_file", {"path": "solution.py", "old": CASES["intervals"]["fixed"],
                                  "new": "def merge_intervals(intervals):\n    return []\n"}),
                   ("finish", {"summary": "stale green result"})]
        run, result, _ = self.run_actions(repo, actions)
        self.assertNotEqual(result["status"], "verified")
        evidence = [e for e in run.events if e["type"] == "verification"]
        self.assertEqual([e["passed"] for e in evidence], [True, False])

    def test_compaction_preserves_complete_tool_call_groups(self):
        repo = self.fixture()
        run = Run(repo, "issue", root=self.runs)
        first = [{"role": "system", "content": "system"}, {"role": "user", "content": "x" * 70000}]
        groups = []
        for group in range(7):
            ids = ["g%d_%d" % (group, n) for n in range(2)]
            groups.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": item, "type": "function", "function": {"name": "read_file", "arguments": "{}"}} for item in ids]})
            groups.extend({"role": "tool", "tool_call_id": item, "content": "{}"} for item in ids)
        compacted = run._compact(first + groups)
        self.assertEqual(compacted[0:2], first)
        self.assertEqual(compacted[2]["role"], "user")
        pending = set()
        for message in compacted[3:]:
            if message["role"] == "assistant":
                self.assertFalse(pending)
                pending = {call["id"] for call in message.get("tool_calls", [])}
            elif message["role"] == "tool":
                self.assertIn(message["tool_call_id"], pending)
                pending.remove(message["tool_call_id"])
        self.assertFalse(pending)

    def test_token_budget_blocks_tool_calls_after_over_budget_response(self):
        repo = self.fixture()
        run, result, provider = self.run_actions(repo, [("edit_file", {"path": "solution.py",
                 "old": CASES["intervals"]["source"], "new": CASES["intervals"]["fixed"]})],
                 max_tokens=1, token_usage=2)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(result["patch"], "")
        self.assertEqual(provider.position, 1)

    def test_cancel_interrupts_running_command(self):
        repo = self.fixture()
        provider = ActionProvider([("run_command", {"command": "sleep 10", "timeout": 20})])
        run = Run(repo, "cancel command", root=self.runs, provider=provider, max_steps=2)
        thread = threading.Thread(target=run.execute)
        thread.start()
        deadline = time.monotonic() + 5
        while not any(e["type"] == "tool_call" and e.get("name") == "run_command" for e in run.events):
            self.assertLess(time.monotonic(), deadline, "command did not start")
            time.sleep(0.02)
        started = time.monotonic()
        run.cancelled.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(), "cancellation should stop the command promptly")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(run.snapshot()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
