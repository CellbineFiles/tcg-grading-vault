import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
APP_DIR = Path(__file__).resolve().parent

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TCGGradingVault.LocalApp.0.93.2")
    except Exception:
        pass

# When launched via pythonw.exe on Windows, sys.stdout and sys.stderr are None.
if sys.stdout is None or sys.stderr is None:
    log_dir = APP_DIR / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_fh = open(log_dir / "app_run.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_fh
    if sys.stderr is None:
        sys.stderr = _log_fh

import uvicorn
from app import app as fastapi_app


def find_available_port(start_port: int = DEFAULT_PORT, max_tries: int = 20) -> int:
    for p in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    return start_port


def _find_chromium_app_browser() -> str | None:
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launch_and_watch_app(url: str, port: int, server: uvicorn.Server, use_browser_tab: bool = False):
    time.sleep(1.0)
    if not use_browser_tab:
        app_browser = _find_chromium_app_browser()
        if app_browser:
            profile_dir = Path(tempfile.gettempdir()) / f"tcg_grading_vault_app_profile_{port}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                t0 = time.monotonic()
                proc = subprocess.Popen([
                    app_browser,
                    f"--app={url}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ])
                proc.wait()
                elapsed = time.monotonic() - t0
                if elapsed < 4.0:
                    print(f"Browser launcher returned after {elapsed:.2f}s; keeping server running at {url}.")
                    return
                server.should_exit = True
                time.sleep(0.4)
                os._exit(0)
            except Exception:
                pass
    webbrowser.open(url)


if __name__ == "__main__":
    use_browser_tab = "--browser" in sys.argv
    port = find_available_port(DEFAULT_PORT)
    url = f"http://{HOST}:{port}"
    if port != DEFAULT_PORT:
        print(f"Note: Port {DEFAULT_PORT} was already in use by another process, so using port {port} instead.")
    print(f"Starting TCG Grading Vault at {url}")
    print("All data is stored locally in ./data/tcg_tracker.db and ./data/uploads/")
    print("Close the app window (or press Ctrl+C) to stop the server.\n")
    config = uvicorn.Config(fastapi_app, host=HOST, port=port, reload=False)
    server = uvicorn.Server(config)
    threading.Thread(target=launch_and_watch_app, args=(url, port, server, use_browser_tab), daemon=True).start()
    server.run()
