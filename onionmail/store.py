"""Maildir-backed message storage plus the outbound queue state."""

from __future__ import annotations

import json
import mailbox
import time
from dataclasses import dataclass, asdict
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

FOLDERS = ("INBOX", "Sent", "Outbox", "Failed", "Drafts")
_PARSER = BytesParser(policy=policy.default)


def _decoded_header(msg, name: str, default: str = "") -> str:
    """`mailbox.Maildir` mesajları eski (compat32) policy ile ayrıştırılıyor —
    `msg.get(...)` RFC 2047 encoded-word'leri (ör. Türkçe karakterli Subject)
    çözmeden ham döndürüyor. `decode_header` bundan bağımsız çalışır."""
    raw = msg.get(name)
    if raw is None:
        return default
    try:
        return str(make_header(decode_header(str(raw))))
    except (UnicodeDecodeError, LookupError, ValueError):
        return str(raw)


@dataclass(slots=True)
class Summary:
    folder: str
    key: str
    from_: str
    to: str
    subject: str
    date: str
    seen: bool
    size: int

    @property
    def epoch(self) -> float:
        try:
            return parsedate_to_datetime(self.date).timestamp()
        except (TypeError, ValueError):
            return 0.0


@dataclass(slots=True)
class QueueEntry:
    key: str                # key of the message inside the Outbox folder
    mail_from: str
    rcpts: list[str]        # recipients still pending
    attempts: int = 0
    next_ts: float = 0.0    # earliest epoch to try again
    last_error: str = ""

    def due(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.next_ts


class Store:
    def __init__(self, maildir_path: Path):
        self.root_path = Path(maildir_path)
        # NB: mailbox.Maildir only lays down tmp/new/cur when the dir does not
        # yet exist, so create the parent but let Maildir create the leaf.
        self.root_path.parent.mkdir(parents=True, exist_ok=True)
        self._box = mailbox.Maildir(self.root_path, create=True)
        self._folders: dict[str, mailbox.Maildir] = {"INBOX": self._box}
        for name in FOLDERS:
            if name == "INBOX":
                continue
            try:
                self._folders[name] = self._box.get_folder(name)
            except mailbox.NoSuchMailboxError:
                self._folders[name] = self._box.add_folder(name)
        self._queue_path = self.root_path.parent / "outbox-state.json"

    # -- folder access -----------------------------------------------------
    def folder(self, name: str) -> mailbox.Maildir:
        return self._folders[name]

    def list(self, name: str) -> list[Summary]:
        box = self._folders[name]
        box.lock()
        try:
            out: list[Summary] = []
            for key in box.keys():
                msg = box.get_message(key)
                out.append(
                    Summary(
                        folder=name,
                        key=key,
                        from_=_decoded_header(msg, "From"),
                        to=_decoded_header(msg, "To"),
                        subject=_decoded_header(msg, "Subject", "(konu yok)"),
                        date=_decoded_header(msg, "Date"),
                        seen="S" in msg.get_flags(),
                        size=len(msg.as_bytes()),
                    )
                )
        finally:
            box.unlock()
        out.sort(key=lambda s: s.epoch, reverse=True)
        return out

    def get(self, name: str, key: str) -> EmailMessage:
        raw = self._folders[name].get_bytes(key)
        return _PARSER.parsebytes(raw)  # type: ignore[return-value]

    def get_bytes(self, name: str, key: str) -> bytes:
        return self._folders[name].get_bytes(key)

    # -- inbound ---------------------------------------------------------------
    def add_incoming(self, raw: bytes) -> str:
        m = mailbox.MaildirMessage(raw)
        m.set_subdir("new")
        m.add_flag("")  # unseen
        box = self._folders["INBOX"]
        box.lock()
        try:
            return box.add(m)
        finally:
            box.unlock()

    def add_to(self, folder: str, raw: bytes, seen: bool = True) -> str:
        m = mailbox.MaildirMessage(raw)
        m.set_subdir("cur" if seen else "new")
        if seen:
            m.add_flag("S")
        box = self._folders[folder]
        box.lock()
        try:
            return box.add(m)
        finally:
            box.unlock()

    # -- mutations ----------------------------------------------------------
    def mark_seen(self, name: str, key: str, seen: bool = True) -> None:
        box = self._folders[name]
        box.lock()
        try:
            msg = box.get_message(key)
            if seen:
                msg.add_flag("S")
            else:
                msg.remove_flag("S")
            box[key] = msg
        finally:
            box.unlock()

    def delete(self, name: str, key: str) -> None:
        box = self._folders[name]
        box.lock()
        try:
            box.discard(key)
        finally:
            box.unlock()

    def move(self, src: str, key: str, dst: str) -> str:
        raw = self._folders[src].get_bytes(key)
        new_key = self.add_to(dst, raw, seen=True)
        self.delete(src, key)
        return new_key

    # -- outbound queue ---------------------------------------------------
    def _read_queue(self) -> list[QueueEntry]:
        if not self._queue_path.is_file():
            return []
        data = json.loads(self._queue_path.read_text() or "[]")
        return [QueueEntry(**d) for d in data]

    def _write_queue(self, entries: list[QueueEntry]) -> None:
        tmp = self._queue_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(e) for e in entries], indent=2))
        tmp.replace(self._queue_path)

    def enqueue(self, raw: bytes, mail_from: str, rcpts: list[str]) -> QueueEntry:
        key = self.add_to("Outbox", raw, seen=True)
        entry = QueueEntry(key=key, mail_from=mail_from, rcpts=list(rcpts))
        q = self._read_queue()
        q.append(entry)
        self._write_queue(q)
        return entry

    def queue(self) -> list[QueueEntry]:
        return self._read_queue()

    def update_queue_entry(self, entry: QueueEntry) -> None:
        q = self._read_queue()
        q = [e for e in q if e.key != entry.key]
        if entry.rcpts:
            q.append(entry)
        self._write_queue(q)

    def complete_queue_entry(self, entry: QueueEntry, failed: bool = False) -> None:
        """Remove from queue; move the message to Sent or Failed."""
        q = [e for e in self._read_queue() if e.key != entry.key]
        self._write_queue(q)
        self.move("Outbox", entry.key, "Failed" if failed else "Sent")
