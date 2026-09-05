"""Kişi defteri — takma ad → onion adresi eşlemesi."""

from __future__ import annotations

from pathlib import Path

import pytest

from onionmail.contacts import Contacts, ContactError

ADDR = "bob@" + "b" * 56 + ".onion"
ADDR2 = "carol@" + "c" * 56 + ".onion"


def test_add_get_remove(tmp_path: Path):
    c = Contacts(tmp_path)
    c.add("Bob", ADDR, note="iş arkadaşı")
    rec = c.get("bob")  # case-insensitive anahtar
    assert rec["nick"] == "Bob"
    assert rec["address"] == ADDR
    assert rec["note"] == "iş arkadaşı"

    c.remove("BOB")
    assert c.get("bob") is None


def test_rejects_bad_nick_or_address(tmp_path: Path):
    c = Contacts(tmp_path)
    with pytest.raises(ContactError):
        c.add("bob,evil", ADDR)
    with pytest.raises(ContactError):
        c.add("bob", "not-an-onion-address")


def test_resolve_falls_back_to_raw_text(tmp_path: Path):
    c = Contacts(tmp_path)
    c.add("bob", ADDR)
    assert c.resolve("bob") == ADDR
    assert c.resolve("Bob") == ADDR             # case-insensitive
    assert c.resolve(ADDR2) == ADDR2            # bilinmeyen -> olduğu gibi
    assert c.resolve_list(["bob", ADDR2]) == [ADDR, ADDR2]


def test_find_by_address(tmp_path: Path):
    c = Contacts(tmp_path)
    c.add("bob", ADDR)
    assert c.find_by_address(ADDR) == "bob"
    assert c.find_by_address(ADDR2) is None


def test_persists_across_instances(tmp_path: Path):
    Contacts(tmp_path).add("bob", ADDR)
    assert Contacts(tmp_path).get("bob")["address"] == ADDR
