"""age (X25519) şifreleme primitifleri."""

from __future__ import annotations

import pytest

from onionmail import crypto

pytestmark = pytest.mark.skipif(not crypto.HAVE_AGE, reason="pyrage kurulu değil")


def test_identity_shape_and_derivation():
    sec, pub = crypto.generate_identity()
    assert sec.startswith("AGE-SECRET-KEY-1")
    assert pub.startswith("age1")
    assert crypto.public_from_secret(sec) == pub


def test_public_from_bad_secret():
    with pytest.raises(crypto.CryptoError):
        crypto.public_from_secret("AGE-SECRET-KEY-1-bozuk")


def test_fingerprint_is_stable_and_formatted():
    _, pub = crypto.generate_identity()
    fp = crypto.fingerprint(pub)
    assert fp == crypto.fingerprint(pub + "\n")          # trim edilir
    assert len(fp) == 19 and fp.count("-") == 3          # ABCD-1234-5678-9ABC
    assert crypto.fingerprint(pub) != crypto.fingerprint(crypto.generate_identity()[1])


def test_encrypt_roundtrip_single():
    sec, pub = crypto.generate_identity()
    ct = crypto.encrypt_for(b"gizli mesaj", [pub])
    assert ct[:20] != b"gizli mesaj"[:20]
    assert crypto.decrypt_with(ct, sec) == b"gizli mesaj"


def test_encrypt_multi_recipient():
    a_sec, a_pub = crypto.generate_identity()
    b_sec, b_pub = crypto.generate_identity()
    ct = crypto.encrypt_for(b"herkese", [a_pub, b_pub])
    assert crypto.decrypt_with(ct, a_sec) == b"herkese"
    assert crypto.decrypt_with(ct, b_sec) == b"herkese"


def test_decrypt_with_wrong_key_fails():
    _, pub = crypto.generate_identity()
    other_sec, _ = crypto.generate_identity()
    ct = crypto.encrypt_for(b"x", [pub])
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_with(ct, other_sec)


def test_encrypt_needs_a_recipient():
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt_for(b"x", [])
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt_for(b"x", ["   "])


def test_sealed_secret_roundtrip():
    sec, _ = crypto.generate_identity()
    sealed = crypto.seal_secret(sec, "hesap-parolam")
    assert sec.encode() not in sealed
    assert crypto.open_secret(sealed, "hesap-parolam") == sec


def test_sealed_secret_wrong_password():
    sec, _ = crypto.generate_identity()
    sealed = crypto.seal_secret(sec, "dogru")
    with pytest.raises(crypto.CryptoError):
        crypto.open_secret(sealed, "yanlis")


def test_seal_rejects_empty_password():
    sec, _ = crypto.generate_identity()
    with pytest.raises(crypto.CryptoError):
        crypto.seal_secret(sec, "")
