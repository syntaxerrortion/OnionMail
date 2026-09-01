"""Build outgoing messages and hand them to the send queue."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid, getaddresses
from pathlib import Path

from .config import Config, is_onion_address
from .store import Store


def _addr_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    return [a.strip() for a in value if a and a.strip()]


def build_message(
    cfg: Config,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[Path] | None = None,
    in_reply_to: str | None = None,
    from_user: str | None = None,
) -> EmailMessage:
    onion = cfg.identity.resolve_onion() or "onionmail"
    from_addr = f"{from_user or cfg.identity.local_user}@{onion}"

    m = EmailMessage()
    m["From"] = from_addr
    m["To"] = ", ".join(_addr_list(to))
    if cc:
        m["Cc"] = ", ".join(_addr_list(cc))
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=onion)
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body)

    for path in attachments or []:
        data = Path(path).read_bytes()
        maintype, _, subtype = _guess_type(path).partition("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                         filename=Path(path).name)
    return m


def _guess_type(path: Path) -> str:
    import mimetypes

    t, _ = mimetypes.guess_type(str(path))
    return t or "application/octet-stream"


def recipients_of(msg: EmailMessage, bcc: list[str] | None = None) -> list[str]:
    """All envelope recipients: To + Cc header addresses plus any explicit Bcc."""
    header_addrs = [a for _, a in getaddresses(
        [str(msg[h]) for h in ("To", "Cc") if msg[h]]
    ) if a]
    seen: dict[str, None] = {}
    for a in header_addrs + _addr_list(bcc):
        seen.setdefault(a, None)
    return list(seen)


def queue_message(
    cfg: Config, store: Store, msg: EmailMessage, bcc: list[str] | None = None
) -> None:
    rcpts = recipients_of(msg, bcc)
    if not rcpts:
        raise ValueError("alıcı yok")
    bad = [r for r in rcpts if not is_onion_address(r)]
    if bad:
        raise ValueError(f"alıcı(lar) v3 .onion değil: {', '.join(bad)}")
    if "Bcc" in msg:
        del msg["Bcc"]  # never transmit the Bcc header
    store.enqueue(msg.as_bytes(), str(msg["From"]), rcpts)
