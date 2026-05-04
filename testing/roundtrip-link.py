#!/usr/bin/env python3
"""Round-trip test for Link nodes:
build a tree with two Questions where the second has a Link child pointing
to the first. Encode, parse, verify: the Link survives, its title matches,
and its target id resolves to the first Question's new id.

Run:  python3 testing/roundtrip-link.py
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


def find_first(node, type_):
    if node.type == type_:
        return node
    for c in node.children:
        r = find_first(c, type_)
        if r:
            return r
    return None


def find_all(node, type_):
    if node.type == type_:
        yield node
    for c in node.children:
        yield from find_all(c, type_)


def main() -> int:
    failures: list = []
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    n = m.UHSNode

    # ---- Pass 1: build a tree, encode, parse, observe assigned ids ----
    src1 = n(type="Root", content="LinkTest", children=[
        n(type="Subject", content="Chapter", children=[
            n(type="Question", content="First Q?", children=[
                n(type="Hint", content="answer one"),
            ]),
            n(type="Question", content="Second Q?", children=[
                n(type="Hint", content="answer two"),
            ]),
            n(type="Link", content="See first question",
              link_target=0),  # placeholder, fixed in pass 2
        ]),
    ])
    blob1 = m.encode_uhs(src1, master_title="LinkTest")
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob1); fp.close()
    try:
        parsed1, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)
    qs = list(find_all(parsed1, "Question"))
    if len(qs) != 2:
        failures.append(f"pass1: expected 2 Questions, got {len(qs)}")
        if failures:
            for f in failures: print("FAIL:", f)
            return 1
    target_id = qs[0].id

    # ---- Pass 2: rebuild with the correct link_target, re-encode, parse ----
    src2 = n(type="Root", content="LinkTest", children=[
        n(type="Subject", content="Chapter", children=[
            n(type="Question", content="First Q?", children=[
                n(type="Hint", content="answer one"),
            ]),
            n(type="Question", content="Second Q?", children=[
                n(type="Hint", content="answer two"),
            ]),
            n(type="Link", content="See first question",
              link_target=target_id),
        ]),
    ])
    blob2 = m.encode_uhs(src2, master_title="LinkTest")
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob2); fp.close()
    try:
        parsed2, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)

    # Verify: Link present, title preserved, target resolves to the first Question.
    links = list(find_all(parsed2, "Link"))
    if len(links) != 1:
        failures.append(f"pass2: expected 1 Link, got {len(links)}")
    else:
        if links[0].content != "See first question":
            failures.append(
                f"Link title: want 'See first question' "
                f"got {links[0].content!r}")
        qs = list(find_all(parsed2, "Question"))
        first_q_id = qs[0].id
        if links[0].link_target != first_q_id:
            failures.append(
                f"Link target: want {first_q_id} (first Question's id) "
                f"got {links[0].link_target}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1

    # ---- Pass 3: markdown round-trip ----
    md = m.serialize_uhs_to_notes_md(parsed2)
    title3, root3 = m.parse_notes_markdown(md)
    blob3 = m.encode_uhs(root3, master_title=title3)
    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob3); fp.close()
    try:
        parsed3, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)
    links3 = list(find_all(parsed3, "Link"))
    qs3 = list(find_all(parsed3, "Question"))
    if len(links3) != 1:
        failures.append(f"[md] expected 1 Link, got {len(links3)}")
    elif links3[0].content != "See first question":
        failures.append(
            f"[md] Link title: got {links3[0].content!r}")
    elif links3[0].link_target != qs3[0].id:
        failures.append(
            f"[md] Link target: want first Question id {qs3[0].id}, "
            f"got {links3[0].link_target}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: Link round-trip clean (binary + markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
