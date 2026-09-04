"""onionmail command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config


def _load(args) -> Config:
    return Config.load(args.config)


def cmd_smtpd(args) -> int:
    from . import smtpd

    smtpd.run(_load(args))
    return 0


def cmd_sender(args) -> int:
    from . import sender

    sender.run(_load(args), interval=args.interval)
    return 0


def cmd_tui(args) -> int:
    from .tui import OnionMailApp

    OnionMailApp(_load(args)).run()
    return 0


def cmd_apid(args) -> int:
    from . import apid

    apid.run(_load(args))
    return 0


def cmd_invite(args) -> int:
    from .accounts import Accounts, Policy

    cfg = _load(args)
    acc = Accounts(cfg.accounts.store_path_p, Policy(
        open_registration=cfg.accounts.open_registration))
    for _ in range(max(1, args.count)):
        print(acc.new_invite())
    return 0


def cmd_useradd(args) -> int:
    import getpass

    from .accounts import AccountError, Accounts, Policy

    cfg = _load(args)
    acc = Accounts(cfg.accounts.store_path_p, Policy(
        open_registration=True,  # admin path bypasses invite
        min_password_len=cfg.accounts.min_password_len))
    pw = args.password or getpass.getpass(f"{args.user} için şifre: ")
    try:
        acc.create(args.user, pw, invite=None)
    except AccountError as e:
        print(f"hata: {e}", file=sys.stderr)
        return 1
    print(f"hesap oluşturuldu: {args.user}@{cfg.identity.resolve_onion() or '<onion>'}")
    return 0


def cmd_passwd(args) -> int:
    import getpass

    from .accounts import AccountError, Accounts

    cfg = _load(args)
    acc = Accounts(cfg.accounts.store_path_p)
    pw = getpass.getpass(f"{args.user} için yeni şifre: ")
    try:
        acc.set_password(args.user, pw)
    except AccountError as e:
        print(f"hata: {e}", file=sys.stderr)
        return 1
    print("şifre güncellendi")
    return 0


def cmd_gui(args) -> int:
    from .gui import run

    run(_load(args), local=args.local)
    return 0


def _resolve_account(cfg, user_arg: str | None) -> str:
    """Çok kullanıcılı modda CLI mesajını hangi hesabın göndereceğini seç."""
    from .accounts import Accounts

    users = Accounts(cfg.accounts.store_path_p).list_users()
    if not users:
        raise ValueError("hesap yok (accounts.enabled=true ama kayıtlı kullanıcı yok)")
    if user_arg:
        u = user_arg.strip().lower()
        if u not in users:
            raise ValueError(f"böyle bir hesap yok: {user_arg}  "
                             f"(var olanlar: {', '.join(users)})")
        return u
    lu = (cfg.identity.local_user or "").lower()
    if lu in users:
        return lu
    if len(users) == 1:
        return users[0]
    raise ValueError(f"birden çok hesap var, --from ile seç: {', '.join(users)}")


def cmd_send(args) -> int:
    from .accounts import Accounts
    from .compose import build_message, queue_message, recipients_of
    from .store import Store

    cfg = _load(args)
    body = sys.stdin.read() if not args.body_file else Path(args.body_file).read_text()

    accounts_obj = Accounts(cfg.accounts.store_path_p) if cfg.accounts.enabled else None
    if accounts_obj is not None:
        user = _resolve_account(cfg, args.from_user)
        store = Store(accounts_obj.maildir_for(user))
    else:
        user = None
        store = Store(cfg.storage.maildir_path)

    msg = build_message(
        cfg,
        to=args.to,
        subject=args.subject,
        body=body,
        attachments=[Path(a) for a in args.attach or []],
        from_user=user,
    )

    encrypt_to = None
    if args.encrypt:
        manual: dict[str, str] = {}
        for kv in args.pubkey:
            addr, sep, pub = kv.partition("=")
            if not sep or not addr.strip() or not pub.strip():
                raise SystemExit(f"--pubkey biçimi 'adres=age1...' olmalı: {kv!r}")
            manual[addr.strip().lower()] = pub.strip()

        onion = cfg.identity.resolve_onion() or ""
        pubs, missing = [], []
        for addr in recipients_of(msg):
            pub = manual.get(addr)
            if not pub and accounts_obj is not None and onion and addr.endswith("@" + onion):
                pub = accounts_obj.get_pubkey(addr.split("@", 1)[0])
            if pub:
                pubs.append(pub)
            else:
                missing.append(addr)
        if missing:
            raise SystemExit(
                "şu alıcı(lar) için age açık anahtarı bulunamadı, --encrypt "
                "kullanılamıyor: " + ", ".join(missing) +
                "\n(--pubkey adres=age1... ile elle ver, ya da alıcı hesap "
                "istemciden pubkey_set yapmış olmalı)")
        encrypt_to = pubs

    queue_message(cfg, store, msg, encrypt_to=encrypt_to)
    who = f"{user}@{cfg.identity.resolve_onion() or '<onion>'}" if user else "yerel kutu"
    tail = " (şifreli)" if encrypt_to else ""
    print(f"kuyruğa alındı{tail} ({who}); 'onionmail sender' çalışıyorsa gönderilecek")
    return 0


def cmd_status(args) -> int:
    import socket

    from .accounts import Accounts
    from .store import Store

    cfg = _load(args)
    onion = cfg.identity.resolve_onion()

    print(f"config       : {cfg.source_path or '(defaults)'}")
    print(f"onion        : {onion or '(bilinmiyor — Tor ayakta mı?)'}")

    if cfg.accounts.enabled:
        acc = Accounts(cfg.accounts.store_path_p)
        users = acc.list_users()
        print(f"mod          : çok kullanıcılı ({len(users)} hesap)")
        tin = tq = tr = 0
        for u in users:
            st = Store(acc.maildir_for(u))
            q = st.queue()
            nin, nq = len(st.list("INBOX")), len(q)
            nr = sum(len(e.rcpts) for e in q)
            tin += nin; tq += nq; tr += nr
            print(f"  {u:<16} inbox:{nin:<4} kuyruk:{nq} ({nr} alıcı)")
        print(f"toplam       : inbox {tin}, kuyruk {tq} bekleyen, {tr} alıcı")
    else:
        store = Store(cfg.storage.maildir_path)
        q = store.queue()
        print("mod          : tek kutu")
        print(f"maildir      : {cfg.storage.maildir_path}")
        print(f"inbox        : {len(store.list('INBOX'))} mesaj")
        print(f"kuyruk       : {len(q)} bekleyen, {sum(len(e.rcpts) for e in q)} alıcı")

    sock = socket.socket()
    sock.settimeout(2)
    reachable = sock.connect_ex((cfg.sender.socks_host, cfg.sender.socks_port)) == 0
    sock.close()
    print(f"tor SOCKS    : {cfg.sender.socks_host}:{cfg.sender.socks_port} "
          f"{'erişilebilir' if reachable else 'ERİŞİLEMİYOR'}")
    return 0


def cmd_setup_tor(args) -> int:
    cfg = _load(args)
    api_line = (
        f"HiddenServicePort {cfg.api.port} {cfg.api.host}:{cfg.api.port}"
        if cfg.accounts.enabled
        else f"# HiddenServicePort {cfg.api.port} {cfg.api.host}:{cfg.api.port}"
        "   (accounts.enabled=true ise aç)"
    )
    print(f"""# /etc/tor/torrc içine ekle, sonra:  sudo systemctl restart tor
HiddenServiceDir /var/lib/tor/onionmail/
HiddenServiceVersion 3
HiddenServicePort 25 {cfg.smtpd.host}:{cfg.smtpd.port}
{api_line}

# Aynı HiddenServiceDir iki portu da tek .onion adresinde yayınlar:
#   :25    -> gelen mail (smtpd)
#   :{cfg.api.port}  -> istemci API (apid) — GUI buraya bağlanır
# Adresi oku:  sudo cat /var/lib/tor/onionmail/hostname
# Bu değeri sunucu config.toml -> [identity].onion (veya hostname_file) ile ver;
# istemcilere de aynı adresi [client].server_onion olarak dağıt.
""")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="onionmail", description=__doc__)
    p.add_argument("-c", "--config", help="config.toml yolu")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("smtpd", help="gelen SMTP sunucusunu çalıştır")
    sp.set_defaults(func=cmd_smtpd)

    sp = sub.add_parser("sender", help="giden kuyruk işleyicisini çalıştır")
    sp.add_argument("--interval", type=int, default=30, help="kuyruk tarama aralığı (sn)")
    sp.set_defaults(func=cmd_sender)

    sp = sub.add_parser("tui", help="terminal arayüzünü aç")
    sp.set_defaults(func=cmd_tui)

    sp = sub.add_parser("gui", help="native masaüstü penceresini aç (PySide6)")
    sp.add_argument("--local", action="store_true",
                    help="uzak sunucuya bağlanma; yerel Maildir kullan (dm1'de)")
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser("apid", help="hesap/mail API servisini çalıştır (2. onion portu)")
    sp.set_defaults(func=cmd_apid)

    sp = sub.add_parser("invite", help="tek kullanımlık davet kodu üret")
    sp.add_argument("-n", "--count", type=int, default=1, help="kaç kod")
    sp.set_defaults(func=cmd_invite)

    sp = sub.add_parser("useradd", help="hesap oluştur (admin; davet kodu gerekmez)")
    sp.add_argument("user")
    sp.add_argument("--password", help="verilmezse sorulur")
    sp.set_defaults(func=cmd_useradd)

    sp = sub.add_parser("passwd", help="bir hesabın şifresini değiştir")
    sp.add_argument("user")
    sp.set_defaults(func=cmd_passwd)

    sp = sub.add_parser("send", help="tek bir mesajı kuyruğa al")
    sp.add_argument("--to", action="append", required=True, help="alıcı@<onion> (tekrarlanabilir)")
    sp.add_argument("--subject", default="(konu yok)")
    sp.add_argument("--body-file", help="gövde dosyası (yoksa stdin)")
    sp.add_argument("--attach", action="append", help="eklenecek dosya (tekrarlanabilir)")
    sp.add_argument("--from", dest="from_user", metavar="KULLANICI",
                    help="çok kullanıcılı modda gönderen hesap (yoksa local_user / tek hesap)")
    sp.add_argument("--encrypt", action="store_true",
                    help="age ile uçtan uca şifrele (tüm alıcıların açık anahtarı gerekir)")
    sp.add_argument("--pubkey", action="append", default=[], metavar="ADRES=AGE1ANAHTAR",
                    help="alıcı için age açık anahtarı (tekrarlanabilir); aynı sunucudaki "
                         "hesaplar apid'e kayıtlıysa otomatik çözülür")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("status", help="durum özeti")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("setup-tor", help="torrc parçacığını yazdır")
    sp.set_defaults(func=cmd_setup_tor)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, FileNotFoundError) as e:
        print(f"hata: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
