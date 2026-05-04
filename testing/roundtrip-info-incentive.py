#!/usr/bin/env python3
"""Round-trip test: build a tree with Info + Incentive nodes, exercise BOTH
the binary round-trip (encode → parse) and the markdown round-trip
(encode → export-md → parse_notes_md → encode → parse).

Run:  python3 testing/roundtrip-info-incentive.py
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


def build_tree() -> "m.UHSNode":
    n = m.UHSNode
    return n(type="Root", content="TestGame", children=[
        n(type="Subject", content="Chapter 1", children=[
            n(type="Comment", content="Note", children=[
                n(type="CommentData",
                  content="Quick note about the chapter."),
            ]),
            n(type="Info", content="Info: about the game", children=[
                n(type="InfoData",
                  content="length=10h\ndate=2026-01-01\nauthor=Tester"),
            ]),
            n(type="Incentive", content="Incentive: hidden answer",
              children=[
                  n(type="IncentiveData",
                    content="ZZZZZZZZZ"),
              ]),
            n(type="Question", content="Q1?", children=[
                n(type="Hint", content="single hint"),
            ]),
        ]),
    ])


def find(node, type_):
    if node.type == type_:
        yield node
    for c in node.children:
        yield from find(c, type_)


def encode_then_parse(root: "m.UHSNode", master: str) -> "m.UHSNode":
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


def assert_info_incentive(tree: "m.UHSNode", label: str,
                          failures: list) -> None:
    info = list(find(tree, "Info"))
    if len(info) != 1:
        failures.append(f"[{label}] Info: expected 1, got {len(info)}")
    elif not info[0].children or info[0].children[0].type != "InfoData":
        failures.append(f"[{label}] Info: missing InfoData child")
    else:
        data = info[0].children[0].content
        for needle in ("length=10h", "date=2026-01-01", "author=Tester"):
            if needle not in data:
                failures.append(
                    f"[{label}] InfoData missing {needle!r}; got {data!r}")

    inc = list(find(tree, "Incentive"))
    if len(inc) != 1:
        failures.append(f"[{label}] Incentive: expected 1, got {len(inc)}")
    elif not inc[0].children or inc[0].children[0].type != "IncentiveData":
        failures.append(f"[{label}] Incentive: missing IncentiveData child")
    elif inc[0].children[0].content != "ZZZZZZZZZ":
        failures.append(
            f"[{label}] IncentiveData mismatch: "
            f"want 'ZZZZZZZZZ' got {inc[0].children[0].content!r}")


def main() -> int:
    failures: list = []

    # 1. Binary round-trip: synth → encode → parse.
    src = build_tree()
    parsed_bin = encode_then_parse(src, "TestGame")
    assert_info_incentive(parsed_bin, "binary", failures)

    # 2. Markdown round-trip:
    #    synth → encode → parse → export-md → parse_notes_md → encode → parse.
    md = m.serialize_uhs_to_notes_md(parsed_bin)
    title2, root2 = m.parse_notes_markdown(md)
    if title2 != "TestGame":
        failures.append(f"[md] title mismatch: {title2!r}")
    parsed_md = encode_then_parse(root2, title2)
    assert_info_incentive(parsed_md, "markdown", failures)

    if failures:
        print("FAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("OK: Info + Incentive round-trip clean (binary + markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
