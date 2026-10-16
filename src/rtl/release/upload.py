"""Check, then (with --push) upload the release to the Hugging Face Hub (config/release.yaml).

    pixi run release-upload            # dry run: checks the package, prints what would be uploaded
    pixi run release-upload --push     # creates the dataset repo (private by default) and uploads it

Needs HF_TOKEN in .env (a write token from https://huggingface.co/settings/tokens) and `hf_repo` set to your
namespace. The licence review (a human gate in data/gold/approvals.yaml) should be done before making it public.
"""

from __future__ import annotations

import argparse
import hashlib

import pyarrow.parquet as pq

from rtl.settings import REPO_ROOT, load_yaml, settings


def check(out) -> list[str]:
    """Problems that block an upload: checksums, required files, excluded columns, GeoParquet metadata."""
    problems = []
    for f in ("README.md", "LICENSE.md", "CITATION.cff", "SHA256SUMS"):
        if not (out / f).exists():
            problems.append(f"missing {f}")
    if not out.exists():
        return [f"no release at {out}: run pixi run release-build"]
    sums = out / "SHA256SUMS"
    for line in (sums.read_text().splitlines() if sums.exists() else []):
        digest, rel = line.split("  ", 1)
        h = hashlib.sha256((out / rel).read_bytes()).hexdigest()
        if h != digest:
            problems.append(f"checksum mismatch: {rel}")
    excluded = set(load_yaml("release.yaml")["licence"]["excluded_columns"])
    parts = sorted((out / "data" / "buildings").glob("*.parquet"))
    if len(parts) != 31:
        problems.append(f"expected 30 district files + unassigned, found {len(parts)}")
    for f in parts[:1] + list((out / "data").glob("*.parquet")):
        schema = pq.read_schema(f)
        if excluded & set(schema.names):
            problems.append(f"{f.name}: excluded columns {sorted(excluded & set(schema.names))}")
    if parts and b"geo" not in (pq.read_schema(parts[0]).metadata or {}):
        problems.append("buildings: no GeoParquet metadata")
    n = sum(pq.ParquetFile(f).metadata.num_rows for f in parts)
    if n != 6_434_247:
        problems.append(f"buildings: {n:,} rows, expected 6,434,247 (one per footprint)")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true", help="actually create the repo and upload")
    args = ap.parse_args()
    cfg = load_yaml("release.yaml")
    out = REPO_ROOT / cfg["out_dir"] / cfg["version"]
    problems = check(out)
    files = [p for p in out.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files) / 1e9
    print(f"{len(files)} files, {size:.2f} GB in {out}; repo {cfg['hf_repo']} (private: {cfg['private']})")
    if problems:
        raise SystemExit("not uploading:\n- " + "\n- ".join(problems))
    if not args.push:
        print("checks pass. Dry run only: rerun with --push to upload.")
        return
    if cfg["hf_repo"].startswith("["):
        raise SystemExit("set hf_repo in config/release.yaml to <your namespace>/<name> first")
    token = settings().hf_token
    if token is None:
        raise SystemExit("add HF_TOKEN=<write token> to .env first")
    from huggingface_hub import HfApi

    api = HfApi(token=token.get_secret_value())
    api.create_repo(cfg["hf_repo"], repo_type="dataset", private=cfg["private"], exist_ok=True)
    api.upload_folder(repo_id=cfg["hf_repo"], repo_type="dataset", folder_path=str(out),
                      commit_message=f"Release {cfg['version']}: {cfg['title']}", ignore_patterns=[".cache/*"])
    print(f"uploaded: https://huggingface.co/datasets/{cfg['hf_repo']}")


if __name__ == "__main__":
    main()
