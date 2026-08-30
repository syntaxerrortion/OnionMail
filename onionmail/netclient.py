"""Client side of the `apid` protocol — used by the desktop app's NetBackend.

Every call opens a fresh connection through Tor's SOCKS5 proxy to
``<server_onion>:<api_port>``, sends one request line, reads one response line,
closes. Stateless per call except for the in-memory session token kept after
``login()``.
"""

from __future__ import annotations

import base64
import socket

import socks  # PySocks

from . import protocol as P

DEFAULT_API_PORT = 8443


class NetError(Exception):
    pass


class AuthError(NetError):
    pass


class NetClient:
    def __init__(self, server_onion: str, *, socks_host="127.0.0.1", socks_port=9050,
                 preshared_key="", timeout=120, api_port=DEFAULT_API_PORT,
                 _direct: tuple[str, int] | None = None):
        self.server_onion = server_onion.strip().lower()
        self.socks_host, self.socks_port = socks_host, socks_port
        self.psk = preshared_key
        self.timeout = timeout
        self.api_port = api_port
        self._direct = _direct          # (host, port) — bypass Tor, for tests
        self.token: str | None = None
        self.token_expires: int = 0
        self.address: str = ""
        self.onion: str = ""

    # -- transport ---------------------------------------------------
    def _connect(self) -> socket.socket:
        if self._direct is not None:
            s = socket.create_connection(self._direct, timeout=self.timeout)
            return s
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, self.socks_host, self.socks_port, rdns=True)
        s.settimeout(self.timeout)
        try:
            s.connect((self.server_onion, self.api_port))
        except (socks.ProxyConnectionError, socks.GeneralProxyError) as e:
            raise NetError(f"sunucuya ulaşılamadı ({e})") from e
        return s

    def _rpc(self, op: str, **fields) -> dict:
        req = {"op": op, **fields}
        if self.psk:
            req["psk"] = self.psk
        s = self._connect()
        try:
            with s.makefile("rwb") as fp:
                P.write_msg(fp, req)
                resp = P.read_msg(fp)
        except P.ProtocolError as e:
            raise NetError(str(e)) from e
        finally:
            s.close()
        if not resp.get("ok"):
            reason = resp.get("error", "bilinmeyen hata")
            if reason in ("not authenticated",):
                raise AuthError(reason)
            raise NetError(reason)
        return resp

    def _auth_rpc(self, op: str, **fields) -> dict:
        if not self.token:
            raise AuthError("giriş yapılmadı")
        try:
            return self._rpc(op, token=self.token, **fields)
        except AuthError:
            self.token = None
            raise

    # -- ops ------------------------------------------------------
    def ping(self) -> dict:
        return self._rpc(P.OP_PING)

    def register(self, user: str, password: str, invite: str | None) -> None:
        self._rpc(P.OP_REGISTER, user=user, password=password, invite=invite)

    def login(self, user: str, password: str) -> dict:
        r = self._rpc(P.OP_LOGIN, user=user, password=password)
        self.token = r["token"]
        self.token_expires = int(r.get("expires", 0))
        self.address = r.get("address", "")
        self.onion = r.get("onion", "")
        return r

    def restore(self, token: str, expires: int, address: str, onion: str) -> None:
        self.token, self.token_expires = token, expires
        self.address, self.onion = address, onion

    def logout(self) -> None:
        try:
            self._auth_rpc(P.OP_LOGOUT)
        except NetError:
            pass
        self.token = None

    def folders(self) -> list[dict]:
        return self._auth_rpc(P.OP_FOLDERS)["folders"]

    def list(self, folder: str = "INBOX") -> list[dict]:
        return self._auth_rpc(P.OP_LIST, folder=folder)["messages"]

    def fetch(self, folder: str, key: str) -> bytes:
        return base64.b64decode(self._auth_rpc(P.OP_FETCH, folder=folder, key=key)["raw"])

    def send(self, raw: bytes, rcpts: list[str] | None = None) -> int:
        r = self._auth_rpc(P.OP_SEND, raw=base64.b64encode(raw).decode("ascii"),
                           rcpts=rcpts or [])
        return int(r.get("queued", 0))

    def delete(self, folder: str, key: str) -> None:
        self._auth_rpc(P.OP_DELETE, folder=folder, key=key)

    def move(self, src: str, key: str, dst: str) -> str:
        return self._auth_rpc(P.OP_MOVE, src=src, key=key, dst=dst)["key"]

    def mark_seen(self, folder: str, key: str, seen: bool = True) -> None:
        self._auth_rpc(P.OP_MARK_SEEN, folder=folder, key=key, seen=seen)
