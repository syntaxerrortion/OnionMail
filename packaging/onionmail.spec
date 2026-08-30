# PyInstaller spec — onionmail masaüstü istemcisi (gömülü Tor).
# Kullanım:  pyinstaller --noconfirm packaging/onionmail.spec
# Ortam:     TOR_DIR   -> içinde tor(.exe) olan dizin (CI doldurur; yoksa atlanır)
#            APP_ICON  -> .ico / .icns yolu (opsiyonel)
import os

from PyInstaller.utils.hooks import collect_submodules

tor_dir = os.environ.get("TOR_DIR", "tor")
icon = os.environ.get("APP_ICON") or None

datas = []
if os.path.isdir(tor_dir):
    for root, _dirs, files in os.walk(tor_dir):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(root, tor_dir)
            dst = "tor" if rel == "." else os.path.join("tor", rel)
            datas.append((src, dst))

_tmpl = os.path.join("packaging", "client.default.json")
if os.path.isfile(_tmpl):
    datas.append((_tmpl, "."))

a = Analysis(
    ["packaging/launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("onionmail"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
              "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="onionmail",
    console=False,          # pencereli uygulama (terminal açmaz)
    icon=icon,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    name="onionmail",       # dist/onionmail/  (onedir) -> zip'lenip dağıtılır
)
