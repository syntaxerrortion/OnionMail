# Tor + onionmail setup on the server (Debian 13)

These steps are done **on the server machine**. Nothing is installed on your
development machine.

## 1. Tor

```bash
sudo apt update
sudo apt install tor
```

Append to `/etc/tor/torrc`:

```
HiddenServiceDir /var/lib/tor/onionmail/
HiddenServiceVersion 3
HiddenServicePort 25 127.0.0.1:8025
HiddenServicePort 8443 127.0.0.1:8443

# SOCKS is already open on 9050 (default). If not:
SOCKSPort 127.0.0.1:9050
```

```bash
sudo systemctl restart tor
sudo cat /var/lib/tor/onionmail/hostname     # 56 chars + .onion  -> your address
```

> One `HiddenServiceDir` publishes both ports on the same `.onion` address:
> `:25` -> incoming mail (`smtpd`), `:8443` -> client API (`apid`, the GUI
> connects here).
> We use 8025 locally so we don't have to listen on 25 as root.
> If you only want a single mailbox (no multi-user), skip the `:8443` line.

## 2. Service user and directories

```bash
sudo useradd --system --home /var/lib/onionmail --shell /usr/sbin/nologin onionmail
sudo mkdir -p /var/lib/onionmail /etc/onionmail /opt/onionmail
sudo chown -R onionmail:onionmail /var/lib/onionmail

# so smtpd can read the onion address:
sudo usermod -aG debian-tor onionmail
sudo chmod 750 /var/lib/tor/onionmail
```

## 3. Application

```bash
sudo rsync -a --exclude .venv --exclude .git ./ /opt/onionmail/
cd /opt/onionmail
sudo python3 -m venv .venv
sudo .venv/bin/pip install -e .
sudo chown -R onionmail:onionmail /opt/onionmail
```

`/etc/onionmail/config.toml` (example: `config.example.toml`):

```toml
[identity]
onion = ""                                    # leave empty -> read from hostname_file
hostname_file = "/var/lib/tor/onionmail/hostname"
local_user = "nejarm"

[smtpd]
host = "127.0.0.1"
port = 8025

[sender]
socks_host = "127.0.0.1"
socks_port = 9050

[storage]
maildir = "/var/lib/onionmail/Maildir"   # used in single-mailbox mode

[sandbox]
backend = "firejail"     # on the server: sudo apt install firejail

# --- multi-user mode (if you want login over the network) ---
[accounts]
enabled = true
store_path = "/var/lib/onionmail/accounts"
open_registration = false     # registration requires an invite code

[api]
host = "127.0.0.1"
port = 8443
preshared_key = ""            # optionally add a shared secret (the client supplies it too)
```

```bash
sudo chown onionmail:onionmail /etc/onionmail/config.toml
sudo chmod 640 /etc/onionmail/config.toml
```

## 4. systemd

```bash
sudo cp systemd/onionmail-*.service /etc/systemd/system/
sudo systemctl daemon-reload
# single mailbox:
sudo systemctl enable --now onionmail-smtpd onionmail-sender
# multi-user (also, if [accounts].enabled=true):
sudo systemctl enable --now onionmail-apid
systemctl status onionmail-smtpd onionmail-sender onionmail-apid
```

## 4b. Accounts and invite codes (multi-user mode)

```bash
S="sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml /opt/onionmail/.venv/bin/python -m onionmail"

# an account for yourself (admin path, no invite needed)
$S useradd nejarm

# generate one-time invite codes for friends and hand them out
$S invite -n 3

# password reset
$S passwd nejarm
```

Your friend opens the GUI on their own PC → enters the server `.onion` address →
"Test connection" → on the **Register** tab: username + password + invite code.
On later launches the session token is kept in
`~/.config/onionmail/session.json`.

## 5. TUI

Run the TUI as your own user (not the service user); but the Maildir belongs to
the `onionmail` user. Cleanest is to run the TUI as `onionmail` too:

```bash
sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml \
     /opt/onionmail/.venv/bin/python -m onionmail tui
```

## 6. Test

Both sides must be running onionmail. Self-test:

```bash
# status
sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml \
     /opt/onionmail/.venv/bin/python -m onionmail status

# mail to yourself (onion -> same onion)
echo "hello me" | sudo -u onionmail ONIONMAIL_CONFIG=/etc/onionmail/config.toml \
     /opt/onionmail/.venv/bin/python -m onionmail send \
     --to nejarm@$(sudo cat /var/lib/tor/onionmail/hostname) --subject "self test"
```

It should show up in INBOX after a few seconds to a few minutes (building the
onion circuit takes time).

## Network notes

* **No router port forwarding needed** — the onion service works over an
  outbound connection; the server does not need to be reachable from outside.
* A dynamic home IP is fine; the onion address stays the same.
* If you turn the server off, the address is unreachable; it comes back when
  you turn it on.
