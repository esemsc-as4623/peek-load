"""Copy the design canvas's pages into app/src/pages and lay out the data folder for the server.

Usage:
    python app/scripts/sync_canvas.py PAGES_DIR BLOBS_DIR [--data-out data/dashboard/app_v1]

PAGES_DIR holds the canvas's project/*.dc.html files; BLOBS_DIR holds the canvas uploads as
<id>.json / <id>.png (both come from reading the canvas). Upload links (/_blob/<id>) become
'@data/<name>' links that the site resolves against VITE_DATA_BASE. Fails on an unknown data id.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SKIP = {"DesignSystem.dc.html"}  # the design sheet is for the canvas only
# wording fixes that only apply to the public site
FIXES = {
    "Nav.dc.html": [("      ['design', 'Design system', 'DesignSystem.dc.html', '']\n", ""),
                    ("['validation', 'Validation & quality', 'Validation.dc.html', 'in_progress'],",
                     "['validation', 'Validation & quality', 'Validation.dc.html', 'in_progress']")],
}


def slug(text, n=6):
    words = re.findall(r"[a-z0-9]+", text.lower())[:n]
    return "-".join(words) or "figure"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages_dir", type=Path)
    ap.add_argument("blobs_dir", type=Path)
    ap.add_argument("--data-out", type=Path, default=APP.parent / "data/dashboard/app_v1")
    a = ap.parse_args()
    names = {k: v for k, v in json.loads((APP / "data-map.json").read_text()).items() if not k.startswith("_")}
    out_pages = APP / "src/pages"
    out_pages.mkdir(parents=True, exist_ok=True)
    a.data_out.mkdir(parents=True, exist_ok=True)
    used = {}
    for page in sorted(a.pages_dir.glob("*.dc.html")):
        if page.name in SKIP:
            continue
        s = page.read_text()
        # figures: <img src="/_blob/id" alt="..."> -> figures/<slug of alt>.png
        def img(m):
            bid, alt = m.group(2), m.group(4)
            name = names.get(bid) or f"figures/{slug(alt)}.png"
            used[bid] = name
            return f'{m.group(1)}@data/{name}{m.group(3)}{alt}'
        s = re.sub(r'(<img[^>]*?src=")/_blob/([0-9a-f]{32})("[^>]*?alt=")([^"]*)', img, s)
        def other(m, page=page):
            bid = m.group(1)
            if bid not in names:
                raise SystemExit(f"{page.name}: unknown data id {bid}; add it to app/data-map.json")
            used[bid] = names[bid]
            return "@data/" + names[bid]
        s = re.sub(r"/_blob/([0-9a-f]{32})", other, s)
        for old, new in FIXES.get(page.name, []):
            if old not in s:
                raise SystemExit(f"{page.name}: expected text not found: {old!r}")
            s = s.replace(old, new)
        (out_pages / page.name).write_text(s)
        print("page", page.name)
    manifest = {}
    for bid, name in sorted(used.items(), key=lambda x: x[1]):
        src = next(a.blobs_dir.glob(bid + ".*"), None)
        if src is None:
            raise SystemExit(f"missing upload {bid} ({name}) in {a.blobs_dir}")
        dst = a.data_out / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        manifest[name] = {"canvas_id": bid, "bytes": dst.stat().st_size,
                          "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()}
    (APP / "data-files.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(manifest)} data files -> {a.data_out}")


if __name__ == "__main__":
    main()
