"""İstemci tarafı age anahtar deposu (uçtan uca şifreleme).

İki dosya, ``~/.config/onionmail/`` altında (client.json / session.json yanında):

* ``keys.json``       — kendi kimliklerin: ``{adres: {public, sealed, created}}``.
  ``sealed`` = özel anahtarın hesap parolasıyla age-scrypt mühürlü hâli (base64).
  Özel anahtar asla düz yazılmaz, sunucuya hiç gitmez.
* ``known_keys.json`` — TOFU eş dizini: ``{adres: {public, fingerprint,
  first_seen, verified, source}}``. Bir eşin anahtarı değişirse SESSIZCE
  güncellenmez; ``remember_peer`` "changed" döner, kararı çağıran verir.

pyrage yoksa: eş dizini (fingerprint dâhil) yine çalışır; yalnızca kendi
kimliğini açmak/oluşturmak ``CryptoError`` fırlatır.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path


class ClientKeys:
    def __init__(self, config_dir: Path):
        self.dir = Path(config_dir)
        self._keys_path = self.dir / "keys.json"
        self._known_path = self.dir / "known_keys.json"
        # o an açık olan oturumun kimliği (unlock_or_create sonrası dolar)
        self.address: str = ""
        self.secret: str | None = None
        self.public: str | None = None

    @property
    def unlocked(self) -> bool:
        return bool(self.secret)

    # -- disk ----------------------------------------------------------
    @staticmethod
    def _read(path: Path) -> dict:
        try:
            d = json.loads(path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, path: Path, data: dict, *, private: bool = False) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        if private:
            os.chmod(tmp, 0o600)
        tmp.replace(path)

    # -- kendi kimliğin ---------------------------------------------
    def unlock_or_create(self, address: str, password: str) -> tuple[str, str, bool]:
        """``address`` için yerel age kimliğini parolayla aç; yoksa üret + mühürle.
        Döner: ``(secret, public, yeni_mi)``. Sonuç bellekte ``self`` üzerinde tutulur."""
        from . import crypto

        address = address.strip().lower()
        data = self._read(self._keys_path)
        rec = data.get(address)
        if rec:
            secret = crypto.open_secret(base64.b64decode(rec["sealed"]), password)
            public = rec.get("public") or crypto.public_from_secret(secret)
            created_new = False
        else:
            secret, public = crypto.generate_identity()
            data[address] = {
                "public": public,
                "sealed": base64.b64encode(crypto.seal_secret(secret, password)).decode(),
                "created": int(time.time()),
            }
            self._write(self._keys_path, data, private=True)
            created_new = True
        self.address, self.secret, self.public = address, secret, public
        return secret, public, created_new

    def change_password(self, address: str, old: str, new: str) -> None:
        from . import crypto

        address = address.strip().lower()
        data = self._read(self._keys_path)
        rec = data.get(address)
        if not rec:
            raise crypto.CryptoError("bu adres için yerel anahtar yok")
        secret = crypto.open_secret(base64.b64decode(rec["sealed"]), old)
        rec["sealed"] = base64.b64encode(crypto.seal_secret(secret, new)).decode()
        self._write(self._keys_path, data, private=True)

    def my_public(self, address: str) -> str | None:
        return (self._read(self._keys_path).get(address.strip().lower()) or {}).get("public")

    def lock(self) -> None:
        self.secret = self.public = None
        self.address = ""

    # -- eş dizini (TOFU) -----------------------------------------
    def remember_peer(self, address: str, public: str, source: str = "header") -> str:
        """Görülen bir eş anahtarını kaydet. Döner: ``new`` / ``same`` /
        ``changed`` (varsa ve farklıysa — üzerine YAZILMAZ) / ``ignored``."""
        from . import crypto

        address = address.strip().lower()
        public = (public or "").strip()
        if not public.startswith("age1"):
            return "ignored"
        data = self._read(self._known_path)
        cur = data.get(address)
        if cur and cur.get("public") == public:
            return "same"
        if cur:
            return "changed"
        data[address] = {
            "public": public,
            "fingerprint": crypto.fingerprint(public),
            "first_seen": int(time.time()),
            "verified": False,
            "source": source,
        }
        self._write(self._known_path, data)
        return "new"

    def set_peer(self, address: str, public: str, *, verified: bool = False,
                 source: str = "manual") -> None:
        """Bir eş anahtarını açıkça yaz (elle giriş ya da değişikliği kabul)."""
        from . import crypto

        address = address.strip().lower()
        public = public.strip()
        if not public.startswith("age1"):
            raise crypto.CryptoError("geçersiz age açık anahtarı (age1... bekleniyor)")
        prev = self._read(self._known_path).get(address) or {}
        data = self._read(self._known_path)
        data[address] = {
            "public": public,
            "fingerprint": crypto.fingerprint(public),
            "first_seen": int(prev.get("first_seen") or time.time()),
            "verified": bool(verified),
            "source": source,
        }
        self._write(self._known_path, data)

    def mark_verified(self, address: str, verified: bool = True) -> None:
        data = self._read(self._known_path)
        rec = data.get(address.strip().lower())
        if rec:
            rec["verified"] = bool(verified)
            self._write(self._known_path, data)

    def forget_peer(self, address: str) -> None:
        data = self._read(self._known_path)
        if data.pop(address.strip().lower(), None) is not None:
            self._write(self._known_path, data)

    def peer_pubkey(self, address: str) -> str | None:
        return (self._read(self._known_path).get(address.strip().lower()) or {}).get("public")

    def peer_info(self, address: str) -> dict | None:
        return self._read(self._known_path).get(address.strip().lower())

    def all_peers(self) -> dict:
        return self._read(self._known_path)
