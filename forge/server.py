"""Loopback-only dashboard API. Same-origin writes, one run at a time."""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import Run
from .demo import demo_run
from .provider import DEFAULT_MODEL, DEFAULT_BASE_URL


class Dashboard:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("FORGE_RUNS_DIR") or Path.cwd() / ".forge" / "runs").resolve()
        self.runs = {}
        self.lock = threading.RLock()
        self.active_id = None

    def config(self):
        return {"model": os.environ.get("AI_MODEL", DEFAULT_MODEL),
                "base_url": os.environ.get("AI_BASE_URL", DEFAULT_BASE_URL),
                "configured": bool(os.environ.get("AI_API_KEY"))}

    def start(self, task=None):
        with self.lock:
            if self.active_id and self.runs[self.active_id].snapshot()["status"] in ("running", "queued"):
                raise ValueError("A run is already active. Wait for it or stop it first.")
            if task is None:
                run = demo_run(root=self.root)
            else:
                if not self.config()["configured"]:
                    raise ValueError("Set AI_API_KEY in the server environment, then restart the dashboard.")
                run = Run(task["repo"], task["issue"], root=self.root,
                          test_command=task.get("test_command", ""), max_steps=int(task.get("max_steps", 40)))
            self.runs[run.id] = run
            self.active_id = run.id
            threading.Thread(target=run.execute, name="forge-" + run.id, daemon=True).start()
            return run.id

    def state(self):
        with self.lock:
            runs = [run.snapshot() for run in self.runs.values()]
            known = set(self.runs)
            if self.root.exists():
                for directory in sorted(self.root.iterdir(), reverse=True)[:100]:
                    if directory.name not in known:
                        try:
                            runs.append(json.loads((directory / "report.json").read_text()))
                        except (OSError, ValueError):
                            pass
            runs.sort(key=lambda r: r.get("started_at", 0), reverse=True)
            # Patches are fetched only for the selected run.
            return {"config": self.config(), "runs": [{**r, "patch": ""} for r in runs], "active_id": self.active_id}

    def detail(self, run_id):
        if not re.fullmatch(r"[\w-]+", run_id):
            raise KeyError("Unknown run")
        with self.lock:
            if run_id in self.runs:
                return self.runs[run_id].snapshot(events=True)
        directory = self.root / run_id
        data = json.loads((directory / "report.json").read_text())
        data["events"] = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        return data


def make_server(port=8765, dashboard=None):
    app = dashboard or Dashboard()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status, body, content_type="application/json"):
            payload = json.dumps(body).encode() if content_type == "application/json" else body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def valid_host(self):
            return self.headers.get("Host", "") in ("127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port))

        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, {"error": "Invalid Host"})
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    return self.respond(200, (Path(__file__).parent / "static" / "index.html").read_text(), "text/html")
                if path == "/api/state":
                    return self.respond(200, app.state())
                if path.startswith("/api/runs/"):
                    parts = path.split("/")
                    detail = app.detail(parts[3])
                    if len(parts) == 5 and parts[4] == "patch":
                        return self.respond(200, detail["patch"], "text/plain")
                    if len(parts) == 5 and parts[4] == "report":
                        report = (app.root / parts[3] / "report.md").read_text()
                        return self.respond(200, report, "text/markdown")
                    if len(parts) == 4:
                        return self.respond(200, detail)
                return self.respond(404, {"error": "Not found"})
            except (OSError, ValueError, KeyError):
                return self.respond(404, {"error": "Run not found"})

        def do_POST(self):
            if not self.valid_host():
                return self.respond(403, {"error": "Invalid Host"})
            origin = self.headers.get("Origin")
            if origin and origin not in ("http://127.0.0.1:" + str(self.server.server_port), "http://localhost:" + str(self.server.server_port)):
                return self.respond(403, {"error": "Cross-origin writes are disabled"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "Content-Type must be application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 100000:
                    raise ValueError("Request body exceeds limit")
                task = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(task, dict):
                    raise ValueError("Request must be a JSON object")
                if self.path == "/api/demo":
                    return self.respond(202, {"id": app.start()})
                if self.path == "/api/run":
                    return self.respond(202, {"id": app.start(task)})
                if self.path == "/api/cancel":
                    with app.lock:
                        if app.active_id:
                            app.runs[app.active_id].cancelled.set()
                    return self.respond(200, {})
                return self.respond(404, {"error": "Not found"})
            except (ValueError, KeyError, TypeError, OSError) as error:
                return self.respond(400, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(port=8765):
    server = make_server(port)
    print("Forge dashboard: http://127.0.0.1:" + str(server.server_port), flush=True)
    print("Local trusted-code runner. Scripted demo available without credentials. Ctrl-C stops the server.", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
