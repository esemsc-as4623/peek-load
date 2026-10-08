import hashlib

from rtl.manifest import sha256_file


def test_sha256_matches_hashlib(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"rooftops" * 100_000)
    assert sha256_file(p, chunk=4096) == hashlib.sha256(p.read_bytes()).hexdigest()
