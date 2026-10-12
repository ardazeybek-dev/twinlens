"""Walk folders, hash every photo (in parallel, cached) and report failures."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .cache import HashCache
from .imaging import PHOTO_EXTENSIONS, PhotoRecord, analyze

ProgressFn = Callable[[int, int], None]

# Folders twinlens itself writes to, plus OS/tool clutter that never holds originals.
_SKIP_DIRS = frozenset(
    {"twinlens-report", "twinlens-quarantine", ".twinlens", "node_modules", "__pycache__",
     "$RECYCLE.BIN", "System Volume Information", ".Trash", ".git"}
)


@dataclass
class ScanResult:
    records: list[PhotoRecord] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    cache_hits: int = 0


def discover(roots: Iterable[Path], extra_skip: Iterable[Path] = ()) -> Iterator[Path]:
    """Yield photo files under ``roots`` once each, in a stable order."""
    skip_abs = {p.resolve() for p in extra_skip}
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root.is_file():
            if root.suffix.lower() in PHOTO_EXTENSIONS and root not in seen:
                seen.add(root)
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".") and (here / d) not in skip_abs
            )
            for name in sorted(filenames):
                path = here / name
                if path.suffix.lower() in PHOTO_EXTENSIONS and path not in seen:
                    seen.add(path)
                    yield path


def _analyze_safe(path: str) -> tuple[str, PhotoRecord | None, str | None]:
    try:
        return path, analyze(Path(path)), None
    except Exception as exc:  # corrupt files must not stop a 50k-photo scan
        return path, None, f"{type(exc).__name__}: {exc}"


def scan(
    roots: Iterable[Path],
    cache: HashCache,
    workers: int = 1,
    progress: ProgressFn | None = None,
    extra_skip: Iterable[Path] = (),
) -> ScanResult:
    paths = list(discover(roots, extra_skip))
    result = ScanResult()
    todo: list[str] = []
    for path in paths:
        stat = path.stat()
        cached = cache.get(path, stat.st_size, stat.st_mtime)
        if cached is not None:
            result.records.append(cached)
        else:
            todo.append(str(path))
    result.cache_hits = len(result.records)

    total, done = len(paths), result.cache_hits
    if progress:
        progress(done, total)

    fresh: list[PhotoRecord] = []

    def collect(outcome: tuple[str, PhotoRecord | None, str | None]) -> None:
        nonlocal done
        path, record, error = outcome
        if record is not None:
            fresh.append(record)
        else:
            result.errors.append((path, error or "unknown error"))
        done += 1
        if progress:
            progress(done, total)

    if workers <= 1 or len(todo) < 8:
        for path in todo:
            collect(_analyze_safe(path))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_analyze_safe, p) for p in todo]
            for future in as_completed(futures):
                collect(future.result())

    cache.put_many(fresh)
    result.records.extend(fresh)
    result.records.sort(key=lambda r: r.path)
    result.errors.sort()
    return result
