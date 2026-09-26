"""Deterministic fixtures exercise the harness; they do not measure model ability."""
import json
import os
import subprocess
import sys
from pathlib import Path

CASES = {
    "intervals": {
        "title": "Merge touching intervals without mutating the caller's input",
        "issue": "merge_intervals must merge overlapping and touching intervals, return sorted intervals, handle empty input, and leave the caller's input unchanged. Fix the regression and run the tests.",
        "source": '''def merge_intervals(intervals):
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
''',
        "fixed": '''def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
''',
        "tests": '''import unittest
from solution import merge_intervals

class IntervalTests(unittest.TestCase):
    def test_touching(self):
        self.assertEqual(merge_intervals([[1, 3], [3, 5]]), [[1, 5]])
    def test_non_mutating(self):
        values = [[5, 9], [1, 3]]
        self.assertEqual(merge_intervals(values), [[1, 3], [5, 9]])
        self.assertEqual(values, [[5, 9], [1, 3]])
    def test_nested(self):
        self.assertEqual(merge_intervals([[1, 8], [2, 4], [7, 12]]), [[1, 12]])
    def test_empty(self):
        self.assertEqual(merge_intervals([]), [])
    def test_single(self):
        self.assertEqual(merge_intervals([[1, 2]]), [[1, 2]])
    def test_negative(self):
        self.assertEqual(merge_intervals([[-3, -1], [-1, 0]]), [[-3, 0]])
''',
    },
    "csv": {
        "title": "Parse quoted CSV fields and embedded newlines",
        "issue": "parse_csv(text) splits on commas and breaks quoted commas, escaped quotes, and multiline fields. Use standard CSV semantics, returning a list of rows; an empty input returns [].",
        "source": '''def parse_csv(text):
    return [line.split(',') for line in text.splitlines()]
''',
        "fixed": '''import csv
import io


def parse_csv(text):
    return list(csv.reader(io.StringIO(text, newline="")))
''',
        "tests": '''import unittest
from solution import parse_csv

class CsvTests(unittest.TestCase):
    def test_quotes(self):
        self.assertEqual(parse_csv('name,city\\n"Doe, Jane",Delhi'), [['name', 'city'], ['Doe, Jane', 'Delhi']])
    def test_newlines(self):
        self.assertEqual(parse_csv('"hello\\nworld",x'), [['hello\\nworld', 'x']])
    def test_escaped(self):
        self.assertEqual(parse_csv('"say ""hi""",x'), [['say "hi"', 'x']])
    def test_empty(self):
        self.assertEqual(parse_csv(''), [])
    def test_trailing(self):
        self.assertEqual(parse_csv('a,b,'), [['a', 'b', '']])
''',
    },
    "cache": {
        "title": "Expire cache entries at their exact deadline",
        "issue": "TTLCache.get must expire entries at now >= deadline, accept falsy cached values, and compute each deadline from the current clock. Fix boundary behavior without changing the public API.",
        "source": '''class TTLCache:
    def __init__(self, clock):
        self.clock = clock
        self.entries = {}

    def set(self, key, value, ttl):
        self.entries[key] = (value, self.clock() + ttl)

    def get(self, key, default=None):
        if key not in self.entries:
            return default
        value, deadline = self.entries[key]
        if self.clock() > deadline:
            del self.entries[key]
            return default
        return value or default
''',
        "fixed": '''class TTLCache:
    def __init__(self, clock):
        self.clock = clock
        self.entries = {}

    def set(self, key, value, ttl):
        self.entries[key] = (value, self.clock() + ttl)

    def get(self, key, default=None):
        if key not in self.entries:
            return default
        value, deadline = self.entries[key]
        if self.clock() >= deadline:
            del self.entries[key]
            return default
        return value
''',
        "tests": '''import unittest
from solution import TTLCache

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.cache = TTLCache(lambda: self.now)
    def test_boundary(self):
        self.cache.set('x', 1, 5)
        self.now = 15
        self.assertEqual(self.cache.get('x', 'missing'), 'missing')
    def test_zero(self):
        self.cache.set('x', 0, 5)
        self.assertEqual(self.cache.get('x', 99), 0)
    def test_false(self):
        self.cache.set('x', False, 5)
        self.assertIs(self.cache.get('x', True), False)
    def test_clock(self):
        self.now = 50
        self.cache.set('x', 8, 5)
        self.now = 54
        self.assertEqual(self.cache.get('x'), 8)
    def test_zero_ttl(self):
        self.cache.set('x', 1, 0)
        self.assertIsNone(self.cache.get('x'))
''',
    },
}


def create_fixture(path, case="intervals"):
    path = Path(path)
    path.mkdir(parents=True)
    definition = CASES[case]
    (path / "solution.py").write_text(definition["source"])
    (path / "test_solution.py").write_text(definition["tests"])
    (path / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    for command in (["git", "init", "-q", str(path)], ["git", "-C", str(path), "add", "."],
                    ["git", "-C", str(path), "-c", "user.name=Forge fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "Add failing fixture"]):
        subprocess.run(command, check=True, capture_output=True)
    return definition


def test_command():
    import shlex
    return shlex.quote(sys.executable) + " -B -m unittest discover -v"


class ScriptedProvider:
    """Known edit sequence for infrastructure testing, never a live-model fallback."""
    def __init__(self, case="intervals"):
        self.case = CASES[case]
        self.position = 0
        self.actions = [
            ("checkpoint", {"phase": "explore", "notes": "Inspect implementation and failing tests. This is a scripted infrastructure fixture."}),
            ("read_file", {"path": "solution.py"}),
            ("read_file", {"path": "test_solution.py"}),
            ("checkpoint", {"phase": "reproduce", "notes": "Baseline test output identifies the bug. Read the relevant branches before editing."}),
            ("run_command", {"command": test_command()}),
            ("checkpoint", {"phase": "implement", "notes": "Apply the fixture's known correction. Keep the existing tests intact."}),
            ("edit_file", {"path": "solution.py", "old": self.case["source"], "new": self.case["fixed"]}),
            ("verify", {}),
            ("finish", {"summary": "Applied the known fixture correction. Existing regression tests pass. This run tests harness mechanics; it makes no claim about model quality."}),
        ]

    def complete(self, messages, tools):
        name, args = self.actions[min(self.position, len(self.actions) - 1)]
        self.position += 1
        return {"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "fixture_" + str(self.position), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
            "input_tokens": 0, "output_tokens": 0}


def demo_run(root=None, case="intervals", live=False, **kwargs):
    from .core import Run
    import uuid
    root = Path(root or os.environ.get("FORGE_RUNS_DIR") or Path.cwd() / ".forge" / "runs").resolve()
    repo = root.parent / "fixtures" / (case + "-" + uuid.uuid4().hex[:8])
    definition = create_fixture(repo, case)
    return Run(repo, definition["issue"], root=root, test_command=test_command(),
               provider=None if live else ScriptedProvider(case), demo=not live, **kwargs)
