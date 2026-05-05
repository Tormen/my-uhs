#!/usr/bin/env python3
"""Phase 2 verification: real .uhs → extracted image/sound bytes.

Uses the anach.uhs sample (HyperImage with one PNG) and any sound-bearing
sample if present. Skips gracefully when samples aren't on disk
(the testing/samples/ dir is gitignored — fetch via run-regression.sh).

Run:  python3 testing/extract-binary.py
"""

import importlib.util
import logging
import sys
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


def main() -> int:
    samples = HERE / "samples"
    log = logging.getLogger("rt"); log.setLevel(logging.WARNING)
    failures: list = []
    checked = 0

    anach = samples / "anach.uhs"
    if anach.is_file():
        checked += 1
        parsed, _ = m.parse_uhs(str(anach), log)
        hotspots = list(find_all(parsed, "HotSpot"))
        if not hotspots:
            failures.append("anach: no HotSpot extracted")
        else:
            for hs in hotspots:
                images = [c for c in hs.children if c.type == "Image"]
                if not images:
                    failures.append(f"anach: HotSpot {hs.content!r} has no Image child")
                    continue
                img = images[0]
                if not img.binary:
                    failures.append(
                        f"anach: HotSpot {hs.content!r} Image.binary is None")
                    continue
                kind, mime = m._detect_binary_kind(img.binary)
                if kind != "png":
                    failures.append(
                        f"anach: HotSpot {hs.content!r} expected PNG, "
                        f"got {kind}/{mime} for {len(img.binary)} bytes")
                else:
                    print(f"  anach: {hs.content!r} → {len(img.binary)}-byte {kind} ✓")

    # Walk every sample for Sound nodes; report any extracted.
    sound_seen = 0
    for f in sorted(samples.glob("*.uhs")) + sorted(samples.glob("*.UHS")):
        try:
            parsed, _ = m.parse_uhs(str(f), log)
        except Exception:
            continue
        for snd in find_all(parsed, "Sound"):
            sd = next((c for c in snd.children if c.type == "SoundData"), None)
            if sd is None or not sd.binary:
                continue
            sound_seen += 1
            kind, _ = m._detect_binary_kind(sd.binary)
            print(f"  {f.name}: Sound {snd.content!r} → "
                  f"{len(sd.binary)}-byte {kind} ✓")
    if sound_seen:
        checked += 1

    if checked == 0:
        print("SKIP: no sample .uhs files in testing/samples/")
        return 0
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: phase 2 binary extraction working on real samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
