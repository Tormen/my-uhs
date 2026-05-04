# my-uhs regression tests

Verifies that `my-uhs read` produces output identical to the Java reference
implementation ([Vhati/OpenUHS](https://github.com/Vhati/OpenUHS)) across a
corpus of real-world `.uhs` files.

## Why this directory is gitignored

The test fixtures (sample `.uhs` files and their rendered expected output)
contain hint content from the [uhs-hints.com](https://www.uhs-hints.com/)
catalog. Fetching them for personal use is fine; checking them into a
public git repository is not. The harness fetches them on demand and
caches them locally — they never enter version control.

## Usage

```sh
# First-time setup: fetches 12 sample .uhs files and the Java OpenUHS jar,
# then generates the reference outputs once. Requires java on PATH and a
# network connection. Total fetch is ~3 MB.
./run-regression.sh --setup

# Run the full regression: parses each sample with both Java and my-uhs,
# diffs the outputs, reports any mismatches.
./run-regression.sh

# Quick mode: runs my-uhs on each sample but skips the Java cross-check.
# Useful for fast iteration when you only want to confirm parsing works.
./run-regression.sh --quick
```

## What's tested

The 12 sample files span the full UHS format range:

| Slug             | Format | Year | Notes                          |
| ---------------- | ------ | ---- | ------------------------------ |
| `alone`          | 91a    | 1993 | Smallest sample                |
| `amfv`           | 91a    | 1995 |                                |
| `11thhour`       | 91a    | 1996 | Tests bare-space hint quirk    |
| `adv660`         | 96a    | 1998 |                                |
| `arcanum`        | 96a    | 2001 |                                |
| `anach`          | 96a    | 2001 | Has hyperpng image nodes       |
| `alone4`         | 96a    | 2001 |                                |
| `agon`           | 96a    | 2003 |                                |
| `amerzone`       | 96a    | 2004 |                                |
| `apollo-justice` | 96a    | 2008 | Tests UTF-8 handling           |
| `portal`         | 96a    | 2009 | Tests text-hunk binary tail    |
| `aom3`           | 96a    | 2012 | Most recent format variant     |

Each file exercises a different combination of node types (subjects, hints,
nesthints, comments, credits, text-hunks, image/audio placeholders) and
encryption variants (string, nest-keyed, text-hunk-keyed).

## Expected results

All 12 should diff to zero lines except `apollo-justice.uhs`, which has a
known 4-line diff caused by a Java `System.out` UTF-8 encoding bug — the
Java reference renders `fiancé` as `fianc?`, while `my-uhs` writes correct
UTF-8. The harness whitelists this case as a pass.

## Layout

```
testing/
├── run-regression.sh    # the test driver
├── README.md            # this file
├── samples/             # cached .uhs files (gitignored)
├── expected/            # Java reference outputs (gitignored)
├── actual/              # my-uhs outputs from last run (gitignored)
├── .openuhs/            # cached Java jar (gitignored)
├── .catalog/            # throwaway my-uhs catalog used for tests (gitignored)
└── .regression.conf     # generated test config (gitignored)
```

## Adding new test cases

Drop a `.uhs` file into `samples/`, then re-run `--setup` to generate the
Java reference for it. The driver picks up any `.uhs` / `.UHS` file in the
samples directory automatically.

If a sample produces unexpected diffs, look at the actual vs expected
files in `actual/` and `expected/` — `diff -u` between them will show
exactly where the parsers disagree.
