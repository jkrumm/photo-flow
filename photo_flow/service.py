"""
LaunchAgent management for the Photo-Flow control panel.

Renders and installs a macOS LaunchAgent that keeps `photoflow serve` running at
http://127.0.0.1:7720. Paths are resolved dynamically (works for venv or pipx
installs) rather than hardcoded, so a moved repo or different interpreter still
produces a valid plist.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from photo_flow.console_utils import success, error, info, warning

LABEL = "com.jkrumm.photoflow"
PLIST_NAME = f"{LABEL}.plist"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7720

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "control_panel" / "web"
WEB_DIST = WEB_DIR / "dist"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / PLIST_NAME
STDOUT_LOG = "/tmp/photoflow.log"
STDERR_LOG = "/tmp/photoflow.err"
DAEMON_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def _photoflow_executable() -> str:
    """Resolve the absolute path to the `photoflow` console script.

    Prefers the script next to the running interpreter (venv/pipx bin dir),
    falling back to PATH lookup.
    """
    candidate = Path(sys.executable).with_name("photoflow")
    if candidate.exists():
        return str(candidate)
    found = shutil.which("photoflow")
    if found:
        return found
    raise RuntimeError(
        "Could not locate the 'photoflow' executable next to the interpreter or on PATH."
    )


def render_plist(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """Render the LaunchAgent plist XML with dynamically resolved paths."""
    exe = _photoflow_executable()
    home = os.path.expanduser("~")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{exe}</string>
    <string>serve</string>
    <string>--host</string>
    <string>{host}</string>
    <string>--port</string>
    <string>{port}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{REPO_ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>{home}</string>
    <key>PATH</key>
    <string>{DAEMON_PATH}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{STDOUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>{STDERR_LOG}</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def _build_spa() -> bool:
    """Build the SPA so FastAPI has a `dist/` to serve. Returns success."""
    if not (WEB_DIR / "package.json").exists():
        error(f"SPA project not found at {WEB_DIR}")
        return False
    npm = shutil.which("npm")
    if not npm:
        error("npm not found — install Node.js to build the control panel.")
        return False
    if not (WEB_DIR / "node_modules").exists():
        info("Installing web dependencies (npm install)…")
        subprocess.run([npm, "install"], cwd=WEB_DIR, check=True)
    info("Building the control panel SPA (npm run build)…")
    subprocess.run([npm, "run", "build"], cwd=WEB_DIR, check=True)
    return True


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def install(build: bool = True, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Render + install the LaunchAgent and (re)load it. Idempotent."""
    if build:
        if not _build_spa():
            error("Aborting install — SPA build failed.")
            return
    elif not WEB_DIST.exists():
        warning(f"No built SPA at {WEB_DIST} — run with build enabled or `npm run build` first.")

    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(render_plist(host, port))
    info(f"Wrote LaunchAgent → {PLIST_PATH}")

    # Reload: unload an existing instance (ignore errors), then load fresh.
    _launchctl("unload", str(PLIST_PATH))
    result = _launchctl("load", "-w", str(PLIST_PATH))
    if result.returncode != 0:
        error(f"launchctl load failed: {result.stderr.strip()}")
        return

    success("Control panel service installed and started.")
    info(f"Open [cyan]http://localhost:{port}[/cyan] (logs: {STDOUT_LOG} / {STDERR_LOG})")


def uninstall() -> None:
    """Unload and remove the LaunchAgent."""
    if not PLIST_PATH.exists():
        warning("LaunchAgent is not installed — nothing to do.")
        return
    _launchctl("unload", str(PLIST_PATH))
    PLIST_PATH.unlink(missing_ok=True)
    success("Control panel service uninstalled.")


def status() -> None:
    """Report whether the LaunchAgent is installed and running."""
    if not PLIST_PATH.exists():
        warning(f"Not installed (no {PLIST_PATH}). Run `photoflow service install`.")
        return
    info(f"Plist: {PLIST_PATH}")
    result = _launchctl("list")
    line = next((ln for ln in result.stdout.splitlines() if LABEL in ln), None)
    if line:
        # Columns: PID  Status  Label
        pid = line.split("\t")[0] if "\t" in line else line.split()[0]
        if pid not in ("-", ""):
            success(f"Running (PID {pid}).")
        else:
            warning("Loaded but not currently running (check /tmp/photoflow.err).")
    else:
        warning("Installed but not loaded. Run `photoflow service install` to (re)load.")


def restart() -> None:
    """Reload the LaunchAgent (picks up a new SPA build / code change)."""
    if not PLIST_PATH.exists():
        warning("Not installed — run `photoflow service install` first.")
        return
    _launchctl("unload", str(PLIST_PATH))
    result = _launchctl("load", "-w", str(PLIST_PATH))
    if result.returncode != 0:
        error(f"launchctl load failed: {result.stderr.strip()}")
        return
    success("Control panel service restarted.")
