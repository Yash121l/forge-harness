"""Standard evaluation entry point and local operator console."""
import argparse
import json
import os
import sys
from pathlib import Path


def print_result(result):
    print("\n" + result["status"].upper() + "  " + result["id"])
    print(result["summary"])
    print("Steps: {steps} | Input tokens: {input_tokens} | Output tokens: {output_tokens}".format(**result))
    if result.get("workspace"):
        print("Workspace: " + result["workspace"])
        print("Report: " + str(Path(result["workspace"]).parent / "report.md"))


def run_with_progress(run, as_json=False):
    if not as_json:
        original_emit = run.emit
        def progress(event_type, **data):
            original_emit(event_type, **data)
            if event_type == "phase":
                print("  > " + data["phase"], flush=True)
            elif event_type == "tool_call":
                print("  [{:02d}] {}".format(data.get("step", 0), data["name"]), flush=True)
            elif event_type == "verification":
                print("  Verification: " + ("PASS" if data["passed"] else "FAIL"), flush=True)
            elif event_type == "error":
                print("  Error: " + data["message"], file=sys.stderr, flush=True)
        run.emit = progress
    result = run.execute()
    if as_json:
        print(json.dumps(result))
    else:
        print_result(result)
    return 0 if result["status"] == "verified" else 1


def main():
    parser = argparse.ArgumentParser(description="Forge | autonomous issue resolution with test evidence")
    sub = parser.add_subparsers(dest="command")
    solve = sub.add_parser("solve", help="Resolve an issue in a clean Git repository")
    solve.add_argument("--repo", required=True)
    source = solve.add_mutually_exclusive_group(required=True)
    source.add_argument("--issue")
    source.add_argument("--issue-file", help="Read text issue from a file; '-' reads stdin")
    solve.add_argument("--test-command", default="")
    solve.add_argument("--max-steps", type=int, default=40)
    solve.add_argument("--max-tokens", type=int, default=120000)
    solve.add_argument("--max-seconds", type=int, default=900)
    solve.add_argument("--json", action="store_true")
    resume = sub.add_parser("resume", help="Resume an interrupted live run from its saved checkpoint")
    resume.add_argument("directory", help="Path to .forge/runs/<id>")
    resume.add_argument("--max-steps", type=int, default=40)
    resume.add_argument("--max-tokens", type=int, default=120000)
    resume.add_argument("--max-seconds", type=int, default=900)
    resume.add_argument("--json", action="store_true")
    demo = sub.add_parser("demo", help="Run a scripted fixture, without a model or credentials")
    demo.add_argument("--case", choices=["intervals", "csv", "cache"], default="intervals")
    demo.add_argument("--json", action="store_true")
    bench = sub.add_parser("bench", help="Run local fixtures; --live measures the configured model")
    bench.add_argument("--live", action="store_true")
    server = sub.add_parser("serve", help="Start the local dashboard")
    server.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import serve
            serve(args.port)
            return 0
        if args.command == "resume":
            from .core import Run
            return run_with_progress(Run.resume(args.directory, max_steps=args.max_steps,
                max_tokens=args.max_tokens, max_seconds=args.max_seconds), args.json)
        if args.command == "demo":
            from .demo import demo_run
            return run_with_progress(demo_run(case=args.case), args.json)
        if args.command == "bench":
            from .demo import CASES, demo_run
            results = []
            for case in CASES:
                result = demo_run(case=case, live=args.live).execute()
                results.append({"case": case, "status": result["status"], "steps": result["steps"],
                                "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
                                "elapsed_seconds": result["elapsed_seconds"], "id": result["id"]})
            report = {"mode": "live-model" if args.live else "scripted-infrastructure", "results": results,
                      "passed": sum(r["status"] == "verified" for r in results), "total": len(results)}
            Path(".forge").mkdir(exist_ok=True)
            Path(".forge/benchmark-" + ("live" if args.live else "scripted") + ".json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] == report["total"] else 1
        if args.command == "solve":
            from .core import Run
            issue = args.issue
            if args.issue_file:
                issue = sys.stdin.read() if args.issue_file == "-" else Path(args.issue_file).read_text()
            return run_with_progress(Run(args.repo, issue, test_command=args.test_command,
                max_steps=args.max_steps, max_tokens=args.max_tokens, max_seconds=args.max_seconds), args.json)
        print("\n  F O R G E\n  Autonomous coding. Inspectable evidence.\n")
        if not sys.stdin.isatty():
            print("Send one JSON task on stdin: {\"repo\":\"/path/to/repo\",\"issue\":\"Fix the bug\",\"test_command\":\"python3 -m unittest\"}")
            line = sys.stdin.readline()
            if not line.strip():
                print("Ready. Use --help, 'demo', 'serve', or 'solve --help'.")
                return 0
            task = json.loads(line)
            from .core import Run
            return run_with_progress(Run(task["repo"], task["issue"], test_command=task.get("test_command", ""),
                                         max_steps=int(task.get("max_steps", 40))), True)
        print("  1  Resolve an issue\n  2  Run scripted demo\n  3  Open local dashboard\n")
        choice = input("Select [1/2/3]: ").strip() or "1"
        if choice == "3":
            from .server import serve
            serve(8765)
            return 0
        if choice == "2":
            from .demo import demo_run
            return run_with_progress(demo_run())
        from .core import Run
        repo = input("Git repository path: ").strip()
        print("Paste issue text. End with a line containing only END:")
        lines = []
        while True:
            line = input()
            if line == "END":
                break
            lines.append(line)
        command = input("Verification command [agent selects if blank]: ").strip()
        return run_with_progress(Run(repo, "\n".join(lines), test_command=command))
    except (ValueError, OSError, KeyError) as error:
        print("Forge: " + str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
