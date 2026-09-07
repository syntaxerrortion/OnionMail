"""Build outgoing messages and hand them to the send queue."""

from __future__ import annotations

from email import message_from_bytes
from email import policy as _policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, getaddresses
from pathlib import Path

from .config import Config, is_onion_address
from .store import Store

# Encrypted onionmail message: the entire inner MIME is wrapped in a single age
# part; the outer envelope carries only the minimum headers needed for routing
# (Subject hidden).
ENC_HEADER = "X-Onionmail-Encrypted"
ENC_MARKER = "age-v1"
ENC_FILENAME = "message.age"
PUBKEY_HEADER = "X-Onionmail-Pubkey"


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
    sender_pubkey: str | None = None,
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
    if sender_pubkey:
        # Opportunistic key propagation: added to plain messages too, so the
        # recipient can record the sender in their TOFU directory.
        m[PUBKEY_HEADER] = sender_pubkey
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


def wrap_encrypted(inner: EmailMessage, recipient_pubkeys: list[str]) -> EmailMessage:
    """`inner` (the real message) → an outer envelope with a single age part.
    The envelope carries From/To/Cc/Date/Message-ID/In-Reply-To; Subject
    becomes `[encrypted message]`, and the real subject + body + attachments
    are inside the encrypted part."""
    from .crypto import encrypt_for

    if "Bcc" in inner:
        del inner["Bcc"]  # the BCC list must not enter even the encrypted part
    blob = encrypt_for(inner.as_bytes(), recipient_pubkeys)

    m = EmailMessage()
    m["From"] = str(inner["From"] or "")
    if inner["To"]:
        m["To"] = str(inner["To"])
    if inner["Cc"]:
        m["Cc"] = str(inner["Cc"])
    m["Subject"] = "[encrypted message]"
    m["Date"] = str(inner["Date"] or formatdate(localtime=True))
    m["Message-ID"] = str(inner["Message-ID"] or make_msgid())
    if inner["In-Reply-To"]:
        m["In-Reply-To"] = str(inner["In-Reply-To"])
        m["References"] = str(inner["References"] or inner["In-Reply-To"])
    if inner[PUBKEY_HEADER]:
        # not secret — keep it on the outer envelope too, so even someone who
        # cannot decrypt can record the sender in TOFU
        m[PUBKEY_HEADER] = str(inner[PUBKEY_HEADER])
    m[ENC_HEADER] = ENC_MARKER
    m.set_content(blob, maintype="application", subtype="octet-stream",
                  disposition="attachment", filename=ENC_FILENAME)
    return m


def is_encrypted(msg: EmailMessage) -> bool:
    return (msg.get(ENC_HEADER) or "").strip().lower().startswith(ENC_MARKER)


def encrypted_blob(msg: EmailMessage) -> bytes:
    """Extract the age-encrypted bytes from the outer envelope."""
    for part in msg.walk():
        if part.get_content_type() == "application/octet-stream":
            return part.get_payload(decode=True) or b""
    raise ValueError("encrypted part not found")


def decrypt_message(msg: EmailMessage, secret: str) -> EmailMessage:
    """Decrypt the encrypted outer envelope → parse and return the inner (real) message."""
    from .crypto import decrypt_with

    inner_raw = decrypt_with(encrypted_blob(msg), secret)
    return message_from_bytes(inner_raw, policy=_policy.default)  # type: ignore[return-value]


def queue_message(
    cfg: Config, store: Store, msg: EmailMessage, bcc: list[str] | None = None,
    encrypt_to: list[str] | None = None,
) -> None:
    rcpts = recipients_of(msg, bcc)
    if not rcpts:
        raise ValueError("no recipient")
    bad = [r for r in rcpts if not is_onion_address(r)]
    if bad:
        raise ValueError(f"recipient(s) are not v3 .onion: {', '.join(bad)}")
    if "Bcc" in msg:
        del msg["Bcc"]  # never transmit the Bcc header
    if encrypt_to:
        msg = wrap_encrypted(msg, encrypt_to)
    store.enqueue(msg.as_bytes(), str(msg["From"]), rcpts)
