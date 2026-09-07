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
    pw = args.password or getpass.getpass(f"password for {args.user}: ")
    try:
        acc.create(args.user, pw, invite=None)
    except AccountError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"account created: {args.user}@{cfg.identity.resolve_onion() or '<onion>'}")
    return 0


def cmd_passwd(args) -> int:
    import getpass

    from .accounts import AccountError, Accounts

    cfg = _load(args)
    acc = Accounts(cfg.accounts.store_path_p)
    pw = getpass.getpass(f"new password for {args.user}: ")
    try:
        acc.set_password(args.user, pw)
    except AccountError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("password updated")
    return 0


def cmd_gui(args) -> int:
    from .gui import run

    run(_load(args), local=args.local)
    return 0


def _resolve_account(cfg, user_arg: str | None) -> str:
    """In multi-user mode, pick which account sends the CLI message."""
    from .accounts import Accounts

    users = Accounts(cfg.accounts.store_path_p).list_users()
    if not users:
        raise ValueError("no accounts (accounts.enabled=true but no registered user)")
    if user_arg:
        u = user_arg.strip().lower()
        if u not in users:
            raise ValueError(f"no such account: {user_arg}  "
                             f"(existing: {', '.join(users)})")
        return u
    lu = (cfg.identity.local_user or "").lower()
    if lu in users:
        return lu
    if len(users) == 1:
        return users[0]
    raise ValueError(f"multiple accounts, pick one with --from: {', '.join(users)}")


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
                raise SystemExit(f"--pubkey must look like 'address=age1...': {kv!r}")
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
                "no age public key found for the following recipient(s), "
                "--encrypt cannot be used: " + ", ".join(missing) +
                "\n(pass one by hand with --pubkey address=age1..., or the "
                "recipient account must have run pubkey_set from a client)")
        encrypt_to = pubs

    queue_message(cfg, store, msg, encrypt_to=encrypt_to)
    who = f"{user}@{cfg.identity.resolve_onion() or '<onion>'}" if user else "local mailbox"
    tail = " (encrypted)" if encrypt_to else ""
    print(f"queued{tail} ({who}); will be sent if 'onionmail sender' is running")
    return 0


def cmd_status(args) -> int:
    import socket

    from .accounts import Accounts
    from .store import Store

    cfg = _load(args)
    onion = cfg.identity.resolve_onion()

    print(f"config       : {cfg.source_path or '(defaults)'}")
    print(f"onion        : {onion or '(unknown — is Tor running?)'}")

    if cfg.accounts.enabled:
        acc = Accounts(cfg.accounts.store_path_p)
        users = acc.list_users()
        print(f"mode         : multi-user ({len(users)} accounts)")
        tin = tq = tr = 0
        for u in users:
            st = Store(acc.maildir_for(u))
            q = st.queue()
            nin, nq = len(st.list("INBOX")), len(q)
            nr = sum(len(e.rcpts) for e in q)
            tin += nin; tq += nq; tr += nr
            print(f"  {u:<16} inbox:{nin:<4} queue:{nq} ({nr} recipients)")
        print(f"total        : inbox {tin}, queue {tq} pending, {tr} recipients")
    else:
        store = Store(cfg.storage.maildir_path)
        q = store.queue()
        print("mode         : single mailbox")
        print(f"maildir      : {cfg.storage.maildir_path}")
        print(f"inbox        : {len(store.list('INBOX'))} messages")
        print(f"queue        : {len(q)} pending, {sum(len(e.rcpts) for e in q)} recipients")

    sock = socket.socket()
    sock.settimeout(2)
    reachable = sock.connect_ex((cfg.sender.socks_host, cfg.sender.socks_port)) == 0
    sock.close()
    print(f"tor SOCKS    : {cfg.sender.socks_host}:{cfg.sender.socks_port} "
          f"{'reachable' if reachable else 'UNREACHABLE'}")
    return 0


def cmd_setup_tor(args) -> int:
    cfg = _load(args)
    api_line = (
        f"HiddenServicePort {cfg.api.port} {cfg.api.host}:{cfg.api.port}"
        if cfg.accounts.enabled
        else f"# HiddenServicePort {cfg.api.port} {cfg.api.host}:{cfg.api.port}"
        "   (enable if accounts.enabled=true)"
    )
    print(f"""# add to /etc/tor/torrc, then:  sudo systemctl restart tor
HiddenServiceDir /var/lib/tor/onionmail/
HiddenServiceVersion 3
HiddenServicePort 25 {cfg.smtpd.host}:{cfg.smtpd.port}
{api_line}

# One HiddenServiceDir publishes both ports on a single .onion address:
#   :25    -> incoming mail (smtpd)
#   :{cfg.api.port}  -> client API (apid) — the GUI connects here
# Read the address:  sudo cat /var/lib/tor/onionmail/hostname
# Set this in the server config.toml -> [identity].onion (or hostname_file);
# distribute the same address to clients as [client].server_onion.
""")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="onionmail", description=__doc__)
    p.add_argument("-c", "--config", help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("smtpd", help="run the incoming SMTP server")
    sp.set_defaults(func=cmd_smtpd)

    sp = sub.add_parser("sender", help="run the outbound queue processor")
    sp.add_argument("--interval", type=int, default=30, help="queue poll interval (s)")
    sp.set_defaults(func=cmd_sender)

    sp = sub.add_parser("tui", help="open the terminal interface")
    sp.set_defaults(func=cmd_tui)

    sp = sub.add_parser("gui", help="open the native desktop window (PySide6)")
    sp.add_argument("--local", action="store_true",
                    help="do not connect to a remote server; use the local Maildir (on the server)")
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser("apid", help="run the account/mail API service (2nd onion port)")
    sp.set_defaults(func=cmd_apid)

    sp = sub.add_parser("invite", help="generate a one-time invite code")
    sp.add_argument("-n", "--count", type=int, default=1, help="how many codes")
    sp.set_defaults(func=cmd_invite)

    sp = sub.add_parser("useradd", help="create an account (admin; no invite code needed)")
    sp.add_argument("user")
    sp.add_argument("--password", help="prompted for if not given")
    sp.set_defaults(func=cmd_useradd)

    sp = sub.add_parser("passwd", help="change an account password")
    sp.add_argument("user")
    sp.set_defaults(func=cmd_passwd)

    sp = sub.add_parser("send", help="queue a single message")
    sp.add_argument("--to", action="append", required=True, help="recipient@<onion> (repeatable)")
    sp.add_argument("--subject", default="(no subject)")
    sp.add_argument("--body-file", help="body file (stdin if omitted)")
    sp.add_argument("--attach", action="append", help="file to attach (repeatable)")
    sp.add_argument("--from", dest="from_user", metavar="USER",
                    help="sender account in multi-user mode (else local_user / the single account)")
    sp.add_argument("--encrypt", action="store_true",
                    help="end-to-end encrypt with age (needs every recipient's public key)")
    sp.add_argument("--pubkey", action="append", default=[], metavar="ADDRESS=AGE1KEY",
                    help="age public key for a recipient (repeatable); resolved automatically "
                         "for accounts on the same server if registered with apid")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("status", help="status summary")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("setup-tor", help="print a torrc snippet")
    sp.set_defaults(func=cmd_setup_tor)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
