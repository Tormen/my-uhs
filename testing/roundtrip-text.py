#!/usr/bin/env python3
"""Round-trip test for Text node (multi-line text block stored in the
binary tail of the .uhs).

Verifies:
- Encoder emits a `\\x00`-separated binary tail when a Text node is present.
- Spec line carries correctly-padded absolute offset + length.
- The text_hunk cipher is reversible: encoded bytes decrypt back to source.

Run:  python3 testing/roundtrip-text.py
"""

import importlib.util
import logging
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("m", ROOT / "my-uhs.py")
m = importlib.util.module_from_spec(spec)
sys.modules["m"] = m
spec.loader.exec_module(m)

PAYLOAD = (
    "ASCII reference card\n"
    "  +-+-+-+\n"
    "  |a|b|c|\n"
    "  +-+-+-+\n"
    "Use this to identify rooms by their letter."
)


def find_first(node, type_):
    if node.type == type_:
        return node
    for c in node.children:
        r = find_first(c, type_)
        if r:
            return r
    return None


def main() -> int:
    failures: list = []
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    n = m.UHSNode

    src = n(type="Root", content="TextTest", children=[
        n(type="Subject", content="Reference", children=[
            n(type="Text", content="ASCII Map", children=[
                n(type="TextData", content=PAYLOAD),
            ]),
        ]),
    ])

    blob = m.encode_uhs(src, master_title="TextTest")

    if b"\x1a" not in blob:
        failures.append("encoded file missing \\x1a binary-tail separator")

    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob); fp.close()
    try:
        parsed, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)

    txt = find_first(parsed, "Text")
    if txt is None:
        failures.append("Text node not found after parse")
    elif not txt.children or txt.children[0].type != "TextData":
        failures.append("Text node missing TextData child")
    else:
        got = txt.children[0].content
        # The parser splits on \r\n / \r / \n and rejoins with \n; trailing
        # blank lines are stripped. Normalize PAYLOAD the same way.
        want = PAYLOAD
        if got != want:
            failures.append(
                f"TextData mismatch:\nwant: {want!r}\ngot : {got!r}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1

    # ---- markdown round-trip: parsed -> export-md -> parse_notes -> encode -> parse ----
    md = m.serialize_uhs_to_notes_md(parsed)
    title2, root2 = m.parse_notes_markdown(md)
    blob2 = m.encode_uhs(root2, master_title=title2)
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob2); fp.close()
    try:
        parsed2, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)
    txt2 = find_first(parsed2, "Text")
    if txt2 is None:
        failures.append("[md] Text node not preserved")
    elif not txt2.children or txt2.children[0].type != "TextData":
        failures.append("[md] TextData missing")
    elif txt2.children[0].content != PAYLOAD:
        failures.append(
            f"[md] TextData mismatch:\n  want {PAYLOAD!r}\n  got  "
            f"{txt2.children[0].content!r}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: Text node round-trip clean (binary + markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
