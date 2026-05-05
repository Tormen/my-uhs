# my-uhs

A small, colorized command-line reader and local catalog manager for
**Universal Hint System** (`.uhs`) files. Pure Python, stdlib only — no Java, no
third-party packages.

The hint-file parser is a faithful port of David Millis'
[OpenUHS](https://github.com/Vhati/OpenUHS) (Java, GPLv2+, 2012). It supports
both 88a (1988) and 91a/96a (1991+) format files. Output of `my-uhs read` has
been verified bit-identical to `OpenUHS --print` across a regression set of 12
real-world hint files spanning the entire format range from the earliest 88a
files (late 1980s) through 91a/96a releases up to the early 2010s.

## Why this exists

The official UHS readers are Windows-only shareware. The Java OpenUHS reader
runs on macOS but pulls in a JVM and emits plain text. `my-uhs` is a single
Python file that runs natively in any modern macOS terminal, prints colorized
output that's actually readable, and adds a local catalog so you can pull
hints once and re-read them offline.

## Install

Drop the `my-uhs` script anywhere on your `$PATH` and `chmod +x` it:

```sh
install -m 0755 my-uhs /usr/local/bin/my-uhs
my-uhs --create-config ~/.my-uhs.conf   # write the default config to that path
# (bare `--create-config` prints to stdout — pipe or redirect as you like)
```

Requirements: Python 3.7+. macOS ships 3.9 in Sonoma and later.

## Quick start

```sh
my-uhs --create-config ~/.my-uhs.conf   # write defaults to that path
my-uhs catalog --search <term>    # browse the remote catalog
my-uhs pull <name>                # substring is enough — exact name not required
my-uhs pull "<two words>"         # multiple matches → interactive prompt
my-uhs pull all --yes             # download everything (warning: hundreds of MB)
my-uhs list                       # show what's in the local catalog
my-uhs list --search <term>       # filter the local catalog
my-uhs read <name>.uhs            # render with color
my-uhs read /tmp/somefile.uhs     # also accepts a direct path
my-uhs push /tmp/myhints.uhs      # register a local file in the catalog
```

## Subcommands

| Command | Purpose |
| --- | --- |
| `read FILE` | Render a `.uhs` file (colorized when stdout is a TTY). FILE may be a path or a catalog name. |
| `title FILE` | Print only the hint file's title. |
| `version FILE` | Print only the file's declared format version (e.g. `91a`, `96a`). |
| `test FILE` | Quietly parse and report success or failure. Exit code 0 on success. |
| `list [--search TERM]` | List entries in the local catalog. With `--search`, filter by title/filename substring. |
| `catalog [--search TERM]` | Refresh the cached remote catalog index from `uhs-hints.com` and list entries. |
| `pull NAME [--yes] [--force]` | Download from the remote catalog. NAME may be an exact filename (`<name>.uhs`), a title or filename substring, or `all`. Multiple matches prompt unless `--yes`. |
| `push FILE` | Register an existing local `.uhs` file into the catalog. Use `--name` to override the catalog key, `--force` to overwrite. |
| `notes NAME` | Open a markdown notes file for a game in `$EDITOR` (creates a UHS-shaped template on first run). For authoring your own hints as you play. |
| `compose NAME [--force]` | Convert a notes markdown file into a real binary `.uhs` and register it in the local catalog, readable by `my-uhs read` like any other entry. `--force` overwrites an existing entry. |
| `export NAME` | Inverse of `compose`: dump an existing `.uhs` from the catalog back to markdown under `notes/<NAME>.md` (round-trip authoring). Image and sound binaries are written as sidecar files. |
| `use NAME` | Interactive hint reveal. Walk chapters → questions → hints one keypress at a time. Quit with `q`; resume picks up at the same question and the same hint number you left off on. |

## Global flags

| Flag | Effect |
| --- | --- |
| `-c PATH` / `--config PATH` | Use a specific config file (skips the search list). |
| `--create-config [PATH]` | Without `PATH`: print the default config to stdout (pipe or redirect as you like). With `PATH`: write the default to that file (refuses to overwrite an existing file). |
| `-D` / `--debug` | Enable verbose logging to the configured logfile. |
| `--no-color` | Disable ANSI color even on a TTY. |
| `--version` | Print version. |

`NO_COLOR` (https://no-color.org/) is also honored.

## Config file

Format: standard INI with a single `[my-uhs]` section.

**Search order (first hit wins):**

1. `/LINKS/default/my-uhs.conf`
2. `~/.my-uhs.conf`
3. `/etc/my-uhs.conf`
4. `/usr/local/etc/my-uhs.conf`

`--config PATH` overrides the search entirely. `--create-config` (no
argument) prints the fully-commented default config to stdout; pass an
explicit target path (`--create-config ~/.my-uhs.conf`) to write it to a
file. The file form refuses to overwrite an existing file.

### Keys

| Key | Default | Notes |
| --- | --- | --- |
| `catalog_dir` | `~/.my-uhs.catalog/` | Holds `index.json`, `remote-catalog.xml`, and `files/<name>.uhs`. |
| `catalog_url` | `http://www.uhs-hints.com/cgi-bin/update.cgi` | The official OpenUHS update server. |
| `user_agent` | `my-uhs/<version> (+OpenUHS-compatible)` | Sent on all HTTP requests. |
| `logfile` | `~/Library/Logs/my-uhs.log` | Only written when `-D` is given. |
| `color` | `auto` | `auto` (TTY-aware), `always`, or `never`. |
| `fetch_timeout` | `30` | Network timeout in seconds for `pull` and `catalog`. |

`~` and `$VAR` references are expanded.

### Example

```ini
[my-uhs]
catalog_dir = /Volumes/Backup/uhs
logfile     = ~/Library/Logs/my-uhs.log
color       = always
fetch_timeout = 60
```

## Authoring your own hint files

`my-uhs notes <name>` opens a markdown file in your `$EDITOR` with a
UHS-shaped template (chapters → puzzles → escalating hint tiers). Edit it
as you play a game, jotting down nudges and solutions. Then
`my-uhs compose <name>` turns it into a real binary `.uhs` file in your
catalog.

```sh
my-uhs notes <name>         # opens ~/.my-uhs.catalog/notes/<name>.md
# ... edit the template, fill in your puzzles and hints ...
my-uhs compose <name>       # creates <name>.uhs in the catalog
my-uhs read <name>.uhs      # read it back, colorized
# Edit the markdown again later, then:
my-uhs compose --force <name>    # overwrite the existing .uhs
```

The markdown format is intentionally minimal:

```markdown
# Game Title

## Chapter 1 — Opening

> Note: Background info that isn't a puzzle goes here.

### How do I open the door?

- Have you searched the room?
- The key is hidden somewhere in plain sight.
- Look under the rug.

### Next puzzle

- First nudge.
- Clearer direction.
- Full solution.

> Credit: Your name here, 2026.
```

The composed `.uhs` is a real 96a-format file — no Java required, no
network involved, fully readable by any UHS-compatible reader.

The encoder produces the node types a hand-authored hint file actually
needs: `subject`, `hint`, `comment`, `credit`, `version`, `blank`. Image,
audio, and `text`-with-binary-tail nodes are not emitted (they're a parser
concern, not an authoring one — the parser still reads them in files made
by other tools). Unicode em-dashes, quotes, arrows, and ellipses are
sanitised to ASCII equivalents on encode; characters outside Latin-1 are
dropped with a `?` placeholder.

## Catalog layout

```
~/.my-uhs.catalog/
├── index.json           # local catalog (title, version, size, source, mtime)
├── remote-catalog.xml   # last-fetched raw remote catalog
├── files/
│   ├── <name>.uhs
│   ├── <name>.uhs
│   └── …
└── notes/
    ├── <name>.md        # source markdown for `compose`
    └── …
```

`index.json` is rewritten atomically on every `pull` / `push`, including
mid-`pull all` interruptions (Ctrl-C) — whatever was downloaded before the
interrupt is preserved.

## Format coverage

The parser handles every node type the Java reference handles:

- 88a: subjects, questions, hints (XOR-encrypted)
- 9x: `subject`, `hint`, `nesthint`, `comment`, `credit`, `text`, `link`,
  `version`, `info`, `incentive`, `blank`, `hyperpng`, `gifa`, `sound`
- All three decryption variants: simple (88a hints), nest-keyed (nesthint /
  incentive), text-hunk-keyed (text-node binary tail)
- Text-escape unfolding: accents (`#a+e'#a-` → `é` etc.), `^break^` substitution,
  `#w-` / `#w+` whitespace mode toggle, `##` literal `#`, the bare-space
  `\n \n` quirk used for ASCII art layouts
- AUX_NEST tree restructuring (the master subject becomes the rendered root)

Image and audio nodes are recognized and structurally preserved, but their
binary payloads aren't rendered (they appear as `^IMAGE^` and `^AUDIO^`
placeholders, matching `OpenUHS --print` behavior).

## How UHS encoding works

UHS hint files aren't truly encrypted in the cryptographic sense — the
algorithm is public and trivially reversible — but the contents are encoded
so a casual `cat` or `strings` on the file shows scrambled text instead
of plaintext spoilers. This was important historically: the official UHS
readers were paid shareware, and the encoding stopped people from grepping
spoilers out of the bytes without buying a reader. The format treats every
byte as a Latin-1 character and works at the per-byte level.

The format uses **three different encoding variants**, picked per node type:

**1. Simple obfuscation (88a hints, standalone `hint` nodes).**
No key — purely a byte transform. Each readable byte `c` becomes either
`(c + 32) / 2` or `(c + 127) / 2` depending on which puts the result back
into the printable ASCII range. The decode is `c < 80 ? c*2 - 32 : c*2 - 127`.
This is what hides hint text in older 88a files and in the simpler `hint`
nodes of 9x files.

**2. Position-keyed obfuscation (`nesthint`, `incentive` nodes).**
Uses a per-file key derived from the master subject title: for each
character `i` of the title, `key[i] = (title[i] + ('k','e','y')[i%3] ^ (i+40))`,
clamped to printable range. To decode a byte `c` at position `i`,
subtract `key[i % keylen] ^ (i + 40)` and wrap into the printable range.
The position dependency means identical input bytes encode differently
depending on where they sit, defeating naive frequency analysis.

**3. Text-hunk encoding (`text` nodes with binary payloads).**
Same per-file key, but indexed differently — `key[i % keylen] ^ ((i % keylen) + 40)`.
The XOR offset varies by `i mod keylen` rather than absolute `i`, which
makes the hunks resistant to a different cut of attack. These hunks live
in the binary tail of the file, after a `0x1A` separator byte, so they
aren't visible in the text section at all.

**Text-escape system (post-decode).**
Decoded text isn't quite plaintext — it carries an escape vocabulary
parsed afterwards: `#a+e'#a-` produces `é`, `^break^` substitutes a space
or newline depending on the `#w-` / `#w+` whitespace mode, `##` is a
literal `#`, and a bare-space line means "force a real blank line in
the rendered output." The parser handles all of these to recover the
original Latin-1 text the author typed.

**The encoder direction (`my-uhs compose`).**
`my-uhs` runs the same operations in reverse to produce a real `.uhs`
file from your markdown notes. For each node it picks the right encoding
variant, derives the key from the title you chose, and emits CRLF-terminated
lines exactly the way the parser expects. The encoder is verified by
round-tripping: every composed file is parsed back immediately and the
resulting tree must match what was encoded, otherwise `compose` errors out
without saving.

## Conformance

`my-uhs read` produces byte-identical output to `java -jar openuhs.jar
--print` across the regression set. The single observed deviation is in
one file in the regression set where Java's `System.out` corrupts the UTF-8
character `é` to `?` due to default-encoding quirks; `my-uhs` writes valid
UTF-8.

## Limitations

- No GUI. The Java OpenUHS reader has a Swing GUI with clickable hint reveal;
  `my-uhs read` is non-interactive and dumps everything at once.
- No image rendering. Hyperpng / gifa / sound binary payloads are recognized
  and skipped.
- No upload-to-uhs-hints.com. The official catalog accepts new files only via
  hand-curated submissions to Jason Strautman; the protocol exposes no upload
  endpoint. `push` therefore only registers files in the *local* catalog.
- `pull` fetches over plain HTTP, because that's what `update.cgi` speaks.

## License

**This program is free software, distributed under the GNU General
Public License version 3 or later (GPLv3+).**

```
Copyright (C) 2026  Klaus

This program incorporates code derived from OpenUHS (Java, 2012),
Copyright (C) 2012  David Millis, originally licensed GPLv2-or-later.
It is therefore distributed under GPLv3+, as permitted by the "or later"
clause of the upstream license.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details: <https://www.gnu.org/licenses/>.
```

In short: you may use, study, modify, and redistribute this program freely,
provided that any redistribution (modified or not) carries the same
copyleft license and that source is made available alongside any binary
distribution. The UHS file format and the official UHS readers remain
© Universal Hint System / Jason Strautman; this tool reads files but does
not redistribute them.

## See also

- [OpenUHS on GitHub](https://github.com/Vhati/OpenUHS) — the upstream Java reader
- [uhs-hints.com](https://www.uhs-hints.com/) — official hint repository
- [Universal Hint System on Wikipedia](https://en.wikipedia.org/wiki/Universal_Hint_System)

