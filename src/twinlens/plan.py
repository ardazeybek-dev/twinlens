"""Plans, quarantine moves and undo — twinlens never deletes a photo.

A *plan* is a JSON file listing, per cluster, which files to keep and which to
remove. `apply` moves the removed files into a quarantine folder (mirroring
their original location) and writes a *manifest*; `undo` reads the manifest
and puts everything back. Both refuse to act on anything that changed since
the scan.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath

from . import __version__
from .cluster import Cluster
from .imaging import file_digest

PLAN_FORMAT = "twinlens/plan@1"
MANIFEST_FORMAT = "twinlens/manifest@1"


class PlanError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_plan(clusters: list[Cluster], roots: list[Path], threshold: int) -> dict:
    return {
        "format": PLAN_FORMAT,
        "generator": f"twinlens {__version__}",
        "created": _now(),
        "roots": [str(r) for r in roots],
        "threshold": threshold,
        "clusters": [
            {
                "id": c.id,
                "kind": c.kind,
                "keep": [m.record.path for m in c.members if m.keep],
                "remove": [
                    {"path": m.record.path, "size": m.record.size, "sha": m.record.sha}
                    for m in c.members
                    if not m.keep
                ],
            }
            for c in clusters
        ],
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path, expected_format: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read {path}: {exc}") from exc
    if data.get("format") != expected_format:
        raise PlanError(f"{path} is not a {expected_format} file (format={data.get('format')!r})")
    return data


def quarantine_path(quarantine_root: Path, original: Path) -> Path:
    """Mirror an absolute path inside the quarantine: C:\\Photos\\a.jpg -> <q>/C/Photos/a.jpg."""
    parts = PurePath(original).parts
    anchor = parts[0].replace(":", "").strip("\\/") or "root"
    return quarantine_root.joinpath(anchor, *parts[1:])


@dataclass
class Move:
    source: Path
    target: Path
    size: int
    sha: str


@dataclass
class ApplyReport:
    moves: list[Move] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(m.size for m in self.moves)


def prepare(plan: dict, quarantine_root: Path, verify_hash: bool = True) -> ApplyReport:
    """Validate every planned removal without touching the disk."""
    report = ApplyReport()
    for cluster in plan.get("clusters", []):
        keep = [Path(p) for p in cluster.get("keep", [])]
        alive_keepers = [p for p in keep if p.is_file()]
        if not alive_keepers:
            for item in cluster.get("remove", []):
                report.skipped.append((item["path"], "no keeper left in this cluster"))
            continue
        for item in cluster.get("remove", []):
            src = Path(item["path"])
            if src in keep:
                report.skipped.append((str(src), "listed as both keep and remove"))
            elif not src.is_file():
                report.skipped.append((str(src), "file no longer exists"))
            elif src.stat().st_size != item.get("size"):
                report.skipped.append((str(src), "file changed since the scan (size)"))
            elif verify_hash and item.get("sha") and file_digest(src) != item["sha"]:
                report.skipped.append((str(src), "file changed since the scan (content)"))
            else:
                target = quarantine_path(quarantine_root, src)
                report.moves.append(Move(src, target, item.get("size", 0), item.get("sha", "")))
    return report


def _free_target(target: Path) -> Path:
    if not target.exists():
        return target
    for n in range(1, 10_000):
        candidate = target.with_name(f"{target.stem}.{n}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise PlanError(f"cannot find a free name next to {target}")


def execute(report: ApplyReport, quarantine_root: Path, plan_path: Path | None = None) -> Path:
    """Move files into quarantine; write the manifest after every move so a crash is undoable."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session = quarantine_root / stamp
    manifest_path = session / "manifest.json"
    manifest = {
        "format": MANIFEST_FORMAT,
        "generator": f"twinlens {__version__}",
        "created": _now(),
        "plan": str(plan_path) if plan_path else None,
        "moves": [],
    }
    for move in report.moves:
        target = _free_target(quarantine_path(session, move.source))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(move.source), str(target))
        move.target = target
        manifest["moves"].append(
            {"from": str(move.source), "to": str(target), "size": move.size, "sha": move.sha}
        )
        write_json(manifest_path, manifest)
    write_json(manifest_path, manifest)
    return manifest_path


@dataclass
class UndoReport:
    restored: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def undo(manifest_path: Path, dry_run: bool = False) -> UndoReport:
    manifest = read_json(manifest_path, MANIFEST_FORMAT)
    report = UndoReport()
    remaining = []
    for item in manifest["moves"]:
        src, dst = Path(item["to"]), Path(item["from"])
        if not src.is_file():
            report.skipped.append((str(dst), "quarantined copy is missing"))
            remaining.append(item)
            continue
        if dst.exists():
            report.skipped.append((str(dst), "a file already exists at the original location"))
            remaining.append(item)
            continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        report.restored.append((str(src), str(dst)))
    if not dry_run:
        manifest["moves"] = remaining
        manifest["undone"] = _now()
        write_json(manifest_path, manifest)
    return report
