"""End-to-end-ish tests that do not need Tor.

* inbound: talk plain SMTP to the loopback listener, assert the message lands
  in the Maildir INBOX.
* queue: enqueue a message, monkeypatch delivery, assert it moves to Sent.
* sandbox: MIME part listing / filename sanitising / HTML neutralising.
"""

from __future__ import annotations

import smtplib
import socket
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

from onionmail.config import Config
from onionmail.store import Store

ONION = "a" * 56 + ".onion"
PEER = "b" * 56 + ".onion"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.identity.onion = ONION
    c.identity.local_user = "me"
    c.smtpd.host = "127.0.0.1"
    c.smtpd.port = _free_port()
    c.storage.maildir = str(tmp_path / "Maildir")
    c.sandbox.backend = "none"
    return c


def test_inbound_delivery(cfg: Config):
    from onionmail.smtpd import build_controller

    store = Store(cfg.storage.maildir_path)
    controller = build_controller(cfg, store)
    controller.start()
    try:
        port = cfg.smtpd.port
        with smtplib.SMTP("127.0.0.1", port, timeout=10) as s:
            s.ehlo("peer.test")
            msg = EmailMessage()
            msg["From"] = f"someone@{PEER}"
            msg["To"] = f"me@{ONION}"
            msg["Subject"] = "merhaba"
            msg.set_content("gövde")
            s.send_message(msg)
    finally:
        controller.stop()

    inbox = store.list("INBOX")
    assert len(inbox) == 1
    assert inbox[0].subject == "merhaba"
    assert "onion-SMTP" in store.get_bytes("INBOX", inbox[0].key).decode()


def test_list_decodes_non_ascii_subject(cfg: Config):
    """`mailbox.Maildir` mesajları compat32 policy'siyle ayrıştırıyor —
    `store.list()` RFC 2047 encoded-word (ör. Türkçe karakterli Subject)
    başlıklarını kendisi çözmeli, ham '=?utf-8?...?=' göstermemeli."""
    store = Store(cfg.storage.maildir_path)
    msg = EmailMessage()
    msg["From"] = f"someone@{PEER}"
    msg["To"] = f"me@{ONION}"
    msg["Subject"] = "[şifreli mesaj]"
    msg.set_content("gövde")
    store.add_incoming(msg.as_bytes())

    inbox = store.list("INBOX")
    assert len(inbox) == 1
    assert inbox[0].subject == "[şifreli mesaj]"


def test_relay_is_refused(cfg: Config):
    from onionmail.smtpd import build_controller

    store = Store(cfg.storage.maildir_path)
    controller = build_controller(cfg, store)
    controller.start()
    try:
        port = cfg.smtpd.port
        with smtplib.SMTP("127.0.0.1", port, timeout=10) as s:
            s.ehlo("peer.test")
            s.mail(f"x@{PEER}")
            code, _ = s.rcpt("victim@example.com")
        assert code == 550
    finally:
        controller.stop()


def test_queue_delivers_and_moves_to_sent(cfg: Config, monkeypatch):
    from onionmail import sender
    from onionmail.compose import build_message, queue_message

    store = Store(cfg.storage.maildir_path)
    msg = build_message(cfg, to=[f"friend@{PEER}"], subject="selam", body="test")
    queue_message(cfg, store, msg)
    assert len(store.queue()) == 1

    sent = []
    monkeypatch.setattr(sender, "deliver_one",
                        lambda raw, mail_from, rcpt, c: sent.append(rcpt))
    sender.process_queue_once(cfg, store)

    assert sent == [f"friend@{PEER}"]
    assert len(store.queue()) == 0
    assert len(store.list("Sent")) == 1


def test_queue_retries_on_transient_failure(cfg: Config, monkeypatch):
    from onionmail import sender
    from onionmail.compose import build_message, queue_message

    store = Store(cfg.storage.maildir_path)
    queue_message(cfg, store,
                  build_message(cfg, to=[f"friend@{PEER}"], subject="x", body="y"))

    def boom(*a, **k):
        raise sender.DeliveryError("Tor circuit failed", permanent=False)

    monkeypatch.setattr(sender, "deliver_one", boom)
    sender.process_queue_once(cfg, store)

    q = store.queue()
    assert len(q) == 1
    assert q[0].attempts == 1
    assert q[0].next_ts > time.time()
    assert len(store.list("Outbox")) == 1


def test_multiuser_send_lands_in_account_queue(cfg: Config, tmp_path: Path, monkeypatch):
    """Çok kullanıcılı modda CLI `send`, çalışan sender'ın taradığı HESAP
    kuyruğuna yazmalı — tek-Maildir kuyruğuna değil (eski bug)."""
    from onionmail import sender
    from onionmail.__main__ import _resolve_account
    from onionmail.accounts import Accounts, Policy
    from onionmail.compose import build_message, queue_message

    cfg.accounts.enabled = True
    cfg.accounts.store_path = str(tmp_path / "accounts")
    Accounts(cfg.accounts.store_path_p,
             Policy(open_registration=True)).create("me", "sifre12345", invite=None)
    acc = Accounts(cfg.accounts.store_path_p)

    user = _resolve_account(cfg, None)          # identity.local_user = "me"
    assert user == "me"

    store = Store(acc.maildir_for(user))
    msg = build_message(cfg, to=[f"friend@{PEER}"], subject="x", body="y",
                        from_user=user)
    assert str(msg["From"]) == f"me@{ONION}"
    queue_message(cfg, store, msg)

    assert len(Store(cfg.storage.maildir_path).queue()) == 0   # tek-Maildir boş
    assert len(store.queue()) == 1                             # hesap kuyruğunda

    stores = sender._stores(cfg)
    assert any(s.root_path == store.root_path for s in stores)

    sent: list = []
    monkeypatch.setattr(sender, "deliver_one",
                        lambda raw, mail_from, rcpt, c: sent.append((mail_from, rcpt)))
    for s in stores:
        sender.process_queue_once(cfg, s)

    assert sent == [(f"me@{ONION}", f"friend@{PEER}")]
    assert len(store.queue()) == 0
    assert len(store.list("Sent")) == 1


def test_encrypted_queue_hides_body_and_bcc(cfg: Config):
    """queue_message(encrypt_to=...) kuyruğa şifreli zarf koyar; gövde de BCC de
    (şifreli parçanın içinde bile) sızmaz."""
    pytest.importorskip("pyrage")
    from onionmail import crypto
    from onionmail.compose import (
        build_message, decrypt_message, is_encrypted, queue_message,
    )

    store = Store(cfg.storage.maildir_path)
    a_sec, a_pub = crypto.generate_identity()
    _, b_pub = crypto.generate_identity()

    inner = build_message(cfg, to=[f"to@{PEER}"], subject="özel", body="gizli metin")
    queue_message(cfg, store, inner, bcc=[f"hidden@{PEER}"], encrypt_to=[a_pub, b_pub])

    q = store.queue()
    assert len(q) == 1
    assert set(q[0].rcpts) == {f"to@{PEER}", f"hidden@{PEER}"}

    key = store.list("Outbox")[0].key
    raw = store.get_bytes("Outbox", key)
    assert b"hidden@" not in raw
    assert "gizli metin".encode() not in raw
    assert is_encrypted(store.get("Outbox", key))

    back = decrypt_message(store.get("Outbox", key), a_sec)
    assert back["Subject"] == "özel"
    assert back["Bcc"] is None
    assert back["To"] == f"to@{PEER}"


def test_sandbox_parts_and_html(cfg: Config):
    from onionmail.sandbox import list_parts, safe_text, sanitize_filename

    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("payload .EXE\x00") == "payload_.EXE"

    msg = EmailMessage()
    msg["Subject"] = "s"
    msg.set_content("düz metin")
    msg.add_alternative(
        "<p>selam<script>steal()</script><img src='http://evil/x'></p>", subtype="html"
    )
    msg.add_attachment(b"MZ...", maintype="application", subtype="x-msdownload",
                       filename="invoice.exe")

    parts = list_parts(msg)
    exe = [p for p in parts if p.filename == "invoice.exe"][0]
    assert exe.dangerous is True

    txt = safe_text(msg)
    assert "düz metin" in txt          # prefers text/plain
    assert "script" not in txt.lower() or "steal" not in txt
