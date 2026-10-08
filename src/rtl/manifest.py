"""Provenance manifest for every raw file.

`data/manifest.json` is committed. Each entry records where a file came from, when, its sha256 and
licence, so any downstream table can be traced back to exact inputs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from tqdm import tqdm

from rtl.locks import manifest_lock
from rtl.settings import MANIFEST_PATH, RAW_DIR, REPO_ROOT, sources


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def read_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def write_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n")


def record(source_id: str, path: Path, url: str, extra: dict | None = None) -> dict:
    """Add or update the manifest entry for a file already on disk."""
    src = sources().get(source_id, {})
    entry = {
        "source_id": source_id,
        "url": url,
        "path": str(path.relative_to(REPO_ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "licence": src.get("licence"),
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **(extra or {}),
    }
    with manifest_lock():  # other workstreams may be recording downloads at the same time
        manifest = read_manifest()
        manifest[entry["path"]] = entry
        write_manifest(manifest)
    return entry


def remote_version(url: str) -> str:
    """Version string for a remote file: its Last-Modified date, else today's date."""
    try:
        r = httpx.head(url, follow_redirects=True, timeout=30)
        lm = r.headers.get("last-modified")
        if lm:
            return parsedate_to_datetime(lm).strftime("%Y-%m-%d")
    except httpx.HTTPError:
        pass
    return datetime.now(UTC).strftime("%Y-%m-%d")


def fetch(source_id: str, url: str | None = None, filename: str | None = None) -> Path:
    """Download a source file into data/raw/<source_id>/<version>/ and record it in the manifest.

    Idempotent: if the file exists and its sha256 matches the manifest, nothing is downloaded.
    """
    url = url or sources()[source_id]["url"]
    if not url:
        raise ValueError(f"{source_id}: no url in config/sources.yaml")
    version = remote_version(url)
    filename = filename or url.rstrip("/").split("/")[-1]
    dest = RAW_DIR / source_id / version / filename
    rel = str(dest.relative_to(REPO_ROOT))

    known = read_manifest().get(rel)
    if dest.exists() and known and known["bytes"] == dest.stat().st_size:
        print(f"[{source_id}] up to date: {rel}")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) or None
        with open(tmp, "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=source_id) as bar:
            for block in r.iter_bytes(1 << 20):
                f.write(block)
                bar.update(len(block))
    tmp.rename(dest)
    entry = record(source_id, dest, url, {"version": version})
    print(f"[{source_id}] {entry['bytes'] / 1e6:.1f} MB  sha256={entry['sha256'][:12]}…  -> {rel}")
    return dest
