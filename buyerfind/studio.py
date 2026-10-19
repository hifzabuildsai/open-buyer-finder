"""Local research workbench. Browser requests never accept provider credentials."""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_KINDS = {"companies", "contacts", "sequences", "competitors", "triggers",
          "hubspot", "notion", "instantly"}
_MAX_BODY = 8192
_MAX_BUNDLE = 20_000_000
_ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
           "/app.js": ("app.js", "text/javascript; charset=utf-8"),
           "/style.css": ("style.css", "text/css; charset=utf-8")}


def _safe_readiness(config):
    """Expose only provider labels and presence checks, never configuration values."""
    rows = []
    for category, defaults in (("model", "ollama"), ("search", "searxng"),
                               ("enrichment", "public")):
        settings = config.get(category) or {}
        provider = settings.get("provider", defaults)
        allowed = {"ollama", "openai_compatible", "searxng", "brave", "public", "hunter"}
        label = provider if provider in allowed else "custom"
        env = settings.get("api_key_env", "")
        requires_key = provider in {"brave", "hunter"}
        has_key = bool(isinstance(env, str) and env and os.environ.get(env))
        # Compatible model gateways can be local and intentionally unauthenticated.
        note = ("API key missing" if requires_key and not has_key else
                "Public sources" if provider == "public" else
                "Configured · connection not tested")
        rows.append({"category": category, "provider": label,
                     "configured": not requires_key or has_key, "note": note})
    return rows


class StudioServer(ThreadingHTTPServer):
    """Thread-safe, one-job server. A research run is bounded by provider settings."""
    daemon_threads = True

    def __init__(self, config, workspace, port=8765):
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("Port must be an integer between 0 and 65535")
        self.config = copy.deepcopy(config)
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.job = None
        self.latest = None
        self.worker = None
        super().__init__(("127.0.0.1", port), StudioHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.host = f"127.0.0.1:{self.server_port}"

    def run_path(self, name):
        if not isinstance(name, str) or not _RUN_NAME.fullmatch(name):
            raise ValueError("Invalid run name")
        path = self.workspace / name
        if path.is_symlink() or path.resolve().parent != self.workspace:
            raise ValueError("Invalid run location")
        return path

    def read_bundle(self, name):
        path = self.run_path(name)
        with self.lock:
            if self.job and self.job["run_name"] == name and self.latest is not None:
                return copy.deepcopy(self.latest)
        file = path / "bundle.json"
        if file.is_symlink() or file.resolve().parent != path.resolve():
            raise ValueError("Invalid bundle location")
        if file.stat().st_size > _MAX_BUNDLE:
            raise ValueError("Bundle exceeds dashboard size limit")
        with file.open(encoding="utf-8") as stream:
            bundle = json.load(stream)
        if not isinstance(bundle, dict) or bundle.get("schema_version") != 2:
            raise ValueError("This run does not contain a v2 research bundle")
        return bundle

    def state(self):
        rows = []
        # Only direct child folders with the published bundle name are accessible.
        for path in sorted(self.workspace.iterdir(), reverse=True):
            if not _RUN_NAME.fullmatch(path.name) or not path.is_dir() or path.is_symlink():
                continue
            try:
                bundle = self.read_bundle(path.name)
            except (OSError, ValueError, TypeError):
                continue
            rows.append({"name": path.name, "product": (bundle.get("product") or {}).get("name")
                         or bundle.get("product_url") or path.name,
                         "mode": bundle.get("mode"), "status": bundle.get("status"),
                         "created_at": bundle.get("created_at")})
            if len(rows) >= 100:
                break
        with self.lock:
            job = copy.deepcopy(self.job)
            if job is not None:
                job["active"] = bool(self.worker and self.worker.is_alive())
        return {"runs": rows, "job": job, "providers": _safe_readiness(self.config),
                "limits": {key: value for key, value in (self.config.get("limits") or {}).items()
                           if key in {"companies", "contacts", "competitors", "search_requests",
                                      "model_calls", "pages", "domain_searches", "email_verifications"}
                           and isinstance(value, (int, float)) and not isinstance(value, bool)}}

    def start_job(self, url=None, demo=False):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("One research run is already active")
            name = ("demo-" if demo else "research-") + datetime.now(timezone.utc).strftime(
                "%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            output = self.run_path(name)
            self.job = {"run_name": name, "status": "running", "stage": "starting"}
            self.latest = None

            def progress(bundle):
                with self.lock:
                    self.latest = copy.deepcopy(bundle)
                    self.job.update(status=bundle.get("status", "running"),
                                    stage=bundle.get("stage", "researching"))

            def run():
                try:
                    # Lazy import lets the HTTP boundary be tested independently.
                    from .market import demo_bundle, run_pipeline, save_bundle
                    if demo:
                        bundle = demo_bundle()
                        save_bundle(output, bundle)
                    else:
                        bundle = run_pipeline(url, self.config, output, progress=progress)
                    progress(bundle)
                except Exception:
                    # Provider exceptions can contain credential-bearing request URLs.
                    with self.lock:
                        message = "Research stopped unexpectedly. Inspect the saved checkpoint and local configuration."
                        self.job.update(status="failed", stage="stopped",
                                        error=message)
                        if self.latest is not None:
                            self.latest.update(status="failed", stage="stopped")
                            self.latest.setdefault("warnings", []).append(message)

            self.worker = threading.Thread(target=run, name="buyerfind-research", daemon=True)
            self.worker.start()
            return copy.deepcopy(self.job)


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "BuyerFinderStudio"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # URLs and user inputs need not become a second local prospect database.
        pass

    def _headers(self, status, mime, size, disposition=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()

    def _response(self, status, body, mime="application/json; charset=utf-8", disposition=None):
        data = (json.dumps(body, ensure_ascii=False).encode("utf-8") if isinstance(body, (dict, list))
                else body.encode("utf-8") if isinstance(body, str) else body)
        self._headers(status, mime, len(data), disposition)
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _host_ok(self):
        hosts = self.headers.get_all("Host", [])
        expected = {self.server.host}
        if self.server.server_port == 80:
            expected.add("127.0.0.1")
        if len(hosts) != 1 or hosts[0] not in expected:
            self._response(403, {"error": "Use the exact local dashboard address printed by the CLI"})
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        if parsed.scheme or parsed.netloc:
            return self._response(400, {"error": "Absolute request targets are not supported"})
        try:
            if path in _ASSETS:
                name, mime = _ASSETS[path]
                data = (Path(__file__).with_name("web") / name).read_text(encoding="utf-8")
                if path == "/":
                    data = data.replace("__STUDIO_TOKEN__", self.server.token)
                return self._response(200, data, mime)
            if path == "/api/state":
                return self._response(200, self.server.state())
            match = re.fullmatch(r"/api/runs/([A-Za-z0-9][A-Za-z0-9_-]{0,79})(/export)?", path)
            if match:
                name, exporting = match.groups()
                bundle = self.server.read_bundle(name)
                if not exporting:
                    return self._response(200, bundle)
                query = parse_qs(parsed.query)
                if set(query) != {"kind"} or len(query["kind"]) != 1 or query["kind"][0] not in _KINDS:
                    return self._response(400, {"error": "Choose a supported CSV kind"})
                kind = query["kind"][0]
                from .exports import export_csv
                data = export_csv(bundle, kind)
                return self._response(200, data, "text/csv; charset=utf-8",
                                      f'attachment; filename="{name}-{kind}.csv"')
            self._response(404, {"error": "Not found"})
        except FileNotFoundError:
            self._response(404, {"error": "Research bundle not found"})
        except (OSError, ValueError, TypeError):
            self._response(400, {"error": "Research bundle is unavailable or invalid"})

    def do_POST(self):
        if not self._host_ok():
            return
        origins = self.headers.get_all("Origin", [])
        tokens = self.headers.get_all("X-Studio-Token", [])
        if (origins != [self.server.origin] or len(tokens) != 1 or
                not secrets.compare_digest(tokens[0].encode("utf-8"), self.server.token.encode("utf-8"))):
            return self._response(403, {"error": "Open the local dashboard to start research"})
        if self.path not in {"/api/research", "/api/demo"}:
            return self._response(404, {"error": "Not found"})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._response(415, {"error": "Use JSON request data"})
        sizes = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(sizes) != 1 or not sizes[0].isdigit():
            return self._response(400, {"error": "A single bounded Content-Length is required"})
        size = int(sizes[0])
        if not 1 <= size <= _MAX_BODY:
            return self._response(413, {"error": "Request body exceeds the dashboard limit"})
        try:
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            demo = self.path == "/api/demo"
            if set(payload) != (set() if demo else {"url"}):
                raise ValueError("Only a product URL is accepted; configure providers in the local settings file")
            url = payload.get("url")
            if not demo:
                if not isinstance(url, str) or not 1 <= len(url) <= 2048:
                    raise ValueError("Enter a product website URL")
                parts = urlsplit(url)
                if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                    raise ValueError("Use a public HTTP or HTTPS product website URL without credentials")
            job = self.server.start_job(url=url, demo=demo)
            self._response(202, job)
        except RuntimeError:
            self._response(409, {"error": "One research run is already active"})
        except (ValueError, UnicodeError):
            self._response(400, {"error": "Invalid request. Enter a public product URL; credentials belong in local configuration."})
        except (OSError, TimeoutError):
            self._response(408, {"error": "Request could not be read"})


def create_server(config, workspace, port=8765):
    """Construct without starting the loop; useful for embedding and HTTP tests."""
    return StudioServer(config, workspace, port)


def serve(config, workspace, port=8765):
    server = create_server(config, workspace, port)
    print(f"Open Buyer Finder Studio: {server.origin}", flush=True)
    print("Local research only. Provider usage follows your settings. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
