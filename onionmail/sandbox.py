"""Safe inspection of untrusted message content.

Nothing from an incoming message is rendered by a real engine or allowed to
touch the network on the host. Two escape hatches:

* ``safe_text(msg)``            -> plain-text view, HTML neutralised in-process
                                  (no script exec, no remote fetch, links inert)
* ``open_attachment(path,cfg)`` -> copy into a throwaway dir and launch a viewer
                                  inside firejail/bwrap with ``--net=none``
"""

from __future__ import annotations

import hashlib
import html
import re
import shlex
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .config import Config

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_DANGEROUS_EXT = {
    ".exe", ".scr", ".com", ".bat", ".cmd", ".msi", ".js", ".jse", ".vbs",
    ".vbe", ".ps1", ".sh", ".jar", ".apk", ".deb", ".rpm", ".lnk", ".iso",
    ".desktop", ".run", ".bin", ".elf",
}


@dataclass(slots=True)
class Part:
    index: int
    content_type: str
    filename: str
    size: int
    sha256: str
    is_attachment: bool

    @property
    def dangerous(self) -> bool:
        return Path(self.filename).suffix.lower() in _DANGEROUS_EXT


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "")
    name = name.replace("\x00", "")
    # keep only the last path component, whatever the separator style
    name = re.split(r"[\\/]", name)[-1].strip().replace(" ", "_")
    name = _SAFE_NAME.sub("_", name)
    name = name.lstrip(".") or "attachment"
    return name[:120]


def list_parts(msg: EmailMessage) -> list[Part]:
    parts: list[Part] = []
    for i, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        fname = part.get_filename() or ""
        parts.append(
            Part(
                index=i,
                content_type=part.get_content_type(),
                filename=sanitize_filename(fname) if fname else f"part-{i}.{_ext(part.get_content_type())}",
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                is_attachment=(part.get_content_disposition() == "attachment") or bool(fname),
            )
        )
    return parts


def _ext(ctype: str) -> str:
    return {"text/plain": "txt", "text/html": "html"}.get(ctype, "bin")


def extract_part(msg: EmailMessage, index: int, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for i, part in enumerate(msg.walk()):
        if i != index or part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        fname = part.get_filename() or f"part-{index}.{_ext(part.get_content_type())}"
        out = dest_dir / sanitize_filename(fname)
        out = _dedupe(out)
        out.write_bytes(payload)
        out.chmod(0o600)
        return out
    raise IndexError(f"no leaf part at index {index}")


def _dedupe(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    n = 1
    while (cand := p.with_name(f"{stem}-{n}{suf}")).exists():
        n += 1
    return cand


# -- safe text view -------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_BR = re.compile(r"<br\s*/?>", re.I)
_BLOCK = re.compile(r"</(p|div|tr|h[1-6]|li)>", re.I)


def _html_to_text(raw: str) -> str:
    raw = _SCRIPT_STYLE.sub("", raw)
    raw = _BR.sub("\n", raw)
    raw = _BLOCK.sub("\n", raw)
    raw = _TAG.sub("", raw)
    return html.unescape(raw)


def safe_text(msg: EmailMessage) -> str:
    """A view that executes nothing and fetches nothing."""
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return "(gövde yok)"
    content = body.get_content()
    if body.get_content_type() == "text/html":
        content = "[HTML nötrlendi — bağlantılar ve uzak içerik devre dışı]\n\n" + _html_to_text(content)
    return content


# -- sandboxed open -----------------------------------------------------------
def _firejail_cmd(jail_dir: Path, target: Path, viewer: str) -> list[str]:
    return [
        "firejail", "--quiet", "--net=none", "--nodbus", "--nosound", "--no3d",
        "--nodvd", "--notv", "--nou2f", "--private-tmp",
        f"--private={jail_dir}",
        "--read-only=" + str(jail_dir / target.name),
        "--", *shlex.split(viewer), target.name,
    ]


def _bwrap_cmd(jail_dir: Path, target: Path, viewer: str) -> list[str]:
    return [
        "bwrap", "--unshare-all", "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
        "--bind", str(jail_dir), "/work", "--chdir", "/work",
        "--", *shlex.split(viewer), target.name,
    ]


def open_attachment(target: Path, cfg: Config) -> subprocess.Popen | None:
    """Open ``target`` in an isolated, network-less environment.

    Returns the spawned process, or None if the backend is unavailable / disabled.
    The caller decides what to do about None (usually: warn the user).
    """
    backend = cfg.sandbox.backend
    if backend == "none":
        return None

    jail = cfg.sandbox.workdir_path / target.stem
    jail.mkdir(parents=True, exist_ok=True)
    jailed = jail / target.name
    if jailed.resolve() != target.resolve():
        shutil.copy2(target, jailed)
        jailed.chmod(0o400)

    if backend == "firejail" and shutil.which("firejail"):
        cmd = _firejail_cmd(jail, jailed, cfg.sandbox.viewer)
    elif backend == "bwrap" and shutil.which("bwrap"):
        cmd = _bwrap_cmd(jail, jailed, cfg.sandbox.viewer)
    else:
        return None

    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def backend_available(cfg: Config) -> bool:
    return cfg.sandbox.backend != "none" and shutil.which(cfg.sandbox.backend) is not None
