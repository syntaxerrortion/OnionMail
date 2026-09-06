# Sunucu (Debian 13) üzerinde Tor + onionmail kurulumu

Bu adımlar **sunucu bilgisayarda** yapılır. Geliştirme makinene hiçbir şey kurulmaz.

## 1. Tor

```bash
sudo apt update
sudo apt install tor
```

`/etc/tor/torrc` sonuna ekle:

```
HiddenServiceDir /var/lib/tor/onionmail/
HiddenServiceVersion 3
HiddenServicePort 25 127.0.0.1:8025
HiddenServicePort 8443 127.0.0.1:8443

# SOCKS zaten 9050'de açık (varsayılan). Kapalıysa:
SOCKSPort 127.0.0.1:9050
```

```bash
sudo systemctl restart tor
sudo cat /var/lib/tor/onionmail/hostname     # 56 karakter + .onion  -> senin adresin
```

> Tek `HiddenServiceDir` iki portu da aynı `.onion` adresinde yayınlar:
> `:25` -> gelen mail (`smtpd`), `:8443` -> istemci API (`apid`, GUI buraya bağlanır).
> 25'i root gerektirmeden dinlememek için yerelde 8025 kullanıyoruz.
> Sadece tek kutu (çok kullanıcı yok) istiyorsan `:8443` satırını atla.

## 2. Servis kullanıcısı ve dizinler

```bash
sudo useradd --system --home /var/lib/onionmail --shell /usr/sbin/nologin onionmail
sudo mkdir -p /var/lib/onionmail /etc/onionmail /opt/onionmail
sudo chown -R onionmail:onionmail /var/lib/onionmail

# smtpd'nin onion adresini okuyabilmesi için:
sudo usermod -aG debian-tor onionmail
sudo chmod 750 /var/lib/tor/onionmail
```

## 3. Uygulama

```bash
sudo rsync -a --exclude .venv --exclude .git ./ /opt/onionmail/
cd /opt/onionmail
sudo python3 -m venv .venv
sudo .venv/bin/pip install -e .
sudo chown -R onionmail:onionmail /opt/onionmail
```

`/etc/onionmail/config.toml` (örnek `config.example.toml`):

```toml
[identity]
onion = ""                                    # boş bırak -> hostname_file'dan okunur
hostname_file = "/var/lib/tor/onionmail/hostname"
local_user = "nejarm"

[smtpd]
host = "127.0.0.1"
port = 8025

[sender]
socks_host = "127.0.0.1"
socks_port = 9050

[storage]
maildir = "/var/lib/onionmail/Maildir"   # tek kutu modunda kullanılır

[sandbox]
backend = "firejail"     # sunucuda: sudo apt install firejail

# --- çok kullanıcılı mod (ağdan login isteniyorsa) ---
[accounts]
enabled = true
store_path = "/var/lib/onionmail/accounts"
open_registration = false     # kayıt davet kodu ister

[api]
host = "127.0.0.1"
port = 8443
preshared_key = ""            # istersen paylaşılan bir sır ekle (istemci de verir)
```

```bash
sudo chown onionmail:onionmail /etc/onionmail/config.toml
sudo chmod 640 /etc/onionmail/config.toml
```

## 4. systemd

```bash
sudo cp systemd/onionmail-*.service /etc/systemd/system/
sudo systemctl daemon-reload
# tek kutu:
sudo systemctl enable --now onionmail-smtpd onionmail-sender
# çok kullanıcılı ([accounts].enabled=true ise ayrıca):
sudo systemctl enable --now onionmail-apid
systemctl status onionmail-smtpd onionmail-sender onionmail-apid
```

## 4b. Hesaplar ve davet kodları (çok kullanıcılı mod)

```bash
S="sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml /opt/onionmail/.venv/bin/python -m onionmail"

# kendine bir hesap (admin yolu, davet gerekmez)
$S useradd nejarm

# arkadaşlar için tek kullanımlık davet kodu üret ve onlara ver
$S invite -n 3

# şifre sıfırlama
$S passwd nejarm
```

Arkadaşın GUI'yi kendi PC'sinde açar → sunucu `.onion` adresini girer →
"Bağlantıyı test et" → **Kaydol** sekmesinde kullanıcı adı + şifre + davet kodu.
Sonraki açılışlarda oturum token'ı `~/.config/onionmail/session.json`'da tutulur.

## 5. TUI

TUI'yi kendi kullanıcınla (servis kullanıcısı değil) çalıştır; ama Maildir
`onionmail` kullanıcısına ait. En temizi TUI'yi de `onionmail` olarak çalıştırmak:

```bash
sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml \
     /opt/onionmail/.venv/bin/python -m onionmail tui
```

## 6. Test

İki tarafın da onionmail çalıştırması gerekir. Kendi kendine test:

```bash
# durum
sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml \
     /opt/onionmail/.venv/bin/python -m onionmail status

# kendine mail (onion -> aynı onion)
echo "merhaba ben" | sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml \
     /opt/onionmail/.venv/bin/python -m onionmail send \
     --to nejarm@$(sudo cat /var/lib/tor/onionmail/hostname) --subject "self test"
```

Birkaç saniye–dakika sonra INBOX'ta görünmeli (onion devresi kurulması zaman alır).

## Ağ notları

* Router'da **port yönlendirmeye gerek yok** — onion service giden bağlantıyla
  çalışır, sunucunun dışarıdan erişilebilir olması gerekmez.
* Dinamik ev IP'si sorun değil; onion adresi sabit kalır.
* Sunucuyu kapatırsan adres erişilemez olur; açınca geri gelir.
