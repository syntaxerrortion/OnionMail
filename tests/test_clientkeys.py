"""Client age key store — own identity + TOFU peer directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from onionmail import crypto
from onionmail.clientkeys import ClientKeys

ADDR = "me@" + "a" * 56 + ".onion"
PEER = "bob@" + "b" * 56 + ".onion"

age = pytest.mark.skipif(not crypto.HAVE_AGE, reason="pyrage not installed")


# --- TOFU peer directory (works without pyrage) ---------------------------

def test_remember_peer_tofu(tmp_path: Path):
    k = ClientKeys(tmp_path)
    pub1 = "age1" + "q" * 55
    pub2 = "age1" + "z" * 55

    assert k.remember_peer(PEER, pub1) == "new"
    assert k.remember_peer(PEER, pub1) == "same"
    assert k.remember_peer(PEER, pub2) == "changed"        # does not overwrite
    assert k.peer_pubkey(PEER) == pub1
    assert k.remember_peer(PEER, "garbage") == "ignored"

    k.set_peer(PEER, pub2, verified=True)                  # accept the change
    assert k.peer_pubkey(PEER) == pub2
    assert k.peer_info(PEER)["verified"] is True
    assert k.peer_info(PEER)["fingerprint"] == crypto.fingerprint(pub2)

    k.forget_peer(PEER)
    assert k.peer_pubkey(PEER) is None


def test_set_peer_rejects_bad_key(tmp_path: Path):
    k = ClientKeys(tmp_path)
    with pytest.raises(crypto.CryptoError):
        k.set_peer(PEER, "not-age")


def test_known_keys_persists_across_instances(tmp_path: Path):
    ClientKeys(tmp_path).remember_peer(PEER, "age1" + "q" * 55)
    assert ClientKeys(tmp_path).peer_pubkey(PEER) == "age1" + "q" * 55


# --- kendi kimlik (pyrage gerekir) ----------------------------------

@age
def test_unlock_or_create_roundtrip(tmp_path: Path):
    k = ClientKeys(tmp_path)
    sec, pub, new = k.unlock_or_create(ADDR, "my-account-password")
    assert new is True
    assert pub.startswith("age1") and sec.startswith("AGE-SECRET-KEY-1")
    assert k.unlocked and k.public == pub

    k2 = ClientKeys(tmp_path)
    sec2, pub2, new2 = k2.unlock_or_create(ADDR, "my-account-password")
    assert new2 is False
    assert (sec2, pub2) == (sec, pub)

    assert k2.my_public(ADDR) == pub
    assert (tmp_path / "keys.json").stat().st_mode & 0o077 == 0    # 600


@age
def test_unlock_wrong_password(tmp_path: Path):
    ClientKeys(tmp_path).unlock_or_create(ADDR, "dogru")
    with pytest.raises(crypto.CryptoError):
        ClientKeys(tmp_path).unlock_or_create(ADDR, "yanlis")


@age
def test_secret_never_on_disk_in_clear(tmp_path: Path):
    k = ClientKeys(tmp_path)
    sec, _, _ = k.unlock_or_create(ADDR, "password")
    assert sec.encode() not in (tmp_path / "keys.json").read_bytes()


@age
def test_change_password(tmp_path: Path):
    k = ClientKeys(tmp_path)
    sec, _, _ = k.unlock_or_create(ADDR, "old")
    k.change_password(ADDR, "old", "new")
    with pytest.raises(crypto.CryptoError):
        ClientKeys(tmp_path).unlock_or_create(ADDR, "old")
    sec2, _, new = ClientKeys(tmp_path).unlock_or_create(ADDR, "new")
    assert new is False and sec2 == sec
