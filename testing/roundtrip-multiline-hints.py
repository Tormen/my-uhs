#!/usr/bin/env python3
"""Round-trip test: multi-line hint content survives encode→parse and the
markdown export→parse→encode→parse path.

Run:  python3 testing/roundtrip-multiline-hints.py
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

MULTI = "first line\nsecond line\nthird line"
SINGLE = "single hint, no newlines"


def build_tree() -> "m.UHSNode":
    n = m.UHSNode
    return n(type="Root", content="MLHints", children=[
        n(type="Subject", content="Chapter", children=[
            n(type="Question", content="Q?", children=[
                n(type="Hint", content=MULTI),
                n(type="Hint", content=SINGLE),
                n(type="Hint", content="another\nmulti-line"),
            ]),
        ]),
    ])


def find(node, type_):
    if node.type == type_:
        yield node
    for c in node.children:
        yield from find(c, type_)


def encode_then_parse(root, master):
    blob = m.encode_uhs(root, master_title=master)
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob); fp.close()
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    try:
        parsed, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)
    return parsed


def main() -> int:
    failures: list = []

    src = build_tree()

    # 1. binary round-trip
    parsed = encode_then_parse(src, "MLHints")
    hints = list(find(parsed, "Hint"))
    expected = [MULTI, SINGLE, "another\nmulti-line"]
    if len(hints) != 3:
        failures.append(f"[binary] expected 3 hints, got {len(hints)}")
    else:
        for i, (h, want) in enumerate(zip(hints, expected)):
            if h.content != want:
                failures.append(
                    f"[binary] hint {i}: want {want!r} got {h.content!r}")

    # 2. markdown round-trip
    md = m.serialize_uhs_to_notes_md(parsed)
    title2, root2 = m.parse_notes_markdown(md)
    parsed2 = encode_then_parse(root2, title2)
    hints2 = list(find(parsed2, "Hint"))
    if len(hints2) != 3:
        failures.append(f"[md] expected 3 hints, got {len(hints2)}")
    else:
        for i, (h, want) in enumerate(zip(hints2, expected)):
            if h.content != want:
                failures.append(
                    f"[md] hint {i}: want {want!r} got {h.content!r}")

    if failures:
        print("FAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("OK: multi-line hint round-trip clean (binary + markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
