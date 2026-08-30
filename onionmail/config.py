"""Configuration loading and validation."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "onionmail" / "config.toml"

ONION_SUFFIX = ".onion"
# v3 onion: 56 base32 chars + ".onion"
V3_ONION_LEN = 56 + len(ONION_SUFFIX)


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p))).resolve()


@dataclass(slots=True)
class Identity:
    onion: str = ""
    hostname_file: str = "/var/lib/tor/onionmail/hostname"
    local_user: str = "user"
    extra_addresses: list[str] = field(default_factory=list)

    def resolve_onion(self) -> str:
        """Return the configured onion, or read it from Tor's hostname file."""
        if self.onion:
            return self.onion.strip().lower()
        hf = Path(self.hostname_file)
        if hf.is_file():
            return hf.read_text().strip().lower()
        return ""

    def accepted_addresses(self, onion: str) -> set[str]:
        addrs = {a.strip().lower() for a in self.extra_addresses if a.strip()}
        if onion:
            addrs.add(f"{self.local_user.lower()}@{onion}")
        return addrs


@dataclass(slots=True)
class SmtpdConf:
    host: str = "127.0.0.1"
    port: int = 8025
    max_message_bytes: int = 25 * 1024 * 1024
    max_rcpt: int = 10


@dataclass(slots=True)
class SenderConf:
    socks_host: str = "127.0.0.1"
    socks_port: int = 9050
    timeout: int = 120
    retry_delays: list[int] = field(
        default_factory=lambda: [60, 300, 900, 3600, 10800, 43200]
    )


@dataclass(slots=True)
class StorageConf:
    maildir: str = "~/.local/share/onionmail/Maildir"

    @property
    def maildir_path(self) -> Path:
        return _expand(self.maildir)


@dataclass(slots=True)
class PolicyConf:
    allowlist_only: bool = False
    allowed_sender_onions: list[str] = field(default_factory=list)

    def sender_allowed(self, onion: str) -> bool:
        if not self.allowlist_only:
            return True
        return onion.lower() in {o.lower() for o in self.allowed_sender_onions}


@dataclass(slots=True)
class SandboxConf:
    backend: str = "firejail"
    workdir: str = "~/.cache/onionmail/sandbox"
    viewer: str = "xdg-open"

    @property
    def workdir_path(self) -> Path:
        return _expand(self.workdir)


@dataclass(slots=True)
class AccountsConf:
    """Server-side: multi-user accounts (needed for network login)."""
    enabled: bool = False
    store_path: str = "~/.local/share/onionmail/accounts"
    open_registration: bool = False
    session_ttl: int = 3600
    max_failed: int = 5
    lock_minutes: int = 15
    min_password_len: int = 8

    @property
    def store_path_p(self) -> Path:
        return _expand(self.store_path)


@dataclass(slots=True)
class ApiConf:
    """Server-side: the `apid` network service Tor exposes on a 2nd onion port."""
    host: str = "127.0.0.1"
    port: int = 8443
    preshared_key: str = ""


@dataclass(slots=True)
class ClientConf:
    """Client-side: where the desktop app connects and remembers its session."""
    server_onion: str = ""
    preshared_key: str = ""
    session_path: str = "~/.config/onionmail/session.json"

    @property
    def session_path_p(self) -> Path:
        return _expand(self.session_path)


@dataclass(slots=True)
class Config:
    identity: Identity = field(default_factory=Identity)
    smtpd: SmtpdConf = field(default_factory=SmtpdConf)
    sender: SenderConf = field(default_factory=SenderConf)
    storage: StorageConf = field(default_factory=StorageConf)
    policy: PolicyConf = field(default_factory=PolicyConf)
    sandbox: SandboxConf = field(default_factory=SandboxConf)
    accounts: AccountsConf = field(default_factory=AccountsConf)
    api: ApiConf = field(default_factory=ApiConf)
    client: ClientConf = field(default_factory=ClientConf)
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        p = (
            Path(path)
            if path
            else Path(os.environ.get("ONIONMAIL_CONFIG", DEFAULT_CONFIG_PATH))
        )
        data: dict = {}
        if p.is_file():
            data = tomllib.loads(p.read_text())
        cfg = cls(
            identity=Identity(**data.get("identity", {})),
            smtpd=SmtpdConf(**data.get("smtpd", {})),
            sender=SenderConf(**data.get("sender", {})),
            storage=StorageConf(**data.get("storage", {})),
            policy=PolicyConf(**data.get("policy", {})),
            sandbox=SandboxConf(**data.get("sandbox", {})),
            accounts=AccountsConf(**data.get("accounts", {})),
            api=ApiConf(**data.get("api", {})),
            client=ClientConf(**data.get("client", {})),
            source_path=p if p.is_file() else None,
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.smtpd.host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError(
                f"smtpd.host must be loopback (got {self.smtpd.host!r}); "
                "Tor forwards to it, it must never be world-reachable"
            )
        if self.smtpd.port < 1 or self.smtpd.port > 65535:
            raise ValueError("smtpd.port out of range")
        if self.api.host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError(
                f"api.host must be loopback (got {self.api.host!r}); "
                "Tor forwards to it, it must never be world-reachable"
            )
        if self.sandbox.backend not in ("firejail", "bwrap", "none"):
            raise ValueError("sandbox.backend must be firejail|bwrap|none")


def is_onion_address(addr: str) -> bool:
    """True if addr looks like localpart@<v3-onion>."""
    _, _, domain = addr.rpartition("@")
    domain = domain.strip().lower()
    return domain.endswith(ONION_SUFFIX) and len(domain) == V3_ONION_LEN
