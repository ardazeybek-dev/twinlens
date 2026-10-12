"""Configuration: defaults < `.env` file < real environment < CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

PREFIX = "TWINLENS_"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a minimal `.env` file: KEY=VALUE lines, `#` comments, optional quotes."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Config:
    threshold: int = 10
    workers: int = 0
    cache: Path = Path(".twinlens/cache.sqlite")
    report_dir: Path = Path("twinlens-report")
    quarantine: Path = Path("twinlens-quarantine")
    thumb_size: int = 320

    def __post_init__(self) -> None:
        if not 0 <= self.threshold <= 64:
            raise ValueError(f"threshold must be between 0 and 64, got {self.threshold}")
        if self.workers < 0:
            raise ValueError(f"workers must be >= 0, got {self.workers}")
        if not 32 <= self.thumb_size <= 2048:
            raise ValueError(f"thumb_size must be between 32 and 2048, got {self.thumb_size}")

    @property
    def effective_workers(self) -> int:
        return self.workers or max(1, os.cpu_count() or 1)

    def with_overrides(self, **overrides: object) -> Config:
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self


_CASTS = {
    "threshold": int,
    "workers": int,
    "cache": Path,
    "report_dir": Path,
    "quarantine": Path,
    "thumb_size": int,
}


def load_config(cwd: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    cwd = cwd or Path.cwd()
    environ = dict(os.environ) if environ is None else environ
    merged: dict[str, str] = {}
    dotenv = cwd / ".env"
    if dotenv.is_file():
        merged.update(parse_dotenv(dotenv.read_text(encoding="utf-8")))
    merged.update({k: v for k, v in environ.items() if k.startswith(PREFIX)})

    kwargs: dict[str, object] = {}
    for field, cast in _CASTS.items():
        raw = merged.get(PREFIX + field.upper())
        if raw is None or raw == "":
            continue
        try:
            kwargs[field] = cast(raw)
        except ValueError as exc:
            raise ValueError(f"{PREFIX}{field.upper()}={raw!r} is not a valid {cast.__name__}") from exc
    return Config(**kwargs)
