"""Durable resume checks using disposable Git fixtures and scripted providers."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from forge.core import Run
from forge.demo import CASES, create_fixture, test_command


class SequenceProvider:
    def __init__(self, responses, model="fixture-model", base_url="http://localhost:9999/v1"):
        self.responses = iter(responses)
        self.config = SimpleNamespace(model=model, base_url=base_url)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        calls = [{"id": "call_%d" % index, "type": "function",
                  "function": {"name": name, "arguments": json.dumps(args)}}
                 for index, (name, args) in enumerate(response, 1)]
        return {"message": {"role": "assistant", "content": None, "tool_calls": calls},
                "input_tokens": 10, "output_tokens": 5}


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        create_fixture(self.repo)
        self.runs = self.root / "runs"

    def tearDown(self):
        self.temp.cleanup()

    def interrupted_run(self):
        edit = ("edit_file", {"path": "solution.py", "old": CASES["intervals"]["source"],
                              "new": CASES["intervals"]["fixed"]})
        provider = SequenceProvider([[edit], RuntimeError("provider unavailable")])
        run = Run(self.repo, CASES["intervals"]["issue"], root=self.runs,
                  test_command=test_command(), provider=provider, max_steps=4)
        result = run.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn("solution.py", result["patch"])
        self.assertTrue((run.directory / "checkpoint.json").exists())
        return run, result

    def test_resume_after_provider_failure_preserves_parent_and_verifies_patch(self):
        original_source = (self.repo / "solution.py").read_text()
        original_head = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True)
        parent, failed = self.interrupted_run()
        parent_report = (parent.directory / "report.json").read_text()
        parent_patch = (parent.directory / "patch.diff").read_text()
        provider = SequenceProvider([[('finish', {'summary': 'Fixed interval merging and verified tests.'})]])
        resumed = Run.resume(parent.directory, provider=provider, max_steps=2)
        self.assertNotEqual(resumed.id, parent.id)
        result = resumed.execute()
        self.assertEqual(result["status"], "verified", result["summary"])
        self.assertEqual(result["parent_run"], parent.id)
        self.assertEqual(result["patch"], failed["patch"])
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(result["verification"]["command"], test_command())
        self.assertEqual((parent.directory / "report.json").read_text(), parent_report)
        self.assertEqual((parent.directory / "patch.diff").read_text(), parent_patch)
        self.assertEqual(json.loads(parent_report)["status"], "failed")
        self.assertEqual((self.repo / "solution.py").read_text(), original_source)
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.repo), "status", "--porcelain"], text=True), "")
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True), original_head)
        self.assertTrue(any(event["type"] == "verification" and event["passed"] for event in resumed.events))
        self.assertEqual(provider.seen[0][-1]["role"], "user")
        self.assertIn("Resume", provider.seen[0][-1]["content"])

    def test_resume_refuses_external_workspace_mutation(self):
        parent, _ = self.interrupted_run()
        (parent.directory / "workspace" / "solution.py").write_text("externally modified\n")
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            Run.resume(parent.directory, provider=SequenceProvider([]))

    def test_resume_refuses_model_or_endpoint_mismatch(self):
        parent, _ = self.interrupted_run()
        for config in ({"model": "different-model"}, {"base_url": "http://localhost:9998/v1"}):
            with self.subTest(config=config):
                resumed = Run.resume(parent.directory, provider=SequenceProvider([], **config))
                result = resumed.execute()
                self.assertEqual(result["status"], "failed")
                self.assertIn("same model and provider endpoint", result["summary"])
                self.assertEqual(json.loads((parent.directory / "report.json").read_text())["status"], "failed")

    def test_more_than_sixteen_tool_calls_receive_one_result_each(self):
        calls = [("read_file", {"path": "solution.py", "start": n, "end": n}) for n in range(1, 19)]
        provider = SequenceProvider([calls])
        run = Run(self.repo, "Inspect fixture", root=self.runs, provider=provider, max_steps=1)
        result = run.execute()
        self.assertEqual(result["status"], "budget_exhausted")
        action_results = [event for event in run.events if event["type"] == "action_result"]
        self.assertEqual(len(action_results), len(calls))
        self.assertTrue(all(item["result"]["ok"] is False for item in action_results[16:]))
        saved = json.loads((run.directory / "checkpoint.json").read_text())
        messages = saved["messages"]
        assistant = next(message for message in messages if message["role"] == "assistant")
        ids = {call["id"] for call in assistant["tool_calls"]}
        results = [message["tool_call_id"] for message in messages if message["role"] == "tool"]
        self.assertEqual(len(results), len(calls))
        self.assertEqual(set(results), ids)


if __name__ == "__main__":
    unittest.main()
