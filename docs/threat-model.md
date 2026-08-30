# Tehdit modeli ve sınırlar

## Ne sağlar

* **Sunucu konumu gizli.** Onion service, dm1'in IP'sini/coğrafi konumunu
  açığa çıkarmaz. Router port yönlendirmesi gerekmez.
* **Taşımada şifreleme.** Onion-to-onion trafiği Tor tarafından uçtan uca
  şifrelenir; STARTTLS olmadan da SMTP konuşması ağda düz görünmez.
* **Kapalı ağ.** Sunucu yalnızca kendi adresine gelen maili kabul eder
  (`relay denied`). Açık relay değil.
* **Politika allowlist'i.** `policy.allowlist_only = true` ile yalnızca bilinen
  onion adreslerinden mail alınır — rastgele onion spam'i engellenir.

## Ne sağlamaz / dikkat

* **Diskte düz metin.** Mesajlar her iki sunucuda Maildir'de şifresiz durur.
  İçerik gizliliği istiyorsan **gövdeyi PGP ile şifrele** (v0.2 hedefi:
  `onionmail/pgp.py`). Şimdilik istemci tarafında elle yapılabilir.
* **Metadata alıcıda görünür.** Karşı sunucu: gönderen adresi, zaman, boyut,
  Subject başlığını görür. Subject'i de korumak istiyorsan PGP/MIME gerekir.
* **Uç nokta güvenliği.** dm1 ele geçirilirse tüm posta ve onion özel anahtarı
  (`/var/lib/tor/onionmail/`) açığa çıkar. Bu anahtar = adresin; yedekle ve
  erişimini kısıtla (`chmod 700`, ayrı kullanıcı).
* **Clearnet yok.** `gmail.com` vb. adreslere gönderemezsin. Yazışacağın
  herkesin kendi onion mail sunucusu olmalı.
* **Trafik analizi.** Global bir gözlemci zamanlama/hacim korelasyonu
  deneyebilir. Yüksek tehdit modeli için mesaj boyutu doldurma / gecikme
  eklemek gerekir — bu projede yok.
* **Sandbox mutlak değil.** `firejail --net=none` ekin ağa çıkmasını ve ev
  dizinine erişmesini engeller ama çekirdek açıkları / firejail yanlış
  yapılandırması riski kalır. Gerçekten şüpheli şeyler için tek kullanımlık VM
  kullan. `backend = "none"` ise ekler yalnızca diske çıkarılır, açılmaz.

## Güvenli varsayılanlar (kod)

* smtpd yalnızca loopback dinler; `config.validate()` 0.0.0.0'a izin vermez.
* Giden SOCKS'ta `rdns=True` — `.onion` adı yerelde çözülmez, DNS sızıntısı yok.
* Ek dosya adları `sanitize_filename()` ile temizlenir (path traversal, kontrol
  karakterleri, boşluk); yalnızca basename kullanılır.
* HTML gövde gerçek motorla render edilmez; `safe_text()` script/style siler,
  etiketleri düşürür, uzak içerik yüklenmez.
* Gelen mesaj boyutu `smtpd.max_message_bytes` ile sınırlı.
