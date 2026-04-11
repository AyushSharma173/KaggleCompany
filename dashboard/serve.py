"""Build dashboard data, watch for changes, and serve via local HTTP.

Runs as a long-lived process. A background thread polls for changes
in transcripts/ and state/ every few seconds and rebuilds when needed.
The web page polls for updates and re-renders.
"""

import http.server
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import build  # local import — build.py is in same dir

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
WATCH_INTERVAL_SECONDS = float(os.environ.get("DASHBOARD_WATCH_INTERVAL", "3"))
DASHBOARD_DIR = Path(__file__).resolve().parent
IN_DOCKER = Path("/.dockerenv").exists()


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


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Serves dashboard files with cache disabled."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):
        # Quieter logging — only show errors
        if args and len(args) >= 2:
            try:
                code = int(str(args[1]))
                if code >= 400:
                    super().log_message(format, *args)
            except (ValueError, IndexError):
                pass


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
    NoCacheHandler.extensions_map.update({".js": "application/javascript"})

    bind_addr = "0.0.0.0" if IN_DOCKER else "127.0.0.1"

    try:
        with http.server.ThreadingHTTPServer((bind_addr, PORT), NoCacheHandler) as httpd:
            url = f"http://localhost:{PORT}"
            print(f"\n[serve] Dashboard running at {url}")
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
