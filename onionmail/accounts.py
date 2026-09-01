"""Server-side account store: usernames, argon2id password hashes, invite codes.

One JSON file (`accounts.json`) under the account root; each account also owns a
Maildir at ``<root>/<user>/Maildir``. All read-modify-write goes through a lock
and an atomic replace, so the threaded `apid` and the `smtpd`/`sender` daemons
can share it.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from .store import Store

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
_PH = PasswordHasher()  # argon2id defaults


class AccountError(Exception):
    pass


@dataclass(slots=True)
class Policy:
    open_registration: bool = False
    max_failed: int = 5
    lock_minutes: int = 15
    min_password_len: int = 8


class Accounts:
    def __init__(self, root: Path, policy: Policy | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy or Policy()
        self._path = self.root / "accounts.json"
        self._lock = threading.Lock()
        if not self._path.is_file():
            self._write({"users": {}, "invites": {}})

    # -- persistence -----------------------------------------------------
    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text() or "{}")
        except ValueError:
            return {"users": {}, "invites": {}}

    def _write(self, data: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.chmod(0o600)
        tmp.replace(self._path)

    # -- queries ------------------------------------------------------
    def exists(self, user: str) -> bool:
        return user in self._read()["users"]

    def list_users(self) -> list[str]:
        return sorted(self._read()["users"])

    def maildir_for(self, user: str) -> Path:
        return self.root / user / "Maildir"

    def store_for(self, user: str) -> Store:
        if not self.exists(user):
            raise AccountError(f"no such account: {user}")
        return Store(self.maildir_for(user))

    # -- invites ----------------------------------------------------
    def new_invite(self) -> str:
        code = secrets.token_urlsafe(9)
        with self._lock:
            data = self._read()
            data["invites"][code] = {"created": int(time.time()), "used_by": None}
            self._write(data)
        return code

    def list_invites(self) -> dict:
        return self._read()["invites"]

    # -- account lifecycle -------------------------------------------
    def create(self, user: str, password: str, invite: str | None) -> None:
        user = user.strip().lower()
        if not USERNAME_RE.match(user):
            raise AccountError("geçersiz kullanıcı adı (a-z 0-9 . _ -, 2-32 karakter)")
        if len(password) < self.policy.min_password_len:
            raise AccountError(f"şifre en az {self.policy.min_password_len} karakter olmalı")
        with self._lock:
            data = self._read()
            if user in data["users"]:
                raise AccountError("bu kullanıcı adı alınmış")
            if not self.policy.open_registration:
                inv = data["invites"].get(invite or "")
                if inv is None or inv["used_by"] is not None:
                    raise AccountError("geçersiz veya kullanılmış davet kodu")
            data["users"][user] = {
                "pw_hash": _PH.hash(password),
                "created": int(time.time()),
                "failed": 0,
                "locked_until": 0,
            }
            if not self.policy.open_registration:
                data["invites"][invite]["used_by"] = user
            self._write(data)
        # create the mailbox outside the lock
        Store(self.maildir_for(user))

    def verify(self, user: str, password: str) -> bool:
        user = user.strip().lower()
        with self._lock:
            data = self._read()
            rec = data["users"].get(user)
            if rec is None:
                _PH.hash(password)  # constant-ish work to blur user enumeration
                return False
            now = time.time()
            if rec.get("locked_until", 0) > now:
                raise AccountError("hesap geçici olarak kilitli, sonra tekrar dene")
            try:
                _PH.verify(rec["pw_hash"], password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                rec["failed"] = rec.get("failed", 0) + 1
                if rec["failed"] >= self.policy.max_failed:
                    rec["locked_until"] = int(now + self.policy.lock_minutes * 60)
                    rec["failed"] = 0
                self._write(data)
                return False
            changed = rec.get("failed") or rec.get("locked_until")
            rec["failed"] = 0
            rec["locked_until"] = 0
            if _PH.check_needs_rehash(rec["pw_hash"]):
                rec["pw_hash"] = _PH.hash(password)
                changed = True
            if changed:
                self._write(data)
            return True

    def set_password(self, user: str, new_password: str) -> None:
        user = user.strip().lower()
        if len(new_password) < self.policy.min_password_len:
            raise AccountError(f"şifre en az {self.policy.min_password_len} karakter olmalı")
        with self._lock:
            data = self._read()
            if user not in data["users"]:
                raise AccountError(f"no such account: {user}")
            data["users"][user]["pw_hash"] = _PH.hash(new_password)
            data["users"][user]["failed"] = 0
            data["users"][user]["locked_until"] = 0
            self._write(data)

    def delete(self, user: str) -> None:
        with self._lock:
            data = self._read()
            data["users"].pop(user, None)
            self._write(data)

    # -- age açık anahtar dizini (uçtan uca şifreleme) ---------------
    def set_pubkey(self, user: str, pubkey: str) -> None:
        user = user.strip().lower()
        with self._lock:
            data = self._read()
            if user not in data["users"]:
                raise AccountError(f"no such account: {user}")
            data["users"][user]["age_pubkey"] = pubkey.strip()
            self._write(data)

    def get_pubkey(self, user: str) -> str | None:
        rec = self._read()["users"].get(user.strip().lower())
        return (rec or {}).get("age_pubkey") or None
