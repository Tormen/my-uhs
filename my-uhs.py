#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
my-uhs — colorized command-line reader and local catalog manager for
Universal Hint System (.uhs) files.

The hint-file parser is a pure-Python port of David Millis' OpenUHS
(Java, GPLv2+, 2012) — https://github.com/Vhati/OpenUHS — covering both
88a and 91a/9x formats. No Java required.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Transparent venv bootstrap — same pattern as my-plex.
# my-uhs runs from a dedicated user-scope venv at ~/.python.venv/my-uhs.
# When VENV_DEPS is non-empty and a dep is missing, we (re-)create the venv,
# `pip install` the deps, and re-exec ourselves inside it. Deps stay empty
# while the script is pure-stdlib; planned additions (e.g. Pillow for the
# `use` zone-preview overlay) just land in VENV_DEPS and bootstrap kicks in
# transparently on next run.
# ---------------------------------------------------------------------------
import os
import subprocess
import sys

VENV_DIR = os.path.expanduser("~/.python.venv/my-uhs")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python3")
VENV_DEPS: list = [
    "Pillow",   # zone-overlay rendering for `use` HotSpot preview
]


def _venv_has_deps() -> bool:
    if not VENV_DEPS:
        return True
    if not os.path.isfile(VENV_PYTHON):
        return False
    site_root = os.path.join(VENV_DIR, "lib")
    if not os.path.isdir(site_root):
        return False
    found: set = set()
    for d in os.listdir(site_root):
        sp = os.path.join(site_root, d, "site-packages")
        if not os.path.isdir(sp):
            continue
        present = {p.lower() for p in os.listdir(sp)}
        for dep in VENV_DEPS:
            # Pip-installed package dirs use the canonical lower-case name
            # (e.g. "PIL" for Pillow, "plexapi" for plexapi). Match on
            # either the dep name or the well-known import name.
            wanted = {dep.lower(), _PKG_IMPORT_ALIASES.get(dep, dep).lower()}
            if present & wanted:
                found.add(dep)
    return found == set(VENV_DEPS)


_PKG_IMPORT_ALIASES = {
    "Pillow": "PIL",  # Pillow installs the PIL/ tree
}


def _bootstrap_venv() -> None:
    print(f"\n >>> Creating python virtualenv {VENV_DIR!r}...\n",
          file=sys.stderr)
    rc = subprocess.call([sys.executable, "-m", "venv", VENV_DIR])
    if rc != 0:
        print(f"\n  ERROR: failed to create venv at {VENV_DIR}\n",
              file=sys.stderr)
        sys.exit(1)
    if VENV_DEPS:
        pip = os.path.join(VENV_DIR, "bin", "pip")
        rc = subprocess.call([pip, "install"] + VENV_DEPS)
        if rc != 0:
            print(f"\n  ERROR: failed to install: {', '.join(VENV_DEPS)}\n",
                  file=sys.stderr)
            sys.exit(1)
    print(f"\n >>> DONE creating python virtualenv {VENV_DIR!r}.",
          file=sys.stderr)
    print("=" * 64, file=sys.stderr)
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)


# Skip bootstrap entirely while VENV_DEPS is empty — keeps the
# pure-stdlib path zero-overhead until a real dep is added.
if VENV_DEPS and os.path.realpath(sys.executable) != os.path.realpath(
        VENV_PYTHON):
    if not _venv_has_deps():
        _bootstrap_venv()
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)


import argparse
import configparser
import json
import logging
import re
import time
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Config — search paths, defaults, load, write-default
# ---------------------------------------------------------------------------

CONFIG_FILENAME = "my-uhs.conf"

# Search order (first hit wins) — also the write-back order for --create-config:
# the highest-priority writable location is used.
CONFIG_SEARCH_PATHS = [
    f"/LINKS/default/{CONFIG_FILENAME}",
    str(Path.home() / f".{CONFIG_FILENAME}"),
    f"/etc/{CONFIG_FILENAME}",
    f"/usr/local/etc/{CONFIG_FILENAME}",
]

DEFAULTS: Dict[str, str] = {
    "catalog_dir":   str(Path.home() / ".my-uhs.catalog"),
    "catalog_url":   "http://www.uhs-hints.com/cgi-bin/update.cgi",
    "user_agent":    f"my-uhs/{__version__} (+OpenUHS-compatible)",
    "logfile":       str(Path.home() / "Library" / "Logs" / "my-uhs.log"),
    "color":         "auto",      # auto | always | never
    "fetch_timeout": "30",        # seconds
}

DEFAULT_CONFIG_TEXT = f"""\
# my-uhs configuration file
# Edit values below; lines starting with '#' are comments.
# Search order: /LINKS/default/  →  ~/.{CONFIG_FILENAME}  →  /etc/  →  /usr/local/etc/
# First hit wins. Override with --config <PATH>.

[my-uhs]

# Local catalog root. Holds:
#   index.json           — local catalog index
#   remote-catalog.xml   — last-fetched remote catalog (raw)
#   files/<name>.uhs     — registered hint files
catalog_dir = {DEFAULTS['catalog_dir']}

# Remote catalog endpoint (the official OpenUHS update server).
catalog_url = {DEFAULTS['catalog_url']}

# User-Agent for HTTP requests.
user_agent = {DEFAULTS['user_agent']}

# Debug log file (only written when -D / --debug is given).
logfile = {DEFAULTS['logfile']}

# Color mode: auto (TTY-aware), always, or never.
color = {DEFAULTS['color']}

# Network timeout for pull / catalog refresh, in seconds.
fetch_timeout = {DEFAULTS['fetch_timeout']}
"""


def find_config(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).is_file() else None
    for p in CONFIG_SEARCH_PATHS:
        if Path(p).is_file():
            return p
    return None


def load_config(explicit: Optional[str]
                ) -> Tuple[Dict[str, str], Optional[str]]:
    cfg = dict(DEFAULTS)
    used = find_config(explicit)
    if used:
        parser = configparser.ConfigParser()
        try:
            parser.read(used, encoding="utf-8")
        except (OSError, configparser.Error) as e:
            print(f"my-uhs: warning: cannot read config {used}: {e}",
                  file=sys.stderr)
            return cfg, None
        section = "my-uhs" if parser.has_section("my-uhs") else None
        items = parser.items(section) if section else parser.defaults().items()
        for k, v in items:
            cfg[k.lower()] = os.path.expanduser(os.path.expandvars(v))
    for k in ("catalog_dir", "logfile"):
        cfg[k] = os.path.expanduser(os.path.expandvars(cfg[k]))
    return cfg, used


def create_config(target_path: str) -> str:
    """
    Write the default config file to `target_path`. Refuses to overwrite an
    existing file. Returns the path written.
    """
    target = Path(target_path)
    if target.exists():
        raise FileExistsError(
            f"refusing to overwrite existing file: {target_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return str(target)


def render_effective_config(cfg: Dict[str, str], source: Optional[str]) -> str:
    """Render the resolved settings as a config-file-style block."""
    header = (f"# my-uhs effective config (source: {source})\n"
              if source else
              "# my-uhs effective config (source: built-in defaults; "
              "no config file found)\n")
    body = "[my-uhs]\n" + "".join(
        f"{k} = {cfg[k]}\n" for k in sorted(cfg))
    return header + body


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(debug: bool, logfile: str) -> logging.Logger:
    log = logging.getLogger("my-uhs")
    log.setLevel(logging.DEBUG if debug else logging.WARNING)
    log.propagate = False
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(logging.Formatter("my-uhs: %(levelname)s: %(message)s"))
    log.addHandler(err)
    if debug:
        try:
            Path(logfile).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(logfile, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
            log.addHandler(fh)
            log.debug("--- my-uhs %s starting (pid=%s) ---",
                      __version__, os.getpid())
        except OSError as e:
            print(f"my-uhs: warning: cannot open debug log {logfile}: {e}",
                  file=sys.stderr)
    return log


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------

class C:
    RESET   = "\033[0m"
    TITLE   = "\033[1;38;5;213m"   # bold pink
    SUBJECT = "\033[1;38;5;75m"    # bold blue
    QUESTION= "\033[38;5;221m"     # yellow
    HINT    = "\033[38;5;252m"     # off-white
    CREDIT  = "\033[38;5;108m"     # muted green
    COMMENT = "\033[38;5;180m"     # tan
    TEXT    = "\033[38;5;252m"
    INFO    = "\033[38;5;244m"     # grey
    LINK    = "\033[38;5;141m"     # purple
    META    = "\033[2;38;5;245m"   # dim
    SKIP    = "\033[2;38;5;240m"
    OK      = "\033[38;5;114m"
    WARN    = "\033[38;5;208m"


def colors_on(mode: str, force_off: bool) -> bool:
    if force_off or mode == "never":
        return False
    if mode == "always":
        return True
    if "NO_COLOR" in os.environ:
        return False
    return sys.stdout.isatty()


class Paint:
    def __init__(self, enabled: bool):
        self.on = enabled
    def __call__(self, text: str, color: str) -> str:
        return f"{color}{text}{C.RESET}" if self.on else text


# ---------------------------------------------------------------------------
# UHS parser — pure Python port of OpenUHSLib (Vhati/OpenUHS, GPLv2+).
# ---------------------------------------------------------------------------

@dataclass
class UHSNode:
    type: str
    content: str = ""
    kind: str = "string"           # "string" | "image" | "audio"
    id: int = -1
    link_target: int = -1
    children: List["UHSNode"] = field(default_factory=list)
    # Real binary blob for Image/SoundData (populated by the parser when a
    # node references an offset+length in the binary tail). None means the
    # parser skipped extraction (legacy behavior or read failure).
    binary: Optional[bytes] = None
    # HyperImage zone coordinates (x1, y1, x2, y2) for nodes attached as
    # spot regions. None when not a HotSpot zone target.
    zone: Optional[Tuple[int, int, int, int]] = None

    @property
    def is_link(self) -> bool:
        return self.link_target != -1


# Magic-byte → (extension, MIME) map for binary blobs in the .uhs tail.
def _detect_binary_kind(b: bytes) -> Tuple[str, str]:
    if not b:
        return ("bin", "application/octet-stream")
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ("png", "image/png")
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return ("gif", "image/gif")
    if b[:3] == b"\xff\xd8\xff":
        return ("jpg", "image/jpeg")
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        return ("wav", "audio/wav")
    if b[:3] == b"ID3" or (len(b) >= 2 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        return ("mp3", "audio/mpeg")
    if b[:4] == b"OggS":
        return ("ogg", "audio/ogg")
    return ("bin", "application/octet-stream")


class UHSParseError(Exception):
    pass


# ---- Decryption (3 variants — see OpenUHSLib) ----

def generate_key(name: str) -> List[int]:
    k = (ord('k'), ord('e'), ord('y'))
    out = []
    for i, ch in enumerate(name):
        v = ord(ch) + (k[i % 3] ^ (i + 40))
        while v > 127:
            v -= 96
        out.append(v)
    return out


def decrypt_string(s: str) -> str:
    """For 88a content and standalone 'hint' hunks (no key)."""
    buf = []
    for ch in s:
        c = ord(ch)
        if c < 32:
            continue
        c = c * 2 - 32 if c < 80 else c * 2 - 127
        buf.append(chr(c))
    return "".join(buf)


def decrypt_nest_string(s: str, key: List[int]) -> str:
    buf = []
    klen = len(key)
    for i, ch in enumerate(s):
        c = ord(ch) - (key[i % klen] ^ (i + 40))
        while c < 32:
            c += 96
        buf.append(chr(c))
    return "".join(buf)


def decrypt_text_hunk(s: str, key: List[int]) -> str:
    buf = []
    klen = len(key)
    for i, ch in enumerate(s):
        co = i % klen
        c = ord(ch) - (key[co] ^ (co + 40))
        while c < 32:
            c += 96
        buf.append(chr(c))
    return "".join(buf)


# ---- Text-escape unfolding ----

_ACCENT = {
    ":": dict(zip("AEIOUaeiou", "ÄËÏÖÜäëïöü")),
    "'": dict(zip("AEIOUaeiou", "ÁÉÍÓÚáéíóú")),
    "`": dict(zip("AEIOUaeiou", "ÀÈÌÒÙàèìòù")),
    "^": dict(zip("AEIOUaeiou", "ÂÊÎÔÛâêîôû")),
    "~": {"N": "Ñ", "n": "ñ"},
}


def parse_text_escapes(s: str) -> str:
    """Port of OpenUHSLib.parseTextEscapes. Hyperlink markers (#h+...#h-)
    are intentionally preserved verbatim, matching Java's behavior in
    --print mode."""
    out = []
    break_str = " "
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        # ## → literal #
        if ch == "#" and i + 1 < n and s[i + 1] == "#":
            out.append("#")
            i += 2
            continue
        if ch == "#":
            # accents: #a+X<acc>#a- (8 chars)
            if i + 7 < n and s[i:i + 3] == "#a+" and s[i + 5:i + 8] == "#a-":
                x, acc = s[i + 3], s[i + 4]
                m = _ACCENT.get(acc, {}).get(x)
                if m is not None:
                    out.append(m); i += 8; continue
                if x == "a" and acc == "e":
                    out.append("æ"); i += 8; continue
                if x == "T" and acc == "M":
                    out.append("™"); i += 8; continue
                # fall through — emit '#' literally
            # whitespace mode toggles
            if i + 2 < n:
                tri = s[i:i + 3]
                if tri in ("#w+", "#w."):
                    break_str = " "; i += 3; continue
                if tri == "#w-":
                    break_str = "\n"; i += 3; continue
        # ^break^ — sub the current break_str
        if ch == "^" and s[i:i + 7] == "^break^":
            out.append(break_str); i += 7; continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---- Low-level file reader ----

def _read_uhs_file(path: str, log: logging.Logger
                   ) -> Tuple[List[str], bytes, int, str, int]:
    """Returns (body_lines, raw_bytes, raw_offset, name, end_hint_section).
    body_lines starts after the 4-line header."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"UHS\r\n"):
        raise UHSParseError("not a UHS file (missing UHS magic)")
    sep = data.find(b"\x1a")
    if sep == -1:
        text_part = data
        raw_offset = -1
        raw_bytes = b""
    else:
        text_part = data[:sep]
        raw_offset = sep + 1
        raw_bytes = data[raw_offset:]
    raw_lines = text_part.split(b"\r\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    # Latin-1 keeps byte==char for the decryption math, matching the Java
    # code (which uses RandomAccessFile.readLine()).
    lines = [b.decode("latin-1") for b in raw_lines]
    log.debug("read %d lines, raw_offset=%d, raw_bytes=%d",
              len(lines), raw_offset, len(raw_bytes))
    if len(lines) < 5 or lines[0] != "UHS":
        raise UHSParseError("UHS header malformed")
    name = lines[1]
    try:
        end_hint = int(lines[3])
    except ValueError as e:
        raise UHSParseError(f"could not parse header line 4: {e}")
    return lines[4:], raw_bytes, raw_offset, name, end_hint


# ---- Top-level dispatch ----

def parse_uhs(path: str, log: logging.Logger) -> Tuple[UHSNode, str]:
    body, raw, raw_off, name, end_hint = _read_uhs_file(path, log)
    sentinel = "** END OF 88A FORMAT **"
    is_88a = True
    for i, ln in enumerate(body):
        if ln == sentinel:
            is_88a = False
            body = body[i + 1:]
            log.debug("9x sentinel at body offset %d", i)
            break
    if is_88a:
        log.debug("format: 88a")
        return _parse_88a(body, name, end_hint, log), "88a"
    root = _parse_9x(body, raw, raw_off, log)
    return root, _detect_version(body)


def _detect_version(body: List[str]) -> str:
    for i, ln in enumerate(body):
        if re.match(r"^\d+ version$", ln) and i + 1 < len(body):
            return body[i + 1]
    return "9x"


# ---- 88a parser ----

def _parse_88a(body: List[str], name: str, end_hint: int,
               log: logging.Logger) -> UHSNode:
    """Port of parse88Format. body starts at the first subject."""
    root = UHSNode(type="Root", content=name)
    if len(body) < 2:
        raise UHSParseError("88a: body too short")
    try:
        q_start = int(body[1]) - 1   # 1-based → 0-based
    except ValueError as e:
        raise UHSParseError(f"88a: bad question-section start: {e}")

    # Subjects: pairs at indices 0,2,4,... up to q_start.
    subj_records: List[Tuple[UHSNode, int]] = []   # (node, first_q)
    for s in range(0, q_start, 2):
        subj = UHSNode(type="Subject",
                       content=parse_text_escapes(decrypt_string(body[s])))
        root.children.append(subj)
        subj_records.append((subj, int(body[s + 1]) - 1))

    # For each subject, walk its question pairs until we hit the next subject's
    # first-question index (or end_hint for the last subject).
    for idx, (subj, first_q) in enumerate(subj_records):
        next_q = (subj_records[idx + 1][1] if idx + 1 < len(subj_records)
                  else end_hint - 1)
        q = first_q
        while q < next_q - 1:
            try:
                question = parse_text_escapes(decrypt_string(body[q]))
                hint_first = int(body[q + 1]) - 1
            except (IndexError, ValueError) as e:
                log.debug("88a: stopping at q=%d: %s", q, e)
                break
            qnode = UHSNode(type="Question", content=question)
            subj.children.append(qnode)
            # Hints run from hint_first up to the next question's first hint,
            # or to end_hint - 1 if this is the last question.
            if q + 3 < next_q * 2:   # crude; refined below
                pass
            # Find next-question start: scan for the next pair-of-(text,#).
            nq = q + 2
            if nq < next_q - 1:
                next_hint = int(body[nq + 1]) - 1 if nq + 1 < len(body) else end_hint - 1
            else:
                next_hint = end_hint - 1
            for h in range(hint_first, next_hint):
                try:
                    line = body[h]
                except IndexError:
                    break
                qnode.children.append(UHSNode(
                    type="Hint",
                    content=parse_text_escapes(decrypt_string(line))))
            q = nq
    return root


# ---- 9x parser ----

_NODE_HEAD = re.compile(r"^(\d+) ([A-Za-z]+)$")


def _parse_9x(body: List[str], raw: bytes, raw_off: int,
              log: logging.Logger) -> UHSNode:
    """Port of parse9xFormat. body starts after the END OF 88A FORMAT line.
    body[1] (1-based) is the first node header — we use 1-based indexing
    throughout to match the Java code's getLoggedString offsets."""
    if len(body) < 2:
        raise UHSParseError("9x: body too short")
    # In Java: name = getLoggedString(uhsFileArray, 2); index = 1; ... +=
    # buildNodes(..., index=1). The "name" line is body[1] (1-based) which
    # is body[0] in Python — but the dispatcher starts at line 1 (1-based)
    # which is the FIRST count/header line for the root subject.
    # We keep 1-based indexing internally to mirror the source closely.

    # Find the master subject title for key generation: it's the line after
    # the first "<n> subject" header.
    master_name = ""
    for i, ln in enumerate(body):
        m = _NODE_HEAD.match(ln)
        if m and m.group(2) == "subject" and i + 1 < len(body):
            master_name = body[i + 1]
            break
    key = generate_key(master_name)
    log.debug("9x master='%s' keylen=%d", master_name, len(key))

    root = UHSNode(type="Root", content="root")
    idx = 1  # 1-based index
    idx = _build_nodes(body, raw, raw_off, root, root, key, idx, log)

    # AUX_NEST (Java's default): hoist the master Subject's children up to
    # root, replace root's content with the master title, then insert a
    # Blank '--=File Info=--' separator before the remaining auxiliary nodes
    # (Version, Info, Incentive, ...).
    if root.children and root.children[0].type == "Subject":
        master = root.children[0]
        root.content = master.content
        root.children = list(master.children)
        root.children.append(UHSNode(type="Blank", content="--=File Info=--"))

    while idx < len(body) + 1:
        consumed = _build_nodes(body, raw, raw_off, root, root, key, idx, log)
        if consumed == idx:
            break
        idx = consumed
    return root


def _line(body: List[str], i: int) -> str:
    """1-based access matching Java's getLoggedString."""
    if i < 1 or i > len(body):
        return ""
    return body[i - 1]


def _build_nodes(body, raw, raw_off, root, current, key, start, log) -> int:
    """Returns the *new* index (start + lines_consumed)."""
    if start < 1 or start > len(body):
        return start + 1
    head = _line(body, start)
    m = _NODE_HEAD.match(head)
    if not m:
        return start + 1
    count, kind = int(m.group(1)), m.group(2)

    handlers = {
        "comment":   _parse_comment,
        "credit":    _parse_credit,
        "hint":      _parse_hint,
        "nesthint":  _parse_nesthint,
        "subject":   _parse_subject,
        "link":      _parse_link,
        "text":      _parse_text,
        "hyperpng":  _parse_skip_image,
        "gifa":      _parse_skip_image,
        "sound":     _parse_skip_sound,
        "blank":     _parse_blank,
        "version":   _parse_version,
        "info":      _parse_info,
        "incentive": _parse_incentive,
    }
    h = handlers.get(kind, _parse_unknown)
    consumed = h(body, raw, raw_off, root, current, key, start, count, log)
    return start + consumed


def _add_child(root: UHSNode, parent: UHSNode, node: UHSNode, start: int):
    node.id = start
    parent.children.append(node)


# Each parser returns lines_consumed (== count). Most have the shape:
#   line[start]            "<count> <kind>"
#   line[start+1]          title
#   line[start+2 ..]       payload (count - 1 more lines after the header)

def _parse_subject(body, raw, raw_off, root, cur, key, start, count, log):
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Subject", content=title)
    _add_child(root, cur, node, start)
    inner = count - 1   # already-consumed: header + title; "inner" excludes both
    j = 0
    base = start + 2
    while j < inner - 1:
        nxt = _build_nodes(body, raw, raw_off, root, node, key, base + j, log)
        step = nxt - (base + j)
        if step <= 0:
            break
        j += step
    return count


def _parse_hint(body, raw, raw_off, root, cur, key, start, count, log):
    """Port of parseHintNode. Notes on quirks faithfully preserved:
       (a) hint segments are joined with the literal '^break^' marker — the
           subsequent parse_text_escapes pass turns it into ' ' (or '\\n'
           when #w- is in effect);
       (b) a payload line of exactly ' ' (single space) is appended as
           '\\n \\n' to force a real blank line in the rendered output."""
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Question", content=title)
    _add_child(root, cur, node, start)
    inner = count - 1 - 1   # header + title already accounted for
    parts: List[str] = []   # current accumulating segment

    def flush():
        if not parts:
            return
        joined = "^break^".join(parts)
        node.children.append(UHSNode(
            type="Hint", content=parse_text_escapes(joined)))
        parts.clear()

    base = start + 2
    for j in range(inner):
        ln = _line(body, base + j)
        if ln == "-":
            flush()
        elif ln == " ":
            parts.append("\n \n")
        else:
            parts.append(decrypt_string(ln))
    flush()
    return count


def _parse_nesthint(body, raw, raw_off, root, cur, key, start, count, log):
    """Port of parseNestHintNode. Like _parse_hint, segments are joined
    with the literal '^break^' marker so parse_text_escapes can substitute
    ' ' or '\\n' depending on the #w-/#w+ toggle in effect."""
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Question", content=title)
    _add_child(root, cur, node, start)
    inner = count - 1 - 1
    parts: List[str] = []

    def flush():
        if not parts:
            return
        joined = "^break^".join(parts)
        node.children.append(UHSNode(
            type="Hint", content=parse_text_escapes(joined)))
        parts.clear()

    base = start + 2
    j = 0
    while j < inner:
        ln = _line(body, base + j)
        if ln == "-":
            flush()
            j += 1
        elif ln == "=":
            flush()
            nxt = _build_nodes(body, raw, raw_off, root, node, key,
                               base + j + 1, log)
            step = nxt - (base + j + 1)
            j += 1 + max(step, 1)
        else:
            parts.append(decrypt_nest_string(ln, key))
            j += 1
    flush()
    return count


def _parse_comment(body, raw, raw_off, root, cur, key, start, count, log):
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Comment", content=title)
    _add_child(root, cur, node, start)
    inner = count - 1
    text = " ".join(_line(body, start + 2 + j) for j in range(inner - 1))
    node.children.append(UHSNode(type="CommentData",
                                 content=parse_text_escapes(text)))
    return count


def _parse_credit(body, raw, raw_off, root, cur, key, start, count, log):
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Credit", content=title)
    _add_child(root, cur, node, start)
    inner = count - 1
    text = " ".join(_line(body, start + 2 + j) for j in range(inner - 1))
    node.children.append(UHSNode(type="CreditData",
                                 content=parse_text_escapes(text)))
    return count


def _parse_text(body, raw, raw_off, root, cur, key, start, count, log):
    """text node: header, title, then a single 'NNNNNNNNN 0 OFFSET LENGTH' line."""
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Text", content=title)
    _add_child(root, cur, node, start)
    spec = _line(body, start + 2)
    # spec format: 9-digit-id ' 0 ' offset ' ' length
    parts = spec.split(" ")
    if len(parts) >= 4 and raw_off != -1:
        try:
            offset = int(parts[-2]) - raw_off
            length = int(parts[-1])
            chunk = raw[offset:offset + length]
            decoded = chunk.decode("latin-1")
            lines = re.split(r"\r\n|\r|\n", decoded)
            # Java's String.split() with no limit drops trailing empty
            # strings; Python's re.split keeps them. Strip to match.
            while lines and lines[-1] == "":
                lines.pop()
            text = "\n".join(decrypt_text_hunk(l, key) for l in lines)
            node.children.append(UHSNode(type="TextData",
                                         content=parse_text_escapes(text)))
        except (ValueError, IndexError) as e:
            log.debug("text node: bad spec line '%s': %s", spec, e)
    return count


def _parse_link(body, raw, raw_off, root, cur, key, start, count, log):
    title = parse_text_escapes(_line(body, start + 1))
    node = UHSNode(type="Link", content=title)
    try:
        node.link_target = int(_line(body, start + 2))
    except ValueError:
        pass
    # Note: Link nodes intentionally have no id (parseLinkNode in Java does
    # not call setId), so the printer renders no '^id^: ' prefix.
    cur.children.append(node)
    return count


def _parse_blank(body, raw, raw_off, root, cur, key, start, count, log):
    cur.children.append(UHSNode(type="Blank", content="^^^"))
    return count


def _parse_version(body, raw, raw_off, root, cur, key, start, count, log):
    title = "Version: " + _line(body, start + 1)
    node = UHSNode(type="Version", content=parse_text_escapes(title))
    _add_child(root, cur, node, start)
    inner = count - 1
    text = " ".join(_line(body, start + 2 + j) for j in range(inner - 1))
    node.children.append(UHSNode(type="VersionData",
                                 content=parse_text_escapes(text)))
    return count


def _parse_info(body, raw, raw_off, root, cur, key, start, count, log):
    """Direct port of parseInfoNode — collects length=/date=/time=/author=/
    publisher=/copyright=/author-note=/game-note=/notice/> lines into a
    single InfoData child, in canonical order, separated as Java does."""
    title = "Info: " + _line(body, start + 1)
    node = UHSNode(type="Info", content=title)
    _add_child(root, cur, node, start)
    inner = count - 1 - 1   # subtract header AND title
    if inner <= 0:
        return count
    bufs = {k: "" for k in (
        "length", "date", "time", "author", "publisher",
        "copyright", "author-note", "game-note", "notice", "unknown")}
    order = ["length", "date", "time", "author", "publisher",
             "copyright", "author-note", "game-note", "notice", "unknown"]
    para_breaks = {"copyright", "author-note", "game-note", "notice"}
    for j in range(inner):
        ln = _line(body, start + 2 + j)
        # Whether to soft-join (' ') or hard-join ('\n') with the running buffer.
        is_para_continuation = (
            ln.startswith("copyright")
            or ln.startswith("notice")
            or ln.startswith("author-note")
            or ln.startswith("game-note")
            or ln.startswith(">"))
        bc = " " if is_para_continuation else "\n"
        # Pick the destination bucket and trim well-known prefixes.
        if   ln.startswith("length="):      key_, payload = "length", ln
        elif ln.startswith("date="):        key_, payload = "date", ln
        elif ln.startswith("time="):        key_, payload = "time", ln
        elif ln.startswith("author="):      key_, payload = "author", ln
        elif ln.startswith("publisher="):   key_, payload = "publisher", ln
        elif ln.startswith("copyright="):
            key_, payload = "copyright", ln[len("copyright="):]
            if not bufs[key_]:
                bufs[key_] = "copyright="; bc = ""
        elif ln.startswith("author-note="):
            key_, payload = "author-note", ln[len("author-note="):]
            if not bufs[key_]:
                bufs[key_] = "author-note="; bc = ""
        elif ln.startswith("game-note="):
            key_, payload = "game-note", ln[len("game-note="):]
            if not bufs[key_]:
                bufs[key_] = "game-note="; bc = ""
        elif ln.startswith(">"):
            key_, payload = "notice", ln[1:]
        else:
            key_, payload = "unknown", ln
            log.debug("info: unknown line: %r", ln)
        if bufs[key_]:
            bufs[key_] += bc
        bufs[key_] += payload
    # Emit in canonical order; insert blank-line separator before
    # paragraph-style buckets that sit after a previous bucket.
    out_parts: List[str] = []
    for i, name in enumerate(order):
        v = bufs[name]
        if not v:
            continue
        if out_parts:
            sep = "\n\n" if name in para_breaks else "\n"
            out_parts.append(sep)
        out_parts.append(v)
    if out_parts:
        node.children.append(UHSNode(type="InfoData", content="".join(out_parts)))
    return count


def _parse_incentive(body, raw, raw_off, root, cur, key, start, count, log):
    title = "Incentive: " + _line(body, start + 1)
    node = UHSNode(type="Incentive", content=title)
    _add_child(root, cur, node, start)
    if count >= 3:
        data = decrypt_nest_string(_line(body, start + 2), key)
        node.children.append(UHSNode(type="IncentiveData", content=data))
    return count


def _parse_image(body, raw, raw_off, root, cur, key, start, count, log):
    """hyperpng / gifa: read main image bytes, then per-zone children.
    Format (per OpenUHSLib's parseHyperImageNode):

        <count> hyperpng | gifa
        <title>
        <id> <offset> <length>      (3 tokens — main image spec)
        <x1> <y1> <x2> <y2>         (zone coords, then a nested hunk)
        <inner-hunk lines...>
        ...

    Inner hunks may be: link, overlay, hyperpng, gifa, text, hint.
    """
    title = parse_text_escapes(_line(body, start + 1))
    wrap = UHSNode(type="HotSpot", content=title)
    _add_child(root, cur, wrap, start)

    spec = _line(body, start + 2).split(" ")
    img = UHSNode(type="Image", content="^IMAGE^", kind="image")
    if len(spec) >= 3 and raw_off != -1:
        try:
            offset = int(spec[-2]) - raw_off
            length = int(spec[-1])
            if 0 <= offset and offset + length <= len(raw):
                img.binary = raw[offset:offset + length]
        except ValueError as e:
            log.debug("hyperpng main spec bad: %r (%s)", spec, e)
    wrap.children.append(img)

    # Zones are at start+3 onward, until inner_end = start+count.
    inner_end = start + count
    j = start + 3
    while j < inner_end:
        zone_line = _line(body, j)
        zone_parts = zone_line.split(" ")
        if len(zone_parts) != 4:
            log.debug("hyperpng: expected 4-token zone line at %d, "
                      "got %r — skipping", j, zone_line)
            j += 1
            continue
        try:
            x1, y1, x2, y2 = (int(zone_parts[i]) for i in range(4))
        except ValueError:
            j += 1
            continue
        # Next line is the hunk type / count.
        head = _line(body, j + 1)
        m = re.match(r"^(\d+)\s+(\w+)\s*$", head)
        if not m:
            j += 1
            continue
        inner_count = int(m.group(1))
        inner_kind = m.group(2)

        if inner_kind == "overlay":
            ov_title = _line(body, j + 2)
            ov_spec = _line(body, j + 3).split(" ")
            ov = UHSNode(type="Overlay",
                         content=parse_text_escapes(ov_title),
                         kind="image",
                         zone=(x1, y1, x2, y2))
            if len(ov_spec) >= 3 and raw_off != -1:
                try:
                    ofs = int(ov_spec[-4]) - raw_off
                    leng = int(ov_spec[-3])
                    if 0 <= ofs and ofs + leng <= len(raw):
                        ov.binary = raw[ofs:ofs + leng]
                except (ValueError, IndexError):
                    pass
            wrap.children.append(ov)
            j += 1 + inner_count   # zone + inner_count lines consumed
        else:
            # Recurse into the inner hunk; result attaches to wrap with
            # the zone coords stamped on it.
            kids_before = len(wrap.children)
            consumed = _build_nodes(
                body, raw, raw_off, root, wrap, key, j + 1, log)
            if len(wrap.children) == kids_before + 1:
                wrap.children[-1].zone = (x1, y1, x2, y2)
            j = consumed
            # _build_nodes returns the absolute index of the next line, so
            # the j increment is implicit. Add 1 for the zone line we
            # already consumed (it sits before the hunk header).
            # Note: consumed is start+inner_count for the parsed hunk, so
            # j is now correctly past it. The zone line itself was at j-1
            # (we passed j+1 to _build_nodes). Fix: we need to add 1
            # because _build_nodes was called with j+1, not j.
            # Actually _build_nodes returns next absolute index after
            # parsing one node; that index already accounts for the hunk
            # but NOT for the zone line we read at j. Compensate by NOT
            # adding 1 here — _build_nodes returned (j+1)+inner_count,
            # which is past the entire zone+hunk pair. So j is correct.
    return count


def _parse_sound(body, raw, raw_off, root, cur, key, start, count, log):
    """sound: <count> sound / title / <id> <offset> <length>"""
    title = parse_text_escapes(_line(body, start + 1))
    wrap = UHSNode(type="Sound", content=title)
    _add_child(root, cur, wrap, start)
    sd = UHSNode(type="SoundData", content="^AUDIO^", kind="audio")
    if count >= 3:
        spec = _line(body, start + 2).split(" ")
        if len(spec) >= 3 and raw_off != -1:
            try:
                offset = int(spec[-2]) - raw_off
                length = int(spec[-1])
                if 0 <= offset and offset + length <= len(raw):
                    sd.binary = raw[offset:offset + length]
            except ValueError as e:
                log.debug("sound spec bad: %r (%s)", spec, e)
    wrap.children.append(sd)
    return count


# Backwards-compat aliases used by the dispatch table.
_parse_skip_image = _parse_image
_parse_skip_sound = _parse_sound


def _parse_unknown(body, raw, raw_off, root, cur, key, start, count, log):
    log.debug("unknown node kind at %d: %r", start, _line(body, start))
    return count


# ---------------------------------------------------------------------------
# UHS encoder — turn a UHSNode tree into a binary 96a .uhs file.
# Limited to the node types a human-authored hint file actually needs:
# Subject / Question (as 'hint') / Comment / Credit / Version / Blank.
# Image / Audio / Text-hunk nodes are not emitted (they require a binary
# tail and aren't useful for hand-authored content). The parser will read
# what we write — round-trip is verified in tests.
# ---------------------------------------------------------------------------

def _enc_string(s: str) -> str:
    """Inverse of decrypt_string: byte → ((byte+32)/2 if even else (byte+127)/2).
    Char codes 32..126 (printable ASCII) round-trip cleanly. Non-ASCII
    Unicode is sanitised to ASCII first."""
    s = _sanitise(s)
    out = []
    for ch in s:
        c = ord(ch)
        if c < 32 or c > 126:
            # Non-printable: drop it (matches what a real UHS file would have).
            continue
        # Inverse of: c < 80 → c*2-32, else c*2-127
        # We want enc such that decrypt(enc) == c.
        # Try the "low" branch first (output < 80): enc*2-32 == c → enc = (c+32)/2
        if (c + 32) % 2 == 0 and (c + 32) // 2 < 80:
            out.append(chr((c + 32) // 2))
        else:
            out.append(chr((c + 127) // 2))
    return "".join(out)


def _enc_nest_string(s: str, key: List[int]) -> str:
    """Inverse of decrypt_nest_string."""
    out = []
    klen = len(key)
    for i, ch in enumerate(s):
        c = ord(ch)
        # decrypt does: tmp = ord - (key[i%klen] ^ (i+40)); while tmp<32: tmp+=96
        # so encode: ord = c + (key[i%klen] ^ (i+40)), then we may need to
        # subtract 96 once or twice to get back into the printable range.
        v = c + (key[i % klen] ^ (i + 40))
        # Pull v down into a byte the parser will lift back to c.
        while v > 127:
            v -= 96
        out.append(chr(v))
    return "".join(out)


def _enc_text_hunk(s: str, key: List[int]) -> str:
    """Inverse of decrypt_text_hunk: same shape as _enc_nest_string but
    using `co = i % klen` for both the key index and the +40 offset."""
    out = []
    klen = len(key)
    for i, ch in enumerate(s):
        co = i % klen
        v = ord(ch) + (key[co] ^ (co + 40))
        while v > 127:
            v -= 96
        out.append(chr(v))
    return "".join(out)


def _count_lines(node: UHSNode) -> int:
    """How many file-lines this node and its descendants will occupy.
    The header line (e.g. '5 subject') counts as 1; then title; then
    inner content. This matches the count Java's parsers expect at the
    head of each hunk."""
    t = node.type
    if t == "Subject":
        # 1 (header) + 1 (title) + sum(child sizes)
        return 2 + sum(_count_lines(c) for c in node.children)
    if t == "Question":
        # header + title + (hint segments) + (= separator + nested child)*
        hints = [c for c in node.children if c.type == "Hint"]
        nested = [c for c in node.children if c.type != "Hint"]
        lines = 0
        for i, h in enumerate(hints):
            if i > 0:
                lines += 1   # '-' separator
            lines += max(1, h.content.count("\n") + 1)
        for c in nested:
            lines += 1                       # '=' separator
            lines += _count_lines(c)
        return 2 + lines
    if t == "Comment":
        # header + title + 1 line per content (joined by ' ' on parse)
        if node.children and node.children[0].type == "CommentData":
            return 3   # header, title, single content line
        return 2
    if t == "Credit":
        if node.children and node.children[0].type == "CreditData":
            return 3
        return 2
    if t == "Version":
        return 3   # header, title (becomes "Version: ..."), content
    if t == "Info":
        # header + title + N data lines (one per \n-separated line of InfoData)
        data = ""
        if node.children and node.children[0].type == "InfoData":
            data = node.children[0].content
        n = max(1, data.count("\n") + 1) if data else 0
        return 2 + n
    if t == "Incentive":
        # header + title + 1 nest-encrypted data line
        if node.children and node.children[0].type == "IncentiveData":
            return 3
        return 2
    if t == "Link":
        # header + title + target id
        return 3
    if t == "Text":
        # header + title + spec line (offset/length into binary tail)
        return 3
    if t == "HotSpot":
        # header + title + main-image spec; zones contribute (4 coords +
        # inner hunk) per child that has node.zone set. Overlay inner =
        # 1+1+1 = 3 lines (zone + 'N overlay' + title + spec).
        # Recursive types use _count_lines via inner hunk.
        n = 3
        for c in node.children:
            if c.zone is None:
                continue
            if c.type == "Overlay":
                n += 4   # zone(1) + '<n> overlay'(1) + title(1) + spec(1)
            else:
                n += 1 + _count_lines(c)
        return n
    if t == "Sound":
        # header + title + spec line
        return 3
    if t == "Blank":
        return 1   # just the header
    return 0


def _emit(node: UHSNode, key: List[int], out: List[str]):
    """Append the file-lines for this node and its descendants to `out`.
    `out` is a list of strings, each becoming one CRLF-terminated line."""
    t = node.type
    n_lines = _count_lines(node)

    if t == "Subject":
        out.append(f"{n_lines} subject")
        out.append(_sanitise(node.content))
        for c in node.children:
            _emit(c, key, out)

    elif t == "Question":
        # Two flavors: 'hint' (plain — only Hint children) and 'nesthint'
        # (mixed — Hint + nested non-Hint children, joined by '=' lines).
        # The cipher used for hint segments differs:
        #   plain hint  → _enc_string  (parser: decrypt_string)
        #   nesthint    → _enc_nest_string (parser: decrypt_nest_string)
        hints = [c for c in node.children if c.type == "Hint"]
        nested = [c for c in node.children if c.type != "Hint"]
        is_nesthint = bool(nested)
        cipher = _enc_nest_string if is_nesthint else _enc_string

        def encode_line(s: str) -> str:
            return cipher(s, key) if is_nesthint else cipher(s)

        out.append(f"{n_lines} {'nesthint' if is_nesthint else 'hint'}")
        out.append(_sanitise(node.content))
        # Emit hint segments first.
        for i, h in enumerate(hints):
            if i > 0:
                out.append("-")
            ln_parts = h.content.split("\n")
            is_multiline = len(ln_parts) > 1
            for j, ln in enumerate(ln_parts):
                if ln == "":
                    out.append(" ")    # bare-space → '\n \n' on parse
                elif j == 0 and is_multiline:
                    out.append(encode_line("#w-" + ln))
                else:
                    out.append(encode_line(ln))
        # Then nested non-Hint children, each preceded by '='.
        for c in nested:
            out.append("=")
            _emit(c, key, out)

    elif t == "Comment":
        out.append(f"{n_lines} comment")
        out.append(_sanitise(node.content))
        if node.children and node.children[0].type == "CommentData":
            # Comment content is joined with ' ' on parse, so we emit it as
            # one line. Multi-paragraph comments lose paragraph breaks here;
            # use multiple Comment nodes for that.
            out.append(_sanitise(node.children[0].content.replace("\n", " ")))

    elif t == "Credit":
        out.append(f"{n_lines} credit")
        out.append(_sanitise(node.content))
        if node.children and node.children[0].type == "CreditData":
            out.append(_sanitise(node.children[0].content.replace("\n", " ")))

    elif t == "Version":
        out.append(f"{n_lines} version")
        # Version title gets "Version: " prefix added on parse, so strip it
        # from our content if present.
        title = node.content
        if title.startswith("Version: "):
            title = title[len("Version: "):]
        out.append(_sanitise(title))
        if node.children and node.children[0].type == "VersionData":
            out.append(_sanitise(node.children[0].content))
        else:
            out.append("")

    elif t == "Info":
        out.append(f"{n_lines} info")
        # Parser auto-prefixes "Info: " on the title; strip it on emit.
        title = node.content
        if title.startswith("Info: "):
            title = title[len("Info: "):]
        out.append(_sanitise(title))
        if node.children and node.children[0].type == "InfoData":
            for ln in node.children[0].content.split("\n"):
                out.append(_sanitise(ln))

    elif t == "Incentive":
        out.append(f"{n_lines} incentive")
        title = node.content
        if title.startswith("Incentive: "):
            title = title[len("Incentive: "):]
        out.append(_sanitise(title))
        if node.children and node.children[0].type == "IncentiveData":
            out.append(_enc_nest_string(
                _sanitise(node.children[0].content), key))

    elif t == "Link":
        out.append(f"{n_lines} link")
        out.append(_sanitise(node.content))
        # link_target is the integer id of the target node. The id is
        # the file-line position where the target's header sits; if the
        # tree structure is preserved end-to-end, the original id is
        # still valid on round-trip. (For markdown round-trip with
        # structure changes, see _allocate_link_remap below.)
        target = node.link_target
        if -1 != getattr(node, "_remap_target", -1):
            target = node._remap_target
        out.append(str(target))

    elif t == "Text":
        out.append(f"{n_lines} text")
        out.append(_sanitise(node.content))
        # Placeholder spec line — encode_uhs's two-pass layout patches
        # the offset+length once the binary tail position is known.
        # Format: 9-digit-id, '0', 10-digit offset, 10-digit length.
        out.append("000000000 0 0000000000 0000000000")
        node._spec_line_idx = len(out) - 1

    elif t == "HotSpot":
        # Pick header keyword by main image format. Default to hyperpng.
        img = next((c for c in node.children if c.type == "Image"), None)
        kind = "hyperpng"
        if img is not None and img.binary:
            ext, _ = _detect_binary_kind(img.binary)
            if ext == "gif":
                kind = "gifa"
        out.append(f"{n_lines} {kind}")
        out.append(_sanitise(node.content))
        # Main image spec: '<9-digit-id> <offset> <length>' (3 tokens).
        out.append("000000000 0000000000 0000000000")
        if img is not None:
            img._spec_line_idx = len(out) - 1
        # Zones — only children with node.zone set are emitted as
        # zone-bearing siblings inside the HotSpot.
        for c in node.children:
            if c.zone is None:
                continue
            x1, y1, x2, y2 = c.zone
            out.append(f"{x1:04d} {y1:04d} {x2:04d} {y2:04d}")
            if c.type == "Overlay":
                out.append("4 overlay")
                out.append(_sanitise(c.content))
                # Spec: '<id> <offset> <length> <x> <y>' (5 tokens).
                # Position defaults to (x1, y1) when absent.
                out.append(
                    f"000000000 0000000000 0000000000 "
                    f"{x1:04d} {y1:04d}")
                c._spec_line_idx = len(out) - 1
            else:
                _emit(c, key, out)

    elif t == "Sound":
        out.append(f"{n_lines} sound")
        out.append(_sanitise(node.content))
        out.append("000000000 0000000000 0000000000")
        sd = next((c for c in node.children
                   if c.type == "SoundData"), None)
        if sd is not None:
            sd._spec_line_idx = len(out) - 1

    elif t == "Blank":
        out.append(f"{n_lines} blank")


_ASCII_FALLBACKS = {
    "\u2014": "--",   # em-dash
    "\u2013": "-",    # en-dash
    "\u2018": "'",    # left single quote
    "\u2019": "'",    # right single quote / apostrophe
    "\u201c": '"',    # left double quote
    "\u201d": '"',    # right double quote
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",    # non-breaking space
    "\u2192": "->",   # right arrow
    "\u2190": "<-",   # left arrow
    "\u2022": "*",    # bullet
}


def _sanitise(s: str) -> str:
    """Map common Unicode punctuation to ASCII, drop anything else outside
    Latin-1. UHS files are 8-bit Latin-1 internally; the parser's text-escape
    system is the official way to embed accents (see parse_text_escapes)."""
    out = []
    for ch in s:
        if ch in _ASCII_FALLBACKS:
            out.append(_ASCII_FALLBACKS[ch])
            continue
        if ord(ch) < 256:
            out.append(ch)
        else:
            # Drop unrepresentable chars — better than crashing.
            out.append("?")
    return "".join(out)


def _allocate_ids(node: UHSNode, idx: int,
                  remap: Dict[int, int]) -> int:
    """Walk in encoder DFS order; populate remap[node.id_at_input] = idx
    so Link.link_target can be patched after the user's edits may have
    shifted positional ids. Returns the index past this node's footprint."""
    if node.id != -1:
        remap[node.id] = idx
    t = node.type
    if t == "Subject":
        # header(1) + title(1) + children
        sub_idx = idx + 2
        for c in node.children:
            sub_idx = _allocate_ids(c, sub_idx, remap)
        return sub_idx
    return idx + _count_lines(node)


def _patch_link_targets(node: UHSNode, remap: Dict[int, int]) -> None:
    """Walk tree; for each Link, set node._remap_target = new positional id
    if the original link_target appears in the remap. _emit picks this up."""
    if node.type == "Link" and node.link_target in remap:
        node._remap_target = remap[node.link_target]
    for c in node.children:
        _patch_link_targets(c, remap)


def encode_uhs(root: UHSNode, master_title: str, version_label: str = "96a",
               version_data: str = "") -> bytes:
    """Build a complete .uhs file from a UHSNode tree.

    The tree is expected to be a Root whose children are the top-level
    chapter/section Subjects. master_title is the file-level title (used
    for key derivation and for the master subject node). version_data is
    the free-form text shown under the Version node when reading the file
    — defaults to empty for clean output."""
    master_title = _sanitise(master_title)
    # Prepare a master Subject that wraps all children — that's what
    # AUX_NEST will undo on parse, restoring the user's structure.
    master = UHSNode(type="Subject", content=master_title,
                     children=list(root.children))

    # Append a Version node at the end as an "aux" sibling (sits outside
    # the master subject).
    version_node = UHSNode(type="Version",
                           content=f"Version: {version_label}",
                           children=[UHSNode(type="VersionData",
                                             content=version_data)])

    key = generate_key(master_title)

    # Pre-pass: allocate positional ids and patch Link.link_target
    # so links survive structural edits between export and compose.
    remap: Dict[int, int] = {}
    _allocate_ids(master, 1, remap)
    _patch_link_targets(master, remap)

    body_lines: List[str] = []
    _emit(master, key, body_lines)
    _emit(version_node, key, body_lines)

    # 88a-style filler that 9x readers must skip past.
    filler = [
        "If you do not have a UHS 91a (or higher) reader, read these hints!",
        "1",
        str(2 + len(body_lines) + 1),  # endHintSection (close enough)
        "** END OF 88A FORMAT **",
    ]

    # Header: "UHS\r\n" + master_title + "\r\n" + "1\r\n" + endHintSection + "\r\n"
    header = ["UHS", master_title, "1", str(len(body_lines) + 100)]
    all_lines = header + filler + body_lines

    # Collect every binary blob in DFS order. For each, remember the
    # body-line index of its placeholder spec and the spec FLAVOR
    # (text → 'id 0 offset length', hyperpng/sound → 'id offset length',
    # overlay → 'id offset length x y'). All placeholders are fixed
    # width so the body byte size doesn't change when we patch.
    body_offset_in_all = len(header) + len(filler)
    bin_blocks: List[bytes] = []
    # Each spec entry: (body_idx, flavor, length, extra_x_y_or_None)
    bin_specs: List[Tuple[int, str, int, Optional[Tuple[int, int]]]] = []

    def collect_bin(n: UHSNode) -> None:
        idx = getattr(n, "_spec_line_idx", -1)
        if n.type == "Text":
            if idx < 0:
                # No spec line — this Text is nested in a hunk type the
                # encoder doesn't yet recurse into (e.g. a `nesthint`).
                # Silently skip rather than corrupt offsets by writing
                # orphan bytes into the binary tail.
                pass
            else:
                data = ""
                if n.children and n.children[0].type == "TextData":
                    data = n.children[0].content
                data = _sanitise(data)
                ln_lines = data.split("\n")
                encoded_lines = [_enc_text_hunk(ln, key) for ln in ln_lines]
                block = ("\r\n".join(encoded_lines) + "\r\n").encode("latin-1")
                bin_blocks.append(block)
                bin_specs.append((idx, "text", len(block), None))
        elif n.type == "Image" and n.binary and idx >= 0:
            bin_blocks.append(n.binary)
            bin_specs.append((idx, "hyperpng", len(n.binary), None))
        elif n.type == "Overlay" and n.binary and idx >= 0:
            bin_blocks.append(n.binary)
            xy = (n.zone[0], n.zone[1]) if n.zone else (0, 0)
            bin_specs.append((idx, "overlay", len(n.binary), xy))
        elif n.type == "SoundData" and n.binary and idx >= 0:
            bin_blocks.append(n.binary)
            bin_specs.append((idx, "sound", len(n.binary), None))
        for c in n.children:
            collect_bin(c)
    collect_bin(master)

    if not bin_blocks:
        text = ("\r\n".join(all_lines) + "\r\n").encode("latin-1")
        return text + b"\x1a"

    # File layout: text_bytes + '\x1a' + binary_tail. The parser locates
    # the boundary via data.find(b'\x1a'), so the binary tail starts at
    # (header+body byte length) + 1 (for the \x1a separator itself).
    text_bytes = ("\r\n".join(all_lines) + "\r\n").encode("latin-1")
    binary_tail_start = len(text_bytes) + 1

    cursor = binary_tail_start
    for body_idx, flavor, length, extra in bin_specs:
        all_idx = body_offset_in_all + body_idx
        if flavor == "text":
            all_lines[all_idx] = (
                f"000000000 0 {cursor:010d} {length:010d}")
        elif flavor == "overlay":
            x, y = extra if extra else (0, 0)
            all_lines[all_idx] = (
                f"000000000 {cursor:010d} {length:010d} "
                f"{x:04d} {y:04d}")
        else:   # hyperpng / sound
            all_lines[all_idx] = (
                f"000000000 {cursor:010d} {length:010d}")
        cursor += length

    text_bytes_final = ("\r\n".join(all_lines) + "\r\n").encode("latin-1")
    if len(text_bytes_final) != len(text_bytes):
        raise RuntimeError(
            f"encode_uhs: text section size shifted after patching "
            f"({len(text_bytes)} → {len(text_bytes_final)}); "
            f"placeholder widths must match real values")

    return text_bytes_final + b"\x1a" + b"".join(bin_blocks)



# ---------------------------------------------------------------------------
# Notes & Compose — author your own .uhs files via a simple markdown format.
# ---------------------------------------------------------------------------

NOTES_TEMPLATE = """\
# {title}

<!--
my-uhs notes file. Edit this in your favorite editor, then run
    my-uhs compose {slug}
to turn it into a real .uhs file in your local catalog.

FORMAT
======
- '# Title' on the first line is the file title (used everywhere).
- '## Chapter Name' starts a chapter (a Subject in UHS terms).
- '### Puzzle / Question' starts a question. Phrase it as the player would.
- Under each question, write hint TIERS as bullets — each bullet is one hint.
  Order them from gentle nudge → clearer direction → full answer.
- '> Note: ...' under a chapter creates a Comment node (background info,
  not a puzzle).
- '> Credit: ...' creates a Credit block (e.g. attribution, your name).
- Blank lines and any other markdown are ignored.

Tip: write the LATER hints first while the puzzle is fresh in your mind,
then go back and write the gentler nudges that don't spoil it.
-->

## Chapter 1 — Getting Started

> Note: This is the opening section. Mention any general gameplay tips here.

### How do I do the first thing?

- Have you tried looking around carefully?
- The thing you need is in the chest by the window.
- Open the chest, take the glass bottle, and use your repair spell on it.

### What about the second puzzle?

- The girl in the clearing wants something.
- She's trying to build a stick fortress — yours can help.
- Cast repair on the bottle, then use the shards on her fortress.

## Chapter 2 — Next Section

### Replace this with your own puzzles

- First nudge.
- Clearer direction.
- Full solution.

> Credit: Authored by you, {date}.
"""


_HINT_TIER_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_NOTE_RE      = re.compile(r"^>\s*Note:\s*(.+)$", re.IGNORECASE)
_CREDIT_RE    = re.compile(r"^>\s*Credit:\s*(.+)$", re.IGNORECASE)
_INFO_RE      = re.compile(r"^>\s*Info:\s*(.+)$", re.IGNORECASE)
_INCENTIVE_RE = re.compile(r"^>\s*Incentive:\s*(.+)$", re.IGNORECASE)
_IMAGE_FILE_RE = re.compile(r"^>\s*Image file:\s*(.+?)\s*$",
                            re.IGNORECASE)
_SOUND_FILE_RE = re.compile(r"^>\s*Sound file:\s*(.+?)\s*$",
                            re.IGNORECASE)
_OVERLAY_RE   = re.compile(
    r"^>\s*Overlay:\s*(.*?)\s*@\s*\((\d+),(\d+)\)-\((\d+),(\d+)\)"
    r"\s*[—-]\s*see\s+(\S+)\s*$", re.IGNORECASE)
# Continuation of a previous '> ' blockquote — same '>' prefix, no
# 'Note/Credit/Info/Incentive' keyword, treated as a follow-up line of the
# most recently opened blockquote-style node.
_BLOCKQUOTE_CONT_RE = re.compile(r"^>\s?(.*)$")
# `{#N}` id marker that may follow a Subject/Question heading. Stripped
# from the visible title and used to set node.id so Link cross-references
# can resolve via encode_uhs's remap pass.
_ID_MARKER_RE = re.compile(r"\s*\{#(\d+)\}\s*$")
# `[Link: title -> #N]` Link reference inside a Subject body.
_LINK_REF_RE  = re.compile(r"^\[Link:\s*(.+?)\s*->\s*#(\d+)\]\s*$",
                           re.IGNORECASE)


def _strip_id_marker(line: str) -> Tuple[str, int]:
    """Return (title-without-marker, id_int_or_-1)."""
    m = _ID_MARKER_RE.search(line)
    if not m:
        return line, -1
    return line[:m.start()].rstrip(), int(m.group(1))


def parse_notes_markdown(
        text: str,
        base_dir: Optional[Path] = None) -> Tuple[str, UHSNode]:
    """Parse a my-uhs notes markdown file into (title, root_node).
    Returns a Root node whose children are top-level Subjects (chapters).

    `base_dir` is the directory containing the .md file; sidecar filenames
    referenced via `> Image file: ...` / `> Sound file: ...` are resolved
    relative to it. None disables sidecar resolution (binaries dropped)."""
    lines = text.splitlines()
    title = "Untitled"
    root = UHSNode(type="Root", content="")
    # `current_chapter` is the most-recent top-level Subject (the `## ` one).
    # `current_subject` may equal `current_chapter` OR a nested Subject
    # opened by `### Sub: ...`; new Questions attach to current_subject.
    current_chapter: Optional[UHSNode] = None
    current_subject: Optional[UHSNode] = None
    current_question: Optional[UHSNode] = None
    # Last opened blockquote-style data node (Comment/Credit/Info/Incentive)
    # — receives subsequent unprefixed `> ` continuation lines.
    current_blockquote_data: Optional[UHSNode] = None
    # `### Text: Title` opens a Text node; the next fenced ``` block's
    # body lines are gathered into this node's TextData child.
    pending_text_node: Optional[UHSNode] = None
    pending_text_lines: List[str] = []
    in_text_fence = False
    # `### Image:` and `### Sound:` open a HotSpot/Sound; the next few
    # `> Image file: ...` / `> Overlay: ...` / `> Sound file: ...`
    # blockquote lines attach binary content from sidecars.
    pending_hotspot: Optional[UHSNode] = None
    pending_sound: Optional[UHSNode] = None
    in_html_comment = False

    def attach_blockquote(parent: UHSNode, type_: str,
                          data_type: str, content: str) -> UHSNode:
        wrap = UHSNode(type=type_,
                       content="Note" if type_ == "Comment"
                       else ("Credit" if type_ == "Credit"
                             else (f"{type_}: " + content[:40])))
        data = UHSNode(type=data_type, content=content)
        wrap.children.append(data)
        parent.children.append(wrap)
        return data

    def _flush_pending_text() -> None:
        nonlocal pending_text_node, pending_text_lines, in_text_fence
        if pending_text_node is not None and pending_text_lines:
            pending_text_node.children.append(
                UHSNode(type="TextData",
                        content="\n".join(pending_text_lines)))
        pending_text_node = None
        pending_text_lines = []
        in_text_fence = False

    for raw in lines:
        ln = raw.rstrip()

        # Fenced ```...``` block following a `### Text:` heading. Open
        # fence on first ``` line, close on next ``` line, accumulate
        # raw content lines (preserving indentation) in between.
        if pending_text_node is not None:
            stripped = ln.lstrip()
            if stripped.startswith("```"):
                if in_text_fence:
                    _flush_pending_text()
                else:
                    in_text_fence = True
                continue
            if in_text_fence:
                pending_text_lines.append(ln)
                continue

        # Skip HTML comments (the embedded instructions in the template).
        if "<!--" in ln:
            in_html_comment = True
        if in_html_comment:
            if "-->" in ln:
                in_html_comment = False
            continue
        if not ln.strip():
            current_blockquote_data = None
            continue

        if ln.startswith("# ") and title == "Untitled":
            title = ln[2:].strip()
            root.content = title
            continue
        if ln.startswith("## "):
            title_text, marker_id = _strip_id_marker(ln[3:].strip())
            current_chapter = UHSNode(type="Subject", content=title_text)
            if marker_id != -1:
                current_chapter.id = marker_id
            root.children.append(current_chapter)
            current_subject = current_chapter
            current_question = None
            current_blockquote_data = None
            continue
        if ln.startswith("### "):
            body_text = ln[4:].strip()
            # `### Text: Title` opens a Text node; the following fenced
            # ```...``` block becomes its TextData content.
            if body_text.lower().startswith("text:"):
                title_text, marker_id = _strip_id_marker(
                    body_text[5:].strip())
                if current_subject is None:
                    current_chapter = UHSNode(
                        type="Subject", content="Hints")
                    root.children.append(current_chapter)
                    current_subject = current_chapter
                txt = UHSNode(type="Text", content=title_text)
                if marker_id != -1:
                    txt.id = marker_id
                current_subject.children.append(txt)
                pending_text_node = txt
                pending_text_lines = []
                in_text_fence = False
                current_question = None
                current_blockquote_data = None
                continue
            # `### Image: Title` → HotSpot node; subsequent
            # `> Image file: path` lines attach binary content from a
            # sidecar file. `> Overlay: ... @ (x,y)-(x,y) — see file`
            # lines attach Overlay children with zone coords.
            if body_text.lower().startswith("image:"):
                title_text, marker_id = _strip_id_marker(
                    body_text[6:].strip())
                if current_subject is None:
                    current_chapter = UHSNode(
                        type="Subject", content="Hints")
                    root.children.append(current_chapter)
                    current_subject = current_chapter
                hs = UHSNode(type="HotSpot", content=title_text)
                if marker_id != -1:
                    hs.id = marker_id
                current_subject.children.append(hs)
                pending_hotspot = hs
                current_question = None
                current_blockquote_data = None
                continue
            # `### Sound: Title` → Sound node; subsequent
            # `> Sound file: path` attaches audio from sidecar.
            if body_text.lower().startswith("sound:"):
                title_text, marker_id = _strip_id_marker(
                    body_text[6:].strip())
                if current_subject is None:
                    current_chapter = UHSNode(
                        type="Subject", content="Hints")
                    root.children.append(current_chapter)
                    current_subject = current_chapter
                sn = UHSNode(type="Sound", content=title_text)
                if marker_id != -1:
                    sn.id = marker_id
                current_subject.children.append(sn)
                pending_sound = sn
                current_question = None
                current_blockquote_data = None
                continue
            # `### Sub: Title` opens a nested Subject under the current
            # top-level chapter. Subsequent Questions attach to it.
            if body_text.lower().startswith("sub:"):
                title_text, marker_id = _strip_id_marker(
                    body_text[4:].strip())
                if current_chapter is None:
                    current_chapter = UHSNode(
                        type="Subject", content="Hints")
                    root.children.append(current_chapter)
                nested = UHSNode(type="Subject", content=title_text)
                if marker_id != -1:
                    nested.id = marker_id
                current_chapter.children.append(nested)
                current_subject = nested
                current_question = None
                current_blockquote_data = None
                continue
            # Plain `### Title` is a Question.
            if current_subject is None:
                # No subject yet — synthesise a chapter.
                current_chapter = UHSNode(type="Subject", content="Hints")
                root.children.append(current_chapter)
                current_subject = current_chapter
            title_text, marker_id = _strip_id_marker(body_text)
            current_question = UHSNode(type="Question", content=title_text)
            if marker_id != -1:
                current_question.id = marker_id
            current_subject.children.append(current_question)
            current_blockquote_data = None
            continue

        # [Link: title -> #N] reference, sibling of Questions inside a
        # Subject. Matches a whole standalone line (no leading bullet etc).
        m_link = _LINK_REF_RE.match(ln.strip())
        if m_link and current_subject is not None:
            link = UHSNode(type="Link", content=m_link.group(1))
            link.link_target = int(m_link.group(2))
            current_subject.children.append(link)
            current_question = None
            current_blockquote_data = None
            continue

        m_note = _NOTE_RE.match(ln)
        if m_note and current_subject is not None:
            current_blockquote_data = attach_blockquote(
                current_subject, "Comment", "CommentData", m_note.group(1))
            current_question = None
            continue

        m_credit = _CREDIT_RE.match(ln)
        if m_credit and current_subject is not None:
            current_blockquote_data = attach_blockquote(
                current_subject, "Credit", "CreditData", m_credit.group(1))
            current_question = None
            continue

        m_info = _INFO_RE.match(ln)
        if m_info and current_subject is not None:
            current_blockquote_data = attach_blockquote(
                current_subject, "Info", "InfoData", m_info.group(1))
            current_question = None
            continue

        m_incentive = _INCENTIVE_RE.match(ln)
        if m_incentive and current_subject is not None:
            current_blockquote_data = attach_blockquote(
                current_subject, "Incentive", "IncentiveData",
                m_incentive.group(1))
            current_question = None
            continue

        # `> Image file: foo.image.1.png` attaches the sidecar bytes
        # to the most recently opened HotSpot as its Image child.
        m_image_file = _IMAGE_FILE_RE.match(ln)
        if m_image_file and pending_hotspot is not None:
            fname = m_image_file.group(1).strip()
            data = b""
            if base_dir is not None:
                path = base_dir / fname
                try:
                    data = path.read_bytes()
                except OSError as e:
                    raise ValueError(
                        f"compose: missing sidecar {path!r}: {e}") from e
            img = UHSNode(type="Image", content="^IMAGE^",
                          kind="image", binary=data or None)
            pending_hotspot.children.append(img)
            continue

        # `> Overlay: title @ (x1,y1)-(x2,y2) — see file` attaches an
        # Overlay child with zone coords.
        m_overlay = _OVERLAY_RE.match(ln)
        if m_overlay and pending_hotspot is not None:
            ov_title = m_overlay.group(1).strip()
            x1, y1, x2, y2 = (int(m_overlay.group(i)) for i in (2, 3, 4, 5))
            fname = m_overlay.group(6).strip()
            data = b""
            if base_dir is not None:
                path = base_dir / fname
                try:
                    data = path.read_bytes()
                except OSError as e:
                    raise ValueError(
                        f"compose: missing sidecar {path!r}: {e}") from e
            ov = UHSNode(type="Overlay", content=ov_title,
                         kind="image", binary=data or None,
                         zone=(x1, y1, x2, y2))
            pending_hotspot.children.append(ov)
            continue

        # `> Sound file: foo.sound.1.wav` attaches sidecar bytes to the
        # most recently opened Sound node as its SoundData child.
        m_sound_file = _SOUND_FILE_RE.match(ln)
        if m_sound_file and pending_sound is not None:
            fname = m_sound_file.group(1).strip()
            data = b""
            if base_dir is not None:
                path = base_dir / fname
                try:
                    data = path.read_bytes()
                except OSError as e:
                    raise ValueError(
                        f"compose: missing sidecar {path!r}: {e}") from e
            sd = UHSNode(type="SoundData", content="^AUDIO^",
                         kind="audio", binary=data or None)
            pending_sound.children.append(sd)
            continue

        # Continuation of a `> ...` blockquote? Append to the last opened
        # blockquote's data, separated by '\n'. Allowed only when the line
        # actually starts with '>' AND we just opened a blockquote node.
        if (current_blockquote_data is not None
                and ln.lstrip().startswith(">")):
            m_cont = _BLOCKQUOTE_CONT_RE.match(ln.lstrip())
            if m_cont:
                tail = m_cont.group(1)
                if current_blockquote_data.content:
                    current_blockquote_data.content += "\n" + tail
                else:
                    current_blockquote_data.content = tail
                continue

        m_hint = _HINT_TIER_RE.match(ln)
        if m_hint and current_question is not None:
            current_question.children.append(
                UHSNode(type="Hint", content=m_hint.group(1).strip()))
            current_blockquote_data = None
            continue
        # Indented continuation of the most-recent hint: ≥ 2 leading
        # spaces and the previous structural line was a hint bullet.
        # Joined onto the hint with a literal newline.
        if (current_question is not None
                and current_question.children
                and current_question.children[-1].type == "Hint"
                and (raw.startswith("  ") or raw.startswith("\t"))
                and raw.strip()):
            tail = raw.lstrip()
            last = current_question.children[-1]
            last.content = last.content + "\n" + tail
            continue
        # Anything else is silently ignored — the file can hold the user's
        # own freeform notes alongside the structured content.

    _flush_pending_text()
    return title, root


def cmd_notes(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load(); cat.ensure_dirs()
    notes_dir = Path(cfg["catalog_dir"]) / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    slug = args.name.lower().replace(" ", "-").replace(".uhs", "")
    if not slug:
        print("my-uhs: notes name cannot be empty", file=sys.stderr)
        return 1
    notes_path = notes_dir / f"{slug}.md"

    if not notes_path.exists():
        title = args.name.replace("-", " ").replace("_", " ").title()
        from datetime import date
        notes_path.write_text(
            NOTES_TEMPLATE.format(title=title, slug=slug, date=date.today().isoformat()),
            encoding="utf-8")
        log.debug("created notes template at %s", notes_path)
        print(paint(f"# new notes file: {notes_path}", C.META))
    else:
        print(paint(f"# editing: {notes_path}", C.META))

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    rc = os.system(f'{editor} "{notes_path}"')
    if rc != 0:
        print(f"my-uhs: editor exited with status {rc}", file=sys.stderr)
        return 2
    print(paint(f"# saved. run: my-uhs compose {slug}", C.OK))
    return 0


def cmd_compose(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load(); cat.ensure_dirs()
    notes_dir = Path(cfg["catalog_dir"]) / "notes"
    slug = args.name.lower().replace(" ", "-").replace(".uhs", "")
    notes_path = notes_dir / f"{slug}.md"
    if not notes_path.is_file():
        print(f"my-uhs: no notes file: {notes_path}", file=sys.stderr)
        print("       run `my-uhs notes <name>` first.", file=sys.stderr)
        return 1

    title, root = parse_notes_markdown(
        notes_path.read_text(encoding="utf-8"),
        base_dir=notes_path.parent)
    if not root.children:
        print("my-uhs: notes file has no chapters (## headings)",
              file=sys.stderr)
        return 2
    log.debug("composed tree: %d chapters", len(root.children))

    # Encode and write into the catalog.
    name = f"{slug}.uhs"
    if not args.force and cat.get(name):
        print(f"my-uhs: already in catalog: {name} (use --force)",
              file=sys.stderr)
        return 1
    data = encode_uhs(root, title, "96a")
    dst = cat.files / name
    dst.write_bytes(data)

    # Verify by parsing it back — catches encoder bugs early.
    try:
        parsed_root, fmt = parse_uhs(str(dst), log)
        ver = hint_version(parsed_root) or fmt
        verified_title = hint_title(parsed_root) or title
    except UHSParseError as e:
        print(f"my-uhs: composed file failed to round-trip: {e}",
              file=sys.stderr)
        dst.unlink(missing_ok=True)
        return 2

    cat.add(CatalogEntry(
        name=name, title=verified_title, version=ver, path=str(dst),
        size=dst.stat().st_size, source="compose", fetched_at=time.time()))
    cat.save()
    print(paint(f"composed {name}  ({ver})  {verified_title}", C.OK))
    print(paint(f"# read with: my-uhs read {name}", C.META))
    return 0


# ---------------------------------------------------------------------------
# Export — turn an existing parsed .uhs into compose-grammar markdown so it
# can be edited by hand and fed back through `compose --force` to update.
# ---------------------------------------------------------------------------

def _md_escape_inline(s: str) -> str:
    """Just make sure the line doesn't accidentally start a markdown
    construct that the notes parser would mis-recognize."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


def _emit_hint_md(content: str, out: List[str]) -> None:
    """Emit a Hint as a `- ...` bullet. Multi-line content folds onto
    indented (2-space) continuation lines; parse_notes_markdown reads
    them back into a single Hint node, and the encoder embeds a leading
    `#w-` directive on multi-line hints so the parser restores newlines."""
    body = _md_escape_inline(content)
    parts = body.split("\n")
    out.append(f"- {parts[0]}")
    for cont in parts[1:]:
        out.append(f"  {cont}" if cont else "  ")


def serialize_uhs_to_notes_md(root: UHSNode,
                              sidecar_paths: Optional[Dict[int, str]] = None
                              ) -> str:
    """Serialize a parsed UHS tree back into the compose-grammar markdown
    notes format. Round-trips Subject/Question/Hint/Comment/Credit cleanly;
    Version/Blank are recreated by the encoder so they're omitted; HotSpot/
    Sound get a clearly-marked stub (binary content cannot yet be re-embedded
    on compose — see plan #2)."""
    title = root.content if root.content and root.content != "root" else "Untitled"
    out: List[str] = [f"# {title}", ""]
    out.append("<!--")
    out.append("Exported from a .uhs by `my-uhs export`.")
    out.append("Edit, then run: my-uhs compose <name> --force  (to update the .uhs)")
    out.append("-->")
    out.append("")

    unsupported: List[str] = []

    def _id_marker(n: UHSNode) -> str:
        return f" {{#{n.id}}}" if n.id != -1 else ""

    def emit_subject(s: UHSNode, depth: int) -> None:
        # depth 1 == top-level chapter ("## "). Depth 2 emits as
        # `### Sub: ...`. Deeper levels are flattened with a marker
        # comment — round-trip will collapse them to depth 2.
        title = _md_escape_inline(s.content or '(untitled)')
        marker = _id_marker(s)
        if depth == 1:
            out.append(f"## {title}{marker}")
        elif depth == 2:
            out.append(f"### Sub: {title}{marker}")
        else:
            out.append(
                f"<!-- TODO(plan-2): nested Subject at depth {depth} "
                f"flattened to depth 2 on round-trip -->")
            out.append(f"### Sub: {title}{marker}")
        out.append("")
        for c in s.children:
            emit_child(c, depth)

    def emit_question(q: UHSNode) -> None:
        out.append(
            f"### {_md_escape_inline(q.content or '(untitled)')}"
            f"{_id_marker(q)}")
        out.append("")
        for c in q.children:
            if c.type == "Hint":
                _emit_hint_md(c.content, out)
            elif c.type == "Question":
                # nested questions — flag (plan #2)
                out.append(
                    f"<!-- TODO(plan-2): nested Question "
                    f"'{(c.content or '')[:40]}' -->")
            else:
                # other children inside a question are rare; flag them
                unsupported.append(c.type)
                out.append(f"<!-- TODO(plan-2): {c.type} child of Question -->")
        out.append("")

    def emit_child(c: UHSNode, parent_depth: int) -> None:
        t = c.type
        if t == "Subject":
            emit_subject(c, parent_depth + 1)
        elif t == "Question":
            emit_question(c)
        elif t == "Comment":
            data = ""
            if c.children and c.children[0].type == "CommentData":
                data = c.children[0].content
            data = _md_escape_inline(data).replace("\n", " ")
            out.append(f"> Note: {data}")
            out.append("")
        elif t == "Credit":
            data = ""
            if c.children and c.children[0].type == "CreditData":
                data = c.children[0].content
            data = _md_escape_inline(data).replace("\n", " ")
            out.append(f"> Credit: {data}")
            out.append("")
        elif t == "Info":
            data = ""
            if c.children and c.children[0].type == "InfoData":
                data = c.children[0].content
            data = _md_escape_inline(data)
            parts = data.split("\n")
            out.append(f"> Info: {parts[0]}")
            for cont in parts[1:]:
                out.append(f"> {cont}" if cont else ">")
            out.append("")
        elif t == "Incentive":
            data = ""
            if c.children and c.children[0].type == "IncentiveData":
                data = c.children[0].content
            data = _md_escape_inline(data).replace("\n", " ")
            out.append(f"> Incentive: {data}")
            out.append("")
        elif t == "Link":
            label = _md_escape_inline(c.content or "(untitled)")
            tgt = c.link_target if c.link_target != -1 else 0
            out.append(f"[Link: {label} -> #{tgt}]")
            out.append("")
        elif t == "Text":
            title = _md_escape_inline(c.content or "(untitled)")
            data = ""
            if c.children and c.children[0].type == "TextData":
                data = c.children[0].content
            out.append(f"### Text: {title}{_id_marker(c)}")
            out.append("```")
            for ln in _md_escape_inline(data).split("\n"):
                out.append(ln)
            out.append("```")
            out.append("")
        elif t in ("Version", "Blank"):
            # Encoder regenerates these — drop on export.
            pass
        elif t == "HotSpot":
            label = _md_escape_inline(c.content or "(untitled)")
            out.append(f"### Image: {label}{_id_marker(c)}")
            # Find Image / Overlay children with sidecars; ignore any
            # other deeply-nested children for now (not yet round-trip).
            for child in c.children:
                if (child.type in ("Image", "Overlay")
                        and sidecar_paths is not None
                        and id(child) in sidecar_paths):
                    fname = sidecar_paths[id(child)]
                    if child.type == "Overlay" and child.zone:
                        x1, y1, x2, y2 = child.zone
                        out.append(
                            f"> Overlay: {_md_escape_inline(child.content or '')}"
                            f" @ ({x1},{y1})-({x2},{y2}) — see {fname}")
                    else:
                        out.append(f"> Image file: {fname}")
                elif child.type == "Image":
                    out.append("> (image bytes not extracted)")
            out.append("")
            # Plan #2: re-embedding requires the binary-tail encoder
            # for HyperImage; until that lands, write back will warn.
            unsupported.append(t)
        elif t == "Sound":
            label = _md_escape_inline(c.content or "(untitled)")
            out.append(f"### Sound: {label}{_id_marker(c)}")
            sd = next((cc for cc in c.children
                       if cc.type == "SoundData"), None)
            if (sd is not None and sidecar_paths is not None
                    and id(sd) in sidecar_paths):
                out.append(f"> Sound file: {sidecar_paths[id(sd)]}")
            else:
                out.append("> (sound bytes not extracted)")
            out.append("")
            unsupported.append(t)
        else:
            unsupported.append(t)
            out.append(f"<!-- TODO(plan-2): unhandled node type '{t}' -->")

    for c in root.children:
        emit_child(c, parent_depth=0)

    if unsupported:
        out.append("")
        out.append("<!--")
        out.append("EXPORT WARNINGS — these node types are not yet round-trippable")
        out.append("through `my-uhs compose` (see plan #2):")
        for t in sorted(set(unsupported)):
            out.append(f"  - {t} ({unsupported.count(t)}x)")
        out.append("Re-composing this file as-is will DROP those nodes.")
        out.append("-->")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def cmd_export(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    src = _resolve_file(args.file, cat)
    root, _ = parse_uhs(src, log)

    slug = Path(src).stem.lower()
    if args.dest:
        dest = Path(args.dest)
    else:
        notes_dir = Path(cfg["catalog_dir"]) / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        dest = notes_dir / f"{slug}.md"

    if dest.exists() and not args.force:
        print(f"my-uhs: refuse to overwrite {dest} (use --force)",
              file=sys.stderr)
        return 2

    # Export sidecar binary blobs (plan #2 §2b). Numbering is 1-based
    # in DFS order. Filenames are `<slug>.image.N.<ext>` and
    # `<slug>.sound.N.<ext>` next to the .md.
    dest_dir = dest.parent
    dest_stem = dest.stem
    sidecar_count = {"image": 0, "sound": 0}
    sidecar_paths: Dict[int, str] = {}   # id(node) -> filename

    def emit_sidecars(node: UHSNode) -> None:
        if node.binary:
            if node.type in ("Image", "Overlay"):
                sidecar_count["image"] += 1
                n = sidecar_count["image"]
                ext, _ = _detect_binary_kind(node.binary)
                fname = f"{dest_stem}.image.{n}.{ext}"
                (dest_dir / fname).write_bytes(node.binary)
                sidecar_paths[id(node)] = fname
            elif node.type == "SoundData":
                sidecar_count["sound"] += 1
                n = sidecar_count["sound"]
                ext, _ = _detect_binary_kind(node.binary)
                fname = f"{dest_stem}.sound.{n}.{ext}"
                (dest_dir / fname).write_bytes(node.binary)
                sidecar_paths[id(node)] = fname
        for c in node.children:
            emit_sidecars(c)
    emit_sidecars(root)

    md = serialize_uhs_to_notes_md(root, sidecar_paths=sidecar_paths)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(md, encoding="utf-8")
    n_img = sidecar_count["image"]
    n_snd = sidecar_count["sound"]
    print(paint(f"exported → {dest}", C.OK))
    if n_img or n_snd:
        print(paint(
            f"  + {n_img} image sidecar{'s' if n_img != 1 else ''}, "
            f"{n_snd} sound sidecar{'s' if n_snd != 1 else ''}", C.META))
    print(paint(f"# edit, then: my-uhs compose {slug} --force", C.META))
    return 0


# ---------------------------------------------------------------------------
# Print — colorized rendering matching Java's --print structure.
# Java emits TAB indentation; we keep that for layout fidelity.
# ---------------------------------------------------------------------------

NODE_COLORS = {
    "Root":         C.TITLE,
    "Subject":      C.SUBJECT,
    "Question":     C.QUESTION,
    "Hint":         C.HINT,
    "Comment":      C.COMMENT,
    "CommentData":  C.COMMENT,
    "Credit":       C.CREDIT,
    "CreditData":   C.CREDIT,
    "Text":         C.TEXT,
    "TextData":     C.TEXT,
    "Info":         C.INFO,
    "Version":      C.INFO,
    "VersionData":  C.INFO,
    "Incentive":    C.INFO,
    "IncentiveData":C.INFO,
    "Link":         C.LINK,
    "Image":        C.SKIP,
    "Sound":        C.SKIP,
    "Blank":        C.META,
}


def render(node: UHSNode, paint: Paint, depth: int = 0, out=sys.stdout):
    indent = "\t" * depth
    id_str = "" if node.id == -1 else f"^{node.id}^: "
    link_str = "" if not node.is_link else f" (^Link to {node.link_target}^)"
    color = NODE_COLORS.get(node.type, C.HINT)

    if node.kind == "image":
        body = "^IMAGE^"
    elif node.kind == "audio":
        body = "^AUDIO^"
    else:
        body = node.content

    line_id = paint(id_str, C.META) if id_str else ""
    line_link = paint(link_str, C.META) if link_str else ""
    out.write(f"{indent}{line_id}{paint(body, color)}{line_link}\n")
    for ch in node.children:
        render(ch, paint, depth + 1, out)


def hint_title(root: UHSNode) -> Optional[str]:
    """Mirror getHintTitle: root content, or first child if it's 'root'."""
    if root.content == "root":
        if root.children and root.children[0].type == "Subject":
            return root.children[0].content or None
        return None
    return root.content or None


def hint_version(node: UHSNode) -> Optional[str]:
    """Reverse-walk to find the last Version node's content (sans 'Version: ')."""
    found: List[str] = []
    def walk(n):
        if n.type == "Version" and n.content:
            found.append(n.content)
        for c in n.children:
            walk(c)
    walk(node)
    if not found:
        return None
    last = found[-1]
    return last[len("Version: "):] if last.startswith("Version: ") else last


# ---------------------------------------------------------------------------
# Local catalog
# ---------------------------------------------------------------------------

@dataclass
class CatalogEntry:
    name: str          # canonical key, e.g. "alone.uhs"
    title: str
    version: str
    path: str          # absolute path to the .uhs file in the catalog
    size: int
    source: str        # "pull" or "push"
    fetched_at: float  # unix epoch
    remote_url: str = ""

    def to_dict(self) -> Dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict) -> "CatalogEntry":
        return cls(**{k: d.get(k, "") for k in (
            "name", "title", "version", "path", "size", "source",
            "fetched_at", "remote_url")})


class Catalog:
    def __init__(self, root: str, log: logging.Logger):
        self.root = Path(root)
        self.files = self.root / "files"
        self.index_path = self.root / "index.json"
        self.remote_cache = self.root / "remote-catalog.xml"
        self.log = log
        self._entries: Dict[str, CatalogEntry] = {}

    def ensure_dirs(self):
        self.files.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        if not self.index_path.is_file():
            self._entries = {}
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.log.warning("index unreadable, starting fresh: %s", e)
            self._entries = {}
            return
        self._entries = {k: CatalogEntry.from_dict(v) for k, v in data.items()}

    def save(self) -> None:
        self.ensure_dirs()
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {k: v.to_dict() for k, v in self._entries.items()},
            indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.index_path)

    def list(self) -> List[CatalogEntry]:
        return sorted(self._entries.values(), key=lambda e: e.title.lower())

    def get(self, name: str) -> Optional[CatalogEntry]:
        return self._entries.get(name.lower())

    def add(self, entry: CatalogEntry) -> None:
        self._entries[entry.name.lower()] = entry

    def remove(self, name: str) -> bool:
        return self._entries.pop(name.lower(), None) is not None


# ---- Remote catalog (uhs-hints.com update.cgi) ----

_FILE_RE = re.compile(
    r"<FILE>(.*?)</FILE>", re.DOTALL)
_TAG_RE = lambda tag: re.compile(rf"<{tag}>(.*?)</{tag}>", re.DOTALL)


@dataclass
class RemoteEntry:
    title: str
    name: str
    url: str
    date: str
    csize: int
    fsize: int


def fetch_remote_catalog(cfg: Dict[str, str], log: logging.Logger
                         ) -> List[RemoteEntry]:
    url = cfg["catalog_url"]
    timeout = float(cfg["fetch_timeout"])
    req = urllib.request.Request(url, headers={"User-Agent": cfg["user_agent"]})
    log.debug("fetching catalog: %s", url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("latin-1")
    cache = Path(cfg["catalog_dir"]) / "remote-catalog.xml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(body, encoding="latin-1")
    return _parse_remote_catalog(body)


def _parse_remote_catalog(text: str) -> List[RemoteEntry]:
    out: List[RemoteEntry] = []
    flat = re.sub(r"[\r\n]", "", text)
    for chunk in _FILE_RE.findall(flat):
        def grab(tag: str) -> str:
            m = _TAG_RE(tag).search(chunk)
            return m.group(1).strip() if m else ""
        try:
            csize = int(grab("FSIZE") or "0")
            fsize = int(grab("FFULLSIZE") or "0")
        except ValueError:
            csize = fsize = 0
        out.append(RemoteEntry(
            title=grab("FTITLE"),
            name=grab("FNAME"),
            url=grab("FURL"),
            date=grab("FDATE"),
            csize=csize,
            fsize=fsize,
        ))
    return out


def fetch_and_extract_uhs(remote: RemoteEntry, cfg: Dict[str, str],
                          dest_dir: Path, log: logging.Logger) -> Path:
    """Download the .zip, extract its single .uhs, return path."""
    timeout = float(cfg["fetch_timeout"])
    req = urllib.request.Request(remote.url,
                                 headers={"User-Agent": cfg["user_agent"]})
    log.debug("downloading %s", remote.url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        zbytes = r.read()
    with zipfile.ZipFile(BytesIO(zbytes)) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith(".uhs")]
        if not members:
            raise RuntimeError(f"no .uhs in archive at {remote.url}")
        member = members[0]
        target = dest_dir / remote.name.lower()
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as dst:
            dst.write(src.read())
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="my-uhs",
        description="Colorized command-line reader and local catalog manager "
                    "for Universal Hint System (.uhs) files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-c", "--config", nargs="?", const="__SHOW__",
                   default=None, metavar="PATH",
                   help="without PATH: print current effective config and "
                        "exit; with PATH: use that config file (skips search)")
    p.add_argument("--create-config", nargs="?", const="__STDOUT__",
                   default=None, metavar="PATH",
                   help="without PATH: print default config to stdout; "
                        "with PATH: write default config to PATH "
                        "(refuses to overwrite)")
    p.add_argument("-D", "--debug", action="store_true",
                   help="enable debug logging to the configured logfile")
    p.add_argument("--no-color", action="store_true",
                   help="disable ANSI color")
    p.add_argument("--complete", metavar="TOPIC",
                   help="emit machine-readable completions for TOPIC "
                        "(files | titles | help-topics) — used by zsh")
    p.add_argument("--version", action="version",
                   version=f"my-uhs {__version__}")

    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("read", help="render a .uhs file (colorized)")
    sp.add_argument("file", help="path to .uhs file, or a name in the catalog")

    sp = sub.add_parser("use",
                        help="interactive hint mode — walk chapters and "
                             "reveal hints one at a time (no spoilers)")
    sp.add_argument("file", help="path to .uhs file, or a name in the catalog")

    sp = sub.add_parser("title", help="print only the file's title")
    sp.add_argument("file")

    sp = sub.add_parser("version", help="print only the file's declared version")
    sp.add_argument("file")

    sp = sub.add_parser("test", help="parse and report success/failure")
    sp.add_argument("file")

    sp = sub.add_parser("list", help="list local catalog entries")
    sp.add_argument("--search", metavar="TERM",
                    help="filter by title or filename substring "
                         "(case-insensitive)")

    sp = sub.add_parser("catalog",
                        help="refresh the cached remote catalog index")
    sp.add_argument("--search", metavar="TERM",
                    help="filter listing by title substring (case-insensitive)")

    sp = sub.add_parser("pull",
                        help="download from the remote catalog into the "
                             "local catalog. NAME may be an exact filename, "
                             "a title/filename substring, or 'all'.")
    sp.add_argument("name", help="catalog name, search term, or 'all'")
    sp.add_argument("--force", action="store_true",
                    help="overwrite an existing local entry")
    sp.add_argument("--yes", "-y", action="store_true",
                    help="don't prompt when a search term matches "
                         "multiple files")

    sp = sub.add_parser("push",
                        help="register an existing local .uhs file "
                             "into the local catalog")
    sp.add_argument("file", help="path to a local .uhs file")
    sp.add_argument("--name", help="catalog name (defaults to file basename)")
    sp.add_argument("--force", action="store_true",
                    help="overwrite an existing local entry")

    sp = sub.add_parser("notes",
                        help="open a markdown notes file for a game in $EDITOR. "
                             "Creates a template on first run.")
    sp.add_argument("name", help="game name / slug (e.g. memoria)")

    sp = sub.add_parser("compose",
                        help="convert a notes markdown file into a real .uhs "
                             "in the local catalog")
    sp.add_argument("name", help="game name / slug (must match a notes file)")
    sp.add_argument("--force", action="store_true",
                    help="overwrite an existing catalog entry")

    sp = sub.add_parser("export",
                        help="dump an existing catalog .uhs into editable "
                             "compose-grammar markdown")
    sp.add_argument("file", help="catalog name (with or without .uhs) "
                                 "or path to a .uhs file")
    sp.add_argument("dest", nargs="?",
                    help="output path (default: <catalog_dir>/notes/<slug>.md)")
    sp.add_argument("--force", action="store_true",
                    help="overwrite an existing destination file")

    return p


def _resolve_file(arg: str, cat: Catalog) -> str:
    """
    Resolve `arg` to a .uhs path. Accepts:
      - an existing filesystem path (`./alone.uhs`, `/path/to/x.uhs`),
      - a catalog name with extension (`alone.uhs`, case-insensitive),
      - a catalog name without extension (`alone`, `Alone`, case-insensitive).
    """
    if Path(arg).is_file():
        return arg
    candidates = [arg]
    if not arg.lower().endswith(".uhs"):
        candidates.append(arg + ".uhs")
    for cand in candidates:
        e = cat.get(cand)
        if e and Path(e.path).is_file():
            return e.path
    raise FileNotFoundError(arg)


def cmd_read(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    path = _resolve_file(args.file, cat)
    root, _ = parse_uhs(path, log)
    render(root, paint)


# ---------------------------------------------------------------------------
# Interactive `use` — actually USE a hint file: walk the tree, reveal
# hints one at a time, never spoil more than asked.
# ---------------------------------------------------------------------------

class UHSInteractive:
    """Stack-based interactive walker over a parsed UHS tree."""

    # Node types that act as navigable containers in the menu.
    NAVIGABLE = {"Subject", "Question", "HotSpot", "Sound"}

    # Node types the encoder can faithfully round-trip. Files containing
    # anything outside this set cannot be safely re-saved after an edit.
    ENCODER_SAFE = {
        "Root", "Subject", "Question", "Hint",
        "Comment", "CommentData", "Credit", "CreditData",
        "Info", "InfoData", "Incentive", "IncentiveData", "Link",
        "Text", "TextData",
        "HotSpot", "Image", "Overlay", "Sound", "SoundData",
        "Version", "VersionData", "Blank",
    }

    def __init__(self, root: "UHSNode", paint: "Paint",
                 state_path: Optional[str] = None,
                 state_key: Optional[str] = None,
                 source_path: Optional[str] = None):
        self.root = root
        self.paint = paint
        self.stack: List["UHSNode"] = [root]
        self.id_map: Dict[int, "UHSNode"] = {}
        self._index_ids(root)
        self.state_path = state_path
        self.state_key = state_key
        self.source_path = source_path
        self._resumed = self._restore_state()
        # Per-session temp dir for previewed binaries; cleaned at quit.
        self._tmp_dir: Optional[Path] = None

    # --- persistent location (resume across runs) ---

    def _load_state_blob(self) -> dict:
        if not self.state_path:
            return {}
        try:
            return json.loads(
                Path(self.state_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _restore_state(self) -> bool:
        if not (self.state_path and self.state_key):
            return False
        blob = self._load_state_blob()
        ids = blob.get(self.state_key, {}).get("stack", [])
        if not ids:
            return False
        nodes = [self.root]
        for nid in ids:
            n = self.id_map.get(nid)
            if not n:
                # Tree changed; bail to root rather than crash.
                return False
            nodes.append(n)
        self.stack = nodes
        return True

    def save_state(self) -> None:
        if not (self.state_path and self.state_key):
            return
        blob = self._load_state_blob()
        # Persist only the path below root; root is always implicit.
        ids = [n.id for n in self.stack[1:] if n.id != -1]
        if ids:
            blob[self.state_key] = {"stack": ids}
        else:
            blob.pop(self.state_key, None)
        try:
            p = Path(self.state_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(blob, indent=2, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(p)
        except OSError as e:
            print(self.paint(
                f"  warning: could not save resume state: {e}", C.WARN))

    def clear_state(self) -> None:
        if not (self.state_path and self.state_key):
            return
        blob = self._load_state_blob()
        if blob.pop(self.state_key, None) is not None:
            try:
                Path(self.state_path).write_text(
                    json.dumps(blob, indent=2, sort_keys=True),
                    encoding="utf-8")
            except OSError:
                pass

    # --- preview / play binary content (plan #2 §3) ---

    def _ensure_tmp_dir(self) -> Path:
        if self._tmp_dir is None:
            import tempfile
            self._tmp_dir = Path(tempfile.mkdtemp(prefix="my-uhs-"))
        return self._tmp_dir

    def _cleanup_tmp_dir(self) -> None:
        if self._tmp_dir is None:
            return
        try:
            for f in self._tmp_dir.iterdir():
                try: f.unlink()
                except OSError: pass
            self._tmp_dir.rmdir()
        except OSError:
            pass
        self._tmp_dir = None

    def _annotated_image_bytes(
            self, raw: bytes, zones: List[Tuple[int, int, int, int]],
            color: str = "#FF0000") -> Optional[bytes]:
        """Return PNG bytes with zone rectangles drawn on top. Requires
        Pillow (added to VENV_DEPS when zone-overlay lands as default).
        Falls back to None when Pillow isn't installed — callers then
        show the un-annotated image and print zone coords in the menu."""
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except ImportError:
            return None
        try:
            from io import BytesIO
            img = Image.open(BytesIO(raw)).convert("RGBA")
            draw = ImageDraw.Draw(img)
            for i, (x1, y1, x2, y2) in enumerate(zones, 1):
                draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
                draw.text((x1 + 2, y1 + 2), str(i), fill=color)
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def _preview_image(self, hotspot: "UHSNode") -> None:
        """Open the HotSpot's main image in macOS Preview / xdg-open.
        If zones are present and Pillow is installed, draw zone outlines
        first; else show un-annotated and list zones in the terminal."""
        img_node = next((c for c in hotspot.children
                         if c.type == "Image" and c.binary), None)
        if img_node is None:
            print(self.paint("  no extractable image bytes here.", C.WARN))
            return
        zones: List[Tuple[int, int, int, int]] = []
        zone_labels: List[str] = []
        for c in hotspot.children:
            if c.zone is not None:
                zones.append(c.zone)
                zone_labels.append(c.content or c.type)
        ext, _ = _detect_binary_kind(img_node.binary)
        body = img_node.binary
        if zones:
            annotated = self._annotated_image_bytes(body, zones)
            if annotated is not None:
                body = annotated
                ext = "png"
                print(self.paint(
                    f"  drew {len(zones)} zone rectangle(s) "
                    f"in red on a copy.", C.INFO))
            else:
                print(self.paint(
                    "  Pillow not installed — preview will show the "
                    "image WITHOUT zone overlays. Zones:", C.WARN))
                for i, (z, lbl) in enumerate(zip(zones, zone_labels), 1):
                    print(self.paint(
                        f"    {i}. ({z[0]},{z[1]})-({z[2]},{z[3]}) "
                        f"{lbl}", C.META))
        tmp_dir = self._ensure_tmp_dir()
        slug = (hotspot.content or "image").replace("/", "_")[:40]
        tmp = tmp_dir / f"{slug}.{ext}"
        tmp.write_bytes(body)
        if sys.platform == "darwin":
            os.system(f'open -a Preview "{tmp}" >/dev/null 2>&1')
        else:
            os.system(f'xdg-open "{tmp}" >/dev/null 2>&1 &')
        print(self.paint(f"  → opened {tmp}", C.OK))

    def _play_sound(self, sound: "UHSNode") -> None:
        sd = next((c for c in sound.children
                   if c.type == "SoundData" and c.binary), None)
        if sd is None:
            print(self.paint("  no extractable audio bytes here.", C.WARN))
            return
        ext, _ = _detect_binary_kind(sd.binary)
        tmp_dir = self._ensure_tmp_dir()
        slug = (sound.content or "sound").replace("/", "_")[:40]
        tmp = tmp_dir / f"{slug}.{ext}"
        tmp.write_bytes(sd.binary)
        if sys.platform == "darwin":
            # afplay blocks until done; run in background so the prompt
            # stays usable. (Could use 'afplay tmp &'.)
            os.system(f'afplay "{tmp}" >/dev/null 2>&1 &')
        else:
            os.system(f'aplay "{tmp}" >/dev/null 2>&1 &')
        print(self.paint(f"  → playing {tmp}", C.OK))

    # --- non-recursive in-place edit ---

    def _unsupported_types(self) -> List[str]:
        """Return sorted unique node types in the tree that the encoder
        cannot round-trip. Empty list = safe to re-save."""
        seen: set = set()
        def walk(n: "UHSNode") -> None:
            seen.add(n.type)
            for c in n.children:
                walk(c)
        walk(self.root)
        return sorted(seen - self.ENCODER_SAFE)

    def _editable_markdown(self, node: "UHSNode") -> str:
        """Render the THIS-LEVEL view of `node` for editing. Children's own
        sub-trees are NOT included — only their titles (for menus) or the
        Hint leaves directly under a Question."""
        title = self._label(node)
        if node.type == "Question":
            hints = self._hints(node)
            lines = [
                "# " + title,
                "",
                "# (Edit this question's title above and its hints below.",
                "#  Each '## Hint N' starts a new hint; text under it is",
                "#  the hint body. Add / remove '## Hint N' sections freely.",
                "#  Comment lines start with '#' and are ignored.)",
                "",
            ]
            if not hints:
                lines += ["## Hint 1", ""]
            else:
                for i, h in enumerate(hints, 1):
                    lines.append(f"## Hint {i}")
                    lines.append(h.content if h.kind == "string"
                                 else f"<{h.kind}>")
                    lines.append("")
            return "\n".join(lines).rstrip() + "\n"

        # Container (Root / Subject): title + ordered list of child titles.
        kids = self._navigable_children(node)
        lines = [
            "# " + title,
            "",
            "# (Edit this section's title above and the child titles below.",
            "#  ONE bullet per child. Reordering renames in place;",
            "#  the number of bullets must match the current child count.",
            "#  Comment lines start with '#' and are ignored.)",
            "",
        ]
        if not kids:
            lines.append("# (no children)")
        else:
            for c in kids:
                lines.append(f"- {c.content or '(untitled)'}")
        return "\n".join(lines) + "\n"

    def _parse_edited_markdown(
            self, text: str, node: "UHSNode"
    ) -> Tuple[Optional[str], Optional[List[str]], Optional[List[str]]]:
        """Parse the edited buffer. Returns (new_title, new_child_titles,
        new_hint_bodies). For containers only new_child_titles is set; for
        Questions only new_hint_bodies. Raises ValueError on a structural
        mismatch."""
        raw_lines = text.splitlines()
        # Title: first '# ' line that is NOT a '## ' subsection header.
        new_title: Optional[str] = None
        for ln in raw_lines:
            s = ln.strip()
            if s.startswith("# ") and not s.startswith("## "):
                new_title = s[2:].strip()
                break
            if s == "#":
                new_title = ""
                break
        if new_title is None:
            raise ValueError("missing title line ('# Title')")

        if node.type == "Question":
            # Split into '## Hint N' sections.
            hints: List[List[str]] = []
            current: Optional[List[str]] = None
            for ln in raw_lines:
                s = ln.rstrip("\n")
                if s.lstrip().startswith("## "):
                    current = []
                    hints.append(current)
                    continue
                if current is None:
                    continue
                # Skip in-section full-line '#' comments.
                if s.lstrip().startswith("#") and not s.lstrip().startswith("##"):
                    continue
                current.append(s)
            bodies = []
            for h in hints:
                while h and h[0].strip() == "":
                    h.pop(0)
                while h and h[-1].strip() == "":
                    h.pop()
                bodies.append("\n".join(h))
            bodies = [b for b in bodies if b != ""]
            return new_title, None, bodies

        # Container: collect bullet items.
        bullets: List[str] = []
        for ln in raw_lines:
            s = ln.lstrip()
            if s.startswith("- "):
                bullets.append(s[2:].rstrip())
            elif s.startswith("-") and s.rstrip() == "-":
                bullets.append("")
        kids = self._navigable_children(node)
        if len(bullets) != len(kids):
            raise ValueError(
                f"child count mismatch: file has {len(kids)} children "
                f"but you provided {len(bullets)} bullets. "
                f"Add/remove children outside `use`; this edit is rename-only.")
        return new_title, bullets, None

    def _apply_edit(self, node: "UHSNode",
                    new_title: str,
                    new_child_titles: Optional[List[str]],
                    new_hint_bodies: Optional[List[str]]) -> None:
        # Update the node's own title (skip for Root — its content is the
        # master file title; changing it changes the encryption key, which
        # we don't want to do casually).
        if node.type != "Root":
            node.content = new_title
        elif new_title and new_title != self._label(node):
            print(self.paint(
                "  note: root/file title is the encryption key — "
                "not changed.", C.WARN))

        if new_child_titles is not None:
            kids = self._navigable_children(node)
            for child, t in zip(kids, new_child_titles):
                child.content = t
        if new_hint_bodies is not None:
            # Replace ONLY the Hint children; preserve any other (e.g.
            # nested Questions) in their original order around them.
            new_children: List["UHSNode"] = []
            replaced_hints = False
            for c in node.children:
                if c.type == "Hint":
                    if not replaced_hints:
                        for body in new_hint_bodies:
                            new_children.append(
                                UHSNode(type="Hint", content=body))
                        replaced_hints = True
                    # drop original Hint children
                else:
                    new_children.append(c)
            if not replaced_hints:
                # No prior Hints — append the new ones at the end.
                for body in new_hint_bodies:
                    new_children.append(
                        UHSNode(type="Hint", content=body))
            node.children = new_children

    def _persist_to_disk(self) -> bool:
        if not self.source_path:
            print(self.paint(
                "  in-memory only: no source path bound.", C.WARN))
            return False
        bad = self._unsupported_types()
        if bad:
            print(self.paint(
                f"  cannot re-save .uhs — file contains node types the "
                f"encoder does not support: {', '.join(bad)}.\n"
                f"  Edits kept in this session only.", C.WARN))
            return False
        try:
            master = self._label(self.root)
            data = encode_uhs(self.root, master_title=master)
            tmp = Path(self.source_path + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(self.source_path)
        except Exception as e:
            print(self.paint(
                f"  failed to write {self.source_path}: {e}", C.WARN))
            return False
        print(self.paint(f"  saved → {self.source_path}", C.OK))
        return True

    def _edit_current(self) -> None:
        node = self.stack[-1]
        editor = (os.environ.get("VISUAL")
                  or os.environ.get("EDITOR")
                  or "vi")
        import tempfile
        suffix = ".question.md" if node.type == "Question" else ".section.md"
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, delete=False, encoding="utf-8") as tf:
            tf.write(self._editable_markdown(node))
            tmp_path = tf.name
        rc = os.system(f'{editor} "{tmp_path}"')
        if rc != 0:
            print(self.paint(
                f"  editor exited with status {rc}; no changes.", C.WARN))
            try: os.unlink(tmp_path)
            except OSError: pass
            return
        try:
            edited = Path(tmp_path).read_text(encoding="utf-8")
        finally:
            try: os.unlink(tmp_path)
            except OSError: pass
        try:
            new_title, new_titles, new_bodies = \
                self._parse_edited_markdown(edited, node)
        except ValueError as e:
            print(self.paint(f"  edit rejected: {e}", C.WARN))
            return
        self._apply_edit(node, new_title, new_titles, new_bodies)
        self._persist_to_disk()

    def _index_ids(self, node: "UHSNode") -> None:
        if node.id != -1:
            self.id_map[node.id] = node
        for c in node.children:
            self._index_ids(c)

    def _label(self, n: "UHSNode") -> str:
        if n.type == "Root":
            return n.content if n.content and n.content != "root" else "(root)"
        return n.content or n.type

    def _breadcrumb(self) -> str:
        return " › ".join(self._label(n) for n in self.stack)

    def _navigable_children(self, node: "UHSNode") -> List["UHSNode"]:
        return [c for c in node.children if c.type in self.NAVIGABLE]

    def _hints(self, node: "UHSNode") -> List["UHSNode"]:
        return [c for c in node.children if c.type == "Hint"]

    def _follow_link_if_any(self, node: "UHSNode") -> "UHSNode":
        if node.is_link and node.link_target in self.id_map:
            return self.id_map[node.link_target]
        return node

    def _ask(self, prompt: str) -> Optional[str]:
        try:
            return input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    # --- menu (Root / Subject / Question with nav children) ---

    def _show_menu(self, node: "UHSNode") -> bool:
        kids = self._navigable_children(node)
        print()
        print(self.paint(self._breadcrumb(), C.META))
        print(self.paint(self._label(node), C.TITLE))
        if not kids:
            print(self.paint("  (no chapters or questions here)", C.WARN))
        else:
            for i, c in enumerate(kids, 1):
                if c.type == "Question":
                    marker, color = "?", C.QUESTION
                elif c.type == "HotSpot":
                    marker, color = "📷", C.LINK
                elif c.type == "Sound":
                    marker, color = "🔊", C.LINK
                else:
                    marker, color = "/", C.SUBJECT
                label = c.content or "(untitled)"
                tail = ""
                if c.is_link:
                    tail = self.paint(f"  → link {c.link_target}", C.META)
                print(f"  {i:>3}. {marker} {self.paint(label, color)}{tail}")
        # `p=preview` only meaningful when this node IS or CONTAINS a
        # binary node we can preview/play. Show conditionally.
        is_binary_node = node.type in ("HotSpot", "Sound")
        prompt_extra = "  p=preview" if is_binary_node else ""
        ans = self._ask(self.paint(
            f"  [number]=open  e=edit{prompt_extra}  "
            f"b=back  c=chapters  q=quit > ",
            C.HINT))
        if ans is None or ans in ("q", "quit", "exit"):
            return False
        if ans in ("c", "chapters", "home", "/"):
            self.stack = [self.root]
            return True
        if ans in ("b", "back", "u", "up", ".."):
            if len(self.stack) > 1:
                self.stack.pop()
            else:
                print(self.paint("  already at the top", C.WARN))
            return True
        if ans in ("e", "edit"):
            self._edit_current()
            return True
        if ans in ("p", "preview", "play") and is_binary_node:
            if node.type == "HotSpot":
                self._preview_image(node)
            else:
                self._play_sound(node)
            return True
        if ans == "":
            return True
        if ans.isdigit():
            i = int(ans) - 1
            if 0 <= i < len(kids):
                self.stack.append(self._follow_link_if_any(kids[i]))
            else:
                print(self.paint(f"  out of range: {ans}", C.WARN))
            return True
        print(self.paint(f"  unknown command: {ans}", C.WARN))
        return True

    # --- hint reveal (Question with Hint leaves) ---

    def _show_hints(self, q: "UHSNode") -> bool:
        hints = self._hints(q)
        nested = self._navigable_children(q)
        print()
        print(self.paint(self._breadcrumb(), C.META))
        print(self.paint("? " + self._label(q), C.QUESTION))
        if not hints and not nested:
            print(self.paint("  (no hints here)", C.WARN))
            ans = self._ask(self.paint(
                "  b=back  c=chapters  q=quit > ", C.HINT))
            return self._handle_nav_only(ans)

        revealed = 0
        total = len(hints)
        while revealed < total:
            remaining = total - revealed
            prompt = self.paint(
                f"  [enter]=reveal hint {revealed+1}/{total}  "
                f"a=all ({remaining})  l=last only  e=edit  "
                f"b=back  c=chapters  q=quit > ", C.HINT)
            ans = self._ask(prompt)
            if ans is None or ans in ("q", "quit", "exit"):
                return False
            if ans in ("c", "chapters", "home", "/"):
                self.stack = [self.root]
                return True
            if ans in ("b", "back", "u", "up", ".."):
                if len(self.stack) > 1:
                    self.stack.pop()
                return True
            if ans in ("e", "edit"):
                self._edit_current()
                # Reload hint list since the node may have changed.
                hints = self._hints(q)
                total = len(hints)
                revealed = min(revealed, total)
                continue
            if ans == "":
                self._print_hint(revealed + 1, hints[revealed])
                revealed += 1
                continue
            if ans in ("a", "all"):
                while revealed < total:
                    self._print_hint(revealed + 1, hints[revealed])
                    revealed += 1
                break
            if ans in ("l", "last"):
                self._print_hint(total, hints[-1],
                                 note=" (jumped to last)")
                revealed = total
                break
            print(self.paint(f"  unknown command: {ans}", C.WARN))

        # Hints exhausted (or none). Offer nested questions if any, else nav.
        if nested:
            return self._show_menu(q)
        print(self.paint(f"— end of hints ({total}/{total}) —", C.META))
        ans = self._ask(self.paint(
            "  b=back  c=chapters  q=quit > ", C.HINT))
        return self._handle_nav_only(ans)

    def _print_hint(self, n: int, hint: "UHSNode", note: str = "") -> None:
        body = hint.content if hint.kind == "string" else f"^{hint.kind.upper()}^"
        print(f"  {n}. {self.paint(body, C.HINT)}"
              f"{self.paint(note, C.META) if note else ''}")

    def _handle_nav_only(self, ans: Optional[str]) -> bool:
        if ans is None or ans in ("q", "quit", "exit"):
            return False
        if ans in ("c", "chapters", "home", "/"):
            self.stack = [self.root]
        elif ans in ("b", "back", "u", "up", "..", ""):
            if len(self.stack) > 1:
                self.stack.pop()
        else:
            print(self.paint(f"  unknown command: {ans}", C.WARN))
        return True

    # --- main loop ---

    def run(self) -> None:
        title = self._label(self.root)
        print(self.paint(f"=== {title} ===", C.TITLE))
        if self._resumed:
            print(self.paint(
                f"(resumed at: {self._breadcrumb()} — `c` for chapters)",
                C.INFO))
        else:
            print(self.paint(
                "Interactive hint mode. Reveal hints one at a time.",
                C.INFO))
        try:
            while self.stack:
                cur = self.stack[-1]
                if cur.type == "Question":
                    ok = self._show_hints(cur)
                else:
                    ok = self._show_menu(cur)
                if not ok:
                    break
        finally:
            self.save_state()
            self._cleanup_tmp_dir()
            if self.state_path and self.state_key:
                if len(self.stack) > 1:
                    print(self.paint(
                        f"saved location → resume with: my-uhs use "
                        f"{Path(self.state_key).stem}", C.META))
                else:
                    print(self.paint(
                        "no saved location (back at chapters).", C.META))
        print(self.paint("bye.", C.META))


def cmd_use(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    path = _resolve_file(args.file, cat)
    root, _ = parse_uhs(path, log)
    state_path = str(Path(cfg["catalog_dir"]) / "use-state.json")
    state_key = os.path.realpath(path)
    UHSInteractive(root, paint,
                   state_path=state_path,
                   state_key=state_key,
                   source_path=path).run()


def cmd_title(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    path = _resolve_file(args.file, cat)
    root, _ = parse_uhs(path, log)
    t = hint_title(root)
    print(f"Title: {t}" if t else "Title: (none)")


def cmd_version(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    path = _resolve_file(args.file, cat)
    root, fmt = parse_uhs(path, log)
    v = hint_version(root) or fmt
    print(f"Version: {v}")


def cmd_test(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    path = _resolve_file(args.file, cat)
    try:
        parse_uhs(path, log)
    except (UHSParseError, OSError) as e:
        print(f"Test: Parsing FAILED ({e})")
        return 2
    print("Test: Parsing succeeded")
    return 0


def cmd_list(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load()
    entries = cat.list()
    if args.search:
        term = args.search.lower()
        entries = [e for e in entries
                   if term in e.title.lower() or term in e.name.lower()]
    if not entries:
        if args.search:
            print(f"(no local catalog entry matches {args.search!r})")
        else:
            print("(catalog is empty — try `my-uhs pull alone.uhs` "
                  "or `my-uhs push <file>`)")
        return 0
    name_w = max((len(e.name) for e in entries), default=4)
    ver_w  = max((len(e.version) for e in entries), default=3)
    for e in entries:
        size_kb = f"{e.size/1024:.1f}K"
        line = (f"{paint(e.name.ljust(name_w), C.LINK)}  "
                f"{paint(e.version.ljust(ver_w), C.INFO)}  "
                f"{paint(size_kb.rjust(8), C.META)}  "
                f"{paint(e.title, C.SUBJECT)}")
        print(line)
    return 0


def cmd_catalog(args, cfg, log, paint):
    try:
        remote = fetch_remote_catalog(cfg, log)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"my-uhs: catalog fetch failed: {e}", file=sys.stderr)
        return 2
    if args.search:
        term = args.search.lower()
        remote = [r for r in remote if term in r.title.lower()]
    print(f"# {len(remote)} entries")
    for r in remote:
        print(f"{paint(r.name.ljust(28), C.LINK)}  "
              f"{paint(r.date.ljust(10), C.META)}  "
              f"{paint(f'{r.csize/1024:6.1f}K', C.META)}  "
              f"{paint(r.title, C.SUBJECT)}")
    return 0


def cmd_pull(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load(); cat.ensure_dirs()
    try:
        remote = fetch_remote_catalog(cfg, log)
    except Exception as e:
        print(f"my-uhs: catalog fetch failed: {e}", file=sys.stderr)
        return 2

    targets: List[RemoteEntry] = []
    if args.name == "all":
        targets = remote
    else:
        # 1) exact filename match wins outright
        by_name = {r.name.lower(): r for r in remote}
        exact = by_name.get(args.name.lower())
        if exact:
            targets.append(exact)
        else:
            # 2) substring search across both filename and title
            term = args.name.lower()
            matches = [r for r in remote
                       if term in r.name.lower() or term in r.title.lower()]
            if not matches:
                print(f"my-uhs: nothing in remote catalog matches "
                      f"{args.name!r}", file=sys.stderr)
                return 1
            if len(matches) == 1 or args.yes:
                targets = matches
            else:
                # Disambiguate interactively (or show and bail if non-tty)
                print(f"# {len(matches)} matches for {args.name!r}:")
                for i, r in enumerate(matches, 1):
                    print(f"  [{i}] {paint(r.name.ljust(28), C.LINK)}  "
                          f"{paint(r.title, C.SUBJECT)}")
                if not sys.stdin.isatty():
                    print("(re-run with an exact filename, "
                          "or pass --yes to pull all matches)",
                          file=sys.stderr)
                    return 1
                try:
                    pick = input("Pick number (or 'a' for all, "
                                 "Enter to cancel): ").strip().lower()
                except EOFError:
                    return 1
                if not pick:
                    return 0
                if pick == "a":
                    targets = matches
                else:
                    try:
                        targets = [matches[int(pick) - 1]]
                    except (ValueError, IndexError):
                        print("my-uhs: invalid selection", file=sys.stderr)
                        return 1

    ok = skipped = failed = 0
    try:
        for r in targets:
            if not args.force and cat.get(r.name):
                print(paint(f"skip  {r.name} (already in catalog; --force to overwrite)",
                            C.META))
                skipped += 1
                continue
            try:
                path = fetch_and_extract_uhs(r, cfg, cat.files, log)
            except Exception as e:
                print(paint(f"fail  {r.name}: {e}", C.WARN), file=sys.stderr)
                failed += 1
                continue
            try:
                root, fmt = parse_uhs(str(path), log)
                ver = hint_version(root) or fmt
                title = hint_title(root) or r.title
            except UHSParseError as e:
                print(paint(f"warn  {r.name}: parse error: {e}", C.WARN),
                      file=sys.stderr)
                ver, title = "?", r.title
            cat.add(CatalogEntry(
                name=r.name.lower(), title=title, version=ver,
                path=str(path), size=path.stat().st_size,
                source="pull", fetched_at=time.time(), remote_url=r.url))
            print(paint(f"ok    {r.name}  ({ver})  {title}", C.OK))
            ok += 1
    finally:
        # Always persist whatever progress we made, even on Ctrl-C.
        cat.save()
    if len(targets) > 1:
        print(paint(f"# {ok} added, {skipped} skipped, {failed} failed", C.META))
    return 0 if failed == 0 else 2


def cmd_push(args, cfg, log, paint):
    cat = Catalog(cfg["catalog_dir"], log); cat.load(); cat.ensure_dirs()
    src = Path(args.file)
    if not src.is_file():
        print(f"my-uhs: not a file: {src}", file=sys.stderr); return 1
    name = (args.name or src.name).lower()
    if not name.endswith(".uhs"):
        name += ".uhs"
    if not args.force and cat.get(name):
        print(f"my-uhs: already in catalog: {name} (use --force)",
              file=sys.stderr)
        return 1
    try:
        root, fmt = parse_uhs(str(src), log)
    except UHSParseError as e:
        print(f"my-uhs: invalid UHS file: {e}", file=sys.stderr); return 2
    ver = hint_version(root) or fmt
    title = hint_title(root) or src.stem
    dst = cat.files / name
    dst.write_bytes(src.read_bytes())
    cat.add(CatalogEntry(
        name=name, title=title, version=ver, path=str(dst),
        size=dst.stat().st_size, source="push", fetched_at=time.time()))
    cat.save()
    print(paint(f"added {name}  ({ver})  {title}", C.OK))
    return 0


# ---------------------------------------------------------------------------
# Zsh completions — install/update on every run; emit with --complete TOPIC.
# Same shape as my-plex's pattern.
# ---------------------------------------------------------------------------

def _emit_completions(topic: str, cfg: Dict[str, str]) -> int:
    """Emit one item per line for the requested completion topic."""
    if topic in ("names", "files", "titles"):
        cat = Catalog(cfg["catalog_dir"], logging.getLogger("my-uhs"))
        cat.load()
        for entry in cat.list():
            if topic == "files":
                print(entry.name)
            elif topic == "names":
                print(Path(entry.name).stem)
            else:  # titles
                print(entry.title)
        return 0
    print(f"my-uhs: unknown --complete topic: {topic}", file=sys.stderr)
    return 2


_ZSH_COMPLETION_SCRIPT = r'''#compdef my-uhs

_my-uhs() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    local -a global_opts
    global_opts=(
        '(-c --config)'{-c,--config}'[print current config / use config file]:config file:_files'
        '--create-config[print default config / write to PATH]:path:_files'
        '(-D --debug)'{-D,--debug}'[enable debug logging]'
        '--no-color[disable ANSI color]'
        '--complete[machine-readable completions for TOPIC]:topic:(names files titles)'
        '--version[print version and exit]'
    )

    local -a subcommands
    subcommands=(
        'read:render a .uhs (colorized)'
        'use:interactive hint mode (no spoilers)'
        'title:print the file title'
        'version:print the declared version'
        'test:parse and report success/failure'
        'list:list local catalog entries'
        'catalog:refresh cached remote catalog'
        'pull:download from remote catalog'
        'push:register a local .uhs into the catalog'
        'notes:open markdown notes file in $EDITOR'
        'compose:turn notes markdown into a .uhs'
        'export:dump a .uhs to compose-grammar markdown'
    )

    _arguments -C \
        $global_opts \
        '1: :->cmd' \
        '*::arg:->args'

    case $state in
        cmd)
            _describe 'subcommand' subcommands
            ;;
        args)
            case $words[1] in
                read|use|title|version|test|export)
                    local -a names
                    names=( ${(f)"$(my-uhs --complete names 2>/dev/null)"} )
                    if (( ${#names} > 0 )); then
                        _describe 'catalog name' names
                    else
                        _files -g '*.uhs'
                    fi
                    ;;
                push)
                    _arguments \
                        '--name[catalog name override]:name:' \
                        '--force[overwrite existing entry]' \
                        '*:.uhs file:_files -g "*.uhs"'
                    ;;
                pull)
                    _arguments \
                        '--force[overwrite existing local entry]' \
                        '(-y --yes)'{-y,--yes}'[no prompt on multi-match]' \
                        '*::name or "all":'
                    ;;
                list)
                    _arguments '--search[filter by substring]:term:'
                    ;;
                catalog)
                    _arguments '--search[filter by substring]:term:'
                    ;;
                compose)
                    local -a names
                    names=( ${(f)"$(my-uhs --complete names 2>/dev/null)"} )
                    _arguments \
                        '--force[overwrite existing entry]' \
                        "*:slug:($names)"
                    ;;
                notes)
                    local -a names
                    names=( ${(f)"$(my-uhs --complete names 2>/dev/null)"} )
                    if (( ${#names} > 0 )); then
                        _describe 'name / slug' names
                    fi
                    ;;
                export)
                    _arguments \
                        '--force[overwrite existing destination]' \
                        '1:catalog name or .uhs path:_files -g "*.uhs"' \
                        '2:dest .md path:_files -g "*.md"'
                    ;;
            esac
            ;;
    esac
}

_my-uhs "$@"
'''


def _install_zsh_completions() -> None:
    """Drop the zsh completion file and (one-time) patch ~/.zshrc to add
    ~/.zsh/completions to fpath. No-op when the on-disk file already
    matches the embedded script."""
    completion_dir = os.path.expanduser("~/.zsh/completions")
    completion_file = os.path.join(completion_dir, "_my-uhs")
    try:
        os.makedirs(completion_dir, exist_ok=True)
        try:
            with open(completion_file, "r", encoding="utf-8") as f:
                if f.read() == _ZSH_COMPLETION_SCRIPT:
                    return
        except FileNotFoundError:
            pass
        with open(completion_file, "w", encoding="utf-8") as f:
            f.write(_ZSH_COMPLETION_SCRIPT)
    except OSError:
        return  # silently skip — not all environments have a writable HOME

    # Patch ~/.zshrc to put completions on fpath, once.
    zshrc = os.path.expanduser("~/.zshrc")
    zshrc_real = os.path.realpath(zshrc) if os.path.islink(zshrc) else zshrc
    if not os.path.isfile(zshrc_real):
        return
    try:
        with open(zshrc_real, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return
    if ".zsh/completions" in content:
        return
    try:
        if "oh-my-zsh.sh" in content:
            patched = re.sub(
                r"(source.*oh-my-zsh\.sh)",
                f"# my-uhs zsh completions\n"
                f"fpath=({completion_dir} $fpath)\n\\1",
                content, count=1)
            with open(zshrc_real, "w", encoding="utf-8") as f:
                f.write(patched)
        else:
            with open(zshrc_real, "a", encoding="utf-8") as f:
                f.write(
                    f"\n# my-uhs zsh completions\n"
                    f"fpath=({completion_dir} $fpath)\n"
                    f"autoload -Uz compinit && compinit\n")
    except OSError:
        return


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    _install_zsh_completions()
    p = _argparser()
    args = p.parse_args(argv)

    # --create-config: print to stdout, or write to PATH, then exit.
    if args.create_config is not None:
        if args.create_config == "__STDOUT__":
            sys.stdout.write(DEFAULT_CONFIG_TEXT)
            return 0
        try:
            path = create_config(args.create_config)
        except Exception as e:
            print(f"my-uhs: --create-config failed: {e}", file=sys.stderr)
            return 2
        print(f"my-uhs: created {path}")
        return 0

    # --config without PATH: print current effective config and exit.
    show_config = args.config == "__SHOW__"
    config_path = None if show_config else args.config

    cfg, used = load_config(config_path)
    if show_config:
        sys.stdout.write(render_effective_config(cfg, used))
        return 0

    # --complete TOPIC: machine-readable lines for the zsh completion.
    if args.complete:
        return _emit_completions(args.complete, cfg)
    log = setup_logging(args.debug, cfg["logfile"])
    if used:
        log.debug("config loaded from %s", used)
    else:
        log.debug("no config file found; using built-in defaults")

    paint = Paint(colors_on(cfg["color"], args.no_color))

    if not args.cmd:
        p.print_help(); return 0

    handlers = {
        "read":    cmd_read,
        "use":     cmd_use,
        "title":   cmd_title,
        "version": cmd_version,
        "test":    cmd_test,
        "list":    cmd_list,
        "catalog": cmd_catalog,
        "pull":    cmd_pull,
        "push":    cmd_push,
        "notes":   cmd_notes,
        "compose": cmd_compose,
        "export":  cmd_export,
    }
    try:
        rc = handlers[args.cmd](args, cfg, log, paint)
        return rc if isinstance(rc, int) else 0
    except FileNotFoundError as e:
        print(f"my-uhs: not found: {e}", file=sys.stderr)
        return 1
    except UHSParseError as e:
        print(f"my-uhs: parse error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr); return 130


if __name__ == "__main__":
    sys.exit(main())
