"""End-to-end encryption — age (X25519) wrappers.

Goal: the server only ever sees an encrypted blob. The entire inner MIME of
an outgoing message (headers, body, attachments) is wrapped in a single
age-encrypted part (see ``compose.py``). The secret key never leaves the
client; on disk it is encrypted with the account password (age scrypt).

If pyrage is not installed, ``HAVE_AGE`` is False and every call raises
``CryptoError`` — the caller then falls back to plaintext.
"""

from __future__ import annotations

import hashlib

try:
    import pyrage
    from pyrage import passphrase, x25519

    HAVE_AGE = True
except ImportError:  # pragma: no cover - pyrage yoksa
    HAVE_AGE = False


class CryptoError(Exception):
    """Invalid key, wrong password, could not decrypt, etc."""


def _require() -> None:
    if not HAVE_AGE:
        raise CryptoError("pyrage not installed — 'pip install pyrage' (optional 'e2e' group)")


# --------------------------------------------------------------------------- #
#  Key pair                                                                   #
# --------------------------------------------------------------------------- #
def generate_identity() -> tuple[str, str]:
    """Generate a new age key pair. Returns ``(secret_key, public_key)`` —
    in ``AGE-SECRET-KEY-1…`` and ``age1…`` form."""
    _require()
    ident = x25519.Identity.generate()
    return str(ident), str(ident.to_public())


def public_from_secret(secret: str) -> str:
    """Derive the public key from the secret key (for verification / recovery)."""
    _require()
    try:
        return str(x25519.Identity.from_str(secret.strip()).to_public())
    except Exception as e:  # noqa: BLE001 - pyrage raises its own error types
        raise CryptoError(f"invalid secret key: {e}") from e


def fingerprint(public_key: str) -> str:
    """Short fingerprint of the public key — shown to the user during TOFU
    verification. The first 8 bytes of SHA-256, in ``ABCD-1234-…`` form."""
    h = hashlib.sha256(public_key.strip().encode()).digest()[:8]
    return "-".join(h.hex()[i:i + 4].upper() for i in range(0, 16, 4))


# --------------------------------------------------------------------------- #
#  Message encryption                                                         #
# --------------------------------------------------------------------------- #
def encrypt_for(plaintext: bytes, recipient_pubkeys: list[str]) -> bytes:
    """Encrypt ``plaintext`` so that it can be decrypted by ALL of the given
    public keys (age multi-recipient: each recipient decrypts on their own)."""
    _require()
    keys = [k.strip() for k in recipient_pubkeys if k and k.strip()]
    if not keys:
        raise CryptoError("no recipient public key")
    try:
        rcpts = [x25519.Recipient.from_str(k) for k in keys]
        return pyrage.encrypt(plaintext, rcpts)
    except Exception as e:  # noqa: BLE001
        raise CryptoError(f"encryption failed: {e}") from e


def decrypt_with(ciphertext: bytes, secret: str) -> bytes:
    """Decrypt ``ciphertext`` with the secret key."""
    _require()
    try:
        return pyrage.decrypt(ciphertext, [x25519.Identity.from_str(secret.strip())])
    except Exception as e:  # noqa: BLE001
        raise CryptoError(
            f"decryption failed (the key may not be for this message): {e}") from e


# --------------------------------------------------------------------------- #
#  Password-protected secret key on disk                                      #
# --------------------------------------------------------------------------- #
def seal_secret(secret: str, password: str) -> bytes:
    """Encrypt the secret key with the account password (age scrypt). This is what gets written to disk."""
    _require()
    if not password:
        raise CryptoError("cannot seal with an empty password")
    return passphrase.encrypt(secret.strip().encode(), password)


def open_secret(sealed: bytes, password: str) -> str:
    """Open a password-protected secret key."""
    _require()
    try:
        return passphrase.decrypt(sealed, password).decode()
    except Exception as e:  # noqa: BLE001
        raise CryptoError(f"could not open secret key (wrong password?): {e}") from e
