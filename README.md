# onionmail

Tor onion service arkasında çalışan, **onion-to-onion** kişisel SMTP mail
sunucusu + terminal arayüzü. Domain yok, DNS yok, kayıt yok. IRC istemcisinin
bir sunucunun `.onion` adresine Tor üzerinden bağlanması gibi, mail teslimi de
"adresteki `.onion`'a 25. porttan bağlan" mantığıyla çalışır.

> **Kapalı ağ:** yalnızca başka onionmail (veya onion MX sunan) sunucularla
> yazışır. `gmail.com`'a mail göndermez. Bkz. `docs/threat-model.md`.

## Parçalar

| Modül | İş |
|-------|-----|
| `onionmail smtpd`  | Gelen SMTP sunucusu. Tor HiddenServicePort → buraya. Tek kutu **veya** hesap başına Maildir'e yazar. |
| `onionmail sender` | Giden kuyruk işleyicisi. Mesajı Tor SOCKS5 üzerinden hedef `.onion:25`'e teslim eder, başarısızlıkta yeniden dener. |
| `onionmail apid`   | **Hesap + mail API servisi** (2. onion portu). GUI buraya bağlanıp kimlik doğrular; register (davet kodlu) / login / list / fetch / send / delete. |
| `onionmail gui`    | **Native masaüstü penceresi** (PySide6) — siyah-beyaz "Messager" (*Who Am I* teması), ters barlar midnight. Menü: `Email · Settings · Help`. Login/Kaydol ekranı, ayrı "New Message" penceresi, Collector (sandbox). `--local` ile sunucuda doğrudan Maildir. Kurulum: `pip install -e ".[gui]"`. |
| `onionmail tui`    | Aynı arayüzün **terminal** sürümü (Textual, yerel mod). SSH üzerinden sunucuda kullanışlı. |
| `onionmail invite` / `useradd` / `passwd` | Davet kodu üret / hesap ekle / şifre değiştir (sunucuda). |
| `onionmail send` / `status` / `setup-tor` | Komut satırından mesaj / durum / `torrc` parçacığı. |

## İki mimari mod

* **Tek kutu** (`accounts.enabled=false`): GUI/TUI Maildir'i doğrudan okur; login yok. Sunucuda veya SSH ile.
* **Çok kullanıcılı** (`accounts.enabled=true`): `apid` çalışır, 2. bir onion portunda yayınlanır. GUI her PC'den Tor üzerinden bağlanır, kullanıcı adı + şifre ile giriş yapar (argon2id, oturum token'ı, davet kodlu kayıt). `Backend` arayüzü sayesinde aynı GUI kodu iki modda da çalışır.

## Sandbox

Gelen mesajdaki hiçbir şey ana sistemde gerçek bir motorla açılmaz:

* **Güvenli metin görünümü** — `text/plain` tercih edilir; HTML in-process
  nötrlenir (script/style silinir, etiketler düşürülür, uzak içerik yüklenmez).
* **Ek açma** — ek geçici bir dizine kopyalanır ve `firejail --net=none`
  (veya `bwrap`) içinde, ağ erişimi olmadan görüntüleyiciye verilir.
  `sandbox.backend = "none"` ise yalnızca diske çıkarılır.

## Hızlı başlangıç (geliştirme)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[gui,dev]"
.venv/bin/python -m pytest -q      # 13 test
```

Gerçek kurulum (sunucu / Debian 13): **`docs/tor-setup.md`**.

## Durum

- Tek kutu: gelen teslim, kuyruk + yeniden deneme, Collector/sandbox — çalışıyor.
- Çok kullanıcılı: `accounts` + `apid` protokolü + `netclient` + `Backend` +
  GUI Login/Settings — çalışıyor, **13 test geçiyor** (Tor'suz, loopback ile).
- **Gerçek onion round-trip henüz test edilmedi** (sunucu kurulunca).

Yol haritası:

- [ ] `pgp.py` — gövde şifreleme/imzalama (`python-gnupg`)
- [ ] `tui.py` → `Backend` (şu an sadece yerel mod)
- [ ] Adres defteri (onion adresleri uzun; takma ad eşlemesi)
- [ ] apid: keep-alive bağlantı, sunucu-tarafı queue durumu op'u
- [ ] Mesaj boyutu doldurma / gönderim gecikmesi (trafik analizi sertliği)
