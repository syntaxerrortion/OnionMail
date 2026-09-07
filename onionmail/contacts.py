"""Contact book — nickname → onion address mapping (local, client-side).

Typing/remembering 56-character onion addresses by hand is tedious; this
module keeps a simple nickname directory in `~/.config/onionmail/contacts.json`.
It is independent of `ClientKeys` (TOFU encryption keys) — it works even
without pyrage installed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .config import is_onion_address

_NICK_RE = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_ .-]{0,30}[A-Za-z0-9_])?$")


class ContactError(Exception):
    """Invalid nickname / address."""


class Contacts:
    def __init__(self, config_dir: Path):
        self.path = Path(config_dir) / "contacts.json"

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def add(self, nick: str, address: str, note: str = "") -> None:
        nick = nick.strip()
        address = address.strip().lower()
        if not nick or not _NICK_RE.match(nick):
            raise ContactError(
                "invalid nickname (letters/digits/underscore/space, no comma)")
        if not is_onion_address(address):
            raise ContactError("invalid .onion address")
        data = self._read()
        data[nick.lower()] = {
            "nick": nick, "address": address, "note": note.strip(),
            "added": int(time.time()),
        }
        self._write(data)

    def remove(self, nick: str) -> None:
        data = self._read()
        if data.pop(nick.strip().lower(), None) is not None:
            self._write(data)

    def get(self, nick: str) -> dict | None:
        return self._read().get(nick.strip().lower())

    def all(self) -> dict:
        return self._read()

    def resolve(self, text: str) -> str:
        """If it is a known nickname, return its address; otherwise leave the
        text as-is (it may already be an onion address)."""
        t = text.strip()
        rec = self.get(t)
        return rec["address"] if rec else t

    def resolve_list(self, addrs: list[str]) -> list[str]:
        return [self.resolve(a) for a in addrs]

    def find_by_address(self, address: str) -> str | None:
        """Find the nickname for an onion address (if any)."""
        address = address.strip().lower()
        for rec in self._read().values():
            if rec.get("address") == address:
                return rec.get("nick")
        return None
