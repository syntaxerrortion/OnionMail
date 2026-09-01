"""Wire protocol between the desktop client and the server API daemon (`apid`).

Framing: one JSON object per line, UTF-8, terminated by ``\\n``. One request line
gets exactly one response line, then the connection may be reused or closed.

Response shape:
    success -> {"ok": true, ...}
    failure -> {"ok": false, "error": "<machine-ish reason>"}

The onion transport is already end-to-end encrypted, so there is no TLS here; an
optional pre-shared key (``psk``) can gate every request on top of the address.
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

PROTOCOL_VERSION = 1
MAX_LINE = 64 * 1024 * 1024  # 64 MiB — a fetched message with attachments

# request ops
OP_PING = "ping"
OP_REGISTER = "register"
OP_LOGIN = "login"
OP_LOGOUT = "logout"
OP_FOLDERS = "folders"
OP_LIST = "list"
OP_FETCH = "fetch"
OP_SEND = "send"
OP_DELETE = "delete"
OP_MOVE = "move"
OP_MARK_SEEN = "mark_seen"
OP_PUBKEY_SET = "pubkey_set"   # kendi age açık anahtarını sunucu dizinine yaz
OP_PUBKEY_GET = "pubkey_get"   # bir adresin açık anahtarını sor (bu sunucudaki hesaplar)

AUTHED_OPS = frozenset({
    OP_LOGOUT, OP_FOLDERS, OP_LIST, OP_FETCH, OP_SEND, OP_DELETE, OP_MOVE,
    OP_MARK_SEEN, OP_PUBKEY_SET, OP_PUBKEY_GET,
})


class ProtocolError(Exception):
    pass


def dumps(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def write_msg(fp: BinaryIO, obj: dict[str, Any]) -> None:
    fp.write(dumps(obj))
    fp.flush()


def read_msg(fp: BinaryIO) -> dict[str, Any]:
    line = fp.readline(MAX_LINE + 1)
    if not line:
        raise ProtocolError("connection closed")
    if len(line) > MAX_LINE:
        raise ProtocolError("line too long")
    try:
        obj = json.loads(line)
    except ValueError as e:
        raise ProtocolError(f"bad json: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolError("message is not an object")
    return obj


def ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, **kw}


def err(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}
