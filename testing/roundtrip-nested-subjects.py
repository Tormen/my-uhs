#!/usr/bin/env python3
"""Round-trip test for nested Subjects (Subject inside Subject).

The encoder already handles Subject recursion natively. The grammar
addition is `### Sub: title` for depth-2 Subjects. A nested-Subject's
children attach to the latest opened Subject, so subsequent Questions
go inside the nested level.

Run:  python3 testing/roundtrip-nested-subjects.py
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


def find_all(node, type_):
    if node.type == type_:
        yield node
    for c in node.children:
        yield from find_all(c, type_)


def first_subject_named(node, name):
    for s in find_all(node, "Subject"):
        if s.content == name:
            return s
    return None


def main() -> int:
    failures: list = []
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    n = m.UHSNode

    # Tree shape:
    #   Root
    #     Chapter A   (depth 1)
    #       Section A1 (depth 2)
    #         Q1
    #       Section A2 (depth 2)
    #         Q2
    #     Chapter B   (depth 1)
    #       Q3        (directly under chapter)
    src = n(type="Root", content="NestTest", children=[
        n(type="Subject", content="Chapter A", children=[
            n(type="Subject", content="Section A1", children=[
                n(type="Question", content="Q1?", children=[
                    n(type="Hint", content="answer 1"),
                ]),
            ]),
            n(type="Subject", content="Section A2", children=[
                n(type="Question", content="Q2?", children=[
                    n(type="Hint", content="answer 2"),
                ]),
            ]),
        ]),
        n(type="Subject", content="Chapter B", children=[
            n(type="Question", content="Q3?", children=[
                n(type="Hint", content="answer 3"),
            ]),
        ]),
    ])

    # 1. binary round-trip via encode -> parse
    blob = m.encode_uhs(src, master_title="NestTest")
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob); fp.close()
    try:
        parsed, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)

    a1 = first_subject_named(parsed, "Section A1")
    a2 = first_subject_named(parsed, "Section A2")
    if a1 is None or a2 is None:
        failures.append("[binary] nested Subjects A1/A2 not preserved")
    else:
        # Q1 must be under A1, Q2 must be under A2
        a1qs = list(find_all(a1, "Question"))
        a2qs = list(find_all(a2, "Question"))
        if len(a1qs) != 1 or a1qs[0].content != "Q1?":
            failures.append(
                f"[binary] Section A1's Question wrong: {a1qs}")
        if len(a2qs) != 1 or a2qs[0].content != "Q2?":
            failures.append(
                f"[binary] Section A2's Question wrong: {a2qs}")

    # 2. markdown round-trip
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

    a1m = first_subject_named(parsed2, "Section A1")
    a2m = first_subject_named(parsed2, "Section A2")
    if a1m is None or a2m is None:
        failures.append("[md] nested Subjects A1/A2 not preserved")
    else:
        if not list(find_all(a1m, "Question")):
            failures.append("[md] Section A1 lost its Question on md round-trip")
        if not list(find_all(a2m, "Question")):
            failures.append("[md] Section A2 lost its Question on md round-trip")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: nested Subjects round-trip clean (binary + markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
