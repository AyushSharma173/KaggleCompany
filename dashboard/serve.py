"""Build dashboard data, watch for changes, and serve via local HTTP.

Runs as a long-lived process. A background thread polls for changes
in transcripts/ and state/ every few seconds and rebuilds when needed.
The web page polls for updates and re-renders.

Also serves REST API endpoints for the control dashboard:
  GET  /api/files?dir=constitutions   — list files in a directory
  GET  /api/file?path=constitutions/vp.md — read file content
  POST /api/file?path=constitutions/vp.md — write file content (body = raw content)
  GET  /api/workflow-config — read state/workflow_config.json
  POST /api/workflow-config — write state/workflow_config.json (body = JSON)
  GET  /api/tool-manifest — read state/tool_manifest.json
  GET  /api/agents — read agent state files
"""

import http.server
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import build  # local import — build.py is in same dir

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
WATCH_INTERVAL_SECONDS = float(os.environ.get("DASHBOARD_WATCH_INTERVAL", "3"))
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent
IN_DOCKER = Path("/.dockerenv").exists()

# Directories the control dashboard is allowed to read/write
ALLOWED_DIRS = {"constitutions", "skills", "strategies"}


def watcher_loop(stop_event: threading.Event) -> None:
    """Periodically check for transcript/state changes and rebuild."""
    last_mtime = -1.0
    while not stop_event.is_set():
        try:
            current = build.max_mtime()
            if current > last_mtime:
                if last_mtime < 0:
                    print(f"[watcher] Initial build...")
                else:
                    print(f"[watcher] Change detected, rebuilding...")
                build.build_all(verbose=False)
                last_mtime = current
                idx = build.OUTPUT / "index.json"
                if idx.exists():
                    size = idx.stat().st_size
                    print(f"[watcher] Built ({size:,} bytes)")
        except Exception as e:
            print(f"[watcher] Error during build: {e}", file=sys.stderr)
        # Sleep with check for stop_event so we can shut down cleanly
        for _ in range(int(WATCH_INTERVAL_SECONDS * 10)):
            if stop_event.is_set():
                return
            time.sleep(0.1)


def _validate_path(rel_path: str) -> Path | None:
    """Validate that a file path is within allowed directories. Returns
    the resolved Path or None if disallowed."""
    if not rel_path:
        return None
    # Normalize and check for traversal
    clean = Path(rel_path)
    if clean.is_absolute() or ".." in clean.parts:
        return None
    # Must be in an allowed directory
    if clean.parts[0] not in ALLOWED_DIRS:
        return None
    return PROJECT_ROOT / clean


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serves dashboard files + API endpoints for the control dashboard."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):
        # Quieter logging — only show errors and API calls
        if args and len(args) >= 2:
            try:
                code = int(str(args[1]))
                if code >= 400:
                    super().log_message(format, *args)
            except (ValueError, IndexError):
                pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    # --- GET API ---

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/files":
            return self._api_list_files(parsed)
        elif path == "/api/file":
            return self._api_read_file(parsed)
        elif path == "/api/workflow-config":
            return self._api_read_workflow_config()
        elif path == "/api/tool-manifest":
            return self._api_read_tool_manifest()
        elif path == "/api/agents":
            return self._api_read_agents()
        else:
            # Fall through to static file serving
            return super().do_GET()

    def _api_list_files(self, parsed):
        qs = parse_qs(parsed.query)
        dir_name = qs.get("dir", [None])[0]
        if not dir_name or dir_name not in ALLOWED_DIRS:
            return self._send_error_json(400, f"dir must be one of {sorted(ALLOWED_DIRS)}")
        target = PROJECT_ROOT / dir_name
        if not target.is_dir():
            return self._send_json({"dir": dir_name, "files": []})
        files = []
        for f in sorted(target.glob("*.md")):
            files.append({
                "name": f.stem,
                "filename": f.name,
                "path": f"{dir_name}/{f.name}",
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
        self._send_json({"dir": dir_name, "files": files})

    def _api_read_file(self, parsed):
        qs = parse_qs(parsed.query)
        rel_path = qs.get("path", [None])[0]
        resolved = _validate_path(rel_path)
        if not resolved:
            return self._send_error_json(400, "Invalid path")
        if not resolved.exists():
            return self._send_error_json(404, f"File not found: {rel_path}")
        content = resolved.read_text(encoding="utf-8")
        self._send_json({"path": rel_path, "content": content})

    def _api_read_workflow_config(self):
        wf_path = PROJECT_ROOT / "state" / "workflow_config.json"
        if not wf_path.exists():
            return self._send_json({})
        data = json.loads(wf_path.read_text())
        self._send_json(data)

    def _api_read_tool_manifest(self):
        tm_path = PROJECT_ROOT / "state" / "tool_manifest.json"
        if not tm_path.exists():
            return self._send_json([])
        data = json.loads(tm_path.read_text())
        self._send_json(data)

    def _api_read_agents(self):
        agents_dir = PROJECT_ROOT / "state" / "agents"
        agents = []
        if agents_dir.is_dir():
            for f in sorted(agents_dir.glob("*.json")):
                try:
                    agents.append(json.loads(f.read_text()))
                except Exception:
                    pass
        self._send_json({"agents": agents})

    # --- POST API ---

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/file":
            return self._api_write_file(parsed)
        elif path == "/api/workflow-config":
            return self._api_write_workflow_config()
        elif path == "/api/create-file":
            return self._api_create_file(parsed)
        elif path == "/api/rename-file":
            return self._api_rename_file(parsed)
        elif path == "/api/delete-file":
            return self._api_delete_file(parsed)
        else:
            self._send_error_json(404, "Not found")

    def _api_write_file(self, parsed):
        qs = parse_qs(parsed.query)
        rel_path = qs.get("path", [None])[0]
        resolved = _validate_path(rel_path)
        if not resolved:
            return self._send_error_json(400, "Invalid path")
        if not resolved.exists():
            return self._send_error_json(404, f"File not found: {rel_path}")
        body = self._read_body()
        content = body.decode("utf-8")
        resolved.write_text(content, encoding="utf-8")
        self._send_json({"path": rel_path, "saved": True, "size": len(content)})

    def _api_create_file(self, parsed):
        qs = parse_qs(parsed.query)
        rel_path = qs.get("path", [None])[0]
        resolved = _validate_path(rel_path)
        if not resolved:
            return self._send_error_json(400, "Invalid path")
        if resolved.exists():
            return self._send_error_json(409, f"File already exists: {rel_path}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        body = self._read_body()
        content = body.decode("utf-8")
        resolved.write_text(content, encoding="utf-8")
        self._send_json({"path": rel_path, "created": True, "size": len(content)})

    def _api_rename_file(self, parsed):
        qs = parse_qs(parsed.query)
        from_path = qs.get("from", [None])[0]
        to_path = qs.get("to", [None])[0]
        from_resolved = _validate_path(from_path)
        to_resolved = _validate_path(to_path)
        if not from_resolved or not to_resolved:
            return self._send_error_json(400, "Invalid path(s)")
        if not from_resolved.exists():
            return self._send_error_json(404, f"File not found: {from_path}")
        if to_resolved.exists():
            return self._send_error_json(409, f"Target already exists: {to_path}")
        # Must be in the same directory
        if from_resolved.parent != to_resolved.parent:
            return self._send_error_json(400, "Cannot rename across directories")
        from_resolved.rename(to_resolved)
        self._send_json({"renamed": True, "from": from_path, "to": to_path})

    def _api_delete_file(self, parsed):
        qs = parse_qs(parsed.query)
        rel_path = qs.get("path", [None])[0]
        resolved = _validate_path(rel_path)
        if not resolved:
            return self._send_error_json(400, "Invalid path")
        if not resolved.exists():
            return self._send_error_json(404, f"File not found: {rel_path}")
        resolved.unlink()
        self._send_json({"deleted": True, "path": rel_path})

    def _api_write_workflow_config(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return self._send_error_json(400, f"Invalid JSON: {e}")
        wf_path = PROJECT_ROOT / "state" / "workflow_config.json"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._send_json({"saved": True, "note": "Restart the system for changes to take effect."})


def main():
    # Initial build
    print("[serve] Initial build...")
    build.build_all(verbose=True)

    # Start the watcher thread
    stop_event = threading.Event()
    watcher = threading.Thread(target=watcher_loop, args=(stop_event,), daemon=True)
    watcher.start()

    # Serve from dashboard directory
    os.chdir(DASHBOARD_DIR)
    DashboardHandler.extensions_map.update({".js": "application/javascript"})

    bind_addr = "0.0.0.0" if IN_DOCKER else "127.0.0.1"

    try:
        with http.server.ThreadingHTTPServer((bind_addr, PORT), DashboardHandler) as httpd:
            url = f"http://localhost:{PORT}"
            print(f"\n[serve] Dashboard running at {url}")
            print(f"[serve] Control dashboard at {url}/control.html")
            print(f"[serve] Watching for changes every {WATCH_INTERVAL_SECONDS}s")
            print(f"[serve] Press Ctrl+C to stop.\n")

            # Open browser unless we're inside Docker
            if not IN_DOCKER:
                webbrowser.open(url)

            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] Shutting down.")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
