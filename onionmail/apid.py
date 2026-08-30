"""`apid` — the account / mail API daemon.

Listens on loopback; Tor publishes it on a second onion port. Speaks the
line-delimited JSON protocol in `protocol.py`. Clients register (invite-gated),
log in for a short-lived session token, then read and send mail through it.
Outbound mail is dropped into the account's queue; the separate `sender` daemon
delivers it over Tor.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import socketserver
import threading
import time
from dataclasses import asdict

from . import protocol as P
from .accounts import AccountError, Accounts, Policy
from .config import Config
from .compose import recipients_of
from .config import is_onion_address

log = logging.getLogger("onionmail.apid")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, cfg: Config, accounts: Accounts):
        super().__init__((cfg.api.host, cfg.api.port), _Handler)
        self.cfg = cfg
        self.accounts = accounts
        self.onion = cfg.identity.resolve_onion()
        self.psk = cfg.api.preshared_key
        self.ttl = cfg.accounts.session_ttl
        self._sessions: dict[str, tuple[str, float]] = {}  # token -> (user, expiry)
        self._slock = threading.Lock()
        self._sfile = cfg.accounts.store_path_p / "sessions.json"
        self._load_sessions()

    # session persistence (apid yeniden başlasa/sunucu reboot olsa da jeton kalsın)
    def _load_sessions(self) -> None:
        try:
            raw = json.loads(self._sfile.read_text())
        except (OSError, ValueError):
            return
        now = time.time()
        self._sessions = {
            t: (u, e) for t, (u, e) in raw.items()
            if isinstance(e, (int, float)) and e > now
        }

    def _save_sessions(self) -> None:
        """`_slock` tutulurken çağrılır."""
        try:
            self._sfile.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._sfile.parent / (self._sfile.name + ".tmp")
            tmp.write_text(json.dumps(self._sessions))
            os.chmod(tmp, 0o600)
            tmp.replace(self._sfile)
        except OSError as e:  # noqa: BLE001
            log.warning("oturum dosyası yazılamadı: %s", e)

    # session helpers ---------------------------------------------------
    def new_session(self, user: str) -> tuple[str, float]:
        token = secrets.token_urlsafe(24)
        exp = time.time() + self.ttl
        with self._slock:
            self._sessions[token] = (user, exp)
            self._save_sessions()
        return token, exp

    def session_user(self, token: str) -> str | None:
        with self._slock:
            rec = self._sessions.get(token or "")
            if not rec:
                return None
            user, exp = rec
            if exp < time.time():
                self._sessions.pop(token, None)
                self._save_sessions()
                return None
            return user

    def drop_session(self, token: str) -> None:
        with self._slock:
            if self._sessions.pop(token or "", None) is not None:
                self._save_sessions()


class _Handler(socketserver.StreamRequestHandler):
    timeout = 120

    @property
    def srv(self) -> _Server:
        return self.server  # type: ignore[return-value]

    def handle(self) -> None:
        peer = self.client_address
        try:
            while True:
                try:
                    req = P.read_msg(self.rfile)
                except P.ProtocolError:
                    return
                resp = self._dispatch(req)
                P.write_msg(self.wfile, resp)
        except (ConnectionError, OSError):
            return
        except Exception:  # noqa: BLE001
            log.exception("handler crash from %s", peer)
            try:
                P.write_msg(self.wfile, P.err("internal error"))
            except OSError:
                pass

    # -- dispatch -------------------------------------------------------
    def _dispatch(self, req: dict) -> dict:
        op = req.get("op")
        if self.srv.psk and not secrets.compare_digest(str(req.get("psk", "")), self.srv.psk):
            return P.err("unauthorized")

        if op == P.OP_PING:
            return P.ok(server="onionmail", version=P.PROTOCOL_VERSION,
                        onion=self.srv.onion,
                        open_registration=self.srv.cfg.accounts.open_registration)
        if op == P.OP_REGISTER:
            return self._register(req)
        if op == P.OP_LOGIN:
            return self._login(req)

        if op in P.AUTHED_OPS:
            user = self.srv.session_user(str(req.get("token", "")))
            if user is None:
                return P.err("not authenticated")
            return self._authed(op, user, req)

        return P.err(f"unknown op: {op!r}")

    # -- unauthenticated ------------------------------------------------
    def _register(self, req: dict) -> dict:
        try:
            self.srv.accounts.create(
                str(req.get("user", "")), str(req.get("password", "")),
                req.get("invite"),
            )
        except AccountError as e:
            time.sleep(0.5)  # gentle throttle
            return P.err(str(e))
        return P.ok()

    def _login(self, req: dict) -> dict:
        user = str(req.get("user", ""))
        try:
            good = self.srv.accounts.verify(user, str(req.get("password", "")))
        except AccountError as e:
            return P.err(str(e))
        if not good:
            time.sleep(0.5)
            return P.err("kullanıcı adı veya şifre hatalı")
        token, exp = self.srv.new_session(user.strip().lower())
        onion = self.srv.onion
        return P.ok(token=token, expires=int(exp),
                    address=f"{user.strip().lower()}@{onion}", onion=onion)

    # -- authenticated ------------------------------------------------
    def _authed(self, op: str, user: str, req: dict) -> dict:
        if op == P.OP_LOGOUT:
            self.srv.drop_session(str(req.get("token", "")))
            return P.ok()

        store = self.srv.accounts.store_for(user)

        if op == P.OP_FOLDERS:
            from .store import FOLDERS
            return P.ok(folders=[
                {"name": f, "count": len(store.list(f))} for f in FOLDERS
            ])

        if op == P.OP_LIST:
            folder = str(req.get("folder", "INBOX"))
            return P.ok(messages=[asdict(s) for s in store.list(folder)])

        if op == P.OP_FETCH:
            raw = store.get_bytes(str(req["folder"]), str(req["key"]))
            return P.ok(raw=base64.b64encode(raw).decode("ascii"))

        if op == P.OP_SEND:
            try:
                raw = base64.b64decode(str(req["raw"]), validate=True)
            except (binascii.Error, KeyError):
                return P.err("bad raw payload")
            rcpts = [str(r).strip().lower() for r in req.get("rcpts", []) if str(r).strip()]
            if not rcpts:
                # fall back to To/Cc headers in the message itself
                from email import policy as epol
                from email.parser import BytesParser
                msg = BytesParser(policy=epol.default).parsebytes(raw)
                rcpts = recipients_of(msg)  # type: ignore[arg-type]
            bad = [r for r in rcpts if not is_onion_address(r)]
            if bad:
                return P.err("alıcı(lar) .onion değil: " + ", ".join(bad))
            store.enqueue(raw, f"{user}@{self.srv.onion}", rcpts)
            return P.ok(queued=len(rcpts))

        if op == P.OP_DELETE:
            store.delete(str(req["folder"]), str(req["key"]))
            return P.ok()

        if op == P.OP_MOVE:
            new_key = store.move(str(req["src"]), str(req["key"]), str(req["dst"]))
            return P.ok(key=new_key)

        if op == P.OP_MARK_SEEN:
            store.mark_seen(str(req["folder"]), str(req["key"]), bool(req.get("seen", True)))
            return P.ok()

        return P.err(f"unhandled op: {op}")


def build_server(cfg: Config) -> _Server:
    accounts = Accounts(
        cfg.accounts.store_path_p,
        Policy(
            open_registration=cfg.accounts.open_registration,
            max_failed=cfg.accounts.max_failed,
            lock_minutes=cfg.accounts.lock_minutes,
            min_password_len=cfg.accounts.min_password_len,
        ),
    )
    return _Server(cfg, accounts)


def run(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not cfg.accounts.enabled:
        log.warning("accounts.enabled is false in config — starting apid anyway")
    srv = build_server(cfg)
    log.info("apid listening on %s:%d  onion=%s  open_registration=%s",
             cfg.api.host, cfg.api.port, srv.onion or "(unknown)",
             cfg.accounts.open_registration)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        log.info("apid stopped")
