#!/usr/bin/env python3
"""Round-trip a nesthint Question — Question with mixed children
(Hint + nested non-Hint such as Text or Link).

Run:  python3 testing/roundtrip-nesthint.py
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

NESTED_TEXT = (
    "ASCII reference inside a question:\n"
    "  +---+\n"
    "  | A |\n"
    "  +---+"
)


def find_all(node, type_):
    if node.type == type_:
        yield node
    for c in node.children:
        yield from find_all(c, type_)


def main() -> int:
    failures: list = []
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    n = m.UHSNode

    src = n(type="Root", content="NestHintTest", children=[
        n(type="Subject", content="Chapter", children=[
            n(type="Question", content="Q with nested Text?", children=[
                n(type="Hint", content="first hint"),
                n(type="Hint", content="second hint"),
                n(type="Text", content="ASCII map", children=[
                    n(type="TextData", content=NESTED_TEXT),
                ]),
                n(type="Hint", content="third hint after the diagram"),
            ]),
            n(type="Question", content="Plain Q?", children=[
                n(type="Hint", content="just a hint"),
            ]),
        ]),
    ])

    blob = m.encode_uhs(src, master_title="NestHintTest")
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob); fp.close()
    try:
        parsed, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)

    qs = list(find_all(parsed, "Question"))
    if len(qs) != 2:
        failures.append(f"expected 2 Questions, got {len(qs)}")
    else:
        # First Q should have 3 Hints + 1 Text child.
        q1_hints = [c for c in qs[0].children if c.type == "Hint"]
        q1_texts = [c for c in qs[0].children if c.type == "Text"]
        if len(q1_hints) != 3:
            failures.append(
                f"Q1: expected 3 Hints, got {len(q1_hints)}: "
                f"{[h.content for h in q1_hints]}")
        if len(q1_texts) != 1:
            failures.append(
                f"Q1: expected 1 nested Text, got {len(q1_texts)}")
        elif (not q1_texts[0].children
              or q1_texts[0].children[0].type != "TextData"):
            failures.append("Q1: nested Text missing TextData")
        elif q1_texts[0].children[0].content != NESTED_TEXT:
            failures.append(
                f"Q1: nested Text content mismatch:\n"
                f"  want {NESTED_TEXT!r}\n"
                f"  got  {q1_texts[0].children[0].content!r}")

        # Second Q is plain hint, should still work.
        q2_hints = [c for c in qs[1].children if c.type == "Hint"]
        if len(q2_hints) != 1 or q2_hints[0].content != "just a hint":
            failures.append(
                f"Q2 plain hint: got {[(h.type,h.content) for h in qs[1].children]}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: nesthint round-trip clean (Hint + nested Text mix)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
