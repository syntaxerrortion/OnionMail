"""Terminal UI — a single monochrome "Messager" window: inbox + compose + sandbox.

The look is modelled on the mail client from the film *Who Am I* (2014):
a menu bar, a two-column message list (sender | subject), a reading pane, and a
floating "New Message" window. Strictly black & white.
"""

from __future__ import annotations

from email.utils import parseaddr
from pathlib import Path

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static, TextArea

from .compose import build_message, queue_message
from .config import Config
from .sandbox import (
    backend_available, extract_part, list_parts, open_attachment, safe_text,
)
from .store import Store

FOLDERS = ["INBOX", "Sent", "Outbox", "Failed", "Drafts"]

MENU = [
    ("email", "Email"),
    ("home", "Home"),
    ("replication", "Replication"),
    ("navigator", "Navigator"),
    ("collector", "Collector"),
    ("widgets", "Widgets"),
    ("help", "Help"),
]

MONO_CSS = """
Screen { background: black; color: white; }

#titlebar { height: 1; background: white; color: black; text-style: bold; padding: 0 1; }
#menubar  { height: 1; background: black; color: white; }
#menubar Static { width: auto; padding: 0 2 0 0; }
#menubar Static:hover { background: white; color: black; }
#statusbar { dock: bottom; height: 1; background: white; color: black; text-style: bold; padding: 0 1; }

#list { height: 45%; background: black; color: white; border: solid white; }
#list:focus { border: solid white; }
#list > .datatable--header       { background: white; color: black; text-style: bold; }
#list > .datatable--cursor       { background: white; color: black; }
#list > .datatable--header-cursor{ background: white; color: black; }
#list > .datatable--hover        { background: black; color: white; }

#body { height: 1fr; background: black; color: white; border: solid white; }

.modal      { background: black; color: white; border: double white; padding: 1 2; scrollbar-size: 1 1; scrollbar-background: black; scrollbar-color: white; }
#compose-box { width: 88%; height: 84%; overflow: hidden; }
#sandbox-box, #textview-box, #help-box { width: 84%; height: 78%; }
.modal-title { height: 1; background: white; color: black; text-style: bold; padding: 0 1; margin-bottom: 1; }
.field-label { color: white; text-style: bold; height: 1; }
#compose-from { color: white; text-style: bold; height: 1; margin: 1 0; }

#compose-grid { grid-size: 2; grid-columns: 3fr 2fr; grid-rows: 4; grid-gutter: 0 2; height: auto; }
#compose-grid Vertical { height: 4; }
Input { background: black; color: white; border: solid white; height: 3; }
Input:focus { border: solid white; background: black; }
Input > .input--selection { background: white; color: black; }
Input > .input--cursor { background: white; color: black; }
#compose-msg, #textview { height: 1fr; background: black; color: white; border: solid white; }
#compose-err, #sandbox-hint { color: white; text-style: bold; height: 1; }

#parts { background: black; color: white; border: solid white; height: 1fr; }
#parts > .datatable--header { background: white; color: black; text-style: bold; }
#parts > .datatable--cursor { background: white; color: black; }
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Kapat")]
    TEXT = (
        "Messager — tuşlar\n\n"
        "  ↑/↓ , j/k   mesajlar arası gezin\n"
        "  Enter       mesajı oku\n"
        "  n           yeni mesaj (New Message)\n"
        "  r           yanıtla\n"
        "  d           sil\n"
        "  a           ekler / sandbox\n"
        "  x           ham kaynak göster/gizle\n"
        "  Tab         sonraki klasör (INBOX/Sent/Outbox/Failed/Drafts)\n"
        "  f           giden kuyruğu şimdi işle (Replication)\n"
        "  g           yenile\n"
        "  q           çık\n\n"
        "Menü çubuğu tıklanabilir: Email=yeni  Home=INBOX  Replication=kuyruk\n"
        "Navigator=klasör  Collector=sandbox  Widgets=ham  Help=bu ekran"
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box", classes="modal"):
            yield Static("Help", classes="modal-title")
            yield Static(self.TEXT)

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


class ComposeScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "İptal"),
        Binding("ctrl+s", "send", "Gönder"),
    ]

    def __init__(self, cfg: Config, store: Store, *, to: str = "", cc: str = "",
                 bcc: str = "", subject: str = "", body: str = "",
                 in_reply_to: str | None = None):
        super().__init__()
        self.cfg, self.store = cfg, store
        self._v = dict(to=to, cc=cc, bcc=bcc, subject=subject, body=body)
        self._in_reply_to = in_reply_to

    def compose(self) -> ComposeResult:
        onion = self.cfg.identity.resolve_onion() or "onionmail"
        with Vertical(id="compose-box", classes="modal"):
            yield Static("New Message", classes="modal-title")
            with Grid(id="compose-grid"):
                with Vertical():
                    yield Label("Subject:", classes="field-label")
                    yield Input(self._v["subject"], id="c-subject")
                with Vertical():
                    yield Label("CC:", classes="field-label")
                    yield Input(self._v["cc"], id="c-cc", placeholder="ad@<onion>, ...")
                with Vertical():
                    yield Label("To:", classes="field-label")
                    yield Input(self._v["to"], id="c-to", placeholder="alıcı@<56 krktr>.onion")
                with Vertical():
                    yield Label("BCC:", classes="field-label")
                    yield Input(self._v["bcc"], id="c-bcc", placeholder="gizli@<onion>, ...")
            yield Static(f"From:  {self.cfg.identity.local_user}@{onion}", id="compose-from")
            yield Input("", id="c-attach", placeholder="Attachments:  /yol/dosya1, /yol/dosya2")
            yield Label("Message:", classes="field-label")
            yield TextArea(self._v["body"], id="compose-msg")
            yield Static("Ctrl+S: gönder   ·   Esc: iptal", id="compose-err")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_send(self) -> None:
        g = lambda i: self.query_one(f"#{i}", Input).value.strip()
        err = self.query_one("#compose-err", Static)
        to = g("c-to")
        if not to:
            err.update("alıcı gerekli (BCC alanının üstündeki satır)")
            return
        attach = [Path(p.strip()) for p in g("c-attach").split(",") if p.strip()]
        missing = [str(p) for p in attach if not p.is_file()]
        if missing:
            err.update("dosya yok: " + ", ".join(missing))
            return
        try:
            msg = build_message(
                self.cfg,
                to=to, cc=g("c-cc"),
                subject=g("c-subject") or "(konu yok)",
                body=self.query_one("#compose-msg", TextArea).text,
                attachments=attach,
                in_reply_to=self._in_reply_to,
            )
            queue_message(self.cfg, self.store, msg, bcc=[
                x.strip() for x in g("c-bcc").split(",") if x.strip()
            ])
        except Exception as e:  # noqa: BLE001
            err.update(str(e))
            return
        self.app.notify("Kuyruğa alındı")
        self.dismiss(True)


class TextViewScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q", "dismiss", "Kapat")]

    def __init__(self, text: str, title: str = "Kaynak"):
        super().__init__()
        self._text, self._title = text, title

    def compose(self) -> ComposeResult:
        with Vertical(id="textview-box", classes="modal"):
            yield Static(self._title, classes="modal-title")
            yield TextArea(self._text, read_only=True, id="textview")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


class SandboxScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,q", "dismiss", "Kapat"),
        Binding("s", "safe_text", "Güvenli metin"),
    ]

    def __init__(self, cfg: Config, store: Store, folder: str, key: str):
        super().__init__()
        self.cfg, self.store, self.folder, self.key = cfg, store, folder, key

    def compose(self) -> ComposeResult:
        msg = self.store.get(self.folder, self.key)
        with Vertical(id="sandbox-box", classes="modal"):
            yield Static("Collector — ek / parça incelemesi", classes="modal-title")
            avail = backend_available(self.cfg)
            yield Static(
                f"backend: {self.cfg.sandbox.backend} "
                + ("[hazır — 'net yok' izole açma]" if avail
                   else "[yok — sadece güvenli metin / diske çıkarma]")
            )
            table = DataTable(id="parts", cursor_type="row")
            table.add_columns("#", "tür", "dosya", "boyut", "risk")
            for p in list_parts(msg):
                table.add_row(str(p.index), p.content_type, p.filename,
                              f"{p.size}", "YÜKSEK" if p.dangerous else "-",
                              key=str(p.index))
            yield table
            yield Static("Enter: seçili parçayı izole ortamda aç   ·   s: güvenli metin   ·   Esc: kapat",
                         id="sandbox-hint")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)

    def action_safe_text(self) -> None:
        msg = self.store.get(self.folder, self.key)
        self.app.push_screen(TextViewScreen(safe_text(msg), "Güvenli metin (nötrlenmiş)"))

    @on(DataTable.RowSelected, "#parts")
    def _open_part(self, event: DataTable.RowSelected) -> None:
        index = int(event.row_key.value)
        msg = self.store.get(self.folder, self.key)
        dest = self.cfg.sandbox.workdir_path / self.key
        path = extract_part(msg, index, dest)
        proc = open_attachment(path, self.cfg)
        if proc is None:
            self.app.notify(f"Sandbox backend yok; diske çıkarıldı:\n{path}",
                            severity="warning", timeout=6)
        else:
            self.app.notify(f"İzole ortamda açıldı (ağ yok): {path.name}")


class OnionMailApp(App):
    CSS = MONO_CSS
    TITLE = "Messager"
    BINDINGS = [
        Binding("q", "quit", "Çık"),
        Binding("n", "compose", "Yeni"),
        Binding("r", "reply", "Yanıtla"),
        Binding("d", "delete", "Sil"),
        Binding("a", "sandbox", "Ekler"),
        Binding("x", "toggle_raw", "Ham"),
        Binding("tab", "next_folder", "Klasör"),
        Binding("f", "flush_queue", "Replication"),
        Binding("g", "refresh", "Yenile"),
        Binding("question_mark", "help", "Yardım"),
        Binding("j", "cursor_down", "", show=False),
        Binding("k", "cursor_up", "", show=False),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.store = Store(cfg.storage.maildir_path)
        self.folder_ix = 0
        self._raw = False
        self._rows: list = []

    @property
    def folder(self) -> str:
        return FOLDERS[self.folder_ix]

    def compose(self) -> ComposeResult:
        yield Static("", id="titlebar")
        with Horizontal(id="menubar"):
            for mid, label in MENU:
                yield Static(label, id=f"menu-{mid}")
        yield DataTable(id="list", cursor_type="row", zebra_stripes=False)
        yield TextArea("", read_only=True, id="body")
        yield Static("", id="statusbar")

    def on_mount(self) -> None:
        t = self.query_one("#list", DataTable)
        t.add_column("Messages", width=24)
        t.add_column("│ Subject", width=66)
        self._reload()

    # -- data --------------------------------------------------------------
    def _reload(self) -> None:
        t = self.query_one("#list", DataTable)
        t.clear()
        self._rows = self.store.list(self.folder)
        outgoing = self.folder in ("Sent", "Outbox", "Failed", "Drafts")
        for s in self._rows:
            raw = (s.to if outgoing else s.from_) or "(?)"
            name, addr = parseaddr(raw)
            who = name or (addr.split("@")[0] if "@" in addr else addr) or raw
            mark = "" if s.seen else "» "
            subj = (s.subject or "(konu yok)")[:62]
            t.add_row(f"{mark}{who}"[:24], f"│ {subj}", key=s.key)
        q = self.store.queue()
        onion = self.cfg.identity.resolve_onion() or "(onion?)"
        self.query_one("#titlebar", Static).update(f"Messager - {self.folder}")
        self.query_one("#statusbar", Static).update(
            f"{onion[:24]}…   msgs:{len(self._rows)}   queue:{len(q)}"
            f"   [{self.folder}]   n:new  r:reply  a:collector  ?:help"
        )
        if t.row_count:
            self._show(self._key_at(0))

    def _key_at(self, row: int) -> str | None:
        t = self.query_one("#list", DataTable)
        if not t.row_count:
            return None
        row = max(0, min(row, t.row_count - 1))
        return t.coordinate_to_cell_key((row, 0)).row_key.value

    def _selected(self) -> str | None:
        t = self.query_one("#list", DataTable)
        if not t.row_count or t.cursor_row is None:
            return None
        return t.coordinate_to_cell_key((t.cursor_row, 0)).row_key.value

    def _show(self, key: str | None) -> None:
        body = self.query_one("#body", TextArea)
        if not key:
            body.text = ""
            return
        if self._raw:
            body.text = self.store.get_bytes(self.folder, key).decode("utf-8", "replace")
        else:
            msg = self.store.get(self.folder, key)
            head = "\n".join(f"{h}: {msg[h]}"
                             for h in ("From", "To", "Cc", "Subject", "Date") if msg[h])
            body.text = head + "\n" + "-" * 40 + "\n" + safe_text(msg)
        if self.folder == "INBOX":
            self.store.mark_seen(self.folder, key, True)

    # -- events ----------------------------------------------------------
    @on(DataTable.RowHighlighted, "#list")
    def _row_changed(self, event: DataTable.RowHighlighted) -> None:
        self._show(event.row_key.value)

    @on(events.Click, "#menubar Static")
    def _menu_click(self, event: events.Click) -> None:
        mid = (event.widget.id or "").removeprefix("menu-")
        {
            "email": self.action_compose,
            "home": lambda: self._goto_folder(0),
            "replication": self.action_flush_queue,
            "navigator": self.action_next_folder,
            "collector": self.action_sandbox,
            "widgets": self.action_toggle_raw,
            "help": self.action_help,
        }.get(mid, lambda: None)()

    # -- actions ------------------------------------------------------
    def action_cursor_down(self) -> None:
        self.query_one("#list", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#list", DataTable).action_cursor_up()

    def action_refresh(self) -> None:
        self._reload()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def _goto_folder(self, ix: int) -> None:
        self.folder_ix = ix % len(FOLDERS)
        self._raw = False
        self._reload()

    def action_next_folder(self) -> None:
        self._goto_folder(self.folder_ix + 1)

    def action_toggle_raw(self) -> None:
        self._raw = not self._raw
        self._show(self._selected())

    def action_compose(self) -> None:
        self.push_screen(ComposeScreen(self.cfg, self.store), lambda _: self._reload())

    def action_reply(self) -> None:
        key = self._selected()
        if not key:
            return
        msg = self.store.get(self.folder, key)
        quoted = "\n".join("> " + ln for ln in safe_text(msg).splitlines())
        self.push_screen(
            ComposeScreen(
                self.cfg, self.store,
                to=str(msg["From"] or ""),
                subject="Re: " + str(msg["Subject"] or ""),
                body="\n\n" + quoted,
                in_reply_to=str(msg["Message-ID"] or "") or None,
            ),
            lambda _: self._reload(),
        )

    def action_delete(self) -> None:
        key = self._selected()
        if key:
            self.store.delete(self.folder, key)
            self._reload()

    def action_sandbox(self) -> None:
        key = self._selected()
        if key:
            self.push_screen(SandboxScreen(self.cfg, self.store, self.folder, key))

    def action_flush_queue(self) -> None:
        self.notify("Kuyruk işleniyor…")
        self.run_worker(self._flush, thread=True, exclusive=True)

    def _flush(self) -> None:
        from .sender import process_queue_once

        try:
            n = process_queue_once(self.cfg, self.store)
            self.call_from_thread(self.notify, f"Kuyruk turu bitti ({n} giriş denendi)")
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Kuyruk hatası: {e}", severity="error")
        self.call_from_thread(self._reload)
