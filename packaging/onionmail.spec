# PyInstaller spec — onionmail desktop client (bundled Tor).
# Usage (from the repo root):  pyinstaller --noconfirm packaging/onionmail.spec
# Environment:  TOR_DIR   -> a directory containing tor(.exe) (tor not bundled if unset)
#         APP_ICON  -> .ico / .icns yolu (opsiyonel)
#
# NOTE: relative paths in a spec are resolved against the spec file's directory;
# so we pass everything as absolute paths derived from SPEC.
import os

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))   # .../packaging
ROOT = os.path.dirname(HERE)                     # repo root

_tor_env = os.environ.get("TOR_DIR")
tor_dir = os.path.abspath(_tor_env) if _tor_env else os.path.join(ROOT, "tor")
icon = os.environ.get("APP_ICON") or None

datas = []
if os.path.isdir(tor_dir):
    for root, _dirs, files in os.walk(tor_dir):
        for fn in files:
            src = os.path.join(root, fn)
            rel = os.path.relpath(root, tor_dir)
            dst = "tor" if rel == "." else os.path.join("tor", rel)
            datas.append((src, dst))
    print(f"[spec] bundling tor: {tor_dir} ({len(datas)} files)")
else:
    print(f"[spec] WARNING: no TOR_DIR ({tor_dir}) — tor will not be bundled")

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("onionmail"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="onionmail",
    console=False,          # windowed (no terminal)
    icon=icon,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    name="onionmail",       # dist/onionmail/  -> zipped and distributed
)
