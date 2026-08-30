# onionmail istemcisini paketleme (Windows / macOS / Linux `.exe`)

Amaç: arkadaşlar **Python / Tor kurmadan** çift tıklayıp bağlansın.

## Ne üretilir

`dist/onionmail/` klasörü (onedir) → zip'lenip dağıtılır. İçinde:

- `onionmail(.exe)` — `packaging/launcher.py`'den PyInstaller ile
- `tor/tor(.exe)` — Tor Expert Bundle'dan; uygulama açılışta kendi Tor'unu
  başlatır (`SocksPort 9250`, kendi `DataDirectory`'si)
- `client.default.json` — sunucu onion'u burada gömülüyse ilk açılışta
  `~/.config/onionmail/client.json`'a kopyalanır; boşsa kullanıcı GUI'de girer

Kullanıcı deneyimi: exe'yi çalıştır → Tor bootstrap (~10-30 sn) → **Giriş
ekranı** → onion + davet kodu + kullanıcı adı + şifre → posta kutusu.

## GitHub Actions (önerilen — çok-OS otomatik)

1. Bu depoyu GitHub'a it (aşağıda "Depoyu oluştur").
2. (İsteğe bağlı) **Settings → Secrets and variables → Actions → Variables**
   altına `SERVER_ONION = <sunucunun .onion adresi>` ekle. Tanımlıysa paketlere
   gömülür.
3. Bir sürüm etiketi at:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
4. Actions çalışır; Windows/macOS/Linux zip'leri hem **artifact** hem de
   **Release** eki olarak yüklenir. `workflow_dispatch` ile elle de tetiklenir.

Tor sürümü `.github/workflows/build.yml` içinde sabit (`V=14.0.1`) — Tor Expert
Bundle güncellendikçe orayı yükselt.

## Elle derleme (tek OS)

Hedef OS'un kendisinde çalıştır (Windows exe'si Windows'ta derlenir):

```bash
python -m venv .venv && . .venv/bin/activate      # win: .venv\Scripts\activate
pip install pyinstaller PySide6 PySocks argon2-cffi aiosmtpd textual
pip install -e .

# Tor Expert Bundle'ı indir, aç, tor/ klasörüne koy:
#   https://www.torproject.org/download/tor/  -> "Expert Bundle"
#   tor/tor(.exe) olacak şekilde

TOR_DIR=tor pyinstaller --noconfirm packaging/onionmail.spec
# çıktı: dist/onionmail/
```

## İmzalama / SmartScreen / antivirüs

- İmzasız exe'de Windows **SmartScreen** ("Daha fazla bilgi → Yine de çalıştır")
  ve macOS **Gatekeeper** (sağ tık → Aç) uyarısı çıkar. Normal.
- PyInstaller onedir, `--onefile`'a göre antivirüs yanlış-pozitifi daha az verir.
- Gerçek imza istiyorsan: Windows için Authenticode sertifikası (ücretli),
  macOS için Apple Developer ID + notarization.

## Depoyu oluştur (gh CLI yoksa)

```bash
cd ~/onionmail
git init && git add -A && git commit -m "onionmail + packaging"
git branch -M main
# GitHub'da web'den boş bir repo aç (README ekleme), sonra:
git remote add origin git@github.com:<kullanıcı>/onionmail.git
git push -u origin main
```

> Depo herkese açıksa: kod açığa çıkar (sır yok — onion adresi ve davet
> kodları kodda değil). Yine de istemiyorsan **private** repo + Actions yine
> çalışır (kişisel hesapta private için aylık ücretsiz dakika sınırı var ama
> bu iş için fazlasıyla yeter).

## Notlar

- `launcher.py` gömülü tor yoksa: açık bir `127.0.0.1:9250`'yi, sonra Tor
  Browser'ın `9150`'sini dener.
- `sandbox.backend` Windows/macOS'ta pratikte `none` — ekler yalnız diske
  çıkarılır (firejail/bwrap yok). İzole açma Linux'a özgü.
- roomcam-view bu pakete dahil değil (ayrı araç).
