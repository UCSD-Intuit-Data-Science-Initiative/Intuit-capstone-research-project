"""Offline tests for Stage 1 fetch/verify. No network: a fake opener serves bytes."""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path

import pytest
import yaml

from capstone.data import fetch as F

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- helpers


def blob_id(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def make_zip(entries: dict[str, bytes], symlinks: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


GOOD_ZIP = make_zip({
    "data/": b"",
    "data/train.json": b'[{"id": 1}]',
    "data/dev.json": b"[]",
    "__MACOSX/data/._train.json": b"junk",
})

UPSTREAM = {
    "LICENSE": b"MIT License\n",
    "dataset/train.json": b'[{"id": "a"}]',
    "data.zip": GOOD_ZIP,
}


class FakeOpener:
    """Serves UPSTREAM bytes by path suffix and records every URL requested."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        for path, data in self.files.items():
            if url.endswith("/" + path):
                return io.BytesIO(data)
        raise AssertionError(f"unexpected URL {url}")


def no_network(url: str):
    raise AssertionError(f"network used: {url}")


def write_sources(root: Path, files: dict[str, bytes], extract=("data.zip",)) -> F.Paths:
    (root / "data").mkdir(parents=True, exist_ok=True)
    doc = {"sources": [{
        "name": "demo", "repo": "https://github.com/acme/demo",
        "commit": "0" * 40, "license": "MIT",
        "files": [{"path": p, "git_blob": blob_id(d), "size": len(d),
                   **({"extract": True} if p in extract else {})}
                  for p, d in files.items()],
    }]}
    (root / "data" / "sources.yaml").write_text(yaml.safe_dump(doc))
    return F.Paths(root)


@pytest.fixture
def locked(tmp_path: Path) -> F.Paths:
    """A project that has fetched and locked UPSTREAM once."""
    paths = write_sources(tmp_path, UPSTREAM)
    F.fetch(paths, opener=FakeOpener(UPSTREAM), write=True, log=lambda _: None)
    return paths


# ---------------------------------------------------------------- hashing


def test_git_blob_id_matches_git(tmp_path: Path):
    p = tmp_path / "hello"
    p.write_bytes(b"hello\n")
    # `printf 'hello\n' | git hash-object --stdin`
    assert F.git_blob_id(p) == "ce013625030ba8dba906f756967f9e9ca394464a"


# ---------------------------------------------------------------- fetch


def test_first_fetch_downloads_extracts_and_locks(locked: F.Paths):
    raw = locked.raw
    assert (raw / "demo/LICENSE").read_bytes() == UPSTREAM["LICENSE"]
    assert (raw / "demo/data/train.json").read_bytes() == b'[{"id": 1}]'
    assert not (raw / "demo/__MACOSX").exists(), "zip junk must be skipped"
    assert not list(raw.rglob(".unzip-*")), "staging folder must be cleaned up"

    lock = F.read_lock(locked.lock)
    assert sorted(lock) == ["demo/LICENSE", "demo/data.zip", "demo/data/dev.json",
                            "demo/data/train.json", "demo/dataset/train.json"]
    assert lock["demo/LICENSE"] == hashlib.sha256(UPSTREAM["LICENSE"]).hexdigest()
    assert (raw / F.LOG_NAME).exists() and F.LOG_NAME not in lock


def test_raw_files_are_read_only(locked: F.Paths):
    for p in locked.raw.rglob("*"):
        if p.is_file() and p.name != F.LOG_NAME:
            assert not p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), p


def test_rerun_is_idempotent_and_offline(locked: F.Paths):
    before = F.read_lock(locked.lock)
    assert F.fetch(locked, opener=no_network, log=lambda _: None) == 0
    assert F.read_lock(locked.lock) == before


def test_download_not_matching_pin_is_rejected(tmp_path: Path):
    paths = write_sources(tmp_path, UPSTREAM)
    tampered = dict(UPSTREAM, **{"dataset/train.json": b'[{"id": "EVIL"}]'})
    with pytest.raises(F.FetchError, match="dataset/train.json"):
        F.fetch(paths, opener=FakeOpener(tampered), write=True, log=lambda _: None)
    assert not (paths.raw / "demo/dataset/train.json").exists()
    assert not list(paths.raw.rglob("*.part")), "temp download must be removed"
    assert not paths.lock.exists()


def test_tampered_raw_file_is_never_overwritten(locked: F.Paths):
    target = locked.raw / "demo/dataset/train.json"
    target.chmod(0o644)
    target.write_bytes(b"edited by hand")
    with pytest.raises(F.FetchError, match="git blob|size"):
        F.fetch(locked, opener=FakeOpener(UPSTREAM), log=lambda _: None)
    assert target.read_bytes() == b"edited by hand"


def test_write_lock_refuses_to_overwrite(locked: F.Paths):
    with pytest.raises(F.FetchError, match="already exists"):
        F.fetch(locked, opener=no_network, write=True, log=lambda _: None)


def test_fetch_without_lock_fails(tmp_path: Path):
    paths = write_sources(tmp_path, UPSTREAM)
    with pytest.raises(F.FetchError, match="not found"):
        F.fetch(paths, opener=no_network, log=lambda _: None)


# ---------------------------------------------------------------- verify


def test_verify_passes_on_clean_data(locked: F.Paths):
    assert F.verify(locked, log=lambda _: None) == 0


def test_verify_detects_mismatch_missing_and_extra(locked: F.Paths):
    dev = locked.raw / "demo/data/dev.json"
    dev.chmod(0o644)
    dev.write_bytes(b"[1]")
    (locked.raw / "demo/LICENSE").unlink()
    (locked.raw / "demo/extra.txt").write_text("x")
    with pytest.raises(F.FetchError) as exc:
        F.verify(locked, log=lambda _: None)
    msg = str(exc.value)
    assert "checksum mismatch: demo/data/dev.json" in msg
    assert "missing: demo/LICENSE" in msg
    assert "unexpected: demo/extra.txt" in msg


def test_verify_requires_every_source_file_locked(locked: F.Paths):
    lines = [l for l in locked.lock.read_text().splitlines() if not l.endswith("demo/LICENSE")]
    locked.lock.write_text("\n".join(lines) + "\n")
    with pytest.raises(F.FetchError, match="missing from checksums"):
        F.verify(locked, log=lambda _: None)


def test_malformed_lock_line_is_rejected(tmp_path: Path):
    lock = tmp_path / "checksums.sha256"
    lock.write_text("not-a-hash  file\n")
    with pytest.raises(F.FetchError, match="malformed"):
        F.read_lock(lock)


# ---------------------------------------------------------------- unzip safety


@pytest.mark.parametrize("bad", [
    make_zip({"../evil.txt": b"x"}),
    make_zip({"/abs/evil.txt": b"x"}),
    make_zip({"data/ok.json": b"x", "data/..\\evil.txt": b"x"}),
    make_zip({"data/ok.json": b"x"}, symlinks=("data/link",)),
])
def test_unsafe_zip_is_rejected_and_leaves_nothing(tmp_path: Path, bad: bytes):
    zp = tmp_path / "bad.zip"
    zp.write_bytes(bad)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(F.FetchError):
        F.safe_extract(zp, dest)
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil.txt").exists()


def test_zip_never_overwrites_existing_files(tmp_path: Path):
    zp = tmp_path / "a.zip"
    zp.write_bytes(GOOD_ZIP)
    dest = tmp_path / "out"
    (dest / "data").mkdir(parents=True)
    with pytest.raises(F.FetchError, match="overwrite"):
        F.safe_extract(zp, dest)


def test_unsafe_path_in_sources_is_rejected(tmp_path: Path):
    paths = write_sources(tmp_path, {"../escape.json": b"x"}, extract=())
    with pytest.raises(F.FetchError, match="unsafe path"):
        F.load_sources(paths.sources)


# ---------------------------------------------------------------- real config


def test_real_sources_are_pinned():
    sources = {s.name: s for s in F.load_sources(REPO_ROOT / "data/sources.yaml")}
    assert sources["finqa"].commit == "0f16e2867befa6840783e58be38c9efb9229d742"
    assert sources["convfinqa"].commit == "cf3eed2d5984960bf06bb8145bcea5e80b0222a6"
    for s in sources.values():
        assert s.license == "MIT"
        assert {f.path for f in s.files} >= {"LICENSE", "README.md"}
        for f in s.files:
            assert len(f.git_blob) == 40 and f.size > 0
    assert sources["finqa"].raw_url("dataset/dev.json") == (
        "https://raw.githubusercontent.com/czyssrs/FinQA/"
        "0f16e2867befa6840783e58be38c9efb9229d742/dataset/dev.json")
