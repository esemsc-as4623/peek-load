"""Gold labelling page: one building at a time, blind to every model label.

    pixi run label-app                     # http://127.0.0.1:8765 (this machine only)
    pixi run label-app --host tailscale    # http://<tailscale-ip>:8765, reachable only over your tailnet

Shows exactly the card Claude sees (text + map) for the 400 gold buildings in data/gold/sample_ids.csv, in a fixed
shuffled order. Every click is appended to data/gold/gold_labels.csv (committed), so you can stop and resume
at any time.
A further 50 already-labelled buildings come back at the end for a second pass, to measure your own
consistency (the "repeat" column). Stdlib only; never listens on all interfaces.
"""

from __future__ import annotations

import csv
import html
import json
import random
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from rtl.llm.cards import CARD_DIR, IMG_DIR
from rtl.schemas import LABEL_CLASSES
from rtl.settings import GOLD_DIR, REPO_ROOT

GOLD = GOLD_DIR / "gold_labels.csv"
FIELDS = ["bldg_id", "label", "confidence", "note", "labeler", "repeat", "labeled_at"]
N_REPEAT = 50
LABELER = "owner"


def queue() -> list[tuple[str, bool]]:
    ids = sorted(pd.read_csv(GOLD_DIR / "sample_ids.csv").query("in_gold").bldg_id)
    rng = random.Random(20261009)
    rng.shuffle(ids)
    return [(i, False) for i in ids] + [(i, True) for i in rng.sample(ids, N_REPEAT)]


def done() -> set[tuple[str, bool]]:
    if not GOLD.exists():
        return set()
    with open(GOLD) as f:
        return {(r["bldg_id"], r["repeat"] == "True") for r in csv.DictReader(f)}


CARDS = None


def card(bid: str) -> str:
    global CARDS
    if CARDS is None:
        CARDS = pd.read_parquet(CARD_DIR / "cards.parquet").set_index("bldg_id").text
    return CARDS[bid]


def page(bid: str, repeat: bool, n_done: int, n_total: int) -> str:
    codebook = html.escape((REPO_ROOT / "docs/codebook.md").read_text())
    buttons = "".join(f'<button name="label" value="{c}">{c.replace("_", " ")}</button>' for c in LABEL_CLASSES)
    return f"""<!doctype html><meta charset="utf-8"><title>Gold labels {n_done}/{n_total}</title>
<style>
 body{{font:15px/1.45 system-ui,sans-serif;margin:16px;background:#fcfcfb;color:#0b0b0b;max-width:1200px}}
 .row{{display:flex;gap:20px;flex-wrap:wrap}} img{{width:440px;max-width:100%;border:1px solid #ccc}}
 pre{{white-space:pre-wrap;background:#fff;border:1px solid #e4e3df;padding:12px;flex:1;min-width:320px;margin:0}}
 button{{font-size:15px;margin:3px;padding:8px 12px;cursor:pointer}} .bar{{color:#52514e}}
 details{{margin-top:16px}} details pre{{font-size:12px}}
</style>
<p class="bar">{n_done} of {n_total} done{" · <b>second pass</b>" if repeat else ""} · {bid}</p>
<div class="row"><img src="/img/{bid}.png" alt="map"><pre>{html.escape(card(bid))}</pre></div>
<form method="post" action="/label">
 <input type="hidden" name="bldg_id" value="{bid}"><input type="hidden" name="repeat" value="{repeat}">
 <p>Confidence: <label><input type="radio" name="confidence" value="high">high</label>
  <label><input type="radio" name="confidence" value="medium" checked>medium</label>
  <label><input type="radio" name="confidence" value="low">low</label>
  &nbsp; Note: <input name="note" size="50" placeholder="optional: which cues decided it"></p>
 <p>{buttons}</p>
</form>
<details><summary>Codebook v1</summary><pre>{codebook}</pre></details>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8", code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/img/"):
            f = IMG_DIR / path.removeprefix("/img/")
            return self._send(f.read_bytes(), "image/png") if f.exists() else self._send(b"", code=404)
        q, d = queue(), done()
        todo = [x for x in q if x not in d]
        if not todo:
            return self._send(f"<p>All {len(q)} done. Thank you!</p>".encode())
        bid, rep = todo[0]
        self._send(page(bid, rep, len(q) - len(todo), len(q)).encode())

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if form.get("label") in LABEL_CLASSES:
            new = not GOLD.exists()
            with open(GOLD, "a", newline="") as f:
                w = csv.DictWriter(f, FIELDS)
                if new:
                    w.writeheader()
                w.writerow({"bldg_id": form["bldg_id"], "label": form["label"],
                            "confidence": form.get("confidence", "medium"), "note": form.get("note", ""),
                            "labeler": LABELER, "repeat": form.get("repeat", "False"),
                            "labeled_at": datetime.now(UTC).isoformat(timespec="seconds")})
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, *args):  # keep the terminal quiet
        pass


def tailscale_ip() -> str:
    """This machine's Tailscale IPv4 (100.64.0.0/10), so the page is reachable only over the tailnet."""
    import subprocess

    ip = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True).stdout.split()[0]
    if not ip.startswith("100."):
        raise SystemExit(f"unexpected Tailscale address {ip!r}")
    return ip


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="'tailscale' or an address (never 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    host = tailscale_ip() if args.host == "tailscale" else args.host
    if host in ("0.0.0.0", "::"):
        raise SystemExit("refusing to listen on all interfaces; use --host tailscale")
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"url": f"http://{host}:{args.port}", "gold_file": str(GOLD)}), flush=True)
    ThreadingHTTPServer((host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
