#!/usr/bin/env python3
"""site.conf test: the site file gives the log base, the config search and
the offer -- and nothing else does.

Each check is built to catch the answer that looks right and is not:
  - a config sitting in the site's search directory under the BARE name is
    found (a hard-coded search directory, or one spelling only, misses it);
  - the default logfile is the site's LOG_BASE_DIR/<user>/my-uhs.log while
    the ENVIRONMENT exports a decoy LOG_BASE_DIR and USER;
  - without the file: /var/log, no search directory, no site offer;
  - there but unreadable, or a line a shell would read otherwise: an error,
    never the neutral values;
  - every offered path but the site's own is one the search reads, and the
    search directory itself is never offered.

Run:  python3 testing/site-conf.py
"""

from __future__ import annotations

import importlib.util
import os
import pwd
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("m", ROOT / "my-uhs.py")
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules["m"] = m
spec.loader.exec_module(m)

FAILS: list[str] = []


def use_site(path: str) -> None:
    """Point the module's one site path at PATH, as the tool would read it."""
    setattr(m, "SITE_CONF", path)


def check(ok: bool, what: str) -> None:
    print(f"{'ok' if ok else 'FAIL'}  {what}")
    if not ok:
        FAILS.append(what)


def main() -> int:
    me = pwd.getpwuid(os.geteuid()).pw_name
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        farm, logs, offer = t / "farm", t / "logs", t / "offer"
        farm.mkdir()
        (farm / "my-uhs").write_text("[my-uhs]\ncolor = never\n")
        site_file = t / "site.conf"
        site_file.write_text(
            "# a comment\n"
            f'DATA_BASE_DIR="{t}/data"\n'
            f'LOG_BASE_DIR="{logs}"\n'
            f'CONFIG_SEARCH_DIR="{farm}"\n'
            f'CONFIG_OFFER_DIR="{offer}"\n'
            'CONFIG_OFFER_NOTE="linked into the search directory"\n')
        use_site(str(site_file))
        os.environ["LOG_BASE_DIR"] = "/decoy/log"
        os.environ["USER"] = "decoy-user"
        site = m.read_site()
        cfg, used = m.load_config(None, site)
        check(used == str(farm / "my-uhs"),
              f"bare-named config in the site's search dir is found ({used})")
        check(cfg["color"] == "never", "its value is used")
        want = f"{logs}/{me}/my-uhs.log"
        check(cfg["logfile"] == want,
              f"logfile from the site and the real user, not the env "
              f"({cfg['logfile']} vs {want})")

        offered = m.config_offer_paths(site)
        searched = set(m.config_search_paths(site))
        check(offered[:1] == [str(offer / "my-uhs.conf")],
              f"the site's offer directory comes first ({offered})")
        check(all(p in searched for p in offered[1:]),
              "every other offer is a searched path")
        check(not any(os.path.dirname(p) == str(farm) for p in offered),
              "the search directory itself is never offered")
        check("(linked into the search directory)" in m.no_config_offer(site),
              "the site's note stands beside its offer")

        same = m.Site(config_search_dir=str(farm), config_offer_dir=str(farm))
        check(not any(os.path.dirname(p) == str(farm)
                      for p in m.config_offer_paths(same)),
              "an offer dir equal to the search dir is not offered")

        use_site(str(t / "absent.conf"))
        site = m.read_site()
        cfg, used = m.load_config(None, site)
        check(site == m.Site(), "no site file: the neutral values")
        check(cfg["logfile"] == f"/var/log/{me}/my-uhs.log",
              f"no site file: logfile under /var/log ({cfg['logfile']})")
        check(not any(str(farm) in p for p in m.config_search_paths(site)),
              "no site file: no search directory")
        check(m.config_offer_paths(site)
              == [p for p in m.config_offer_paths(site)
                  if p in set(m.config_search_paths(site))],
              "no site file: no site offer")

        bad = t / "bad.conf"
        bad.write_text('LOG_BASE_DIR="$(id -un)"\n')
        use_site(str(bad))
        try:
            m.read_site()
            check(False, "a line a shell would expand is refused")
        except m.SiteConfError:
            check(True, "a line a shell would expand is refused")

        if os.geteuid() != 0:   # root reads a mode-000 file anyway
            site_file.chmod(0)
            use_site(str(site_file))
            try:
                m.read_site()
                check(False, "an unreadable site file is an error")
            except m.SiteConfError:
                check(True, "an unreadable site file is an error")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
