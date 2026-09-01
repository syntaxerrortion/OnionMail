"""Uçtan uca şifreleme — age (X25519) sarmalayıcıları.

Amaç: sunucu yalnızca şifreli blob görür. Giden mesajın tüm iç MIME'ı
(başlıklar, gövde, ekler) tek bir age şifreli parçaya sarılır (bkz.
``compose.py``). Özel anahtar hiçbir zaman istemciden çıkmaz; disk üzerinde
hesap parolasıyla (age scrypt) şifreli durur.

pyrage kurulu değilse ``HAVE_AGE`` False olur ve her çağrı ``CryptoError``
fırlatır — çağıran taraf bu durumda düz metne düşer.
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
    """Anahtar geçersiz, parola yanlış, şifre çözülemedi vb."""


def _require() -> None:
    if not HAVE_AGE:
        raise CryptoError("pyrage kurulu değil — 'pip install pyrage' (opsiyonel 'e2e' grubu)")


# --------------------------------------------------------------------------- #
#  Anahtar çifti                                                              #
# --------------------------------------------------------------------------- #
def generate_identity() -> tuple[str, str]:
    """Yeni age anahtar çifti üret. Döner: ``(secret_key, public_key)`` —
    ``AGE-SECRET-KEY-1…`` ve ``age1…`` biçiminde."""
    _require()
    ident = x25519.Identity.generate()
    return str(ident), str(ident.to_public())


def public_from_secret(secret: str) -> str:
    """Özel anahtardan açık anahtarı türet (doğrulama / kurtarma için)."""
    _require()
    try:
        return str(x25519.Identity.from_str(secret.strip()).to_public())
    except Exception as e:  # noqa: BLE001 - pyrage kendi hata tiplerini verir
        raise CryptoError(f"geçersiz özel anahtar: {e}") from e


def fingerprint(public_key: str) -> str:
    """Açık anahtarın kısa parmak izi — TOFU doğrulamasında kullanıcıya
    gösterilir. SHA-256'nın ilk 8 baytı, ``ABCD-1234-…`` biçiminde."""
    h = hashlib.sha256(public_key.strip().encode()).digest()[:8]
    return "-".join(h.hex()[i:i + 4].upper() for i in range(0, 16, 4))


# --------------------------------------------------------------------------- #
#  Mesaj şifreleme                                                            #
# --------------------------------------------------------------------------- #
def encrypt_for(plaintext: bytes, recipient_pubkeys: list[str]) -> bytes:
    """``plaintext``'i verilen açık anahtarların HEPSİNE çöz(ül)ebilir şekilde
    şifrele (age çok-alıcılı: her alıcı tek başına açar)."""
    _require()
    keys = [k.strip() for k in recipient_pubkeys if k and k.strip()]
    if not keys:
        raise CryptoError("alıcı açık anahtarı yok")
    try:
        rcpts = [x25519.Recipient.from_str(k) for k in keys]
        return pyrage.encrypt(plaintext, rcpts)
    except Exception as e:  # noqa: BLE001
        raise CryptoError(f"şifreleme başarısız: {e}") from e


def decrypt_with(ciphertext: bytes, secret: str) -> bytes:
    """``ciphertext``'i özel anahtarla çöz."""
    _require()
    try:
        return pyrage.decrypt(ciphertext, [x25519.Identity.from_str(secret.strip())])
    except Exception as e:  # noqa: BLE001
        raise CryptoError(
            f"şifre çözme başarısız (anahtar bu mesaj için değil olabilir): {e}") from e


# --------------------------------------------------------------------------- #
#  Disk üzerinde parola korumalı özel anahtar                                 #
# --------------------------------------------------------------------------- #
def seal_secret(secret: str, password: str) -> bytes:
    """Özel anahtarı hesap parolasıyla şifrele (age scrypt). Diske bu yazılır."""
    _require()
    if not password:
        raise CryptoError("boş parola ile mühürlenemez")
    return passphrase.encrypt(secret.strip().encode(), password)


def open_secret(sealed: bytes, password: str) -> str:
    """Parola korumalı özel anahtarı aç."""
    _require()
    try:
        return passphrase.decrypt(sealed, password).decode()
    except Exception as e:  # noqa: BLE001
        raise CryptoError(f"özel anahtar açılamadı (parola yanlış?): {e}") from e
