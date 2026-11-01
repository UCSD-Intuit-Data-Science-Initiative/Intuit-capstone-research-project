"""Stage 1: fetch FinQA and ConvFinQA at pinned commits and verify checksums.

Usage (inside Docker, from the repo root):
    python -m capstone.data.fetch               # fetch, then verify against data/checksums.sha256
    python -m capstone.data.fetch --write-lock  # first time only: fetch, then create the checksums file
    python -m capstone.data.fetch --verify      # offline: check data/raw against the checksums file

Guarantees:
  * Every downloaded file matches the git blob id pinned in data/sources.yaml.
  * Zips are extracted only if every entry is a plain file inside the target folder.
  * Raw files are made read-only and never overwritten; a mismatch is an error.
  * Only data/license/README files are downloaded; no upstream code is fetched or run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

import yaml

CHUNK = 1 << 20  # 1 MiB
MAX_UNZIPPED_BYTES = 2 * 1024**3  # refuse zips that expand beyond 2 GiB
LOG_NAME = "FETCH_LOG.json"

Opener = Callable[[str], BinaryIO]


class FetchError(Exception):
    """Any problem that should stop the pipeline."""


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class SourceFile:
    path: str
    git_blob: str
    size: int
    extract: bool = False


@dataclass(frozen=True)
class Source:
    name: str
    repo: str
    commit: str
    license: str
    files: list[SourceFile] = field(default_factory=list)

    def raw_url(self, path: str) -> str:
        owner_repo = self.repo.removeprefix("https://github.com/").rstrip("/")
        return f"https://raw.githubusercontent.com/{owner_repo}/{self.commit}/{path}"


def load_sources(path: Path) -> list[Source]:
    doc = yaml.safe_load(path.read_text())
    sources = []
    for s in doc["sources"]:
        files = [SourceFile(**f) for f in s["files"]]
        for f in files:
            _check_relpath(f.path)
        sources.append(
            Source(name=s["name"], repo=s["repo"], commit=s["commit"],
                   license=s["license"], files=files)
        )
    return sources


# ---------------------------------------------------------------- hashing


def git_blob_id(path: Path) -> str:
    """Git's object id for a file: sha1(b"blob <size>\\0" + content)."""
    h = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _check_pinned(path: Path, f: SourceFile) -> None:
    size = path.stat().st_size
    if size != f.size:
        raise FetchError(f"{path}: size {size} != pinned {f.size}")
    blob = git_blob_id(path)
    if blob != f.git_blob:
        raise FetchError(f"{path}: git blob {blob} != pinned {f.git_blob}")


# ---------------------------------------------------------------- download


def default_opener(url: str) -> BinaryIO:
    req = urllib.request.Request(url, headers={"User-Agent": "d23-capstone-fetch"})
    return urllib.request.urlopen(req, timeout=120)  # noqa: S310 (fixed https host)


def download(url: str, dest: Path, f: SourceFile, opener: Opener) -> None:
    """Stream url to dest via a temp file; only move it into place if it matches the pin."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, opener(url) as resp:
            shutil.copyfileobj(resp, out, CHUNK)
        _check_pinned(tmp, f)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    _make_read_only(dest)


def _make_read_only(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


# ---------------------------------------------------------------- unzip


def _check_relpath(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
        raise FetchError(f"unsafe path: {name!r}")
    return p


def _is_junk(p: PurePosixPath) -> bool:
    return p.parts[0] == "__MACOSX" or any(part.startswith(".") for part in p.parts)


def safe_extract(zip_path: Path, dest_dir: Path) -> dict[str, int]:
    """Extract zip_path into dest_dir, refusing anything that is not a plain file inside it.

    Extraction goes to a temp folder first and is moved into place only if every
    entry passed, so a rejected zip leaves nothing behind.
    """
    counts = {"files": 0, "bytes": 0, "skipped": 0}
    with zipfile.ZipFile(zip_path) as zf:
        members = []
        total = 0
        for info in zf.infolist():
            p = _check_relpath(info.filename.rstrip("/") if info.is_dir() else info.filename)
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise FetchError(f"{zip_path}: symlink entry {info.filename!r}")
            if _is_junk(p):
                counts["skipped"] += 1
                continue
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_UNZIPPED_BYTES:
                raise FetchError(f"{zip_path}: expands beyond {MAX_UNZIPPED_BYTES} bytes")
            members.append((info, p))

        tops = {p.parts[0] for _, p in members}
        clashes = sorted(t for t in tops if (dest_dir / t).exists())
        if clashes:
            raise FetchError(f"{zip_path}: would overwrite existing {clashes} in {dest_dir}")

        staging = Path(tempfile.mkdtemp(dir=dest_dir, prefix=".unzip-"))
        try:
            for info, p in members:
                out = staging.joinpath(*p.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
                if out.stat().st_size != info.file_size:
                    raise FetchError(f"{zip_path}: {info.filename} size mismatch after extract")
                _make_read_only(out)
                counts["files"] += 1
                counts["bytes"] += info.file_size
            for t in sorted(tops):
                os.replace(staging / t, dest_dir / t)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return counts


# ---------------------------------------------------------------- checksums


def build_manifest(raw_dir: Path) -> dict[str, str]:
    """sha256 of every file under raw_dir, keyed by posix path relative to raw_dir."""
    manifest = {}
    for path in sorted(raw_dir.rglob("*")):
        rel = path.relative_to(raw_dir)
        if not path.is_file() or rel.as_posix() == LOG_NAME:
            continue
        if any(part.startswith(".") for part in rel.parts) or path.name.endswith(".part"):
            continue  # staging/temp leftovers; never part of the dataset
        manifest[rel.as_posix()] = sha256_file(path)
    return manifest


def write_lock(lock_path: Path, manifest: dict[str, str]) -> None:
    lines = [f"{digest}  {rel}" for rel, digest in sorted(manifest.items())]
    lock_path.write_text("\n".join(lines) + "\n")


def read_lock(lock_path: Path) -> dict[str, str]:
    lock = {}
    for n, line in enumerate(lock_path.read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        digest, sep, rel = line.partition("  ")
        if not sep or len(digest) != 64:
            raise FetchError(f"{lock_path}:{n}: malformed line")
        lock[rel] = digest
    return lock


def compare(manifest: dict[str, str], lock: dict[str, str]) -> list[str]:
    problems = []
    for rel in sorted(lock.keys() - manifest.keys()):
        problems.append(f"missing: {rel}")
    for rel in sorted(manifest.keys() - lock.keys()):
        problems.append(f"unexpected: {rel}")
    for rel in sorted(lock.keys() & manifest.keys()):
        if lock[rel] != manifest[rel]:
            problems.append(f"checksum mismatch: {rel}")
    return problems


# ---------------------------------------------------------------- stages


@dataclass
class Paths:
    root: Path

    @property
    def sources(self) -> Path:
        return self.root / "data" / "sources.yaml"

    @property
    def lock(self) -> Path:
        return self.root / "data" / "checksums.sha256"

    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"


def fetch(paths: Paths, opener: Opener = default_opener, write: bool = False,
          log: Callable[[str], None] = print) -> int:
    if write and paths.lock.exists():
        raise FetchError(f"{paths.lock} already exists; delete it on purpose before re-locking")
    if not write and not paths.lock.exists():
        raise FetchError(f"{paths.lock} not found; run `make data-lock` once and commit it")

    sources = load_sources(paths.sources)
    run = {"started": _now(), "python": platform.python_version(), "sources": []}
    for src in sources:
        src_dir = paths.raw / src.name
        entry = {"name": src.name, "repo": src.repo, "commit": src.commit,
                 "license": src.license, "files": []}
        for f in src.files:
            dest = src_dir.joinpath(*PurePosixPath(f.path).parts)
            if dest.exists():
                _check_pinned(dest, f)  # raw data is immutable: refuse to overwrite
                action = "present"
            else:
                log(f"  downloading {src.name}/{f.path} ({f.size:,} bytes)")
                download(src.raw_url(f.path), dest, f, opener)
                action = "downloaded"
            rec = {"path": f.path, "bytes": f.size, "git_blob": f.git_blob, "action": action}
            if f.extract:
                tops_exist = _extracted_already(dest, dest.parent)
                if tops_exist:
                    rec["extract"] = "present"
                else:
                    rec["extract"] = safe_extract(dest, dest.parent)
            log(f"  {src.name}/{f.path}: {action}")
            entry["files"].append(rec)
        run["sources"].append(entry)

    manifest = build_manifest(paths.raw)
    run["manifest"] = {"files": len(manifest),
                       "bytes": sum((paths.raw / r).stat().st_size for r in manifest)}
    if write:
        write_lock(paths.lock, manifest)
        run["lock"] = "written"
        log(f"Wrote {paths.lock} ({len(manifest)} files). Commit it.")
    else:
        problems = compare(manifest, read_lock(paths.lock))
        run["lock"] = "ok" if not problems else problems
        if problems:
            _write_log(paths, run)
            raise FetchError("raw data does not match checksums:\n  " + "\n  ".join(problems))
    _write_log(paths, run)
    log(f"OK: {run['manifest']['files']} files, {run['manifest']['bytes']:,} bytes in {paths.raw}")
    return 0


def verify(paths: Paths, log: Callable[[str], None] = print) -> int:
    if not paths.lock.exists():
        raise FetchError(f"{paths.lock} not found")
    lock = read_lock(paths.lock)
    expected = {f"{s.name}/{f.path}" for s in load_sources(paths.sources) for f in s.files}
    unlocked = sorted(expected - lock.keys())
    if unlocked:
        raise FetchError(f"sources.yaml files missing from checksums: {unlocked}")
    problems = compare(build_manifest(paths.raw), lock)
    if problems:
        raise FetchError("raw data does not match checksums:\n  " + "\n  ".join(problems))
    log(f"OK: all {len(lock)} files match {paths.lock}")
    return 0


def _extracted_already(zip_path: Path, dest_dir: Path) -> bool:
    with zipfile.ZipFile(zip_path) as zf:
        tops = {PurePosixPath(i.filename).parts[0] for i in zf.infolist()
                if not _is_junk(PurePosixPath(i.filename))}
    return bool(tops) and all((dest_dir / t).exists() for t in tops)


def _write_log(paths: Paths, run: dict) -> None:
    run["finished"] = _now()
    paths.raw.mkdir(parents=True, exist_ok=True)
    (paths.raw / LOG_NAME).write_text(json.dumps(run, indent=2) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write-lock", action="store_true", help="create data/checksums.sha256")
    mode.add_argument("--verify", action="store_true", help="offline check of data/raw")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    args = ap.parse_args(argv)
    paths = Paths(args.root)
    try:
        return verify(paths) if args.verify else fetch(paths, write=args.write_lock)
    except FetchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
