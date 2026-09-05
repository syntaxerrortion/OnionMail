"""A single mail-store interface with two implementations, so the GUI and TUI
don't care whether the mailbox is a local Maildir or a remote `apid` over Tor.

* ``LocalBackend``  — direct Maildir access (run the client on the dm1 / over SSH)
* ``NetBackend``    — talk to the server's `apid` onion, authenticated
"""

from __future__ import annotations

import abc
from email import policy as _epol
from email.message import EmailMessage
from email.parser import BytesParser

from .compose import queue_message, recipients_of, wrap_encrypted
from .config import Config
from .netclient import NetClient
from .store import FOLDERS, Store, Summary

_PARSER = BytesParser(policy=_epol.default)


class Backend(abc.ABC):
    address: str = ""
    keys = None  # ClientKeys | None — kişinin uçtan uca şifreleme kimliği

    @abc.abstractmethod
    def folders(self) -> list[str]: ...

    @abc.abstractmethod
    def list(self, folder: str) -> list[Summary]: ...

    @abc.abstractmethod
    def get_bytes(self, folder: str, key: str) -> bytes: ...

    def get(self, folder: str, key: str) -> EmailMessage:
        return _PARSER.parsebytes(self.get_bytes(folder, key))  # type: ignore[return-value]

    @abc.abstractmethod
    def delete(self, folder: str, key: str) -> None: ...

    @abc.abstractmethod
    def move(self, src: str, key: str, dst: str) -> str: ...

    @abc.abstractmethod
    def mark_seen(self, folder: str, key: str, seen: bool = True) -> None: ...

    @abc.abstractmethod
    def send(self, msg: EmailMessage, bcc: list[str] | None = None,
              encrypt_to: list[str] | None = None) -> int: ...

    @abc.abstractmethod
    def queue_count(self) -> int: ...


class LocalBackend(Backend):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.storage.maildir_path)
        onion = cfg.identity.resolve_onion()
        self.address = f"{cfg.identity.local_user}@{onion}" if onion else cfg.identity.local_user

    def folders(self) -> list[str]:
        return list(FOLDERS)

    def list(self, folder: str) -> list[Summary]:
        return self.store.list(folder)

    def get_bytes(self, folder: str, key: str) -> bytes:
        return self.store.get_bytes(folder, key)

    def delete(self, folder: str, key: str) -> None:
        self.store.delete(folder, key)

    def move(self, src: str, key: str, dst: str) -> str:
        return self.store.move(src, key, dst)

    def mark_seen(self, folder: str, key: str, seen: bool = True) -> None:
        self.store.mark_seen(folder, key, seen)

    def send(self, msg: EmailMessage, bcc: list[str] | None = None,
             encrypt_to: list[str] | None = None) -> int:
        queue_message(self.cfg, self.store, msg, bcc=bcc, encrypt_to=encrypt_to)
        return len(recipients_of(msg, bcc))

    def queue_count(self) -> int:
        return len(self.store.queue())


class NetBackend(Backend):
    def __init__(self, client: NetClient, keys=None):
        self.client = client
        self.address = client.address
        self.keys = keys  # ClientKeys | None — girişte açıldıysa dolu

    def folders(self) -> list[str]:
        return [f["name"] for f in self.client.folders()]

    def list(self, folder: str) -> list[Summary]:
        return [Summary(**d) for d in self.client.list(folder)]

    def get_bytes(self, folder: str, key: str) -> bytes:
        return self.client.fetch(folder, key)

    def delete(self, folder: str, key: str) -> None:
        self.client.delete(folder, key)

    def move(self, src: str, key: str, dst: str) -> str:
        return self.client.move(src, key, dst)

    def mark_seen(self, folder: str, key: str, seen: bool = True) -> None:
        self.client.mark_seen(folder, key, seen)

    def send(self, msg: EmailMessage, bcc: list[str] | None = None,
             encrypt_to: list[str] | None = None) -> int:
        rcpts = recipients_of(msg, bcc)
        if "Bcc" in msg:
            del msg["Bcc"]
        if self.address and "@" in self.address:
            # istemci config'i kendi kimliğini bilmez; build_message From'u
            # 'user@onionmail' diye uydurur. Giriş yapılan hesabın gerçek
            # adresini koy ki alıcı yanıtlayabilsin (encrypt'ten önce: dış zarf
            # From'u inner'dan kopyalanıyor).
            del msg["From"]
            msg["From"] = self.address
        if encrypt_to:
            msg = wrap_encrypted(msg, encrypt_to)  # istemcide sarılır — sunucu şifreleyemez
        return self.client.send(msg.as_bytes(), rcpts)

    def queue_count(self) -> int:
        return -1  # server-side queue; not exposed per-op yet
