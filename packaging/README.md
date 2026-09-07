# Packaging the onionmail client (Windows / macOS / Linux `.exe`)

Goal: friends double-click and connect **without installing Python / Tor**.

## What is produced

A `dist/onionmail/` folder (onedir) → zipped and distributed. It contains:

- `onionmail(.exe)` — built from `packaging/launcher.py` with PyInstaller
- `tor/tor(.exe)` — from the Tor Expert Bundle; the app starts its own Tor on
  launch (`SocksPort 9250`, its own `DataDirectory`)

The server onion is **not embedded** in the package. User experience: run the
exe → Tor bootstrap (~10-30 s) → **Login screen** → server onion address + Tor
SOCKS (pre-filled) → "Test connection" → invite code + username + password →
mailbox. The onion and the session are remembered in `~/.config/onionmail/`, so
later launches go straight to the mailbox.

## GitHub Actions (recommended — multi-OS, automatic)

1. Push this repo to GitHub (see "Create the repo" below).
2. Push a version tag:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. Actions runs; Windows/macOS/Linux zips are uploaded both as **artifacts** and
   as **Release** assets. Can also be triggered manually with
   `workflow_dispatch`.

For your friend: download the zip → extract → run `onionmail(.exe)` → on the
Login screen enter the **server onion address** and the invite code.

The Tor version is pinned in `.github/workflows/build.yml` (`V=14.0.1`) — bump
it there as the Tor Expert Bundle is updated.

## Manual build (single OS)

Run it on the target OS itself (a Windows exe is built on Windows):

```bash
python -m venv .venv && . .venv/bin/activate      # win: .venv\Scripts\activate
pip install pyinstaller PySide6 PySocks argon2-cffi aiosmtpd textual
pip install -e .

# Download the Tor Expert Bundle, extract it, put it in the tor/ folder:
#   https://www.torproject.org/download/tor/  -> "Expert Bundle"
#   so that tor/tor(.exe) exists

TOR_DIR=tor pyinstaller --noconfirm packaging/onionmail.spec
# output: dist/onionmail/
```

## Signing / SmartScreen / antivirus

- An unsigned exe triggers Windows **SmartScreen** ("More info → Run anyway")
  and macOS **Gatekeeper** (right-click → Open) warnings. Normal.
- PyInstaller onedir produces fewer antivirus false positives than `--onefile`.
- If you want a real signature: an Authenticode certificate for Windows (paid),
  an Apple Developer ID + notarization for macOS.

## Create the repo (without the gh CLI)

```bash
cd ~/onionmail
git init && git add -A && git commit -m "onionmail + packaging"
git branch -M main
# Create an empty repo on the GitHub website (no README), then:
git remote add origin git@github.com:<user>/onionmail.git
git push -u origin main
```

> If the repo is public: the code is exposed (no secrets — the onion address and
> invite codes are not in the code). If you still don't want that, a **private**
> repo works with Actions too (a personal account has a monthly free-minutes
> limit for private repos, but it is more than enough for this).

## Notes

- If there is no bundled tor, `launcher.py` tries an open `127.0.0.1:9250`, then
  Tor Browser's `9150`.
- `sandbox.backend` is effectively `none` on Windows/macOS — attachments are
  only extracted to disk (no firejail/bwrap). Isolated opening is Linux-only.
- roomcam-view is not part of this package (separate tool).
