"""Native desktop GUI (PySide6) — the monochrome "Messager" window.

Same look as the terminal UI (modelled on the mail client in *Who Am I*), but a
real OS window: a menu bar, a two-column message list, a reading pane, and a
free-floating "New Message" window. The backend (store / smtpd / sender /
sandbox / compose) is shared verbatim; only this front end is Qt.
"""

from __future__ import annotations

import json
import sys
import time
from email.utils import parseaddr
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .backend import Backend, LocalBackend, NetBackend
from .compose import build_message
from .config import Config
from .netclient import NetClient, NetError
from .sandbox import (
    backend_available, extract_part, list_parts, open_attachment, safe_text,
)

FOLDERS = ["INBOX", "Sent", "Outbox", "Failed", "Drafts"]
OUTGOING = {"Sent", "Outbox", "Failed", "Drafts"}

# Inverted "bar" accent: midnight (dark smoky navy) instead of white.
MIDNIGHT = "#0f1626"
MIDNIGHT_HI = "#17233b"   # a touch lighter, for the selected row vs. the header

QSS = f"""
* {{ background:#000; color:#fff; font-family:"DejaVu Sans Mono","Courier New",monospace; font-size:13px; }}
QMainWindow, QWidget, QDialog {{ background:#000; }}
QMenuBar {{ background:#000; color:#fff; }}
QMenuBar::item {{ padding:3px 12px; background:#000; }}
QMenuBar::item:selected {{ background:{MIDNIGHT}; color:#fff; }}
QMenu {{ background:#000; color:#fff; border:1px solid #fff; }}
QMenu::item:selected {{ background:{MIDNIGHT}; color:#fff; }}
QLabel#titlebar, QLabel#modalTitle {{ background:{MIDNIGHT}; color:#fff; font-weight:bold; padding:3px 8px; border:1px solid #fff; }}
QLabel#fieldLabel {{ font-weight:bold; }}
QLabel#fromLine {{ font-weight:bold; padding:4px 0; }}
QHeaderView::section {{ background:{MIDNIGHT}; color:#fff; font-weight:bold; border:0; border-right:1px solid #3a3a5a; border-bottom:1px solid #fff; padding:3px 8px; }}
QTableWidget {{ background:#000; color:#fff; gridline-color:#3a3a3a; border:1px solid #fff; outline:0; }}
QTableWidget::item {{ padding:2px 6px; }}
QTableWidget::item:selected {{ background:{MIDNIGHT_HI}; color:#fff; }}
QPlainTextEdit, QLineEdit, QTextEdit {{ background:#000; color:#fff; border:1px solid #fff; selection-background-color:{MIDNIGHT}; selection-color:#fff; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border:1px solid #fff; }}
QStatusBar {{ background:{MIDNIGHT}; color:#fff; font-weight:bold; border-top:1px solid #fff; }}
QStatusBar::item {{ border:0; }}
QSplitter::handle {{ background:#fff; height:2px; }}
QPushButton {{ background:#000; color:#fff; border:1px solid #fff; padding:4px 14px; }}
QPushButton:hover, QPushButton:default {{ background:{MIDNIGHT}; color:#fff; }}
QScrollBar:vertical {{ background:#000; width:12px; border-left:1px solid #fff; }}
QScrollBar:horizontal {{ background:#000; height:12px; border-top:1px solid #fff; }}
QScrollBar::handle {{ background:#fff; min-height:20px; min-width:20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background:#000; }}
QToolTip {{ background:{MIDNIGHT}; color:#fff; border:1px solid #fff; }}
"""


# --------------------------------------------------------------------------- #
#  Compose window (its own top-level window, non-modal)                        #
# --------------------------------------------------------------------------- #
class ComposeWindow(QWidget):
    sent = Signal()

    def __init__(self, cfg: Config, backend: Backend, *, to="", cc="", bcc="",
                 subject="", body="", in_reply_to: str | None = None):
        super().__init__()
        self.cfg, self.backend, self._in_reply_to = cfg, backend, in_reply_to
        from_addr = backend.address or (cfg.identity.resolve_onion() or "onionmail")

        self.setWindowTitle("New Message")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(760, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        title = QLabel("New Message")
        title.setObjectName("modalTitle")
        root.addWidget(title)

        body_box = QWidget()
        root.addWidget(body_box, 1)
        lay = QVBoxLayout(body_box)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(2)
        self.e_subject = QLineEdit(subject)
        self.e_cc = QLineEdit(cc)
        self.e_to = QLineEdit(to)
        self.e_bcc = QLineEdit(bcc)
        self.e_to.setPlaceholderText("alıcı@<56 karakter>.onion")
        self.e_cc.setPlaceholderText("ad@<onion>, ...")
        self.e_bcc.setPlaceholderText("gizli@<onion>, ...")
        for col, text in ((0, "Subject:"), (1, "CC:")):
            lb = QLabel(text); lb.setObjectName("fieldLabel")
            grid.addWidget(lb, 0, col)
        grid.addWidget(self.e_subject, 1, 0)
        grid.addWidget(self.e_cc, 1, 1)
        for col, text in ((0, "To:"), (1, "BCC:")):
            lb = QLabel(text); lb.setObjectName("fieldLabel")
            grid.addWidget(lb, 2, col)
        grid.addWidget(self.e_to, 3, 0)
        grid.addWidget(self.e_bcc, 3, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        lay.addLayout(grid)

        lay.addWidget(QLabel(f"From:  {from_addr}", objectName="fromLine"))

        arow = QHBoxLayout()
        self.e_attach = QLineEdit()
        self.e_attach.setPlaceholderText("Attachments:  (dosya yolları, virgülle)")
        b_attach = QPushButton("…")
        b_attach.setFixedWidth(40)
        b_attach.clicked.connect(self._pick_files)
        arow.addWidget(QLabel("Attachments:", objectName="fieldLabel"))
        arow.addWidget(self.e_attach, 1)
        arow.addWidget(b_attach)
        lay.addLayout(arow)

        lay.addWidget(QLabel("Message:", objectName="fieldLabel"))
        self.e_body = QTextEdit(body)
        lay.addWidget(self.e_body, 1)

        btns = QHBoxLayout()
        self.lbl_err = QLabel("Ctrl+S: gönder   ·   Esc: kapat")
        btns.addWidget(self.lbl_err, 1)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.close)
        b_send = QPushButton("Send")
        b_send.setDefault(True)
        b_send.clicked.connect(self._send)
        btns.addWidget(b_cancel)
        btns.addWidget(b_send)
        lay.addLayout(btns)

        QShortcut(QKeySequence("Ctrl+S"), self, self._send)
        QShortcut(QKeySequence("Esc"), self, self.close)

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Ek seç")
        if paths:
            cur = [p for p in self.e_attach.text().split(",") if p.strip()]
            self.e_attach.setText(", ".join(cur + paths))

    def _send(self) -> None:
        to = self.e_to.text().strip()
        if not to:
            self.lbl_err.setText("alıcı gerekli")
            return
        attach = [Path(p.strip()) for p in self.e_attach.text().split(",") if p.strip()]
        missing = [str(p) for p in attach if not p.is_file()]
        if missing:
            self.lbl_err.setText("dosya yok: " + ", ".join(missing))
            return
        try:
            msg = build_message(
                self.cfg, to=to, cc=self.e_cc.text(),
                subject=self.e_subject.text().strip() or "(konu yok)",
                body=self.e_body.toPlainText(),
                attachments=attach, in_reply_to=self._in_reply_to,
            )
            self.backend.send(msg, bcc=[
                x.strip() for x in self.e_bcc.text().split(",") if x.strip()
            ])
        except Exception as e:  # noqa: BLE001
            self.lbl_err.setText(str(e))
            return
        self.sent.emit()
        self.close()


# --------------------------------------------------------------------------- #
#  Collector / sandbox dialog                                                  #
# --------------------------------------------------------------------------- #
class CollectorDialog(QDialog):
    def __init__(self, cfg: Config, backend: Backend, folder: str, key: str, parent=None):
        super().__init__(parent)
        self.cfg, self.backend, self.folder, self.key = cfg, backend, folder, key
        self.msg = backend.get(folder, key)
        self.setWindowTitle("Collector — ek / parça incelemesi")
        self.resize(680, 420)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Collector — ek / parça incelemesi"); t.setObjectName("modalTitle")
        lay.addWidget(t)

        inner = QWidget(); lay.addWidget(inner, 1)
        v = QVBoxLayout(inner); v.setContentsMargins(12, 10, 12, 12)

        avail = backend_available(cfg)
        v.addWidget(QLabel(
            f"backend: {cfg.sandbox.backend} "
            + ("[hazır — 'ağ yok' izole açma]" if avail
               else "[yok — sadece güvenli metin / diske çıkarma]")))

        self.parts = list_parts(self.msg)
        self.table = QTableWidget(len(self.parts), 5)
        self.table.setHorizontalHeaderLabels(["#", "tür", "dosya", "boyut", "risk"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for r, p in enumerate(self.parts):
            for c, val in enumerate((str(p.index), p.content_type, p.filename,
                                     str(p.size), "YÜKSEK" if p.dangerous else "-")):
                self.table.setItem(r, c, QTableWidgetItem(val))
        self.table.resizeColumnsToContents()
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        for label, slot in (("İzole aç", self._open),
                            ("Güvenli metin", self._safe_text),
                            ("Diske çıkar", self._extract),
                            ("Kapat", self.accept)):
            b = QPushButton(label); b.clicked.connect(slot); row.addWidget(b)
        lay.addLayout(row)

    def _sel_index(self) -> int | None:
        r = self.table.currentRow()
        return self.parts[r].index if 0 <= r < len(self.parts) else None

    def _dest(self) -> Path:
        d = self.cfg.sandbox.workdir_path / self.key
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _extract(self) -> None:
        ix = self._sel_index()
        if ix is None:
            return
        path = extract_part(self.msg, ix, self._dest())
        QMessageBox.information(self, "Çıkarıldı", str(path))

    def _open(self) -> None:
        ix = self._sel_index()
        if ix is None:
            return
        path = extract_part(self.msg, ix, self._dest())
        proc = open_attachment(path, self.cfg)
        if proc is None:
            QMessageBox.warning(self, "Sandbox yok",
                                f"backend yok; diske çıkarıldı:\n{path}")
        else:
            QMessageBox.information(self, "Açıldı",
                                   f"İzole ortamda açıldı (ağ yok):\n{path.name}")

    def _safe_text(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Güvenli metin (nötrlenmiş)")
        dlg.resize(640, 480)
        lay = QVBoxLayout(dlg)
        te = QPlainTextEdit(safe_text(self.msg))
        te.setReadOnly(True)
        lay.addWidget(te)
        dlg.exec()


# --------------------------------------------------------------------------- #
#  Small generic background worker                                             #
# --------------------------------------------------------------------------- #
class _Worker(QThread):
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.ok.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            self.fail.emit(str(e))


# --------------------------------------------------------------------------- #
#  Client-side settings persistence (no TOML rewrite: a small JSON overlay)     #
# --------------------------------------------------------------------------- #
def _client_json_path(cfg: Config) -> Path:
    return cfg.client.session_path_p.with_name("client.json")


def load_client_overrides(cfg: Config) -> None:
    """Overlay ~/.config/onionmail/client.json onto cfg (server address, SOCKS,
    sandbox backend). Set by the Settings dialog; keeps config.toml untouched."""
    p = _client_json_path(cfg)
    if not p.is_file():
        return
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return
    if d.get("server_onion") is not None:
        cfg.client.server_onion = d["server_onion"].strip().lower()
    if d.get("preshared_key") is not None:
        cfg.client.preshared_key = d["preshared_key"]
    if d.get("socks_host"):
        cfg.sender.socks_host = d["socks_host"]
    if d.get("socks_port"):
        cfg.sender.socks_port = int(d["socks_port"])
    if d.get("sandbox_backend") in ("firejail", "bwrap", "none"):
        cfg.sandbox.backend = d["sandbox_backend"]


def save_client_overrides(cfg: Config, **fields) -> None:
    p = _client_json_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = {}
    if p.is_file():
        try:
            cur = json.loads(p.read_text())
        except ValueError:
            pass
    cur.update(fields)
    p.write_text(json.dumps(cur, indent=2))


def _session_path(cfg: Config) -> Path:
    return cfg.client.session_path_p


def load_session(cfg: Config) -> dict | None:
    p = _session_path(cfg)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if d.get("expires", 0) <= time.time() + 60:
        return None
    return d


def save_session(cfg: Config, client: NetClient) -> None:
    p = _session_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "server_onion": client.server_onion,
        "socks_host": client.socks_host, "socks_port": client.socks_port,
        "preshared_key": client.psk, "api_port": client.api_port,
        "token": client.token, "expires": client.token_expires,
        "address": client.address, "onion": client.onion,
    }, indent=2))
    p.chmod(0o600)


def clear_session(cfg: Config) -> None:
    _session_path(cfg).unlink(missing_ok=True)


def client_from_session(d: dict) -> NetClient:
    c = NetClient(d["server_onion"], socks_host=d.get("socks_host", "127.0.0.1"),
                  socks_port=d.get("socks_port", 9050),
                  preshared_key=d.get("preshared_key", ""),
                  api_port=d.get("api_port", 8443))
    c.restore(d["token"], d.get("expires", 0), d.get("address", ""), d.get("onion", ""))
    return c


# --------------------------------------------------------------------------- #
#  Login / registration window (first run, or after logout / token expiry)     #
# --------------------------------------------------------------------------- #
class LoginWindow(QWidget):
    logged_in = Signal(object)  # NetClient

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._client: NetClient | None = None
        self._w: _Worker | None = None
        self.setWindowTitle("Messager — Sunucuya bağlan")
        self.resize(560, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(QLabel("Sunucuya bağlan", objectName="modalTitle"))
        box = QWidget(); root.addWidget(box, 1)
        v = QVBoxLayout(box); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)

        form = QFormLayout()
        self.e_onion = QLineEdit(cfg.client.server_onion)
        self.e_onion.setPlaceholderText("<56 karakter>.onion")
        self.e_socks_h = QLineEdit(cfg.sender.socks_host)
        self.e_socks_p = QLineEdit(str(cfg.sender.socks_port))
        self.e_psk = QLineEdit(cfg.client.preshared_key)
        self.e_psk.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Sunucu onion:", self.e_onion)
        form.addRow("Tor SOCKS host:", self.e_socks_h)
        form.addRow("Tor SOCKS port:", self.e_socks_p)
        form.addRow("Ön-paylaşımlı anahtar:", self.e_psk)
        v.addLayout(form)

        b_test = QPushButton("Bağlantıyı test et")
        b_test.clicked.connect(self._test)
        v.addWidget(b_test)

        self.tabs = QTabWidget()
        self.tabs.setEnabled(False)
        v.addWidget(self.tabs, 1)

        # login tab
        lt = QWidget(); lf = QFormLayout(lt)
        self.li_user = QLineEdit(); self.li_pass = QLineEdit()
        self.li_pass.setEchoMode(QLineEdit.EchoMode.Password)
        lf.addRow("Kullanıcı:", self.li_user)
        lf.addRow("Şifre:", self.li_pass)
        b_login = QPushButton("Giriş yap"); b_login.clicked.connect(self._login)
        lf.addRow(b_login)
        self.tabs.addTab(lt, "Giriş")

        # register tab
        rt = QWidget(); rf = QFormLayout(rt)
        self.re_user = QLineEdit(); self.re_p1 = QLineEdit(); self.re_p2 = QLineEdit()
        self.re_inv = QLineEdit()
        self.re_p1.setEchoMode(QLineEdit.EchoMode.Password)
        self.re_p2.setEchoMode(QLineEdit.EchoMode.Password)
        rf.addRow("Kullanıcı:", self.re_user)
        rf.addRow("Şifre:", self.re_p1)
        rf.addRow("Şifre (tekrar):", self.re_p2)
        rf.addRow("Davet kodu:", self.re_inv)
        b_reg = QPushButton("Kaydol"); b_reg.clicked.connect(self._register)
        rf.addRow(b_reg)
        self.tabs.addTab(rt, "Kaydol")

        self.status = QLabel("Önce sunucu onion adresini gir ve bağlantıyı test et.")
        v.addWidget(self.status)

    def _mk_client(self) -> NetClient:
        return NetClient(
            self.e_onion.text().strip().lower(),
            socks_host=self.e_socks_h.text().strip() or "127.0.0.1",
            socks_port=int(self.e_socks_p.text().strip() or "9050"),
            preshared_key=self.e_psk.text(),
        )

    def _run(self, fn, on_ok):
        self.status.setText("… çalışıyor")
        self._w = _Worker(fn)
        self._w.ok.connect(on_ok)
        self._w.fail.connect(lambda m: self.status.setText(f"hata: {m}"))
        self._w.start()

    def _test(self) -> None:
        if not self.e_onion.text().strip():
            self.status.setText("onion adresi gerekli")
            return
        self._client = self._mk_client()
        self._run(self._client.ping, self._tested)

    def _tested(self, pong: dict) -> None:
        self.tabs.setEnabled(True)
        reg = "açık" if pong.get("open_registration") else "davet kodlu"
        self.status.setText(f"bağlandı · sunucu {pong.get('onion','?')[:20]}… · kayıt: {reg}")

    def _login(self) -> None:
        if not self._client:
            self.status.setText("önce bağlantıyı test et")
            return
        u, p = self.li_user.text().strip(), self.li_pass.text()
        self._run(lambda: self._client.login(u, p), lambda _r: self._done())

    def _register(self) -> None:
        if not self._client:
            self.status.setText("önce bağlantıyı test et")
            return
        if self.re_p1.text() != self.re_p2.text():
            self.status.setText("şifreler eşleşmiyor")
            return
        u, p, inv = self.re_user.text().strip(), self.re_p1.text(), self.re_inv.text().strip()
        self._run(lambda: self._client.register(u, p, inv), lambda _r: self._registered(u))

    def _registered(self, user: str) -> None:
        self.status.setText("kayıt tamam — şimdi giriş yap")
        self.li_user.setText(user)
        self.tabs.setCurrentIndex(0)

    def _done(self) -> None:
        save_client_overrides(
            self.cfg, server_onion=self._client.server_onion,
            preshared_key=self._client.psk,
            socks_host=self._client.socks_host, socks_port=self._client.socks_port,
        )
        save_session(self.cfg, self._client)
        self.logged_in.emit(self._client)
        self.close()


# --------------------------------------------------------------------------- #
#  Settings dialog                                                             #
# --------------------------------------------------------------------------- #
class SettingsDialog(QDialog):
    logged_out = Signal()

    def __init__(self, cfg: Config, backend: Backend, parent=None):
        super().__init__(parent)
        self.cfg, self.backend = cfg, backend
        self.setWindowTitle("Settings")
        self.resize(520, 300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("Settings", objectName="modalTitle"))
        box = QWidget(); lay.addWidget(box, 1)
        form = QFormLayout(box); form.setContentsMargins(14, 12, 14, 12)

        net = isinstance(backend, NetBackend)
        self.e_onion = QLineEdit(cfg.client.server_onion)
        self.e_socks_h = QLineEdit(cfg.sender.socks_host)
        self.e_socks_p = QLineEdit(str(cfg.sender.socks_port))
        self.e_psk = QLineEdit(cfg.client.preshared_key)
        self.e_psk.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Hesap:", QLabel(backend.address or "(yerel)"))
        form.addRow("Mod:", QLabel("uzak sunucu (Tor)" if net else "yerel Maildir"))
        form.addRow("Sunucu onion:", self.e_onion)
        form.addRow("Tor SOCKS host:", self.e_socks_h)
        form.addRow("Tor SOCKS port:", self.e_socks_p)
        form.addRow("Ön-paylaşımlı anahtar:", self.e_psk)

        row = QHBoxLayout()
        if net:
            b_out = QPushButton("Çıkış yap")
            b_out.clicked.connect(self._logout)
            row.addWidget(b_out)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        form.addRow(row)
        self.note = QLabel("Değişiklikler bir sonraki açılışta geçerli olur.")
        form.addRow(self.note)

    def _save(self) -> None:
        save_client_overrides(
            self.cfg,
            server_onion=self.e_onion.text().strip().lower(),
            preshared_key=self.e_psk.text(),
            socks_host=self.e_socks_h.text().strip() or "127.0.0.1",
            socks_port=int(self.e_socks_p.text().strip() or "9050"),
        )
        self.accept()

    def _logout(self) -> None:
        try:
            if isinstance(self.backend, NetBackend):
                self.backend.client.logout()
        except NetError:
            pass
        clear_session(self.cfg)
        self.logged_out.emit()
        self.accept()


# --------------------------------------------------------------------------- #
#  Main window                                                                 #
# --------------------------------------------------------------------------- #
class MessagerWindow(QMainWindow):
    def __init__(self, cfg: Config, backend: Backend):
        super().__init__()
        self.cfg = cfg
        self.backend = backend
        self.folder_ix = 0
        self._raw = False
        self._rows: list = []
        self._children: list = []
        self._worker: _Worker | None = None
        self._inbox_keys: set[str] = set()
        self._inbox_seeded = False
        self._flash_timer: QTimer | None = None
        self._chime_fx = None

        self.resize(1000, 680)
        self._build_menu()

        self.titlebar = QLabel("")
        self.titlebar.setObjectName("titlebar")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Messages", "Subject"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 240)
        self.table.currentCellChanged.connect(lambda *_: self._render_selected())

        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.body)
        split.setSizes([300, 360])

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.titlebar)
        v.addWidget(split, 1)
        self.setCentralWidget(central)

        self.status = self.statusBar()

        self._shortcuts()

        self._auto = QTimer(self)
        self._auto.timeout.connect(self._auto_refresh)
        self._auto.start(20_000)

        self.reload()

    # -- menu / shortcuts ------------------------------------------------
    def _build_menu(self) -> None:
        mb = self.menuBar()
        mb.setNativeMenuBar(False)
        for label, slot in (
            ("Email", self.act_compose),
            ("Settings", self.act_settings),
            ("Help", self.act_help),
        ):
            a = QAction(label, self)
            a.triggered.connect(slot)
            mb.addAction(a)

    def _shortcuts(self) -> None:
        for keys, slot in (
            ("N", self.act_compose), ("R", self.act_reply), ("D", self.act_delete),
            ("Delete", self.act_delete), ("A", self.act_collector),
            ("X", self.act_toggle_raw), ("F", self.act_flush), ("G", self.reload),
            ("Ctrl+A", self.table.selectAll), ("Ctrl+Shift+A", self.table.clearSelection),
            ("Ctrl+Tab", self.act_next_folder), ("?", self.act_help),
            ("Ctrl+Q", self.close),
        ):
            QShortcut(QKeySequence(keys), self, slot)

    # -- data ----------------------------------------------------------
    @property
    def folder(self) -> str:
        return FOLDERS[self.folder_ix]

    def reload(self, select: bool = True) -> None:
        prev_key = None if select else self._selected_key()
        try:
            self._rows = self.backend.list(self.folder)
        except Exception as e:  # noqa: BLE001
            self.status.showMessage(f"liste alınamadı: {e}")
            return
        self.table.setRowCount(len(self._rows))
        outgoing = self.folder in OUTGOING
        for r, s in enumerate(self._rows):
            name, addr = parseaddr((s.to if outgoing else s.from_) or "")
            who = name or (addr.split("@")[0] if "@" in addr else addr) or "(?)"
            mark = "" if s.seen else "» "
            it0 = QTableWidgetItem(f"{mark}{who}")
            it0.setData(Qt.ItemDataRole.UserRole, s.key)
            self.table.setItem(r, 0, it0)
            self.table.setItem(r, 1, QTableWidgetItem(s.subject or "(konu yok)"))
        self.titlebar.setText(f"Messager - {self.folder}")
        self.setWindowTitle(f"Messager - {self.folder}")
        qc = self.backend.queue_count()
        qtxt = "server" if qc < 0 else str(qc)
        who = self.backend.address or "(yerel)"
        self.status.showMessage(
            f"{who[:30]}   msgs:{len(self._rows)}   queue:{qtxt}   "
            f"[{self.folder}]   N:new  R:reply  Ctrl+A:seç  D:sil  A:collector  ?:help"
        )
        if not self._rows:
            self.body.setPlainText("")
        elif select:
            self.table.selectRow(0)
        elif prev_key is not None:
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == prev_key:
                    self.table.selectRow(r)
                    break
        if self.folder == "INBOX":
            self._inbox_keys = {s.key for s in self._rows}
            self._inbox_seeded = True

    def _selected_key(self) -> str | None:
        r = self.table.currentRow()
        if r < 0 or r >= self.table.rowCount():
            return None
        it = self.table.item(r, 0)
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _render_selected(self) -> None:
        key = self._selected_key()
        if not key:
            self.body.setPlainText("")
            return
        try:
            if self._raw:
                self.body.setPlainText(
                    self.backend.get_bytes(self.folder, key).decode("utf-8", "replace"))
            else:
                msg = self.backend.get(self.folder, key)
                head = "\n".join(f"{h}: {msg[h]}"
                                 for h in ("From", "To", "Cc", "Subject", "Date") if msg[h])
                self.body.setPlainText(head + "\n" + "-" * 48 + "\n" + safe_text(msg))
            if self.folder == "INBOX":
                self.backend.mark_seen(self.folder, key, True)
        except Exception as e:  # noqa: BLE001
            self.body.setPlainText(f"(mesaj alınamadı: {e})")

    def _auto_refresh(self) -> None:
        if self.folder != "INBOX":
            return
        try:
            rows = self.backend.list("INBOX")
        except Exception:  # noqa: BLE001
            return
        keys = {s.key for s in rows}
        new = (keys - self._inbox_keys) if self._inbox_seeded else set()
        if keys != self._inbox_keys:
            self.reload(select=not new)
        if new:
            self._notify_new(new)

    # -- new-mail flash + chime ---------------------------------------
    def _notify_new(self, new_keys: set) -> None:
        self._flash_new(new_keys)
        if self._sound_enabled():
            self._chime()

    def _sound_enabled(self) -> bool:
        p = self.cfg.client.session_path_p.with_name("client.json")
        try:
            return bool(json.loads(p.read_text()).get("notify_sound", True))
        except (OSError, ValueError):
            return True

    def _flash_new(self, new_keys: set) -> None:
        rows = [r for r in range(self.table.rowCount())
                if self.table.item(r, 0) is not None
                and self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) in new_keys]
        if not rows:
            return
        white, black = QColor("#ffffff"), QColor("#000000")
        state = {"n": 8}  # 4 kez yak + 4 kez söndür
        if self._flash_timer is not None:
            self._flash_timer.stop()
        t = QTimer(self)
        self._flash_timer = t

        def restore() -> None:
            for r in rows:
                for c in (0, 1):
                    it = self.table.item(r, c)
                    if it:
                        it.setBackground(QBrush())
                        it.setForeground(QBrush())

        def tick() -> None:
            if self.folder != "INBOX" or state["n"] <= 0:
                restore()
                t.stop()
                return
            on = state["n"] % 2 == 0
            for r in rows:
                for c in (0, 1):
                    it = self.table.item(r, c)
                    if not it:
                        continue
                    it.setBackground(white if on else QBrush())
                    it.setForeground(black if on else QBrush())
            state["n"] -= 1

        t.timeout.connect(tick)
        tick()
        t.start(170)

    def _chime(self) -> None:
        try:
            from PySide6.QtMultimedia import QSoundEffect
        except Exception:  # noqa: BLE001
            return
        try:
            fx = self._chime_fx or QSoundEffect(self)
            self._chime_fx = fx
            fx.setSource(QUrl.fromLocalFile(str(self._chime_wav())))
            fx.setVolume(0.35)
            fx.play()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _chime_wav() -> Path:
        import math
        import struct
        import tempfile
        import wave

        path = Path(tempfile.gettempdir()) / "onionmail-chime.wav"
        if path.exists():
            return path
        rate = 44100
        frames = bytearray()
        for freq, dur in ((880.0, 0.12), (1174.66, 0.18)):  # A5 -> D6
            n = int(rate * dur)
            for i in range(n):
                env = max(min(i / 400.0, 1.0, (n - i) / 900.0), 0.0)
                s = math.sin(2 * math.pi * freq * i / rate) * env * 0.6
                frames += struct.pack("<h", int(s * 32767))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        return path

    # -- actions -----------------------------------------------------
    def _goto(self, ix: int) -> None:
        self.folder_ix = ix % len(FOLDERS)
        self._raw = False
        self.reload()

    def act_next_folder(self) -> None:
        self._goto(self.folder_ix + 1)

    def act_toggle_raw(self) -> None:
        self._raw = not self._raw
        self._render_selected()

    def _spawn(self, w: ComposeWindow) -> None:
        w.sent.connect(self.reload)
        w.destroyed.connect(lambda: self._children.remove(w) if w in self._children else None)
        self._children.append(w)
        w.show()

    def act_compose(self) -> None:
        self._spawn(ComposeWindow(self.cfg, self.backend))

    def act_reply(self) -> None:
        key = self._selected_key()
        if not key:
            return
        msg = self.backend.get(self.folder, key)
        quoted = "\n".join("> " + ln for ln in safe_text(msg).splitlines())
        self._spawn(ComposeWindow(
            self.cfg, self.backend,
            to=str(msg["From"] or ""),
            subject="Re: " + str(msg["Subject"] or ""),
            body="\n\n" + quoted,
            in_reply_to=str(msg["Message-ID"] or "") or None,
        ))

    def _selected_keys(self) -> list[str]:
        keys: list[str] = []
        for r in sorted({ix.row() for ix in self.table.selectedIndexes()}):
            it = self.table.item(r, 0)
            k = it.data(Qt.ItemDataRole.UserRole) if it else None
            if k:
                keys.append(k)
        return keys

    def act_delete(self) -> None:
        keys = self._selected_keys() or ([k] if (k := self._selected_key()) else [])
        if not keys:
            return
        if len(keys) > 1:
            yes = QMessageBox.question(
                self, "Sil", f"{len(keys)} mesaj silinsin mi?  [{self.folder}]",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if yes != QMessageBox.StandardButton.Yes:
                return
        n = 0
        for k in keys:
            try:
                self.backend.delete(self.folder, k)
                n += 1
            except Exception as e:  # noqa: BLE001
                self.status.showMessage(f"silinemedi: {e}")
                break
        self.reload()
        self.status.showMessage(f"{n} mesaj silindi  [{self.folder}]")

    def _table_menu(self, pos) -> None:
        m = QMenu(self)
        sel = len(self._selected_keys())
        a_all = m.addAction("Tümünü seç")
        a_clear = m.addAction("Seçimi temizle")
        m.addSeparator()
        a_del = m.addAction(f"Seçileni sil ({sel})" if sel else "Seçileni sil")
        a_del.setEnabled(sel > 0)
        chosen = m.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == a_all:
            self.table.selectAll()
        elif chosen == a_clear:
            self.table.clearSelection()
        elif chosen == a_del:
            self.act_delete()

    def act_collector(self) -> None:
        key = self._selected_key()
        if key:
            CollectorDialog(self.cfg, self.backend, self.folder, key, self).exec()

    def act_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self.backend, self)
        dlg.logged_out.connect(self._on_logout)
        dlg.exec()

    def _on_logout(self) -> None:
        QMessageBox.information(self, "Çıkış", "Oturum kapatıldı. Programı yeniden aç.")
        self.close()

    def act_help(self) -> None:
        QMessageBox.information(self, "Help", (
            "Messager — kısayollar\n\n"
            "N  yeni mesaj      R  yanıtla       D  sil\n"
            "A  collector (sandbox)              X  ham kaynak\n"
            "Ctrl+Tab  sonraki klasör           G  yenile\n"
            "F  gideni şimdi gönder (yerel mod)  ?  bu ekran   Ctrl+Q  çık\n\n"
            "Menü: Email = yeni mesaj · Settings = sunucu/hesap · Help = bu ekran"
        ))

    def act_flush(self) -> None:
        if not isinstance(self.backend, LocalBackend):
            self.status.showMessage("uzak modda kuyruk sunucuda işleniyor", 4000)
            return
        if self._worker and self._worker.isRunning():
            return
        self.status.showMessage("Kuyruk işleniyor…")
        from .sender import process_queue_once
        be = self.backend
        self._worker = _Worker(lambda: process_queue_once(self.cfg, be.store))
        self._worker.ok.connect(lambda n: (
            self.status.showMessage(f"Kuyruk turu bitti ({n} giriş denendi)", 4000),
            self.reload()))
        self._worker.fail.connect(lambda m: QMessageBox.warning(self, "Kuyruk hatası", m))
        self._worker.start()


# --------------------------------------------------------------------------- #
#  Entry point — pick backend, show login or the main window                   #
# --------------------------------------------------------------------------- #
def run(cfg: Config, local: bool = False) -> None:
    load_client_overrides(cfg)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Messager")
    app.setStyleSheet(QSS)
    app._refs = []  # keep windows alive

    def open_main(backend: Backend) -> None:
        win = MessagerWindow(cfg, backend)
        app._refs.append(win)
        win.show()

    use_net = not local and (cfg.client.server_onion or load_session(cfg))
    if not use_net:
        open_main(LocalBackend(cfg))
        sys.exit(app.exec())

    sess = load_session(cfg)
    if sess:
        open_main(NetBackend(client_from_session(sess)))
    else:
        login = LoginWindow(cfg)
        app._refs.append(login)
        login.logged_in.connect(lambda c: open_main(NetBackend(c)))
        login.show()
    sys.exit(app.exec())
