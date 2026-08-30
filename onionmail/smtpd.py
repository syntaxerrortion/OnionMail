"""Inbound SMTP server. Tor's HiddenServicePort forwards to this loopback port."""

from __future__ import annotations

import logging
import threading
from email.utils import format_datetime
from datetime import datetime, timezone

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer, Envelope, Session

from .config import Config, is_onion_address
from .store import Store

log = logging.getLogger("onionmail.smtpd")


class OnionHandler:
    def __init__(self, cfg: Config, store: Store | None = None, accounts=None):
        self.cfg = cfg
        self.accounts = accounts            # multi-user mode when set
        self.store = store                  # single-user mode when set
        self.onion = cfg.identity.resolve_onion()
        self.accepted = cfg.identity.accepted_addresses(self.onion)
        if not self.onion:
            log.warning("onion address unknown (Tor not up yet?); accepting by localpart only")

    def _localpart(self, addr: str) -> str | None:
        """Return the accepted localpart for addr, or None if we won't take it."""
        addr = addr.strip().lower()
        local, _, domain = addr.rpartition("@")
        if self.accounts is not None:
            if self.onion and domain == self.onion and self.accounts.exists(local):
                return local
            return None
        if addr in self.accepted:
            return local
        if self.onion and domain == self.onion and local == self.cfg.identity.local_user.lower():
            return local
        return None

    def _is_local(self, addr: str) -> bool:
        return self._localpart(addr) is not None

    async def handle_EHLO(self, server: SMTPServer, session: Session, envelope: Envelope,
                          hostname: str, responses: list[str]) -> list[str]:
        session.host_name = hostname
        return responses

    async def handle_MAIL(self, server: SMTPServer, session: Session, envelope: Envelope,
                          address: str, mail_options: list[str]) -> str:
        _, _, sender_onion = address.rpartition("@")
        if not self.cfg.policy.sender_allowed(sender_onion):
            log.info("rejected MAIL FROM %s (not in allowlist)", address)
            return "550 5.7.1 sender not allowed"
        envelope.mail_from = address
        envelope.mail_options.extend(mail_options)
        return "250 2.1.0 OK"

    async def handle_RCPT(self, server: SMTPServer, session: Session, envelope: Envelope,
                          address: str, rcpt_options: list[str]) -> str:
        if not self._is_local(address):
            log.info("refused relay to %s", address)
            return "550 5.7.1 relaying denied; this server only accepts mail for itself"
        if len(envelope.rcpt_tos) >= self.cfg.smtpd.max_rcpt:
            return "452 4.5.3 too many recipients"
        envelope.rcpt_tos.append(address)
        return "250 2.1.5 OK"

    async def handle_DATA(self, server: SMTPServer, session: Session, envelope: Envelope) -> str:
        data: bytes = envelope.content if isinstance(envelope.content, bytes) else envelope.content.encode()
        if len(data) > self.cfg.smtpd.max_message_bytes:
            return "552 5.3.4 message too large"

        peer_onion = getattr(session, "host_name", "") or "unknown"
        stamp = format_datetime(datetime.now(timezone.utc))
        received = (
            f"Received: from {peer_onion} via Tor\r\n"
            f"\tby {self.onion or 'onionmail'} with onion-SMTP;\r\n"
            f"\t{stamp}\r\n"
        ).encode()

        blob = received + data
        try:
            if self.accounts is not None:
                keys = []
                for rcpt in envelope.rcpt_tos:
                    lp = self._localpart(rcpt)
                    if lp is None:
                        continue
                    keys.append(self.accounts.store_for(lp).add_incoming(blob))
                key = ",".join(keys)
            else:
                key = self.store.add_incoming(blob)
        except Exception:  # noqa: BLE001  -- never leak internals to the peer
            log.exception("failed to store incoming message")
            return "451 4.3.0 temporary local error, try again later"
        log.info("stored incoming message %s from=%s rcpts=%s bytes=%d",
                 key, envelope.mail_from, envelope.rcpt_tos, len(data))
        return "250 2.0.0 message accepted for delivery"


def build_controller(cfg: Config, store: Store | None = None, accounts=None) -> Controller:
    handler = OnionHandler(cfg, store, accounts)
    return Controller(
        handler,
        hostname=cfg.smtpd.host,
        port=cfg.smtpd.port,
        ident="onionmail",
        data_size_limit=cfg.smtpd.max_message_bytes + 4096,
        enable_SMTPUTF8=True,
    )


def run(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if cfg.accounts.enabled:
        from .accounts import Accounts, Policy
        accounts = Accounts(cfg.accounts.store_path_p, Policy(
            open_registration=cfg.accounts.open_registration))
        controller = build_controller(cfg, accounts=accounts)
        log.info("multi-user mode: %d account(s)", len(accounts.list_users()))
    else:
        controller = build_controller(cfg, Store(cfg.storage.maildir_path))
    controller.start()
    log.info("listening on %s:%d  onion=%s", cfg.smtpd.host, cfg.smtpd.port,
             cfg.identity.resolve_onion() or "(unknown yet)")
    stop = threading.Event()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        log.info("stopped")
