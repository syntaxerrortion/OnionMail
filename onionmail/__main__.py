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


def cmd_send(args) -> int:
    from .compose import build_message, queue_message
    from .store import Store

    cfg = _load(args)
    body = sys.stdin.read() if not args.body_file else Path(args.body_file).read_text()
    msg = build_message(
        cfg,
        to=args.to,
        subject=args.subject,
        body=body,
        attachments=[Path(a) for a in args.attach or []],
    )
    store = Store(cfg.storage.maildir_path)
    queue_message(cfg, store, msg)
    print("kuyruğa alındı; 'onionmail sender' çalışıyorsa gönderilecek")
    return 0


def cmd_status(args) -> int:
    import socket

    from .store import Store

    cfg = _load(args)
    onion = cfg.identity.resolve_onion()
    store = Store(cfg.storage.maildir_path)
    q = store.queue()

    print(f"config       : {cfg.source_path or '(defaults)'}")
    print(f"onion        : {onion or '(bilinmiyor — Tor ayakta mı?)'}")
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
