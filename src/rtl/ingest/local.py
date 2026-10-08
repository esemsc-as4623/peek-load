"""Register files you downloaded yourself (DHS, World Bank microdata, NISR) in the manifest.

Restricted data lives under data/restricted/<source_id>/ (gitignored) and is NEVER redistributed.
Only its path, size and sha256 go into the committed manifest, so analyses stay traceable.

    pixi run register-local dhs_2019 data/restricted/dhs/RW2025DHS/
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rtl.manifest import record
from rtl.settings import REPO_ROOT


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source_id", help="id from config/sources.yaml, e.g. dhs_2019 or mtf_rwanda")
    ap.add_argument("path", type=Path, help="file or directory under data/restricted/")
    args = ap.parse_args()

    root = (REPO_ROOT / args.path).resolve() if not args.path.is_absolute() else args.path
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    for f in files:
        e = record(args.source_id, f, url="local:manual-download", extra={"access": "restricted"})
        print(f"registered {e['path']}  {e['bytes'] / 1e6:.1f} MB  sha256={e['sha256'][:12]}…")


if __name__ == "__main__":
    main()
