#!/usr/bin/env python3
"""onionmail desktop client — single-file launcher with a bundled Tor.

When packaged with PyInstaller it starts its own `tor(.exe)` and, once the
circuit is up, opens the onionmail GUI. The user needs neither Python nor
Tor installed.

In development (unpackaged): uses the system `tor` or an already-open SOCKS;
otherwise falls back to Tor Browser's 9150.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCKS_PORT = 9250          # this app's own Tor
TOR_BROWSER_PORT = 9150    # fallback: if the user has Tor Browser open


def _res_dir() -> Path:
    """In-package resource directory (PyInstaller: sys._MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _tor_binary() -> Path | None:
    name = "tor.exe" if os.name == "nt" else "tor"
    bundled = _res_dir() / "tor" / name
    if bundled.is_file():
        return bundled
    found = shutil.which("tor")
    return Path(found) if found else None


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "onionmail" / "tor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_tor() -> tuple[subprocess.Popen | None, int]:
    """Returns (process, socks_port). If process is None, one is already running."""
    if _port_open("127.0.0.1", SOCKS_PORT):
        return None, SOCKS_PORT
    tor = _tor_binary()
    if not tor:
        if _port_open("127.0.0.1", TOR_BROWSER_PORT):
            print("no bundled tor — using Tor Browser (9150)")
            return None, TOR_BROWSER_PORT
        print("WARNING: tor not found and Tor Browser is not open")
        return None, SOCKS_PORT
    dd = _state_dir()
    torrc = dd / "torrc"
    torrc.write_text(
        f"SocksPort 127.0.0.1:{SOCKS_PORT}\n"
        f"DataDirectory {dd}\n"
        "ControlPort 0\n"
        "AvoidDiskWrites 1\n"
    )
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [str(tor), "-f", str(torrc)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            print("tor exited unexpectedly")
            return None, SOCKS_PORT
        line = (proc.stdout.readline() or "").strip()
        if "Bootstrapped" in line:
            print(line)
            if "Bootstrapped 100%" in line:
                return proc, SOCKS_PORT
        elif not line and _port_open("127.0.0.1", SOCKS_PORT):
            time.sleep(2)
            return proc, SOCKS_PORT
        elif not line:
            time.sleep(0.4)
    print("tor bootstrap timed out — will try anyway")
    return proc, SOCKS_PORT


def main() -> int:
    tor_proc, socks_port = start_tor()
    try:
        from onionmail.config import Config
        from onionmail.gui import run as gui_run

        cfg = Config.load()
        cfg.sender.socks_host = "127.0.0.1"
        cfg.sender.socks_port = socks_port
        try:
            gui_run(cfg)
        except SystemExit:
            pass
        return 0
    finally:
        if tor_proc and tor_proc.poll() is None:
            tor_proc.terminate()
            try:
                tor_proc.wait(5)
            except subprocess.TimeoutExpired:
                tor_proc.kill()


if __name__ == "__main__":
    sys.exit(main())
