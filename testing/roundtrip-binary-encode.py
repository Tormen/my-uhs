#!/usr/bin/env python3
"""Phase 4 verification: encode_uhs writes HotSpot and Sound binary data
into the .uhs binary tail; parser reads it back identically.

Two cases:
1. Synth tree with a tiny PNG (hand-built minimal valid PNG) and a tiny
   WAV. Encode → parse → assert binary bytes match.
2. Round-trip the anach.uhs sample if present: parse → re-encode →
   re-parse, assert HotSpot main image bytes match exactly.

Run:  python3 testing/roundtrip-binary-encode.py
"""

import importlib.util
import logging
import struct
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("m", ROOT / "my-uhs.py")
m = importlib.util.module_from_spec(spec)
sys.modules["m"] = m
spec.loader.exec_module(m)


def find_first(node, type_):
    if node.type == type_:
        return node
    for c in node.children:
        r = find_first(c, type_)
        if r:
            return r
    return None


def build_min_png() -> bytes:
    """Build a 1x1 transparent PNG by hand."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw = b"\x00" + b"\x00\x00\x00\x00"
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def build_min_wav() -> bytes:
    """Build a minimal silent 1-sample 8kHz mono PCM WAV."""
    pcm = b"\x80"
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)
    data_chunk = b"data" + struct.pack("<I", len(pcm)) + pcm
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def main() -> int:
    failures: list = []
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    n = m.UHSNode

    PNG = build_min_png()
    WAV = build_min_wav()

    src = n(type="Root", content="BinTest", children=[
        n(type="Subject", content="Chapter", children=[
            n(type="HotSpot", content="A picture", children=[
                n(type="Image", content="^IMAGE^", kind="image",
                  binary=PNG),
            ]),
            n(type="Sound", content="A sound", children=[
                n(type="SoundData", content="^AUDIO^", kind="audio",
                  binary=WAV),
            ]),
        ]),
    ])

    blob = m.encode_uhs(src, master_title="BinTest")
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob); fp.close()
    try:
        parsed, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)

    img = find_first(parsed, "Image")
    if img is None or not img.binary:
        failures.append("Image not parsed back")
    elif img.binary != PNG:
        failures.append(
            f"PNG bytes mismatch: want {len(PNG)}, got "
            f"{len(img.binary or b'')}")

    snd = find_first(parsed, "SoundData")
    if snd is None or not snd.binary:
        failures.append("SoundData not parsed back")
    elif snd.binary != WAV:
        failures.append(
            f"WAV bytes mismatch: want {len(WAV)}, got "
            f"{len(snd.binary or b'')}")

    # Real-sample round-trip if anach is on disk.
    anach = HERE / "samples" / "anach.uhs"
    if anach.is_file():
        parsed_a, _ = m.parse_uhs(str(anach), log)
        # Re-encode and parse back.
        blob_a = m.encode_uhs(parsed_a, master_title=parsed_a.content)
        fp = tempfile.NamedTemporaryFile(
            suffix=".uhs", delete=False, dir=str(HERE))
        fp.write(blob_a); fp.close()
        try:
            re_a, _ = m.parse_uhs(fp.name, log)
        finally:
            Path(fp.name).unlink(missing_ok=True)
        orig = find_first(parsed_a, "Image")
        re_img = find_first(re_a, "Image")
        if orig and orig.binary and re_img and re_img.binary:
            if orig.binary != re_img.binary:
                failures.append(
                    f"anach round-trip: image bytes differ "
                    f"({len(orig.binary)} vs {len(re_img.binary)})")
            else:
                print(f"  anach: re-encoded {len(orig.binary)}-byte "
                      f"PNG round-trips byte-exact ✓")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: HotSpot + Sound binary-tail encoder round-trips clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
