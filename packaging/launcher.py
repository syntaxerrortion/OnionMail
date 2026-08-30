#!/usr/bin/env python3
"""onionmail masaüstü istemcisi — gömülü Tor ile tek parça başlatıcı.

PyInstaller ile paketlendiğinde kendi `tor(.exe)`'sini başlatır, devre
kurulunca onionmail GUI'sini açar. Kullanıcıda Python veya Tor kurulu
olmasına gerek yoktur.

Geliştirmede (paketlenmemişken): sistemdeki `tor`u ya da zaten açık bir
SOCKS'u kullanır; yoksa Tor Browser'ın 9150'sine düşer.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCKS_PORT = 9250          # bu uygulamanın kendi Tor'u
TOR_BROWSER_PORT = 9150    # yedek: kullanıcıda Tor Browser açıksa


def _res_dir() -> Path:
    """Paket içi kaynak dizini (PyInstaller: sys._MEIPASS)."""
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
    """(süreç, socks_port) döndürür. Süreç None ise zaten çalışan biri var."""
    if _port_open("127.0.0.1", SOCKS_PORT):
        return None, SOCKS_PORT
    tor = _tor_binary()
    if not tor:
        if _port_open("127.0.0.1", TOR_BROWSER_PORT):
            print("gömülü tor yok — Tor Browser (9150) kullanılıyor")
            return None, TOR_BROWSER_PORT
        print("UYARI: tor bulunamadı ve Tor Browser açık değil")
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
            print("tor beklenmedik şekilde kapandı")
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
    print("tor bootstrap zaman aşımı — yine de denenecek")
    return proc, SOCKS_PORT


def _seed_client_json(socks_port: int) -> None:
    """İlk çalıştırmada, paketle gelen şablondan sunucu onion'unu yaz."""
    try:
        from onionmail.config import Config
        cfg = Config.load()
        target = cfg.client.session_path_p.with_name("client.json")
    except Exception:  # noqa: BLE001
        target = Path.home() / ".config" / "onionmail" / "client.json"
    if target.is_file():
        return
    tmpl = _res_dir() / "client.default.json"
    if not tmpl.is_file():
        return
    try:
        d = json.loads(tmpl.read_text())
    except ValueError:
        return
    if not str(d.get("server_onion", "")).endswith(".onion"):
        return  # şablon boş — kullanıcı GUI'de girecek
    d["socks_host"] = "127.0.0.1"
    d["socks_port"] = socks_port
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(d, indent=2))
    try:
        target.chmod(0o600)
    except OSError:
        pass


def main() -> int:
    tor_proc, socks_port = start_tor()
    try:
        _seed_client_json(socks_port)
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
