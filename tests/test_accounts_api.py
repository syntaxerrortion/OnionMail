"""Account store + `apid` network protocol + multi-user smtpd delivery.

No Tor: the apid server binds a real loopback port and NetClient connects to it
directly (``_direct=``), which exercises the exact same request/response code
path the SOCKS transport would.
"""

from __future__ import annotations

import socket
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

from onionmail.accounts import AccountError, Accounts, Policy
from onionmail.config import Config
from onionmail.netclient import AuthError, NetClient, NetError

ONION = "a" * 56 + ".onion"
PEER = "b" * 56 + ".onion"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --------------------------------------------------------------------------- #
#  account store                                                               #
# --------------------------------------------------------------------------- #
def test_account_lifecycle(tmp_path: Path):
    acc = Accounts(tmp_path / "accounts", Policy(open_registration=False, max_failed=3))
    code = acc.new_invite()

    with pytest.raises(AccountError):
        acc.create("neo", "short", code)                    # too short
    with pytest.raises(AccountError):
        acc.create("Neo!", "longenough", code)              # bad name

    acc.create("neo", "correct horse", code)
    assert acc.exists("neo")
    with pytest.raises(AccountError):
        acc.create("trinity", "another one", code)          # invite already used

    assert acc.verify("neo", "correct horse") is True
    assert acc.verify("neo", "wrong") is False
    assert acc.verify("neo", "wrong") is False
    assert acc.verify("neo", "wrong") is False              # 3rd failure -> lock
    with pytest.raises(AccountError):
        acc.verify("neo", "correct horse")                  # locked

    assert acc.maildir_for("neo").is_dir()


def test_unknown_user_verify_is_false(tmp_path: Path):
    acc = Accounts(tmp_path / "a")
    assert acc.verify("ghost", "whatever") is False


# --------------------------------------------------------------------------- #
#  apid + netclient                                                            #
# --------------------------------------------------------------------------- #
@pytest.fixture
def server(tmp_path: Path):
    from onionmail.apid import build_server

    cfg = Config()
    cfg.identity.onion = ONION
    cfg.accounts.enabled = True
    cfg.accounts.store_path = str(tmp_path / "accounts")
    cfg.accounts.open_registration = False
    cfg.api.host = "127.0.0.1"
    cfg.api.port = _free_port()

    srv = build_server(cfg)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    yield cfg, srv
    srv.shutdown()


def _client(cfg: Config) -> NetClient:
    return NetClient(ONION, _direct=("127.0.0.1", cfg.api.port), timeout=5)


def test_ping_and_register_and_login(server):
    cfg, srv = server
    c = _client(cfg)

    pong = c.ping()
    assert pong["server"] == "onionmail" and pong["onion"] == ONION

    with pytest.raises(NetError):
        c.register("neo", "correct horse", "bogus-invite")

    code = srv.accounts.new_invite()
    c.register("neo", "correct horse", code)

    r = c.login("neo", "correct horse")
    assert r["address"] == f"neo@{ONION}"
    assert c.token

    with pytest.raises(NetError):
        c.login("neo", "nope")


def test_send_list_fetch_delete(server):
    cfg, srv = server
    code = srv.accounts.new_invite()
    c = _client(cfg)
    c.register("neo", "correct horse", code)
    c.login("neo", "correct horse")

    msg = EmailMessage()
    msg["From"] = f"neo@{ONION}"
    msg["To"] = f"friend@{PEER}"
    msg["Subject"] = "wake up"
    msg.set_content("follow the white rabbit")
    queued = c.send(msg.as_bytes(), [f"friend@{PEER}"])
    assert queued == 1

    # it landed in the account's Outbox queue on the server
    store = srv.accounts.store_for("neo")
    assert len(store.queue()) == 1
    outbox = c.list("Outbox")
    assert outbox and outbox[0]["subject"] == "wake up"

    raw = c.fetch("Outbox", outbox[0]["key"])
    assert b"white rabbit" in raw

    c.delete("Outbox", outbox[0]["key"])
    assert c.list("Outbox") == []


def test_send_rejects_non_onion_recipient(server):
    cfg, srv = server
    code = srv.accounts.new_invite()
    c = _client(cfg)
    c.register("neo", "correct horse", code)
    c.login("neo", "correct horse")
    with pytest.raises(NetError):
        c.send(b"From: x\r\nTo: y\r\n\r\nhi", ["someone@example.com"])


def test_authed_op_without_token_fails(server):
    cfg, _ = server
    c = _client(cfg)
    with pytest.raises(AuthError):
        c.list("INBOX")


def test_pubkey_directory(server):
    cfg, srv = server
    code = srv.accounts.new_invite()
    c = _client(cfg)
    c.register("neo", "correct horse", code)
    c.login("neo", "correct horse")

    assert c.pubkey_get(f"neo@{ONION}") is None          # not published yet
    c.pubkey_set("age1" + "q" * 55)
    assert c.pubkey_get(f"neo@{ONION}") == "age1" + "q" * 55
    assert c.pubkey_get("neo") == "age1" + "q" * 55       # localpart de olur
    assert c.pubkey_get(f"stranger@{PEER}") is None       # another server
    assert c.pubkey_get(f"ghost@{ONION}") is None         # nonexistent account

    with pytest.raises(NetError):
        c.pubkey_set("not-an-age-key")

    assert srv.accounts.get_pubkey("neo") == "age1" + "q" * 55


def test_pubkey_ops_need_auth(server):
    cfg, _ = server
    c = _client(cfg)
    with pytest.raises(AuthError):
        c.pubkey_get(f"neo@{ONION}")
    with pytest.raises(AuthError):
        c.pubkey_set("age1xxx")


def test_psk_gate(tmp_path: Path):
    from onionmail.apid import build_server

    cfg = Config()
    cfg.identity.onion = ONION
    cfg.accounts.enabled = True
    cfg.accounts.store_path = str(tmp_path / "acc")
    cfg.api.host = "127.0.0.1"
    cfg.api.port = _free_port()
    cfg.api.preshared_key = "s3kr1t"
    srv = build_server(cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.1)
    try:
        NetClient(ONION, _direct=("127.0.0.1", cfg.api.port), timeout=5).ping()
        assert False, "ping without psk should fail"
    except NetError:
        pass
    good = NetClient(ONION, _direct=("127.0.0.1", cfg.api.port),
                     preshared_key="s3kr1t", timeout=5)
    assert good.ping()["ok"] is True
    srv.shutdown()


# --------------------------------------------------------------------------- #
#  multi-user smtpd delivery                                                   #
# --------------------------------------------------------------------------- #
def test_smtpd_routes_to_account_maildir(tmp_path: Path):
    import smtplib

    from onionmail.smtpd import build_controller

    cfg = Config()
    cfg.identity.onion = ONION
    cfg.accounts.enabled = True
    cfg.accounts.store_path = str(tmp_path / "accounts")
    cfg.smtpd.host = "127.0.0.1"
    cfg.smtpd.port = _free_port()

    acc = Accounts(cfg.accounts.store_path_p, Policy(open_registration=True))
    acc.create("neo", "correct horse", None)

    controller = build_controller(cfg, accounts=acc)
    controller.start()
    try:
        with smtplib.SMTP("127.0.0.1", cfg.smtpd.port, timeout=10) as s:
            s.ehlo("peer.test")
            m = EmailMessage()
            m["From"] = f"morpheus@{PEER}"
            m["To"] = f"neo@{ONION}"
            m["Subject"] = "the matrix"
            m.set_content("has you")
            s.send_message(m)
            # unknown localpart is refused
            s.mail(f"x@{PEER}")
            code, _ = s.rcpt(f"nobody@{ONION}")
            assert code == 550
    finally:
        controller.stop()

    inbox = acc.store_for("neo").list("INBOX")
    assert len(inbox) == 1 and inbox[0].subject == "the matrix"


# --------------------------------------------------------------------------- #
#  NetBackend: the From header of an outgoing message                          #
# --------------------------------------------------------------------------- #
def test_netbackend_rewrites_from_to_account_address():
    """The client config does not know its own identity; ``build_message``
    fakes From as ``user@onionmail``. ``NetBackend`` must write the real
    address of the logged-in account so the recipient can reply."""
    from onionmail.backend import NetBackend
    from onionmail.compose import build_message

    captured: dict = {}

    class _FakeClient:
        address = f"nejarm@{ONION}"

        def send(self, raw: bytes, rcpts: list[str]) -> int:
            captured["raw"] = raw
            captured["rcpts"] = rcpts
            return 1

    from onionmail.config import Identity

    # client config with no identity (no hostname file → From is faked)
    cfg = Config(identity=Identity(hostname_file="/nonexistent/hostname"))
    msg = build_message(cfg, to=[f"bob@{PEER}"], subject="selam", body="test")
    assert str(msg["From"]) == "user@onionmail"

    NetBackend(_FakeClient()).send(msg)

    assert f"From: nejarm@{ONION}".encode() in captured["raw"]
    assert captured["rcpts"] == [f"bob@{PEER}"]
