"""Outbound delivery over Tor.

Each recipient address is ``localpart@<v3-onion>``. Delivery = open a SOCKS5
connection through the local Tor daemon, CONNECT to ``<onion>:25`` and speak
plain SMTP. The onion protocol is already end-to-end encrypted, so STARTTLS is
optional (and off by default: onion mail servers rarely present usable certs).
"""

from __future__ import annotations

import logging
import smtplib
import socket
import time

import socks  # PySocks

from .config import Config, is_onion_address
from .store import QueueEntry, Store

log = logging.getLogger("onionmail.sender")


class DeliveryError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _socks_connect(onion: str, port: int, cfg: Config) -> socks.socksocket:
    s = socks.socksocket()
    s.set_proxy(
        socks.SOCKS5,
        cfg.sender.socks_host,
        cfg.sender.socks_port,
        rdns=True,  # let Tor resolve the .onion, never locally
    )
    s.settimeout(cfg.sender.timeout)
    try:
        s.connect((onion, port))
    except socks.ProxyConnectionError as e:
        raise DeliveryError(f"cannot reach Tor SOCKS at "
                            f"{cfg.sender.socks_host}:{cfg.sender.socks_port} ({e})") from e
    except socks.GeneralProxyError as e:
        # Tor could not build a circuit to the onion (offline / bad address).
        raise DeliveryError(f"Tor could not connect to {onion}:{port} ({e})") from e
    return s


def deliver_one(raw: bytes, mail_from: str, rcpt: str, cfg: Config) -> None:
    """Deliver ``raw`` to a single recipient. Raises DeliveryError on failure."""
    if not is_onion_address(rcpt):
        raise DeliveryError(f"{rcpt!r} is not a v3 .onion address", permanent=True)
    _, _, onion = rcpt.rpartition("@")
    our_onion = cfg.identity.resolve_onion() or "onionmail"

    # smtplib.SMTP.data() dot-stuffs but does NOT fix line endings for bytes
    # input. Messages serialised with email's default policy use bare "\n", so
    # the receiving SMTP server sees one huge line -> 500 "Line too long"
    # (RFC 5321 4.5.3.1.6). Normalise every line ending to CRLF here.
    raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")

    sock = _socks_connect(onion, 25, cfg)
    smtp = smtplib.SMTP(local_hostname=our_onion, timeout=cfg.sender.timeout)
    smtp.sock = sock
    smtp.file = sock.makefile("rb")
    smtp._host = onion  # for optional STARTTLS SNI
    try:
        code, msg = smtp.getreply()
        if code != 220:
            raise DeliveryError(f"greeting {code}: {msg!r}", permanent=500 <= code < 600)
        smtp.ehlo_or_helo_if_needed()
        try:
            smtp.mail(mail_from)
            code, msg = smtp.rcpt(rcpt)
            if code not in (250, 251):
                raise DeliveryError(f"RCPT {code}: {msg!r}", permanent=500 <= code < 600)
            code, msg = smtp.data(raw)
            if code != 250:
                raise DeliveryError(f"DATA {code}: {msg!r}", permanent=500 <= code < 600)
        finally:
            try:
                smtp.quit()
            except smtplib.SMTPException:
                pass
    except smtplib.SMTPResponseException as e:
        raise DeliveryError(f"{e.smtp_code}: {e.smtp_error!r}",
                            permanent=500 <= e.smtp_code < 600) from e
    except (smtplib.SMTPException, OSError, socket.timeout) as e:
        raise DeliveryError(str(e)) from e
    finally:
        try:
            smtp.close()
        except Exception:  # noqa: BLE001
            pass


def process_queue_once(cfg: Config, store: Store) -> int:
    """Attempt every due queue entry once. Returns count of entries touched."""
    now = time.time()
    touched = 0
    for entry in store.queue():
        if not entry.due(now):
            continue
        touched += 1
        raw = store.get_bytes("Outbox", entry.key)
        still_pending: list[str] = []
        errors: list[str] = []
        for rcpt in entry.rcpts:
            try:
                deliver_one(raw, entry.mail_from, rcpt, cfg)
                log.info("delivered %s -> %s", entry.key, rcpt)
            except DeliveryError as e:
                log.warning("delivery failed %s -> %s: %s%s",
                            entry.key, rcpt, e, " (permanent)" if e.permanent else "")
                if not e.permanent:
                    still_pending.append(rcpt)
                errors.append(f"{rcpt}: {e}")

        entry.rcpts = still_pending
        entry.last_error = "; ".join(errors)
        if not still_pending:
            failed = bool(errors)
            store.complete_queue_entry(entry, failed=failed)
            if failed:
                _bounce(store, entry, raw)
        else:
            entry.attempts += 1
            delays = cfg.sender.retry_delays
            if entry.attempts > len(delays):
                store.complete_queue_entry(entry, failed=True)
                _bounce(store, entry, raw)
            else:
                entry.next_ts = now + delays[entry.attempts - 1]
                store.update_queue_entry(entry)
    return touched


def _bounce(store: Store, entry: QueueEntry, raw: bytes) -> None:
    """Drop a local delivery-failure notice into INBOX."""
    from email.message import EmailMessage

    m = EmailMessage()
    m["Subject"] = "Delivery failed (onionmail)"
    m["From"] = "onionmail-daemon@localhost"
    m["To"] = entry.mail_from or "you@localhost"
    m.set_content(
        "The message below could not be delivered to the recipient(s):\n\n"
        f"  {entry.last_error}\n\n"
        f"Attempts: {entry.attempts}\n"
        "--- original message headers ---\n"
        + raw.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
    )
    store.add_incoming(m.as_bytes())


def _stores(cfg: Config) -> list[Store]:
    """One Store per outbound queue: every account in multi-user mode, else the
    single configured Maildir."""
    if cfg.accounts.enabled:
        from .accounts import Accounts

        acc = Accounts(cfg.accounts.store_path_p)
        return [Store(acc.maildir_for(u)) for u in acc.list_users()]
    return [Store(cfg.storage.maildir_path)]


def run(cfg: Config, interval: int = 30) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("sender loop started (interval=%ds, socks=%s:%d, accounts=%s)",
             interval, cfg.sender.socks_host, cfg.sender.socks_port, cfg.accounts.enabled)
    try:
        while True:
            for store in _stores(cfg):
                try:
                    process_queue_once(cfg, store)
                except Exception:  # noqa: BLE001
                    log.exception("queue pass failed for %s", store.root_path)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("stopped")
