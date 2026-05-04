#!/usr/bin/env python3
"""Round-trip test: build a tree with Info + Incentive nodes, encode, parse
back, assert the tree we get matches what we put in.

Lives in testing/ so it's discoverable; run from the project root with:

    python3 testing/roundtrip-info-incentive.py
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


def main() -> int:
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    src = build_tree()
    blob = m.encode_uhs(src, master_title="TestGame")

    fp = tempfile.NamedTemporaryFile(
        suffix=".uhs", delete=False, dir=str(HERE))
    fp.write(blob); fp.close()
    try:
        parsed, _ = m.parse_uhs(fp.name, log)
    finally:
        Path(fp.name).unlink(missing_ok=True)

    failures: list[str] = []

    info_nodes = list(find(parsed, "Info"))
    if len(info_nodes) != 1:
        failures.append(f"Info: expected 1, got {len(info_nodes)}")
    else:
        info = info_nodes[0]
        if not info.children or info.children[0].type != "InfoData":
            failures.append("Info: missing InfoData child")
        else:
            data = info.children[0].content
            for needle in ("length=10h", "date=2026-01-01",
                           "author=Tester"):
                if needle not in data:
                    failures.append(
                        f"InfoData missing {needle!r}; got {data!r}")

    inc_nodes = list(find(parsed, "Incentive"))
    if len(inc_nodes) != 1:
        failures.append(f"Incentive: expected 1, got {len(inc_nodes)}")
    else:
        inc = inc_nodes[0]
        if not inc.children or inc.children[0].type != "IncentiveData":
            failures.append("Incentive: missing IncentiveData child")
        else:
            got = inc.children[0].content
            if got != "ZZZZZZZZZ":
                failures.append(
                    f"IncentiveData mismatch: want 'ZZZZZZZZZ' got {got!r}")

    if failures:
        print("FAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("OK: Info + Incentive round-trip clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
