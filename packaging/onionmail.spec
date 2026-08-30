# PyInstaller spec — onionmail masaüstü istemcisi (gömülü Tor).
# Kullanım (repo kökünden):  pyinstaller --noconfirm packaging/onionmail.spec
# Ortam:  TOR_DIR   -> içinde tor(.exe) olan dizin (yoksa tor gömülmez)
#         APP_ICON  -> .ico / .icns yolu (opsiyonel)
#
# NOT: spec içindeki göreli yollar spec dosyasının dizinine göre çözülür;
# bu yüzden her şeyi SPEC'ten türetilen mutlak yollarla veriyoruz.
import os

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))   # .../packaging
ROOT = os.path.dirname(HERE)                     # repo kökü

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
    print(f"[spec] tor gömülüyor: {tor_dir} ({len(datas)} dosya)")
else:
    print(f"[spec] UYARI: TOR_DIR yok ({tor_dir}) — tor gömülmeyecek")

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
    console=False,          # pencereli (terminal açmaz)
    icon=icon,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    name="onionmail",       # dist/onionmail/  -> zip'lenip dağıtılır
)
