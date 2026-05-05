"""ClosetMind desktop launcher.

This is the entry point used when ClosetMind is packaged via PyInstaller
into a double-clickable .app / .exe. It does three things:

  1. Find a free TCP port (so two instances on the same machine don't
     fight over a fixed port).
  2. Start uvicorn in a background thread, bound to 127.0.0.1 only —
     never exposed to the LAN. The desktop bundle is single-user-on-this
     -machine; remote access would defeat the local-first privacy
     model.
  3. Open the user's default browser pointing at the new server. Uses
     a small delay so the server is actually accepting connections
     before the browser tries to load.

The launcher blocks on the uvicorn thread so closing the terminal /
killing the .app process actually shuts down the server.

Frozen-mode considerations (PyInstaller-specific):
  - When run via `python launcher.py` in dev, BASE_DIR is the project
    root and `from app.main import app` works via the normal Python
    package import.
  - When frozen, sys._MEIPASS points at the unpacked bundle dir.
    PyInstaller's `runtime_hooks` / `datas` config (see closetmind.spec)
    ensures app/static/ and app/templates/ are present at that root,
    so Jinja2 + StaticFiles paths in app/main.py resolve correctly.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _find_free_port(preferred_range=(18888, 18999)) -> int:
    """Try a fixed range first (so user's browser history / bookmarks
    sometimes hit), then fall back to OS-assigned random port."""
    lo, hi = preferred_range
    for port in range(lo, hi + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
            continue
    # Fall back to whatever the OS hands us.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_server(port: int, timeout_s: float = 10.0) -> bool:
    """Poll the port until it accepts connections, then return True.
    Returns False if we time out (unusual — uvicorn boots in <1s)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main():
    # Frozen builds don't have a writable cwd by default — make sure
    # ~/.closetmind/ resolution still works (it's based on Path.home()
    # which is fine in frozen mode).
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"[ClosetMind] starting on {url}")

    # Importing here (not at module top) so PyInstaller's static analysis
    # picks up the dependency tree but the import time is amortized into
    # server startup rather than launcher startup.
    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",  # quieter — desktop user doesn't care about HTTP logs
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Run uvicorn in a daemon thread so it dies when main() returns
    # (e.g. user closes the terminal window the bundle launched into).
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if _wait_for_server(port):
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"[ClosetMind] could not auto-open browser: {e}")
            print(f"[ClosetMind] open manually: {url}")
    else:
        print(f"[ClosetMind] server didn't start within timeout — open manually: {url}")

    # Block on the uvicorn thread. Ctrl+C in the terminal cleanly shuts it down.
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\n[ClosetMind] shutting down...")
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
